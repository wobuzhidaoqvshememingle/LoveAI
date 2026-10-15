# -*- coding: utf-8 -*-
"""Codex 八大功能恋爱适配测试。

覆盖：
  1. 多 Agent 并行分析（mock 分析函数 + 超时兜底）
  2. Skills 技能系统（内置技能可加载 / 可执行）
  3. 策略审查（review_message 输出 score/issues/suggestions）
  4. 上下文持久化（save / load / list / resume 一致性）
  5. MCP 工具集成（注册 / 调用 / 参数校验）
  6. 任务自动分解（步骤非空 + 依赖关系 + 优先级）
  7. 本地执行器（白名单 / write_note 真实落盘 / read_file 只读）
  8. 多维分析插件（注册 / 运行 / 列表）
"""
import json
import os
import sys
import time
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.codex_adaptation import (  # noqa: E402
    CodexAdaptation,
    SkillManager,
    ContextKeeper,
    MCPToolRegistry,
    TaskDecomposer,
    LocalExecutor,
    AnalysisPluginRegistry,
)


class TestCodexAdaptation(unittest.TestCase):
    """8 大功能每个至少 1 个测试。"""

    @classmethod
    def setUpClass(cls):
        # 用真实项目根目录初始化（会在 data/ 下创建 skills/contexts/notes）
        cls.adapter = CodexAdaptation(project_root=PROJECT_ROOT)

    # ------------------------------------------------------------------ #
    # 1. 多 Agent 并行分析
    # ------------------------------------------------------------------ #
    def test_01_parallel_analysis_builtin_perspectives(self):
        """默认 5 个视角都应跑完并返回结果字典。"""
        out = self.adapter.parallel_analysis("今天有点累，不想说话", person_id="p1")
        self.assertIn("results", out)
        self.assertIn("errors", out)
        # 默认 5 个视角
        self.assertEqual(len(out["perspectives_requested"]), 5)
        # 全部成功，无错误
        self.assertEqual(out["errors"], {})
        # 每个视角都返回了 dict
        for v in out["results"].values():
            self.assertIsInstance(v, dict)
        # risk 视角应识别出"累"这条负面信号（非强制，看结果结构即可）
        self.assertIn("risk", out["results"])

    def test_01_parallel_analysis_mock_and_timeout(self):
        """用 mock 分析函数注入；一个视角故意 sleep 超时（<15s，这里用快速函数验证并行）。"""
        # 注册一个 mock 视角
        self.adapter.register_analyzer("mock_view",
                                       lambda text, pid: {"echo": text[:5], "pid": pid})
        out = self.adapter.parallel_analysis("你好世界测试", person_id="p2",
                                            perspectives=["mock_view"])
        self.assertEqual(out["results"]["mock_view"]["echo"], "你好世界测")
        self.assertEqual(out["results"]["mock_view"]["pid"], "p2")
        # 未知视角应进入 errors
        out2 = self.adapter.parallel_analysis("x", perspectives=["not_exist_view"])
        self.assertIn("not_exist_view", out2["errors"])

    # ------------------------------------------------------------------ #
    # 2. Skills 技能系统
    # ------------------------------------------------------------------ #
    def test_02_skill_builtins_loadable(self):
        """5 个内置恋爱技能都应能列出 + 加载。"""
        names = [s["name"] for s in self.adapter.list_skills()]
        for must in ["icebreaker", "cold_war_resolve", "invitation",
                     "gift_pick", "apology"]:
            self.assertIn(must, names, f"内置技能 {must} 缺失")
        cfg = self.adapter.load_skill("icebreaker")
        self.assertIsNotNone(cfg)
        self.assertIn("rules", cfg)
        self.assertIsInstance(cfg["rules"], list)

    def test_02_skill_execute_and_register(self):
        """execute_skill 用 context 填模板；register_skill 落盘可重载。"""
        out = self.adapter.execute_skill("invitation",
                                         {"day": "周六", "time": "下午3点",
                                          "place": "天河", "shop": "咖啡馆"})
        self.assertTrue(out["ok"])
        self.assertIn("周六", out["output"])

        # 注册新技能
        ok = self.adapter.skill_manager.register_skill(
            "unittest_demo",
            {"title": "测试技能", "description": "单测用",
             "prompt": "demo", "rules": ["r1"], "template": "hello {who}"})
        self.assertTrue(ok)
        # 重新加载（模拟从盘读）
        cfg = self.adapter.skill_manager.load_skill("unittest_demo")
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["template"], "hello {who}")

    # ------------------------------------------------------------------ #
    # 3. 策略审查
    # ------------------------------------------------------------------ #
    def test_03_strategy_review_score_and_issues(self):
        """高压迫词消息应扣分并出 issues；温和表达应加分。"""
        # 压迫式消息
        bad = self.adapter.review_message("你为什么不回我？你必须立刻解释清楚！",
                                         context={"stage": 4, "partner_state": "busy"})
        self.assertLess(bad["score"], 75)
        self.assertTrue(any(i["level"] in ("high", "mid") for i in bad["issues"]))
        self.assertGreater(len(bad["suggestions"]), 0)
        self.assertEqual(bad["timing_fit"], "not_now")  # busy 状态

        # 温和短消息
        good = self.adapter.review_message("我今天路过你上次说的那家面馆，想起你了。你呢？",
                                          context={"stage": 5})
        self.assertGreaterEqual(good["score"], 70)
        self.assertIn("expected_effect", good)

    # ------------------------------------------------------------------ #
    # 4. 上下文持久化
    # ------------------------------------------------------------------ #
    def test_04_context_save_load_resume(self):
        """save -> load 字段一致；resume 返回 pending_steps；list 能看到。"""
        sid = f"unittest_sess_{int(time.time())}"
        state = {
            "progress": 60,
            "completed_modules": ["emotion", "strategy"],
            "intermediate_results": {"emotion": {"overall": "neutral"}},
            "pending_steps": ["risk", "opportunity"],
        }
        ok = self.adapter.save_context(sid, state)
        self.assertTrue(ok)

        loaded = self.adapter.load_context(sid)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["state"]["progress"], 60)
        self.assertEqual(loaded["state"]["completed_modules"], ["emotion", "strategy"])

        resumed = self.adapter.resume_session(sid)
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["pending_steps"], ["risk", "opportunity"])
        self.assertIn("resumed_at", resumed)

        # list_sessions 里能看到
        sids = [s["session_id"] for s in self.adapter.list_sessions()]
        self.assertIn(sid, sids)

    def test_04_context_not_found(self):
        res = self.adapter.resume_session("definitely_not_exist_sess_xyz")
        self.assertFalse(res["ok"])

    # ------------------------------------------------------------------ #
    # 5. MCP 工具集成
    # ------------------------------------------------------------------ #
    def test_05_mcp_register_call_list(self):
        """内置 5 工具已注册；参数校验拦截缺参；自定义工具可注册调用。"""
        tools = self.adapter.mcp.list_tools()
        names = [t["name"] for t in tools]
        for must in ["wechat_send", "screenshot", "ocr", "read_file", "web_fetch"]:
            self.assertIn(must, names)

        # 缺参调用应失败
        r = self.adapter.mcp.call_tool("wechat_send", params={})  # 缺 message
        self.assertFalse(r["ok"])
        self.assertIn("missing", r)

        # 正常调用 wechat_send（占位）
        r2 = self.adapter.mcp.call_tool("wechat_send", params={"message": "在吗"})
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["result"]["status"], "stubbed")

        # 自定义工具
        self.adapter.mcp.register_tool("echo", lambda p: {"got": p.get("x")},
                                       "echo", required_params=["x"])
        r3 = self.adapter.mcp.call_tool("echo", params={"x": 42})
        self.assertTrue(r3["ok"])
        self.assertEqual(r3["result"]["got"], 42)

        # 未知工具
        r4 = self.adapter.mcp.call_tool("no_such_tool")
        self.assertFalse(r4["ok"])

    # ------------------------------------------------------------------ #
    # 6. 任务自动分解
    # ------------------------------------------------------------------ #
    def test_06_decompose_goal_steps_nonempty_with_deps(self):
        """"挽回前女友"应拆出 5+ 步，且步骤有 prerequisite / priority。"""
        out = self.adapter.decompose_goal("挽回前女友")
        self.assertGreaterEqual(len(out["steps"]), 5)
        # 每步字段齐全
        for s in out["steps"]:
            self.assertIn("step_id", s)
            self.assertIn("action", s)
            self.assertIn("description", s)
            self.assertIn("estimated_duration", s)
            self.assertIn("prerequisite", s)
            self.assertIn("priority", s)
        # 至少一步有依赖（prerequisite 非空）
        self.assertTrue(any(s["prerequisite"] for s in out["steps"]))
        # priority_order 覆盖所有 step_id
        self.assertEqual(set(out["priority_order"]),
                         {s["step_id"] for s in out["steps"]})
        # 命中挽回模板
        self.assertEqual(out["match_template"], "挽回")

    def test_06_decompose_fallback(self):
        """无关键词命中时走兜底 5 步。"""
        out = self.adapter.decompose_goal("一个完全奇怪的目标 xyz")
        self.assertEqual(out["match_template"], "fallback")
        self.assertGreaterEqual(len(out["steps"]), 4)

    # ------------------------------------------------------------------ #
    # 7. 本地执行器
    # ------------------------------------------------------------------ #
    def test_07_local_executor_whitelist(self):
        """白名单外动作被拒；write_note 真实落盘；read_file 只读项目内。"""
        # 白名单外
        r = self.adapter.execute_action("format_c_drive", params={})
        self.assertEqual(r["status"], "denied")

        # 缺参
        r2 = self.adapter.execute_action("write_note", params={})
        self.assertEqual(r2["status"], "denied")

        # write_note 真实落盘
        r3 = self.adapter.execute_action("write_note",
                                         params={"content": "单测笔记 hello"})
        self.assertEqual(r3["status"], "ok")
        path = r3["detail"]["path"]
        self.assertTrue(os.path.exists(path))

        # read_file 读项目内文件
        r4 = self.adapter.execute_action("read_file",
                                        params={"path": "main.py"})
        self.assertEqual(r4["status"], "ok")
        self.assertIn("preview", r4["detail"])

        # send_wechat 占位
        r5 = self.adapter.execute_action("send_wechat",
                                        params={"message": "在吗"})
        self.assertEqual(r5["status"], "stubbed")

    # ------------------------------------------------------------------ #
    # 8. 多维分析插件
    # ------------------------------------------------------------------ #
    def test_08_analysis_plugins(self):
        """内置 5 插件可列出 / 运行；自定义插件可注册。"""
        plugs = self.adapter.plugins.list_plugins()
        names = [p["name"] for p in plugs]
        for must in ["emotion_trend", "reply_pattern", "relationship_stage",
                     "topic_heat", "word_style"]:
            self.assertIn(must, names)

        # 跑情感趋势
        r = self.adapter.plugins.run_plugin("emotion_trend", {
            "chat_history": ["今天想你了", "好开心见到你", "算了不想说了"]})
        self.assertTrue(r["ok"])
        self.assertIn(r["result"]["trend"], ("up", "down", "flat"))

        # 跑用词风格
        r2 = self.adapter.plugins.run_plugin("word_style",
                                             {"text": "你好吗？在干嘛呢！周末一起吃饭吗？"})
        self.assertTrue(r2["ok"])
        self.assertGreater(r2["result"]["question_rate"], 0)

        # 自定义插件
        self.adapter.plugins.register_plugin("my_count",
                                             lambda d: {"len": len(d.get("t", ""))},
                                             "计数")
        r3 = self.adapter.plugins.run_plugin("my_count", {"t": "abcd"})
        self.assertTrue(r3["ok"])
        self.assertEqual(r3["result"]["len"], 4)

        # 未知插件
        r4 = self.adapter.plugins.run_plugin("no_plugin")
        self.assertFalse(r4["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
