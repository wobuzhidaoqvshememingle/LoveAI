# -*- coding: utf-8 -*-
"""T3 手动粘贴 / 微信导出文本 / JSON / CSV（包装现有 ChatImporter 的解析能力）。

零依赖、纯本地、零风险；不直接依赖微信密钥。collect() 返回标准 Message，
入库交给 store（与 ChatImporter 自带的 _save 解耦，便于统一去重/编排）。
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from typing import Any, List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_THIRDPARTY, DIR_SENT, DIR_RECEIVED, ok)
from .. import env

logger = logging.getLogger(__name__)

_ME = {"me", "my", "i", "自己", "我"}


def _iso_to_ts(s: str) -> Optional[float]:
    if not s:
        return None
    s = str(s).strip().replace("/", "-")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def _dir_from_speaker(sp: str) -> str:
    return DIR_SENT if str(sp).strip().lower() in _ME else DIR_RECEIVED


class T3ManualPasteAdapter(SourceAdapter):
    id = "T3_manual_paste"
    name = "手动粘贴/导出文本(txt/json/csv)"
    platform = PLAT_THIRDPARTY
    requires = frozenset()
    risk_level = RISK_NONE
    default_enabled = True
    local_runnable = True
    success_prior = 0.95
    produces_plaintext = True

    def available(self) -> Availability:
        # 纯标准库，任何环境都可用
        return ok("纯本地文本解析，无需外部依赖")

    def collect(self, text: str = "", path: str = "", fmt: str = "auto",
                peer_name: Optional[str] = None, **opts) -> List[Message]:
        if path and not text:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            if fmt == "auto":
                fmt = path.lower().rsplit(".", 1)[-1]
        if not text or not text.strip():
            return []
        fmt = fmt if fmt != "auto" else self._guess(text)
        if fmt == "json":
            return self._collect_json(text, peer_name)
        if fmt == "csv":
            return self._collect_csv(text, peer_name)
        return self._collect_text(text, peer_name)

    @staticmethod
    def _guess(text: str) -> str:
        s = text.strip()
        if s.startswith("[") or s.startswith("{"):
            try:
                json.loads(s)
                return "json"
            except Exception:
                pass
        return "text"

    def _collect_text(self, text: str, peer_name: Optional[str]) -> List[Message]:
        from services.chat_importer import ChatImporter
        parsed = ChatImporter(None).parse_text(text)
        out: List[Message] = []
        for d in parsed:
            out.append(Message(
                direction=d.get("direction", DIR_RECEIVED),
                content=d.get("content", ""),
                timestamp=_iso_to_ts(d.get("created_at", "")),
                msg_type=d.get("msg_type", "text") or "text",
                source_id=self.id,
                peer_name=peer_name,
                speaker="我" if d.get("direction") == DIR_SENT else (peer_name or "对方"),
            ))
        return out

    def _collect_json(self, text: str, peer_name: Optional[str]) -> List[Message]:
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("messages") or data.get("data") or []
        out: List[Message] = []
        for item in data:
            direction = _dir_from_speaker(item.get("speaker",
                                    item.get("direction", "")))
            ts = item.get("time") or item.get("timestamp") or item.get("created_at")
            out.append(Message(
                direction=direction,
                content=str(item.get("content", "")),
                timestamp=float(ts) if isinstance(ts, (int, float)) else _iso_to_ts(str(ts)),
                msg_type=str(item.get("msg_type", "text")),
                source_id=self.id,
                peer_id=item.get("wxid") or item.get("peer_id"),
                peer_name=peer_name or item.get("peer_name"),
                wechat_msg_id=str(item["msg_id"]) if item.get("msg_id") else None,
                speaker="我" if direction == DIR_SENT else (peer_name or "对方"),
            ))
        return out

    def _collect_csv(self, text: str, peer_name: Optional[str]) -> List[Message]:
        out: List[Message] = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            direction = _dir_from_speaker(row.get("speaker",
                                   row.get("direction", "")))
            ts = row.get("time") or row.get("timestamp")
            out.append(Message(
                direction=direction,
                content=str(row.get("content", "")),
                timestamp=float(ts) if isinstance(ts, (int, float)) else _iso_to_ts(str(ts)),
                msg_type=str(row.get("msg_type", "text") or "text"),
                source_id=self.id,
                peer_name=peer_name,
                speaker="我" if direction == DIR_SENT else (peer_name or "对方"),
            ))
        return out
