# -*- coding: utf-8 -*-
"""第六批第二批：任务链/每日任务/截图分析/危机预警。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch6_tasks import task_chain, daily_tasks, screenshot_analyze, crisis_alert


class TestBatch6B(unittest.TestCase):
    def test_chain_unlocks_next(self):
        r = task_chain("暧昧", ["主动邀约"])
        self.assertEqual(r["next"], "提升亲密表达")

    def test_daily_tasks_3(self):
        self.assertGreaterEqual(len(daily_tasks("暧昧", {})), 3)

    def test_screenshot_emotion(self):
        r = screenshot_analyze("你到底什么意思？")
        self.assertEqual(r["emotion"], "疑惑")
        self.assertEqual(len(r["suggested_replies"]), 3)

    def test_crisis_red(self):
        r = crisis_alert({"trust_drop_days":3,"freq_drop_pct":60,"neg_emotion_pct":70})
        self.assertEqual(r["level"], "红")
        self.assertIsNotNone(r["repair_task"])

    def test_crisis_normal(self):
        r = crisis_alert({})
        self.assertEqual(r["level"], "正常")


if __name__ == "__main__":
    unittest.main(verbosity=2)
