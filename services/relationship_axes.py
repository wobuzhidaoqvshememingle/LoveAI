# -*- coding: utf-8 -*-
"""
LoveAI v3.2.0 - 关系六轴评分引擎（Trust/Attraction/Comfort/Investment/Respect/Emotional）

v3.0 死代码复活（原 backups/dead_services/relationship_axes.py 1900B 版本）：
  - 保留 AXES / _DIM_TO_AXIS / _WEIGHTS / score_axes() 向后兼容入口；
  - 保留 18 维→6 轴映射 + 缺省 50 兜底。

v3.2.0 新增（借跨学科机制卡 M1-1/M1-2）：
  - AXIS_PROBES 表（M1-2 消费者信心指数探针加权）：6 轴 × 每轴 4-6 个探针，
    每个探针 = {probe_id, description, weight, detector_fn}。detector_fn 是
    纯规则函数（正则/关键词/回复时长阈值），不调 LLM。
  - SlidingWindowCalibrator 类（M1-1 HRV 滑动窗口功率谱）：
      window=20 条消息，每轴维护 EMA(α=0.15) + 置信区间（窗口内信号方差越大
      区间越宽），输出 (value 0-100, confidence 0-1) 二元组。

【未来方向（本轮不做）】
  - M1-3 地震走时层析成像（卡尔曼滤波融合探针观测）：本轮不上，登记为未来增强。
    原因：LoveAI 无"对话→轴值"的物理正演模型，正则化权重靠拍脑袋；恋爱对话
    探针分布明显偏态（大部分平淡、偶尔高情绪），高斯假设不成立，需换粒子滤波。

服务层纯 Python：不 import PySide6，不 import LLM gateway。
"""
from __future__ import annotations

import logging
import math
import re
import statistics
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

AXES = ("Trust", "Attraction", "Comfort", "Investment", "Respect", "Emotional")

# 维度→轴映射（18维近似归类；键名缺失时用默认值）—— 来自死代码，保留兼容
_DIM_TO_AXIS = {
    "trust": "Trust", "reliability": "Trust", "honesty": "Trust",
    "attraction": "Attraction", "passion": "Attraction", "chemistry": "Attraction",
    "comfort": "Comfort", "ease": "Comfort", "safety": "Comfort",
    "investment": "Investment", "effort": "Investment", "sacrifice": "Investment",
    "respect": "Respect", "equality": "Respect", "recognition": "Respect",
    "emotional": "Emotional", "intimacy": "Emotional", "bonding": "Emotional",
}

# 六轴权重（加权平均得总分）—— 来自死代码，保留兼容
_WEIGHTS = {"Trust": 0.25, "Attraction": 0.2, "Comfort": 0.15,
            "Investment": 0.15, "Respect": 0.1, "Emotional": 0.15}


# ====================================================================== #
# 兼容入口：18维 dict → 六轴聚合（死代码原样保留）
# ====================================================================== #
def score_axes(dimensions: Optional[dict]) -> dict:
    """维度dict → 六轴分数 + 加权总分（向后兼容入口）。"""
    dimensions = dimensions or {}
    buckets = {a: [] for a in AXES}
    for dim, val in dimensions.items():
        axis = _DIM_TO_AXIS.get(dim.lower())
        if axis:
            try:
                buckets[axis].append(max(0.0, min(100.0, float(val))))
            except Exception:
                pass
    axes = {}
    for a in AXES:
        vals = buckets[a]
        axes[a] = round(sum(vals) / len(vals), 1) if vals else 50.0
    total = round(sum(axes[a] * _WEIGHTS[a] for a in AXES), 1)
    return {"axes": axes, "weighted_total": total, "weights": dict(_WEIGHTS)}


# ====================================================================== #
# M1-2：探针 detector 纯规则函数库（输入消息 dict，输出 0/1/2 分）
# 消息 dict 约定：
#   {"content": str, "direction": "incoming"|"outgoing",
#    "reply_sec": float|None  # 距上一条回复秒数}
# ====================================================================== #
def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


# ---- Trust 信任 ---- #
_TRUST_NICKNAME = ("宝宝", "宝贝", "亲爱的", "老婆", "老公", "猪猪", "乖乖", "亲亲")
_TRUST_COMMIT = ("我答应", "我保证", "下次一定", "说好了", "我会的", "相信我", "我说到做到")
_TRUST_REPORT = ("我到公司了", "我到家了", "我现在在", "刚开完会", "跟你说个事", "跟你汇报")


def _det_trust_nickname(m: dict) -> float:
    return 2.0 if _has_any(m.get("content", ""), _TRUST_NICKNAME) else 0.0


def _det_trust_commit(m: dict) -> float:
    return 2.0 if _has_any(m.get("content", ""), _TRUST_COMMIT) else 0.0


