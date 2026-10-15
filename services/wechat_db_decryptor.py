# -*- coding: utf-8 -*-
"""
微信数据库解密器（纯 Python 实现，不依赖外部 sqlcipher 命令行）

支持两种加密格式：
- SQLCipher4（微信 4.x，WeChatAppEx）：AES-256-CBC + HMAC-SHA512，每页预留 80 字节
  （IV 16 字节 + HMAC 64 字节）。密钥从内存提取后为 32 字节原始密钥（hex 64 字符），
  盐值为数据库文件头 16 字节（或内存中伴随密钥一起出现的 16 字节）。
- wxSQLite3（微信 3.x，WeChat.exe）：AES-256-CBC，每页预留 48 字节
  （IV 16 字节 + HMAC-SHA256 32 字节）。

优先尝试 pysqlcipher3 / sqlcipher3 绑定；不可用时回退到基于 cryptography 的
逐页解密实现。所有公网方法均不修改原数据库，只输出解密后的明文 .db。

设计原则：
- 只读打开原库（rb），绝不写回；
- 密钥仅在内存中使用，不打印、不落盘；
- 任何异常都抛出明确的 DecryptError，调用方据此给出"密钥错误 / 库损坏 / 依赖缺失"等提示。
"""
from __future__ import annotations

import logging
import os
import struct
import hashlib
import hmac as hmac_mod
from typing import Optional, Tuple, Union

logger = logging.getLogger(__name__)

# ---- 常量 ----
SQLCIPHER4_PAGE_SIZE = 4096
SQLCIPHER4_RESERVE = 80          # IV(16) + HMAC-SHA512(64)
SQLCIPHER4_KDF_ITER = 256000     # 密码模式 PBKDF2 迭代数（微信4.x实测）
SQLCIPHER4_HMAC_KDF_ITER = 2     # HMAC key 派生迭代数
SQLCIPHER4_HMAC_SALT_XOR = 0x3a  # HMAC key 派生用 salt ^ 0x3a
WXSQLITE3_PAGE_SIZE = 4096
WXSQLITE3_RESERVE = 48           # IV(16) + HMAC-SHA256(32)
SQLITE_HEADER = b"SQLite format 3\x00"


class DecryptError(Exception):
    """解密失败的统一异常。"""


