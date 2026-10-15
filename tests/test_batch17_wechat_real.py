# -*- coding: utf-8 -*-
"""微信接入紧急修复：真实实现测试（12项）。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.wechat_vision_monitor import WechatVisionMonitor, _find_wechat_window
from services.wechat_system_reader import WechatSystemReader
from services.wechat_cache_reader import WechatCacheReader, _xor_dat_to_png
from services.wechat_config_parser import WechatConfigParser
from services.wechat_auto_importer import WechatAutoImporter
from services.wechat_access_manager import register_all_methods
from services.wechat_advanced_reader import WechatAdvancedReader


class TestVisionReal(unittest.TestCase):
    def test_find_window_may_return_none(self):
        # offscreen 环境可能没有微信，None 或 tuple
        r = _find_wechat_window()
        self.assertTrue(r is None or isinstance(r, tuple))
    def test_capture_returns_none_when_no_window(self):
        m = WechatVisionMonitor()
        # 无窗口返回 None；本机若有微信窗口则返回 PNG bytes，均不崩溃
        shot = m.capture_wechat_window()
        self.assertTrue(shot is None or isinstance(shot, bytes))
    def test_ocr_empty(self):
        m = WechatVisionMonitor()
        self.assertEqual(m.ocr_image(b""), "")


class TestSystemReal(unittest.TestCase):
    def test_clipboard_read(self):
        s = WechatSystemReader()
        self.assertIsInstance(s.read_clipboard_text(), str)
    def test_dns(self):
        s = WechatSystemReader()
        self.assertIsInstance(s.read_dns_cache(), str)
    def test_notifications_list(self):
        s = WechatSystemReader()
        self.assertIsInstance(s.read_notifications(), list)


class TestCacheReal(unittest.TestCase):
    def test_xor_png(self):
        # 构造 0x63 异或后的伪 PNG
        head = b"\x89PNG\r\n\x1a\n"
        xored = bytes(b ^ 0x63 for b in head)
        self.assertEqual(_xor_dat_to_png(xored)[:8], head)
    def test_availability(self):
        r = WechatCacheReader(root="nonexistent_xxx")
        self.assertFalse(r.availability_check()["available"])


class TestConfigReal(unittest.TestCase):
    def test_find_configs(self):
        p = WechatConfigParser(root="nonexistent_xxx")
        self.assertEqual(p.find_config_files(), [])


class TestRegisterAll(unittest.TestCase):
    def test_register_all(self):
        ai = WechatAutoImporter()
        services = {
            "wechat_db_decryptor": object(),
            "wechat_memory_dumper": object(),
            "wechat_vision_monitor": object(),
            "wechat_cache_reader": object(),
            "wechat_system_reader": object(),
            "wechat_mobile_reader": object(),
            "wechat_advanced_reader": WechatAdvancedReader(),
        }
        n = register_all_methods(ai, services)
        self.assertGreaterEqual(n, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
