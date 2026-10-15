# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - R3.3-33 六种杀猪盘模式枚举 + 时序链单元测试
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.scam_patterns import (  # noqa: E402
    ScamPatternType,
    SCAM_CHAINS,
    detect_scam_stage,
    detect_scam_stage_dict,
    MIN_SIGNALS_FOR_CONFIDENT,
)


class TestScamPatternEnum(unittest.TestCase):
    """枚举完整性。"""

    def test_six_patterns_exist(self) -> None:
        expected = {
            "investment_scam", "romance_scam", "gambling_scam",
            "pig_butchering", "bird_killing", "fish_killing",
        }
        got = {p.value for p in ScamPatternType}
        self.assertEqual(got, expected)

    def test_each_pattern_has_chain(self) -> None:
        for p in ScamPatternType:
            self.assertIn(p, SCAM_CHAINS)
            chain = SCAM_CHAINS[p]
            self.assertGreaterEqual(len(chain), 2, f"{p} 时序链至少 2 阶段")
            for stage in chain:
                self.assertIn("stage", stage)
                self.assertIn("keywords", stage)
                self.assertIn("window_days", stage)
                self.assertGreater(len(stage["keywords"]), 0)


class TestRomancePlusInvestment(unittest.TestCase):
    """典型"网恋+荐股"对话：应识别为 ROMANCE/INVESTMENT 且 stage 正确。"""

    def setUp(self) -> None:
        # 一段从情感升温到引入项目再到收割的对话
        self.chat = [
            "早安呀宝贝，昨晚睡得好吗？",
            "我想你了，今天上班都没精神",
            "我们未来一定要在一起，我只有你了",
            "我最近在做一个数字货币项目，稳赚不赔，你要不要试试？",
            "我带你操作，内部渠道，低风险高收益",
            "你先充一点试试，我帮你开户",
            "现在出金要缴税，你再投一笔就能一起提出来",
            "系统维护中，账户被冻结了，需要保证金解冻",
        ]

    def test_returns_dict_with_required_keys(self) -> None:
        r = detect_scam_stage_dict(self.chat)
        for k in ("pattern", "stage", "confidence", "evidence", "advice"):
            self.assertIn(k, r)

    def test_identifies_top_pattern(self) -> None:
        r = detect_scam_stage(self.chat)
        # 关键词命中同时覆盖 ROMANCE_SCAM（早安/我想你/未来/我只有你）
        # 与 INVESTMENT_SCAM（数字货币/内部渠道/出金/保证金）。
        # 收割阶段（stage4_收割）关键词权重最高，应落在这两类之一。
        self.assertIn(r.pattern, {"romance_scam", "investment_scam", "pig_butchering"})

    def test_stage_is_harvest_or_intro(self) -> None:
        r = detect_scam_stage(self.chat)
        # 对话中既有"引入项目"也有"收割"信号，应落到较后的阶段
        self.assertIsNotNone(r.stage)
        self.assertTrue(
            any(k in r.stage for k in ("引入", "收割", "项目", "入金", "大额", "冻结")),
            f"意外的 stage: {r.stage}",
        )

    def test_confidence_high_with_enough_signals(self) -> None:
        r = detect_scam_stage(self.chat)
        self.assertGreaterEqual(r.confidence, 0.3)
        self.assertFalse(r.data_insufficient)

    def test_evidence_contains_real_messages(self) -> None:
        r = detect_scam_stage(self.chat)
        self.assertGreater(len(r.evidence), 0)
        # evidence 应来自原对话
        for ev in r.evidence:
            self.assertIn(ev, self.chat)


class TestInsufficientData(unittest.TestCase):
    """信号 <3 时必须 data_insufficient=True 且 confidence<0.3。"""

    def test_empty_history(self) -> None:
        r = detect_scam_stage([])
        self.assertTrue(r.data_insufficient)
        self.assertLess(r.confidence, 0.3)

    def test_random_chat_few_signals(self) -> None:
        chat = [
            "今天天气不错",
            "中午吃了拉面",
            "晚上要加班",
        ]
        r = detect_scam_stage(chat)
        self.assertTrue(r.data_insufficient)
        self.assertLess(r.confidence, 0.3)

    def test_threshold_constant(self) -> None:
        self.assertEqual(MIN_SIGNALS_FOR_CONFIDENT, 3)


class TestFishKilling(unittest.TestCase):
    """杀鱼盘：冒充客服/公检法。"""

    def test_fish_killing_detected(self) -> None:
        chat = [
            "你好，我是XX平台客服，工号 8866",
            "你涉嫌洗钱，需要配合调查",
            "请下载这个会议软件，我们屏幕共享",
            "把钱转到安全账户，告诉我验证码",
        ]
        r = detect_scam_stage(chat)
        self.assertEqual(r.pattern, "fish_killing")
        self.assertIn("转账", r.stage)
        self.assertFalse(r.data_insufficient)


class TestBirdKilling(unittest.TestCase):
    """杀鸟盘：刷单。"""

    def test_bird_killing_detected(self) -> None:
        chat = [
            "抖音关注一单 5 块，日结",
            "已返款，提现到账了",
            "现在需要做连单任务，订单未完成",
            "需要补单，再充值才能返款",
        ]
        r = detect_scam_stage(chat)
        self.assertEqual(r.pattern, "bird_killing")
        self.assertIn("大额", r.stage)


if __name__ == "__main__":
    unittest.main(verbosity=2)
