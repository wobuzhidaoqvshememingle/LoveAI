# -*- coding: utf-8 -*-
"""数字分身 v2 新功能单测：人格风格 / 状态调制 / 历史残留。

不构造 DigitalTwin 实例（避免 db/llm 依赖），直接调 classmethod/staticmethod。
"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.digital_twin import DigitalTwin  # noqa: E402


class TestDigitalTwinV2(unittest.TestCase):

    # ------------------------------------------------------------------ #
    # _personality_style
    # ------------------------------------------------------------------ #
    def test_personality_style_narcissistic(self):
        s = DigitalTwin._personality_style({"attachment_style": "自恋型"})
        self.assertEqual(s["tone"], "self_centered")
        self.assertGreaterEqual(s["edge"], 0.7)

    def test_personality_style_avoid(self):
        s = DigitalTwin._personality_style({"attachment_style": "回避型"})
        self.assertEqual(s["tone"], "cold")
        self.assertEqual(s["length_bias"], "short")
        self.assertEqual(s["initiative"], "low")

    def test_personality_style_anxious(self):
        s = DigitalTwin._personality_style({"attachment_style": "焦虑型"})
        self.assertEqual(s["tone"], "anxious")
        self.assertEqual(s["initiative"], "high")

    def test_personality_style_secure(self):
        s = DigitalTwin._personality_style({"attachment_style": "安全型"})
        self.assertEqual(s["tone"], "balanced")

    def test_personality_style_numeric_fallback_neurotic(self):
        s = DigitalTwin._personality_style({"neuroticism": 85})
        self.assertEqual(s["tone"], "anxious")

    def test_personality_style_numeric_fallback_low_agree(self):
        s = DigitalTwin._personality_style({"agreeableness": 20})
        self.assertEqual(s["tone"], "self_centered")

    def test_personality_style_empty_defaults_secure(self):
        s = DigitalTwin._personality_style({})
        self.assertEqual(s["tone"], "balanced")

    def test_personality_style_has_quirks(self):
        s = DigitalTwin._personality_style({"attachment_style": "回避型"})
        self.assertIn("quirks", s)
        self.assertTrue(len(s["quirks"]) >= 2)

    # ------------------------------------------------------------------ #
    # _state_modulate
    # ------------------------------------------------------------------ #
    def test_state_good_adds_tilde_and_extends(self):
        out = DigitalTwin._state_modulate("好呀", {"mood": "good"})
        self.assertTrue(out.endswith("～") or out.endswith("？"))

    def test_state_bad_shortens(self):
        out = DigitalTwin._state_modulate("我今天也挺累的，想跟你聊聊这件事",
                                         {"mood": "bad"})
        self.assertLessEqual(len(out), 15)

    def test_state_busy_says_in_a_while(self):
        out = DigitalTwin._state_modulate("好", {"mood": "busy"})
        self.assertIn("忙", out)

    def test_state_normal_unchanged(self):
        out = DigitalTwin._state_modulate("好的，知道了", {"mood": "normal"})
        self.assertEqual(out, "好的，知道了")

    def test_state_empty_input(self):
        self.assertEqual(DigitalTwin._state_modulate("", {"mood": "good"}), "")

    # ------------------------------------------------------------------ #
    # _history_bias
    # ------------------------------------------------------------------ #
    def test_history_after_angry_adds_residue(self):
        out = DigitalTwin._history_bias(
            "好的，那就这样",
            [{"content": "你真让我生气", "emotion": "angry"}],
        )
        self.assertIn("再说吧", out)

    def test_history_after_happy_extends_soft(self):
        out = DigitalTwin._history_bias(
            "好呀",
            [{"content": "今天好开心", "emotion": "happy"}],
        )
        self.assertTrue(out.endswith("～"))

    def test_history_empty_unchanged(self):
        out = DigitalTwin._history_bias("好的", [])
        self.assertEqual(out, "好的")

    def test_history_none_unchanged(self):
        out = DigitalTwin._history_bias("好的", None)
        self.assertEqual(out, "好的")


if __name__ == "__main__":
    unittest.main()
