# -*- coding: utf-8 -*-
"""
LoveAI - 主动发起建议线（Proactive Outreach）

────────────────────────────────────────────────────────────────────────────
定位（LoveAI=给用户出建议/提醒，不是 AI 代聊）：
  系统不能纯被动等对方回话。本模块在【合适时机】生成一条"提醒用户主动联系蛋卷"
  的建议（不是替用户发消息），由 task_engine.add_custom_task(source="proactive_outreach")
  落入任务链。借鉴抖音人机恋"主动消息三链"，按 LoveAI 定位改造为四类：
    * topic   主动话题：开启一个新的轻松话题；
    * care    关心：一句低压关心（吃饭/降温/忙完了？）；
    * invite  邀约：顺势约见面/一起做点事；
    * node    关系节点推进：纪念日/升阶节点/低谷后的一次推进。

【时机门控（概率门+冷却门，不固定频率——固定频率会让主动失去信息价值，反信息论）】
  G1 静默时段：当前小时 ∈ [0,7) 一律不触发（深夜不骚扰）。
  G2 冷却门：距用户上次主动联系 < MIN_COOLDOWN_HOURS 不触发（防连环轰炸）。
  G3 基线偏移触发：用 baseline_surprise 的 information_value——只有"相对她长期基线
     的高意外"才触发，而不是拍脑袋的"绝对值 27 分钟没回"。
  G4 关系节点触发：罗盘 north_star 的情绪低点/长期沉默/升阶节点。
  G5 概率门：命中触发源后仍按概率放行（rng），让主动不可预测，避免对方习以为常。

────────────────────────────────────────────────────────────────────────────
【边界（必须遵守，注释随代码走）】
  1. 不借鉴"吃醋状态机"：表演情绪会被识破为套路（30种死法#6/#13）。本模块只建议
     中性/低压的主动，不生成"假装吃醋/试探"的内容。
  2. 主动 ≠ 固定定时主动：固定频率让主动变成背景噪音、失去信息价值。故用 G5 概率门
     + G3 基线偏移，命中才建议，而非每 N 小时准点弹。
  3. 服从"留灯式低压（回撤≠降频）"：对回避型/低安全感阶段【降频不施压】——
     她需要空间时，主动建议退到最低，只在"长期沉默点"才轻轻提醒一次。
  4. 单次不归因：G3 的基线偏移需配合"持续时长+是否伴随其他信号"，本模块只输出
     "建议一次主动"，不输出"她冷淡了"的判断。
纯 Python，不 import PySide6，不写库；ctx 由调用方构造，便于单测。
"""
from __future__ import annotations

from typing import Optional

# 主动任务类型
TYPE_TOPIC = "topic"      # 主动话题
TYPE_CARE = "care"        # 关心
TYPE_INVITE = "invite"    # 邀约
TYPE_NODE = "node"        # 关系节点推进

SILENT_START_HOUR = 0     # 静默时段 [0,7)
SILENT_END_HOUR = 7
MIN_COOLDOWN_HOURS = 24.0  # 冷却门：距上次主动至少间隔
DEFAULT_PROBABILITY = 0.5  # G5 概率门放行概率（非固定频率）


def should_recommend_outreach(
    now_hour: int,
    hours_since_last_outreach: float,
    surprise: Optional[dict] = None,
    north_star: Optional[dict] = None,
    attachment: str = "",
    rng: float = 0.9,
    probability: float = DEFAULT_PROBABILITY,
) -> dict:
    """判断此刻是否该给用户弹一条"主动联系蛋卷"的建议。

    Args:
        now_hour: 当前小时(0-23)。
        hours_since_last_outreach: 距用户上次主动联系的小时数。
        surprise: baseline_surprise.evaluate_reply_surprise 的输出（可 None）。
        north_star: {"emotional_low","long_silence","escalation_node"} 布尔。
        attachment: 对方依恋类型描述（含"回避"即按回避型处理）。
        rng: [0,1) 确定性随机值（测试可传 0.9 必过 / 0.1 必不过概率门）。
        probability: 概率门放行阈值。

    Returns:
        dict: {fire, reason, suggested_type, title, do_not_push}
    """
    surprise = surprise or {}
    north_star = north_star or {}
    avoidant = "回避" in (attachment or "")

    # G1 静默时段
    if SILENT_START_HOUR <= now_hour < SILENT_END_HOUR:
        return _result(False, "静默时段[0,7)不触发", do_not_push=False)

    # G2 冷却门
    if hours_since_last_outreach < MIN_COOLDOWN_HOURS:
        return _result(False, f"冷却中(距上次主动{hours_since_last_outreach:.0f}h<{MIN_COOLDOWN_HOURS:.0f}h)",
                       do_not_push=False)

    # 触发源：G3 基线偏移（高意外） 或 G4 关系节点
    baseline_offset = (surprise.get("information_value") == "high")
    long_silence = bool(north_star.get("long_silence"))
    emotional_low = bool(north_star.get("emotional_low"))
    escalation_node = bool(north_star.get("escalation_node"))
    node_trigger = long_silence or emotional_low or escalation_node

    if not (baseline_offset or node_trigger):
        return _result(False, "无基线偏移/无关系节点信号，不触发（避免固定频率）",
                       do_not_push=False)

    # 回避型/低安全感：留灯低压——只在"长期沉默点"轻轻建议，其余降频不施压
    if avoidant and not long_silence:
        return _result(False, "回避型/低安全感阶段：降频不施压，仅长期沉默点才提醒",
                       do_not_push=True)

    # G5 概率门：命中触发源后仍按概率放行，让主动不可预测
    if rng >= probability:
        return _result(False, f"命中触发但概率门拦截(rng={rng}>={probability})——不固定频率",
                       do_not_push=False)

    # 选建议类型
    if escalation_node:
        stype, title = TYPE_NODE, "关系节点：借节点推进一次关系（非压力性提议）"
    elif baseline_offset and long_silence:
        stype, title = TYPE_CARE, "低压关心：发一句'最近忙吗/照顾好自己'，不追问不施压"
    elif baseline_offset:
        stype, title = TYPE_TOPIC, "开个轻松新话题（不聊关系、不查岗）"
    else:
        stype, title = TYPE_INVITE, "顺势邀约：轻松提一次见面/一起做点事"

    return _result(True, f"触发(基线偏移={baseline_offset},节点={node_trigger})",
                   suggested_type=stype, title=title, do_not_push=False)


def _result(fire: bool, reason: str, suggested_type: str = "", title: str = "",
            do_not_push: bool = False) -> dict:
    return {
        "fire": fire,
        "reason": reason,
        "suggested_type": suggested_type,
        "title": title,
        "do_not_push": do_not_push,
        "task_source": "proactive_outreach",
    }
