# -*- coding: utf-8 -*-
"""共享密钥验密 oracle 与内存/转储镜像候选扫描（P2/P3/P4/P5/P9 复用）。

核心：
- make_key_oracle(db_path)：返回"32 字节候选 -> 是否正确"的验密函数（只读首页 HMAC）。
- scan_blob_for_key(blob, oracle)：在一块内存里按对齐步长枚举高熵 32B 候选并验密。
- scan_file_for_key(path, db_path)：对大型 .dmp/镜像分块流式扫描（跨块重叠）。
- scan_sqlkey_strings(blob)：识别 `x'<64hex><32hex>'` 明文密钥串（旧 4.0-4.1）。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Callable, Iterator, Optional

from . import env

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 验密 oracle
# ---------------------------------------------------------------------------

def make_key_oracle(db_path: str, encryption_type: Optional[str] = None) -> Callable[[bytes], bool]:
    """构造候选验密函数（auto：先 raw 直验，再 passphrase）。正确返回 True。"""
    raw = make_raw_key_oracle(db_path, encryption_type)
    pw = make_passphrase_oracle(db_path, encryption_type)

    def oracle(cand: bytes) -> bool:
        return raw(cand) or pw(cand)
    return oracle


def _page_materials(db_path: str, page_size: int = 4096) -> dict:
    """只读一次首页并预计算两种布局的验密材料（避免每候选重复开文件）。"""
    import struct
    with open(db_path, "rb") as f:
        page = f.read(page_size)
    salt = page[:16]
    # SQLCipher4（reserve 80）
    sc_stored = page[-64:]
    sc_hmac_input = page[16:4032] + struct.pack("<I", 1)
    # wxsqlite（reserve 48）
    wx_reserve = 48
    wx_iv = page[page_size - wx_reserve: page_size - wx_reserve + 16]
    wx_body = page[16: page_size - wx_reserve]
    return {"salt": salt, "sc_stored": sc_stored, "sc_hmac_input": sc_hmac_input,
            "wx_iv": wx_iv, "wx_body": wx_body, "wx_reserve": wx_reserve}


def _sc_raw_verify(cand: bytes, m: dict) -> bool:
    import hashlib, hmac as _hmac
    mac_salt = bytes(b ^ 0x3a for b in m["salt"])
    mk = hashlib.pbkdf2_hmac("sha512", cand, mac_salt, 2, 32)
    return _hmac.new(mk, m["sc_hmac_input"], hashlib.sha512).digest() == m["sc_stored"]


def _wx_raw_verify(cand: bytes, m: dict) -> bool:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from services.wechat_db_decryptor import _wxsqlite_body_looks_valid
    dec = Cipher(algorithms.AES(cand), modes.CBC(m["wx_iv"])).decryptor()
    plain = dec.update(m["wx_body"]) + dec.finalize()
    return _wxsqlite_body_looks_valid(plain, m["wx_reserve"])


def make_raw_key_oracle(db_path: str,
                        encryption_type: Optional[str] = None) -> Callable[[bytes], bool]:
    """廉价内存级验密（页材料只读一次）：候选直接当 enc_key。

    覆盖 raw-key 常驻(4.x) 与 3.x；无 256k PBKDF2。
    """
    m = _page_materials(db_path)
    et = encryption_type
    def oracle(cand: bytes) -> bool:
        if len(cand) != 32:
            return False
        try:
            if et == "wxsqlite3":
                return _wx_raw_verify(cand, m)
            if et == "sqlcipher4":
                return _sc_raw_verify(cand, m)
            return _sc_raw_verify(cand, m) or _wx_raw_verify(cand, m)
        except Exception:
            return False
    return oracle


def make_passphrase_oracle(db_path: str,
                           encryption_type: Optional[str] = None) -> Callable[[bytes], bool]:
    """昂贵验密：候选是 passphrase，PBKDF2(256k) 派生 enc_key 后再校验 HMAC。"""
    import hashlib, hmac as _hmac
    m = _page_materials(db_path)
    def oracle(cand: bytes) -> bool:
        if len(cand) != 32:
            return False
        try:
            enc_key = hashlib.pbkdf2_hmac("sha512", cand, m["salt"], 256000, 32)
            return _sc_raw_verify(enc_key, m)
        except Exception:
            return False
    return oracle


# ---------------------------------------------------------------------------
# 候选枚举（复用 key_extractor）
# ---------------------------------------------------------------------------

def iter_candidates(blob: bytes, step: int = 4) -> Iterator[bytes]:
    from services.wechat_key_extractor import _iter_candidates
    return _iter_candidates(blob, step=step)


def scan_blob_for_key(blob: bytes,
                      oracle: Callable[[bytes], bool],
                      step: int = 4,
                      max_candidates: Optional[int] = None) -> Optional[bytes]:
    tested = 0
    for cand in iter_candidates(blob, step=step):
        if max_candidates is not None and tested >= max_candidates:
            break
        tested += 1
        if oracle(cand):
            return cand
    return None


# ---------------------------------------------------------------------------
# 大文件流式扫描
# ---------------------------------------------------------------------------

def scan_file_for_key(file_path: str,
                      db_path: str,
                      encryption_type: Optional[str] = None,
                      chunk_size: int = 64 * 1024 * 1024,   # 64 MB
                      overlap: int = 64,
                      step: int = 4,
                      max_bytes: Optional[int] = None,
                      try_passphrase: bool = False,
                      max_passphrase_candidates: int = 2000) -> Optional[bytes]:
    """对大型转储/镜像分块扫描，块间留 overlap 防止候选跨块漏掉。

    顺序（廉价→昂贵）：
      1) raw key 直验（快）；2) 明文 x'hex' 串；
      3) 仅当 try_passphrase，受限数量地做 256k passphrase 验证。
    Returns: 命中的 32 字节 key，否则 None。
    """
    raw_oracle = make_raw_key_oracle(db_path, encryption_type)
    total = 0
    pw_budget = max_passphrase_candidates
    with open(file_path, "rb") as f:
        carry = b""
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            buf = carry + chunk
            # 1) raw 直验
            found = scan_blob_for_key(buf, raw_oracle, step=step)
            if found:
                return found
            # 2) 明文 x'hex' 串（ASCII / UTF-16）
            hit = scan_sqlkey_strings(buf) or scan_sqlkey_strings_utf16(buf)
            if hit and raw_oracle(hit):
                return hit
            # 3) 受限 passphrase 验证
            if try_passphrase and pw_budget > 0:
                pw_oracle = make_passphrase_oracle(db_path, encryption_type)
                found_pw = scan_blob_for_key(
                    buf, pw_oracle, step=step, max_candidates=pw_budget)
                if found_pw:
                    return found_pw
                pw_budget -= min(pw_budget, max_passphrase_candidates)
            carry = buf[-overlap:] if overlap else b""
            if max_bytes and total >= max_bytes:
                break
    return None


# ---------------------------------------------------------------------------
# 明文 SQLCipher key 字符串（旧 4.0-4.1 在内存里以 x'..' 存在）
# ---------------------------------------------------------------------------

_ASCII_SQLKEY = re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})?'")
_ASCII_SQLKEY_64 = re.compile(rb"x'([0-9a-fA-F]{64})'")


def scan_sqlkey_strings(blob: bytes) -> Optional[bytes]:
    """从字节块里找 x'<64hex>' 形式密钥（ASCII）；返回 32B 或 None。"""
    m = _ASCII_SQLKEY.search(blob) or _ASCII_SQLKEY_64.search(blob)
    if not m:
        return None
    try:
        return bytes.fromhex(m.group(1).decode("ascii"))
    except Exception:
        return None


def scan_sqlkey_strings_utf16(blob: bytes) -> Optional[bytes]:
    """UTF-16LE 形式 x'...'（部分版本）。"""
    try:
        text = blob.decode("utf-16le", errors="ignore")
    except Exception:
        return None
    m = re.search(r"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})?'", text)
    if not m:
        return None
    try:
        return bytes.fromhex(m.group(1))
    except Exception:
        return None
