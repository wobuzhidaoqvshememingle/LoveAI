# -*- coding: utf-8 -*-
"""A3 现成无障碍 App 导出物格式适配解析（不重写 App、不自己跑无障碍服务）。

支持的导出格式：
  - JSON：io.drawcore.wx / worktool 风格的 {messages:[{is_send,content,time,...}]}
  - HTML：简单 <table> 导出，按 <tr class="sent|received"> 或文本启发式判方向

合规提示：高频无障碍读取微信在 2026 年有封号实锤，本通道只解析"用户已经
自己导出来的文件"，不主动驱动无障碍服务。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import List, Optional
from html.parser import HTMLParser

from ..base import (SourceAdapter, Message, Availability, RISK_LOW,
                    PLAT_ANDROID, DIR_SENT, DIR_RECEIVED, ok, missing)
from .. import env

logger = logging.getLogger(__name__)

COMPLIANCE_NOTE = ("高频无障碍读取微信 2026 年有封号实锤；本通道仅解析已导出文件，"
                   "不主动开启/驱动无障碍服务。")


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------
def parse_export_json(text: str,
                      source_id: str = "A3_accessibility_export") -> List[Message]:
    data = json.loads(text)
    if isinstance(data, dict):
        msgs = data.get("messages") or []
        talker_name = data.get("talker_name") or data.get("peer_name")
    else:
        msgs = data
        talker_name = None
    out: List[Message] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        is_send = bool(m.get("is_send", m.get("isSend", 0)))
        content = (m.get("content") or m.get("text") or "").strip()
        if not content:
            continue
        ts = None
        t = m.get("time") or m.get("create_time") or m.get("createTime")
        if isinstance(t, (int, float)):
            ts = int(t)
        out.append(Message(
            direction=DIR_SENT if is_send else DIR_RECEIVED,
            content=content,
            timestamp=ts,
            msg_type="text",
            source_id=source_id,
            peer_id=m.get("talker") or data.get("talker") if isinstance(data, dict) else None,
            peer_name=talker_name,
            speaker="我" if is_send else (talker_name or "对方"),
        ))
    return out


# ---------------------------------------------------------------------------
# HTML 解析
# ---------------------------------------------------------------------------
class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []  # [(class, [cell_texts])]
        self._cur_row_cls = ""
        self._cur_cells = []
        self._in_td = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self._cur_row_cls = a.get("class", "")
            self._cur_cells = []
        elif tag in ("td", "th"):
            self._in_td = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_td = False
            self._cur_cells.append("".join(self._buf).strip())
        elif tag == "tr":
            if self._cur_cells:
                self.rows.append((self._cur_row_cls, self._cur_cells))
            self._cur_row_cls = ""
            self._cur_cells = []

    def handle_data(self, data):
        if self._in_td:
            self._buf.append(data)


def parse_export_html(text: str,
                      source_id: str = "A3_accessibility_export") -> List[Message]:
    p = _TableParser()
    p.feed(text)
    out: List[Message] = []
    for cls, cells in p.rows:
        # cells: [who, text, time?]
        if len(cells) < 2:
            continue
        who, body = cells[0], cells[1]
        body = body.strip()
        if not body:
            continue
        if "sent" in cls.lower():
            direction = DIR_SENT
        elif "received" in cls.lower():
            direction = DIR_RECEIVED
        else:
            direction = DIR_SENT if who in ("我", "me", "Me") else DIR_RECEIVED
        out.append(Message(
            direction=direction,
            content=body,
            timestamp=None,
            msg_type="text",
            source_id=source_id,
            speaker=who or ("我" if direction == DIR_SENT else "对方"),
        ))
    return out


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------
class A3AccessibilityExportAdapter(SourceAdapter):
    id = "A3_accessibility_export"
    name = "无障碍App导出物解析(现成文件)"
    platform = PLAT_ANDROID
    requires = frozenset()
    risk_level = RISK_LOW
    default_enabled = True
    local_runnable = True
    success_prior = 0.6
    produces_plaintext = True

    def available(self) -> Availability:
        # 纯离线解析本地文件，只要 Python 标准库可用即可
        return ok("纯离线解析本地导出文件（JSON/HTML/txt）",
                  note=COMPLIANCE_NOTE)

    def collect(self,
                path: str = "",
                text: Optional[str] = None,
                fmt: str = "auto",
                **opts) -> List[Message]:
        if text is None:
            if not path or not os.path.isfile(path):
                raise FileNotFoundError(
                    f"A3 需要 path 指向已导出文件（.json/.html/.txt），"
                    f"或直接传 text；当前 path={path!r}")
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            ext = os.path.splitext(path)[1].lower()
            if fmt == "auto":
                if ext == ".json":
                    fmt = "json"
                elif ext in (".html", ".htm"):
                    fmt = "html"
                else:
                    fmt = "txt"

        if fmt == "json":
            return parse_export_json(text, source_id=self.id)
        if fmt == "html":
            return parse_export_html(text, source_id=self.id)
        # txt：每行 "我: xxx" / "对方: xxx"
        return self._parse_txt(text)

    def _parse_txt(self, text: str) -> List[Message]:
        out: List[Message] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^(我|对方|她|他)\s*[:：]\s*(.+)$", line)
            if not m:
                continue
            who, body = m.group(1), m.group(2)
            out.append(Message(
                direction=DIR_SENT if who == "我" else DIR_RECEIVED,
                content=body, timestamp=None, msg_type="text",
                source_id=self.id, speaker=who,
            ))
        return out