def _det_trust_report(m: dict) -> float:
    return 1.0 if _has_any(m.get("content", ""), _TRUST_REPORT) else 0.0


def _det_trust_ask_for_help(m: dict) -> float:
    txt = m.get("content", "")
    return 1.0 if ("帮我" in txt or "请教" in txt or "你觉得呢" in txt) else 0.0


# ---- Attraction 吸引 ---- #
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]")
_NIGHT_HOUR_RE = None  # 由消息时间字段判断，这里先做内容探针
_ATTRACT_INITIATIVE = ("在干嘛", "干嘛呢", "忙什么", "想起你", "突然想")
_ATTRACT_DEEP_TALK = ("其实我", "跟你说真的", "我心里", "跟你聊聊", "跟你说说")


def _det_attract_emoji(m: dict) -> float:
    n = len(_EMOJI_RE.findall(m.get("content", "")))
    return 2.0 if n >= 3 else (1.0 if n == 1 else 0.0)


def _det_attract_initiative(m: dict) -> float:
    return 1.0 if _has_any(m.get("content", ""), _ATTRACT_INITIATIVE) else 0.0


def _det_attract_deeptalk(m: dict) -> float:
    return 2.0 if _has_any(m.get("content", ""), _ATTRACT_DEEP_TALK) else 0.0


def _det_attract_invite(m: dict) -> float:
    txt = m.get("content", "")
    return 2.0 if ("出来玩" in txt or "约你" in txt or "一起吃饭" in txt or "看电影" in txt) else 0.0


# ---- Comfort 舒适 ---- #
_COMFORT_FILLER = ("嗯", "哦", "噢", "额", "哈哈", "呵呵", "哈哈哈哈", "嘿嘿")
_COMFORT_RANT = ("烦死", "气死", "好累", "倒霉", "无语", "真服了", "不想动")


def _det_comfort_filler(m: dict) -> float:
    txt = m.get("content", "").strip()
    return 1.0 if txt in _COMFORT_FILLER or len(txt) <= 3 else 0.0


def _det_comfort_rant(m: dict) -> float:
    return 1.0 if _has_any(m.get("content", ""), _COMFORT_RANT) else 0.0


def _det_comfort_reply_quick(m: dict) -> float:
    rs = m.get("reply_sec")
    if rs is None:
        return 0.0
    return 2.0 if rs <= 60 else (1.0 if rs <= 300 else 0.0)


def _det_comfort_no_silence(m: dict) -> float:
    # 超过 4 小时才回 → 扣舒适度（本探针只记 0/正，负值由 calibrator 平滑）
    rs = m.get("reply_sec")
    if rs is None:
        return 0.0
    return 0.0 if rs <= 14400 else -1.0


# ---- Investment 投入 ---- #
_INVEST_REMEMBER = ("上次你说", "你之前说", "你上次", "我记得你", "你上次那个")
_INVEST_GIFT = ("送你", "给你买", "请你吃", "给你带", "礼物")


def _det_invest_length(m: dict) -> float:
    L = len(m.get("content", "") or "")
    return 2.0 if L >= 80 else (1.0 if L >= 30 else 0.0)


def _det_invest_remember(m: dict) -> float:
    return 2.0 if _has_any(m.get("content", ""), _INVEST_REMEMBER) else 0.0


def _det_invest_gift(m: dict) -> float:
    return 1.0 if _has_any(m.get("content", ""), _INVEST_GIFT) else 0.0


def _det_invest_reply_persistent(m: dict) -> float:
    # 回复时长适中（2-30 分钟）= 认真组织语言
    rs = m.get("reply_sec")
    if rs is None:
        return 0.0
    return 1.0 if 120 <= rs <= 1800 else 0.0


# ---- Respect 尊重 ---- #
_RESPECT_LISTEN = ("你说", "你先说", "听你", "你觉得", "你怎么看", "你的想法")
_RESPECT_PUBLIC = ("我们朋友", "我对象", "我女朋友", "我男朋友", "带你认识")


def _det_respect_listen(m: dict) -> float:
    return 1.0 if _has_any(m.get("content", ""), _RESPECT_LISTEN) else 0.0


def _det_respect_apology(m: dict) -> float:
    return 2.0 if ("对不起" in m.get("content", "") or "我错了" in m.get("content", "")) else 0.0


def _det_respect_public_claim(m: dict) -> float:
    return 2.0 if _has_any(m.get("content", ""), _RESPECT_PUBLIC) else 0.0


def _det_respect_no_interrupt(m: dict) -> float:
    # 对方连续两条我们只回一条 → 不打扰 = 1 分（简化：消息长度适中）
    L = len(m.get("content", "") or "")
    return 1.0 if 20 <= L <= 200 else 0.0


