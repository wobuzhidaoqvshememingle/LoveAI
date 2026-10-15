# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 2D 序列帧动画控制器单测（R3.3-51）

覆盖：
  - 呼吸：scale_y 在 1.00~1.02 区间内
  - 眨眼：调用 _start_blink / _end_blink 状态正确切换
  - 表情切换：合法/非法值
  - 口型映射：0..1 振幅 -> a/i/u/e/o
  - sprite 加载：12 张占位图都能扫到
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

from services.pet_animator import (  # noqa: E402
    PetAnimator,
    EXPRESSIONS,
    VOWELS,
    BREATH_AMPLITUDE,
)


class TestPetAnimator(unittest.TestCase):
    def setUp(self):
        self.anim = PetAnimator()

    def test_sprites_loaded(self):
        # 12 张占位图都应在
        for name in (EXPRESSIONS + ["eyes_open", "eyes_closed"]
                     + [f"mouth_{v}" for v in VOWELS]):
            self.assertIn(name, self.anim.sprites, f"missing sprite: {name}")

    def test_expression_set(self):
        for e in EXPRESSIONS:
            self.anim.set_expression(e)
            self.assertEqual(self.anim.current_state()["expression"], e)

    def test_expression_unknown_ignored(self):
        self.anim.set_expression("happy")
        self.anim.set_expression("angry")  # 非法
        self.assertEqual(self.anim.current_state()["expression"], "happy")

    def test_tts_level_to_mouth(self):
        # 0 -> rest
        self.anim.set_tts_level(0.0)
        self.assertEqual(self.anim.current_state()["mouth"], "rest")
        # 满振幅 -> 最后一个元音 o
        self.anim.set_tts_level(1.0)
        self.assertEqual(self.anim.current_state()["mouth"], "o")
        # 小振幅 -> a
        self.anim.set_tts_level(0.1)
        self.assertEqual(self.anim.current_state()["mouth"], "a")

    def test_level_clamped(self):
        self.anim.set_tts_level(5.0)
        self.assertLessEqual(self.anim.current_state()["tts_level"], 1.0)
        self.anim.set_tts_level(-5.0)
        self.assertGreaterEqual(self.anim.current_state()["tts_level"], 0.0)

    def test_blink_state_transitions(self):
        self.anim._start_blink()
        self.assertEqual(self.anim.current_state()["eyes"], "closed")
        self.anim._end_blink()
        self.assertEqual(self.anim.current_state()["eyes"], "open")

    def test_breath_scale_range(self):
        # 手动跑几个 tick，scale_y 应在 1.0 ~ 1.0+amplitude 之间
        for _ in range(100):
            self.anim._tick()
            s = getattr(self.anim, "_scale_y", 1.0)
            self.assertGreaterEqual(s, 1.0 - 1e-6)
            self.assertLessEqual(s, 1.0 + BREATH_AMPLITUDE + 1e-6)

    def test_start_stop(self):
        self.anim.start()
        self.assertTrue(self.anim.is_running())
        self.anim.stop()
        self.assertFalse(self.anim.is_running())


if __name__ == "__main__":
    unittest.main()
