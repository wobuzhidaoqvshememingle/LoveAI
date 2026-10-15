"""
LoveAI v2.0 - 插件服务

职责：
1. Skill 技能管理：扫描 skills/ 目录，加载技能指令（Markdown）或 Python 入口，
   供 LLM 按指令执行或直接调用 Python run()。
2. Python 插件系统：扫描 plugins/ 目录，动态发现 / 加载 / 启用 / 禁用第三方插件。

设计约束：
- 纯 Python 服务层，不 import PySide6；信号通过构造参数 signals 注入。
- 插件 / 技能加载全部走 try/except，单个失败不影响其他。
- 插件 activate() 不自动执行，必须显式 enable_plugin 才调用。
- 数据库操作统一走 db.execute / fetch_one / fetch_all，SQL 参数使用 :name 命名格式。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.database import Database
    from app.signals import AppSignals

logger = logging.getLogger(__name__)

# 技能索引候选文件名（兼容历史 skill_list.json 与规范 SKILLS_INDEX.json）
_SKILL_INDEX_FILES = ("SKILLS_INDEX.json", "skill_list.json")


class PluginService:
    """Skill 技能 + Python 插件统一管理服务"""

    def __init__(self,
                 db: Optional["Database"] = None,
                 config: Any = None,
                 signals: Optional["AppSignals"] = None) -> None:
        """初始化插件服务。

        Args:
            db: Database 实例（execute / fetch_one / fetch_all），可为 None。
            config: 配置服务（ConfigService），可为 None。
            signals: AppSignals 实例，用于错误上报，可为 None。
        """
        self.db = db
        self.config = config
        self.signals = signals

        # 项目根目录 = 本文件所在 services/ 的上一级
        project_root = Path(__file__).resolve().parent.parent
        self.skills_dir: str = str(project_root / "skills")
        self.plugins_dir: str = str(project_root / "plugins")

        # 已加载技能 {name: {metadata, content, entry_module, ...}}
        self.loaded_skills: dict[str, dict] = {}
        # 已加载插件 {name: {module, instance, enabled, metadata}}
        self.loaded_plugins: dict[str, dict] = {}

        # 可用技能 / 插件元数据缓存
        self.available_skills: list[dict] = []
        self.available_plugins: list[dict] = []

        # 启动时扫描（全部容错，目录缺失不崩溃）
        try:
            self._scan_skills()
        except Exception as e:
            logger.error(f"扫描技能目录异常: {e}")
        try:
            self._scan_plugins()
        except Exception as e:
            logger.error(f"扫描插件目录异常: {e}")

        # 确保插件状态表存在（容错，db 为 None 时跳过）
        self._ensure_plugin_state_table()

    # ------------------------------------------------------------------ #
    # Skill 技能扫描
    # ------------------------------------------------------------------ #
    def _scan_skills(self) -> list:
        """扫描 skills/ 目录，发现所有技能。

        发现规则：
        - 子目录内含 SKILL.md 或 SKILL.json 的视为目录型技能；
        - skills/ 下扁平的 *.md 文件视为文件型技能（文件名即技能名）；
        - 同时读取 SKILLS_INDEX.json / skill_list.json，用索引元数据（中文名、描述、
          category）补全文件型技能。

        Returns:
            list[dict]: 技能元数据列表，同时缓存到 self.available_skills。
        """
        skills: list[dict] = []

        # 1. 读取索引文件，按 id / file 建立映射
        index_map: dict[str, dict] = {}
        for index_name in _SKILL_INDEX_FILES:
            index_path = os.path.join(self.skills_dir, index_name)
            if os.path.isfile(index_path):
                try:
                    with open(index_path, "r", encoding="utf-8") as f:
                        index_data = json.load(f)
                    for item in index_data.get("skills", []):
                        key = (item.get("id")
                               or (item.get("file") or "").replace(".md", "")
                               or item.get("name"))
                        if key:
                            index_map[key] = item
                    logger.debug(f"技能索引已加载: {index_path}")
                except Exception as e:
                    logger.warning(f"技能索引读取失败 {index_path}: {e}")
                break  # 只取第一个存在的索引

        # 2. 目录不存在直接返回空
        if not os.path.isdir(self.skills_dir):
            logger.debug(f"技能目录不存在: {self.skills_dir}")
            self.available_skills = skills
            return skills

        seen: set[str] = set()

        # 3. 遍历目录
        for entry in sorted(os.listdir(self.skills_dir)):
            entry_path = os.path.join(self.skills_dir, entry)
            try:
                # --- 目录型技能：含 SKILL.md / SKILL.json ---
                if os.path.isdir(entry_path) and not entry.startswith((".", "_")):
                    skill_md = os.path.join(entry_path, "SKILL.md")
                    skill_json = os.path.join(entry_path, "SKILL.json")
                    if not (os.path.isfile(skill_md) or os.path.isfile(skill_json)):
                        continue

                    meta: dict[str, Any] = {}
                    if os.path.isfile(skill_json):
                        try:
                            with open(skill_json, "r", encoding="utf-8") as f:
                                meta = json.load(f)
                        except Exception as e:
                            logger.warning(f"技能元数据读取失败 {skill_json}: {e}")

                    name = meta.get("name") or entry
                    if name in seen:
                        continue
                    seen.add(name)
                    skills.append({
                        "name": name,
                        "version": meta.get("version", "1.0"),
                        "description": meta.get("description", ""),
                        "category": meta.get("category", "uncategorized"),
                        "entrypoint": meta.get("entrypoint"),
                        "path": entry_path,
                        "content_path": skill_md if os.path.isfile(skill_md) else None,
                        "source": "directory",
                    })

                # --- 文件型技能：扁平 *.md ---
                elif os.path.isfile(entry_path) and entry.endswith(".md"):
                    name = os.path.splitext(entry)[0]
                    if name in seen:
                        continue
                    idx = index_map.get(name, {})
                    seen.add(name)
                    skills.append({
                        "name": name,
                        "version": idx.get("version", "1.0"),
                        "description": idx.get("description", ""),
                        "category": idx.get("category", "uncategorized"),
                        "entrypoint": idx.get("entrypoint"),
                        "path": self.skills_dir,
                        "content_path": entry_path,
                        "source": "file",
                    })
            except Exception as e:
                logger.warning(f"扫描技能条目失败 {entry}: {e}")
                continue

        self.available_skills = skills
        logger.info(f"扫描到 {len(skills)} 个技能")
        return skills

    # ------------------------------------------------------------------ #
    # Skill 加载 / 执行 / 列表
    # ------------------------------------------------------------------ #
    def load_skill(self, skill_name: str) -> dict:
        """加载指定技能。

        读取 SKILL.md 内容；若 entrypoint 指向 .py 文件则用 importlib 动态加载，
        缓存到 self.loaded_skills。

        Args:
            skill_name: 技能名（目录名或 md 文件名，不含扩展名）。

        Returns:
            dict: {"name", "loaded", "metadata", "content", 失败时带 "error"}。
        """
        if skill_name in self.loaded_skills:
            return self.loaded_skills[skill_name]

        meta = next((s for s in self.available_skills if s["name"] == skill_name), None)
        if not meta:
            logger.warning(f"技能不存在: {skill_name}")
            return {"name": skill_name, "loaded": False, "error": "技能不存在"}

        # 读取 Markdown 指令内容
        content = ""
        content_path = meta.get("content_path")
        if content_path and os.path.isfile(content_path):
            try:
                with open(content_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                logger.warning(f"技能内容读取失败 {content_path}: {e}")

        # 动态加载 Python entrypoint（可选）
        entry_module = None
        entrypoint = meta.get("entrypoint")
        if entrypoint:
            try:
                entry_path = entrypoint
                if not os.path.isabs(entry_path):
                    entry_path = os.path.join(meta.get("path", self.skills_dir), entrypoint)
                if os.path.isfile(entry_path):
                    module_name = f"loveai_skill_{skill_name}"
                    spec = importlib.util.spec_from_file_location(module_name, entry_path)
                    if spec and spec.loader:
                        entry_module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(entry_module)
                        logger.debug(f"技能入口加载成功: {skill_name} -> {entry_path}")
            except Exception as e:
                logger.warning(f"技能入口加载失败 {skill_name}: {e}")

        result = {
            "name": skill_name,
            "loaded": True,
            "metadata": meta,
            "content": content,
            "entry_module": entry_module,
        }
        self.loaded_skills[skill_name] = result
        logger.info(f"技能加载成功: {skill_name}")
        return result

    def execute_skill(self, skill_name: str, context: dict = None) -> dict:
        """执行技能。

        - 若技能带 Python entrypoint 且实现了 run(context)，直接调用并返回结果；
        - 否则返回 SKILL.md 指令文本，供 LLM 按指令执行。

        Args:
            skill_name: 技能名。
            context: 执行上下文（可选字典）。

        Returns:
            dict: 执行结果或指令包。
        """
        context = context or {}
        loaded = self.load_skill(skill_name)
        if not loaded.get("loaded"):
            return loaded

        entry_module = loaded.get("entry_module")
        if entry_module is not None and hasattr(entry_module, "run"):
            try:
                result = entry_module.run(context)
                return {
                    "name": skill_name,
                    "executed": True,
                    "type": "python",
                    "result": result,
                }
            except Exception as e:
                logger.error(f"技能 Python 执行失败 {skill_name}: {e}")
                self._emit_error("skill_exec", f"技能 {skill_name} 执行失败: {e}")
                return {"name": skill_name, "executed": False, "error": str(e)}

        # 纯指令型：返回指令文本，交给 LLM 按流程执行
        return {
            "name": skill_name,
            "executed": True,
            "type": "instructions",
            "instructions": loaded.get("content", ""),
            "context": context,
        }

    def list_skills(self, category: str = None) -> list:
        """列出所有可用技能，可按 category 过滤。

        Args:
            category: 分类名，None 返回全部。

        Returns:
            list[dict]: 每项含 name / description / category / version / loaded。
        """
        result: list[dict] = []
        for s in self.available_skills:
            if category and s.get("category") != category:
                continue
            result.append({
                "name": s.get("name"),
                "description": s.get("description", ""),
                "category": s.get("category", "uncategorized"),
                "version": s.get("version", "1.0"),
                "loaded": s["name"] in self.loaded_skills,
            })
        return result

    # ------------------------------------------------------------------ #
    # Python 插件扫描
    # ------------------------------------------------------------------ #
    def _scan_plugins(self) -> list:
        """扫描 plugins/ 目录，发现 Python 插件。

        发现规则：
        - 子目录含 plugin.json（metadata）+ __init__.py / main.py 视为目录型插件；
        - plugins/ 下扁平 *.py 文件（排除 __init__.py）视为单文件插件，
          从模块 docstring / PLUGIN_META 常量提取元数据。
        - 目录不存在时返回空列表，不抛异常。

        Returns:
            list[dict]: 插件元数据列表，缓存到 self.available_plugins。
        """
        plugins: list[dict] = []

        if not os.path.isdir(self.plugins_dir):
            logger.debug(f"插件目录不存在: {self.plugins_dir}")
            self.available_plugins = plugins
            return plugins

        # --- 目录型插件 ---
        for entry in sorted(os.listdir(self.plugins_dir)):
            entry_path = os.path.join(self.plugins_dir, entry)
            if not os.path.isdir(entry_path) or entry.startswith((".", "_")):
                continue
            try:
                plugin_json = os.path.join(entry_path, "plugin.json")
                init_py = os.path.join(entry_path, "__init__.py")
                main_py = os.path.join(entry_path, "main.py")
                entry_py = None
                if os.path.isfile(init_py):
                    entry_py = init_py
                elif os.path.isfile(main_py):
                    entry_py = main_py

                if not (os.path.isfile(plugin_json) and entry_py):
                    continue

                meta: dict[str, Any] = {}
                try:
                    with open(plugin_json, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception as e:
                    logger.warning(f"插件元数据读取失败 {plugin_json}: {e}")

                name = meta.get("name") or entry
                plugins.append({
                    "name": name,
                    "version": meta.get("version", "1.0"),
                    "description": meta.get("description", ""),
                    "author": meta.get("author", ""),
                    "permissions": meta.get("permissions", []),
                    "enabled": bool(meta.get("enabled", False)),
                    "entrypoint": meta.get("entrypoint") or entry_py,
                    "path": entry_py,
                    "source": "directory",
                })
            except Exception as e:
                logger.warning(f"扫描插件目录失败 {entry_path}: {e}")
                continue

        # --- 单文件插件 ---
        for entry in sorted(os.listdir(self.plugins_dir)):
            entry_path = os.path.join(self.plugins_dir, entry)
            if not (os.path.isfile(entry_path) and entry.endswith(".py")):
                continue
            if entry == "__init__.py":
                continue
            try:
                name = os.path.splitext(entry)[0]
                meta = self._extract_py_metadata(entry_path)
                plugins.append({
                    "name": name,
                    "version": meta.get("version", "1.0"),
                    "description": meta.get("description", ""),
                    "author": meta.get("author", ""),
                    "permissions": meta.get("permissions", []),
                    "enabled": bool(meta.get("enabled", False)),
                    "entrypoint": entry_path,
                    "path": entry_path,
                    "source": "file",
                })
            except Exception as e:
                logger.warning(f"扫描单文件插件失败 {entry_path}: {e}")
                continue

        self.available_plugins = plugins
        logger.info(f"扫描到 {len(plugins)} 个插件")
        return plugins

    @staticmethod
    def _extract_py_metadata(path: str) -> dict:
        """从单文件 .py 提取插件元数据（模块 docstring / PLUGIN_META 常量）。

        Args:
            path: .py 文件路径。

        Returns:
            dict: 含 version / description / author 等字段，解析失败返回默认值。
        """
        meta: dict[str, Any] = {
            "version": "1.0",
            "description": "",
            "author": "",
            "permissions": [],
        }
        try:
            with open(path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=path)
            docstring = ast.get_docstring(tree)
            if docstring:
                # 取 docstring 首行作为描述
                meta["description"] = docstring.strip().splitlines()[0][:200]
            # 查找模块级 PLUGIN_META / __PLUGIN_INFO__ = {...}
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id in ("PLUGIN_META", "__PLUGIN_INFO__"):
                            try:
                                parsed = ast.literal_eval(node.value)
                                if isinstance(parsed, dict):
                                    meta.update(parsed)
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"解析插件元数据失败 {path}: {e}")
        return meta

    # ------------------------------------------------------------------ #
    # Python 插件加载 / 卸载 / 启停
    # ------------------------------------------------------------------ #
    def load_plugin(self, plugin_name: str) -> dict:
        """加载 Python 插件（importlib 动态导入）。

        - 用 spec_from_file_location 创建模块；
        - 若模块暴露 Plugin 类则实例化；
        - 检查 activate / deactivate / get_info 接口存在性（不强制要求）；
        - 单个插件失败返回 error，不抛出异常，不影响其他插件。

        Args:
            plugin_name: 插件名。

        Returns:
            dict: {"name", "loaded", "metadata"} 或 {"name", "loaded": False, "error"}。
        """
        meta = next((p for p in self.available_plugins if p["name"] == plugin_name), None)
        if not meta:
            logger.warning(f"插件不存在: {plugin_name}")
            return {"name": plugin_name, "loaded": False, "error": "插件不存在"}

        if plugin_name in self.loaded_plugins:
            return {"name": plugin_name, "loaded": True,
                    "metadata": meta, "already_loaded": True}

        path = meta.get("path")
        if not path or not os.path.isfile(path):
            return {"name": plugin_name, "loaded": False, "error": f"插件文件不存在: {path}"}

        try:
            module_name = f"loveai_plugin_{plugin_name}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"无法创建模块规范: {path}")

            # 将插件目录临时加入 sys.path，便于插件内部 import 同级模块
            plugin_dir = os.path.dirname(path)
            added_path = False
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)
                added_path = True

            try:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            finally:
                # 不立即从 sys.path 移除（插件模块可能已 import 同级包），
                # 仅在卸载时清理 sys.modules 中的模块。
                _ = added_path

            # 实例化 Plugin 类（若存在）
            instance = None
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls is not None and isinstance(plugin_cls, type):
                try:
                    instance = plugin_cls()
                except Exception as e:
                    logger.warning(f"插件 {plugin_name} Plugin 实例化失败: {e}")

            self.loaded_plugins[plugin_name] = {
                "module": module,
                "instance": instance,
                "enabled": False,
                "metadata": meta,
            }
            logger.info(f"插件加载成功: {plugin_name}")
            return {"name": plugin_name, "loaded": True, "metadata": meta}

        except Exception as e:
            logger.error(f"插件加载失败 {plugin_name}: {e}")
            # 清理可能残留在 sys.modules 的半成品
            sys.modules.pop(f"loveai_plugin_{plugin_name}", None)
            self._emit_error("plugin_load", f"插件 {plugin_name} 加载失败: {e}")
            return {"name": plugin_name, "loaded": False, "error": str(e)}

    def unload_plugin(self, plugin_name: str) -> dict:
        """卸载插件（先尝试 deactivate，再从 sys.modules 与缓存移除）。

        Args:
            plugin_name: 插件名。

        Returns:
            dict: {"unloaded": True} 或 {"unloaded": False, "error": ...}。
        """
        info = self.loaded_plugins.get(plugin_name)
        if not info:
            return {"unloaded": False, "error": "插件未加载"}

        try:
            # 调用 deactivate（容错）
            target = info.get("instance") or info.get("module")
            if target is not None and hasattr(target, "deactivate"):
                try:
                    target.deactivate()
                except Exception as e:
                    logger.warning(f"插件 {plugin_name} deactivate 异常: {e}")

            # 从 sys.modules 移除
            module_name = f"loveai_plugin_{plugin_name}"
            sys.modules.pop(module_name, None)

            del self.loaded_plugins[plugin_name]
            logger.info(f"插件卸载成功: {plugin_name}")
            return {"unloaded": True, "name": plugin_name}
        except Exception as e:
            logger.error(f"插件卸载失败 {plugin_name}: {e}")
            return {"unloaded": False, "error": str(e)}

    def enable_plugin(self, plugin_name: str) -> dict:
        """启用插件：加载 → activate() → 记录状态。

        注意：activate() 仅在此处显式调用，加载阶段不会自动执行插件逻辑。

        Args:
            plugin_name: 插件名。

        Returns:
            dict: {"enabled": True} 或 {"enabled": False, "error": ...}。
        """
        load_res = self.load_plugin(plugin_name)
        if not load_res.get("loaded"):
            return load_res

        info = self.loaded_plugins.get(plugin_name)
        if not info:
            return {"enabled": False, "error": "插件加载后未找到实例"}

        target = info.get("instance") or info.get("module")
        try:
            if hasattr(target, "activate"):
                target.activate()
            info["enabled"] = True
            self._set_plugin_state(plugin_name, True)
            logger.info(f"插件已启用: {plugin_name}")
            return {"enabled": True, "name": plugin_name}
        except Exception as e:
            logger.error(f"插件 activate 失败 {plugin_name}: {e}")
            self._emit_error("plugin_enable", f"插件 {plugin_name} 启用失败: {e}")
            return {"enabled": False, "name": plugin_name, "error": str(e)}

    def disable_plugin(self, plugin_name: str) -> dict:
        """禁用插件：deactivate() → unload() → 记录状态。

        Args:
            plugin_name: 插件名。

        Returns:
            dict: {"disabled": True}。
        """
        info = self.loaded_plugins.get(plugin_name)
        if info:
            target = info.get("instance") or info.get("module")
            if target is not None and hasattr(target, "deactivate"):
                try:
                    target.deactivate()
                except Exception as e:
                    logger.warning(f"插件 {plugin_name} deactivate 异常: {e}")

        self._set_plugin_state(plugin_name, False)
        self.unload_plugin(plugin_name)
        logger.info(f"插件已禁用: {plugin_name}")
        return {"disabled": True, "name": plugin_name}

    def list_plugins(self) -> list:
        """列出所有插件及状态。

        Returns:
            list[dict]: 每项为 metadata + loaded + enabled。
        """
        result: list[dict] = []
        for p in self.available_plugins:
            info = self.loaded_plugins.get(p["name"])
            item = dict(p)
            item["loaded"] = info is not None
            item["enabled"] = bool(info.get("enabled")) if info else False
            result.append(item)
        return result

    def get_plugin_info(self, plugin_name: str) -> dict:
        """获取插件详情：metadata + 接口列表 + 运行状态。

        Args:
            plugin_name: 插件名。

        Returns:
            dict: {"metadata", "loaded", "enabled", "interfaces"}。
        """
        meta = next((p for p in self.available_plugins if p["name"] == plugin_name), None)
        info = self.loaded_plugins.get(plugin_name)

        interfaces: list[str] = []
        if info:
            target = info.get("instance") or info.get("module")
            for m in ("activate", "deactivate", "get_info", "run", "register"):
                if hasattr(target, m):
                    interfaces.append(m)

        return {
            "metadata": meta or {"name": plugin_name},
            "loaded": info is not None,
            "enabled": bool(info.get("enabled")) if info else False,
            "interfaces": interfaces,
        }

    def install_plugin_from_path(self, source_path: str) -> dict:
        """从本地路径安装插件（复制到 plugins/ 目录）。

        Args:
            source_path: 源路径，可为：
                - 含 plugin.json 的插件目录（整目录复制）；
                - 单个 .py 插件文件。

        Returns:
            dict: {"installed": True, "name": ...} 或 {"installed": False, "error": ...}。
        """
        if not os.path.exists(source_path):
            return {"installed": False, "error": f"源路径不存在: {source_path}"}

        try:
            os.makedirs(self.plugins_dir, exist_ok=True)

            if os.path.isdir(source_path):
                # 目录型：必须含 plugin.json
                plugin_json = os.path.join(source_path, "plugin.json")
                if not os.path.isfile(plugin_json):
                    return {"installed": False, "error": "源目录缺少 plugin.json"}

                # 确定插件名
                name = os.path.basename(source_path.rstrip("/\\"))
                try:
                    with open(plugin_json, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    name = meta.get("name") or name
                except Exception:
                    pass

                dest = os.path.join(self.plugins_dir, name)
                if os.path.exists(dest):
                    return {"installed": False, "error": f"插件已存在: {name}"}
                shutil.copytree(source_path, dest)

            elif os.path.isfile(source_path) and source_path.endswith(".py"):
                name = os.path.splitext(os.path.basename(source_path))[0]
                dest = os.path.join(self.plugins_dir, os.path.basename(source_path))
                if os.path.exists(dest):
                    return {"installed": False, "error": f"插件已存在: {name}"}
                shutil.copy2(source_path, dest)

            else:
                return {"installed": False,
                        "error": "源必须是含 plugin.json 的目录或单个 .py 文件"}

            # 重新扫描以纳入新插件
            self._scan_plugins()
            logger.info(f"插件安装成功: {name} <- {source_path}")
            return {"installed": True, "name": name}

        except Exception as e:
            logger.error(f"插件安装失败 {source_path}: {e}")
            self._emit_error("plugin_install", f"插件安装失败: {e}")
            return {"installed": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _ensure_plugin_state_table(self) -> None:
        """确保 plugin_states 表存在（容错，db 为 None 时跳过）。"""
        if not self.db:
            return
        try:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS plugin_states ("
                "name TEXT PRIMARY KEY, "
                "enabled INTEGER DEFAULT 0, "
                "updated_at TEXT DEFAULT (datetime('now'))"
                ")"
            )
        except Exception as e:
            logger.debug(f"插件状态表创建失败（忽略）: {e}")

    def _set_plugin_state(self, name: str, enabled: bool) -> None:
        """记录插件启用/禁用状态到数据库（容错）。

        Args:
            name: 插件名。
            enabled: 是否启用。
        """
        if not self.db:
            return
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO plugin_states (name, enabled, updated_at) "
                "VALUES (:name, :enabled, datetime('now'))",
                {"name": name, "enabled": 1 if enabled else 0},
            )
        except Exception as e:
            logger.debug(f"插件状态写库失败（忽略）: {e}")

    def _emit_error(self, source: str, message: str) -> None:
        """通过 signals 上报错误（signals 为 None 时静默）。"""
        if self.signals is None:
            return
        try:
            self.signals.error_occurred.emit({
                "source": source,
                "message": message,
                "severity": "warning",
            })
        except Exception:
            pass
