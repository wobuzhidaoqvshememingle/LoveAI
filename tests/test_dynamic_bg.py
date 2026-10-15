# -*- coding: utf-8 -*-
"""R3.3-21 动效背景 UI 单测：DynamicBackgroundWidget 实例化 / show-hide / 开关 / 透明度。"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.dynamic_background import DynamicBackground, DynamicBackgroundWidget  # noqa: E402


class TestDynamicBackgroundWidget(unittest.TestCase):
    def test_alias_same_class(self):
        """DynamicBackgroundWidget 是 DynamicBackground 的别名。"""
        self.assertIs(DynamicBackgroundWidget, DynamicBackground)

    def test_instantiate_show_hide_no_raise(self):
        """实例化 -> show -> hide 全程不抛异常。"""
        w = DynamicBackgroundWidget(particle_count=12)
        w.resize(200, 120)
        w.show()
        w.hide()
        # 隐藏后定时器应已停（CPU 约束）
        self.assertFalse(w._timer.isActive())

    def test_toggle_solid_mode(self):
        """set_enabled(False) -> 纯色模式且停表；再开恢复跑表。"""
        w = DynamicBackgroundWidget(particle_count=8)
        w.resize(200, 120)
        w.show()
        w.set_enabled(False)
        self.assertFalse(w.is_enabled())
        self.assertFalse(w._timer.isActive())
        # 纯色模式 paintEvent 不抛异常（离屏截图）
        pm = w.grab()
        self.assertFalse(pm.isNull())
        w.set_enabled(True)
        self.assertTrue(w.is_enabled())
        pm2 = w.grab()
        self.assertFalse(pm2.isNull())

    def test_opacity_range(self):
        """set_opacity 0-100 夹紧；坏值回退默认。"""
        w = DynamicBackgroundWidget(particle_count=4)
        w.set_opacity(50)
        self.assertEqual(w.opacity(), 50)
        w.set_opacity(999)
        self.assertEqual(w.opacity(), 100)
        w.set_opacity(-5)
        self.assertEqual(w.opacity(), 0)
        w.set_opacity("abc")  # type: ignore[arg-type]
        self.assertEqual(w.opacity(), 90)

    def test_grab_animated_no_raise(self):
        """开启状态下离屏截图（走 paintEvent 粒子分支）不抛异常。"""
        w = DynamicBackgroundWidget(particle_count=10)
        w.resize(240, 160)
        w.show()
        pm = w.grab()
        self.assertFalse(pm.isNull())
        w.hide()


if __name__ == "__main__":
    unittest.main()
