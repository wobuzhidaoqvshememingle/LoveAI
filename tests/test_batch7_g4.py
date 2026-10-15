# -*- coding: utf-8 -*-
"""第七批第四批(16-21)测试。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch7_group4 import (set_proxy, proxy_for_model_request,
    wechat_ui_template, scan_wechat_files, subject_for, autonomy_task, stats_panel)


class TestBatch7G4(unittest.TestCase):
    def test_proxy(self):
        set_proxy("http://127.0.0.1:7890")
        self.assertTrue(proxy_for_model_request())

    def test_wechat_ui_versions(self):
        self.assertIn("chat_list", wechat_ui_template("4.1"))

    def test_scan_framework(self):
        self.assertFalse(scan_wechat_files("C:/x")["key_found"])

    def test_subject_routing(self):
        self.assertEqual(subject_for("她让我写借条"), "法学")

    def test_autonomy(self):
        self.assertEqual(autonomy_task("暧昧")["type"], "无设备练习")

    def test_stats_rate(self):
        r = stats_panel({"task_done":8,"task_total":10,"analyze_count":50})
        self.assertEqual(r["task_completion_rate"], 0.8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
