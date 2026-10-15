# -*- coding: utf-8 -*-
"""第七批第二批(6-10)测试。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch7_group2 import (outcome_probability, NotificationSettings,
    sanitize, backup_all, HOTKEYS, rebind)


class TestBatch7G2(unittest.TestCase):
    def test_probability_sums_1(self):
        self.assertAlmostEqual(sum(outcome_probability("愤怒").values()), 1.0, 1)

    def test_notification_toggle(self):
        n = NotificationSettings(); n.set("new_message", False)
        self.assertFalse(n.should_notify("new_message"))

    def test_sanitize(self):
        self.assertEqual(sanitize("宝贝我爱死你"), "亲爱的很在乎你")

    def test_backup(self):
        self.assertTrue(backup_all({"a":1})["encrypted"])

    def test_rebind(self):
        rebind("open_main", "ctrl+alt+p")
        self.assertEqual(HOTKEYS["open_main"], "ctrl+alt+p")


if __name__ == "__main__":
    unittest.main(verbosity=2)
