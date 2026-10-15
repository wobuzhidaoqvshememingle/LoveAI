# -*- coding: utf-8 -*-
"""第八批剩余第二批：可视化/报告导出/上下文压缩/微信网页框架/登录态。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch9_report import (relationship_visualizer, export_report,
    context_compressor, WechatWebOperator, LoginManager)


class TestBatch9B(unittest.TestCase):
    def test_visualizer(self):
        self.assertTrue(relationship_visualizer({}, [])["html_ready"])

    def test_export(self):
        self.assertEqual(export_report("p","html")["format"], "html")

    def test_compress(self):
        r = context_compressor(["m"]*40, limit=10)
        self.assertTrue(r["compressed"]); self.assertEqual(len(r["messages"]),10)

    def test_wechat_web_framework(self):
        o = WechatWebOperator(); self.assertFalse(o.ready())

    def test_login(self):
        lm = LoginManager(); lm.set("wx","t")
        self.assertTrue(lm.valid("wx"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
