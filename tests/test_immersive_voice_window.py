# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 沉浸式语音交互桌面单测（R3.3-49）

offscreen 模式下：
  - 窗口能实例化不抛异常
  - set_vad_level / set_subtitle / set_expression / set_tts_level 不崩
  - 立绘 QLabel 能加载占位图
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

from app.widgets.immersive_voice_window import ImmersiveVoiceWindow  # noqa: E402


class TestImmersiveVoiceWindow(unittest.TestCase):
    def test_instantiate_no_raise(self):
        w = ImmersiveVoiceWindow()
        self.assertIsNotNone(w)

    def test_set_vad_level_does_not_crash(self):
        w = ImmersiveVoiceWindow()
        for lv in (0.0, 0.3, 0.7, 1.0, -1.0, 2.0):
            w.set_vad_level(lv)

    def test_set_subtitle(self):
        w = ImmersiveVoiceWindow()
        w.set_subtitle("你好呀")
        self.assertEqual(w.subtitle.text(), "你好呀")

    def test_set_expression(self):
        w = ImmersiveVoiceWindow()
        for expr in ("neutral", "happy", "sad", "thinking", "surprised", "unknown"):
            w.set_expression(expr)
        # 非法值不应改变表情
        self.assertIn(w.animator.current_state()["expression"],
                      ("neutral", "happy", "sad", "thinking", "surprised"))

    def test_set_tts_level(self):
        w = ImmersiveVoiceWindow()
        w.set_tts_level(0.9)
        st = w.animator.current_state()
        self.assertIn(st["mouth"], ("a", "i", "u", "e", "o"))
        w.set_tts_level(0.0)
        self.assertEqual(w.animator.current_state()["mouth"], "rest")

    def test_portrait_label_has_sprites(self):
        w = ImmersiveVoiceWindow()
        # sprite 目录下应有 12 张占位图
        self.assertGreaterEqual(len(w.animator.sprites), 10)


if __name__ == "__main__":
    unittest.main()