# ---------------------------------------------------------------------------
# 依赖探测
# ---------------------------------------------------------------------------
def _try_import_crypto():
    """尝试导入 cryptography 原语；返回 (AES, padding, ok)。"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
        return Cipher, algorithms, modes, True
    except Exception as e:  # noqa: BLE001
        logger.warning("cryptography 不可用：%s；将无法使用纯 Python 解密", e)
        return None, None, None, False


def _try_import_sqlcipher():
    """尝试导入 sqlcipher3 / pysqlcipher3 绑定。"""
    try:
        from sqlcipher3 import dbapi2 as sqlcipher  # type: ignore
        return sqlcipher
    except Exception:
        pass
    try:
        from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore
        return sqlcipher
    except Exception:
        pass
    return None


def _bytes_from_key(key: Union[str, bytes]) -> bytes:
    """把 hex 字符串或 bytes 统一为 32 字节原始密钥。"""
    if isinstance(key, bytes):
        if len(key) == 32:
            return key
        if len(key) == 64:
            try:
                return bytes.fromhex(key.decode("ascii"))
            except Exception as e:  # noqa: BLE001
                raise DecryptError(f"bytes 密钥长度 64 但不是 hex：{e}")
        raise DecryptError(f"密钥字节长度异常：{len(key)}（期望 32 或 64）")
    if isinstance(key, str):
        s = key.strip()
        if s.lower().startswith("x'") and s.endswith("'"):
            s = s[2:-1]
        if len(s) == 64:
            try:
                return bytes.fromhex(s)
            except Exception as e:  # noqa: BLE001
                raise DecryptError(f"hex 密钥非法：{e}")
        raise DecryptError(f"hex 密钥长度异常：{len(s)}（期望 64）")
    raise DecryptError(f"密钥类型不支持：{type(key)!r}")


# ---------------------------------------------------------------------------
# 加密类型检测
# ---------------------------------------------------------------------------
def detect_encryption_type(db_path: str) -> str:
    """检测数据库加密类型。

    Returns:
        "plain"      —— 未加密（标准 SQLite）
        "sqlcipher4" —— SQLCipher4（4.x）
        "wxsqlite3"  —— wxSQLite3（3.x）
        "unknown"    —— 无法判定
    """
    if not os.path.exists(db_path):
        raise DecryptError(f"数据库不存在：{db_path}")
    with open(db_path, "rb") as f:
        head = f.read(64)
    if not head:
        raise DecryptError(f"数据库为空：{db_path}")
    if head.startswith(SQLITE_HEADER):
        return "plain"
    size = os.path.getsize(db_path)
    if size % SQLCIPHER4_PAGE_SIZE != 0:
        return "unknown"
    # 经验判定：4.x 与 3.x 的库头 16 字节都是随机盐值，无法仅凭文件区分；
    # 由调用方通过微信版本 / 进程类型传入，这里给一个保守默认。
    return "sqlcipher4"


# ---------------------------------------------------------------------------
# SQLCipher4 纯 Python 实现
# ---------------------------------------------------------------------------
def _sqlcipher4_derive_hmac_key(enc_key: bytes, salt: bytes) -> bytes:
    """原始密钥模式下派生 HMAC key：PBKDF2-HMAC-SHA512(key, salt, iter=2, dklen=32)。"""
    return hashlib.pbkdf2_hmac("sha512", enc_key, salt, SQLCIPHER4_HMAC_KDF_ITER, 32)


def _sqlcipher4_verify_with_derived(enc_key: bytes, salt: bytes,
                                    page1: bytes, hmac_input: bytes,
                                    stored_hmac: bytes,
                                    use_xor_salt: bool = True) -> bool:
    """用已派生的 enc_key 校验第一页 HMAC。"""
    mac_salt = bytes(b ^ SQLCIPHER4_HMAC_SALT_XOR for b in salt) if use_xor_salt else salt
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt,
                                   SQLCIPHER4_HMAC_KDF_ITER, 32)
    digest = hmac_mod.new(mac_key, hmac_input, hashlib.sha512).digest()
    return hmac_mod.compare_digest(digest, stored_hmac)


def verify_key_sqlcipher4(db_path: str,
                          key: Union[str, bytes],
                          salt: Optional[bytes] = None,
                          mode: str = "auto") -> bool:
    """验证 SQLCipher4 密钥是否正确（读第一页 HMAC）。

    Args:
        db_path: 4.x 数据库路径
        key: 32 字节 passphrase（4.x 实际模式）或原始 enc_key（旧模式）
        salt: 可选，16 字节盐；不传则取数据库头 16 字节
        mode: "auto" / "passphrase" / "raw_key"
            - passphrase: key 是口令，enc_key = PBKDF2(key, salt, 256000)
            - raw_key:    key 直接是 enc_key
            - auto: 两种都试
    """
    try:
        raw = _bytes_from_key(key)
    except DecryptError:
        return False
    if len(raw) != 32:
        return False
    try:
        with open(db_path, "rb") as f:
            page1 = f.read(SQLCIPHER4_PAGE_SIZE)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取第一页失败 %s：%s", db_path, e)
        return False
    if len(page1) < SQLCIPHER4_PAGE_SIZE:
        return False
    if salt is None:
        salt = page1[:16]
    if len(salt) != 16:
        return False
    stored_hmac = page1[-64:]
    # HMAC 覆盖：第1页 加密体[16:4016] + IV[4016:4032] + 小端页号(=1)
    hmac_input = (page1[16:SQLCIPHER4_PAGE_SIZE - SQLCIPHER4_RESERVE + 16]
                  + struct.pack("<I", 1))

    if mode in ("auto", "passphrase"):
        # 4.x 实测模式：key 是 passphrase，先 PBKDF2(256k) 得 enc_key，
        # 再用 salt^0x3a 派 mac_key
        try:
            enc_key = hashlib.pbkdf2_hmac("sha512", raw, salt,
                                           SQLCIPHER4_KDF_ITER, 32)
            if _sqlcipher4_verify_with_derived(enc_key, salt, page1,
                                               hmac_input, stored_hmac,
                                               use_xor_salt=True):
                return True
        except Exception:
            pass
    if mode in ("auto", "raw_key"):
        # key 直接是 enc_key；mac_key 仍为 PBKDF2(enc_key, salt^0x3a, 2)
        if _sqlcipher4_verify_with_derived(raw, salt, page1, hmac_input,
                                           stored_hmac, use_xor_salt=True):
            return True
    return False


def _decrypt_page_sqlcipher4(enc_key: bytes, page: bytes, page_no: int) -> bytes:
    """解密单页 SQLCipher4 数据，返回明文页（page_size 字节）。

    磁盘布局（page_size=4096, reserve=80）：
      [0:16] 盐（仅第1页，明文）  [16:4016] 加密体  [4016:4032] IV  [4032:4096] HMAC
    第 1 页解密后，磁盘上的 [0:16] 盐不回写明文库；明文页开头 16 字节是固定的
    "SQLite format 3\\x00"，由本函数补齐。
    """
    Cipher, algorithms, modes, ok = _try_import_crypto()
    if not ok:
        raise DecryptError("cryptography 不可用，无法逐页解密")
    reserve = SQLCIPHER4_RESERVE
    page_size = len(page)
    iv = page[page_size - reserve: page_size - reserve + 16]
    if page_no == 1:
        body = page[16: page_size - reserve]
    else:
        body = page[: page_size - reserve]
    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(body) + decryptor.finalize()
    # SQLCipher 各页加密体长度（页1=4000，余=4016）均为 16 的整数倍，
    # 不使用 PKCS 填充，故不得按末字节截断（否则会切掉真实数据）。
    if page_no == 1:
        return SQLITE_HEADER + plain
    return plain


def decrypt_sqlcipher4(db_path: str,
                       key: Union[str, bytes],
                       output_path: str,
                       salt: Optional[bytes] = None,
                       mode: str = "auto") -> str:
    """解密 SQLCipher4 数据库到 output_path（明文 SQLite）。

    Args:
        mode: "auto" / "passphrase" / "raw_key"。
            passphrase 模式下 key 是 32 字节口令，先 PBKDF2(256k) 派生 enc_key。
    """
    raw = _bytes_from_key(key)
    # 读文件 salt
    with open(db_path, "rb") as f:
        page1 = f.read(SQLCIPHER4_PAGE_SIZE)
    file_salt = salt or page1[:16]

    # 判定模式并派生 enc_key
    if mode == "raw_key":
        enc_key = raw
    elif mode == "passphrase":
        enc_key = hashlib.pbkdf2_hmac("sha512", raw, file_salt,
                                       SQLCIPHER4_KDF_ITER, 32)
    else:  # auto
        if verify_key_sqlcipher4(db_path, raw, salt=file_salt, mode="passphrase"):
            enc_key = hashlib.pbkdf2_hmac("sha512", raw, file_salt,
                                           SQLCIPHER4_KDF_ITER, 32)
        elif verify_key_sqlcipher4(db_path, raw, salt=file_salt, mode="raw_key"):
            enc_key = raw
        else:
            raise DecryptError("SQLCipher4 密钥校验失败（HMAC 不匹配），请检查密钥或盐")

    sqlcipher = _try_import_sqlcipher()
    if sqlcipher is not None:
        return _decrypt_via_sqlcipher_binding(sqlcipher, db_path, enc_key, output_path, file_salt)

    # 逐页解密回退
    size = os.path.getsize(db_path)
    if size % SQLCIPHER4_PAGE_SIZE != 0:
        raise DecryptError(f"数据库大小 {size} 不是页大小 {SQLCIPHER4_PAGE_SIZE} 的整数倍")
    n_pages = size // SQLCIPHER4_PAGE_SIZE
    with open(db_path, "rb") as fin, open(output_path, "wb") as fout:
        for i in range(n_pages):
            page = fin.read(SQLCIPHER4_PAGE_SIZE)
            if not page:
                break
            plain_page = _decrypt_page_sqlcipher4(enc_key, page, i + 1)
            # 写回时按原始页大小写出；最后一页的明文可能短于页大小，
            # SQLite 按页大小读，因此要补齐到页大小。
            if len(plain_page) < SQLCIPHER4_PAGE_SIZE:
                plain_page = plain_page + b"\x00" * (SQLCIPHER4_PAGE_SIZE - len(plain_page))
            fout.write(plain_page[:SQLCIPHER4_PAGE_SIZE])
    logger.info("SQLCipher4 逐页解密完成：%s -> %s（%d 页）", db_path, output_path, n_pages)
    return output_path


def _decrypt_via_sqlcipher_binding(sqlcipher, db_path, enc_key, output_path, salt) -> str:
    """通过 sqlcipher3 绑定解密（最快路径）：导出到 KEY '' 的明文 ATTACH 库。"""
    # 明文目标若已存在先移除，避免 ATTACH 复用旧文件
    for suffix in ("", "-wal", "-shm"):
        p = output_path + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    conn = sqlcipher.connect(db_path)
    try:
        # 原始密钥：PRAGMA key = "x'...'"
        hex_key = enc_key.hex()
        conn.execute(f"PRAGMA key = \"x'{hex_key}'\"")
        conn.execute("PRAGMA cipher_compatibility = 4")
        conn.execute("PRAGMA kdf_iter = 256000")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        # 挂一个明文(KEY '')目标，把主库导出为明文
        conn.execute("ATTACH DATABASE ? AS plain KEY ''", (output_path,))
        conn.execute("SELECT sqlcipher_export('plain')")
        conn.execute("DETACH DATABASE plain")
        conn.commit()
    finally:
        conn.close()
    logger.info("SQLCipher4 绑定解密完成：%s -> %s", db_path, output_path)
    return output_path


# ---------------------------------------------------------------------------
# wxSQLite3（3.x）纯 Python 实现
# ---------------------------------------------------------------------------
def verify_key_wxsqlite3(db_path: str, key: Union[str, bytes]) -> bool:
    """验证 wxSQLite3 密钥：尝试解密第一页并检查 SQLite 头。"""
    try:
        enc_key = _bytes_from_key(key)
    except DecryptError:
        return False
    if len(enc_key) != 32:
        return False
    try:
        plain = _decrypt_first_page_wxsqlite3(db_path, enc_key)
    except Exception as e:  # noqa: BLE001
        logger.debug("wxsqlite3 第一页解密异常：%s", e)
        return False
    return _wxsqlite_body_looks_valid(plain)


def _decrypt_first_page_wxsqlite3(db_path: str, enc_key: bytes) -> bytes:
    """解密第 1 页密文体，返回明文页的 [16 : page-reserve]（不含文件头）。"""
    Cipher, algorithms, modes, ok = _try_import_crypto()
    if not ok:
        raise DecryptError("cryptography 不可用")
    with open(db_path, "rb") as f:
        page = f.read(WXSQLITE3_PAGE_SIZE)
    if len(page) < WXSQLITE3_PAGE_SIZE:
        raise DecryptError("第一页过短")
    reserve = WXSQLITE3_RESERVE
    iv = page[WXSQLITE3_PAGE_SIZE - reserve: WXSQLITE3_PAGE_SIZE - reserve + 16]
    body = page[16:WXSQLITE3_PAGE_SIZE - reserve]
    decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(body) + decryptor.finalize()
    return plain


def _wxsqlite_body_looks_valid(plain: bytes, reserve: int = WXSQLITE3_RESERVE) -> bool:
    """校验解密后的明文页 [16:...] 头部结构常量（廉价、强判别，替代恒真的拼头检查）。

    对应 SQLite 文件头偏移 16..23：
      16-17 页大小(4096 -> 10 00)；18 写版本∈{1,2}；19 读版本∈{1,2}；
      20 每页保留字节；21 最大负载分数=64；22 最小=32；23 叶最小=32。
    """
    if len(plain) < 8:
        return False
    page_size_ok = plain[0:2] in (b"\x10\x00", b"\x20\x00", b"\x00\x01")  # 4096/8192/65536
    versions_ok = plain[2] in (1, 2) and plain[3] in (1, 2)
    payload_ok = plain[5] == 64 and plain[6] == 32 and plain[7] == 32
    reserve_ok = plain[4] == reserve
    return page_size_ok and versions_ok and payload_ok and reserve_ok


def decrypt_wxsqlite3(db_path: str, key: Union[str, bytes], output_path: str) -> str:
    """解密 wxSQLite3 数据库到 output_path。"""
    enc_key = _bytes_from_key(key)
    if not verify_key_wxsqlite3(db_path, enc_key):
        raise DecryptError("wxSQLite3 密钥校验失败（未找到 SQLite 头）")
    Cipher, algorithms, modes, ok = _try_import_crypto()
    if not ok:
        raise DecryptError("cryptography 不可用，无法逐页解密")
    size = os.path.getsize(db_path)
    n_pages = size // WXSQLITE3_PAGE_SIZE
    with open(db_path, "rb") as fin, open(output_path, "wb") as fout:
        for i in range(n_pages):
            page = fin.read(WXSQLITE3_PAGE_SIZE)
            if not page:
                break
            reserve = WXSQLITE3_RESERVE
            iv = page[WXSQLITE3_PAGE_SIZE - reserve: WXSQLITE3_PAGE_SIZE - reserve + 16]
            if i == 0:
                body = page[16:WXSQLITE3_PAGE_SIZE - reserve]
                head = SQLITE_HEADER
            else:
                body = page[:WXSQLITE3_PAGE_SIZE - reserve]
                head = b""
            decryptor = Cipher(algorithms.AES(enc_key), modes.CBC(iv)).decryptor()
            plain = decryptor.update(body) + decryptor.finalize()
            # 各页密文体长度均为 16 倍数，无 PKCS 填充，不按末字节剥离
            out_page = (head + plain)
            if len(out_page) < WXSQLITE3_PAGE_SIZE:
                out_page = out_page + b"\x00" * (WXSQLITE3_PAGE_SIZE - len(out_page))
            fout.write(out_page[:WXSQLITE3_PAGE_SIZE])
    logger.info("wxSQLite3 解密完成：%s -> %s", db_path, output_path)
    return output_path


# ---------------------------------------------------------------------------
# 通用入口
# ---------------------------------------------------------------------------
def decrypt_db(db_path: str,
               key: Union[str, bytes],
               output_path: str,
               encryption_type: Optional[str] = None,
               salt: Optional[bytes] = None) -> str:
    """自动检测类型并解密。

    Args:
        db_path: 加密库路径
        key: 32 字节原始密钥 / 64 hex
        output_path: 输出明文库路径
        encryption_type: 可选，强制指定 "sqlcipher4" / "wxsqlite3" / "plain"
        salt: 可选，4.x 专用 16 字节盐
    """
    if encryption_type is None:
        encryption_type = detect_encryption_type(db_path)
    if encryption_type == "plain":
        # 未加密，直接拷贝
        import shutil
        shutil.copyfile(db_path, output_path)
        return output_path
    if encryption_type == "sqlcipher4":
        return decrypt_sqlcipher4(db_path, key, output_path, salt=salt)
    if encryption_type == "wxsqlite3":
        return decrypt_wxsqlite3(db_path, key, output_path)
    raise DecryptError(f"未知加密类型：{encryption_type}")


def verify_key(db_path: str, key: Union[str, bytes],
               encryption_type: Optional[str] = None,
               salt: Optional[bytes] = None) -> bool:
    """通用密钥验证。"""
    if encryption_type is None:
        encryption_type = detect_encryption_type(db_path)
    if encryption_type == "plain":
        return True
    if encryption_type == "sqlcipher4":
        return verify_key_sqlcipher4(db_path, key, salt=salt)
    if encryption_type == "wxsqlite3":
        return verify_key_wxsqlite3(db_path, key)
    return False


__all__ = [
    "DecryptError",
    "detect_encryption_type",
    "verify_key_sqlcipher4",
    "verify_key_wxsqlite3",
    "decrypt_sqlcipher4",
    "decrypt_wxsqlite3",
    "decrypt_db",
    "verify_key",
]
