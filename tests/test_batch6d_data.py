# -*- coding: utf-8 -*-
"""第六批第四批：时间线/隐私模式/多目标/AB学习。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch6_data import (relationship_timeline, PrivacyMode,
    MultiTarget, ABLearning)


class TestBatch6D(unittest.TestCase):
    def test_timeline_nodes(self):
        r = relationship_timeline([{"ts":1,"event":"stage_up","new_value":60}])
        self.assertEqual(r["node_count"], 1)
        self.assertEqual(len(r["affection_curve"]), 1)

    def test_privacy_redact(self):
        p = PrivacyMode(enabled=True)
        self.assertNotIn("秘密", p.redact("秘密"))

    def test_multitarget_merge(self):
        m = MultiTarget()
        m.register("wx1","小雨"); m.register("wx2","小雨")
        self.assertTrue(m.merge_same_person("wx1","wx2"))

    def test_ab_learning_best(self):
        ab = ABLearning()
        ab.record("共情", 0.9); ab.record("霸道", 0.3)
        self.assertEqual(ab.best_style(), "共情")


if __name__ == "__main__":
    unittest.main(verbosity=2)
