# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 损失厌恶 / 沉没成本分析（LossAversion）· 相对代价比例版

核心洞察（用户提出）：
  评估对方付出，不看绝对值，看「相对代价比例 = 付出绝对值 / 对方该维度可承受能力」。
  拥有100万的人花1块 vs 只有1块的人花1块——在付出者眼里价值完全不同。
  例：月入5000花500（10%）> 月入5万花2000（4%）；
      每天工作12小时挤出30分钟 > 空闲者每天陪2小时。

设计原则（手段中立）：
  - 每维评分 = 相对代价比例，不是绝对值；
  - 可承受能力从档案/聊天推断，信息不足用合理默认并标「估算」；
  - 单模块失败不影响主流程。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# 6 个维度的文本投入信号
_DIM_KEYWORDS = {
    "时间": ("主动找我", "在干嘛", "早安", "晚安", "见面", "约"),
    "情感": ("想你", "关心", "心疼", "委屈", "难过", "告诉你", "秘密", "脆弱"),
    "精力行动": ("帮你", "接你", "送你", "陪你", "请假", "绕路", "为你"),
    "金钱": ("红包", "转账", "礼物", "请客", "我买", "报销"),
    "社交": ("我朋友", "介绍给", "见家长", "带你去", "我圈子"),
    "未来规划": ("明年", "以后我们", "结婚", "买房", "一起", "计划"),
}

_PERCEIVED_KEYWORDS = (
    "我为你付出", "我对你这么好", "我花了多少", "我容易吗", "我牺牲", "我放弃了",
)

# 可承受能力线索（从聊天推断）
_BUSY_SIGNS = ("加班", "忙", "项目", "出差", "早出晚归", "996")
_EMOTION_DEPL = ("好累", " emo", "情绪", "焦虑", "内耗", "压力大", "失眠")
_INTROVERT_SIGNS = ("社恐", "内向", "不爱社交", "一个人待着")
_FUTURE_RESIST = ("不婚", "不想结婚", "还没玩够", "顺其自然")
_RICH_SIGNS = ("老板", "公司", "收入不错", "买房了", "年终奖")


def _hits(text: str, kws: tuple[str, ...]) -> int:
    return sum(1 for k in kws if k in text)


def _estimate_capacity(received_text: str, your_text: str, their_monthly_income: float = 0.0) -> dict:
    """推断对方6维可承受能力（信息不足用默认值并标 estimated）。

    Returns: dict[dim] = (capacity_value, unit, estimated:bool)
    """
    text = received_text or ""
    busy = _BUSY_SIGNS and (_hits(text, _BUSY_SIGNS) >= 1)
    depl = _hits(text, _EMOTION_DEPL) >= 1
    introvert = _hits(text, _INTROVERT_SIGNS) >= 1
    rich = their_monthly_income > 0 or _hits(text, _RICH_SIGNS) >= 1

    income = their_monthly_income or (20000.0 if rich else 6000.0)
    return {
        "金钱": (income, "元/月", their_monthly_income <= 0),
        "时间": (2.0 if busy else 4.0, "小时/天可支配", True),
        "情感": (0.5 if depl else 1.0, "情感能量(0-1)", depl),
        "精力行动": (1.0, "能力资源系数(0-1)", True),
        "社交": (0.3 if introvert else 0.8, "社交开放度(0-1)", True),
        "未来规划": (0.5 if _hits(text, _FUTURE_RESIST) >= 1 else 1.0, "规划意愿(0-1)", True),
    }


def _ratio_score(absolute: float, capacity: float) -> tuple[float, float]:
    """代价比例 = 相对付出 / 可承受能力；返回 (ratio, score 0-2)。"""
    if capacity <= 0:
        capacity = 1.0
    ratio = round(absolute / capacity, 3)
    if ratio >= 0.10:      # 占可承受能力 ≥10%
        score = 2
    elif ratio >= 0.03:    # 3%-10%
        score = 1
    else:
        score = 0
    return ratio, score


