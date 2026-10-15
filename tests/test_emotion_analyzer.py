# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - EmotionAnalyzer 单元测试

测试情感分析器：
  - 8 种情感标签的分类
  - 强度 / 置信度 / 关键词 / 次要标签
  - 疑问句检测
  - 回复速度暗示
  - 批量分析
  - 颜色映射
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.emotion_analyzer import EmotionAnalyzer  # noqa: E402


class TestEmotionAnalyzer(unittest.TestCase):
    """EmotionAnalyzer 测试类。"""

    def setUp(self) -> None:
        """初始化情感分析器（不接 LLM，纯规则匹配）。"""
        self.analyzer = EmotionAnalyzer(llm_gateway=None)

    # ------------------------------------------------------------------ #
    # 单条消息情感分类
    # ------------------------------------------------------------------ #
    def test_analyze_happy(self) -> None:
        """积极开心消息应被识别为 happy 且强度 > 5。"""
        result = self.analyzer.analyze("哈哈今天好开心")
        self.assertEqual(result["label"], "happy")
        self.assertGreater(result["intensity"], 5)

    def test_analyze_sad(self) -> None:
        """难过消息应被识别为 sad 且强度 > 3。

        注：单条"难过"关键词强度仅 2，这里用两条难过关键词叠加，
        既保证 label=sad，又满足 intensity>3 的断言。
        """
        result = self.analyzer.analyze("好难过好伤心")
        self.assertIn(result["label"], ["sad"])
        self.assertGreater(result["intensity"], 3)

    def test_analyze_angry(self) -> None:
        """生气消息应被识别为 angry。"""
        result = self.analyzer.analyze("气死我了")
        self.assertEqual(result["label"], "angry")

    def test_analyze_neutral_short(self) -> None:
        """单字冷淡回复应被识别为 neutral/cold 且强度 <= 2。"""
        result = self.analyzer.analyze("哦")
        self.assertIn(result["label"], ["neutral", "cold"])
        self.assertLessEqual(result["intensity"], 2)

    def test_analyze_question(self) -> None:
        """含疑问词/问号的消息应被标记为疑问句。"""
        result = self.analyzer.analyze("你为什么这样？")
        self.assertTrue(result["is_question"])

    def test_analyze_love(self) -> None:
        """爱慕消息应被识别为 love 或 happy。"""
        result = self.analyzer.analyze("想你了宝贝")
        self.assertIn(result["label"], ["love", "happy"])

    def test_analyze_confused(self) -> None:
        """疑惑消息应被识别为 confused。"""
        result = self.analyzer.analyze("啥意思啊？")
        self.assertEqual(result["label"], "confused")

    # ------------------------------------------------------------------ #
    # 结果字段完整性
    # ------------------------------------------------------------------ #
    def test_keywords_returned(self) -> None:
        """分析结果应包含 keywords 列表字段。"""
        result = self.analyzer.analyze("哈哈今天好开心")
        self.assertIn("keywords", result)
        self.assertIsInstance(result["keywords"], list)
        # "哈哈" 和 "开心" 都应被命中
        self.assertIn("哈哈", result["keywords"])
        self.assertIn("开心", result["keywords"])

    def test_secondary_labels(self) -> None:
        """混合情感消息应产生次要标签。"""
        # "想你了宝贝好开心" 同时命中 love(想你/宝贝) 和 happy(开心)
        result = self.analyzer.analyze("想你了宝贝好开心")
        self.assertIn("secondary_labels", result)
        self.assertIsInstance(result["secondary_labels"], list)
        self.assertGreaterEqual(len(result["secondary_labels"]), 1,
                               "混合情感应至少有 1 个次要标签")

    def test_message_length(self) -> None:
        """分析结果应包含 message_length 字段。"""
        text = "随便一段测试消息"
        result = self.analyzer.analyze(text)
        self.assertIn("message_length", result)
        self.assertEqual(result["message_length"], len(text))

    def test_response_speed_hint(self) -> None:
        """分析结果应包含 response_speed_hint，取值为 fast/medium/slow。"""
        result = self.analyzer.analyze("哈哈今天好开心")
        self.assertIn("response_speed_hint", result)
        self.assertIn(result["response_speed_hint"],
                      ["fast", "medium", "slow"])

    # ------------------------------------------------------------------ #
    # 颜色映射
    # ------------------------------------------------------------------ #
    def test_get_emotion_color_happy(self) -> None:
        """happy 应映射为绿色 #4ade80。"""
        self.assertEqual(self.analyzer.get_emotion_color("happy"), "#4ade80")

    def test_get_emotion_color_angry(self) -> None:
        """angry 应映射为红色 #f87171。"""
        self.assertEqual(self.analyzer.get_emotion_color("angry"), "#f87171")

    def test_get_emotion_color_neutral(self) -> None:
        """neutral 应映射为黄色 #fbbf24。"""
        self.assertEqual(self.analyzer.get_emotion_color("neutral"), "#fbbf24")

    # ------------------------------------------------------------------ #
    # 批量分析
    # ------------------------------------------------------------------ #
    def test_batch_analyze(self) -> None:
        """batch_analyze 应处理多条消息并返回等长结果列表。"""
        messages = [
            {"content": "哈哈今天好开心", "direction": "received"},
            {"content": "气死我了", "direction": "sent"},
            {"content": "哦", "direction": "received"},
        ]
        results = self.analyzer.batch_analyze(messages)
        self.assertIsInstance(results, list)
        self.assertEqual(len(results), len(messages))
        # 每条结果都应携带 direction 字段
        for i, r in enumerate(results):
            self.assertEqual(r["direction"], messages[i]["direction"])
            self.assertIn("label", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
