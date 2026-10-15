"""
LoveAI v2.0 - Codex 八大核心功能恋爱场景适配（CodexAdaptation）

借鉴自编程助手 Codex 的 8 个核心能力，全部改造为恋爱辅助语境下的等价物：

  1. 多 Agent 并行工作  -> parallel_analysis：多个分析视角在独立线程并行跑，
                          每个视角最多 15 秒超时，最后汇总结果。
  2. Skills 技能系统    -> SkillManager：把"提示词 + 规则 + 模板"打包成可复用技能，
                          内置破冰 / 冷战 / 邀约 / 礼物 / 道歉 5 类恋爱技能。
  3. 代码审查(PR Review)-> strategy_review：对拟发送消息 / 策略做技术审查，
                          输出 issues + 评分(0-100) + 改进建议（仅做技术维度审查）。
  4. 上下文持久化       -> ContextKeeper：保存分析会话状态，支持中断后 resume。
  5. MCP 工具集成       -> MCPToolRegistry：统一外部工具注册 / 调用接口，调用前校验参数。
  6. 任务自动分解       -> TaskDecomposer：把复杂恋爱目标拆成带依赖 / 时长的步骤序列。
  7. 终端 / CLI 执行    -> LocalExecutor：白名单机制安全执行本地操作（发微信占位 / 写笔记等）。
  8. 数据/创意分析插件 -> AnalysisPluginRegistry：可插拔分析模块，统一 analyze(data)->dict 接口。

设计约束：
  - 纯 Python，不 import PySide6；logger = logging.getLogger(__name__)。
  - 所有公开方法 try/except 兜底，单模块失败不影响主流程。
  - 多线程用 threading.Thread(daemon=True) + join(timeout=15)。
  - 文件操作统一 os.makedirs(dir, exist_ok=True)。
  - 手段中立：只做技术层面的结构 / 语气 / 时机分析，不评价对错善恶。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 单视角并行分析的超时（秒）
_PARALLEL_TIMEOUT = 15.0


# ====================================================================== #
# 1. Skills 技能系统（SkillManager）
# ====================================================================== #

class SkillManager:
    """恋爱技能管理器：技能 = prompt + 规则 + 模板 的可复用包。

    技能配置为 JSON，落盘在 data/skills/{name}.json；内置 5 个恋爱技能。
    """

    # 内置技能（内存兜底；register_skill 会额外落盘）
    BUILTIN_SKILLS: dict[str, dict[str, Any]] = {
        "icebreaker": {
            "name": "icebreaker",
            "title": "破冰话术",
            "description": "初识 / 久未联系时的低压力开场白生成。",
            "prompt": "基于对方画像生成 3 条低压力开场白，以共同场景或轻事实切入。",
            "rules": ["不查户口式连续提问", "不查岗", "以对方朋友圈 / 共同场景切入", "单条不超过 30 字"],
            "template": "{scene}，{light_opener}",
        },
        "cold_war_resolve": {
            "name": "cold_war_resolve",
            "title": "冷战化解",
            "description": "冷战 / 降温期的低浓度重启话术。",
            "prompt": "生成一条不追问、不解释第二遍、带一个具体小事的重启消息。",
            "rules": ["不追问'你为什么不理我'", "不道歉认错过度", "带一个具体事实 / 物品", "留对方回复空间"],
            "template": "刚看到 {trigger}，想起你上次说的 {thing}，{light}",
        },
        "invitation": {
            "name": "invitation",
            "title": "邀约技巧",
            "description": "把口头好感落成具体见面安排。",
            "prompt": "根据对方兴趣点给出 2 个具体时间 + 地点方案，降低决策成本。",
            "rules": ["给具体时间而非'有空吗'", "地点选对方熟悉的区域", "时长控制在 2 小时内", "被拒不追问原因"],
            "template": "{day} 下午 {time}，{place} 那家 {shop}，要不要一起？",
        },
        "gift_pick": {
            "name": "gift_pick",
            "title": "礼物推荐",
            "description": "按关系阶段与对方偏好选礼物。",
            "prompt": "给出 3 档预算礼物候选，避免过度贵重造成压力。",
            "rules": ["初期不送贵重物品", "优先实用 / 体验类", "避开暗示性过强的礼物", "附上一句轻卡片文案"],
            "template": "预算 {budget}：{option1} / {option2} / {option3}",
        },
        "apology": {
            "name": "apology",
            "title": "道歉策略",
            "description": "冲突后的结构化道歉（不使用情感施压）。",
            "prompt": "用'我具体哪件事 + 我当时的感受 + 下次怎么做'结构生成道歉。",
            "rules": ["不'我都道歉了你还要怎样'", "不翻旧账", "一次说清不重复发", "不要求立刻原谅"],
            "template": "关于 {event}，我当时 {behavior}，下次我会 {improve}。",
        },
    }

    def __init__(self, data_dir: str) -> None:
        """初始化技能管理器。

        Args:
            data_dir: data/skills 所在的父目录（项目 data/ 目录）。
        """
        self.skills_dir = os.path.join(data_dir, "skills")
        try:
            os.makedirs(self.skills_dir, exist_ok=True)
        except Exception as e:  # pragma: no cover - 目录创建失败仅记录
            logger.error(f"创建技能目录失败: {e}")
        # 已注册到文件系统的技能覆盖表 {name: config}
        self._file_skills: dict[str, dict] = {}
        try:
            self._load_file_skills()
        except Exception as e:
            logger.error(f"加载已落盘技能失败: {e}")

    # ------------------------------------------------------------------ #
    def _load_file_skills(self) -> None:
        """扫描 skills_dir，把已有 *.json 读入内存。"""
        if not os.path.isdir(self.skills_dir):
            return
        for fn in os.listdir(self.skills_dir):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.skills_dir, fn), "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict) and cfg.get("name"):
                    self._file_skills[cfg["name"]] = cfg
            except Exception as e:
                logger.warning(f"技能文件 {fn} 读取失败: {e}")

    # ------------------------------------------------------------------ #
    def list_skills(self) -> list[dict[str, Any]]:
        """列出所有可用技能（内置 + 已落盘）。

        Returns:
            list[dict]: 每项含 name / title / description。
        """
        out: list[dict] = []
        seen: set[str] = set()
        try:
            for name, cfg in self.BUILTIN_SKILLS.items():
                out.append({"name": name, "title": cfg.get("title", name),
                            "description": cfg.get("description", "")})
                seen.add(name)
        except Exception as e:
            logger.error(f"列出内置技能失败: {e}")
        try:
            for name, cfg in self._file_skills.items():
                if name in seen:
                    continue
                out.append({"name": name, "title": cfg.get("title", name),
                            "description": cfg.get("description", "")})
        except Exception as e:
            logger.error(f"列出自定义技能失败: {e}")
        return out

    # ------------------------------------------------------------------ #
    def load_skill(self, name: str) -> Optional[dict[str, Any]]:
        """按名称加载技能配置（先查文件覆盖，再查内置）。

        Args:
            name: 技能名。

        Returns:
            dict 或 None。
        """
        try:
            if name in self._file_skills:
                return self._file_skills[name]
            if name in self.BUILTIN_SKILLS:
                return self.BUILTIN_SKILLS[name]
        except Exception as e:
            logger.error(f"加载技能 {name} 失败: {e}")
        return None

    # ------------------------------------------------------------------ #
    def register_skill(self, name: str, config: dict[str, Any]) -> bool:
        """注册新技能并落盘为 JSON。

        Args:
            name: 技能名。
            config: 技能配置（prompt / rules / template 等）。

        Returns:
            是否成功。
        """
        try:
            cfg = dict(config)
            cfg["name"] = name
            self._file_skills[name] = cfg
            os.makedirs(self.skills_dir, exist_ok=True)
            path = os.path.join(self.skills_dir, f"{name}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"注册技能 {name} 失败: {e}")
            return False

    # ------------------------------------------------------------------ #
    def execute_skill(self, name: str, context: Optional[dict] = None) -> dict[str, Any]:
        """执行技能：用 context 填充模板，返回生成结果 + 规则。

        Args:
            name: 技能名。
            context: 模板变量。

        Returns:
            {"skill", "output", "rules", "ok"}。
        """
        context = context or {}
        try:
            cfg = self.load_skill(name)
            if not cfg:
                return {"ok": False, "skill": name, "output": "", "rules": [],
                        "error": "skill_not_found"}
            template = cfg.get("template", "")
            try:
                output = template.format(**context) if template else cfg.get("prompt", "")
            except Exception:
                # 模板变量缺失时降级为原文
                output = template or cfg.get("prompt", "")
            return {
                "ok": True,
                "skill": name,
                "title": cfg.get("title", name),
                "output": output,
                "rules": cfg.get("rules", []),
                "prompt": cfg.get("prompt", ""),
            }
        except Exception as e:
            logger.error(f"执行技能 {name} 失败: {e}")
            return {"ok": False, "skill": name, "output": "", "rules": [], "error": str(e)}


# ====================================================================== #
# 4. 上下文持久化（ContextKeeper）
# ====================================================================== #

class ContextKeeper:
    """分析会话状态持久化：保存 / 读取 / 列举 / 恢复。

    状态文件落在 data/contexts/{session_id}.json。
    """

    def __init__(self, data_dir: str) -> None:
        self.contexts_dir = os.path.join(data_dir, "contexts")
        try:
            os.makedirs(self.contexts_dir, exist_ok=True)
        except Exception as e:  # pragma: no cover
            logger.error(f"创建上下文目录失败: {e}")

    def _path(self, session_id: str) -> str:
        # 防止路径穿越：只取文件名部分
        safe = os.path.basename(session_id)
        return os.path.join(self.contexts_dir, f"{safe}.json")

    # ------------------------------------------------------------------ #
    def save_context(self, session_id: str, state: dict[str, Any]) -> bool:
        """保存会话状态。

        Args:
            session_id: 会话 ID。
            state: 状态字典（进度 / 已完成模块 / 中间结果 / 待办步骤）。

        Returns:
            是否成功。
        """
        try:
            os.makedirs(self.contexts_dir, exist_ok=True)
            payload = {
                "session_id": session_id,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "state": state,
            }
            with open(self._path(session_id), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存会话 {session_id} 失败: {e}")
            return False

    # ------------------------------------------------------------------ #
    def load_context(self, session_id: str) -> Optional[dict[str, Any]]:
        """读取会话状态。

        Returns:
            完整 payload（含 saved_at / state），不存在返回 None。
        """
        try:
            path = self._path(session_id)
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取会话 {session_id} 失败: {e}")
            return None

    # ------------------------------------------------------------------ #
    def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有已保存会话（不含完整 state，仅元信息）。"""
        out: list[dict] = []
        try:
            if not os.path.isdir(self.contexts_dir):
                return out
            for fn in os.listdir(self.contexts_dir):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.contexts_dir, fn), "r", encoding="utf-8") as f:
                        payload = json.load(f)
                    out.append({
                        "session_id": payload.get("session_id", fn[:-5]),
                        "saved_at": payload.get("saved_at", ""),
                        "progress": (payload.get("state") or {}).get("progress", 0),
                    })
                except Exception as e:
                    logger.warning(f"会话文件 {fn} 读取失败: {e}")
        except Exception as e:
            logger.error(f"列举会话失败: {e}")
        return out

    # ------------------------------------------------------------------ #
    def resume_session(self, session_id: str) -> dict[str, Any]:
        """恢复会话：加载状态并返回带恢复标记的结构。

        Returns:
            {"ok", "session_id", "state", "pending_steps", "resumed_at"}。
        """
        try:
            payload = self.load_context(session_id)
            if not payload:
                return {"ok": False, "session_id": session_id, "state": {},
                        "pending_steps": [], "error": "session_not_found"}
            state = payload.get("state", {})
            return {
                "ok": True,
                "session_id": session_id,
                "state": state,
                "completed_modules": state.get("completed_modules", []),
                "pending_steps": state.get("pending_steps", []),
                "saved_at": payload.get("saved_at", ""),
                "resumed_at": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception as e:
            logger.error(f"恢复会话 {session_id} 失败: {e}")
            return {"ok": False, "session_id": session_id, "state": {},
                    "pending_steps": [], "error": str(e)}


# ====================================================================== #
# 5. MCP 工具集成（MCPToolRegistry）
# ====================================================================== #

class MCPToolRegistry:
    """统一外部工具注册 / 调用接口。

    调用前校验必填参数；内置 5 个工具（微信发送 / 截图 / OCR / 读文件 / 网页获取），
    其中发微信 / 截图 / OCR / 网页获取为占位实现（不真实触达外部），读文件为真实只读。
    """

    def __init__(self, project_root: Optional[str] = None) -> None:
        self._tools: dict[str, dict[str, Any]] = {}
        self._project_root = project_root or str(Path(__file__).resolve().parent.parent)
        self._register_builtins()

    # ------------------------------------------------------------------ #
    def _register_builtins(self) -> None:
        """注册内置 MCP 工具。"""
        try:
            self.register_tool("wechat_send", self._tool_wechat_send,
                               "发送微信消息（占位实现，不真实发送）",
                               required_params=["message"])
            self.register_tool("screenshot", self._tool_screenshot,
                               "屏幕截图（占位实现）", required_params=[])
            self.register_tool("ocr", self._tool_ocr,
                               "图片文字识别（占位实现）", required_params=["image_path"])
            self.register_tool("read_file", self._tool_read_file,
                               "读取项目内文本文件（只读）", required_params=["path"])
            self.register_tool("web_fetch", self._tool_web_fetch,
                               "抓取网页内容（占位实现）", required_params=["url"])
        except Exception as e:
            logger.error(f"注册内置 MCP 工具失败: {e}")

    # ---- 内置工具实现（占位 / 安全只读） ---- #
    def _tool_wechat_send(self, params: dict) -> dict:
        return {"status": "stubbed", "queued": True,
                "to": params.get("to", ""),
                "message": str(params.get("message", ""))[:80],
                "note": "微信发送为占位，未真实发出"}

    def _tool_screenshot(self, params: dict) -> dict:
        return {"status": "stubbed", "saved": False,
                "note": "截图为占位实现"}

    def _tool_ocr(self, params: dict) -> dict:
        return {"status": "stubbed", "text": "",
                "note": f"OCR 为占位实现: {params.get('image_path', '')}"}

    def _tool_read_file(self, params: dict) -> dict:
        path = params.get("path", "")
        # 限制在项目目录内，防止越权读任意文件
        full = os.path.normpath(os.path.join(self._project_root, path))
        root = os.path.normpath(self._project_root)
        if not full.startswith(root):
            return {"status": "denied", "error": "path_outside_project"}
        if not os.path.exists(full):
            return {"status": "not_found", "error": "file_not_found"}
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"status": "ok", "path": full, "size": len(content),
                "content": content[:5000]}

    def _tool_web_fetch(self, params: dict) -> dict:
        return {"status": "stubbed", "url": params.get("url", ""),
                "content": "", "note": "网页获取为占位实现，不发起网络请求"}

    # ------------------------------------------------------------------ #
    def register_tool(self, name: str, func: Callable[[dict], dict],
                      description: str = "",
                      required_params: Optional[list[str]] = None) -> bool:
        """注册工具。

        Args:
            name: 工具名。
            func: 入参 dict、出参 dict 的可调用对象。
            description: 工具描述。
            required_params: 必填参数名列表。
        """
        try:
            self._tools[name] = {
                "func": func,
                "description": description,
                "required_params": required_params or [],
            }
            return True
        except Exception as e:
            logger.error(f"注册 MCP 工具 {name} 失败: {e}")
            return False

    # ------------------------------------------------------------------ #
    def list_tools(self) -> list[dict[str, Any]]:
        """列出所有已注册工具的元信息（不含 func）。"""
        try:
            return [{"name": n, "description": t.get("description", ""),
                     "required_params": t.get("required_params", [])}
                    for n, t in self._tools.items()]
        except Exception as e:
            logger.error(f"列出 MCP 工具失败: {e}")
            return []

    # ------------------------------------------------------------------ #
    def call_tool(self, name: str, params: Optional[dict] = None) -> dict[str, Any]:
        """调用工具，调用前校验必填参数。

        Returns:
            {"ok", "result" | "error"}。
        """
        params = params or {}
        try:
            tool = self._tools.get(name)
            if not tool:
                return {"ok": False, "error": "tool_not_found", "result": None}
            missing = [p for p in tool["required_params"] if p not in params or params[p] in (None, "")]
            if missing:
                return {"ok": False, "error": "missing_params",
                        "missing": missing, "result": None}
            result = tool["func"](params)
            return {"ok": True, "result": result}
        except Exception as e:
            logger.error(f"调用 MCP 工具 {name} 失败: {e}")
            return {"ok": False, "error": str(e), "result": None}


