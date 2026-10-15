# -*- coding: utf-8 -*-
"""罗盘导航真实接线测试（2026-10-13 外科手术式接通）。

验证：
1. _build_compass_snapshot 把 analyze() 的真实进边字段透传进 snapshot；
2. evaluate() 的导航字段不再是默认/占位/恒值——current_position 无"待计算"、
   direction_probability 随证据变化且归一化≈1；
3. 空 snapshot 不崩溃，缺失项标"未实现+原因"而非占位串；
4. 模式库 case_006（压力过载→给空间）可被真实事件信号命中。
纯本地、确定性、不调外部 LLM、不依赖正式库。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.direction_compass import DirectionCompass  # noqa: E402
from services.pattern_library import PatternLibrary  # noqa: E402


def _nav(snap):
    return DirectionCompass().evaluate("t", snap)


class TestSnapshotWiring(unittest.TestCase):
    """_build_compass_snapshot 透传真实进边字段。"""

    def test_build_snapshot_feeds_new_fields(self):
        from services.task_engine import TaskEngine
        te = TaskEngine.__new__(TaskEngine)  # 不触发 __init__（无需 db/affection）
        te.strategy_advisor = None
        persona = {
            "attachment": {"primary_type": "回避型"},
            "risk_warning": {"overall_level": "低"},
            "deception_risk": "低",
            "emotion_pattern": {"pattern": "中性平稳"},
            "trajectory_vector": {"emotion_trend": "↓", "activity_trend": "→",
                                  "delay_trend": "↑", "mutation_points": [{"date": "x"}],
                                  "periodicity": "深夜模式"},
            "trust": {"score": 42},
            "suspicion_chain": {"suspicion": 65.0, "level": "高"},
            "stress_attribution": {"category_distribution": {"relationship_threat": 1},
                                    "focal_events_count": 2},
            "pattern_match": {"matched": True, "case_id": "case_006",
                              "pattern": "压力过载", "similarity": 0.5},
            "conflict_repair": {"repair_rate": 30.0},
            "direction_history": {"current_direction": "后撤"},
            "forecast": {"p_separation_risk": 0.65},
        }
        snap = te._build_compass_snapshot(persona, "t")
        for k in ("trajectory_vector", "trust_score", "suspicion_score",
                  "stress_dist", "pattern_match", "repair_rate",
                  "direction_history_current", "p_separation_risk_in"):
            self.assertIn(k, snap, f"snapshot 缺进边键 {k}")
        self.assertEqual(snap["trust_score"], 42)
        self.assertEqual(snap["suspicion_score"], 65.0)
        self.assertEqual(snap["p_separation_risk_in"], 0.65)


class TestNavigationRealValues(unittest.TestCase):
    """导航字段从真实证据推导，非默认/占位/恒值。"""

    def test_no_placeholder_current_position(self):
        out = _nav({
            "trajectory_vector": {"emotion_trend": "→", "activity_trend": "→",
                                  "delay_trend": "↓", "mutation_points": [{"date": "x"}] * 8,
                                  "periodicity": "深夜模式(8%)"},
            "trust_score": 56, "suspicion_score": 0.0, "suspicion_level": "正常",
            "stress_dist": {}, "stress_focal_count": 0,
            "pattern_match": {"matched": False}, "repair_rate": 0,
        })
        cp = out["current_position"]
        self.assertNotEqual(cp["trajectory"], "待计算")
        self.assertNotEqual(cp["attribution"], "待计算")
        self.assertNotIn("待计算", str(cp))
        self.assertIn("情绪", cp["trajectory"])
        self.assertIn("56", cp["trust_level"])

    def test_direction_probability_not_constant_and_normalized(self):
        # 证据强（情绪↓+关系威胁+高怀疑）→ 后撤占主导
        hot = _nav({
            "trajectory_vector": {"emotion_trend": "↓", "delay_trend": "↑",
                                  "mutation_points": []},
            "trust_score": 40, "suspicion_score": 70, "suspicion_level": "高",
            "stress_dist": {"relationship_threat": 2}, "stress_focal_count": 2,
            "pattern_match": {"matched": True, "case_id": "case_004"},
            "repair_rate": 20,
        })["direction_probability"]
        # 证据弱（全平稳）→ 接近先验、后撤不主导
        calm = _nav({
            "trajectory_vector": {"emotion_trend": "→", "delay_trend": "→",
                                  "mutation_points": []},
            "trust_score": 60, "suspicion_score": 10, "suspicion_level": "正常",
            "stress_dist": {}, "stress_focal_count": 0,
            "pattern_match": {"matched": False}, "repair_rate": 90,
        })["direction_probability"]
        self.assertAlmostEqual(hot["P后撤有效"] + hot["P升温"] + hot["P维稳"],
                               1.0, places=2)
        self.assertAlmostEqual(calm["P后撤有效"] + calm["P升温"] + calm["P维稳"],
                               1.0, places=2)
        self.assertGreater(hot["P后撤有效"], calm["P后撤有效"],
                           "强证据应使后撤概率显著高于弱证据")
        # 不再是旧的恒值 0.5/0.3/0.2
        self.assertNotEqual(hot["P后撤有效"], 0.5)

    def test_path_status_and_detour_from_evidence(self):
        out = _nav({
            "trajectory_vector": {"emotion_trend": "↓", "delay_trend": "↑",
                                  "mutation_points": []},
            "trust_score": 50, "suspicion_score": 10,
            "stress_dist": {"real_world_pressure": 1}, "stress_focal_count": 1,
            "pattern_match": {"matched": True, "case_id": "case_006"},
            "repair_rate": 80,
        })
        # 延迟↑+情绪↓ → 拥堵
        self.assertEqual(out["path_status"], "拥堵")
        # 绕行触发（给空间类模式命中）→ 有计划
        self.assertIn(out["detour_plan"]["magnitude"], ("轻度=换话术降压/给空间",
                                                        "大幅=后撤留灯"))

    def test_threshold_linkage_rule(self):
        # 后撤方向 + 高怀疑 → 强化引导改变
        out = _nav({
            "trajectory_vector": {"emotion_trend": "↓", "delay_trend": "↑",
                                  "mutation_points": []},
            "trust_score": 40, "suspicion_score": 65,
            "stress_dist": {"relationship_threat": 1}, "stress_focal_count": 1,
            "pattern_match": {"matched": True, "case_id": "case_003"},
            "repair_rate": 30,
        })
        self.assertIn("强化引导改变", out["threshold_linkage"]["suggestion"])
        # 低怀疑 → 常规应对
        out2 = _nav({
            "trajectory_vector": {"emotion_trend": "→", "delay_trend": "→",
                                  "mutation_points": []},
            "trust_score": 60, "suspicion_score": 10,
            "stress_dist": {}, "stress_focal_count": 0,
            "pattern_match": {"matched": False}, "repair_rate": 90,
        })
        self.assertIn("常规应对", out2["threshold_linkage"]["suggestion"])

    def test_empty_snapshot_no_crash_no_placeholder(self):
        out = _nav({})
        self.assertIn("aligned", out)
        # 缺失项标"未实现+原因"，不写"待计算"
        self.assertNotIn("待计算", str(out["current_position"]))
        self.assertIn("未实现", str(out["current_position"]))


class TestPatternCase006(unittest.TestCase):
    """case_006（压力过载→给空间）可被事件信号命中。"""

    def test_case006_matches_pressure_overload(self):
        pl = PatternLibrary()
        r = pl.match("压力过载事件 放过我")
        self.assertTrue(r["matched"])
        self.assertEqual(r["case_id"], "case_006")
        self.assertIn("给空间", r["suggested_response"])

    def test_case006_exists(self):
        ids = {c.case_id for c in PatternLibrary().cases}
        self.assertIn("case_006", ids)


if __name__ == "__main__":
    unittest.main()
