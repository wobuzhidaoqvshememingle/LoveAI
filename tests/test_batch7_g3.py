# -*- coding: utf-8 -*-
"""第七批第三批(11-15)测试。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch7_group3 import (set_theme, set_font, detect_gender,
    top1_execution, top2_ask, match_person_by_window_title)


class TestBatch7G3(unittest.TestCase):
    def test_theme_font(self):
        set_theme("浅色"); set_font(20)
        from services.batch7_group3 import UI_PREF
        self.assertEqual(UI_PREF["theme"], "浅色")

    def test_gender_detect(self):
        self.assertEqual(detect_gender("我买了口红和包包"), "女")

    def test_top_split(self):
        self.assertEqual(top1_execution(["邀约"])["核心"], "主动执行+攻略")

    def test_window_match(self):
        r = match_person_by_window_title("与小雨聊天", {"p_x":"小雨"})
        self.assertTrue(r["matched"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
