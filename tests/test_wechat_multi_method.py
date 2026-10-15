# -*- coding: utf-8 -*-
"""
微信多方法导出体系测试（全部 mock，不依赖真实微信）
"""
from __future__ import annotations

import os
import struct
import hashlib
import hmac as hmac_mod
import tempfile
import threading
import time

import pytest


# =====================================================================
# 解密器测试
# =====================================================================
class TestDecryptor:
    def test_detect_encryption_type_plain(self, tmp_path):
        from services.wechat_db_decryptor import detect_encryption_type
        p = tmp_path / "plain.db"
        p.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
        assert detect_encryption_type(str(p)) == "plain"

    def test_detect_encryption_type_sqlcipher4(self, tmp_path):
        from services.wechat_db_decryptor import detect_encryption_type
        p = tmp_path / "enc.db"
        p.write_bytes(os.urandom(4096))  # 随机头，不是 SQLite
        assert detect_encryption_type(str(p)) in ("sqlcipher4", "wxsqlite3")

    def test_passphrase_roundtrip(self, tmp_path):
        """构造一个 SQLCipher4 加密页，用 passphrase 模式 verify+decrypt。"""
        from services.wechat_db_decryptor import (
            SQLCIPHER4_PAGE_SIZE, SQLCIPHER4_RESERVE,
        )
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        passphrase = os.urandom(32)
        salt = os.urandom(16)
        page_size = SQLCIPHER4_PAGE_SIZE
        reserve = SQLCIPHER4_RESERVE

        # 派生 enc_key 和 mac_key
        enc_key = hashlib.pbkdf2_hmac("sha512", passphrase, salt, 256000, 32)
        mac_salt = bytes(b ^ 0x3a for b in salt)
        mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, 32)

        # 第1页明文：SQLite 头(16) + 随机内容(4000-16=3984) = 4000 字节
        plain = b"SQLite format 3\x00" + os.urandom(page_size - 16 - reserve - 16)
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES256(enc_key), modes.CBC(iv))
        enc = cipher.encryptor()
        enc_body = enc.update(plain) + enc.finalize()
        # 标准 SQLCipher4：HMAC 覆盖 ciphertext + IV + 小端页号（篡改 IV 也会失配）
        hmac_input = enc_body + iv + struct.pack("<I", 1)
        h = hmac_mod.new(mac_key, hmac_input, hashlib.sha512).digest()
        page1 = salt + enc_body + iv + h
        assert len(page1) == page_size

        db_path = tmp_path / "t.db"
        # 写一个最小合法 db：只写第1页
        db_path.write_bytes(page1)

        from services.wechat_db_decryptor import verify_key_sqlcipher4
        assert verify_key_sqlcipher4(str(db_path), passphrase, mode="passphrase") is True

        # 错误密钥应失败
        assert verify_key_sqlcipher4(str(db_path), os.urandom(32), mode="passphrase") is False


# =====================================================================
# 密钥提取器测试
# =====================================================================
class TestKeyExtractor:
    def test_machine_fingerprint_stable(self):
        from services.wechat_key_extractor import _machine_fingerprint
        fp1 = _machine_fingerprint()
        fp2 = _machine_fingerprint()
        assert fp1 == fp2
        assert len(fp1) >= 16

    def test_encrypt_decrypt_blob(self):
        from services.wechat_key_extractor import _encrypt_blob, _decrypt_blob
        msg = '{"key": "abc"}'.encode("utf-8")
        enc = _encrypt_blob(msg)
        assert enc != msg
        dec = _decrypt_blob(enc)
        assert dec == msg

    def test_detect_wechat_process_returns_dict_or_none(self):
        from services.wechat_key_extractor import detect_wechat_process
        # 不依赖真实进程：只验证不抛异常，返回 None 或 dict
        result = detect_wechat_process()
        assert result is None or isinstance(result, dict)

    def test_memory_reader_class_exists(self):
        from services.wechat_key_extractor import MemoryReader
        assert hasattr(MemoryReader, "__init__")
        assert hasattr(MemoryReader, "iter_readable_regions")
        assert hasattr(MemoryReader, "close")


# =====================================================================
# 数据库读取器测试
# =====================================================================
class TestDBReader:
    def test_merge_accounts(self):
        from services.wechat_db_reader import merge_accounts
        a = [{"msg_id": 1, "create_time": 100, "content": "a"},
             {"msg_id": 3, "create_time": 300, "content": "c"}]
        b = [{"msg_id": 2, "create_time": 200, "content": "b"},
             {"msg_id": 4, "create_time": 400, "content": "d"}]
        merged = merge_accounts([
            {"account": "wxid_a", "messages": a},
            {"account": "wxid_b", "messages": b},
        ])
        times = [m["create_time"] for m in merged]
        assert times == sorted(times)
        assert len(merged) == 4

    def test_msg_type_names(self):
        from services.wechat_db_reader import MSG_TYPE_NAMES
        assert MSG_TYPE_NAMES.get(1) == "text"
        assert MSG_TYPE_NAMES.get(3) == "image"
        assert MSG_TYPE_NAMES.get(34) == "voice"
        assert MSG_TYPE_NAMES.get(43) == "video"

    def test_reader_init_missing_db_dir(self):
        from services.wechat_db_reader import WeChatDBReader
        r = WeChatDBReader(db_dir=r"C:\nonexistent_wxid_dir_xyz",
                            key="00" * 32, version="4.x")
        assert r is not None
        # 读取应返回空迭代器，不抛异常
        msgs = list(r.read_messages())
        assert msgs == []


