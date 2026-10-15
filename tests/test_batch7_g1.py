# -*- coding: utf-8 -*-
"""第七批第一批(1-5)测试。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch7_group1 import (stage_to_11, fill_wechat_input, q_avatar,
    dual_twin_simulate, consult)


class TestBatch7G1(unittest.TestCase):
    def test_11_level(self):
        r = stage_to_11(45)
        self.assertEqual(r["level_name"], "暧昧")

    def test_wechat_fill_framework(self):
        self.assertEqual(fill_wechat_input("你好")["action"], "fill_wechat_input")

    def test_q_avatar(self):
        self.assertFalse(q_avatar("a")["ready"])

    def test_dual_twin(self):
        r = dual_twin_simulate("冷淡", "主动")
        self.assertIn("user_side_mock", r)

    def test_consult_modes(self):
        self.assertEqual(consult("问题", "growth")["mode"], "自我成长咨询")


if __name__ == "__main__":
    unittest.main(verbosity=2)
