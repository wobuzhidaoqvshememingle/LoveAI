# -*- coding: utf-8 -*-
"""R3.3-77 共享本地消息总线（BridgeBus）。

在 services.event_bus.EventBus 之上【组合扩展】，不改动 EventBus 本身（service_facade
在用其公开 API，兼容层底线）：
  - 复用 EventBus 的同步发布、深度防环（max_depth）、单轮按事件类型去重；
  - 新增：载荷 schema 校验（缺必填 key 即去噪丢弃并记指标）、命名消费方登记、
    总线级指标汇总。

事件驱动、不轮询。生产环境可把 service_facade 的 EventBus 实例注入进来，
从而让桥直接消费 facade 已 publish 的 AFFECTION_CHANGED 等事件。
"""
from __future__ import annotations

import logging
from typing import Callable

from services.event_bus import EventBus, DEFAULT_MAX_DEPTH
from services.bridges import events as ev
from services.bridges.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class BridgeBus:
    def __init__(self, bus: EventBus | None = None,
                 metrics: MetricsCollector | None = None,
                 max_depth: int = 4) -> None:
        # 底层同步事件总线：可注入存量 bus（生产挂钩），否则自建（headless/测试）
        self._bus = bus or EventBus(max_depth=max_depth)
        self.metrics = metrics or MetricsCollector()
        # 命名消费方登记：consumer_name -> [event_type...]（可观测：谁在听什么）
        self._consumers: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ #
    @property
    def raw_bus(self) -> EventBus:
        return self._bus

    def register_consumer(self, name: str, event_types: list[str]) -> None:
        self._consumers.setdefault(name, []).extend(event_types)

    def consumers_report(self) -> dict:
        return dict(self._consumers)

    # ------------------------------------------------------------------ #
    def subscribe(self, event_type: str, callback: Callable[[dict], None],
                  consumer: str = "", keys_of_interest: list | None = None) -> None:
        if consumer:
            self.register_consumer(consumer, [event_type])
        self._bus.subscribe(event_type, callback, keys_of_interest=keys_of_interest)

    # ------------------------------------------------------------------ #
    def _validate(self, event_type: str, data: dict) -> bool:
        required = ev.required_keys(event_type)
        for k in required:
            v = data.get(k)
            if v is None or (isinstance(v, str) and not v):
                return False
        return True

    def publish(self, event_type: str, data: dict | None = None) -> dict:
        """带 schema 校验的发布。校验失败则去噪丢弃（不传播），记指标。"""
        data = dict(data or {})
        if not self._validate(event_type, data):
            self.metrics.record_denoised(1)
            logger.debug("事件 %s 载荷缺必填 key，去噪丢弃: %s", event_type, data)
            return {"event": event_type, "denoised": True}
        return self._bus.publish(event_type, data)

    def reset_pass(self) -> None:
        self._bus.reset_pass()

    def metrics_summary(self) -> dict:
        return self.metrics.summary()
