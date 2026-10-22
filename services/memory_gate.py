# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - R3.3-34 记忆闸门（校准数据去噪）

职责：
  calibration_runner 回灌画像 / 写入记忆前，先过本闸门做去噪。
  不做语义判断，只做规则化过滤与分层路由：

    a) 太短 / 纯语气词（<4 字，或"嗯/哦/好的/哈哈"）→ 丢弃
    b) 同 speaker 24h 内同主题词重复 ≥3 次 → 去重，保留最新
    c) 高情绪爆发期（脏话/嘲讽）→ 打 noisy=True，路由到 summary 层（不进 vector）
    d) source="unknown"（无法确认 speaker）→ 降权写入 world_info 层（不进 vector）
    e) confidence < 0.3 → 不写入，只落 logs/memory_gate_rejected.log

设计原则与 deception_detector 一致：不做有罪推定。
  - 高情绪原话不丢弃，只是降级到 summary 层（避免污染向量召回）；
  - 无法确认 speaker 的条目不丢弃，只是降权到 world_info；
  - 只有 a/e 两类硬规则才真正丢弃。

纯 Python 服务层，不依赖 PySide6 / 数据库。
日志默认写到项目根 logs/memory_gate_rejected.log，可通过构造参数覆盖。
"""
from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 规则常量
# --------------------------------------------------------------------------- #
#: a) 最短有效字数
MIN_TEXT_LEN: int = 4

#: a) 纯语气词 / 无信息量短词
INTERJECTIONS: frozenset[str] = frozenset({
    "嗯", "嗯嗯", "哦", "噢", "喔", "额", "呃", "啊", "哈", "哈哈", "哈哈哈",
    "好", "好的", "好吧", "好呀", "行", "行吧", "可以", "ok", "OK", "Ok",
    "是", "是的", "对", "对的", "没错", "不是", "没有", "没事",
    "呵呵", "嘿嘿", "嘻嘻", "诶", "唉", "唉唉",
})

#: c) 高情绪/脏话词（命中即打 noisy，降级到 summary 层）
HIGH_EMOTION_WORDS: tuple[str, ...] = (
    "操", "草", "滚", "傻逼", "傻x", "煞笔", "他妈", "他妈的", "靠",
    "艹", "妈的", "废物", "去死", "神经病", "有病", "脑残", "垃圾",
)

#: e) confidence 硬阈值
MIN_CONFIDENCE: float = 0.3

#: b) 同 speaker 24h 内同主题重复次数阈值
DEDUP_REPEAT_LIMIT: int = 3

#: 24h 窗口
DEDUP_WINDOW_HOURS: int = 24

#: 合法目标层
VALID_TARGET_LAYERS: tuple[str, ...] = ("summary", "vector", "world_info", "authors_note")


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class AcceptedItem:
    """通过闸门的条目。"""

    raw: dict[str, Any]
    target_layer: str
    denoised_text: str
    noisy: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "target_layer": self.target_layer,
            "denoised_text": self.denoised_text,
            "noisy": self.noisy,
        }


@dataclass
class RejectedItem:
    """被闸门拒绝的条目。"""

    raw: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"raw": self.raw, "reason": self.reason}


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
_PUNCT_RE = re.compile(r"[\s，。！？、,.!?~～…—\-_「」『』《》()（）\[\]【】\"'`]+")


def _normalize_text(text: Any) -> str:
    """去标点/空白，用于相似度与主题签名。非 str 输入容错转 str（不崩）。"""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    return _PUNCT_RE.sub("", text)


def _topic_signature(text: str) -> str:
    """提取主题签名：归一化后取前 10 个字符（足够区分"她喜欢猫"vs"她讨厌狗"）。"""
    norm = _normalize_text(text)
    return norm[:10]


def _bigrams(text: str) -> set[str]:
    """提取中文二元组（2-gram），用于跨句相似度。"""
    norm = _normalize_text(text)
    if len(norm) < 2:
        return set()
    return {norm[i:i + 2] for i in range(len(norm) - 1)}


def _parse_ts(ts: Any) -> Optional[datetime]:
    """容错解析 timestamp：支持 ISO str / epoch float / int / datetime。"""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, (int, float)):
        try:
            # 秒级 / 毫秒级都兼容
            v = float(ts)
            if v > 1e12:
                v = v / 1000.0
            return datetime.fromtimestamp(v)
        except Exception:  # noqa: BLE001
            return None
    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(s[: len(fmt) + 2], fmt) if False else datetime.fromisoformat(s)
            except Exception:  # noqa: BLE001
                continue
        try:
            return datetime.fromisoformat(s)
        except Exception:  # noqa: BLE001
            return None
    return None


def _has_high_emotion(text: str) -> bool:
    return any(w in text for w in HIGH_EMOTION_WORDS)


# --------------------------------------------------------------------------- #
# 单条闸门
# --------------------------------------------------------------------------- #
def gate_calibration_item(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """对单条校准原始条目过闸门。

    Args:
        raw: dict，期望含 text / source / confidence / timestamp / speaker。
             缺字段时按最保守处理（不崩）。

    Returns:
        - 通过时返回 dict: {target_layer, denoised_text, noisy}
        - 拒绝时返回 None（原因由调用方/process_batch 记录）

    规则顺序：
        e) confidence < 0.3      → None（低置信）
        a) 太短/语气词            → None（无信息量）
        c) 高情绪                → noisy=True, layer=summary
        d) source=unknown        → layer=world_info
        默认                     → layer=vector
    """
    if not isinstance(raw, dict):
        return None

    raw_text = raw.get("text")
    if raw_text is None:
        text = ""
    elif isinstance(raw_text, str):
        text = raw_text.strip()
    else:
        # 非 str（int/list/dict）容错：转成字符串再判，绝不因字段类型不规范崩溃
        text = str(raw_text).strip()
    source = (raw.get("source") or "unknown").strip()
    confidence = raw.get("confidence")
    try:
        conf_val = float(confidence) if confidence is not None else 1.0
    except (TypeError, ValueError):
        conf_val = 1.0

    # e) 低置信硬拒绝
    if conf_val < MIN_CONFIDENCE:
        logger.info("memory_gate reject: low_confidence conf=%.2f text=%r",
                    conf_val, text[:30])
        return None

    # a) 太短 / 纯语气词硬拒绝
    if len(text) < MIN_TEXT_LEN or text in INTERJECTIONS:
        logger.info("memory_gate reject: too_short text=%r", text[:30])
        return None
    # 去标点后仍过短（"好的。"这种）也算无信息量
    if len(_normalize_text(text)) < MIN_TEXT_LEN:
        logger.info("memory_gate reject: too_short_after_norm text=%r", text[:30])
        return None

    # 路由目标层
    noisy = False
    if _has_high_emotion(text):
        # c) 高情绪：不丢，但降级到 summary，不进 vector
        noisy = True
        target_layer = "summary"
    elif source == "unknown":
        # d) 无法确认 speaker：降权到 world_info
        target_layer = "world_info"
    else:
        target_layer = "vector"

    return {
        "target_layer": target_layer,
        "denoised_text": text,
        "noisy": noisy,
    }


# --------------------------------------------------------------------------- #
# 批量闸门（含去重）
# --------------------------------------------------------------------------- #
class MemoryGate:
    """校准数据批量闸门：逐条过 gate_calibration_item + 跨条去重。

    Args:
        rejected_log_path: 被拒条目落盘日志路径；默认 <项目根>/logs/memory_gate_rejected.log。
    """

    def __init__(self, rejected_log_path: Optional[str] = None) -> None:
        if rejected_log_path is None:
            # 默认写到项目根 logs/ 下
            here = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(here)
            rejected_log_path = os.path.join(project_root, "logs", "memory_gate_rejected.log")
        self.rejected_log_path = rejected_log_path

    # ------------------------------------------------------------------ #
    def process_batch(
        self, items: list[dict[str, Any]]
    ) -> tuple[list[AcceptedItem], list[RejectedItem]]:
        """批量处理：返回 (accepted, rejected)。

        b) 去重规则：同 speaker 在 DEDUP_WINDOW_HOURS 内同主题签名出现
           ≥ DEDUP_REPEAT_LIMIT 次时，仅保留最新一条，其余作为重复项 reject。
        """
        accepted: list[AcceptedItem] = []
        rejected: list[RejectedItem] = []

        # 第一遍：单条闸门
        interim: list[tuple[int, dict[str, Any], Optional[dict[str, Any]]]] = []
        for idx, raw in enumerate(items):
            verdict = gate_calibration_item(raw)
            interim.append((idx, raw, verdict))
            if verdict is None:
                # 判定具体原因（用于日志）
                reason = self._classify_reject_reason(raw)
                rejected.append(RejectedItem(raw=raw, reason=reason))

        # 第二遍：跨条去重（仅对通过单条闸门的候选做）
        # 按 speaker 分组，统计每个 2-gram 出现在哪些候选里。
        # 若某个 2-gram 在 ≥ DEDUP_REPEAT_LIMIT 条候选中出现，则这些候选
        # 视为"同主题重复"，仅保留最新一条。
        candidates: list[tuple[int, dict[str, Any], set[str]]] = []
        for idx, raw, verdict in interim:
            if verdict is None:
                continue
            bg = _bigrams(raw.get("text") or "")
            candidates.append((idx, raw, bg))

        # speaker -> list of (idx, ts, bigrams)
        by_speaker: dict[str, list[tuple[int, Optional[datetime], set[str]]]] = defaultdict(list)
        for idx, raw, bg in candidates:
            speaker = str(raw.get("speaker") or "unknown")
            ts = _parse_ts(raw.get("timestamp"))
            by_speaker[speaker].append((idx, ts, bg))

        drop_idx: set[int] = set()
        for speaker, items in by_speaker.items():
            if len(items) < DEDUP_REPEAT_LIMIT:
                continue
            # 对每个 2-gram，找出包含它的候选 idx 列表
            bg_to_idxs: dict[str, list[int]] = defaultdict(list)
            for idx, _ts, bg in items:
                for gram in bg:
                    bg_to_idxs[gram].append(idx)
            # 收集所有"被某个热门 2-gram 覆盖"的 idx
            covered: dict[int, list[Optional[datetime]]] = defaultdict(list)
            for gram, idxs in bg_to_idxs.items():
                if len(set(idxs)) < DEDUP_REPEAT_LIMIT:
                    continue
                # 这些 idx 视为同主题重复组
                ts_map = {idx: ts for idx, ts, _ in items}
                group_idxs = sorted(set(idxs), key=lambda i: (ts_map[i] is None,
                                                              ts_map[i] or datetime.min))
                # 24h 窗口过滤
                ref_ts = ts_map[group_idxs[-1]]
                if ref_ts is None:
                    windowed = group_idxs
                else:
                    windowed = [i for i in group_idxs
                                if ts_map[i] is None or (ref_ts - ts_map[i]) <= timedelta(hours=DEDUP_WINDOW_HOURS)]
                if len(windowed) >= DEDUP_REPEAT_LIMIT:
                    keep = windowed[-1]
                    for i in windowed[:-1]:
                        drop_idx.add(i)

        # 组装 accepted
        for idx, raw, verdict in interim:
            if verdict is None:
                continue
            if idx in drop_idx:
                rejected.append(RejectedItem(raw=raw, reason="duplicate_within_24h"))
                continue
            accepted.append(AcceptedItem(
                raw=raw,
                target_layer=verdict["target_layer"],
                denoised_text=verdict["denoised_text"],
                noisy=verdict["noisy"],
            ))

        # 落盘被拒日志
        if rejected:
            self._flush_rejected_log(rejected)

        return accepted, rejected

    # ------------------------------------------------------------------ #
    @staticmethod
    def _classify_reject_reason(raw: Any) -> str:
        """单条被拒时，给出机器可读的原因标签。非 dict 输入归为 invalid_item。"""
        if not isinstance(raw, dict):
            return "invalid_item"
        text = (raw.get("text") or "").strip()
        try:
            conf_val = float(raw.get("confidence")) if raw.get("confidence") is not None else 1.0
        except (TypeError, ValueError):
            conf_val = 1.0
        if conf_val < MIN_CONFIDENCE:
            return "low_confidence"
        if len(text) < MIN_TEXT_LEN or text in INTERJECTIONS:
            return "too_short_or_interjection"
        if len(_normalize_text(text)) < MIN_TEXT_LEN:
            return "too_short_after_norm"
        return "unknown"

    # ------------------------------------------------------------------ #
    def _flush_rejected_log(self, rejected: list[RejectedItem]) -> None:
        """把被拒条目追加写入日志文件。"""
        try:
            os.makedirs(os.path.dirname(self.rejected_log_path), exist_ok=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            now = datetime.now().isoformat(timespec="seconds")
            with open(self.rejected_log_path, "a", encoding="utf-8") as f:
                for r in rejected:
                    raw = r.raw if isinstance(r.raw, dict) else {}
                    text = (raw.get("text") or "")[:80]
                    f.write(
                        f"[{now}] reason={r.reason} "
                        f"speaker={raw.get('speaker')} "
                        f"source={raw.get('source')} "
                        f"conf={raw.get('confidence')} "
                        f"text={text!r}\n"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning("写 rejected log 失败: %s", e)


__all__ = [
    "gate_calibration_item",
    "MemoryGate",
    "AcceptedItem",
    "RejectedItem",
    "MIN_TEXT_LEN",
    "MIN_CONFIDENCE",
    "INTERJECTIONS",
    "HIGH_EMOTION_WORDS",
    "DEDUP_REPEAT_LIMIT",
]
