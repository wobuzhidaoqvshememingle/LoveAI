# -*- coding: utf-8 -*-
"""R3.3-82 连续帧动画状态扩展测试。

新增断言：
  - mouth_open (0..1 连续) 随 TTS 振幅变化；
  - blink_phase 随眨眼开/闭切换；
  - 向后兼容：旧的离散 mouth 名 / eyes 字符串行为不变。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from services.pet_animator import PetAnimator  # noqa: E402


class TestPetAnimatorFrames(unittest.TestCase):
    def setUp(self):
        self.anim = PetAnimator()

    def test_mouth_open_continuous(self):
        self.anim.set_tts_level(0.0)
        st = self.anim.current_state()
        self.assertEqual(st["mouth"], "rest")
        self.assertEqual(st["mouth_open"], 0.0)

        self.anim.set_tts_level(1.0)
        st = self.anim.current_state()
        self.assertEqual(st["mouth"], "o")          # 旧离散兼容
        self.assertGreater(st["mouth_open"], 0.9)   # 新连续量

        self.anim.set_tts_level(0.1)
        st = self.anim.current_state()
        self.assertEqual(st["mouth"], "a")
        self.assertLess(st["mouth_open"], 0.2)

    def test_blink_phase_continuous(self):
        self.anim._start_blink()
        st = self.anim.current_state()
        self.assertEqual(st["eyes"], "closed")
        self.assertEqual(st["blink_phase"], 1.0)
        self.anim._end_blink()
        self.assertEqual(self.anim.current_state()["blink_phase"], 0.0)

    def test_new_fields_present(self):
        st = self.anim.current_state()
        self.assertIn("mouth_open", st)
        self.assertIn("blink_phase", st)


if __name__ == "__main__":
    unittest.main()
