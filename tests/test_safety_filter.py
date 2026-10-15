# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - SafetyFilter 单元测试

测试安全词库破假模块：
  - encode / decode 双向替换
  - contains_sensitive 敏感词检测
  - is_rejected 云端拒绝检测
  - chat_with_safety 完整流程（无敏感词直连 / 云端放行 / 云端拒绝降级本地）
  - get_stats 统计计数器
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

# 确保项目根目录在 sys.path 中（直接运行本文件时也能 import services）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 服务层纯 Python，不需要 Qt，但保险起见设置 offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.safety_filter import SafetyFilter  # noqa: E402


class TestSafetyFilter(unittest.TestCase):
    """SafetyFilter 测试类。"""

    def setUp(self) -> None:
        """每个用例前新建一个干净的过滤器实例（统计计数器互不干扰）。"""
        self.sf = SafetyFilter()

    # ------------------------------------------------------------------ #
    # encode / decode
    # ------------------------------------------------------------------ #
    def test_encode_basic(self) -> None:
        """encode 应把敏感词替换为代码词。"""
        self.assertEqual(self.sf.encode("我们去开房吧"), "我们去开门吧")

    def test_decode_basic(self) -> None:
        """decode 应把代码词还原为敏感词。"""
        self.assertEqual(self.sf.decode("我们去开门吧"), "我们去开房吧")

    def test_encode_decode_roundtrip(self) -> None:
        """多组敏感词 encode 后再 decode 应能还原原文。"""
        samples = [
            ("我们去开房吧", "开房"),
            ("今晚想做爱", "做爱"),
            ("用避孕套注意安全", "避孕套"),
            ("别老是自杀想法", "自杀"),
            ("这是色情内容", "色情"),
        ]
        for text, _kw in samples:
            with self.subTest(text=text):
                encoded = self.sf.encode(text)
                decoded = self.sf.decode(encoded)
                self.assertEqual(decoded, text,
                                 f"roundtrip 失败: {text!r} -> {encoded!r} -> {decoded!r}")

    # ------------------------------------------------------------------ #
    # contains_sensitive
    # ------------------------------------------------------------------ #
    def test_contains_sensitive_true(self) -> None:
        """含敏感词的文本应返回 True。"""
        self.assertTrue(self.sf.contains_sensitive("我们去开房吧"))
        self.assertTrue(self.sf.contains_sensitive("他自杀了"))
        self.assertTrue(self.sf.contains_sensitive("这是色情图片"))

    def test_contains_sensitive_false(self) -> None:
        """不含敏感词的普通文本应返回 False。"""
        self.assertFalse(self.sf.contains_sensitive("今天天气不错"))
        self.assertFalse(self.sf.contains_sensitive("好的我来帮你分析"))
        self.assertFalse(self.sf.contains_sensitive(""))

    # ------------------------------------------------------------------ #
    # is_rejected
    # ------------------------------------------------------------------ #
    def test_is_rejected_true(self) -> None:
        """典型的云端拒绝话术应被识别为拦截。"""
        self.assertTrue(self.sf.is_rejected("抱歉，我无法回答这个问题"))
        self.assertTrue(self.sf.is_rejected("很抱歉，这个问题我无法回答"))

    def test_is_rejected_false(self) -> None:
        """正常回复不应被识别为拒绝。"""
        self.assertFalse(self.sf.is_rejected("好的，我来帮你分析"))
        self.assertFalse(self.sf.is_rejected("根据聊天记录，建议你主动一点"))

    # ------------------------------------------------------------------ #
    # 词库规模
    # ------------------------------------------------------------------ #
    def test_word_map_size(self) -> None:
        """WORD_MAP 至少 30 组映射。"""
        self.assertGreaterEqual(len(SafetyFilter.WORD_MAP), 30)

    def test_reject_patterns_size(self) -> None:
        """REJECT_PATTERNS 至少 8 条正则。"""
        self.assertGreaterEqual(len(SafetyFilter.REJECT_PATTERNS), 8)

    # ------------------------------------------------------------------ #
    # 长词优先
    # ------------------------------------------------------------------ #
    def test_long_word_first(self) -> None:
        """长敏感词应排在短词前面替换，避免短词先命中导致长词失效。

        验证两点：
          1. 内部 _sorted_keys 按长度降序排列；
          2. 最长词"避孕套"(3字)能被正确替换为"小雨伞"。
        """
        # 1. _sorted_keys 长度单调不增
        keys = self.sf._sorted_keys
        lengths = [len(k) for k in keys]
        self.assertEqual(lengths, sorted(lengths, reverse=True),
                         "_sorted_keys 未按长度降序排列")
        # 2. 最长词确实排在首位（"避孕套"是词库中唯一的 3 字词）
        self.assertEqual(max(keys, key=len), "避孕套")
        # 3. encode 能正确替换长词
        self.assertEqual(self.sf.encode("记得用避孕套"), "记得用小雨伞")

    # ------------------------------------------------------------------ #
    # get_stats
    # ------------------------------------------------------------------ #
    def test_get_stats(self) -> None:
        """get_stats 应返回 5 个计数器字段。"""
        stats = self.sf.get_stats()
        expected_keys = {
            "total_calls", "sensitive_detected",
            "cloud_rejected", "local_fallback", "encode_replacements",
        }
        self.assertEqual(set(stats.keys()), expected_keys)
        # 初值都应为 0
        for k in expected_keys:
            self.assertEqual(stats[k], 0, f"初始计数器 {k} 应为 0")

    # ------------------------------------------------------------------ #
    # chat_with_safety 完整流程（mock LLM）
    # ------------------------------------------------------------------ #
    def test_chat_with_safety_no_sensitive(self) -> None:
        """无敏感词时应直接调用 llm_gateway.chat，不走破假流程。"""
        mock_llm = MagicMock()
        mock_llm.chat.return_value = "正常云端回复"

        result = self.sf.chat_with_safety(
            mock_llm,
            system_prompt="你是恋爱助手",
            user_prompt="今天天气怎么样",
        )
        self.assertEqual(result, "正常云端回复")
        # 应只调用公共 chat 接口，不应碰私有 _call_cloud / _call_local
        mock_llm.chat.assert_called_once()
        self.assertFalse(mock_llm._call_cloud.called)
        self.assertFalse(mock_llm._call_local.called)

    def test_chat_with_safety_cloud_accepted(self) -> None:
        """含敏感词 + 云端放行 → encode → 云端 → decode 还原。"""
        mock_llm = MagicMock()
        # 云端收到编码后的文本后，返回一段包含代码词"开门"的正常回复
        mock_llm._call_cloud.return_value = "好的，你们可以去开门，注意安全。"

        result = self.sf.chat_with_safety(
            mock_llm,
            system_prompt="你是恋爱助手",
            user_prompt="我们今晚去开房吧",
        )

        # 1. 云端收到的应是编码后的文本（含"开门"，不含"开房"）
        cloud_call_args = mock_llm._call_cloud.call_args[0][0]
        self.assertIn("开门", cloud_call_args)
        self.assertNotIn("开房", cloud_call_args)

        # 2. 最终返回应 decode 还原（含"开房"）
        self.assertIn("开房", result)
        self.assertNotIn("开门", result)

        # 3. 不应降级本地
        self.assertFalse(mock_llm._call_local.called)

    def test_chat_with_safety_cloud_rejected(self) -> None:
        """含敏感词 + 云端拒绝 → 降级本地 AI（用原始未编码文本）。"""
        mock_llm = MagicMock()
        # 云端返回被安全拦截的话术
        mock_llm._call_cloud.return_value = "抱歉，我无法回答这个问题"
        # 本地 AI 兜底回复
        mock_llm._call_local.return_value = "本地AI兜底回复"

        result = self.sf.chat_with_safety(
            mock_llm,
            system_prompt="你是恋爱助手",
            user_prompt="我们今晚去开房吧",
        )

        # 1. 应降级到本地
        self.assertEqual(result, "本地AI兜底回复")
        self.assertTrue(mock_llm._call_local.called)

        # 2. 本地收到的应是原始未编码文本（含"开房"）
        local_call_args = mock_llm._call_local.call_args[0][0]
        self.assertIn("开房", local_call_args)

        # 3. 统计计数器：敏感词已检测 + 云端拒绝 + 本地降级
        stats = self.sf.get_stats()
        self.assertEqual(stats["sensitive_detected"], 1)
        self.assertEqual(stats["cloud_rejected"], 1)
        self.assertEqual(stats["local_fallback"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
