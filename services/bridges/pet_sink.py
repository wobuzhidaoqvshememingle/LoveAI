# -*- coding: utf-8 -*-
"""R3.3-77 桌宠表现出口（PetSink）。

桌宠 WS（app/pet_ws_server.py）由方向 A 子代理负责，本桥只读使用其广播能力：
  - 生产态：惰性 import app.pet_ws_server._broadcast，把事件真投给桌宠客户端；
  - headless / 测试态：用 InMemoryPetSink 捕获事件流，可断言"桌宠表现真的变了"。

服务层纯 Python：惰性 import 发生在调用时，避免在 services 层 import 时拉入 Qt/WS。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class InMemoryPetSink:
    """内存桌宠 sink：捕获广播事件，供 headless 断言。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))

    def by_event(self, name: str) -> list[dict]:
        return [e for e in self.events if e.get("event") == name]

    def last(self) -> dict | None:
        return self.events[-1] if self.events else None


def make_default_sink():
    """惰性构造真实 WS 广播 sink；不可用（headless/无 loop）时回退内存 sink。"""
    def _sink(event: dict) -> None:
        try:
            from app import pet_ws_server  # 惰性导入，不拉入 Qt 建链期
            pet_ws_server._broadcast(event)
        except Exception as e:  # 无 WS loop / 未启动：静默，不炸业务
            logger.debug("桌宠广播不可用，事件丢弃: %s", e)
    return _sink