# ---- Emotional 情感 ---- #
_EMO_WORD = ("想你", "喜欢你", "爱你", "在乎你", "心疼", "挂念", "梦见你", "离不开")
_EMO_VULNERABLE = ("我其实", "我害怕", "我没安全感", "我担心", "我自卑", "我脆弱")
_EMO_FUTURE = ("以后我们", "将来", "明年", "下次我们一起", "我们去", "我们的家")


def _det_emo_affect(m: dict) -> float:
    return 2.0 if _has_any(m.get("content", ""), _EMO_WORD) else 0.0


def _det_emo_vulnerable(m: dict) -> float:
    return 2.0 if _has_any(m.get("content", ""), _EMO_VULNERABLE) else 0.0


def _det_emo_future(m: dict) -> float:
    return 2.0 if _has_any(m.get("content", ""), _EMO_FUTURE) else 0.0


def _det_emo_goodnight(m: dict) -> float:
    txt = m.get("content", "")
    return 1.0 if ("晚安" in txt or "好梦" in txt or "睡吧" in txt) else 0.0


# ====================================================================== #
# AXIS_PROBES 表（M1-2）
# ====================================================================== #
AXIS_PROBES: dict[str, list[dict]] = {
    "Trust": [
        {"probe_id": "trust.nickname", "description": "亲密称呼使用", "weight": 1.0, "detector": _det_trust_nickname},
        {"probe_id": "trust.commit",    "description": "承诺/担保词", "weight": 1.2, "detector": _det_trust_commit},
        {"probe_id": "trust.report",    "description": "主动汇报行踪", "weight": 0.8, "detector": _det_trust_report},
        {"probe_id": "trust.askhelp",   "description": "主动求助/征求意见", "weight": 0.6, "detector": _det_trust_ask_for_help},
    ],
    "Attraction": [
        {"probe_id": "attract.emoji",     "description": "emoji/表情密度", "weight": 0.8, "detector": _det_attract_emoji},
        {"probe_id": "attract.initiative","description": "主动发起话题", "weight": 1.0, "detector": _det_attract_initiative},
        {"probe_id": "attract.deeptalk",  "description": "深度话题袒露", "weight": 1.2, "detector": _det_attract_deeptalk},
        {"probe_id": "attract.invite",    "description": "主动邀约", "weight": 1.2, "detector": _det_attract_invite},
    ],
    "Comfort": [
        {"probe_id": "comfort.filler",   "description": "语气词/短回复（松弛感）", "weight": 0.6, "detector": _det_comfort_filler},
        {"probe_id": "comfort.rant",     "description": "吐槽/抱怨分享", "weight": 0.8, "detector": _det_comfort_rant},
        {"probe_id": "comfort.quick",    "description": "回复速度 ≤1/5 分钟", "weight": 1.0, "detector": _det_comfort_reply_quick},
        {"probe_id": "comfort.long_silence", "description": "回复 >4h（负向探针）", "weight": -0.8, "detector": _det_comfort_no_silence},
    ],
    "Investment": [
        {"probe_id": "invest.length",     "description": "回复字数", "weight": 0.8, "detector": _det_invest_length},
        {"probe_id": "invest.remember",   "description": "记住细节/旧事", "weight": 1.2, "detector": _det_invest_remember},
        {"probe_id": "invest.gift",        "description": "请客/送礼词", "weight": 0.8, "detector": _det_invest_gift},
        {"probe_id": "invest.persistent", "description": "回复组织时长适中", "weight": 0.6, "detector": _det_invest_reply_persistent},
    ],
    "Respect": [
        {"probe_id": "respect.listen",   "description": "倾听/征询对方意见", "weight": 0.8, "detector": _det_respect_listen},
        {"probe_id": "respect.apology",   "description": "主动道歉", "weight": 1.0, "detector": _det_respect_apology},
        {"probe_id": "respect.public",    "description": "公开关系宣称", "weight": 1.2, "detector": _det_respect_public_claim},
        {"probe_id": "respect.noninterrupt", "description": "不抢话/适中长度", "weight": 0.6, "detector": _det_respect_no_interrupt},
    ],
    "Emotional": [
        {"probe_id": "emo.affect",     "description": "情感词直接表达", "weight": 1.2, "detector": _det_emo_affect},
        {"probe_id": "emo.vulnerable", "description": "脆弱/不安全感袒露", "weight": 1.0, "detector": _det_emo_vulnerable},
        {"probe_id": "emo.future",     "description": "未来共同规划词", "weight": 1.0, "detector": _det_emo_future},
        {"probe_id": "emo.goodnight",  "description": "晚安/关怀收尾", "weight": 0.6, "detector": _det_emo_goodnight},
    ],
}


