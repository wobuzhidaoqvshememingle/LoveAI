# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - DigitalTwin 单元测试

覆盖：
  - 无 SOUL.md 时使用默认档案
  - _parse_json 四种格式（直解 / json 代码块 / 首尾花括号 / raw 兜底）
  - _build_system_prompt 非空
  - accuracy_score（无记录=0.0 / 有记录后正确计算）
  - simulate_reply / predict_reaction / compare_options 接口结构（mock LLM）
  - record_feedback 落盘 feedback.json
  - feedback.json 跨实例恢复
  - generate_profile 落盘 SOUL.md + personality.json
  - LLM 异常时降级
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.digital_twin import DigitalTwin  # noqa: E402


class TestDigitalTwin(unittest.TestCase):
    """DigitalTwin 测试类（临时 SQLite + 临时 data_dir）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_twin_")
        self.db_path = os.path.join(self.tmpdir, "test_twin.db")
        self.data_dir = os.path.join(self.tmpdir, "data")
        self.db = Database(self.db_path)
        self.db.init_tables()
        # 插入一个人物，供 _fetch_person_basic 兜底查询
        self.db.execute(
            "INSERT INTO persons (id, nickname, gender, occupation) "
            "VALUES (:id, :n, :g, :o)",
            {"id": "p1", "n": "苏晚", "g": "女", "o": "设计师"},
        )
        self.llm = MagicMock()
        self.rag = MagicMock()
        self.twin = DigitalTwin("p1", self.db, self.llm, self.rag,
                               data_dir=self.data_dir)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化 / 默认档案
    # ------------------------------------------------------------------ #
    def test_init_no_soul_uses_default(self) -> None:
        """无 SOUL.md 时应加载默认占位档案，personality 各维度默认 50。"""
        self.assertIn("默认占位", self.twin.soul)
        self.assertIn("苏晚", self.twin.soul)
        for k in ("openness", "conscientiousness", "extraversion",
                  "agreeableness", "neuroticism"):
            self.assertEqual(self.twin.personality[k], 50)

    # ------------------------------------------------------------------ #
    # _parse_json 四种格式
    # ------------------------------------------------------------------ #
    def test_parse_json_direct(self) -> None:
        """格式1：直接可解析的 JSON。"""
        out = self.twin._parse_json('{"emotion": "happy", "confidence": 0.8}')
        self.assertEqual(out["emotion"], "happy")
        self.assertAlmostEqual(out["confidence"], 0.8)

    def test_parse_json_codeblock(self) -> None:
        """格式2：```json ... ``` 代码块。"""
        raw = '```json\n{"a": 1, "b": 2}\n```'
        out = self.twin._parse_json(raw)
        self.assertEqual(out, {"a": 1, "b": 2})

    def test_parse_json_brace_snippet(self) -> None:
        """格式3：前后有多余文字，截取首 { 到末 }。"""
        raw = '好的，结果如下：{"x": 10} 以上。'
        out = self.twin._parse_json(raw)
        self.assertEqual(out, {"x": 10})

    def test_parse_json_raw_fallback(self) -> None:
        """格式4：完全解析失败时返回 {"raw": text}。"""
        out = self.twin._parse_json("这根本不是 JSON")
        self.assertEqual(out, {"raw": "这根本不是 JSON"})

    # ------------------------------------------------------------------ #
    # system prompt
    # ------------------------------------------------------------------ #
    def test_build_system_prompt_nonempty(self) -> None:
        """_build_system_prompt 应返回非空字符串且包含人格描述。"""
        prompt = self.twin._build_system_prompt([], {"total_score": 60})
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 50)
        self.assertIn("苏晚", prompt)
        self.assertIn("扮演", prompt)

    # ------------------------------------------------------------------ #
    # 准确率
    # ------------------------------------------------------------------ #
    def test_accuracy_no_records(self) -> None:
        """无反馈记录时准确率为 0.0。"""
        self.assertEqual(self.twin.accuracy_score(), 0.0)

    def test_accuracy_with_records(self) -> None:
        """2 accurate / 4 条总记录 = 0.5。"""
        self.twin.record_feedback("i1", "accurate")
        self.twin.record_feedback("i2", "accurate")
        self.twin.record_feedback("i3", "partial")
        self.twin.record_feedback("i4", "inaccurate")
        self.assertEqual(self.twin.accuracy_score(), 0.5)

    # ------------------------------------------------------------------ #
    # simulate_reply / predict_reaction / compare_options
    # ------------------------------------------------------------------ #
    def test_simulate_reply(self) -> None:
        """simulate_reply 应返回 LLM 文本并去除首尾引号。"""
        self.llm.chat.return_value = '"好呀，周末见"'
        reply = self.twin.simulate_reply("context", [], {})
        self.assertEqual(reply, "好呀，周末见")

    def test_simulate_reply_llm_error(self) -> None:
        """LLM 抛异常时 simulate_reply 走规则模板句兜底，返回非空且不崩溃。"""
        self.llm.chat.side_effect = RuntimeError("boom")
        reply = self.twin.simulate_reply("x", [], {})
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply), 0)

    def test_predict_reaction_structure(self) -> None:
        """predict_reaction 返回约定结构，confidence 裁剪到 [0,1]。"""
        self.llm.chat.return_value = json.dumps({
            "emotion": "happy",
            "inner_thought": "有点开心",
            "possible_replies": ["好啊", "可以"],
            "action": "答应邀约",
            "confidence": 0.8,
        })
        out = self.twin.predict_reaction("约她吃饭", [], {})
        for k in ("emotion", "inner_thought", "possible_replies",
                  "action", "confidence"):
            self.assertIn(k, out)
        self.assertIsInstance(out["possible_replies"], list)
        self.assertEqual(len(out["possible_replies"]), 2)
        self.assertGreaterEqual(out["confidence"], 0.0)
        self.assertLessEqual(out["confidence"], 1.0)

    def test_compare_options_structure(self) -> None:
        """compare_options 返回 option_a/option_b/preferred/reason。"""
        self.llm.chat.return_value = json.dumps({
            "option_a": {"emotion": "ok", "reply": "嗯",
                         "affection_delta": 1, "risk": "低"},
            "option_b": {"emotion": "一般", "reply": "哦",
                         "affection_delta": -1, "risk": "中"},
            "preferred": "a",
            "reason": "A 更自然",
        })
        out = self.twin.compare_options("方案A", "方案B", [], {})
        self.assertIn("option_a", out)
        self.assertIn("option_b", out)
        self.assertIn(out["preferred"], ("a", "b"))
        self.assertIn("reason", out)
        self.assertIsInstance(out["option_a"]["affection_delta"], float)

    # ------------------------------------------------------------------ #
    # 反馈记录 / 持久化
    # ------------------------------------------------------------------ #
    def test_record_feedback_writes_log(self) -> None:
        """record_feedback 应追加到 feedback_log 并落盘 feedback.json。"""
        self.twin.record_feedback("p1-1", "accurate", note="猜对了")
        self.assertEqual(len(self.twin.feedback_log), 1)
        self.assertEqual(self.twin.feedback_log[0]["score"], "accurate")
        fb_path = os.path.join(self.twin.person_dir, "feedback.json")
        self.assertTrue(os.path.exists(fb_path))
        with open(fb_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIsInstance(data, list)
        self.assertEqual(data[0]["note"], "猜对了")

    def test_feedback_persist_across_instances(self) -> None:
        """feedback.json 应能被新实例恢复。"""
        self.twin.record_feedback("p1-2", "accurate")
        self.twin.record_feedback("p1-3", "partial")
        # 新实例
        twin2 = DigitalTwin("p1", self.db, MagicMock(), MagicMock(),
                             data_dir=self.data_dir)
        self.assertEqual(len(twin2.feedback_log), 2)
        scores = {f["score"] for f in twin2.feedback_log}
        self.assertEqual(scores, {"accurate", "partial"})

    # ------------------------------------------------------------------ #
    # generate_profile 落盘
    # ------------------------------------------------------------------ #
    def test_generate_profile_writes_files(self) -> None:
        """generate_profile 应生成 SOUL.md 与 personality.json 并更新内存。"""
        self.llm.load_prompt.return_value = "prompt text"
        self.llm.chat.return_value = (
            "# 苏晚的人格速写\n温柔、慢热、喜欢咖啡。\n\n"
            "## personality\n"
            '{"openness":60,"conscientiousness":55,"extraversion":40,'
            '"agreeableness":80,"neuroticism":30}'
        )
        out = self.twin.generate_profile({"nickname": "苏晚"})
        self.assertIn("soul", out)
        self.assertIn("personality", out)
        soul_path = os.path.join(self.twin.person_dir, "SOUL.md")
        pers_path = os.path.join(self.twin.person_dir, "personality.json")
        self.assertTrue(os.path.exists(soul_path))
        self.assertTrue(os.path.exists(pers_path))
        with open(pers_path, "r", encoding="utf-8") as f:
            pers = json.load(f)
        self.assertEqual(pers["openness"], 60)
        self.assertIn("温柔", self.twin.soul)


if __name__ == "__main__":
    unittest.main(verbosity=2)
