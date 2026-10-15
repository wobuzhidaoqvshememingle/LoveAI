# -*- coding: utf-8 -*-
"""
LoveAI - 基线意外度（Shannon Surprise / 香农信息量）回复特征

────────────────────────────────────────────────────────────────────────────
定位（与既有 no_reply_detector 的关系，勿重复造轮子）：
  no_reply_detector 已做"等待时长 / 历史平均回复时间"的倍数(ratio)分级，
  不是纯绝对值判断。本模块是其上的【增量特征】：把"这次行为有多意外"
  显式量化为香农信息量视角的意外度，并按【基线回复速度分型】做差分加权——
    * 秒回型基线 → 突然不回 = 高意外（高信息量，值得报警）；
    * 从不秒回/慢回型基线 → 同等时长不回 = 低意外（低信息量，本就如此）。
  同一绝对等待时长，在不同基线下信号强度应当不同；这正是"意外度"要补的。

核心公式：
  surprise_z = (当前等待 - 基线均值) / max(基线标准差, 抖动地板)
  - 这是 z-score 式偏离：偏离自身历史分布越远，意外度越高；
  - std 缺失/为0 时回退为相对偏离 (|w-mean|/max(mean,1))，不瞎猜；
  - 高意外阈值 = surprise_z >= 2（约 2 个标准差外）。

基线分型（由 mean 回复延迟决定，分钟）：
  秒回型    mean <= 5
  正常型    5 < mean <= 60
  慢回型    60 < mean <= 240
  沉默型    mean > 240（极少主动回，等他=常态）

────────────────────────────────────────────────────────────────────────────
【边界（必须遵守，与"回撤≠降频"同源）】
  1. 单次不归因：一次偏离不直接下"她冷淡了"结论，必须看【持续时长 +
     是否伴随其他信号】（长度变短/主动下降/冲突词）。本模块只给"意外度"
     权重，不单独触发任何惩罚/施压动作。
  2. 回避型/低安全感阶段：高意外度≠该追着问。对回避型或低安全感阶段，
     不回往往是其情绪调节方式，此时"问一句"策略不适用（会触发防御），
     应降频不施压——见 proactive_outreach 的门控。
  3. 数据不足（回复样本 < MIN_SAMPLES）不计算，返回 available=False，不瞎猜。
纯 Python，不 import PySide6，不写库；消息由调用方传入，便于单测。
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Optional

MIN_SAMPLES: int = 5          # 建立基线所需最小回复样本
SURPRISE_Z_HIGH: float = 2.0  # 高意外阈值（z >= 2 个标准差外）
_JITTER_FLOOR: float = 1.0     # std 地板（分钟），防除零


def classify_baseline_type(mean_lag_min: float) -> str:
    """按平均回复延迟把对方回复基线分型。"""
    if mean_lag_min <= 5:
        return "秒回型"
    if mean_lag_min <= 60:
        return "正常型"
    if mean_lag_min <= 240:
        return "慢回型"
    return "沉默型"


def surprise_z(waiting_min: float, mean: float, std: float) -> float:
    """当前等待相对基线的意外度（z 式偏离，可负=比平时还快/短）。"""
    if std and std > 0:
        return (waiting_min - mean) / std
    # std 缺失：回退相对偏离
    denom = max(mean, _JITTER_FLOOR)
    return (waiting_min - mean) / denom


def evaluate_reply_surprise(
    waiting_min: float,
    reply_lags_min: list[float],
) -> dict:
    """给定"当前已等待分钟数"与历史回复延迟样本，输出基线意外度特征。

    Args:
        waiting_min: 最后一条我方消息至今、对方未回的等待分钟数。
        reply_lags_min: 历史 sent->received 回复延迟样本（分钟）。

    Returns:
        dict: {available, baseline_type, mean, std, surprise_z,
               is_high_surprise, information_value, note}
    """
    lags = [x for x in (reply_lags_min or []) if x is not None and x >= 0]
    if len(lags) < MIN_SAMPLES:
        return {
            "available": False,
            "reason": f"回复样本不足({len(lags)}/{MIN_SAMPLES})，无法建立基线意外度",
            "waiting_min": waiting_min,
        }

    mean = statistics.fmean(lags)
    std = statistics.pstdev(lags) if len(lags) >= 2 else 0.0
    btype = classify_baseline_type(mean)
    z = surprise_z(waiting_min, mean, std)

    # 信息价值：只有"比基线明显更久"才算高意外；比基线还快/短=无意外信息
    is_high = (z >= SURPRISE_Z_HIGH) and (waiting_min > mean)
    # 差分语义：秒回型/正常型基线的高意外才有信息量；沉默型等他本就正常
    if btype == "沉默型":
        info = "low"   # 她本就极少秒回，同等等待=低意外
    elif is_high and btype in ("秒回型", "正常型"):
        info = "high"  # 平时秒回突然长时间不回=高意外
    elif is_high:
        info = "medium"
    else:
        info = "low"

    note = (
        f"基线={btype}(均值{mean:.0f}分钟/σ{std:.0f})，当前等待{waiting_min:.0f}分钟，"
        f"意外度z={z:.1f}，信息量={info}"
    )
    return {
        "available": True,
        "baseline_type": btype,
        "mean_min": round(mean, 2),
        "std_min": round(std, 2),
        "waiting_min": waiting_min,
        "surprise_z": round(z, 2),
        "is_high_surprise": is_high,
        "information_value": info,
        "note": note,
    }


# --------------------------------------------------------------------------- #
# 从消息序列直接算回复延迟样本（与 no_reply_detector 同口径，供上层接线复用）
# --------------------------------------------------------------------------- #
def extract_reply_lags(messages: list[dict]) -> list[float]:
    """从 [{direction, created_at}, ...] 抽 sent->received 回复延迟（分钟）。"""
    def _dt(s):
        if not s:
            return None
        s = str(s).strip().replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    seq = sorted(messages, key=lambda m: _dt(m.get("created_at")) or datetime.min)
    lags: list[float] = []
    for i in range(1, len(seq)):
        d0 = str(seq[i - 1].get("direction", "")).lower()
        d1 = str(seq[i].get("direction", "")).lower()
        if d0 == "sent" and d1 == "received":
            t0, t1 = _dt(seq[i - 1].get("created_at")), _dt(seq[i].get("created_at"))
            if t0 and t1 and t1 >= t0:
                lags.append((t1 - t0).total_seconds() / 60.0)
    return lags