# ====================================================================== #
# M1-1：SlidingWindowCalibrator
# ====================================================================== #
class SlidingWindowCalibrator:
    """对每条消息跑探针，按窗口 EMA 平滑六轴值。

    - window=20 条消息；
    - 每轴 EMA(α=0.15)：新值 = (1-α)*old + α*probe_score;
    - 置信区间：窗口内信号方差越大，confidence 越低；
    - 输出 {axis: {"value": 0-100, "confidence": 0-1, "evidence": int(probe命中数)}}。
    """

    DEFAULT_WINDOW: int = 20
    EMA_ALPHA: float = 0.15
    # 探针单条最大得分（4 探针 × 2 分 = 8；归一化到 0-100 用此常数）
    MAX_PROBE_SUM: float = 8.0

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self.window: int = int(window)
        # 每轴当前 EMA 值（0-100），初始化为中性 50
        self._values: dict[str, float] = {a: 50.0 for a in AXES}
        # 每轴最近窗口内的原始探针分（用于方差→置信区间）
        self._raw: dict[str, list[float]] = {a: [] for a in AXES}

    # ---- 单条消息打分 ---- #
    def score_message(self, msg: dict) -> dict[str, float]:
        """跑一条消息的全部探针，返回 {axis: probe_score(-8..+8)}。"""
        out: dict[str, float] = {a: 0.0 for a in AXES}
        for axis, probes in AXIS_PROBES.items():
            s = 0.0
            for p in probes:
                try:
                    raw = float(p["detector"](msg) or 0.0)
                except Exception:
                    raw = 0.0
                s += raw * p["weight"]
            out[axis] = s
        return out

    # ---- 喂消息进窗口，更新 EMA ---- #
    def update(self, msg: dict) -> dict[str, float]:
        """喂一条消息，返回每轴新的 0-100 平滑值。"""
        raw_scores = self.score_message(msg)
        for axis in AXES:
            r = raw_scores[axis]
            self._raw[axis].append(r)
            if len(self._raw[axis]) > self.window:
                self._raw[axis] = self._raw[axis][-self.window:]
            # raw 范围约 [-8, +8]，映射到 [-50, +50] 偏移量，叠加在 50 中性基准上
            delta = max(-self.MAX_PROBE_SUM, min(self.MAX_PROBE_SUM, r))
            target = 50.0 + (delta / self.MAX_PROBE_SUM) * 50.0
            # EMA 更新
            self._values[axis] = (1.0 - self.EMA_ALPHA) * self._values[axis] + self.EMA_ALPHA * target
        return dict(self._values)

    # ---- 当前状态 + 置信区间 ---- #
    def current(self) -> dict:
        """输出六轴当前值 + 置信度。"""
        out: dict[str, dict] = {}
        for axis in AXES:
            vals = self._raw[axis]
            if len(vals) >= 2:
                var = statistics.pvariance(vals)
                # 方差越大 → 置信区间越宽 → confidence 越低
                # 方差范围理论上 0 ~ ~16，归一化到 0~1
                conf = max(0.15, min(1.0, 1.0 - var / 16.0))
            else:
                conf = 0.3  # 冷启动数据不足，置信度低
            v = self._values[axis]
            out[axis] = {
                "value": round(max(0.0, min(100.0, v)), 1),
                "confidence": round(conf, 2),
                "samples": len(vals),
            }
        return out

    # ---- 兼容老接口：把 current() 包成 score_axes 同形 dict ---- #
    def as_score_axes(self) -> dict:
        cur = self.current()
        axes_v = {a: cur[a]["value"] for a in AXES}
        total = round(sum(axes_v[a] * _WEIGHTS[a] for a in AXES), 1)
        return {
            "axes": axes_v,
            "weighted_total": total,
            "weights": dict(_WEIGHTS),
            "by_axis_detail": cur,
        }


# ====================================================================== #
# 模块级便捷函数（person_state_snapshot.py 接线用）
# ====================================================================== #
_default_calibrator: Optional[SlidingWindowCalibrator] = None


def get_default_calibrator() -> SlidingWindowCalibrator:
    """进程内默认校准器单例（避免重复建窗口）。"""
    global _default_calibrator
    if _default_calibrator is None:
        _default_calibrator = SlidingWindowCalibrator()
    return _default_calibrator


def score_axes_with_probes(recent_messages: list[dict]) -> dict:
    """便捷入口：喂最近 N 条消息，返回六轴 + 置信度。

    recent_messages 每条约定见 score_message()。
    冷启动（消息为空）返回中性 50 + 低置信度。
    """
    cal = get_default_calibrator()
    for m in recent_messages or []:
        try:
            cal.update(m)
        except Exception as e:
            logger.debug("relationship_axes 单条消息打分失败: %s", e)
    return cal.as_score_axes()
