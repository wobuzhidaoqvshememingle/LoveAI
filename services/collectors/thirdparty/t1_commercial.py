# -*- coding: utf-8 -*-
"""T1 商业软件导出导入（楼月等"微信聊天记录导出恢复助手"：HTM / TXT / DOCX）。

纯本地、标准库、零风险：
  - .htm/.html：解析楼月风格 <div class="message me|other"> 气泡（时间/说话人/正文）。
  - .txt：解析"时间 说话人"头行 + 后续正文行 的导出文本。
  - .docx：用标准库 zipfile 解 word/document.xml，抽段落文本后复用 TXT 解析。
  - 老式 .doc 二进制无法可靠解析 -> 明确报错并引导另存为 .docx / 导出 htm/txt。

不触网、不注入、不发送；只读导出文件。
"""
from __future__ import annotations

import io
import logging
import os
import re
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from typing import List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_THIRDPARTY, DIR_SENT, DIR_RECEIVED, ok)

logger = logging.getLogger(__name__)

_ME_TOKENS = {"我", "自己", "本人", "me", "self", "mine"}

_TS_FMTS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M")

# TXT 头行：2026-01-01 12:00:00 说话人
_TXT_HEADER = re.compile(
    r"^\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)\s+(.+?)\s*$")

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _to_ts(raw: str) -> Optional[float]:
    s = (raw or "").strip().replace("/", "-")
    if not s:
        return None
    for fmt in _TS_FMTS:
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def _direction(speaker: str) -> str:
    return DIR_SENT if str(speaker).strip().lower() in _ME_TOKENS else DIR_RECEIVED


class _LouyueHtmlParser(HTMLParser):
    """楼月风格：<div class="message me|other"> 内含 time/name/text 子 div。"""

    def __init__(self) -> None:
        super().__init__()
        self.msgs: List[dict] = []
        self._cur: Optional[dict] = None
        self._depth = 0
        self._field: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        cls = (dict(attrs).get("class") or "").split()
        if tag == "div" and "message" in cls:
            side = DIR_SENT if ("me" in cls or "right" in cls) else DIR_RECEIVED
            self._cur = {"direction": side, "time": "", "name": "", "chunks": []}
            self._depth = 1
            self._field = None
            return
        if self._cur is not None:
            self._depth += 1
            if tag == "div":
                if "time" in cls:
                    self._field = "time"
                elif "name" in cls:
                    self._field = "name"
                elif "text" in cls:
                    self._field = "text"

    def handle_endtag(self, tag):
        if self._cur is None:
            return
        if tag == "div":
            self._depth -= 1
            if self._field:
                self._field = None
            if self._depth <= 0:
                m = self._cur
                self.msgs.append({
                    "direction": m["direction"],
                    "time": m["time"].strip(),
                    "name": m["name"].strip(),
                    "text": "".join(m["chunks"]).strip(),
                })
                self._cur = None
                self._depth = 0

    def handle_data(self, data):
        if self._cur is None:
            return
        if self._field == "time":
            self._cur["time"] += data
        elif self._field == "name":
            self._cur["name"] += data
        elif self._field == "text":
            self._cur["chunks"].append(data)


def _docx_to_lines(data: bytes) -> List[str]:
    """从 .docx（zip）中抽取 word/document.xml 的段落纯文本行。"""
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml_bytes = z.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    lines: List[str] = []
    for p in root.iter(f"{{{_W_NS}}}p"):
        text = "".join((t.text or "") for t in p.iter(f"{{{_W_NS}}}t"))
        text = text.strip()
        if text:
            lines.append(text)
    return lines


class T1CommercialAdapter(SourceAdapter):
    id = "T1_commercial"
    name = "商业软件导出导入(楼月等 htm/txt/docx)"
    platform = PLAT_THIRDPARTY
    requires = frozenset()
    risk_level = RISK_NONE
    default_enabled = True
    local_runnable = True
    success_prior = 0.9
    produces_plaintext = True

    def available(self) -> Availability:
        return ok("纯本地标准库解析商业导出文件，无需外部依赖")

    # ------------------------------------------------------------------
    def collect(self, path: str = "", text: str = "", fmt: str = "auto",
                peer_name: Optional[str] = None, **opts) -> List[Message]:
        if path and not text:
            ext = path.lower().rsplit(".", 1)[-1]
            if ext == "doc":
                raise ValueError(
                    "老式 .doc 二进制无法可靠解析：请在导出软件中另存为 .docx，"
                    "或改导出为 .htm / .txt 后再导入")
            if ext == "docx":
                with open(path, "rb") as f:
                    lines = _docx_to_lines(f.read())
                return self._parse_txt("\n".join(lines), peer_name)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            if fmt == "auto":
                fmt = ext
        if not text or not text.strip():
            return []
        if fmt == "auto":
            fmt = "htm" if text.lstrip().lower().startswith("<") else "txt"
        if fmt in ("htm", "html"):
            return self._parse_html(text, peer_name)
        return self._parse_txt(text, peer_name)

    # ------------------------------------------------------------------
    def _parse_txt(self, text: str, peer_name: Optional[str]) -> List[Message]:
        out: List[Message] = []
        pending: Optional[dict] = None

        def flush() -> None:
            nonlocal pending
            if pending is None:
                return
            content = "\n".join(pending["lines"]).strip()
            sp = pending["speaker"]
            if content:
                out.append(Message(
                    direction=_direction(sp),
                    content=content,
                    timestamp=_to_ts(pending["time"]),
                    msg_type="text",
                    source_id=self.id,
                    peer_name=peer_name,
                    speaker=sp,
                ))
            pending = None

        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            m = _TXT_HEADER.match(line)
            if m:
                flush()
                pending = {"time": m.group(1),
                           "speaker": m.group(2).strip(), "lines": []}
            elif pending is not None:
                pending["lines"].append(line)
        flush()
        return out

    def _parse_html(self, text: str, peer_name: Optional[str]) -> List[Message]:
        p = _LouyueHtmlParser()
        p.feed(text)
        out: List[Message] = []
        for m in p.msgs:
            if not m["text"]:
                continue
            if m["direction"] == DIR_SENT:
                speaker = m["name"] or "我"
            else:
                speaker = m["name"] or peer_name or "对方"
            out.append(Message(
                direction=m["direction"],
                content=m["text"],
                timestamp=_to_ts(m["time"]),
                msg_type="text",
                source_id=self.id,
                peer_name=peer_name,
                speaker=speaker,
            ))
        return out
