# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - PluginService 单元测试

覆盖：
  - __init__ 不崩溃
  - _scan_skills / list_skills / load_skill / execute_skill
  - _scan_plugins 空目录 / list_plugins / load_plugin 不存在
  - enable / disable 插件
  - install_plugin_from_path 路径校验
  - 坏插件隔离不崩溃 / 按 category 过滤技能
"""
import os
import sys
import json
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.plugin_service import PluginService  # noqa: E402


def _write(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


WORKING_PLUGIN = '''
"""一个可用测试插件。"""
PLUGIN_META = {"name": "greet", "version": "1.0", "description": "打招呼"}


class Plugin:
    def __init__(self):
        self.activated = False

    def activate(self):
        self.activated = True

    def deactivate(self):
        self.activated = False
'''

BROKEN_PLUGIN = '''
"""语法错误插件，用于测试隔离。"""
def broken(:::):
'''


class TestPluginService(unittest.TestCase):
    """PluginService 测试类（skills/plugins 目录重定向到临时目录）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_plugin_")
        self.skills_dir = os.path.join(self.tmpdir, "skills")
        self.plugins_dir = os.path.join(self.tmpdir, "plugins")
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.plugins_dir, exist_ok=True)
        # 构造（扫描真实目录只读），随后重定向到临时目录并清空缓存
        self.svc = PluginService(db=None, config=None, signals=None)
        self.svc.skills_dir = self.skills_dir
        self.svc.plugins_dir = self.plugins_dir
        self.svc.available_skills = []
        self.svc.available_plugins = []
        self.svc.loaded_skills = {}
        self.svc.loaded_plugins = {}

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def test_init_no_crash(self) -> None:
        self.assertIsInstance(self.svc.available_skills, list)
        self.assertIsInstance(self.svc.available_plugins, list)

    # ------------------------------------------------------------------ #
    # 技能扫描 / 加载 / 执行
    # ------------------------------------------------------------------ #
    def test_scan_skills_directory_type(self) -> None:
        """目录型技能（含 SKILL.md + SKILL.json）应被发现。"""
        d = os.path.join(self.skills_dir, "mycmd")
        _write(os.path.join(d, "SKILL.md"), "# 指令")
        _write(os.path.join(d, "SKILL.json"),
               json.dumps({"name": "mycmd", "category": "tool",
                           "description": "测试技能"}))
        skills = self.svc._scan_skills()
        names = [s["name"] for s in skills]
        self.assertIn("mycmd", names)

    def test_scan_skills_file_type(self) -> None:
        """扁平 .md 文件型技能应被发现。"""
        _write(os.path.join(self.skills_dir, "hello.md"), "# 你好")
        skills = self.svc._scan_skills()
        names = [s["name"] for s in skills]
        self.assertIn("hello", names)

    def test_list_skills(self) -> None:
        """list_skills 返回结构化列表。"""
        _write(os.path.join(self.skills_dir, "a.md"), "# A")
        self.svc._scan_skills()
        result = self.svc.list_skills()
        self.assertTrue(any(s["name"] == "a" for s in result))
        self.assertIn("category", result[0])

    def test_load_skill_reads_md(self) -> None:
        """load_skill 应读取 SKILL.md 内容。"""
        d = os.path.join(self.skills_dir, "readme_skill")
        _write(os.path.join(d, "SKILL.md"), "## 执行流程\n第一步打招呼")
        self.svc._scan_skills()
        loaded = self.svc.load_skill("readme_skill")
        self.assertTrue(loaded["loaded"])
        self.assertIn("执行流程", loaded["content"])

    def test_load_skill_not_exists(self) -> None:
        """加载不存在技能返回 error。"""
        loaded = self.svc.load_skill("no_such_skill")
        self.assertFalse(loaded["loaded"])
        self.assertIn("error", loaded)

    def test_execute_skill_returns_instructions(self) -> None:
        """纯指令型技能 execute_skill 返回 instructions 文本。"""
        _write(os.path.join(self.skills_dir, "instr.md"), "按步骤回复对方")
        self.svc._scan_skills()
        result = self.svc.execute_skill("instr")
        self.assertTrue(result["executed"])
        self.assertEqual(result["type"], "instructions")
        self.assertIn("回复", result["instructions"])

    def test_filter_skills_by_category(self) -> None:
        """按 category 过滤技能。"""
        for name, cat in (("toolone", "tool"), ("helpone", "helper")):
            d = os.path.join(self.skills_dir, name)
            _write(os.path.join(d, "SKILL.md"), "# x")
            _write(os.path.join(d, "SKILL.json"),
                   json.dumps({"name": name, "category": cat}))
        self.svc._scan_skills()
        tool_only = self.svc.list_skills(category="tool")
        names = [s["name"] for s in tool_only]
        self.assertIn("toolone", names)
        self.assertNotIn("helpone", names)

    # ------------------------------------------------------------------ #
    # 插件扫描 / 加载 / 启停
    # ------------------------------------------------------------------ #
    def test_scan_plugins_empty_dir(self) -> None:
        """空插件目录返回空列表。"""
        self.assertEqual(self.svc._scan_plugins(), [])

    def test_list_plugins(self) -> None:
        """list_plugins 返回列表。"""
        self.svc._scan_plugins()
        self.assertIsInstance(self.svc.list_plugins(), list)

    def test_load_plugin_not_exists(self) -> None:
        """加载不存在插件返回 error。"""
        result = self.svc.load_plugin("ghost")
        self.assertFalse(result["loaded"])
        self.assertIn("插件不存在", result["error"])

    def test_enable_disable_plugin(self) -> None:
        """可用插件 enable/disable 状态流转。"""
        _write(os.path.join(self.plugins_dir, "greet.py"), WORKING_PLUGIN)
        self.svc._scan_plugins()
        self.assertTrue(any(p["name"] == "greet" for p in self.svc.available_plugins))

        res = self.svc.enable_plugin("greet")
        self.assertTrue(res["enabled"])
        self.assertTrue(self.svc.loaded_plugins["greet"]["enabled"])

        dis = self.svc.disable_plugin("greet")
        self.assertTrue(dis["disabled"])
        self.assertNotIn("greet", self.svc.loaded_plugins)

    def test_broken_plugin_isolated(self) -> None:
        """坏插件（语法错误）不影响扫描与其他插件，加载时返回 error。"""
        _write(os.path.join(self.plugins_dir, "broken.py"), BROKEN_PLUGIN)
        # 扫描不应崩溃
        plugins = self.svc._scan_plugins()
        self.assertTrue(any(p["name"] == "broken" for p in plugins))
        # 加载坏插件返回 error，不抛异常
        res = self.svc.load_plugin("broken")
        self.assertFalse(res["loaded"])
        self.assertIn("error", res)

    # ------------------------------------------------------------------ #
    # 安装
    # ------------------------------------------------------------------ #
    def test_install_plugin_nonexistent_path(self) -> None:
        """安装不存在的源路径返回 error。"""
        res = self.svc.install_plugin_from_path(os.path.join(self.tmpdir, "nope.py"))
        self.assertFalse(res["installed"])
        self.assertIn("不存在", res["error"])

    def test_install_plugin_py_file(self) -> None:
        """从单个 .py 文件安装插件成功并可被发现。"""
        src = os.path.join(self.tmpdir, "srcplug.py")
        _write(src, WORKING_PLUGIN)
        res = self.svc.install_plugin_from_path(src)
        self.assertTrue(res["installed"])
        self.assertEqual(res["name"], "srcplug")
        names = [p["name"] for p in self.svc.list_plugins()]
        self.assertIn("srcplug", names)

    def test_install_plugin_wrong_type(self) -> None:
        """源为非 .py 非目录文件时拒绝安装。"""
        f = os.path.join(self.tmpdir, "note.txt")
        _write(f, "hello")
        res = self.svc.install_plugin_from_path(f)
        self.assertFalse(res["installed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
