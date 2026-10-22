# -*- coding: utf-8 -*-
"""
tests/test_revoke_msg_bytes_guard.py — 补齐 `services/collectors/store.py::is_revokemsg`
的 **bytes / bytearray 分支**覆盖（该分支此前全仓覆盖为 0）。

被测生产代码（现行 5,552 B，SHA256 3B4C60F7…AE74B22，逐行核对于 :14-:28）：

    :14  def is_revokemsg(content: Union[str, bytes]) -> bool:
    :24      if not content:                    # 空值早退（"" / b"" / bytearray()）
    :25          return False
    :26      if isinstance(content, (bytes, bytearray)):
    :27          return b"revokemsg" in bytes(content).lower()
    :28      return "revokemsg" in content.lower()

**关于"真实入参"的两点如实说明**（不粉饰）：

1. `:26-27` 的判定逻辑是「**大小为写不敏感的 substring 命中**」——
   即在字节流里找 ASCII 序列 `b"revokemsg"`。**它不做 zstd 解压，也不认 zstd 魔数**。
   因此"命中撤回标记的 bytes"= 字节流中**明文**含 `revokemsg` 的 bytes；
   本文件用真实微信撤回 sysmsg XML 的 UTF-8 编码作为真值。
2. 上游提示的「zstd 魔数/前缀」确有实据：既有用例
   `tests/test_withdraw_and_metrics.py:200` 的样本 repr 为
   `b'(\\xb5/\\xfd <sysmsg type="revokemsg">...'`，其前缀 `0x28 0xB5 0x2F 0xFD`
   正是 **zstd 魔数**（小端 0xFD2FB528）。故本文件额外构造了
   「zstd 魔数前缀 + 明文撤回标记」的真实形貌（判 True），
   并如实标注反例：**纯 zstd 压缩帧（无明文标记）判 False** ——
   这是现行生产逻辑的边界，不是本文件要改变的行为（本轮不改产品代码）。

本文件只新增测试，不改任何产品代码。
"""
from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.collectors import store  # noqa: E402  （按模块属性调用，便于变异反证打桩）

# --------------------------------------------------------------------------- #
# 真实取值（全部为真实形貌，非占位符）
# --------------------------------------------------------------------------- #
# 真实微信撤回 sysmsg XML（撤回通知的 XML 结构）
_REVOKE_XML_STR = ('<sysmsg type="revokemsg"><revokemsg>'
                   '<session>wxid_abcdefg</session><newmsgid>1234567890</newmsgid>'
                   '</revokemsg></sysmsg>')

# 真实**非**撤回 sysmsg XML（同一询的另一种系统消息，用于假值侧）
_NON_REVOKE_XML_STR = ('<sysmsg type="delchatroommember"><delchatroommember>'
                       '<text><![CDATA[你被移出了群聊]]></text>'
                       '</delchatroommember></sysmsg>')

# zstd 魔数（小端 0xFD2FB528）—— 与 tests/test_withdraw_and_metrics.py:200 样本前缀一致
_ZSTD_MAGIC = bytes([0x28, 0xB5, 0x2F, 0xFD])


class TestBytesTruth:
    """bytes/bytearray 分支（:26-27）—— 真值侧。"""

    def test_real_revoke_xml_as_bytes_returns_true(self):
        """真实微信撤回 sysmsg XML 的 UTF-8 字节流 -> True。"""
        payload = _REVOKE_XML_STR.encode("utf-8")
        assert isinstance(payload, bytes)
        assert store.is_revokemsg(payload) is True

    def test_zstd_magic_prefix_plus_marker_returns_true(self):
        """真实形貌：zstd 魔数前缀 + 明文撤回标记 -> True（:27 的 substring 命中）。"""
        payload = _ZSTD_MAGIC + b"\x00\x01\x02" + _REVOKE_XML_STR.encode("utf-8")
        assert payload[:4] == _ZSTD_MAGIC
        assert store.is_revokemsg(payload) is True

    def test_marker_match_is_case_insensitive_in_bytes(self):
        """大小写不敏感：`REVOKEMSG` 的字节流同样命中（:27 的 .lower()）。"""
        assert store.is_revokemsg(b'<SYSMSG TYPE="REVOKEMSG"/>') is True

    def test_bytearray_with_marker_returns_true(self):
        """bytearray 也走 :26-27 分支（isinstance 覆盖 bytearray）。"""
        payload = bytearray(_REVOKE_XML_STR.encode("utf-8"))
        assert store.is_revokemsg(payload) is True


