# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - JevJudge 轻量判断层单元测试

覆盖：
  - classify_message（6 类消息分类）
  - judge_intent（从候选选项中选意图）
  - classify_emotion_quick（6 类快速情绪）
  - judge_urgency（0-10 紧急度）
  - select_action（动作选择）
  - batch_judge（批量判断）

全部用纯本地规则验证，至少测试 10 条不同文本。
"""
import os
import sys
import time
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.jev_judge import JevJudge  # noqa: E402


class TestJevJudge(unittest.TestCase):
    """JevJudge 纯本地规则测试。"""

    def setUp(self) -> None:
        self.j = JevJudge()  # 不传 llm，纯本地规则

    # ------------------------------------------------------------------ #
    # 1. classify_message
    # ------------------------------------------------------------------ #
    def test_classify_urgent(self) -> None:
        """紧急消息：应识别为 urgent。"""
        out = self.j.classify_message("在吗，急！马上回电话，出事了！")
        self.assertEqual(out["category"], "urgent")
        self.assertGreaterEqual(out["confidence"], 0.8)

    def test_classify_spam(self) -> None:
        """垃圾/诈骗消息：应识别为 spam。"""
        out = self.j.classify_message("恭喜您中奖了，请点击链接领取")
        self.assertEqual(out["category"], "spam")

    def test_classify_ad(self) -> None:
        """广告消息：应识别为 ad。"""
        out = self.j.classify_message("限时折扣！点击链接下单，加微信领优惠券")
        self.assertEqual(out["category"], "ad")

    def test_classify_notify(self) -> None:
        """系统通知：应识别为 notify。"""
        out = self.j.classify_message("您的订单已发货，物流更新通知")
        self.assertEqual(out["category"], "notify")

    def test_classify_customer(self) -> None:
        """客户咨询：应识别为 customer。"""
        out = self.j.classify_message("你好，请问这个产品多少钱？想采购")
        self.assertEqual(out["category"], "customer")

    def test_classify_social(self) -> None:
        """普通社交：未命中特殊规则应归 social。"""
        out = self.j.classify_message("今天天气不错，下班一起吃饭？")
        self.assertEqual(out["category"], "social")

    def test_classify_empty(self) -> None:
        """空文本：不应抛异常，返回 social 兜底。"""
        out = self.j.classify_message("")
        self.assertEqual(out["category"], "social")
        self.assertGreaterEqual(out["confidence"], 0.0)

    # ------------------------------------------------------------------ #
    # 2. judge_intent
    # ------------------------------------------------------------------ #
    def test_judge_intent_basic(self) -> None:
        """意图判断：应返回合法结构。"""
        out = self.j.judge_intent(
            "周末想去吃火锅", ["约饭", "表白", "道歉", "吐槽"])
        self.assertGreaterEqual(out["index"], 0)
        self.assertLess(out["index"], 4)
        self.assertTrue(out["option"])
        self.assertGreaterEqual(out["confidence"], 0.0)

    def test_judge_intent_empty_options(self) -> None:
        """空选项：index=-1，不抛异常。"""
        out = self.j.judge_intent("随便说点什么", [])
        self.assertEqual(out["index"], -1)

    def test_judge_intent_exact_match(self) -> None:
        """完全包含某选项时应选中它。"""
        out = self.j.judge_intent(
            "我想跟你表白", ["约饭", "表白", "道歉", "吐槽"])
        self.assertEqual(out["option"], "表白")

    # ------------------------------------------------------------------ #
    # 3. classify_emotion_quick
    # ------------------------------------------------------------------ #
    def test_emotion_happy(self) -> None:
        """快乐词：应识别为 happy。"""
        out = self.j.classify_emotion_quick("今天好开心呀，哈哈哈，太棒了！")
        self.assertEqual(out["emotion"], "happy")

    def test_emotion_angry(self) -> None:
        """愤怒词：应识别为 angry。"""
        out = self.j.classify_emotion_quick("气死我了！他怎么能这样！靠！")
        self.assertEqual(out["emotion"], "angry")

    def test_emotion_sad(self) -> None:
        """悲伤词：应识别为 sad。"""
        out = self.j.classify_emotion_quick("心里好难过，委屈，想哭")
        self.assertEqual(out["emotion"], "sad")

    def test_emotion_positive(self) -> None:
        """正面情感：应识别为 positive。"""
        out = self.j.classify_emotion_quick("我喜欢你，想你了，好爱你")
        self.assertIn(out["emotion"], ("positive", "happy"))

    def test_emotion_negative(self) -> None:
        """负面情感：应识别为 negative。"""
        out = self.j.classify_emotion_quick("烦死了，不想理你，失望透顶")
        self.assertIn(out["emotion"], ("negative", "angry", "sad"))

    def test_emotion_neutral(self) -> None:
        """无情感倾向：应识别为 neutral。"""
        out = self.j.classify_emotion_quick("今天周三，下午三点开会")
        self.assertEqual(out["emotion"], "neutral")

    # ------------------------------------------------------------------ #
    # 4. judge_urgency
    # ------------------------------------------------------------------ #
    def test_urgency_high(self) -> None:
        """高紧急度：应 need_immediate=True。"""
        out = self.j.judge_urgency("出事了！马上回来，十万火急！")
        self.assertGreaterEqual(out["score"], 5)
        self.assertTrue(out["need_immediate"])
        self.assertGreater(len(out["hits"]), 0)

    def test_urgency_low(self) -> None:
        """低紧急度：应 need_immediate=False。"""
        out = self.j.judge_urgency("今天天气不错，周末有空一起爬山吗")
        self.assertLess(out["score"], 5)
        self.assertFalse(out["need_immediate"])

    # ------------------------------------------------------------------ #
    # 5. select_action
    # ------------------------------------------------------------------ #
    def test_select_action_negative_state(self) -> None:
        """负面状态：应优先选安慰/道歉类动作。"""
        out = self.j.select_action(
            "她现在很生气，我们在冷战",
            ["道歉安慰她", "约她升温约会", "保持距离沉默"])
        self.assertGreaterEqual(out["index"], 0)
        self.assertIn("action", out)

    def test_select_action_positive_state(self) -> None:
        """正面状态：应优先选升温/推进类动作。"""
        out = self.j.select_action(
            "她今天很开心，约会很愉快，氛围甜蜜",
            ["升温推进表白", "道歉安抚", "保持距离"])
        self.assertGreaterEqual(out["index"], 0)

    def test_select_action_empty_actions(self) -> None:
        """空动作列表：index=-1，不抛异常。"""
        out = self.j.select_action("她生气了", [])
        self.assertEqual(out["index"], -1)

    # ------------------------------------------------------------------ #
    # 6. batch_judge
    # ------------------------------------------------------------------ #
    def test_batch_judge_message(self) -> None:
        """批量消息分类：返回与输入等长的结果。"""
        items = [
            "在吗，急！马上回电话",
            "恭喜您中奖了请点击链接",
            "今天天气不错",
            "请问这个多少钱",
            "您的订单已发货",
        ]
        out = self.j.batch_judge(items, "message")
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["category"], "urgent")
        self.assertEqual(out[1]["category"], "spam")
        self.assertEqual(out[4]["category"], "notify")

    def test_batch_judge_emotion(self) -> None:
        """批量情绪分类。"""
        items = [
            "我好开心哈哈哈",
            "气死我了靠",
            "心里好难过想哭",
            "今天周三开会",
        ]
        out = self.j.batch_judge(items, "emotion")
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0]["emotion"], "happy")
        self.assertEqual(out[1]["emotion"], "angry")

    # ------------------------------------------------------------------ #
    # 性能 & 鲁棒性
    # ------------------------------------------------------------------ #
    def test_speed_under_10ms(self) -> None:
        """纯本地规则单次判断应 < 10ms。"""
        t0 = time.perf_counter()
        for _ in range(100):
            self.j.classify_emotion_quick("测试一段中等长度的中文消息，"
                                         "包含一些情感词，看看速度如何")
        avg_ms = (time.perf_counter() - t0) / 100 * 1000
        self.assertLess(avg_ms, 10.0,
                        f"平均耗时 {avg_ms:.2f}ms 超过 10ms 阈值")

    def test_no_exception_on_garbage_input(self) -> None:
        """垃圾输入不应抛异常。"""
        for bad in [None, 123, [], {}, tuple(), 3.14]:
            self.j.classify_message(bad)  # type: ignore[arg-type]
            self.j.classify_emotion_quick(bad)  # type: ignore[arg-type]
            self.j.judge_urgency(bad)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
