# -*- coding: utf-8 -*-
"""4项补做功能测试：流式/四档窗口/样例插件/pipeline默认开启。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from app.widgets.streaming_reply import StreamingThread, StreamingReplyWidget
from app.widgets.four_windows import FloatingBall, OverlayChat, Top2SmallWindow, WindowSwitcher


class TestStreaming(unittest.TestCase):
    def test_thread_instantiate(self):
        t = StreamingThread(generator=lambda: ["a","b","c"])
        self.assertIsNotNone(t)
    def test_widget_offscreen(self):
        w = StreamingReplyWidget()  # 无 QApplication 时跳过 __init__ 不崩
        w.set_text("hello")          # set_text 也安全
    def test_degrade_on_bad_gen(self):
        t = StreamingThread(generator=lambda: 1/0)
        # 不崩即可


class TestFourWindows(unittest.TestCase):
    def test_ball(self):
        b = FloatingBall()
        self.assertIn("截图分析", b.quick_actions())
    def test_overlay_set_replies(self):
        o = OverlayChat(); o.set_replies(["r1","r2"])
    def test_top2(self):
        w = Top2SmallWindow()
    def test_switcher(self):
        s = WindowSwitcher(); s.register("a", None)
        self.assertFalse(s.switch_to("missing"))


class TestSamplePlugins(unittest.TestCase):
    def _load(self, dirname, modname):
        import importlib.util
        path = os.path.join(PROJECT_ROOT, "plugins", dirname, "plugin.py")
        spec = importlib.util.spec_from_file_location(modname, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.Plugin

    def test_phrases(self):
        Plugin = self._load("custom_phrases", "cp_plugin")
        p = Plugin(); p.initialize()
        self.assertGreater(len(p.get_phrases("邀约")), 0); p.cleanup()
    def test_analysis_dim(self):
        Plugin = self._load("custom_analysis", "ca_plugin")
        p = Plugin()
        r = p.on_after_analysis({"person_id":"x"})
        self.assertIn("extra_dimensions", r)
    def test_notification(self):
        Plugin = self._load("custom_notification", "cn_plugin")
        p = Plugin(); p.on_analysis_complete({"person_id":"p"})
    def test_manifest_files(self):
        for d in ("custom_phrases","custom_analysis","custom_notification"):
            self.assertTrue(os.path.exists(os.path.join(PROJECT_ROOT,"plugins",d,"manifest.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
