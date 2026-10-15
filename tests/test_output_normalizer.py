# -*- coding: utf-8 -*-
"""模型对齐层 + 质量底线（E22-E23）单测。"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.output_normalizer import (  # noqa: E402
    normalize_output, quality_check, safe_generate,
)


class TestNormalize(unittest.TestCase):

    def test_strip_preamble(self):
        raw = "好的，我来分析一下：他其实是回避型，需要空间。"
        out = normalize_output(raw)
        self.assertNotIn("我来分析", out)
        self.assertIn("回避型", out)

    def test_normalize_percent(self):
        out = normalize_output("命中率约百分之六十，50 ％左右")
        self.assertIn("60%", out)
        self.assertIn("50%", out)

    def test_length_truncate(self):
        long = "我们今天聊一下这个事情" * 50
        out = normalize_output(long, max_len=60)
        self.assertLessEqual(len(out), 80)

    def test_unify_exclaim(self):
        out = normalize_output("你好厉害啊！！！！")
        self.assertNotIn("！！！！", out)


class TestQualityCheck(unittest.TestCase):

    def test_empty(self):
        ok, reason = quality_check("")
        self.assertFalse(ok)
        self.assertEqual(reason, "empty")

    def test_too_short(self):
        ok, reason = quality_check("你好")
        self.assertFalse(ok)
        self.assertEqual(reason, "too_short")

    def test_repetition(self):
        ok, reason = quality_check("哈哈哈哈哈哈哈哈哈哈")
        self.assertFalse(ok)
        self.assertEqual(reason, "repetition")
        ok2, reason2 = quality_check("我想你我想你我想你我想你今天")
        self.assertFalse(ok2)
        self.assertEqual(reason2, "repetition")

    def test_garbled(self):
        ok, reason = quality_check("正常的一句话内容够长不会乱码")
        self.assertTrue(ok)

    def test_ok(self):
        ok, reason = quality_check("今天天气不错，适合出去走走", topic="天气")
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")


class TestSafeGenerate(unittest.TestCase):

    def test_garbled_falls_back(self):
        out = safe_generate("\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd",
                            rule_fallback="我在，慢慢说。")
        self.assertNotIn("\ufffd", out)

    def test_clean_passthrough(self):
        out = safe_generate("好的，我来分析：他是回避型。", topic="回避")
        self.assertNotIn("我来分析", out)
        self.assertTrue(len(out) >= 5)


if __name__ == "__main__":
    unittest.main()
