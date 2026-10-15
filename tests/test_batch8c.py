# -*- coding: utf-8 -*-
"""第八批第三批：意图截图/上下文仪表盘/并行。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch8_intent import intent_screenshot, context_dashboard, parallel_analyzer


class TestBatch8C(unittest.TestCase):
    def test_intent_transfer(self):
        self.assertEqual(intent_screenshot("转账￥520")["type"], "转账")

    def test_context_compress(self):
        r = context_dashboard({"chat":5000}, limit=4000)
        self.assertTrue(r["compressed"])

    def test_parallel_isolates(self):
        r = parallel_analyzer({"ok":lambda:1,"bad":lambda:1/0})
        self.assertEqual(r["ok"],1); self.assertIsNone(r["bad"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