# ====================================================================== #
# 6. 任务自动分解（TaskDecomposer）
# ====================================================================== #

class TaskDecomposer:
    """把复杂恋爱目标拆成带依赖 / 时长 / 优先级的步骤序列。

    基于关键词模板匹配（无 LLM 也能跑）；匹配不到时给通用 5 步模板。
    """

    # 关键词 -> 步骤模板
    _GOAL_TEMPLATES: list[dict[str, Any]] = [
        {
            "match": ["挽回", "复合", "前女友", "前任"],
            "steps": [
                {"action": "情绪止损", "description": "停止追问 / 认错轰炸，给自己 3 天冷静期",
                 "estimated_duration": "3天", "priority": 1},
                {"action": "复盘关系", "description": "列出分手导火索与自己的核心问题，不甩锅",
                 "estimated_duration": "2小时", "priority": 2, "prerequisite": ["情绪止损"]},
                {"action": "重建生活节奏", "description": "恢复工作 / 运动 / 社交，不在朋友圈卖惨",
                 "estimated_duration": "1-2周", "priority": 2, "prerequisite": ["情绪止损"]},
                {"action": "低压力重启联系", "description": "用一个具体小事（非情感诉求）发一条消息",
                 "estimated_duration": "1条消息", "priority": 3,
                 "prerequisite": ["复盘关系", "重建生活节奏"]},
                {"action": "观察回应节奏", "description": "按对方回复温度调整浓度，不一次推太满",
                 "estimated_duration": "1-2周", "priority": 3, "prerequisite": ["低压力重启联系"]},
                {"action": "提出一次轻见面", "description": "在对方回应积极时约一件 2 小时内的小事",
                 "estimated_duration": "1次", "priority": 4, "prerequisite": ["观察回应节奏"]},
            ],
        },
        {
            "match": ["道歉", "认错", "吵架", "生气"],
            "steps": [
                {"action": "厘清事实", "description": "写下自己具体哪件事做错，不泛化",
                 "estimated_duration": "30分钟", "priority": 1},
                {"action": "选择时机", "description": "等双方情绪平复、对方状态不差时再发",
                 "estimated_duration": "半天", "priority": 2, "prerequisite": ["厘清事实"]},
                {"action": "发送结构化道歉", "description": "具体事件 + 我的感受 + 下次怎么改，不要求立刻原谅",
                 "estimated_duration": "1条消息", "priority": 3, "prerequisite": ["选择时机"]},
                {"action": "给对方空间", "description": "发完不追问回复，正常过自己的一天",
                 "estimated_duration": "1-2天", "priority": 3, "prerequisite": ["发送结构化道歉"]},
                {"action": "用行动验证", "description": "下次同类场景按新方式做一次，而不是重复道歉",
                 "estimated_duration": "1周", "priority": 4, "prerequisite": ["给对方空间"]},
            ],
        },
        {
            "match": ["邀约", "见面", "约会"],
            "steps": [
                {"action": "捕捉兴趣点", "description": "从最近聊天里找一个对方提过的兴趣",
                 "estimated_duration": "回顾记录", "priority": 1},
                {"action": "拟定双方案", "description": "给 2 个具体时间 + 地点（对方熟悉区域）",
                 "estimated_duration": "20分钟", "priority": 2, "prerequisite": ["捕捉兴趣点"]},
                {"action": "发出邀约", "description": "用具体时间开口，不问'有空吗'",
                 "estimated_duration": "1条消息", "priority": 3, "prerequisite": ["拟定双方案"]},
                {"action": "确认细节", "description": "对方答应后定死时间 / 地点 / 时长",
                 "estimated_duration": "1-2轮", "priority": 4, "prerequisite": ["发出邀约"]},
            ],
        },
        {
            "match": ["破冰", "开场白", "不熟", "刚加"],
            "steps": [
                {"action": "找共同场景", "description": "从朋友圈 / 群聊 / 共同好友找一个轻事实",
                 "estimated_duration": "10分钟", "priority": 1},
                {"action": "生成开场白", "description": "单条不超过 30 字，带一个开放小钩子",
                 "estimated_duration": "1条消息", "priority": 2, "prerequisite": ["找共同场景"]},
                {"action": "接住对方回复", "description": "顺着对方的话往下接，不立刻查户口",
                 "estimated_duration": "2-3轮", "priority": 3, "prerequisite": ["生成开场白"]},
                {"action": "抛一个轻话题", "description": "把话题从事实过渡到观点 / 偏好",
                 "estimated_duration": "1轮", "priority": 4, "prerequisite": ["接住对方回复"]},
            ],
        },
    ]

    # 通用兜底模板
    _FALLBACK_STEPS: list[dict[str, Any]] = [
        {"action": "明确现状", "description": "记录当前关系状态与已有互动",
         "estimated_duration": "30分钟", "priority": 1},
        {"action": "拆解小目标", "description": "把大目标拆成一周内能做的一件事",
         "estimated_duration": "1小时", "priority": 2, "prerequisite": ["明确现状"]},
        {"action": "执行第一步", "description": "低压力、可量化地发出第一个动作",
         "estimated_duration": "1次", "priority": 3, "prerequisite": ["拆解小目标"]},
        {"action": "观察反馈", "description": "记录对方反应，判断浓度是否合适",
         "estimated_duration": "3天", "priority": 3, "prerequisite": ["执行第一步"]},
        {"action": "调整下一步", "description": "根据反馈加码 / 收力，再进入下一轮",
         "estimated_duration": "1周", "priority": 4, "prerequisite": ["观察反馈"]},
    ]

    # ------------------------------------------------------------------ #
    def decompose_goal(self, goal_description: str,
                       context: Optional[dict] = None) -> dict[str, Any]:
        """把复杂恋爱目标拆成步骤。

        Args:
            goal_description: 目标描述，如"挽回前女友"。
            context: 额外上下文（关系阶段 / 对方偏好等，仅记录不强制使用）。

        Returns:
            {"goal", "steps", "priority_order", "match_template"}。
            steps 每项含 action / description / estimated_duration / prerequisite / priority。
        """
        context = context or {}
        try:
            text = goal_description or ""
            chosen = None
            for tpl in self._GOAL_TEMPLATES:
                if any(kw in text for kw in tpl["match"]):
                    chosen = tpl
                    break
            steps_raw = chosen["steps"] if chosen else self._FALLBACK_STEPS

            # 给每步分配 step_id，并规范化 prerequisite
            steps: list[dict[str, Any]] = []
            for i, s in enumerate(steps_raw, start=1):
                prereq = s.get("prerequisite", []) or []
                steps.append({
                    "step_id": f"S{i}",
                    "action": s.get("action", ""),
                    "description": s.get("description", ""),
                    "estimated_duration": s.get("estimated_duration", ""),
                    "priority": s.get("priority", 3),
                    "prerequisite": prereq,
                })

            # 优先级排序（同优先级按出现顺序）
            ordered = sorted(range(len(steps)), key=lambda i: steps[i].get("priority", 9))
            priority_order = [steps[i]["step_id"] for i in ordered]

            return {
                "goal": text,
                "context": context,
                "steps": steps,
                "priority_order": priority_order,
                "match_template": chosen["match"][0] if chosen else "fallback",
            }
        except Exception as e:
            logger.error(f"分解目标失败: {e}")
            return {"goal": goal_description, "steps": [], "priority_order": [],
                    "match_template": "error", "error": str(e)}


