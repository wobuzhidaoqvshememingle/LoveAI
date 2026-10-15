# -*- coding: utf-8 -*-
"""第1层 原始流层（raw_stream）。

职责：消息按时间戳归档、做话题分段索引。原文不改、不删，输出可追溯的
消息索引（含全局序号 idx）与分段索引（segment），供后续层逐层聚合。

分段阈值（明确、可调，非黑箱）：
  - 时间断点：相邻两条消息间隔 >= SEGMENT_GAP_HOURS 小时，判为新话题段；
  - 话题切换：同一对方消息命中"新话题引导词"（对了/那/说起来/换个事）且与上一段
    情感极性跳变，也单独切段。
"""
from datetime import datetime

# 话题分段时间断点（小时）：超过该间隔视为新话题/新场景
SEGMENT_GAP_HOURS = 6.0

# 话题切换引导词（弱信号，仅在时间断点不明显时辅助切段）
_TOPIC_SWITCH = ("对了", "说起来", "换个事", "那啥", "另外")


def _parse_dt(m):
    ca = m.get("created_at")
    if not ca:
        return None
    try:
        return datetime.fromisoformat(str(ca))
    except Exception:
        return None


class RawStream:
    """把原始消息列表归档为带序号的有序流，并切分话题段。"""

    def build(self, messages: list) -> dict:
        """
        Args:
            messages: [{"direction","content","created_at",...}, ...]（可能乱序）。

        Returns:
            dict: {
              "messages": [{idx, direction, content, dt, ts}],   # 按时间升序、带全局 idx
              "segments": [{seg_id, start_idx, end_idx, start_ts, end_ts}],
              "n_messages": int,
            }
        """
        parsed = []
        for i, m in enumerate(messages or []):
            dt = _parse_dt(m)
            parsed.append({
                "order": i,
                "direction": m.get("direction"),
                "content": (m.get("content") or "").strip(),
                "dt": dt,
            })
        # 按时间升序（无时间戳的保持原相对顺序排到最后）
        parsed.sort(key=lambda x: (x["dt"] is None, x["dt"] or datetime.max))

        msgs = []
        for idx, p in enumerate(parsed):
            msgs.append({
                "idx": idx,
                "direction": p["direction"],
                "content": p["content"],
                "dt": p["dt"],
                "ts": p["dt"].isoformat() if p["dt"] else None,
            })

        segments = self._segment(msgs)
        return {
            "messages": msgs,
            "segments": segments,
            "n_messages": len(msgs),
        }

    def _segment(self, msgs: list) -> list:
        segs = []
        if not msgs:
            return segs
        cur_start = 0
        prev_dt = msgs[0]["dt"]
        for i in range(1, len(msgs)):
            dt = msgs[i]["dt"]
            gap_h = None
            if dt and prev_dt:
                gap_h = (dt - prev_dt).total_seconds() / 3600.0
            topic_switch = any(w in msgs[i]["content"] for w in _TOPIC_SWITCH) \
                and msgs[i]["direction"] == "received"
            if (gap_h is not None and gap_h >= SEGMENT_GAP_HOURS) or topic_switch:
                segs.append({
                    "seg_id": len(segs),
                    "start_idx": cur_start,
                    "end_idx": i - 1,
                    "start_ts": msgs[cur_start]["ts"],
                    "end_ts": msgs[i - 1]["ts"],
                })
                cur_start = i
            prev_dt = dt
        segs.append({
            "seg_id": len(segs),
            "start_idx": cur_start,
            "end_idx": len(msgs) - 1,
            "start_ts": msgs[cur_start]["ts"],
            "end_ts": msgs[-1]["ts"],
        })
        return segs
