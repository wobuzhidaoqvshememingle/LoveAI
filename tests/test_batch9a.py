# -*- coding: utf-8 -*-
"""第八批剩余第一批：快捷面板/主题/插件/流式/多平台导入。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch9_ui import (get_quick_panel_data, ThemeManager,
    PluginManager, stream_generate, import_chats)


class TestBatch9A(unittest.TestCase):
    def test_quick_panel(self):
        self.assertEqual(get_quick_panel_data([], {})["recent_tasks"], [])

    def test_theme(self):
        tm = ThemeManager(); tm.set("护眼粉","大")
        self.assertEqual(tm.info()["theme"], "护眼粉")

    def test_plugin(self):
        pm = PluginManager(); pm.load("x", {}); pm.disable("x")
        self.assertEqual(pm.active(), [])

    def test_stream(self):
        self.assertEqual("".join(stream_generate("ab")), "ab")

    def test_import(self):
        r = import_chats("qq", "2024-01-01|me|你好\n2024-01-01|her|在吗")
        self.assertEqual(r["imported"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
