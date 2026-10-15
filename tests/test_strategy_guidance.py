# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 攻略导向策略验证测试

验证核心原则：系统终极目的是引导关系变好（攻略游戏对象），
不是撤离/保护自己/降低期望。所有手段都是攻略手段。

覆盖：
  1. 回避型冷淡场景：策略包含给空间+低压沟通+间接关心，目的是让对方主动靠近
  2. 焦虑型追问场景：策略包含及时回应+明确表达+建立安全感
  3. 低兑现率场景：策略是引导兑现习惯，不是"这个人不行"
  4. 高伪装风险场景：建议是观察验证+推进，不是赶紧跑/撤离
  5. 激烈争吵场景：策略是修复后关系更紧密，不是谁对谁错
  6. 推荐技巧都带攻略目的说明
  7. 禁忌技巧都带禁忌原因说明
  8. 高可信度等级策略不是撤离导向
  9. 输出中无"分手/撤离/放弃"作为最终建议
  10. deception_detector极高风险建议不是重新评估导向
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.strategy_advisor import StrategyAdvisor  # noqa: E402
from services.deception_detector import DeceptionDetector  # noqa: E402


class TestStrategyGuidance(unittest.TestCase):
    """攻略导向策略验证测试类。"""

    def setUp(self) -> None:
        self.adv = StrategyAdvisor(None)
        self.detector = DeceptionDetector(None)

    # ------------------------------------------------------------------ #
    # 场景1：回避型冷淡
    # ------------------------------------------------------------------ #
    def test_scenario1_avoidant_cold(self) -> None:
        """场景1：回避型冷淡——策略包含给空间+低压沟通+间接关心，目的是让对方主动靠近。"""
        persona = {
            "attachment": {"type": "回避型", "avoidance_score": 75, "anxiety_score": 25},
            "conflict_style": {"primary_key": "cold_war"},
        }
        r = self.adv.analyze(persona, relation_stage=4)

        # 验证推荐技巧包含核心手段
        recs_raw = r["recommended_raw"]
        self.assertIn("给对方空间", recs_raw)
        self.assertIn("低压沟通", recs_raw)
        self.assertIn("间接表达关心", recs_raw)

        # 验证攻略目的说明是"让对方主动靠近"而非"放弃"
        recs_annotated = " ".join(r["recommended"])
        self.assertIn("主动靠近", recs_annotated)

        # 验证输出中没有撤离导向词汇
        reasoning = r["reasoning"]
        self.assertNotIn("赶紧跑", reasoning)
        self.assertNotIn("及时止损", reasoning)

    # ------------------------------------------------------------------ #
    # 场景2：焦虑型追问
    # ------------------------------------------------------------------ #
    def test_scenario2_anxious_pursuit(self) -> None:
        """场景2：焦虑型追问——策略包含及时回应+明确表达+建立安全感。"""
        persona = {
            "attachment": {"type": "焦虑型", "avoidance_score": 25, "anxiety_score": 75},
        }
        r = self.adv.analyze(persona, relation_stage=3)

        recs_raw = r["recommended_raw"]
        self.assertIn("及时回应", recs_raw)
        self.assertIn("明确表达爱意", recs_raw)
        self.assertIn("持续确认", recs_raw)

        # 攻略目的是稳定情绪推进关系
        recs_annotated = " ".join(r["recommended"])
        self.assertIn("安全感", recs_annotated)
        self.assertIn("推进亲密", recs_annotated)

        # 无撤离导向
        self.assertNotIn("分手", r["reasoning"])
        self.assertNotIn("撤离", r["reasoning"])

    # ------------------------------------------------------------------ #
    # 场景3：低兑现率
    # ------------------------------------------------------------------ #
    def test_scenario3_low_fulfillment(self) -> None:
        """场景3：低兑现率——策略是引导兑现习惯，不是"这个人不行/放弃"。"""
        # 测试 trust_guidance low 等级
        recs, forb, note = StrategyAdvisor._trust_guidance("low")

        # 应该推荐小承诺建立信任闭环
        self.assertIn("小额试探", recs)
        self.assertIn("通过小承诺建立信任闭环", recs)

        # 不应该说"这个人不行"或"放弃"
        self.assertNotIn("这个人不行", note)
        self.assertNotIn("放弃", note)
        self.assertNotIn("重新评估关系", note)

        # 应该引导兑现习惯
        self.assertIn("引导", note)
        self.assertIn("兑现", note)

    # ------------------------------------------------------------------ #
    # 场景4：高伪装风险
    # ------------------------------------------------------------------ #
    def test_scenario4_high_deception_risk(self) -> None:
        """场景4：高伪装风险——建议是观察验证+推进，不是赶紧跑/撤离。"""
        # 测试 deception_detector 极高风险 recommendation
        level, rec = self.detector._level_and_recommendation(85.0)
        self.assertEqual(level, "极高")

        # 不应该说"强烈建议重新评估"或"保护好自己"
        self.assertNotIn("强烈建议重新评估", rec)
        self.assertNotIn("保护好自己", rec)

        # 应该说"谨慎推进"和"验证真实自我"
        self.assertIn("谨慎推进", rec)
        self.assertIn("验证", rec)
        self.assertIn("真实自我", rec)

        # 测试 strategy_advisor 的高伪装风险策略
        persona = {
            "attachment": "secure",
            "deception_risk": {
                "risk_level": "高",
                "risk_score": 72,
                "data_sufficient": True,
            },
        }
        r = self.adv.analyze(persona, relation_stage=5)
        recs_raw = r["recommended_raw"]

        self.assertIn("保持观察", recs_raw)
        self.assertIn("引导真实展现", recs_raw)
        self.assertIn("观察验证", recs_raw)

        # reasoning 中无赶紧跑/撤离
        self.assertNotIn("赶紧跑", r["reasoning"])
        self.assertNotIn("撤离", r["reasoning"])

    # ------------------------------------------------------------------ #
    # 场景5：激烈争吵后
    # ------------------------------------------------------------------ #
    def test_scenario5_after_big_fight(self) -> None:
        """场景5：激烈争吵后——策略是修复后关系更紧密，不是谁对谁错。"""
        persona = {
            "attachment": "secure",
            "conflict_style": {"primary_key": "emotional_outburst"},
            "self_differentiation": {"level": "低"},
        }
        r = self.adv.analyze(persona, relation_stage=5)

        conflict = r["conflict_approach"]

        # 应该提到修复后关系更紧密
        self.assertIn("修复", conflict)
        self.assertIn("更紧密", conflict)

        # 不应该说"赢了争吵"导向
        self.assertNotIn("赢了", conflict)

        # reasoning 无分手
        self.assertNotIn("分手", r["reasoning"])

    # ------------------------------------------------------------------ #
    # 推荐技巧包含攻略目的说明
    # ------------------------------------------------------------------ #
    def test_recommended_has_purpose(self) -> None:
        """推荐技巧都应该带攻略目的说明（格式：技巧名（目的：xxx））。"""
        r = self.adv.analyze({"attachment": "avoidant"}, 5)

        # 至少前3个推荐技巧应该带目的说明
        annotated_count = sum(
            1 for tip in r["recommended"] if "（目的：" in tip
        )
        self.assertGreaterEqual(annotated_count, 3,
                                "至少3个推荐技巧应带攻略目的说明")

    # ------------------------------------------------------------------ #
    # 禁忌技巧包含禁忌原因
    # ------------------------------------------------------------------ #
    def test_forbidden_has_reason(self) -> None:
        """禁忌技巧都应该带禁忌原因说明（格式：技巧名（禁忌原因：xxx））。"""
        r = self.adv.analyze({"attachment": "anxious"}, 5)

        annotated_count = sum(
            1 for forb in r["forbidden"] if "（禁忌原因：" in forb
        )
        self.assertGreaterEqual(annotated_count, 3,
                                "至少3个禁忌技巧应带禁忌原因说明")

    # ------------------------------------------------------------------ #
    # 高可信度等级策略不是撤离导向
    # ------------------------------------------------------------------ #
    def test_critical_trust_not_withdrawal(self) -> None:
        """极高可信度风险下，策略不是"重新评估关系/保护自己"，而是观察验证+推进。"""
        recs, forb, note = StrategyAdvisor._trust_guidance("critical")

        # 不应该有撤离导向的推荐技巧
        self.assertNotIn("重新评估关系", recs)
        self.assertNotIn("保护自身利益", recs)
        self.assertNotIn("降低期望", recs)

        # 应该有观察验证导向的技巧
        self.assertIn("观察验证", recs)
        self.assertIn("引导真实展现", recs)
        self.assertIn("设定清晰底线", recs)

        # note 中不应该说"避免继续单方面投入"
        self.assertNotIn("避免继续单方面投入", note)
        # 应该说"根据验证结果决定推进策略"
        self.assertIn("根据验证结果", note)

    # ------------------------------------------------------------------ #
    # 输出中无"分手/撤离/放弃"作为最终建议
    # ------------------------------------------------------------------ #
    def test_no_withdrawal_words_in_output(self) -> None:
        """各种场景的输出中，不应将"分手/撤离/放弃"作为推荐策略。"""
        test_cases = [
            {"attachment": "avoidant"},
            {"attachment": "anxious"},
            {"attachment": "secure"},
            {"attachment": "fearful"},
            {"deception_risk": {"risk_level": "高", "risk_score": 70, "data_sufficient": True}},
            {"deception_risk": {"risk_level": "极高", "risk_score": 85, "data_sufficient": True}},
        ]

        for persona in test_cases:
            with self.subTest(persona=persona):
                r = self.adv.analyze(persona, relation_stage=5)
                output = " ".join(r["recommended"]) + " " + r["reasoning"]

                # 这些词不应该作为推荐策略出现
                self.assertNotIn("赶紧跑", output)
                self.assertNotIn("及时止损", output)
                self.assertNotIn("这个人不行", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
