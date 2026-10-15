# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 同步事件总线（Synchronous Event Bus）

目的：在人物状态快照（中心数据池）之上提供"模块更新→依赖模块同步重算"的
事件触发层。关键约束：**同步**发布与订阅——publish 时在同一遍调用栈内
依次执行所有订阅者回调，不引入异步事件循环，因此不破坏 analyze_chat
单遍同步流程的确定性。

防循环设计：
- 深度限制：当前处理深度超过 MAX_DEPTH 即停止向更深层传播；
- 单轮去重：同一事件类型在同一"传播轮"内只触发一次订阅者，避免
  A→B→A 的乒乓重算。

服务层纯 Python：不 import PySide6。
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---- 事件类型枚举（字符串常量，避免拼写漂移）----
EMOTION_UPDATED = "emotion_updated"
PROFILE_UPDATED = "profile_updated"
AFFECTION_CHANGED = "affection_changed"
STAGE_CHANGED = "stage_changed"
STRATEGY_CHANGED = "strategy_changed"
TASKS_COMPLETED = "tasks_completed"
FINANCIAL_UPDATED = "financial_updated"
LOSS_AVERSION_UPDATED = "loss_aversion_updated"
TWIN_PREDICTION_UPDATED = "twin_prediction_updated"
MEMORY_UPDATED = "memory_updated"
TRAINING_DATA_COLLECTED = "training_data_collected"
TRAINING_COMPLETED = "training_completed"

ALL_EVENTS = (
    EMOTION_UPDATED, PROFILE_UPDATED, AFFECTION_CHANGED, STAGE_CHANGED,
    STRATEGY_CHANGED, TASKS_COMPLETED, FINANCIAL_UPDATED,
    LOSS_AVERSION_UPDATED, TWIN_PREDICTION_UPDATED, MEMORY_UPDATED,
    TRAINING_DATA_COLLECTED, TRAINING_COMPLETED,
)

DEFAULT_MAX_DEPTH = 3


class EventBus:
    """同步事件总线。

    Usage:
        bus = EventBus()
        bus.subscribe(EventBus.AFFECTION_CHANGED, my_callback)
        bus.publish(EventBus.AFFECTION_CHANGED, {"person_id": "p1", "total": 65})
    """

    # 暴露为类属性，便于引用
    EMOTION_UPDATED = EMOTION_UPDATED
    PROFILE_UPDATED = PROFILE_UPDATED
    AFFECTION_CHANGED = AFFECTION_CHANGED
    STAGE_CHANGED = STAGE_CHANGED
    STRATEGY_CHANGED = STRATEGY_CHANGED
    TASKS_COMPLETED = TASKS_COMPLETED
    FINANCIAL_UPDATED = FINANCIAL_UPDATED
    LOSS_AVERSION_UPDATED = LOSS_AVERSION_UPDATED
    TWIN_PREDICTION_UPDATED = TWIN_PREDICTION_UPDATED
    MEMORY_UPDATED = MEMORY_UPDATED
    TRAINING_DATA_COLLECTED = TRAINING_DATA_COLLECTED
    TRAINING_COMPLETED = TRAINING_COMPLETED

    def __init__(self, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
        self._subs: dict[str, list[Callable]] = defaultdict(list)
        self._max_depth = max_depth
        # 当前传播栈（深度），以及单轮已发布事件集合（去重）
        self._depth = 0
        self._fired_this_pass: set[str] = set()
        # 统计：便于测试与观测
        self.publish_count = 0

    # ------------------------------------------------------------------ #
    def subscribe(self, event_type: str, callback: Callable[[dict], None],
                  keys_of_interest: list | None = None) -> None:
        """订阅事件。回调签名：callback(data: dict) -> None。

        keys_of_interest: 可选，声明该订阅者关心的 Key。发布方在 data 中带
        changed_keys 时，只有与声明 Key 相交的订阅者才被触发；不传则全触发（向后兼容）。
        """
        self._subs[event_type].append((callback, set(keys_of_interest or [])))

    def reset_pass(self) -> None:
        """开始新的一轮传播前调用，清空单轮去重集合。"""
        self._fired_this_pass = set()
        self._depth = 0

    # ------------------------------------------------------------------ #
    def publish(self, event_type: str, data: dict | None = None) -> dict:
        """同步发布事件：依次调用所有订阅者回调。

        - 单轮去重：同一 event_type 在同一轮内只触发一次；
        - 深度保护：传播深度超过 _max_depth 时直接停止；
        - 单订阅者异常被吞掉并记录，不影响其他订阅者。

        Returns:
            统计 dict：{event, subscribers, invoked}。
        """
        data = data or {}
        # 深度保护：超过上限停止传播
        if self._depth > self._max_depth:
            logger.debug("事件传播超过深度上限(%d)，停止 %s", self._max_depth, event_type)
            return {"event": event_type, "subscribers": 0, "invoked": 0, "depth_limited": True}
        # 单轮去重
        if event_type in self._fired_this_pass:
            return {"event": event_type, "subscribers": len(self._subs.get(event_type, [])),
                    "invoked": 0, "deduplicated": True}

        self._fired_this_pass.add(event_type)
        self.publish_count += 1
        self._depth += 1
        changed = set(data.get("changed_keys") or [])
        invoked = 0
        try:
            for cb, want_keys in self._subs.get(event_type, []):
                # 按需调度：订阅者声明了 Key 且与 changed_keys 无交集 → 跳过
                if want_keys and changed and not (want_keys & changed):
                    continue
                try:
                    cb(data)
                    invoked += 1
                except Exception as e:  # noqa: BLE001 - 单订阅者隔离
                    logger.warning("事件 %s 订阅者回调失败: %s", event_type, e)
        finally:
            self._depth -= 1
        return {"event": event_type, "subscribers": len(self._subs.get(event_type, [])),
                "invoked": invoked}
