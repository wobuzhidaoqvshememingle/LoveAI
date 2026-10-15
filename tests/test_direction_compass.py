# -*- coding: utf-8 -*-
"""DirectionCompass 方向罗盘单元测试。"""
import os
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.direction_compass import DirectionCompass  # noqa: E402


class TestDirectionGate(unittest.TestCase):
    def setUp(self):
        self.c = DirectionCompass()

    def test_withdraw_fail(self):
        g = self.c._direction_gate({"strategy_text": "这个不行就放弃，及时止损分手"})
        self.assertFalse(g["pass"])
        self.assertEqual(g["level"], "fail")

    def test_advance_pass(self):
        g = self.c._direction_gate({"strategy_text": "当前策略：破冰升温，引导对等投入推进关系"})
        self.assertTrue(g["pass"])

    def test_respect_stop(self):
        g = self.c._direction_gate({"strategy_text": "对方明确拒绝，尊重对方，停止联系"})
        self.assertTrue(g["pass"])

    def test_no_action_warn(self):
        g = self.c._direction_gate({"strategy_text": "随便聊聊看看"})
        self.assertEqual(g["level"], "warn")


class TestMatchGate(unittest.TestCase):
    def setUp(self):
        self.c = DirectionCompass()

    def test_avoidant_pressure_fail(self):
        g = self.c._match_gate({
            "attachment": "回避型",
            "strategy_text": "高频表白，直接施压催促她给答案"})
        self.assertEqual(g["level"], "fail")

    def test_avoidant_space_pass(self):
        g = self.c._match_gate({
            "attachment": "回避型",
            "strategy_text": "降压给空间，低需求感，间歇性引导投入"})
        self.assertTrue(g["pass"])

    def test_anxious_cold_fail(self):
        g = self.c._match_gate({
            "attachment": "焦虑型",
            "strategy_text": "故意不回，长期冷淡，制造稀缺"})
        self.assertEqual(g["level"], "fail")

    def test_anxious_stable_pass(self):
        g = self.c._match_gate({
            "attachment": "焦虑型",
            "strategy_text": "稳定回应，给足安全感和确定性"})
        self.assertTrue(g["pass"])

    def test_high_risk_no_counter_warn(self):
        g = self.c._match_gate({
            "attachment": "安全型", "risk_level": "5",
            "strategy_text": "正常聊天，温和推进"})
        self.assertIn(g["level"], ("warn", "fail"))


class TestTruthGate(unittest.TestCase):
    def setUp(self):
        self.c = DirectionCompass()

    def test_high_deception_warn(self):
        g = self.c._truth_gate({"deception_risk": "高"})
        self.assertEqual(g["level"], "warn")

    def test_low_validity_high_weight_warn(self):
        g = self.c._truth_gate({"review_validity": 0.4, "sim_weight": 0.7})
        self.assertEqual(g["level"], "warn")

    def test_normal_pass(self):
        g = self.c._truth_gate({"deception_risk": "低",
                                "review_validity": 0.8, "sim_weight": 0.6})
        self.assertTrue(g["pass"])


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_compass_")
        self.db = Database(os.path.join(self.tmpdir, "c.db"))
        self.db.init_tables()
        self.c = DirectionCompass(self.db)

    def test_aligned_good(self):
        out = self.c.evaluate("p1", {
            "stage": "暧昧", "attachment": "回避型", "risk_level": "低",
            "strategy_text": "降压给空间，破冰升温，引导对等投入推进",
            "deception_risk": "低", "review_validity": 0.8, "sim_weight": 0.6,
            "affection_trend": "up",
            "metrics": {"mutual": 0.8, "reciprocity": 0.75, "repair": 0.7,
                        "satisfaction": 0.8, "stability": 0.7},
        })
        self.assertTrue(out["aligned"], out["verdict"])

    def test_drift_on_withdraw(self):
        out = self.c.evaluate("p1", {
            "stage": "倦怠", "attachment": "安全型",
            "strategy_text": "放弃吧，及时止损，分手撤离",
            "affection_trend": "down",
            "metrics": {"mutual": 0.2, "reciprocity": 0.2},
        })
        self.assertFalse(out["aligned"])
        self.assertTrue(out["drift"]["drift"])

    def test_backward_gaps_listed(self):
        out = self.c.evaluate("p1", {
            "strategy_text": "破冰升温，引导对等投入推进",
            "metrics": {"mutual": 0.2, "reciprocity": 0.8},
        })
        dims = [g["dimension"] for g in out["gaps"]]
        self.assertIn("mutual", dims)
        self.assertNotIn("reciprocity", dims)

    def test_empty_snapshot_no_crash(self):
        out = self.c.evaluate("p1", {})
        self.assertIn("aligned", out)
        self.assertIsNone(out["alignment_score"])


if __name__ == "__main__":
    unittest.main()
