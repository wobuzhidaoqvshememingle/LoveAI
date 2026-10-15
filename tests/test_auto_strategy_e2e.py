# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - R3.3-47 端到端：NPD 聊天 -> 识别 -> 反制策略 -> 3 条具体话术。

不 mock 任何业务逻辑：直接喂 12 条典型 NPD 聊天（贬低/煤气灯/合理化/孤立），
跑 auto_strategy_pipeline.run()，断言：
  1. persona 里识别到 NPD（primary_personality 含 'npd'/'NPD'/'自恋'）
     且 dark_triad_tendencies.narcissism > 0.6
  2. strategy 里包含 manipulation_counter（反制操纵）且 triggered=True
  3. candidates 是 3 条具体可复制的话术（不是"你应该…"原则性话）
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.auto_strategy_pipeline import run  # noqa: E402


NPD_CHATS = [
    {"role": "other", "text": "你太敏感了，这点事也值得生气？"},
    {"role": "other", "text": "我从没说过那种话，是你自己听错了。"},
    {"role": "other", "text": "都是你的错，你要是不闹我会这样吗？"},
    {"role": "other", "text": "你有病吧，连这点小事都做不好。"},
    {"role": "other", "text": "只有我能忍你，你想多了，离开我你谁都找不到。"},
    {"role": "other", "text": "我这都是为你好，你怎么不知好歹？"},
    {"role": "user", "text": "我只是想我们好好聊聊。"},
    {"role": "other", "text": "行啊，那你说，我听着。（转头就刷手机）"},
    {"role": "other", "text": "你朋友都不了解你，她们就是想看我们分手。"},
    {"role": "user", "text": "可你上周明明答应过我……"},
    {"role": "other", "text": "我答应过？我怎么不记得了。你是不是有臆想症？"},
    {"role": "other", "text": "你要是离开我就没人要了，自己掂量。"},
]


class TestAutoStrategyNpdE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run({
            "person_id": "npd_e2e",
            "recent_chats": NPD_CHATS,
            "user_intent": "修复",
        })

    def test_returns_required_top_level_keys(self):
        for k in ("persona", "six_layer", "strategy", "tasks", "candidates",
                  "prediction", "confidence"):
            self.assertIn(k, self.result, f"缺少顶层字段 {k}")

    def test_persona_detects_npd(self):
        persona = self.result["persona"]
        primary = str(persona.get("primary_personality")
                      or persona.get("primary_key") or "")
        self.assertTrue(
            any(kw in primary.lower() for kw in ("npd", "自恋")),
            f"persona 没识别到 NPD: primary={primary!r}")

    def test_dark_triad_score_above_threshold(self):
        psy = self.result["persona"].get("psychology") or {}
        dt = psy.get("dark_triad_tendencies") or {}
        narcissism = float(dt.get("narcissism") or 0.0)
        self.assertGreater(
            narcissism, 0.6,
            f"dark_triad.narcissism={narcissism} 未超过 0.6 阈值")

    def test_strategy_contains_manipulation_counter(self):
        strategy = self.result["strategy"]
        mc = strategy.get("manipulation_counter") or {}
        self.assertTrue(mc.get("triggered"),
                        "strategy.manipulation_counter.triggered 应为 True")
        self.assertGreaterEqual(
            len(mc.get("principles") or []), 2,
            "manipulation_counter 至少要给 2 条反制原则")

    def test_candidates_are_three_concrete_lines(self):
        cands = self.result["candidates"]
        self.assertEqual(len(cands), 3, "candidates 必须是 3 条")
        for c in cands:
            self.assertIsInstance(c, str)
            self.assertGreater(len(c), 10, f"话术过短不像具体可复制句: {c!r}")
            # 不能是原则性的"你应该…/你要…"
            self.assertFalse(c.strip().startswith(("你应该", "你要", "建议你")),
                             f"话术是原则性指示，不是可复制成句: {c!r}")

    def test_candidates_are_npd_counter_specific(self):
        """NPD 话术应包含划界/引用原话/不接贬低的语义关键词。"""
        joined = " ".join(self.result["candidates"])
        # 至少命中一个反制关键词
        self.assertTrue(
            any(kw in joined for kw in ("我不", "不接受", "引用", "原话", "划界",
                                        "停一下", "各有各的责任", "背")),
            f"3 条话术里看不到 NPD 反制语义: {self.result['candidates']}")


if __name__ == "__main__":
    unittest.main()
