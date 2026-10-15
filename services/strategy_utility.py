# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - Utility AI 策略评分（替代部分硬编码）

每个策略一个效用函数，输入当前完整人物状态 dict，输出 0-1 适用度。
选最高分 + 给第二选择。向后兼容：strategy_advisor 旧逻辑不动，本模块为可选增强。
输入 state 可含：attachment / emotion_pad / axes / loss_index / social_position / trend。
服务层纯 Python。
"""
from __future__ import annotations


def _base(state) -> dict:
    return state if isinstance(state, dict) else {}


def score_propagate(s):
    # 推进：好感度中高、信任高、情绪正向、非回避激活
    a = _base(s)
    score = 0.3
    ax = a.get("axes", {}) or {}
    if ax.get("Trust", 50) > 55: score += 0.2
    if a.get("emotion_pad", {}).get("p", 0.5) > 0.6: score += 0.15
    if a.get("attachment") == "回避": score -= 0.3
    if a.get("trend") == "对方冷淡": score -= 0.25
    return max(0.0, min(1.0, score))


def score_space(s):
    # 给空间：回避激活 / 对方冷淡 / 情绪负向
    a = _base(s); score = 0.2
    if a.get("attachment") == "回避": score += 0.35
    if a.get("trend") == "对方冷淡": score += 0.25
    if a.get("emotion_pad", {}).get("p", 0.5) < 0.4: score += 0.15
    return max(0.0, min(1.0, score))


def score_counter(s):
    # 反制：损失厌恶低、有操控信号
    a = _base(s); score = 0.1
    if (a.get("loss_index", 100) or 100) < 50: score += 0.4
    if a.get("manipulation"): score += 0.4
    return max(0.0, min(1.0, score))


def score_invest(s):
    # 引导投入：投入失衡、损失感低
    a = _base(s); score = 0.2
    ax = a.get("axes", {}) or {}
    if ax.get("Investment", 50) < 40: score += 0.35
    if (a.get("loss_index", 100) or 100) < 55: score += 0.2
    return max(0.0, min(1.0, score))


def score_repair(s):
    # 修复：情绪负向、近期冲突
    a = _base(s); score = 0.2
    if a.get("recent_conflict"): score += 0.4
    if a.get("emotion_pad", {}).get("p", 0.5) < 0.35: score += 0.25
    return max(0.0, min(1.0, score))


_SCORERS = {
    "推进": score_propagate, "给空间": score_space, "反制": score_counter,
    "引导投入": score_invest, "修复": score_repair,
}


def choose_strategy(state: dict) -> dict:
    """返回 {chosen, utility, runner_up, all_scores}。"""
    scores = {name: round(fn(state), 3) for name, fn in _SCORERS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    chosen, u = ranked[0]
    runner = ranked[1][0] if len(ranked) > 1 else None
    return {"chosen": chosen, "utility": u, "runner_up": runner, "all_scores": scores}


# ============ Utility + Contract Net 三层融合决策 ============
# 各候选策略的投标细节（预期效果/成本/风险/兜底）
_BIDS = {
    "推进":   {"expected_axes": {"Attraction": +4}, "cost": 0.6, "risk": 0.5,
              "fallback": "若对方反感则切给空间"},
    "给空间": {"expected_axes": {"Comfort": +3}, "cost": 0.2, "risk": 0.2,
              "fallback": "若对方主动联系则切轻量接触"},
    "反制":   {"expected_axes": {"Trust": +1}, "cost": 0.8, "risk": 0.7,
              "fallback": "渐进式减量，防止对方直接跑掉"},
    "引导投入": {"expected_axes": {"Investment": +5}, "cost": 0.5, "risk": 0.4,
              "fallback": "小步请求，被拒则退回"},
    "修复":   {"expected_axes": {"Trust": +4, "Emotional": +3}, "cost": 0.4, "risk": 0.3,
              "fallback": "持续温和，不追问对错"},
}


def _dynamic_weight(name: str, history_effects: dict) -> float:
    """根据历史效果动态调整权重：效果差降权。"""
    eff = history_effects.get(name)
    if eff is None:
        return 1.0
    return max(0.3, min(1.5, 1.0 + eff))


def fused_choose(state: dict, history_effects: dict | None = None,
                 high_risk: bool = False) -> dict:
    """三层融合：Utility初筛(<0.3排除)→Contract Net投标(效果/成本/风险加权)→可选组合。"""
    history_effects = history_effects or {}
    base = choose_strategy(state)
    scores = base["all_scores"]
    # 第一层：初筛 + 动态权重
    candidates = {}
    for name, u in scores.items():
        if u < 0.3:
            continue
        candidates[name] = u * _dynamic_weight(name, history_effects)
    ranked = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)
    # 第二层：Manager 投标评估（效果/成本/风险）
    def bid_score(name):
        bid = _BIDS.get(name, {"cost": 0.5, "risk": 0.5})
        effect = sum(bid.get("expected_axes", {}).values())
        # 越高分越好：效用*效果权重 - 成本/风险惩罚
        return round((candidates[name] * (1 + effect / 10)) - bid["cost"] * 0.3 - bid["risk"] * 0.3, 3)
    bids = {n: bid_score(n) for n, _ in ranked}
    best = max(bids, key=bids.get)
    # 第三层：若最高分不够突出，组合前两名
    combo = None
    if len(ranked) >= 2 and bids[best] - bids[ranked[1][0]] < 0.1:
        combo = [best, ranked[1][0]]
    return {
        "meta": "ContractNet" if high_risk else "Utility快评",
        "candidates": list(candidates.keys()),
        "chosen": best, "bid_score": bids[best],
        "combination": combo,
        "fallback": _BIDS.get(best, {}).get("fallback"),
    }
