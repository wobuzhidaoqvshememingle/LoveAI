# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 非文本标注读取接口 (nontext_analysis)
================================================================

供情感分析链路（emotion_analyzer / persona_analyzer / affection_engine 等）
消费 nontext_annotations 表。本模块只读，不写库。

用法::

    from services.nontext_analysis import NontextAnalysis
    na = NontextAnalysis()
    ann = na.get_annotation(chat_message_id=9851)
    by_type = na.get_annotations_by_type()
    cov = na.get_coverage()
    hint = na.merge_emotion_hint(chat_message_id=9851)

分析链路如何消费（见 docs/非文本标注接入说明.md）:
    情感分析在处理一条消息时，若该消息是非文本，文本字段为空，
    调用 merge_emotion_hint(msg_id) 拿 annotation 里的 text_content 作为
    补充文本，并并入 emotion_hint / emotion_score。
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Optional

logger = logging.getLogger("LoveAI.nontext.analysis")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOVAI_DB = os.path.join(PROJECT_ROOT, "data", "loveai.db")


class NontextAnalysis:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or LOVAI_DB

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    # ------------------------------------------------------------------ #
    def get_annotation(self, chat_message_id: int) -> Optional[dict]:
        """按 chat_messages.id 取单条标注。无则返回 None。"""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT * FROM nontext_annotations WHERE chat_message_id=?",
                (chat_message_id,),
            ).fetchone()
            return dict(row) if row else None
        except sqlite3.OperationalError as e:
            logger.warning("nontext_annotations 表不存在: %s", e)
            return None
        finally:
            con.close()

    # ------------------------------------------------------------------ #
    def get_annotations_by_type(self, person_id: str = "demo0001") -> dict:
        """按 media_type 聚合，返回 {media_type: {count, parsed, partial, failed}}。"""
        con = self._connect()
        out: dict[str, dict] = {}
        try:
            rows = con.execute(
                """SELECT a.media_type, a.parse_status, COUNT(*) c
                   FROM nontext_annotations a
                   JOIN chat_messages m ON m.id=a.chat_message_id
                   WHERE m.person_id=?
                   GROUP BY a.media_type, a.parse_status""",
                (person_id,),
            ).fetchall()
            for r in rows:
                mt = r["media_type"] or "other"
                d = out.setdefault(mt, {"count": 0, "parsed": 0, "partial": 0, "failed": 0})
                d[r["parse_status"]] = r["c"]
                d["count"] += r["c"]
            return out
        except sqlite3.OperationalError as e:
            logger.warning("nontext_annotations 表不存在: %s", e)
            return {}
        finally:
            con.close()

    # ------------------------------------------------------------------ #
    def get_coverage(self, person_id: str = "demo0001") -> dict:
        """总覆盖率 {total, parsed, partial, failed, pct_*}。"""
        con = self._connect()
        try:
            row = con.execute(
                """SELECT a.parse_status, COUNT(*) c
                   FROM nontext_annotations a
                   JOIN chat_messages m ON m.id=a.chat_message_id
                   WHERE m.person_id=?
                   GROUP BY a.parse_status""",
                (person_id,),
            ).fetchall()
            d = {"parsed": 0, "partial": 0, "failed": 0}
            for r in row:
                d[r["parse_status"]] = r["c"]
            total = sum(d.values())
            return {
                "total": total,
                "parsed": d["parsed"],
                "partial": d["partial"],
                "failed": d["failed"],
                "pct_parsed": round(100 * d["parsed"] / total, 1) if total else 0.0,
                "pct_partial": round(100 * d["partial"] / total, 1) if total else 0.0,
                "pct_failed": round(100 * d["failed"] / total, 1) if total else 0.0,
            }
        except sqlite3.OperationalError as e:
            logger.warning("nontext_annotations 表不存在: %s", e)
            return {"total": 0, "parsed": 0, "partial": 0, "failed": 0,
                    "pct_parsed": 0.0, "pct_partial": 0.0, "pct_failed": 0.0}
        finally:
            con.close()

    # ------------------------------------------------------------------ #
    def merge_emotion_hint(self, chat_message_id: int) -> dict:
        """返回可并入情感分析的补充信号。

        :return: {
            "has_annotation": bool,
            "text_content": str,     # 非文本里能提取的文本（引用正文/转账/语音转写等）
            "category": str,
            "emotion_hint": str,     # 情感提示
            "emotion_score": float|None,
            "confidence": float,
            "source": str,
        }
        无标注时返回 has_annotation=False 的空壳。
        """
        ann = self.get_annotation(chat_message_id)
        if not ann:
            return {"has_annotation": False, "text_content": "", "category": "",
                    "emotion_hint": "", "emotion_score": None,
                    "confidence": 0.0, "source": "none"}
        return {
            "has_annotation": True,
            "text_content": ann.get("text_content") or "",
            "category": ann.get("category") or "",
            "emotion_hint": ann.get("emotion_hint") or "",
            "emotion_score": ann.get("emotion_score"),
            "confidence": ann.get("confidence") or 0.0,
            "source": ann.get("source") or "none",
        }


# 模块级便捷单例
_singleton: Optional[NontextAnalysis] = None


def get_nontext_analysis() -> NontextAnalysis:
    global _singleton
    if _singleton is None:
        _singleton = NontextAnalysis()
    return _singleton


if __name__ == "__main__":
    import json
    na = NontextAnalysis()
    print("coverage:", json.dumps(na.get_coverage(), ensure_ascii=False))
    print("by_type:", json.dumps(na.get_annotations_by_type(), ensure_ascii=False, indent=2))