# ====================================================================== #
# 7. 本地执行器（LocalExecutor）
# ====================================================================== #

class LocalExecutor:
    """白名单机制安全执行本地操作。

    支持的 action_type：
      - send_wechat：发送微信（占位，只记录不真实发）
      - take_screenshot：截图（占位）
      - read_file：读项目内文件（真实只读）
      - write_note：写一条笔记到 data/notes/（真实写）
      - set_reminder：设提醒（占位，只记录）
    """

    def __init__(self, project_root: Optional[str] = None) -> None:
        self._project_root = project_root or str(Path(__file__).resolve().parent.parent)
        self._data_dir = os.path.join(self._project_root, "data")
        self._notes_dir = os.path.join(self._data_dir, "notes")
        self._log_path = os.path.join(self._data_dir, "executor.log")
        try:
            os.makedirs(self._notes_dir, exist_ok=True)
        except Exception as e:  # pragma: no cover
            logger.error(f"创建 notes 目录失败: {e}")

        # 白名单：action_type -> {required, handler_name}
        self._whitelist: dict[str, dict[str, Any]] = {
            "send_wechat": {"required": ["message"]},
            "take_screenshot": {"required": []},
            "read_file": {"required": ["path"]},
            "write_note": {"required": ["content"]},
            "set_reminder": {"required": ["minutes"]},
        }

    # ------------------------------------------------------------------ #
    def _log(self, line: str) -> None:
        """追加执行日志。"""
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {line}\n")
        except Exception as e:  # pragma: no cover
            logger.warning(f"写执行日志失败: {e}")

    # ------------------------------------------------------------------ #
    def execute_action(self, action_type: str,
                       params: Optional[dict] = None) -> dict[str, Any]:
        """执行一个本地动作。

        Args:
            action_type: 白名单内的动作名。
            params: 动作参数。

        Returns:
            {"status": "ok"/"denied"/"error", "detail": ...}。
        """
        params = params or {}
        try:
            # 1) 白名单校验
            if action_type not in self._whitelist:
                self._log(f"DENY {action_type} (not in whitelist)")
                return {"status": "denied", "detail": f"action '{action_type}' not allowed"}
            required = self._whitelist[action_type]["required"]
            missing = [p for p in required if p not in params or params[p] in (None, "")]
            if missing:
                self._log(f"DENY {action_type} missing {missing}")
                return {"status": "denied", "detail": f"missing params: {missing}"}

            self._log(f"EXEC {action_type} params={ {k: (str(v)[:40]) for k,v in params.items()} }")

            # 2) 分发执行
            if action_type == "send_wechat":
                result = {"status": "stubbed", "note": "微信发送为占位，未真实发出",
                          "message": str(params["message"])[:80]}
            elif action_type == "take_screenshot":
                result = {"status": "stubbed", "note": "截图为占位"}
            elif action_type == "read_file":
                result = self._do_read_file(params["path"])
            elif action_type == "write_note":
                result = self._do_write_note(params["content"])
            elif action_type == "set_reminder":
                result = {"status": "stubbed", "minutes": params["minutes"],
                          "note": "提醒为占位，未接入系统闹钟"}
            else:  # pragma: no cover
                result = {"status": "error", "detail": "unknown"}

            # 3) 执行后验证
            if isinstance(result, dict) and result.get("status") in ("ok", "stubbed"):
                self._log(f"OK   {action_type} -> {result.get('status')}")
            else:
                self._log(f"FAIL {action_type} -> {result}")
            return {"status": result.get("status", "ok"), "detail": result}
        except Exception as e:
            logger.error(f"执行动作 {action_type} 失败: {e}")
            self._log(f"ERR  {action_type} {e}")
            return {"status": "error", "detail": str(e)}

    # ------------------------------------------------------------------ #
    def _do_read_file(self, path: str) -> dict:
        full = os.path.normpath(os.path.join(self._project_root, path))
        root = os.path.normpath(self._project_root)
        if not full.startswith(root):
            return {"status": "denied", "detail": "path outside project"}
        if not os.path.exists(full):
            return {"status": "not_found", "detail": path}
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"status": "ok", "path": full, "size": len(content),
                "preview": content[:500]}

    def _do_write_note(self, content: str) -> dict:
        os.makedirs(self._notes_dir, exist_ok=True)
        fname = f"note_{int(time.time())}_{uuid.uuid4().hex[:6]}.txt"
        path = os.path.join(self._notes_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(content))
        return {"status": "ok", "path": path, "bytes": len(str(content))}


