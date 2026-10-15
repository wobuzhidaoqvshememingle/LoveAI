# -*- coding: utf-8 -*-
"""第四批：战略层/执行轨迹/情绪动力学/状态日志/护栏闸口。"""
import os, sys, tempfile, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from models.database import Database
from services.batch4_enhance import (relationship_strategist, ExecutionTracer,
    decay_emotion, accumulate_emotion, StateChangeLog, gate_check)


class TestBatch4(unittest.TestCase):
    def test_strategist_stable_stage(self):
        r = relationship_strategist({"Trust":70,"Attraction":60,"Comfort":60}, 9)
        self.assertEqual(r["stage"], "稳定")
        self.assertIn("health_score", r)

    def test_strategist_conflict_overrides(self):
        r = relationship_strategist({"Trust":40}, 7, recent_conflict=True)
        self.assertEqual(r["stage"], "矛盾期")

    def test_tracer_records_and_failures(self):
        td = tempfile.mkdtemp()
        db = Database(os.path.join(td, "t.db")); db.init_tables()
        tr = ExecutionTracer(db)
        tr.log("emotion", "你好", "平静", True, 1.2)
        tr.log("twin", "在吗", "ERR", False, 9.9)
        self.assertEqual(len(tr.recent_failures()), 1)

    def test_emotion_decay(self):
        self.assertLess(decay_emotion("愤怒", 1.0, 24), 1.0)  # 半衰期后衰减

    def test_emotion_burst_threshold(self):
        r = accumulate_emotion(["愤怒","愤怒","愤怒"], "愤怒")
        self.assertTrue(r["burst_risk"])

    def test_state_change_log(self):
        log = StateChangeLog()
        log.record("affection.total", +3, "任务完成", "task_engine")
        self.assertEqual(log.recent("affection.total")[0]["delta"], 3)

    def test_gate_check_catches_mismatch(self):
        r = gate_check("给空间", ["emotion"], "你怎么了，为什么不回", "给空间")
        self.assertFalse(r["pass"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