# =====================================================================
# 实时监听器测试
# =====================================================================
class TestRealtimeMonitor:
    def test_start_stop_polling(self):
        from services.wechat_realtime_monitor import WeChatRealtimeMonitor
        msgs = []
        mon = WeChatRealtimeMonitor(reader=None,
                                     on_new_messages=lambda m: msgs.extend(m))
        mon.start(mode="polling", interval=0.1)
        assert mon.is_running() is True
        time.sleep(0.3)
        mon.stop()
        assert mon.is_running() is False
        # reader=None 时不会有消息
        assert msgs == []

    def test_callback_triggered(self):
        from services.wechat_realtime_monitor import WeChatRealtimeMonitor

        class FakeReader:
            def __init__(self):
                self.called = 0

            def read_messages(self, start_time=None):
                self.called += 1
                if self.called >= 2:
                    return [{"msg_id": 1, "create_time": 100, "content": "hi"}]
                return []

        reader = FakeReader()
        received = []
        mon = WeChatRealtimeMonitor(reader=reader,
                                     on_new_messages=lambda m: received.extend(m))
        mon.start(mode="polling", interval=0.1)
        time.sleep(0.6)
        mon.stop()
        assert len(received) >= 1
        assert received[0]["content"] == "hi"


# =====================================================================
# 自动导入器测试
# =====================================================================
class TestAutoImporter:
    def _make_legacy_method(self, available=True, msgs=None, raises=None):
        class M:
            def availability_check(self):
                return {"available": available}

            def import_chats(self):
                if raises:
                    raise raises
                return msgs or []
        return M()

    def test_register_and_auto_import_legacy(self):
        from services.wechat_auto_importer import WechatAutoImporter
        imp = WechatAutoImporter()
        imp.register_method("A1", self._make_legacy_method(
            msgs=[{"msg_id": 1, "content": "hello", "create_time": 100,
                   "talker_id": "wxid_1"}]))
        result = imp.auto_import(person_id="p1")
        assert result["total"] == 1
        assert result["used"][0]["method"] == "A1"
        assert result["used"][0]["count"] == 1

    def test_dedup(self):
        from services.wechat_auto_importer import WechatAutoImporter
        imp = WechatAutoImporter()
        imp.register_method("A1", self._make_legacy_method(
            msgs=[{"msg_id": 1, "content": "x", "create_time": 100,
                   "talker_id": "w1"}]))
        imp.register_method("A2", self._make_legacy_method(
            msgs=[{"msg_id": 1, "content": "x", "create_time": 100,
                   "talker_id": "w1"},
                  {"msg_id": 2, "content": "y", "create_time": 200,
                   "talker_id": "w1"}]))
        result = imp.auto_import(person_id="p1")
        assert result["total"] == 2  # 重复 msg_id=1 只算一次

    def test_f_category_skipped_without_advanced(self):
        from services.wechat_auto_importer import WechatAutoImporter
        imp = WechatAutoImporter()
        imp.register_method("F1", self._make_legacy_method(available=True,
                                                            msgs=[{"msg_id": 9, "content": "z", "create_time": 1, "talker_id": "w"}]),
                             category="F")
        result = imp.auto_import(person_id="p1")
        assert result["total"] == 0
        assert "F1" in result["skipped"]

    def test_cancel(self):
        from services.wechat_auto_importer import WechatAutoImporter
        imp = WechatAutoImporter()
        imp.register_method("A1", self._make_legacy_method())
        imp.cancel_import()
        result = imp.auto_import(person_id="p1")
        # 取消后 legacy 方法直接被跳过（因为 _cancel=True）
        assert result["total"] == 0

    def test_progress_dict(self):
        from services.wechat_auto_importer import WechatAutoImporter
        imp = WechatAutoImporter()
        p = imp.get_progress()
        assert "tried" in p and "total" in p and "success" in p
        assert "percentage" in p and "message_count" in p

    def test_get_log_and_result(self):
        from services.wechat_auto_importer import WechatAutoImporter
        imp = WechatAutoImporter()
        result = imp.auto_import(person_id="p1")
        log = imp.get_log()
        assert isinstance(log, list)
        assert imp.get_result() is result

    def test_incremental_import(self):
        from services.wechat_auto_importer import WechatAutoImporter
        imp = WechatAutoImporter()
        result = imp.incremental_import()
        assert "messages" in result


# =====================================================================
# 事件总线集成（如果项目里有 event_bus）
# =====================================================================
class TestEventBusIntegration:
    def test_event_bus_publish_does_not_crash(self):
        """验证 importer 接受 event_bus 但不依赖它的具体实现。"""
        from services.wechat_auto_importer import WechatAutoImporter

        class FakeBus:
            def __init__(self):
                self.events = []

            def publish(self, name, payload):
                self.events.append((name, payload))

        bus = FakeBus()
        imp = WechatAutoImporter()
        imp.set_event_bus(bus)
        imp.register_method("A1", TestAutoImporter._make_legacy_method(
            self=None, msgs=[{"msg_id": 1, "content": "x", "create_time": 1, "talker_id": "w"}]))
        imp.auto_import(person_id="p1")
        # 至少发布了 wechat_import_complete
        names = [e[0] for e in bus.events]
        assert "wechat_import_complete" in names
