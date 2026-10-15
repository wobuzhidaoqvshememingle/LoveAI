# -*- coding: utf-8 -*-
"""共享视觉/OCR 解析（本地优先）：把聊天截图 OCR 结果转成标准 Message。

微信气泡几何：对方(received)气泡靠左，我(sent)气泡靠右。
同时兼容显式说话人标签（"我:" / "对方:" / "她:" 等）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from .base import Message, DIR_RECEIVED, DIR_SENT
from . import env

logger = logging.getLogger(__name__)

_SPEAKER_RE = re.compile(r"^\s*([^:：]{1,12})[:：]\s*(.*)$")
_ME = {"我", "自己", "me", "i", "my", "本人", "咱"}
_OTHER = {"对方", "她", "他", "你", "ta", "other", "目标", "对象"}
# 时间分隔行：12:00 / 昨天 21:02 / 前天 09:10 / 10月8日 12:00 / 星期五 21:02
_TIME_TOKEN_RE = re.compile(
    r"^\s*(?:(昨天|前天|星期[一二三四五六日天]|\d{1,2}月\d{1,2}日)\s+)?"
    r"\d{1,2}:\d{2}(?::\d{2})?\s*$")
_TIME_WORD_RE = re.compile(r"^\s*(昨天|前天|星期[一二三四五六日天])\s*$")

_ocr_singleton = None


def get_ocr():
    """惰性构造 OCRService（本地 rapidocr）。"""
    global _ocr_singleton
    if _ocr_singleton is None:
        from services.ocr_service import OCRService
        _ocr_singleton = OCRService()
    return _ocr_singleton


def ocr_image(path: str) -> Dict[str, Any]:
    """对单张图片跑本地 OCR，返回 OCRService 结果 dict。"""
    return get_ocr().recognize_image(path)


def _box_metrics(box: Sequence[Sequence[float]]):
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    return min(xs), min(ys), max(xs), max(ys)


def _classify_line(text: str, center_x: float, width: int):
    """返回 (direction, cleaned_text, label_found)。"""
    m = _SPEAKER_RE.match(text)
    if m:
        speaker, body = m.group(1).strip(), m.group(2)
        sl = speaker.lower()
        if sl in {x.lower() for x in _ME}:
            return DIR_SENT, body.strip(), True
        if sl in {x.lower() for x in _OTHER}:
            return DIR_RECEIVED, body.strip(), True
    # 几何判定（宽度未知时默认 received）
    if width:
        ratio = center_x / width
        if ratio >= 0.62:
            return DIR_SENT, text, False
        if ratio <= 0.45:
            return DIR_RECEIVED, text, False
    return DIR_RECEIVED, text, False


def parse_chat_ocr(ocr_result: Dict[str, Any], image_width: int,
                   source_id: str = "", peer_name: Optional[str] = None) -> List[Message]:
    """把一次 OCR 结果解析成 Message 列表（多行气泡自动聚合）。"""
    lines = ocr_result.get("lines") or []
    items = []
    for ln in lines:
        text = (ln.get("text") or "").strip()
        box = ln.get("box")
        if not text or not box:
            continue
        # 时间分隔行（12:00 / 昨天 21:02 等）跳过，不当消息
        if _TIME_TOKEN_RE.match(text) or _TIME_WORD_RE.match(text):
            continue
        x0, y0, x1, y1 = _box_metrics(box)
        center_x = (x0 + x1) / 2
        direction, cleaned, _label = _classify_line(text, center_x, image_width)
        if not cleaned:
            continue
        items.append({"dir": direction, "text": cleaned,
                      "top": y0, "bottom": y1, "height": max(1.0, y1 - y0)})

    items.sort(key=lambda d: d["top"])

    # 聚合同方向、垂直紧邻的行
    messages: List[Message] = []
    cur = None
    for it in items:
        if (cur and it["dir"] == cur["dir"]
                and it["top"] - cur["bottom"] <= cur["height"] * 0.9):
            cur["text"] = cur["text"] + "\n" + it["text"]
            cur["bottom"] = it["bottom"]
        else:
            if cur:
                messages.append(_make(cur, source_id, peer_name))
            cur = {"dir": it["dir"], "text": it["text"],
                   "top": it["top"], "bottom": it["bottom"], "height": it["height"]}
    if cur:
        messages.append(_make(cur, source_id, peer_name))
    return messages


def _make(cluster: Dict, source_id: str, peer_name: Optional[str]) -> Message:
    return Message(
        direction=cluster["dir"],
        content=cluster["text"].strip(),
        timestamp=None,  # 截图通常只有时分、无日期，不臆造
        msg_type="text",
        source_id=source_id,
        peer_name=peer_name,
        speaker="我" if cluster["dir"] == DIR_SENT else (peer_name or "对方"),
    )


def image_width_of(path: str) -> int:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return int(im.width)
    except Exception:
        return 0
