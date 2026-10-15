# -*- coding: utf-8 -*-
"""T2 开源工具导出导入（chatlog / weflow-cli / PyWxDump / 留痕：JSON / CSV / HTML / 已解密 sqlite）。

纯本地、标准库、零风险：
  - JSON：chatlog 风格（data[] 数组，speaker=self/other，msgId/talker/createTime）。
  - CSV：PyWxDump 风格（is_sender 0/1，talker/nickname/content/create_time/msg_id）。
  - HTML：weflow/留痕 风格 <table> 行（时间 | 说话人 | 正文）。
  - 已解密 sqlite 库：直接以只读方式打开读消息表（已解密则无需 key；
    若是加密库请走 P1SqlcipherDirectAdapter）。

不触网、不注入、不发送；只读导出文件。
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from html.parser import HTMLParser
from typing import List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_THIRDPARTY, DIR_SENT, DIR_RECEIVED, ok)

logger = logging.getLogger(__name__)

_ME_TOKENS = {"self", "me", "mine", "我", "自己", "true", "1"}


def _ts(raw) -> Optional[float]:
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw or "").strip().replace("/", "-")
    if not s:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def _is_sender(*vals) -> bool:
    for v in vals:
        if v is None:
            continue
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v == 1
        if str(v).strip().lower() in _ME_TOKENS:
            return True
    return False


class _TableHtmlParser(HTMLParser):
    """weflow/留痕 风格：<tr><td>时间</td><td>说话人</td><td>正文</td></tr>。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


class T2OpensourceAdapter(SourceAdapter):
    id = "T2_opensource"
    name = "开源工具导出导入(chatlog/PyWxDump/weflow json/csv/html)"
    platform = PLAT_THIRDPARTY
    requires = frozenset()
    risk_level = RISK_NONE
    default_enabled = True
    local_runnable = True
    success_prior = 0.9
    produces_plaintext = True

    def available(self) -> Availability:
        return ok("纯本地标准库解析开源导出，无需外部依赖")

    # ------------------------------------------------------------------
    def collect(self, path: str = "", text: str = "", fmt: str = "auto",
                db_path: Optional[str] = None,
                peer_name: Optional[str] = None, **opts) -> List[Message]:
        if db_path and os.path.isfile(db_path):
            return self._collect_sqlite(db_path, peer_name)
        if path and not text:
            ext = path.lower().rsplit(".", 1)[-1]
            if ext in ("db", "sqlite", "sqlite3"):
                return self._collect_sqlite(path, peer_name)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            if fmt == "auto":
                fmt = ext
        if not text or not text.strip():
            return []
        if fmt == "auto":
            s = text.lstrip()
            if s.startswith("[") or s.startswith("{"):
                fmt = "json"
            elif s.startswith("<"):
                fmt = "html"
            else:
                fmt = "csv"
        if fmt == "json":
            return self._collect_json(text, peer_name)
        if fmt in ("htm", "html"):
            return self._collect_html(text, peer_name)
        return self._collect_csv(text, peer_name)

    # ------------------------------------------------------------------
    def _collect_json(self, text: str, peer_name: Optional[str]) -> List[Message]:
        data = json.loads(text)
        if isinstance(data, dict):
            data = (data.get("data") or data.get("messages")
                    or data.get("result") or data.get("list") or [])
        out: List[Message] = []
        for it in data:
            if not isinstance(it, dict):
                continue
            direction = DIR_SENT if _is_sender(it.get("speaker"),
                                                it.get("is_sender")) else DIR_RECEIVED
            mid = it.get("msgId") or it.get("msg_id") or it.get("id")
            out.append(Message(
                direction=direction,
                content=str(it.get("content", "") or ""),
                timestamp=_ts(it.get("timestamp") or it.get("createTime")
                              or it.get("create_time") or it.get("time")),
                msg_type="text",
                source_id=self.id,
                peer_id=it.get("talker") or it.get("wxid") or it.get("peer_id"),
                peer_name=peer_name or it.get("nickname"),
                wechat_msg_id=str(mid) if mid is not None else None,
                speaker="我" if direction == DIR_SENT
                else (peer_name or it.get("nickname") or "对方"),
            ))
        return out

    def _collect_csv(self, text: str, peer_name: Optional[str]) -> List[Message]:
        out: List[Message] = []
        for row in csv.DictReader(io.StringIO(text)):
            direction = DIR_SENT if _is_sender(row.get("is_sender"),
                                               row.get("direction")) else DIR_RECEIVED
            out.append(Message(
                direction=direction,
                content=str(row.get("content", "") or ""),
                timestamp=_ts(row.get("create_time") or row.get("timestamp")
                              or row.get("time")),
                msg_type="text",
                source_id=self.id,
                peer_id=row.get("talker") or row.get("wxid"),
                peer_name=peer_name or row.get("nickname"),
                wechat_msg_id=str(row["msg_id"]) if row.get("msg_id") else None,
                speaker="我" if direction == DIR_SENT
                else (peer_name or row.get("nickname") or "对方"),
            ))
        return out

    def _collect_html(self, text: str, peer_name: Optional[str]) -> List[Message]:
        p = _TableHtmlParser()
        p.feed(text)
        out: List[Message] = []
        for cells in p.rows:
            if len(cells) < 3:
                continue
            time_s, speaker, content = cells[0], cells[1], cells[-1]
            low = content.lower()
            if "content" in low or "内容" in content:  # 表头行
                continue
            direction = DIR_SENT if _is_sender(speaker) else DIR_RECEIVED
            out.append(Message(
                direction=direction,
                content=content,
                timestamp=_ts(time_s),
                msg_type="text",
                source_id=self.id,
                peer_name=peer_name,
                speaker=speaker,
            ))
        return out

    # ------------------------------------------------------------------
    def _collect_sqlite(self, db_path: str,
                        peer_name: Optional[str]) -> List[Message]:
        """已解密库：只读打开，自动探测消息表与常见列名。"""
        import sqlite3
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")  # 只读意图：禁止任何写操作
        try:
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            target = next((t for t in tables
                           if ("msg" in t.lower() or "message" in t.lower()
                               or "chat" in t.lower())), None)
            if not target:
                return []
            cols = [r[1] for r in con.execute(
                f'PRAGMA table_info("{target}")')]

            def pick(names):
                for n in names:
                    for c in cols:
                        if c.lower() == n.lower():
                            return c
                return None

            c_content = pick(["content", "str_content", "text"])
            if not c_content:
                return []
            c_sender = pick(["is_sender", "issender", "sender", "issend"])
            c_talker = pick(["talker", "talker_id", "wxid"])
            c_time = pick(["create_time", "createtime", "datetime", "createTime"])
            c_id = pick(["msg_id", "msgsvr_id", "msgid"])

            out: List[Message] = []
            for row in con.execute(f'SELECT * FROM "{target}"'):
                d = dict(row)
                is_me = bool(d.get(c_sender)) if c_sender else False
                ts_raw = d.get(c_time) if c_time else None
                out.append(Message(
                    direction=DIR_SENT if is_me else DIR_RECEIVED,
                    content=str(d.get(c_content) or ""),
                    timestamp=_ts(ts_raw),
                    msg_type="text",
                    source_id=self.id,
                    peer_id=d.get(c_talker) if c_talker else None,
                    peer_name=peer_name,
                    wechat_msg_id=(str(d[c_id])
                                   if c_id and d.get(c_id) is not None else None),
                    speaker="我" if is_me else (peer_name or "对方"),
                ))
            return out
        finally:
            con.close()