# ====================================================================== #
# 8. 多维分析插件（AnalysisPluginRegistry）
# ====================================================================== #

class AnalysisPluginRegistry:
    """可插拔分析模块注册表。插件统一接口：analyze(data: dict) -> dict。"""

    def __init__(self) -> None:
        self._plugins: dict[str, dict[str, Any]] = {}
        self._register_builtins()

    # ------------------------------------------------------------------ #
    def _register_builtins(self) -> None:
        try:
            self.register_plugin("emotion_trend", self._plug_emotion_trend,
                                 "情感趋势分析：根据聊天历史判断情绪走向")
            self.register_plugin("reply_pattern", self._plug_reply_pattern,
                                 "回复模式分析：回复长度 / 频率特征")
            self.register_plugin("relationship_stage", self._plug_relationship_stage,
                                 "关系阶段识别：按互动密度粗判阶段")
            self.register_plugin("topic_heat", self._plug_topic_heat,
                                 "话题热度分析：统计高频话题词")
            self.register_plugin("word_style", self._plug_word_style,
                                 "用词风格分析：标点 / 长度 / 语气特征")
        except Exception as e:
            logger.error(f"注册内置分析插件失败: {e}")

    # ---- 内置插件实现 ---- #
    def _plug_emotion_trend(self, data: dict) -> dict:
        history = data.get("chat_history", []) or []
        pos = sum(1 for m in history if isinstance(m, str)
                  if any(w in m for w in ["想你", "开心", "好啊", "喜欢", "哈哈", "爱"]))
        neg = sum(1 for m in history if isinstance(m, str)
                  if any(w in m for w in ["算了", "烦", "累", "不想", "分手", "生气", "无语"]))
        total = max(len(history), 1)
        return {"positive": pos, "negative": neg,
                "positive_ratio": round(pos / total, 2),
                "trend": "up" if pos > neg else ("down" if neg > pos else "flat")}

    def _plug_reply_pattern(self, data: dict) -> dict:
        replies = data.get("replies", []) or []
        if not replies:
            return {"avg_len": 0, "short_ratio": 0.0, "count": 0}
        lengths = [len(str(r)) for r in replies]
        avg = sum(lengths) / len(lengths)
        short = sum(1 for l in lengths if l <= 5) / len(lengths)
        return {"count": len(replies), "avg_len": round(avg, 1),
                "short_reply_ratio": round(short, 2)}

    def _plug_relationship_stage(self, data: dict) -> dict:
        density = data.get("interaction_density", 0)  # 0-1
        init = data.get("partner_initiation", 0)
        if density >= 0.7 and init >= 3:
            stage, label = 6, "高浓度稳定期"
        elif density >= 0.5:
            stage, label = 4, "熟悉连接期"
        elif density >= 0.3:
            stage, label = 2, "初识破冰期"
        else:
            stage, label = 0, "陌生/降温期"
        return {"stage": stage, "label": label,
                "density": density, "partner_initiation": init}

    def _plug_topic_heat(self, data: dict) -> dict:
        messages = data.get("messages", []) or []
        keywords = data.get("keywords", ["吃饭", "电影", "旅行", "工作", "家人",
                                         "游戏", "音乐", "周末"])
        heat = {kw: sum(1 for m in messages if isinstance(m, str) and kw in m)
                for kw in keywords}
        top = sorted(heat.items(), key=lambda x: x[1], reverse=True)[:5]
        return {"heat": heat, "top_topics": [{"topic": k, "count": v} for k, v in top]}

    def _plug_word_style(self, data: dict) -> dict:
        text = str(data.get("text", ""))
        if not text:
            return {"length": 0, "question_rate": 0.0, "exclaim_rate": 0.0,
                    "avg_sentence_len": 0}
        q = text.count("?") + text.count("？")
        e = text.count("!") + text.count("！")
        sents = re.split(r"[。！？!?\n]", text)
        sents = [s for s in sents if s.strip()]
        avg = (sum(len(s) for s in sents) / len(sents)) if sents else 0
        return {"length": len(text),
                "question_rate": round(q / max(len(text), 1), 3),
                "exclaim_rate": round(e / max(len(text), 1), 3),
                "avg_sentence_len": round(avg, 1)}

    # ------------------------------------------------------------------ #
    def register_plugin(self, name: str, analyzer: Callable[[dict], dict],
                        description: str = "") -> bool:
        """注册插件。analyzer 签名：analyze(data: dict) -> dict。"""
        try:
            self._plugins[name] = {"analyzer": analyzer, "description": description}
            return True
        except Exception as e:
            logger.error(f"注册分析插件 {name} 失败: {e}")
            return False

    # ------------------------------------------------------------------ #
    def list_plugins(self) -> list[dict[str, Any]]:
        try:
            return [{"name": n, "description": p.get("description", "")}
                    for n, p in self._plugins.items()]
        except Exception as e:
            logger.error(f"列出分析插件失败: {e}")
            return []

    # ------------------------------------------------------------------ #
    def run_plugin(self, name: str, data: Optional[dict] = None) -> dict[str, Any]:
        """运行指定插件。"""
        data = data or {}
        try:
            plug = self._plugins.get(name)
            if not plug:
                return {"ok": False, "error": "plugin_not_found", "result": None}
            result = plug["analyzer"](data)
            return {"ok": True, "plugin": name, "result": result}
        except Exception as e:
            logger.error(f"运行分析插件 {name} 失败: {e}")
            return {"ok": False, "error": str(e), "result": None}


