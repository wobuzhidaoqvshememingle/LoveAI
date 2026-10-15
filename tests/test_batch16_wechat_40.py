# -*- coding: utf-8 -*-
"""微信40种方法+一键自动导入测试（30项）。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.wechat_auto_importer import WechatAutoImporter
from services.wechat_cache_reader import WechatCacheReader
from services.wechat_system_reader import WechatSystemReader
from services.wechat_mobile_reader import WechatMobileReader
from services.wechat_advanced_reader import WechatAdvancedReader, F_METHODS
from services.wechat_importer import WechatDBExtra
from services.wechat_vision_monitor import WechatVisionExtra
from services.wechat_access_manager import METHODS_40, get_all_methods, get_methods_by_category


class _Mock:
    def __init__(self, avail=True, msgs=None):
        self._avail = avail
        self._msgs = msgs or []
    def availability_check(self):
        return {"available": self._avail, "reason": ""}
    def import_chats(self):
        return self._msgs


class TestAutoImporter(unittest.TestCase):
    def test_register(self):
        ai = WechatAutoImporter()
        ai.register_method("A01", _Mock(msgs=[{"t":1}]), "A", 1)
        r = ai.auto_import("p1")
        self.assertEqual(r["total"], 1)
    def test_skip_unavailable(self):
        ai = WechatAutoImporter()
        ai.register_method("A01", _Mock(avail=False), "A")
        r = ai.auto_import("p1")
        self.assertEqual(r["total"], 0)
    def test_dedupe(self):
        ai = WechatAutoImporter()
        ai.register_method("A01", _Mock(msgs=[{"x":1},{"x":1},{"x":2}]), "A")
        r = ai.auto_import("p1")
        self.assertEqual(r["total"], 2)
    def test_progress(self):
        ai = WechatAutoImporter()
        ai.register_method("A01", _Mock(), "A")
        ai.auto_import("p1")
        p = ai.get_progress()
        self.assertEqual(p["tried"], 1)
    def test_log(self):
        ai = WechatAutoImporter()
        ai.register_method("A01", _Mock(msgs=[{"x":1}]), "A")
        ai.auto_import("p1")
        self.assertEqual(len(ai.get_log()), 1)
    def test_cancel(self):
        ai = WechatAutoImporter()
        ai.register_method("A01", _Mock(), "A")
        ai.cancel()
        ai.auto_import("p1")
        self.assertEqual(len(ai.get_log()), 0)


class TestClassA(unittest.TestCase):
    def test_wal(self):
        self.assertEqual(WechatDBExtra().read_wal("x"), [])
    def test_temp(self):
        self.assertEqual(WechatDBExtra().read_temp_files(), [])
    def test_registry(self):
        self.assertEqual(WechatDBExtra().read_registry(), {})
    def test_crash_dump(self):
        self.assertEqual(WechatDBExtra().analyze_crash_dump("x")["keys"], [])


class TestClassB(unittest.TestCase):
    def test_record(self):
        self.assertEqual(WechatVisionExtra().record_screen(), b"")
    def test_ui_auto(self):
        self.assertEqual(WechatVisionExtra().read_ui_automation_tree(), {})
    def test_notification(self):
        self.assertEqual(WechatVisionExtra().grab_notifications(), [])
    def test_cef(self):
        self.assertEqual(WechatVisionExtra().read_cef_debug(), {})


class TestClassC(unittest.TestCase):
    def test_images(self):
        self.assertEqual(WechatCacheReader(root="nonexistent").list_images(), [])
    def test_emoticon(self):
        self.assertEqual(WechatCacheReader(root="nonexistent").list_emoticons(), [])
    def test_mini_app(self):
        self.assertEqual(WechatCacheReader(root="nonexistent").list_mini_app(), [])
    def test_import(self):
        r = WechatCacheReader(root="nonexistent").import_chats()
        self.assertEqual(r[0]["images"], 0)


class TestClassD(unittest.TestCase):
    def test_clip_history(self):
        self.assertEqual(WechatSystemReader().read_clipboard_history(), [])
    def test_ime(self):
        self.assertEqual(WechatSystemReader().read_ime_user_dict(), [])
    def test_dns(self):
        self.assertIsInstance(WechatSystemReader().read_dns_cache(), str)
    def test_favorites(self):
        self.assertEqual(WechatSystemReader().read_favorites(), [])


class TestClassE(unittest.TestCase):
    def test_unavailable(self):
        self.assertFalse(WechatMobileReader().availability_check()["available"])
    def test_backup(self):
        self.assertEqual(WechatMobileReader().read_backup(), [])
    def test_screencast(self):
        self.assertEqual(WechatMobileReader().read_screencast_ocr(), [])


class TestClassF(unittest.TestCase):
    def test_default_disabled(self):
        f = WechatAdvancedReader()
        self.assertFalse(f.availability_check()["available"])
    def test_enable(self):
        f = WechatAdvancedReader()
        f.enable()
        self.assertTrue(f.availability_check()["available"])
    def test_list_methods(self):
        self.assertEqual(len(WechatAdvancedReader().list_methods()), 5)


class TestAccess40(unittest.TestCase):
    def test_40_count(self):
        self.assertEqual(len(METHODS_40), 40)
    def test_priority_cats(self):
        cats = set(m["cat"] for m in METHODS_40)
        self.assertEqual(cats, {"A","B","C","D","E","F"})
    def test_filter_cat(self):
        self.assertEqual(len(get_methods_by_category("A")), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
