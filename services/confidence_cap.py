# -*- coding: utf-8 -*-
"""services/confidence_cap.py — P-01 证据量→置信上限硬封顶 + P-04 高压降档策略。

本模块是【纯函数 / 纯策略】层：
- 不连网、不加载模型、不碰数据库、不导入 PySide6；
- 所有数值均为冷启动先验默认值，随 T 闭环校准；UI 需自行区分"默认 / 已校准"。

== P-01（证据量 → 置信上限硬封顶，补 R/T）==
同场景【有效证据 n_eff】（时间衰减后）映射置信上限：
  - n_eff <  5 ：仅"假设"，不驱动策略、不展示概率档；
  - 5  ~ 9    ：上限 ≤ 45%；
  - 10 ~ 29   ：上限 ≤ 60%；
  - 30 ~ 99   ：上限 ≤ 80%；
  - ≥100 且校准达标：不封顶，以 T 实测命中率为准。
命中 P-04 降档情形时，在原档基础上【再降一档】。

== P-04（样本外 / 高压强制降档 与 先验切换，补 E/R/T）==
- 降档白名单触发：冲突升级 / 高情绪唤醒 / 冷战后重启 / 场景新颖度超阈 / 分布突变；
- 动作：置信强制降档 + 标准披露措辞；禁用"一定/肯定/他不会"等确定性语言；
- 剧变期切换：个体历史分布临时切回人群先验 + 强降档；
- 非典型个体：最初约 50 条消息偏离人群先验过大时，放宽收缩 κ 并整体降档。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

# ====================================================================== #
# P-01：时间衰减 → 有效证据量 n_eff
# ====================================================================== #
# 半衰期（天）：冷启动先验，旧证据按指数衰减。w = 0.5 ** (age_days / half_life)
DEFAULT_HALF_LIFE_DAYS = 90.0


def time_decay_weight(age_days: float, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    """单条证据经时间衰减后的权重（0<w<=1）。

    Args:
        age_days: 证据距今天数（>=0）。
        half_life_days: 半衰期，默认 90 天。

    Returns:
        float: 衰减权重，夹在 (0, 1]。
    """
    age = max(0.0, float(age_days))
    hl = max(1e-6, float(half_life_days))
    return 0.5 ** (age / hl)


def effective_n(weights: List[float]) -> float:
    """把逐条证据的衰减权重求和，得时间衰减后的有效证据量 n_eff。

    例：10 条证据全部很新 → n_eff≈10；半数已过一个半衰期 → n_eff≈7.5。
    """
    if not weights:
        return 0.0
    s = sum(max(0.0, float(w)) for w in weights)
    return max(0.0, s)


# ====================================================================== #
# P-01：置信档阶梯（low -> high）
# ====================================================================== #
# 每档对应的置信上沿（用于"再降一档"时把置信也压到该档上沿）。
BAND_ORDER: List[str] = ["假设", "很低", "偏低", "中等", "较高", "很高"]
BAND_CEILING: Dict[str, float] = {
    "假设": 0.0,    # 不展示概率档
    "很低": 0.20,
    "偏低": 0.45,
    "中等": 0.60,
    "较高": 0.80,
    "很高": 1.00,
}

# n_eff → 允许达到的最高档（硬封顶）。
# n_eff<5 只能停在"假设"；>=100 且校准达标才可触及"很高"。
def _max_band_for_neff(n_eff: float, calibrated: bool) -> str:
    n = float(n_eff)
    if n < 5:
        return "假设"
    if n < 10:      # 5-9
        return "偏低"     # 上限 0.45
    if n < 30:      # 10-29
        return "中等"     # 上限 0.60
    if n < 100:     # 30-99
        return "较高"     # 上限 0.80
    # >=100：仅在【校准达标】时不封顶（可到"很高"）；否则仍压在"较高"
    return "很高" if calibrated else "较高"


def ceiling_for_neff(n_eff: float, calibrated: bool = False) -> Dict[str, Any]:
    """P-01 核心映射：n_eff → 该场景允许的置信上限。

    Returns:
        dict: {
          ceiling: float  允许的最大置信（<=1）；n_eff<5 时为 0（不展示概率）；
          band: str       允许达到的最高档；
          drives_strategy: bool  n_eff<5 时为 False（不驱动策略）；
          show_probability: bool n_eff<5 时为 False（不展示概率档）；
        }
    """
    n = float(n_eff)
    max_band = _max_band_for_neff(n, calibrated)
    if n < 5:
        return {
            "ceiling": 0.0,
            "band": "假设",
            "drives_strategy": False,
            "show_probability": False,
        }
    return {
        "ceiling": BAND_CEILING[max_band],
        "band": max_band,
        "drives_strategy": True,
        "show_probability": True,
    }


def band_of(confidence: float) -> str:
    """把一个 0-1 置信值映射到档位。"""
    c = min(max(float(confidence), 0.0), 1.0)
    if c <= 0.0:
        return "假设"
    if c <= 0.20:
        return "很低"
    if c <= 0.45:
        return "偏低"
    if c <= 0.60:
        return "中等"
    if c <= 0.80:
        return "较高"
    return "很高"


def downgrade_band(band: str) -> str:
    """在档位阶梯上【再降一档】，最低落到"假设"。"""
    if band not in BAND_ORDER:
        band = "假设"
    idx = BAND_ORDER.index(band)
    return BAND_ORDER[max(0, idx - 1)]


def apply_evidence_cap(raw_confidence: float, n_eff: float,
                      calibrated: bool = False,
                      downgraded: bool = False) -> Dict[str, Any]:
    """P-01 硬封顶主入口（纯函数）。

    Args:
        raw_confidence: 模型/规则给出的原始置信（0-1）。
        n_eff: 时间衰减后的有效证据量。
        calibrated: 是否已通过 P-02 校准达标（ECE<=0.10 稳态线）。
        downgraded: 是否命中 P-04 降档情形（在原档基础上再降一档）。

    Returns:
        dict: {
          raw, capped, ceiling, band,
          drives_strategy, show_probability, downgraded, note
        }
    """
    raw = min(max(float(raw_confidence), 0.0), 1.0)
    rule = ceiling_for_neff(n_eff, calibrated)

    # 1) n_eff<5：直接锁死为"假设"，不驱动策略、不展示概率
    if float(n_eff) < 5:
        return {
            "raw": raw,
            "capped": 0.0,
            "ceiling": 0.0,
            "band": "假设",
            "drives_strategy": False,
            "show_probability": False,
            "downgraded": False,
            "note": "有效证据不足(n_eff<5)：仅作假设，不驱动策略、不展示概率档",
        }

    # 2) 先按 n_eff 上限封顶
    capped = min(raw, rule["ceiling"])
    band = band_of(capped)
    # 封顶后档位不得超过 n_eff 允许的最高档
    max_band_idx = BAND_ORDER.index(rule["band"])
    if BAND_ORDER.index(band) > max_band_idx:
        band = rule["band"]
        capped = min(capped, BAND_CEILING[band])

    # 3) P-04 降档：在原档基础上再降一档，并把置信压到新档上沿
    if downgraded:
        band = downgrade_band(band)
        capped = min(capped, BAND_CEILING[band])

    return {
        "raw": raw,
        "capped": round(capped, 4),
        "ceiling": rule["ceiling"],
        "band": band,
        "drives_strategy": rule["drives_strategy"],
        "show_probability": rule["show_probability"],
        "downgraded": bool(downgraded),
        "note": f"n_eff={n_eff:.1f} 硬封顶至 {rule['ceiling']:.2f}"
                + ("；P-04 再降一档" if downgraded else ""),
    }


# ====================================================================== #
# P-04：高压降档触发 / 披露措辞 / 先验切换 / 非典型个体
# ====================================================================== #
# 降档白名单触发键
P4_TRIGGERS = (
    "conflict_escalation",      # 冲突升级
    "high_arousal",             # 高情绪唤醒
    "post_coldwar_restart",     # 冷战后重启
    "novelty_over_threshold",   # 场景新颖度（最近邻距离）超阈
    "distribution_shift",       # T 检出行为分布突变
)

# 禁用的确定性措辞（出现即视为违反数字诚实规范）
FORBIDDEN_DETERMINISTIC_WORDS = ("一定", "肯定", "他不会", "绝不会", "必然", "百分百")

# 剧变期切换键（个体历史分布 → 人群先验 + 强降档）
TURMOIL_SIGNALS = (
    "new_relationship_early",   # 新关系初期
    "recent_severe_conflict",    # 激烈冲突后
    "major_life_event",         # 重大变故期
)

# 非典型个体检验：最初约多少条消息用于与人群先验比较
ATYPICAL_WINDOW = 50
# 偏离人群先验超过该阈值 → 判为非典型个体
ATYPICAL_DEVIATION_THRESHOLD = 0.35
# 基础收缩 κ（数值含义：κ 越大，越向人群先验收缩；放宽 = 调小 κ，少拉向人群均值）
BASE_SHRINK_KAPPA = 10.0


def detect_downgrade(flags: Dict[str, bool]) -> Dict[str, Any]:
    """根据触发标志判定是否需要强制降档。

    Args:
        flags: dict，键取自 P4_TRIGGERS（值 bool）。

    Returns:
        dict: {downgrade, triggers(list), turmoil, switch_to_prior}
    """
    triggers = [t for t in P4_TRIGGERS if flags.get(t)]
    turmoil_signals = [t for t in TURMOIL_SIGNALS if flags.get(t)]
    downgrade = len(triggers) > 0
    turmoil = len(turmoil_signals) > 0
    # 剧变期：切回人群先验，且强降档
    switch_to_prior = turmoil
    return {
        "downgrade": downgrade or turmoil,
        "triggers": triggers,
        "turmoil": turmoil,
        "turmoil_signals": turmoil_signals,
        "switch_to_prior": switch_to_prior,
    }


def disclosure_wording(capped_confidence: float, outcome_label: str,
                       n_eff: float, downgraded: bool = False) -> str:
    """P-04 标准披露措辞。

    形如："历史上类似情况下约 Y% 出现 Z；当前情境样本少，仅供参考"。
    强制软化语气，不得含确定性词。
    """
    y = round(min(max(float(capped_confidence), 0.0), 1.0) * 100)
    label = outcome_label or "该反应"
    base = f"历史上类似情况下约 {y}% 出现「{label}」；当前情境样本少，仅供参考"
    if downgraded:
        base += "（当前处于高压/样本外情境，已自动降档）"
    return base


def contains_deterministic_language(text: str) -> bool:
    """检测披露/建议文本是否命中禁用的确定性措辞。"""
    if not text:
        return False
    return any(w in text for w in FORBIDDEN_DETERMINISTIC_WORDS)


def atypical_adjustment(early_deviation: float,
                       n_early: int = ATYPICAL_WINDOW,
                       base_kappa: float = BASE_SHRINK_KAPPA,
                       window: int = ATYPICAL_WINDOW) -> Dict[str, Any]:
    """非典型个体判定：最初约 50 条消息与人群先验偏离过大时，放宽收缩 κ 并整体降档。

    Args:
        early_deviation: 个体早期分布与人群先验的偏离距离（0-1）。
        n_early: 已积累的早期消息数（须约 >= window 才做判定）。
        base_kappa: 基础收缩 κ。
        window: 判定窗口，默认 50 条。

    Returns:
        dict: {atypical, kappa, downgrade, note}
              atypical=True 时 kappa 调小（放宽收缩，少拉向人群均值），并整体降档。
    """
    dev = min(max(float(early_deviation), 0.0), 1.0)
    if n_early < window:
        return {"atypical": False, "kappa": base_kappa, "downgrade": False,
                "note": f"早期样本 {n_early}<{window}，暂不做非典型判定"}
    if dev > ATYPICAL_DEVIATION_THRESHOLD:
        # 放宽收缩 κ：乘 0.5（向人群均值的拉力减半），并整体降一档
        relaxed = round(base_kappa * 0.5, 3)
        return {
            "atypical": True,
            "kappa": relaxed,
            "downgrade": True,
            "note": f"早期偏离人群先验 {dev:.2f}>{ATYPICAL_DEVIATION_THRESHOLD}："
                    f"非典型个体，收缩κ {base_kappa}→{relaxed} 并整体降档",
        }
    return {"atypical": False, "kappa": base_kappa, "downgrade": False,
            "note": f"早期偏离 {dev:.2f} 未超阈，按标准收缩"}


# ====================================================================== #
# 便捷入口：按证据量 evidence_n 直接封顶（防止前端出现 99%/100%）
# ====================================================================== #
def cap_confidence(confidence: float, evidence_n: int = 0) -> float:
    """按证据量 evidence_n 对置信度做硬封顶，返回封顶后的 0-1 浮点数。

    分档（与任务约定一致）：
      - evidence_n <  5 ：顶到 0.30
      - 5  <= n <  10 ：顶到 0.45
      - 10 <= n <  30 ：顶到 0.60
      - 30 <= n < 100 ：顶到 0.80
      - n >= 100      ：不封顶（返回原值夹在 0-1）

    Args:
        confidence: 模型/规则给出的原始置信（任意数值，会先夹到 0-1）。
        evidence_n: 有效证据条数（如最近聊天/证据数）；不可用传 0。

    Returns:
        float: 封顶后的置信（0-1）。
    """
    try:
        c = min(max(float(confidence), 0.0), 1.0)
    except (TypeError, ValueError):
        c = 0.0
    try:
        n = int(evidence_n or 0)
    except (TypeError, ValueError):
        n = 0
    if n < 5:
        cap = 0.30
    elif n < 10:
        cap = 0.45
    elif n < 30:
        cap = 0.60
    elif n < 100:
        cap = 0.80
    else:
        return c
    return min(c, cap)


# ====================================================================== #
# 对外稳定入口（consult_engine / 其他 agent 会 guarded import）：
#   - cap_score(value, evidence_n) -> float   把数值置信压到诚实区间；
#   - cap_answer_confidence(text)  -> str     把回答里的绝对化/虚高措辞压成诚实语气。
# 纯本地、无第三方依赖；任何异常都不抛，坏输入给保守默认（数字诚实）。
# ====================================================================== #
# 通用诚实上限：模型/规则永远不应宣称 100% 确定（数字诚实硬红线）。
HONEST_SCORE_CEILING = 0.99


def cap_score(value, evidence_n: int = 0) -> float:
    """把任意置信数值压到诚实区间（0-1）。

    - 接受 0-1 或 0-100：>1 的输入自动当作百分数除以 100；
    - 永不返回 1.0（封顶 HONEST_SCORE_CEILING=0.99，绝无 100%）；
    - 再按证据量 evidence_n 叠加 cap_confidence 的硬封顶（证据少→压更低）；
    - 坏输入（None/字符串无法解析）一律保守返回 0.0，不抛异常。

    Args:
        value: 原始置信（0-1 或 0-100）。
        evidence_n: 有效证据条数；不可用传 0。

    Returns:
        float: 封顶后的置信（0-1，<=0.99）。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v > 1.0:  # 0-100 制 → 0-1
        v = v / 100.0
    v = min(max(v, 0.0), 1.0)
    v = min(v, HONEST_SCORE_CEILING)
    capped = cap_confidence(v, evidence_n)
    return round(min(capped, HONEST_SCORE_CEILING), 4)


