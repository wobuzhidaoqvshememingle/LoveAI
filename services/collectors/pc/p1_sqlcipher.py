# -*- coding: utf-8 -*-
"""P1 已知 key 时离线解密本地微信库（SQLCipher4 / wxSQLite3）。

纯离线、只读原库、零封号风险。包装现有 wechat_db_decryptor + wechat_db_reader，
不重写解密逻辑。key 来源可为用户手填，或 P2/P3/P9 等通道产出。
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Sequence

from ..base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_PC, DIR_SENT, DIR_RECEIVED, ok, missing)
from .. import env

logger = logging.getLogger(__name__)


class P1SqlcipherDirectAdapter(SourceAdapter):
    id = "P1_sqlcipher_direct"
    name = "已知Key离线解密本地库"
    platform = PLAT_PC
    requires = frozenset({"key"})
    risk_level = RISK_NONE
    default_enabled = True
    local_runnable = True
    success_prior = 0.9
    produces_plaintext = True

    def available(self) -> Availability:
        accounts = env.xwechat_roots()
        if not accounts:
            return missing(
                "未发现 4.x 本地数据目录(xwechat_files)",
                ["先在本机登录一次微信以生成 db_storage",
                 "或通过 O1 官方迁移把记录搬到本机",
                 "或手动指定账号目录 account_root 参数"])
        return ok("发现本地加密库，需提供 32 字节 key（可由 P2/P3/P9 产出或手填）",
                  accounts=list(accounts), needs_key=True)

    def collect(self,
                key=None,
                account_root: Optional[str] = None,
                version: str = "4.x",
                contact_filter: Optional[Sequence[str]] = None,
                **opts) -> List[Message]:
        if key is None:
            raise ValueError("P1 需要 key（hex 字符串或 32 字节）；可用 P2/P3/P9 提取")
        account_root = account_root or (env.xwechat_roots()[0] if env.xwechat_roots() else None)
        if not account_root or not os.path.isdir(account_root):
            raise FileNotFoundError(f"账号目录不存在: {account_root}")

        # 先对首个消息库验 key，错误即明确失败（不静默返回空）
        from services import wechat_db_decryptor as dec
        msg_dbs = env.find_message_dbs(account_root)
        if not msg_dbs:
            raise FileNotFoundError(f"账号目录下未发现 message_*.db: {account_root}")
        if not dec.verify_key(msg_dbs[0], key):
            raise dec.DecryptError(
                "密钥校验失败（HMAC 不匹配）：key 错误或与该库不匹配")

        from services.wechat_db_reader import WeChatDBReader
        reader = WeChatDBReader(account_root, key, version=version)
        out: List[Message] = []
        for m in reader.read_messages(contact_filter=list(contact_filter) if contact_filter else None):
            is_me = int(m.get("is_sender", 0)) == 1
            talker = m.get("talker_id") or None
            out.append(Message(
                direction=DIR_SENT if is_me else DIR_RECEIVED,
                content=m.get("content") or "",
                timestamp=int(m["create_time"]) if m.get("create_time") else None,
                msg_type=m.get("type_name") or "unknown",
                source_id=self.id,
                peer_id=talker,
                peer_name=m.get("display_name") or None,
                speaker="我" if is_me else (m.get("display_name") or "对方"),
                wechat_msg_id=(str(m["msg_id"]) if m.get("msg_id") is not None else None),
                extra={"wx_type": m.get("msg_type"), "sub_type": m.get("sub_type")},
            ))
        return out
