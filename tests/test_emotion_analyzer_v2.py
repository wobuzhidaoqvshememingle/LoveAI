# -*- coding: utf-8 -*-
"""情绪分析 v2 新功能单测：0-100 强度 / 趋势追踪 / 语境感知。"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.emotion_analyzer import EmotionAnalyzer, intensity_100, track_emotion_trend  # noqa: E402


class TestEmotionAnalyzerV2(unittest.TestCase):

    def setUp(self):
        self.an = EmotionAnalyzer()

    # ------------------------------------------------------------------ #
    # 0-100 强度
    # ------------------------------------------------------------------ #
    def test_intensity_100_range(self):
        for text in ["", "哦", "哈哈好开心！！！", "气死我了烦死了滚！！！"]:
            r = self.an.intensity_100(text)
            self.assertGreaterEqual(r["intensity_100"], 0)
            self.assertLessEqual(r["intensity_100"], 100)

    def test_intensity_exclamation_raises(self):
        base = self.an.intensity_100("今天好开心")["intensity_100"]
        hot = self.an.intensity_100("今天好开心！！！")["intensity_100"]
        self.assertGreater(hot, base)

    def test_intensity_extreme_words_raise(self):
        base = self.an.intensity_100("有点烦")["intensity_100"]
        extreme = self.an.intensity_100("烦死了气死我了")["intensity_100"]
        self.assertGreater(extreme, base)

    def test_intensity_short_message_lower(self):
        short = self.an.intensity_100("哦")["intensity_100"]
        long = self.an.intensity_100("今天发生了好多事我想一件一件跟你说清楚你听我慢慢讲完好不好")["intensity_100"]
        self.assertGreater(long, short)

    def test_intensity_speed_bonus(self):
        fast = self.an.intensity_100("好呀", reply_speed="fast")["intensity_100"]
        slow = self.an.intensity_100("好呀", reply_speed="slow")["intensity_100"]
        self.assertGreater(fast, slow)

    def test_module_level_intensity(self):
        v = intensity_100("哈哈好开心！！！")
        self.assertGreater(v, 30)

    # ------------------------------------------------------------------ #
    # 趋势追踪
    # ------------------------------------------------------------------ #
    def test_trend_empty(self):
        r = track_emotion_trend("p1", [])
        self.assertEqual(r["trend"], "stable")
        self.assertIsNone(r["warning"])

    def test_trend_single_message(self):
        r = track_emotion_trend("p1", [{"content": "哈哈"}])
        self.assertEqual(r["trend"], "stable")

    def test_trend_sudden(self):
        # 前一条高强度开心，后一条极低冷淡 → 突变
        msgs = [
            {"content": "哈哈今天太开心了！！！"},
            {"content": "哦"},
        ]
        r = track_emotion_trend("p1", msgs)
        self.assertEqual(r["trend"], "sudden")
        self.assertIsNotNone(r["warning"])
        self.assertIn("突然", r["warning"])

    def test_trend_gradual(self):
        # 慢慢变冷：强度逐次下降
        msgs = [
            {"content": "哈哈好开心！"},
            {"content": "嗯，还行吧"},
            {"content": "哦"},
            {"content": "算了，不想说"},
        ]
        r = track_emotion_trend("p1", msgs)
        self.assertIn(r["trend"], ("gradual", "sudden"))
        self.assertEqual(r["direction"], "down")

    def test_trend_stable(self):
        msgs = [
            {"content": "嗯，好的"},
            {"content": "行，知道了"},
            {"content": "好，那就这样"},
        ]
        r = track_emotion_trend("p1", msgs)
        self.assertEqual(r["trend"], "stable")

    # ------------------------------------------------------------------ #
    # 语境感知
    # ------------------------------------------------------------------ #
    def test_context_sensitive_in_fight(self):
        label, inten = self.an._context_aware_emotion(
            "随便你", {"in_fight": True})
        self.assertEqual(label, "angry")
        self.assertGreaterEqual(inten, 80)

    def test_context_sensitive_ambiguous(self):
        label, inten = self.an._context_aware_emotion(
            "随便你", {"ambiguous_warming": True})
        self.assertEqual(label, "love")
        self.assertGreaterEqual(inten, 50)

    def test_context_sensitive_normal(self):
        label, inten = self.an._context_aware_emotion("随便你", {})
        self.assertEqual(label, "cold")
        self.assertLessEqual(inten, 40)

    def test_context_sensitive_other_phrases_untouched(self):
        # 非敏感短话，走规则结果
        label, inten = self.an._context_aware_emotion("今天吃了吗", {})
        self.assertIn(label, ("neutral", "happy"))
        self.assertGreaterEqual(inten, 0)

    def test_context_doesnt_crash_on_empty(self):
        label, inten = self.an._context_aware_emotion("", {})
        self.assertIsInstance(label, str)
        self.assertIsInstance(inten, int)

    # ------------------------------------------------------------------ #
    # 未说出口的需求（FriendFixAI）
    # ------------------------------------------------------------------ #
    def test_unspoken_angry_in_fight(self):
        need = self.an.infer_unspoken_need("angry", {"in_fight": True})
        self.assertIn("台阶", need)

    def test_unspoken_cold_long_silence(self):
        need = self.an.infer_unspoken_need("cold", {"long_silence": True})
        self.assertIn("先开口", need)

    def test_unspoken_sad_normal(self):
        need = self.an.infer_unspoken_need("sad", {})
        self.assertIn("听", need)

    def test_unspoken_unknown_label_fallback(self):
        need = self.an.infer_unspoken_need("weird_label_xyz", {})
        self.assertTrue(need)  # 不崩，有兜底


if __name__ == "__main__":
    unittest.main()
