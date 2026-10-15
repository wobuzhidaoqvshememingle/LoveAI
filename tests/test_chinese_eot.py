# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 中文 EoT 二次判断单测（R3.3-36）

覆盖：
  T1  句末问号 + 沉默 800ms → eot=True
  T2  逗号后沉默 1s → eot=False（句中停顿，不切轮次）
  T3  "然后呢"尾巴 → eot=False（延长判定）
  T4  "嗯" + 沉默 500ms 内 → eot=False（backchannel）
  T5  句末句号 + 沉默 200ms → eot=False（有标点但沉默不够）
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.chinese_eot import detect_chinese_eot  # noqa: E402


class TestChineseEOT(unittest.TestCase):
    def test_question_mark_with_long_silence_is_end(self):
        """T1：句末问号 + 沉默 800ms → 真结束。"""
        r = detect_chinese_eot("你今天吃饭了吗？", silence_ms=900, last_token_ts=0.0, now=1.0)
        self.assertTrue(r["eot"])
        self.assertEqual(r["reason"], "sentence_end_punctuation")
        self.assertGreater(r["confidence"], 0.8)

    def test_comma_short_silence_not_end(self):
        """T2：逗号后沉默 1s → 不切轮次。"""
        r = detect_chinese_eot("我今天去了超市，", silence_ms=1000, last_token_ts=0.0, now=1.0)
        self.assertFalse(r["eot"])
        self.assertEqual(r["reason"], "comma_mid_sentence")

    def test_connector_tail_extends_judgement(self):
        """T3：'然后呢'尾巴 → 延长判定，不立刻切。"""
        r = detect_chinese_eot("然后呢", silence_ms=400, last_token_ts=0.0, now=1.0)
        self.assertFalse(r["eot"])
        self.assertEqual(r["reason"], "connector_tail_waiting")

    def test_short_ack_backchannel(self):
        """T4：'嗯' 500ms 内 → backchannel，不切轮次。"""
        r = detect_chinese_eot("嗯", silence_ms=300, last_token_ts=0.0, now=1.0)
        self.assertFalse(r["eot"])
        self.assertEqual(r["reason"], "short_ack_backchannel")

    def test_sentence_end_punctuation_but_silence_short(self):
        """T5：句末句号但沉默只有 200ms → 还不能切。"""
        r = detect_chinese_eot("今天天气不错。", silence_ms=200, last_token_ts=0.0, now=1.0)
        self.assertFalse(r["eot"])
        self.assertEqual(r["reason"], "punctuation_but_silence_short")

    def test_empty_text_not_end(self):
        """空文本不算结束。"""
        r = detect_chinese_eot("", silence_ms=5000, last_token_ts=0.0, now=1.0)
        self.assertFalse(r["eot"])
        self.assertEqual(r["reason"], "empty_text")


if __name__ == "__main__":
    unittest.main()
