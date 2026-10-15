# -*- coding: utf-8 -*-
"""R3.3-80 复杂度画像发布器（桥接层上游小工具）。

把 services.relationship_complexity.analyze_relationship_complexity 的真实画像结果
翻译成总线事件 COMPLEXITY_PROFILE_UPDATED 发出去，下游 A/B/D/F 三座桥据此消费。

设计：只做"算画像 → 发事件"的薄封装，不重复引擎逻辑；纯服务层、无网络。
"""
from __future__ import annotations

import logging

from services.relationship_complexity import analyze_relationship_complexity
from services.bridges import events as ev

logger = logging.getLogger(__name__)


def publish_complexity_profile(bus, person_id: str, messages: list,
                                persona_result: dict | None = None) -> dict:
    """跑复杂度画像并发布 COMPLEXITY_PROFILE_UPDATED。

    Args:
        bus: BridgeBus 实例（或任何有 publish(event_type, payload) 的对象）。
        person_id: 人物 ID。
        messages: 聊天消息列表（dict 取 content/text，否则 str）。
        persona_result: 可选，persona 分析结果（用于 attachment 联动）。

    Returns:
        dict: 完整复杂度画像（供上层/脚本直接断言）。
    """
    profile = analyze_relationship_complexity(person_id, messages, persona_result)
    payload = {
        "person_id": person_id,
        "profile": profile,
        "family_system": profile["family_system"],
        "relationship_state": profile["relationship_state"],
        "rebound_pattern": profile["rebound_pattern"],
        "condition_gap": profile["condition_gap"],
        "trauma_drivers": profile["trauma_drivers"],
    }
    try:
        bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("发布 COMPLEXITY_PROFILE_UPDATED 失败: %s", e)
    return profile