class TestBytesFalse:
    """bytes/bytearray 分支（:24-25 / :26-27）—— 假值侧。"""

    def test_empty_bytes_returns_false(self):
        """b"" 走 :24-25 空值早退。"""
        assert store.is_revokemsg(b"") is False

    def test_plain_bytes_returns_false(self):
        assert store.is_revokemsg(b"\x00\x01\x02\x03binary blob no marker") is False

    def test_near_miss_revoke_returns_false(self):
        """近似词 `revoke`（少 3 个字母）不命中 -> False。"""
        assert store.is_revokemsg(b"revoke") is False

    def test_real_non_revoke_sysmsg_bytes_returns_false(self):
        """真实**非**撤回 sysmsg XML 的字节流 -> False（区分度证据）。"""
        payload = _NON_REVOKE_XML_STR.encode("utf-8")
        assert b"revokemsg" not in payload.lower()
        assert store.is_revokemsg(payload) is False

    def test_zstd_frame_without_cleartext_marker_returns_false(self):
        """边界（如实记录）：纯 zstd 压缩帧、无明文标记 -> False。

        现行生产逻辑不做解压；此用例把该边界钉住，避免被误读成"应该判 True"。
        """
        payload = _ZSTD_MAGIC + b"\x1f\x0a\x00\x99\x5c\x33\x52\x91"
        assert store.is_revokemsg(payload) is False

    def test_empty_bytearray_returns_false(self):
        """bytearray() 亦为 falsy -> :24-25。"""
        assert store.is_revokemsg(bytearray()) is False

    def test_plain_bytearray_returns_false(self):
        assert store.is_revokemsg(bytearray(b"plain bytes, nothing special")) is False


class TestStrRegression:
    """str 分支（:28）回归 —— 取值与既有 tests/test_withdraw_and_metrics.py 不重复。"""

    def test_uppercase_marker_str_returns_true(self):
        assert store.is_revokemsg('<SYSMSG TYPE="REVOKEMSG"/>') is True

    def test_minimal_marker_str_returns_true(self):
        assert store.is_revokemsg("xx revokemsg yy") is True

    def test_chinese_plaintext_withdraw_notice_returns_false(self):
        """中文纯文本提示（无 XML）不算撤回 —— 与 :17-19 的口径一致。"""
        assert store.is_revokemsg("对方撤回了一条消息") is False

    def test_non_revoke_sysmsg_str_returns_false(self):
        assert store.is_revokemsg(_NON_REVOKE_XML_STR) is False

    def test_near_miss_revoke_str_returns_false(self):
        assert store.is_revokemsg("revoke") is False


class TestNotVacuous:
    """非恒真：把判定钉成"必定翻转"的对照，以及 str/bytes 同口径。"""

    def test_paired_control_marker_removed_flips_verdict(self):
        """负控：同一 payload，**只**把标记替换掉 -> 判定必须翻转。

        若 :27 恒返回 True（或恒 False），本用例必失败。
        """
        payload = _ZSTD_MAGIC + _REVOKE_XML_STR.encode("utf-8")
        stripped = payload.replace(b"revokemsg", b"xxxxxxxxx")
        assert b"revokemsg" in payload.lower()
        assert b"revokemsg" not in stripped.lower()
        assert store.is_revokemsg(payload) is True
        assert store.is_revokemsg(stripped) is False

    def test_str_and_bytes_get_same_verdict_for_same_content(self):
        """同口径：同一内容以 str / 字节流两种形态给出，结论必须一致。"""
        assert store.is_revokemsg(_REVOKE_XML_STR) is True
        assert store.is_revokemsg(_REVOKE_XML_STR.encode("utf-8")) is True
        assert store.is_revokemsg(_NON_REVOKE_XML_STR) is False
        assert store.is_revokemsg(_NON_REVOKE_XML_STR.encode("utf-8")) is False


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