def analyze_loss_aversion(
    received_text: str,
    your_text: str = "",
    your_financial_count: int = 0,
    their_financial_count: int = 0,
    their_monthly_income: float = 0.0,
    your_monthly_income: float = 0.0,
    their_spent_yuan: float = 0.0,
    your_spent_yuan: float = 0.0,
) -> dict:
    """计算对方相对代价比例、损失感指数与引导建议。

    每维输出：绝对值 / 可承受能力 / 代价比例 / 评分。
    """
    received_text = received_text or ""
    your_text = your_text or ""
    cap = _estimate_capacity(received_text, your_text, their_monthly_income)

    dims_out: dict[str, dict] = {}
    their_total = 0
    for dim, kws in _DIM_KEYWORDS.items():
        abs_hits = _hits(received_text, kws)
        capacity_val, unit, estimated = cap[dim]
        # 金钱维度直接用金额比例；其余维度用信号命中数 / 可承受能力
        if dim == "金钱" and their_spent_yuan > 0:
            absolute = their_spent_yuan
        else:
            absolute = float(abs_hits)
        ratio, score = _ratio_score(absolute, capacity_val)
        their_total += score
        dims_out[dim] = {
            "absolute": absolute,
            "capacity": capacity_val,
            "capacity_unit": unit,
            "estimated": estimated,
            "cost_ratio": ratio,
            "score": score,
        }

    # 双方相对付出比（金钱，比例口径）
    your_ratio = round((your_spent_yuan or 0) / (your_monthly_income or 6000.0), 3)
    their_ratio = round((their_spent_yuan or 0) / (their_monthly_income or 6000.0), 3)

    perceived = min(2, sum(1 for k in _PERCEIVED_KEYWORDS if k in received_text))

    # 损失感指数（基于相对代价，不是绝对值）
    loss_index = round(min(100, their_total / 12 * 60 + perceived * 15
                           + max(0, their_financial_count) * 5), 1)

    if loss_index < 35 or their_total == 0:
        risk = "高风险：对方相对代价投入低，觉得收支平衡/赚了，不怕离开"
    elif loss_index < 60:
        risk = "中风险：对方有一定相对投入但沉没成本尚浅"
    else:
        risk = "低风险：对方相对沉没成本较深，离开有真实损失感"

    # 失衡（相对口径）
    if your_ratio > their_ratio and your_spent_yuan > 0:
        imbalance_note = (f"你方金钱相对付出 {your_ratio:.1%} 高于对方 {their_ratio:.1%}"
                          "——你占可承受比例更高，对方投入意愿偏低")
    elif their_ratio > your_ratio and their_spent_yuan > 0:
        imbalance_note = f"对方金钱相对付出 {their_ratio:.1%} 高于你方 {your_ratio:.1%}"
    else:
        imbalance_note = "双方投入大致平衡（相对口径）"

    suggestion: list[dict] = []
    if loss_index < 50:
        suggestion += [
            {"action": "引导对方投入（富兰克林效应）",
             "how": "自然请对方帮低难度小忙，让对方为你做事。",
             "effect": "对方相对沉没成本上升，合理化'我在乎他'。",
             "cost": "若对方拒绝任何小忙，投入意愿低。"},
            {"action": "小承诺+登门槛",
             "how": "先兑现极小承诺，再逐步加大。",
             "effect": "承诺一致性持续投入。",
             "cost": "连续不兑现→投入意愿不足。"},
        ]
    else:
        suggestion.append({
            "action": "可适度推进关系/提出要求",
            "how": "对方相对沉没成本较深，可自然推进（约见面/确认定位/共同计划）。",
            "effect": "有沉没成本兜底，对方更愿意配合。",
            "cost": "推进过快仍可能反弹，保持节奏。",
        })

    return {
        "dimensions": dims_out,
        "their_investment_total": their_total,
        "perceived_investment": perceived,
        "loss_index": loss_index,
        "risk": risk,
        "money_ratio_you": your_ratio,
        "money_ratio_them": their_ratio,
        "imbalance_note": imbalance_note,
        "suggestion": suggestion,
    }
