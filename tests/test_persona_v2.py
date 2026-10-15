# -*- coding: utf-8 -*-
"""
LoveAI v2 - 画像系统 v2 单元测试

覆盖：
  - 三维依恋评分计算（回避/焦虑/安全基底，0-100）
  - 四种主类型识别（疏离型回避/焦虑型/恐惧型/安全型）
  - 数据不足时的中等默认值 + low 置信
  - 向后兼容 type 字段
  - 行为验证机制（自述 vs 实证：一致 / 不一致）
  - 可信度维度计算（承诺兑现率/言行一致性/等级）
  - strategy_advisor 接入：三维评分 -> 策略桶、低可信度策略保守化、
    自述行为矛盾提醒、旧画像向后兼容
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.persona_analyzer import PersonaAnalyzer  # noqa: E402
from services.strategy_advisor import StrategyAdvisor  # noqa: E402

BASE = datetime(2026, 9, 1, 9, 0, 0)


def _m(direction, content, t):
    return {"direction": direction, "content": content, "msg_type": 1,
            "created_at": (BASE + timedelta(minutes=t)).isoformat()}


class FakeDB:
    def __init__(self, msgs):
        self._m = msgs

    def fetch_all(self, sql, params=None):
        return self._m


def _analyze(msgs):
    pa = PersonaAnalyzer(FakeDB(msgs))
    pa._save_profile = lambda *a, **k: None  # 不落盘
    return pa.analyze("__test__")


# ---- 消息构造器：三种典型依恋人设 ---- #
def build_secure():
    msgs, t = [], 0
    for _ in range(12):
        msgs.append(_m("sent", "今天工作有点累", t)); t += 20
        msgs.append(_m("received", "辛苦啦，我理解你，好好休息", t)); t += 25
        msgs.append(_m("sent", "晚上可能要加班", t)); t += 20
        msgs.append(_m("received", "没关系，我们好好沟通，慢慢来，我相信你", t)); t += 30
        t += 120
    msgs.append(_m("sent", "我今天有点生气，你那句话让我难过", t)); t += 40
    msgs.append(_m("received", "抱歉，我也有不对，我们聊聊，一起面对", t))
    return msgs


def build_anxious():
    msgs, t = [], 0
    for _ in range(10):
        msgs.append(_m("sent", "嗯，知道了", t)); t += 30
        msgs.append(_m("received", "你怎么了，是不是我哪里惹你不高兴了", t)); t += 1
        msgs.append(_m("received", "你是不是不爱我了，你今天好冷淡", t)); t += 1
        msgs.append(_m("received", "你去哪里了，为什么不回我，我好怕失去你", t)); t += 1
        msgs.append(_m("received", "你还在乎我吗，你是不是烦我了", t)); t += 20
    return msgs


def build_fearful():
    msgs, t = [], 0
    for _ in range(6):
        msgs.append(_m("sent", "我们谈谈吧，最近我很生气，你这样我很难过", t)); t += 400
        msgs.append(_m("received", "我需要空间，别逼我，给我点时间", t)); t += 5
        msgs.append(_m("received", "我想静静，不想聊这个", t)); t += 5
        msgs.append(_m("sent", "那我先不打扰你", t)); t += 2
        msgs.append(_m("received", "你是不是不要我了", t)); t += 1
        msgs.append(_m("received", "你还在吗，为什么不回我", t)); t += 1
        msgs.append(_m("received", "我好怕失去你，我没有安全感", t)); t += 60
    return msgs


def build_broken_promises():
    msgs, t = [], 0
    for _ in range(6):
        msgs.append(_m("received", "我答应周末陪你，说好了", t)); t += 120
        msgs.append(_m("received", "我保证下次一定，我说到做到", t)); t += 30
        msgs.append(_m("sent", "那说好了，我等你", t)); t += 2000
        msgs.append(_m("received", "啊忘了，下次再说吧，临时有事", t)); t += 60
        msgs.append(_m("sent", "你答应我的呢？你又没兑现，说好的又这样", t)); t += 30
        msgs.append(_m("received", "别生气嘛", t)); t += 120
        msgs.append(_m("sent", "你总是说说而已，你说的和做的不一样", t)); t += 60
    return msgs


class TestThreeDimensionAttachment(unittest.TestCase):
    """三维依恋评分计算。"""

    def test_scores_in_0_100(self):
        """三个维度评分都应在 0-100。"""
        prof = _analyze(build_anxious())
        att = prof["attachment"]
        for k in ("avoidance_score", "anxiety_score", "security_score"):
            self.assertGreaterEqual(att[k], 0)
            self.assertLessEqual(att[k], 100)

    def test_secure_low_both(self):
        """安全型：回避<40 且 焦虑<40，主类型安全型。"""
        att = _analyze(build_secure())["attachment"]
        self.assertLess(att["avoidance_score"], 40)
        self.assertLess(att["anxiety_score"], 40)
        self.assertEqual(att["primary_type"], "安全型")

    def test_anxious_type(self):
        """焦虑型：焦虑>=60，回避<40，主类型焦虑型。"""
        att = _analyze(build_anxious())["attachment"]
        self.assertGreaterEqual(att["anxiety_score"], 60)
        self.assertLess(att["avoidance_score"], 40)
        self.assertEqual(att["primary_type"], "焦虑型")

    def test_fearful_type_both_high(self):
        """恐惧型：回避>=60 且 焦虑>=60，主类型恐惧型。"""
        att = _analyze(build_fearful())["attachment"]
        self.assertGreaterEqual(att["avoidance_score"], 60)
        self.assertGreaterEqual(att["anxiety_score"], 60)
        self.assertEqual(att["primary_type"], "恐惧型")

    def test_backward_compat_type_field(self):
        """向后兼容：attachment.type 应等于 primary_type。"""
        att = _analyze(build_fearful())["attachment"]
        self.assertEqual(att["type"], att["primary_type"])

    def test_data_insufficient_default(self):
        """消息<30：给中等默认值50 + low置信 + data_note。"""
        msgs = [_m("received", "你好", i) for i in range(10)]
        att = _analyze(msgs)["attachment"]
        self.assertEqual(att["avoidance_score"], 50)
        self.assertEqual(att["anxiety_score"], 50)
        self.assertEqual(att["confidence"], "low")
        self.assertIn("数据不足", att.get("data_note", ""))


class TestBehaviorVerification(unittest.TestCase):
    """行为验证：自述 vs 实证。"""

    def test_inconsistent_self_report_avoidant_but_anxious(self):
        """自称回避型但行为焦虑：consistent=False，实证为焦虑型。"""
        msgs = build_anxious()
        # 在对方消息里加入自述回避型
        msgs.insert(0, _m("received", "我是回避型，我比较独立", 0))
        bv = _analyze(msgs)["behavior_verification"]
        self.assertEqual(bv["self_report_attachment"], "回避型")
        self.assertEqual(bv["consistent"], False)
        self.assertIn("焦虑", bv["behavioral_assessment"])

    def test_no_self_report(self):
        """对方未自述：self_report=None，consistent=None。"""
        bv = _analyze(build_secure())["behavior_verification"]
        self.assertIsNone(bv["self_report_attachment"])
        self.assertIsNone(bv["consistent"])

    def test_consistent_self_report(self):
        """对方自述焦虑型且行为也焦虑：consistent=True。"""
        msgs = build_anxious()
        msgs.insert(0, _m("received", "我是焦虑型，我没有安全感", 0))
        bv = _analyze(msgs)["behavior_verification"]
        self.assertEqual(bv["self_report_attachment"], "焦虑型")
        self.assertEqual(bv["consistent"], True)


class TestTrustworthiness(unittest.TestCase):
    """可信度维度。"""

    def test_broken_promises_critical(self):
        """多次答应不兑现：综合可信度低/critical。"""
        tw = _analyze(build_broken_promises())["trustworthiness"]
        self.assertLess(tw["overall_trust_score"], 40)
        self.assertEqual(tw["level"], "critical")

    def test_clean_default_medium(self):
        """无承诺记录的安全型：兑现率中性默认，综合中等。"""
        tw = _analyze(build_secure())["trustworthiness"]
        self.assertIn(tw["level"], ("high", "medium"))
        self.assertIn("promise_fulfillment_rate", tw)
        self.assertIn("word_action_consistency", tw)
        self.assertIn("overall_trust_score", tw)

    def test_trust_level_thresholds(self):
        """等级阈值：>=80 high, 60-80 medium, 40-60 low, <40 critical。"""
        adv = StrategyAdvisor(None)
        # 直接调用内部映射逻辑：通过画像传入不同 overall_trust_score
        for score, expect in [(90, "high"), (70, "medium"), (50, "low"), (30, "critical")]:
            persona = {"trustworthiness": {"overall_trust_score": score, "level":
                       "high" if score >= 80 else "medium" if score >= 60
                       else "low" if score >= 40 else "critical"}}
            r = adv.analyze(persona, 4)
            self.assertEqual(r["trust_level"], expect)


class TestStrategyAdvisorV2(unittest.TestCase):
    """strategy_advisor 接入三维评分 / 可信度 / 行为验证。"""

    def setUp(self):
        self.adv = StrategyAdvisor(None)

    def test_fearful_from_three_dim_scores(self):
        """三维评分（双高）-> 恐惧型策略：温和稳定/逐步建立信任。"""
        persona = {"attachment": {"avoidance_score": 80, "anxiety_score": 80,
                                  "security_score": 20, "primary_type": "恐惧型"}}
        r = self.adv.analyze(persona, 4)
        self.assertIn("温和稳定", r["recommended_raw"])
        self.assertIn("逐步建立信任", r["recommended_raw"])

    def test_low_trust_strategy_guide_oriented(self):
        """低可信度：策略为观察验证+引导真实展现（攻略导向），禁忌含大额投入。"""
        persona = {"attachment": "secure",
                   "trustworthiness": {"overall_trust_score": 30, "level": "critical"}}
        r = self.adv.analyze(persona, 4)
        # 攻略导向：不是"重新评估关系/降低期望"，而是"观察验证/引导真实展现/设定底线"
        self.assertIn("观察验证", r["recommended_raw"])
        self.assertIn("引导真实展现", r["recommended_raw"])
        self.assertIn("设定清晰底线", r["recommended_raw"])
        self.assertIn("大额投入", r["forbidden_raw"])
        self.assertIn("可信度严重不足", r["reasoning"])
        # 不应该有撤离导向的技巧
        self.assertNotIn("重新评估关系", r["recommended_raw"])
        self.assertNotIn("降低期望", r["recommended_raw"])

    def test_behavior_inconsistent_warning(self):
        """自述与行为不一致：reasoning 含行为验证提醒。"""
        persona = {"attachment": {"avoidance_score": 10, "anxiety_score": 80,
                                  "primary_type": "焦虑型"},
                   "behavior_verification": {"consistent": False}}
        r = self.adv.analyze(persona, 4)
        self.assertIn("行为验证提醒", r["reasoning"])
        self.assertTrue(r["behavior_inconsistent"])

    def test_backward_compat_old_string_type(self):
        """旧画像（扁平字符串 attachment='avoidant'）仍正常工作。"""
        r = self.adv.analyze({"attachment": "avoidant"}, 4)
        self.assertIn("给对方空间", r["recommended_raw"])

    def test_backward_compat_old_nested_type(self):
        """旧画像（嵌套 attachment.type='回避型'）仍正常工作。"""
        r = self.adv.analyze({"attachment": {"type": "回避型"}}, 4)
        self.assertIn("给对方空间", r["recommended_raw"])

    def test_no_trust_field_no_intervention(self):
        """画像无 trustworthiness 字段时不注入可信度策略（向后兼容）。"""
        r = self.adv.analyze({"attachment": "secure"}, 4)
        self.assertIsNone(r["trust_level"])
        self.assertNotIn("可信度影响", r["reasoning"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
