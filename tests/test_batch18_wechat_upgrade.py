# -*- coding: utf-8 -*-
"""微信接入开源借鉴升级测试（15项）。"""
import os, sys, unittest, tempfile
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from tools.wechat_db_decryptor import WechatDbDecryptor
from services.wechat_cache_reader import detect_image_format, parse_wxgf, WechatCacheReader
from services.wechat_importer import WechatRealtimeMonitor
from services.wechat_config_parser import get_account_info, get_avatar, get_all_accounts


class TestKeyExtraction(unittest.TestCase):
    def test_pattern_regex_exists(self):
        d = WechatDbDecryptor()
        # 用错误pid应返回None，不崩溃
        self.assertIsNone(d.search_key_in_memory(999999))
    def test_pipeline_no_pid(self):
        d = WechatDbDecryptor()
        self.assertFalse(d.run_full_pipeline()["ok"])
    def test_cached_key(self):
        d = WechatDbDecryptor()
        d._cached_key = b"0" * 32
        self.assertEqual(d.search_key_in_memory(1), b"0" * 32)


class TestMultiDB(unittest.TestCase):
    def test_scall_nonexistent(self):
        d = WechatDbDecryptor()
        self.assertEqual(d.scan_all_databases("nonexistent"), [])
    def test_decrypt_all(self):
        d = WechatDbDecryptor()
        self.assertEqual(d.decrypt_all_databases("nonexistent", b"x"), [])
    def test_read_all_tables(self):
        d = WechatDbDecryptor()
        self.assertEqual(d.read_all_tables({"path": "nonexistent"}), {})


class TestImageV2(unittest.TestCase):
    def test_detect_plain_png(self):
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            path = f.name
        self.assertEqual(detect_image_format(path), "plain")
        os.unlink(path)
    def test_read_dat_missing(self):
        r = WechatCacheReader()
        self.assertEqual(r.read_image_dat("nonexistent"), b"")


class TestWxgf(unittest.TestCase):
    def test_parse_wxgf_format(self):
        with tempfile.NamedTemporaryFile(suffix=".wxgf", delete=False) as f:
            f.write(b"wxgf" + b"\x00" * 0x20 + b"silkdata")
            path = f.name
        out = parse_wxgf(path)
        self.assertTrue(out.endswith(".extracted.amr"))
        os.unlink(path)
        if os.path.exists(out): os.unlink(out)
    def test_parse_non_wxgf(self):
        with tempfile.NamedTemporaryFile(suffix=".amr", delete=False) as f:
            f.write(b"#!AMR")
            path = f.name
        self.assertEqual(parse_wxgf(path), path)
        os.unlink(path)


class TestRealtimeMonitor(unittest.TestCase):
    def test_start_stop(self):
        m = WechatRealtimeMonitor()
        self.assertTrue(m.start_monitoring("x.db"))
        m.stop_monitoring()
    def test_position(self):
        m = WechatRealtimeMonitor()
        m.set_last_position("x.db", 100)
        self.assertEqual(m.get_last_position("x.db"), 100)
    def test_new_messages_missing(self):
        m = WechatRealtimeMonitor()
        self.assertEqual(m.get_new_messages("nonexistent.db"), [])


class TestAccountInfo(unittest.TestCase):
    def test_get_account(self):
        info = get_account_info()
        self.assertIn("wxid", info)
    def test_get_avatar(self):
        self.assertEqual(get_avatar(), "")
    def test_get_all_accounts(self):
        self.assertIsInstance(get_all_accounts(), list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
