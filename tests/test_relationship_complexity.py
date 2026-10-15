# -*- coding: utf-8 -*-
"""R3.3-80 services/relationship_complexity.py 单元测试（纯本地，不联网）。

覆盖 A/C/D/E/F 五类：
  - 每类至少一条 detected=True 路径；
  - 每类缺失路径走"未知"（尤其 C 类信息不足 → status=='未知'）；
  - D 类 cycle_count>=2 的循环用例；
  - F 类与 persona_result.attachment 联动链路用例。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.relationship_complexity import (  # noqa: E402
    extract_family_system,
    extract_relationship_state,
    detect_rebound_patterns,
    model_condition_gap,
    analyze_trauma_drivers,
    analyze_relationship_complexity,
)


def msg(text: str) -> dict:
    return {"content": text}


class TestFamilySystemA(unittest.TestCase):
    """A 类：家庭系统。"""

    def test_detected_remarried_grandparent_debt(self):
        messages = [
            msg("我是离异的，之前带个娃，现在孩子上小学了"),
            msg("主要是我奶奶说了算，她老人家不点头这婚结不了"),
            msg("我们家还欠着房贷，每个月还钱压力挺大"),
            msg("我还有个姐姐，爸妈年纪大了以后养老肯定要一起担"),
        ]
        fs = extract_family_system(messages)
        self.assertTrue(fs["remarried_family"]["detected"])
        self.assertTrue(fs["remarried_family"]["evidence"])
        self.assertTrue(fs["stepchildren"]["detected"])
        self.assertEqual(fs["stepchildren"]["acceptance_level"], "中")
        self.assertTrue(fs["grandparent_influence"]["detected"])
        self.assertIn("奶奶", fs["grandparent_influence"]["who"])
        self.assertTrue(fs["family_debt"]["detected"])
        self.assertIn(fs["family_debt"]["level"], ("轻", "中", "重"))
        self.assertTrue(fs["caregiving_pressure"]["detected"])

    def test_no_signal_all_unknown(self):
        messages = [msg("今天天气不错"), msg("晚上吃什么")]
        fs = extract_family_system(messages)
        self.assertFalse(fs["remarried_family"]["detected"])
        self.assertFalse(fs["stepchildren"]["detected"])
        self.assertEqual(fs["stepchildren"]["acceptance_level"], "未知")
        self.assertFalse(fs["grandparent_influence"]["detected"])
        self.assertEqual(fs["grandparent_influence"]["stance"], "未知")
        self.assertFalse(fs["family_debt"]["detected"])


class TestRelationshipStateC(unittest.TestCase):
    """C 类：关系状态。"""

    def test_married_with_legal_prompt(self):
        messages = [
            msg("我们已经结婚三年了，我老婆最近在看房子"),
            msg("首付还差一点，可能要一起贷款"),
        ]
        st = extract_relationship_state(messages)
        self.assertEqual(st["status"], "已婚")
        self.assertTrue(st["evidence"])
        self.assertTrue(st["legal_risk_prompts"])

    def test_divorced(self):
        messages = [msg("我离过婚，前夫之前是做生意的")]
        st = extract_relationship_state(messages)
        self.assertEqual(st["status"], "离异")

    def test_unknown_when_insufficient_info(self):
        """信息不足必须标'未知'，绝不臆造。"""
        messages = [msg("在吗"), msg("刚下班，好累")]
        st = extract_relationship_state(messages)
        self.assertEqual(st["status"], "未知")
        self.assertEqual(st["confidence"], 0.0)
        self.assertEqual(st["evidence"], [])


class TestReboundPatternD(unittest.TestCase):
    """D 类：复合循环。"""

    def test_cycle_count_ge_two(self):
        messages = [
            msg("算了我们分手吧，实在过不下去"),
            msg("上次闹完我们又和好了，他来楼下等我到半夜"),
            msg("这次又提分手，说真的不想继续了"),
            msg("吵完第三天他又加回来求复合，我又心软了"),
        ]
        rb = detect_rebound_patterns(messages)
        self.assertGreaterEqual(rb["cycle_count"], 2)
        self.assertTrue(rb["cycle_evidence"])
        self.assertTrue(rb["intervention_tasks"])

    def test_entangled_and_ex_new(self):
        messages = [
            msg("他拉黑我两天又复联，天天发小作文纠缠"),
            msg("听说他已经带新女朋友去吃饭了"),
            msg("我真的想拉黑但又怕他寻死觅活"),
        ]
        rb = detect_rebound_patterns(messages)
        self.assertTrue(rb["entangled"])
        self.assertTrue(rb["ex_new_partner"])
        self.assertIn(rb["escalation_level"], ("低", "中", "高"))

    def test_no_cycle(self):
        messages = [msg("今天看了电影"), msg("周末去爬山")]
        rb = detect_rebound_patterns(messages)
        self.assertEqual(rb["cycle_count"], 0)
        self.assertFalse(rb["entangled"])
        self.assertEqual(rb["escalation_level"], "无")


class TestConditionGapE(unittest.TestCase):
    """E 类：条件差/异地。"""

    def test_income_gap_and_long_distance(self):
        messages = [
            msg("他收入比我高不少，年薪是我两倍"),
            msg("我们异地快8个月了，高铁来回要四个小时"),
        ]
        cg = model_condition_gap(messages)
        self.assertTrue(cg["income_gap"]["detected"])
        self.assertEqual(cg["income_gap"]["direction"], "对方高")
        self.assertTrue(cg["long_distance"]["detected"])
        self.assertEqual(cg["long_distance"]["months"], 8)
        self.assertIn(cg["tension_level"], ("低", "中", "高"))
        self.assertEqual(cg["task_difficulty_adjustment"], "困难")

    def test_no_gap(self):
        messages = [msg("周末一起逛超市")]
        cg = model_condition_gap(messages)
        self.assertFalse(cg["income_gap"]["detected"])
        self.assertFalse(cg["long_distance"]["detected"])
        self.assertEqual(cg["tension_level"], "未知")


class TestTraumaDriversF(unittest.TestCase):
    """F 类：创伤驱动 + 依恋联动。"""

    def test_trauma_with_attachment_link(self):
        messages = [
            msg("小时候我爸经常家暴，父母天天吵架，童年阴影很大"),
            msg("我特别怕你不要我，你一不回消息我就慌"),
            msg("我从来不敢拒绝，总是委屈自己迁就别人"),
        ]
        persona = {
            "attachment": {
                "primary_type": "焦虑型",
                "type": "焦虑型",
                "anxiety_score": 72,
                "avoidance_score": 30,
            }
        }
        tr = analyze_trauma_drivers(messages, persona)
        types = {t["type"] for t in tr["traumas"]}
        self.assertIn("原生家庭创伤", types)
        self.assertIn("被抛弃恐惧", types)
        self.assertIn("讨好型损耗", types)
        # 每个创伤都有 behavior_link
        for t in tr["traumas"]:
            self.assertTrue(t["behavior_link"])
            self.assertTrue(t["evidence"])
        # 依恋联动：焦虑型 + 被抛弃恐惧
        self.assertIn("焦虑型", tr["attachment_link"])
        self.assertTrue(tr["strategy_adaptations"])

    def test_no_persona_result(self):
        messages = [msg("随便聊聊")]
        tr = analyze_trauma_drivers(messages, None)
        self.assertEqual(tr["traumas"], [])
        self.assertIn("未提供 persona", tr["attachment_link"])


class TestFacade(unittest.TestCase):
    """门面函数：schema 完整性 + complexity_flags 每类至少一条。"""

    def test_full_profile_schema(self):
        messages = [
            msg("我离异带娃，主要我奶奶说了算"),
            msg("我们已经结婚了，房子首付可能要一起贷款"),
            msg("上个月分手又和好了，这个月又分手复合了一次"),
            msg("他收入比我高，我们异地快半年了"),
            msg("小时候我父母天天吵架，我特别怕你不要我"),
        ]
        profile = analyze_relationship_complexity("p_test", messages, {
            "attachment": {"primary_type": "恐惧型", "type": "恐惧型"}})
        self.assertEqual(profile["person_id"], "p_test")
        for key in ("family_system", "relationship_state", "rebound_pattern",
                    "condition_gap", "trauma_drivers", "complexity_flags"):
            self.assertIn(key, profile)
        cats = {f["category"] for f in profile["complexity_flags"]}
        for c in ("A", "C", "D", "E", "F"):
            self.assertIn(c, cats)


if __name__ == "__main__":
    unittest.main()
