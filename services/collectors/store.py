# -*- coding: utf-8 -*-
"""标准消息入库：对齐现有 chat_messages 表（不改其结构）。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Union

from .base import Message

logger = logging.getLogger(__name__)


def is_revokemsg(content: str) -> bool:
    """判断一条消息是否为微信撤回系统通知。

    口径（P1）：仅当 content 内含 <sysmsg type="revokemsg"> 标记时判为撤回。
    纯文本"你撤回了一条消息"等系统提示不在此列（它们是通知回显，不含 revokemsg XML），
    与正式库已标记的 2 条撤回（wechat_msg_id=7983/10742）口径一致。
    """
    if not content:
        return False
    return "revokemsg" in content.lower()


class MessageStore:
    """把 Message 序列写入 chat_messages。

    Args:
        db: models.database.Database 实例（具备 execute / fetch_one），或数据库文件路径。
    """

    def __init__(self, db: Any):
        if hasattr(db, "execute") and hasattr(db, "fetch_one"):
            self.db = db
            self._owns_db = False
        else:
            from models.database import Database
            self.db = Database(str(db))
            self.db.init_tables()
            self._owns_db = True
        # 确保表存在
        if hasattr(self.db, "init_tables"):
            try:
                self.db.init_tables()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def ensure_person(self, person_id: str, nickname: Optional[str] = None,
                      peer_id: Optional[str] = None) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO persons (id, nickname) VALUES (:id, :nickname)",
            {"id": person_id, "nickname": nickname or person_id})
        if peer_id:
            try:
                self.db.add_person_wxid(person_id, peer_id)
            except Exception:
                pass

    def _existing(self, person_id: str, msg: Message) -> Optional[Any]:
        if msg.wechat_msg_id:
            row = self.db.fetch_one(
                "SELECT id FROM chat_messages WHERE person_id=:p AND wechat_msg_id=:m LIMIT 1",
                {"p": person_id, "m": msg.wechat_msg_id})
            if row:
                return row
        content = (msg.content or "").strip()
        if not content:
            return None
        return self.db.fetch_one(
            "SELECT id FROM chat_messages WHERE person_id=:p AND content=:c "
            "AND direction=:d LIMIT 1",
            {"p": person_id, "c": content, "d": msg.direction})

    def persist(self, messages: Iterable[Message],
                person_id: Optional[str] = None,
                person_nickname: Optional[str] = None) -> Dict[str, Any]:
        """入库，返回统计。person_id 缺省时用消息自带 person_id。"""
        inserted = 0
        duplicates = 0
        empty = 0
        me = other = 0
        persons_touched: Dict[str, str] = {}

        for msg in messages:
            pid = person_id or msg.person_id
            if not pid:
                # 无归属人物：放到以 peer 命名的临时人物
                pid = msg.peer_id or "unassigned"
            nick = person_nickname or msg.peer_name or msg.speaker or pid
            self.ensure_person(pid, nick, msg.peer_id)
            persons_touched[pid] = nick

            content = (msg.content or "").strip()
            if not content and not msg.wechat_msg_id:
                empty += 1
                continue
            if self._existing(pid, msg):
                duplicates += 1
                continue
            row = msg.to_row()
            created_at = row["created_at"] or datetime.now().isoformat()
            # P1：撤回系统通知（revokemsg）落 is_withdrawn=1，画像侧据此排除
            is_withdrawn = 1 if is_revokemsg(row["content"]) else 0
            self.db.execute(
                "INSERT INTO chat_messages "
                "(person_id, direction, content, msg_type, wechat_msg_id, created_at, is_withdrawn) "
                "VALUES (:person_id,:direction,:content,:msg_type,:wechat_msg_id,:created_at,:is_withdrawn)",
                {"person_id": pid, "direction": row["direction"],
                 "content": row["content"], "msg_type": row["msg_type"],
                 "wechat_msg_id": msg.wechat_msg_id, "created_at": created_at,
                 "is_withdrawn": is_withdrawn})
            inserted += 1
            if msg.direction == "sent":
                me += 1
            else:
                other += 1

        result = {"inserted": inserted, "duplicates": duplicates, "empty": empty,
                  "sent": me, "received": other,
                  "persons": persons_touched}
        logger.info("采集入库: 插入=%d 重复=%d (我=%d 对方=%d)",
                    inserted, duplicates, me, other)
        return result

    def count_for(self, person_id: str) -> int:
        row = self.db.fetch_one(
            "SELECT COUNT(*) AS n FROM chat_messages WHERE person_id=:p",
            {"p": person_id})
        return int(row["n"]) if row else 0
