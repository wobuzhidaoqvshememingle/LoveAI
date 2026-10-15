# -*- coding: utf-8 -*-
"""A4 root 直读安卓微信 EnMicroMsg.db（老版 key 派生 + SQLCipher 1.x 解密）。

老版安卓微信（<= 约 2019）的 EnMicroMsg.db：
  - key 派生：passphrase = MD5(IMEI + uin)[:7]（小写），作为 SQLCipher 口令
  - 加密参数：page_size=1024 / HMAC-SHA1 / kdf_iter=4000（PBKDF2-HMAC-SHA1）
新版安卓微信已改用信息流分析（研究向，不做强依赖，仅在元数据中标注）。

本通道需要 root（或已 pull 出来的库文件）。本模块把 key 派生与解密读取做成
纯函数，便于单测（fixture 库即用这套参数加密）。
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_ANDROID, DIR_SENT, DIR_RECEIVED, ok, missing)
from .. import env

logger = logging.getLogger(__name__)

# 老版 SQLCipher 参数（EnMicroMsg.db 实测）
LEGACY_PAGE_SIZE = 1024
LEGACY_KDF_ITER = 4000


# ---------------------------------------------------------------------------
# 可测纯函数：key 派生 + 解密读取
# ---------------------------------------------------------------------------
def derive_android_key(imei: str, uin: str) -> str:
    """老版 key 派生：MD5(IMEI + uin)[:7]，小写。

    >>> derive_android_key("861234567890123", "123456789")
    '7eaf189'
    """
    return hashlib.md5((str(imei) + str(uin)).encode("utf-8")).hexdigest()[:7].lower()


def read_enmicromsg(db_path: str, passphrase: str,
                     talker: Optional[str] = None) -> List[Message]:
    """用老版参数打开 EnMicroMsg.db 并读取 Message 表。

    直接用 sqlcipher3 绑定打开（避免把整个库先解成明文文件）。
    表结构兼容老版：Message(msgId, talker, isSend, content, createTime, type)。
    """
    from sqlcipher3 import dbapi2 as sqlcipher  # 延迟导入
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"EnMicroMsg.db 不存在: {db_path}")
    conn = sqlcipher.connect(db_path)
    try:
        # 口令里若含单引号需转义（fixture 不含，但真实场景可能）
        safe = passphrase.replace("'", "''")
        conn.execute(f"PRAGMA key = '{safe}'")
        conn.execute(f"PRAGMA cipher_page_size = {LEGACY_PAGE_SIZE}")
        conn.execute(f"PRAGMA kdf_iter = {LEGACY_KDF_ITER}")
        conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1")
        conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA1")
        # 验密：跑一条查询；key 错会抛 OperationalError
        sql = ("SELECT msgId, talker, isSend, content, createTime, type "
               "FROM Message")
        params: List[object] = []
        if talker:
            sql += " WHERE talker = ?"
            params.append(talker)
        sql += " ORDER BY msgId"
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    finally:
        conn.close()

    out: List[Message] = []
    for msg_id, talker_id, is_send, content, create_time, mtype in rows:
        out.append(Message(
            direction=DIR_SENT if int(is_send or 0) == 1 else DIR_RECEIVED,
            content=content or "",
            timestamp=int(create_time) if create_time else None,
            msg_type="text" if int(mtype or 1) == 1 else "unknown",
            source_id="A4_root_db",
            peer_id=talker_id or None,
            speaker="我" if int(is_send or 0) == 1 else (talker_id or "对方"),
            wechat_msg_id=str(msg_id) if msg_id is not None else None,
            extra={"legacy": True},
        ))
    return out


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------
class A4RootDbAdapter(SourceAdapter):
    id = "A4_root_db"
    name = "Root直读EnMicroMsg.db(老版key)"
    platform = PLAT_ANDROID
    requires = frozenset({"root", "key"})
    risk_level = RISK_NONE
    default_enabled = True
    local_runnable = True
    success_prior = 0.7
    produces_plaintext = True

    def available(self) -> Availability:
        # 本机离线：只要 sqlcipher3 可导入即可用 fixture/手动指定库
        try:
            from sqlcipher3 import dbapi2  # noqa: F401
        except Exception:
            return missing("sqlcipher3 绑定不可用",
                           ["pip install sqlcipher3-binary",
                            "或提供已 pull 到本地的 EnMicroMsg.db"])
        return ok("sqlcipher3 可用；需提供 root 权限 pull 出的 EnMicroMsg.db"
                  " 及对应 IMEI/uin 以派生 key")

    def collect(self,
                db_path: str = "",
                imei: str = "",
                uin: str = "",
                passphrase: Optional[str] = None,
                talker: Optional[str] = None,
                **opts) -> List[Message]:
        if not db_path:
            raise ValueError("A4 需要 db_path（已 pull 到本地的 EnMicroMsg.db）")
        if passphrase is None:
            if not imei or not uin:
                raise ValueError(
                    "A4 需要 passphrase 或同时提供 imei+uin 以派生 key："
                    "MD5(imei+uin)[:7]")
            passphrase = derive_android_key(imei, uin)
        return read_enmicromsg(db_path, passphrase, talker=talker)