# 绝对化/虚高措辞 → 诚实措辞。顺序敏感：先长后短，先特化后泛化，避免子串误伤。
# （"一定不会" 必须排在 "一定" 之前；"绝对不" 排在 "绝对" 之前。）
_CONFIDENCE_WORD_RULES = [
    ("百分之百", "较高概率"),
    ("100%确定", "较高概率"),
    ("百分百", "较高概率"),
    ("100%", "较高概率"),
    ("99.9%", "较高概率"),
    ("99%", "较高概率"),
    ("绝对不会", "大概率不会"),
    ("绝对会", "大概率会"),
    ("绝对不", "大概率不"),
    ("绝对", "大概率"),
    ("一定不会", "大概率不会"),
    ("一定会", "大概率会"),
    ("一定", "大概率"),
    ("肯定不会", "大概率不会"),
    ("肯定会", "大概率会"),
    ("肯定不", "大概率不"),
    ("肯定", "大概率"),
    ("必然不会", "大概率不会"),
    ("必然会", "大概率会"),
    ("必然不", "大概率不"),
    ("必然", "大概率"),
    ("绝不会", "大概率不会"),
    ("绝不", "大概率不"),
    ("他不会", "他大概率不会"),
]

# 需要保护的"诚实对冲短语"：替换前先占位，替换后还原，避免被改成不通顺的话。
_GUARD_PHRASES = [
    ("不一定", "\x01GUARD_BUDING1\x01"),
    ("不见得", "\x01GUARD_BUJIDE1\x01"),
]


def cap_answer_confidence(text):
    """把一段回答里的绝对化/虚高置信措辞压到诚实区间，返回改写后的文本。

    例：「他一定会答应，成功率100%」→「他大概率会答应，成功率较高概率」。

    - 永不抛异常：非字符串/None 安全返回；
    - 保护诚实对冲词（不一定 / 不见得）不被误伤；
    - 与 contains_deterministic_language 互补：这里是"改写"，那里是"检测"。

    Args:
        text: 原始回答文本（str）。

    Returns:
        str: 压平绝对化措辞后的文本。空/None 原样返回。
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    if not text:
        return text

    out = text
    # 1) 保护诚实对冲短语
    guards = []
    for i, (bad, token) in enumerate(_GUARD_PHRASES):
        if bad in out:
            out = out.replace(bad, token)
            guards.append((token, bad))

    # 2) 绝对化 → 大概率 / 较高概率
    for bad, good in _CONFIDENCE_WORD_RULES:
        if bad in out:
            out = out.replace(bad, good)

    # 3) 还原保护短语
    for token, bad in guards:
        out = out.replace(token, bad)
    return out