# ====================================================================== #
# 主门面：CodexAdaptation
# ====================================================================== #

class CodexAdaptation:
    """把 Codex 8 个核心能力适配到恋爱辅助场景的统一入口。

    组合了：SkillManager / ContextKeeper / MCPToolRegistry / TaskDecomposer /
    LocalExecutor / AnalysisPluginRegistry，并在此之上提供
    parallel_analysis（多 Agent 并行）与 strategy_review（策略审查）。
    """

    # 默认并行分析视角
    DEFAULT_PERSPECTIVES = ["emotion", "strategy", "twin", "risk", "opportunity"]

    def __init__(self, project_root: Optional[str] = None) -> None:
        self.project_root = project_root or str(Path(__file__).resolve().parent.parent)
        self.data_dir = os.path.join(self.project_root, "data")
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except Exception as e:  # pragma: no cover
            logger.error(f"创建 data 目录失败: {e}")

        # 子系统
        try:
            self.skill_manager = SkillManager(self.data_dir)
        except Exception as e:
            logger.error(f"初始化 SkillManager 失败: {e}")
            self.skill_manager = None  # type: ignore
        try:
            self.context = ContextKeeper(self.data_dir)
        except Exception as e:
            logger.error(f"初始化 ContextKeeper 失败: {e}")
            self.context = None  # type: ignore
        try:
            self.mcp = MCPToolRegistry(self.project_root)
        except Exception as e:
            logger.error(f"初始化 MCPToolRegistry 失败: {e}")
            self.mcp = None  # type: ignore
        try:
            self.decomposer = TaskDecomposer()
        except Exception as e:
            logger.error(f"初始化 TaskDecomposer 失败: {e}")
            self.decomposer = None  # type: ignore
        try:
            self.executor = LocalExecutor(self.project_root)
        except Exception as e:
            logger.error(f"初始化 LocalExecutor 失败: {e}")
            self.executor = None  # type: ignore
        try:
            self.plugins = AnalysisPluginRegistry()
        except Exception as e:
            logger.error(f"初始化 AnalysisPluginRegistry 失败: {e}")
            self.plugins = None  # type: ignore

        # 并行分析视角注册表：name -> callable(chat_text, person_id) -> dict
        self._analyzers: dict[str, Callable[[str, str], dict]] = {}
        self._register_builtin_analyzers()

    # ------------------------------------------------------------------ #
    # 1. 多 Agent 并行分析
    # ------------------------------------------------------------------ #
    def _register_builtin_analyzers(self) -> None:
        """注册 5 个默认视角的规则分析器。"""
        try:
            self._analyzers["emotion"] = self._an_emotion
            self._analyzers["strategy"] = self._an_strategy
            self._analyzers["twin"] = self._an_twin
            self._analyzers["risk"] = self._an_risk
            self._analyzers["opportunity"] = self._an_opportunity
        except Exception as e:
            logger.error(f"注册内置分析视角失败: {e}")

    def register_analyzer(self, name: str,
                          fn: Callable[[str, str], dict]) -> bool:
        """注册自定义分析视角（测试 / 扩展用）。"""
        try:
            self._analyzers[name] = fn
            return True
        except Exception as e:
            logger.error(f"注册分析视角 {name} 失败: {e}")
            return False

    def _an_emotion(self, chat_text: str, person_id: str) -> dict:
        pos = sum(1 for w in ["想你", "喜欢", "开心", "好啊", "哈哈", "谢谢"] if w in chat_text)
        neg = sum(1 for w in ["算了", "烦", "累", "不想", "分手", "生气", "无语"] if w in chat_text)
        return {"view": "emotion", "positive_hits": pos, "negative_hits": neg,
                "overall": "positive" if pos > neg else ("negative" if neg > pos else "neutral")}

    def _an_strategy(self, chat_text: str, person_id: str) -> dict:
        if any(w in chat_text for w in ["想你", "在干嘛"]):
            direction = "可升温"
        elif any(w in chat_text for w in ["算了", "不想", "烦"]):
            direction = "先止损"
        else:
            direction = "稳节奏"
        return {"view": "strategy", "direction": direction,
                "suggestion": "按对方回复浓度调整你的消息长度，不单向输出"}

    def _an_twin(self, chat_text: str, person_id: str) -> dict:
        return {"view": "twin", "likely_reply": "对方更可能以'嗯/哦/表情包'回应长段独白，"
                "或以具体事实回应具体事实",
                "note": "数字分身模拟：不情绪化、就事论事"}

    def _an_risk(self, chat_text: str, person_id: str) -> dict:
        risks = []
        if any(w in chat_text for w in ["必须", "立刻", "马上", "到底"]):
            risks.append("命令式词语，易激起对抗")
        if any(w in chat_text for w in ["我错了", "对不起", "求求"]):
            risks.append("过度道歉 / 乞求，易压低姿态")
        if len(chat_text) > 150:
            risks.append("消息过长，单端输出过载")
        return {"view": "risk", "risks": risks, "risk_level": "high" if risks else "low"}

    def _an_opportunity(self, chat_text: str, person_id: str) -> dict:
        opps = []
        if any(w in chat_text for w in ["一起", "约", "吃饭", "电影"]):
            opps.append("出现邀约信号，可落实具体时间")
        if any(w in chat_text for w in ["累", "难过", "压力"]):
            opps.append("出现脆弱信号，可提供陪伴而非建议")
        return {"view": "opportunity", "opportunities": opps,
                "has_signal": bool(opps)}

    # ------------------------------------------------------------------ #
    def parallel_analysis(self, chat_text: str, person_id: str = "self",
                           perspectives: Optional[list[str]] = None) -> dict[str, Any]:
        """多 Agent 并行分析：每个视角独立线程，最多 15 秒。

        Args:
            chat_text: 聊天文本。
            person_id: 对方 ID。
            perspectives: 视角名列表，默认 DEFAULT_PERSPECTIVES。

        Returns:
            {"results": {视角: 结果}, "errors": {视角: 错误}, "summary": str}。
        """
        perspectives = perspectives or self.DEFAULT_PERSPECTIVES
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        lock = threading.Lock()
        threads: list[tuple[str, threading.Thread]] = []

        def _run(name: str, fn: Callable[[str, str], dict]) -> None:
            try:
                r = fn(chat_text, person_id)
                with lock:
                    results[name] = r
            except Exception as e:
                with lock:
                    errors[name] = str(e)

        for p in perspectives:
            fn = self._analyzers.get(p)
            if fn is None:
                errors[p] = "unknown_perspective"
                continue
            t = threading.Thread(target=_run, args=(p, fn), daemon=True,
                                 name=f"codex-an-{p}")
            threads.append((p, t))
            t.start()

        for p, t in threads:
            t.join(timeout=_PARALLEL_TIMEOUT)
            if t.is_alive():
                with lock:
                    errors[p] = f"timeout_{int(_PARALLEL_TIMEOUT)}s"

        try:
            ok = len([k for k in results if k not in errors])
            summary = f"完成 {ok}/{len(perspectives)} 个视角分析"
        except Exception:
            summary = ""

        return {"results": results, "errors": errors,
                "perspectives_requested": perspectives, "summary": summary}

    # ------------------------------------------------------------------ #
    # 3. 策略审查（PR Review 恋爱版）
    # ------------------------------------------------------------------ #
    def review_message(self, message: str,
                       context: Optional[dict] = None,
                       person_id: str = "self") -> dict[str, Any]:
        """对拟发送消息做技术审查（仅做技术维度审查）。

        Args:
            message: 拟发送的消息文本。
            context: 上下文（关系阶段 / 对方最近状态等）。
            person_id: 对方 ID。

        Returns:
            {"score": 0-100, "issues": [...], "suggestions": [...],
             "tone_fit", "timing_fit", "expected_effect"}。
        """
        context = context or {}
        try:
            issues: list[dict[str, Any]] = []
            suggestions: list[str] = []
            score = 70  # 基准分

            msg = message or ""
            n = len(msg)

            # -- 长度 --
            if n == 0:
                issues.append({"level": "high", "point": "消息为空"})
                score -= 20
            elif n > 150:
                issues.append({"level": "mid", "point": f"消息过长({n}字)，单端输出过载"})
                score -= 10
                suggestions.append("拆成 1-2 条短句，把球留给对方")
            elif 10 <= n <= 80:
                score += 5

            # -- 语气：命令 / 压迫词 --
            pressure = [w for w in ["必须", "立刻", "马上", "到底", "你为什么"] if w in msg]
            if pressure:
                issues.append({"level": "high", "point": f"含命令/压迫词: {pressure}"})
                score -= 15
                suggestions.append("把'你为什么不回'改成'我这边有点惦记，你还好吗'")

            # -- 过度道歉 / 乞求 --
            beg = [w for w in ["求求", "我错了", "对不起我", "别离开我"] if w in msg]
            if beg:
                issues.append({"level": "mid", "point": f"过度道歉/乞求: {beg}"})
                score -= 10
                suggestions.append("一次说清具体哪件事，不要连续道歉")

            # -- 单向输出（无问句 / 无钩子） --
            has_q = ("?" in msg) or ("？" in msg)
            if not has_q and n > 20:
                issues.append({"level": "low", "point": "无开放式钩子，对方难接话"})
                score -= 5
                suggestions.append("结尾留一个轻问题，降低回复成本")

            # -- 加分项：真实感受表达 --
            if any(w in msg for w in ["我感到", "我觉得", "我这边"]):
                score += 10
                suggestions.append("保留这种'说自己感受不攻击对方'的表达")

            score = max(0, min(100, score))

            # -- 时机判断（基于 context） --
            stage = context.get("stage", 3)
            partner_state = context.get("partner_state", "")
            if partner_state in ("busy", "angry", "asleep"):
                timing_fit = "not_now"
                issues.append({"level": "mid", "point": f"对方当前状态:{partner_state}，建议延后"})
                score -= 5
            elif stage >= 6:
                timing_fit = "ok_close"
            else:
                timing_fit = "ok_light"

            tone_fit = "warm" if any(w in msg for w in ["谢谢", "记得", "想你"]) else "neutral"

            expected_effect = (
                "对方大概率觉得被尊重，愿意回一句" if score >= 75
                else ("可能引发压力或冷场" if score < 60
                      else "中性，取决于对方当下状态")
            )

            return {
                "score": score,
                "issues": issues,
                "suggestions": suggestions,
                "tone_fit": tone_fit,
                "timing_fit": timing_fit,
                "expected_effect": expected_effect,
                "person_id": person_id,
            }
        except Exception as e:
            logger.error(f"策略审查失败: {e}")
            return {"score": 0, "issues": [], "suggestions": [],
                    "tone_fit": "unknown", "timing_fit": "unknown",
                    "expected_effect": "", "error": str(e)}

    # ------------------------------------------------------------------ #
    # 便捷转发（保持与 8 大功能一一对应）
    # ------------------------------------------------------------------ #
    def list_skills(self) -> list[dict]:
        return self.skill_manager.list_skills() if self.skill_manager else []

    def load_skill(self, name: str):
        return self.skill_manager.load_skill(name) if self.skill_manager else None

    def execute_skill(self, name: str, context: Optional[dict] = None) -> dict:
        return self.skill_manager.execute_skill(name, context) if self.skill_manager \
            else {"ok": False, "error": "skill_manager_unavailable"}

    def save_context(self, session_id: str, state: dict) -> bool:
        return self.context.save_context(session_id, state) if self.context else False

    def load_context(self, session_id: str):
        return self.context.load_context(session_id) if self.context else None

    def list_sessions(self) -> list[dict]:
        return self.context.list_sessions() if self.context else []

    def resume_session(self, session_id: str) -> dict:
        return self.context.resume_session(session_id) if self.context \
            else {"ok": False, "error": "context_unavailable"}

    def decompose_goal(self, goal_description: str,
                       context: Optional[dict] = None) -> dict:
        return self.decomposer.decompose_goal(goal_description, context) \
            if self.decomposer else {"goal": goal_description, "steps": []}

    def execute_action(self, action_type: str,
                       params: Optional[dict] = None) -> dict:
        return self.executor.execute_action(action_type, params) if self.executor \
            else {"status": "error", "detail": "executor_unavailable"}


__all__ = [
    "CodexAdaptation",
    "SkillManager",
    "ContextKeeper",
    "MCPToolRegistry",
    "TaskDecomposer",
    "LocalExecutor",
    "AnalysisPluginRegistry",
]
