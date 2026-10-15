# -*- coding: utf-8 -*-
"""第八批第一批：API额度/工作流模板/OCR/长截图/任务看板。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch8_harness import (ApiBalance, pick_template, structured_ocr,
    long_screenshot, task_board)


class TestBatch8A(unittest.TestCase):
    def test_api_balance_level(self):
        ab = ApiBalance(); ab.balance = 0.02
        r = ab.check(0.05)
        self.assertTrue(r["degrade_to_local"])

    def test_pick_template(self):
        self.assertIn("strategy=修复", pick_template("吵架修复我的"))

    def test_structured_ocr(self):
        r = structured_ocr("你好？\n在吗")
        self.assertEqual(r["count"], 2)

    def test_long_screenshot(self):
        self.assertTrue(long_screenshot(["a.png","b.png"])["stitched"])

    def test_task_board(self):
        r = task_board([{"title":"邀约","status":"进行中"},{"title":"破冰","status":"待开始"}])
        self.assertEqual(r["total"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
