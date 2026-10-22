# -*- coding: utf-8 -*-
"""算法层边界测试（R3.3-45 死磕验收：侧写该分层的分层、评分该准的准）。

覆盖范围（只动 services/six_layer_persona.py 与 services/persona_analyzer.py 的纯函数/方法）：
  A. to_six_layer 边界：None 输入 / attachment 为字符串 / 非 dict 嵌套 / person_id 覆盖
  B. extract_family_context + _pick_family_winner：空消息 / manual 覆盖 / 平票取最近 /
     票多者胜 / 父母离异双计 / 字符串消息兼容
  C. 评分器空输入兜底：满意度 / 价值观 / 自我分化 / 情绪模式 / 事件权重 / 可信度
  D. classify_connection_type 边界：空画像 / 向后兼容字符串 attachment
  E. "该准的准"：明显高/低分输入确实把对应维度推到位
"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.six_layer_persona import (  # noqa: E402
    Layer, SixLayerProfile, to_six_layer,
)
from services.persona_analyzer import (  # noqa: E402
    PersonaAnalyzer, extract_family_context, _pick_family_winner,
)


# ====================================================================== #
# A. to_six_layer 边界
# ====================================================================== #
class TestToSixLayerEdges(unittest.TestCase):

    def test_none_input_no_crash(self):
        prof = to_six_layer(None)
        self.assertEqual(sum(len(v) for v in prof.evidence.values()), 0)
        self.assertEqual(prof.person_id, "")

    def test_attachment_as_plain_string(self):
        """attachment 不是 dict 而是字符串时，走 elif str 分支挂到 L4。"""
        prof = to_six_layer({"attachment": "anxious_preoccupied"})
        l4 = [e["quote"] for e in prof.evidence[Layer.L4_ATTACHMENT]]
        self.assertEqual(len(l4), 1)
        self.assertIn("anxious_preoccupied", l4[0])

    def test_non_dict_nested_values_gracefully_skipped(self):
        """communication_style/behavior 是字符串而非 dict 时不炸、不挂证据。"""
        prof = to_six_layer({
            "communication_style": "温和直接",   # 字符串，不是 dict
            "behavior": "string_not_dict",      # 字符串，不是 dict
        })
        self.assertEqual(len(prof.evidence[Layer.L1_SURFACE]), 0)
        self.assertEqual(len(prof.evidence[Layer.L2_BEHAVIOR]), 0)

    def test_empty_attachment_dict_no_crash(self):
        prof = to_six_layer({"attachment": {}})
        self.assertEqual(len(prof.evidence[Layer.L4_ATTACHMENT]), 0)

    def test_person_id_override_wins(self):
        prof = to_six_layer({"person_id": "from_dict"}, person_id="override")
        self.assertEqual(prof.person_id, "override")

    def test_person_id_falls_back_to_dict(self):
        prof = to_six_layer({"person_id": "from_dict"})
        self.assertEqual(prof.person_id, "from_dict")

    def test_relationship_stage_plain_string(self):
        """relationship_stage 直接是字符串（不是 dict）时也挂 L1。"""
        prof = to_six_layer({"relationship_stage": "暧昧期"})
        l1_quotes = " ".join(e["quote"] for e in prof.evidence[Layer.L1_SURFACE])
        self.assertIn("暧昧期", l1_quotes)

    def test_relationship_stage_dict_without_known_keys(self):
        """relationship_stage 是 dict 但没有 stage/label/name → 不挂证据、不炸。"""
        prof = to_six_layer({"relationship_stage": {"confidence": "high"}})
        self.assertEqual(len(prof.evidence[Layer.L1_SURFACE]), 0)

    def test_add_evidence_none_dict_defaults(self):
        p = SixLayerProfile()
        p.add_evidence(Layer.L1_SURFACE, None)  # type: ignore[arg-type]
        ev = p.evidence[Layer.L1_SURFACE][0]
        self.assertEqual(ev["source"], "unknown")
        self.assertEqual(ev["weight"], 1.0)


# ====================================================================== #
# B. extract_family_context + _pick_family_winner
# ====================================================================== #
class TestFamilyVoting(unittest.TestCase):

    def test_empty_messages_all_unknown(self):
        r = extract_family_context([])
        self.assertEqual(r["family_structure"], "未知")
        self.assertEqual(r["parents_status"], "未知")
        self.assertEqual(r["atmosphere"], "未知")
        self.assertEqual(r["siblings"], [])
        self.assertFalse(r["manual"])

    def test_manual_override(self):
        r = extract_family_context([], manual={"family_structure": "双亲"})
        self.assertEqual(r["family_structure"], "双亲")
        # 未提供的键仍走字面提取
        self.assertEqual(r["parents_status"], "未知")
        self.assertTrue(r["manual"])

    def test_pick_winner_takes_higher_count(self):
        votes = {"a": [3, 0], "b": [1, 99]}
        self.assertEqual(_pick_family_winner(votes), "a")

    def test_pick_winner_tie_breaks_by_later_index(self):
        votes = {"单亲": [1, 0], "双亲": [1, 5], "重组": [0, -1]}
        self.assertEqual(_pick_family_winner(votes), "双亲")

    def test_pick_winner_all_zero_returns_none(self):
        self.assertIsNone(_pick_family_winner({"a": [0, -1], "b": [0, -1]}))

    def test_divorce_double_counts_to_single_parent(self):
        """'父母离异' 同时记 状态=离异 + 结构=单亲。"""
        r = extract_family_context([{"content": "父母离异，我跟我妈过"}])
        self.assertEqual(r["parents_status"], "离异")
        self.assertEqual(r["family_structure"], "单亲")

    def test_siblings_detected_dedup(self):
        r = extract_family_context([
            {"content": "我姐比我大两岁"},
            {"content": "我姐今天来"},      # 重复姐姐，应去重
            {"content": "我哥也在"},
        ])
        self.assertIn("姐姐", r["siblings"])
        self.assertIn("哥哥", r["siblings"])
        self.assertEqual(r["siblings"].count("姐姐"), 1)

    def test_string_messages_compat(self):
        """消息元素是裸字符串（不是 dict）时也能提取。"""
        r = extract_family_context(["我爸妈感情很好", "我有个姐姐"])
        self.assertEqual(r["family_structure"], "双亲")
        self.assertEqual(r["parents_status"], "和睦")
        self.assertIn("姐姐", r["siblings"])

    def test_empty_content_skipped(self):
        r = extract_family_context([{"content": ""}, {"direction": "received"}])  # 无 content
        self.assertEqual(r["family_structure"], "未知")


# ====================================================================== #
# C. 评分器空输入 / 兜底分
# ====================================================================== #
class TestScorerEmptyFallbacks(unittest.TestCase):

    def setUp(self):
        self.an = PersonaAnalyzer(db=None)

    def test_satisfaction_empty_messages(self):
        r = self.an._analyze_relationship_satisfaction([], [], [], {}, {})
        self.assertEqual(r["score"], 50)
        self.assertEqual(r["level"], "中等")

    def test_values_match_empty_messages(self):
        r = self.an._analyze_values_match("", "", [])
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["level"], "未知")
        self.assertIn("数据不足", r["description"])

    def test_self_differentiation_empty_messages(self):
        r = self.an._analyze_self_differentiation("", "", [])
        self.assertEqual(r["score"], 0)
        self.assertEqual(r["level"], "未知")

    def test_emotion_pattern_empty_list(self):
        r = self.an._analyze_emotion_pattern([])
        self.assertEqual(r["pattern"], "中性平稳")
        self.assertEqual(r["positive_count"], 0)
        self.assertEqual(r["negative_count"], 0)

    def test_weight_by_event_type_none(self):
        self.assertEqual(self.an._weight_by_event_type(None), 1.0)

    def test_weight_by_event_type_empty_string(self):
        self.assertEqual(self.an._weight_by_event_type(""), 1.0)

    def test_trustworthiness_no_promises_default_medium(self):
        r = self.an._analyze_trustworthiness([], "", "")
        self.assertEqual(r["promise_count"], 0)
        self.assertEqual(r["promise_fulfillment_rate"], 70)
        self.assertEqual(r["level"], "medium")


# ====================================================================== #
# D. classify_connection_type 边界
# ====================================================================== #
class TestClassifyConnectionTypeEdges(unittest.TestCase):

    def setUp(self):
        self.an = PersonaAnalyzer(db=None)

    def test_empty_persona_no_crash(self):
        r = self.an.classify_connection_type({})
        self.assertIn("type", r)
        self.assertIn(r["type"], ("镜像型", "互补型", "成长型", "共同旅程型", "对立型"))
        self.assertIn("score_breakdown", r)

    def test_backward_compat_string_attachment(self):
        """旧画像 attachment='secure' 字符串也能跑。"""
        r = self.an.classify_connection_type({"attachment": "secure"})
        self.assertIn("type", r)

    def test_opposing_wins_over_everything(self):
        """风险预警=高 → 直接对立型，优先级最高。"""
        r = self.an.classify_connection_type({
            "attachment": {"primary_type": "安全型"},
            "values_match": {"score": 80},
            "risk_warning": {"overall_level": "高"},
        })
        self.assertEqual(r["type"], "对立型")


# ====================================================================== #
# E. "该准的准"：明显高/低分输入把对应维度推到位
# ====================================================================== #
class TestScorerSanityHighLow(unittest.TestCase):

    def setUp(self):
        self.an = PersonaAnalyzer(db=None)

    def test_high_self_diff_input_scores_high(self):
        text = ("我觉得你的想法有道理，在我看来，我需要一点空间，"
                "我自己决定，我们可以有不同意见，你说得有道理我也有不同看法")
        r = self.an._analyze_self_differentiation(text, "",
                                                  [{"direction": "received", "content": "x"}])
        self.assertGreaterEqual(r["score"], 60)

    def test_low_self_diff_input_scores_low(self):
        text = ("你不爱我了，你根本不在乎我，我都是为了你，我为你付出这么多，"
                "你必须，你应该，我离不开你，没有你我活不下去，你就是我的全部")
        r = self.an._analyze_self_differentiation(text, "",
                                                  [{"direction": "received", "content": "x"}])
        self.assertLessEqual(r["score"], 30)
        self.assertEqual(r["level"], "自我分化低")

    def test_broken_promises_drives_trust_critical(self):
        msgs = []
        t = 0
        for _ in range(5):
            msgs.append({"direction": "received", "content": "我答应周末陪你，说好了",
                         "msg_type": 1})
            msgs.append({"direction": "sent", "content": "那说好了", "msg_type": 1})
            msgs.append({"direction": "received", "content": "啊忘了，下次再说吧",
                         "msg_type": 1})
            msgs.append({"direction": "sent", "content": "你又没兑现", "msg_type": 1})
        r = self.an._analyze_trustworthiness(
            msgs,
            "我答应你，我说到做到，我会改的，别生气嘛",
            "你又没兑现，你总是说说而已")
        self.assertLess(r["overall_trust_score"], 40)
        self.assertEqual(r["level"], "critical")


# ====================================================================== #
# F. _analyze_power_dynamic 对 created_at 类型兼容（int 时间戳不炸）
# ====================================================================== #
class TestPowerDynamicCreatedAtTypes(unittest.TestCase):

    def setUp(self):
        self.an = PersonaAnalyzer(db=None)

    def _split(self, msgs):
        other = [m for m in msgs if m["direction"] == "received"]
        mine = [m for m in msgs if m["direction"] == "sent"]
        return self.an._analyze_power_dynamic(msgs, other, mine)

    def test_int_timestamp_no_crash(self):
        """created_at 为 int Unix 时间戳时不炸、能跨天统计发起次数。"""
        msgs = [
            {"direction": "sent",     "content": "早",  "msg_type": 1, "created_at": 1792627200},
            {"direction": "received", "content": "早啊", "msg_type": 1, "created_at": 1792627800},
            {"direction": "received", "content": "在忙", "msg_type": 1, "created_at": 1792713600},
            {"direction": "sent",     "content": "嗯",  "msg_type": 1, "created_at": 1792714200},
        ]
        r = self._split(msgs)
        self.assertIn("pattern", r)
        # 跨两天：第一天 sent 发起，第二天 received 发起
        self.assertEqual(r["my_initiate"], 1)
        self.assertEqual(r["other_initiate"], 1)

    def test_str_timestamp_regression(self):
        """ISO 字符串时间戳原路径不回归。"""
        msgs = [
            {"direction": "sent",     "content": "早",  "msg_type": 1, "created_at": "2026-10-21T08:00:00"},
            {"direction": "received", "content": "早啊", "msg_type": 1, "created_at": "2026-10-21T08:10:00"},
            {"direction": "received", "content": "在忙", "msg_type": 1, "created_at": "2026-10-22T08:00:00"},
            {"direction": "sent",     "content": "嗯",  "msg_type": 1, "created_at": "2026-10-22T08:10:00"},
        ]
        r = self._split(msgs)
        self.assertEqual(r["my_initiate"], 1)
        self.assertEqual(r["other_initiate"], 1)

    def test_missing_created_at_no_crash(self):
        """缺 created_at 字段时不炸，所有消息归同一天。"""
        msgs = [
            {"direction": "sent",     "content": "x", "msg_type": 1},
            {"direction": "received", "content": "y", "msg_type": 1},
        ]
        r = self._split(msgs)
        self.assertIn("pattern", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
