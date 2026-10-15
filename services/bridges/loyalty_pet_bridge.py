# -*- coding: utf-8 -*-
"""R3.3-78 桥：忠诚度（反制线）↔ 桌宠表现。

与 AffectionPetBridge（引导线：好感度 → 亲昵表现）并列，本桥只管反制线：
    忠诚度＝听话程度/遵从度。忠诚度变化 → 投"顺从向"桌宠情绪事件。

设计要点（严格不动既有桥文件）：
  - 事件类型常量 LOYALTY_CHANGED 统一登记在 events.py 注册表（本文件 re-export 兼容）；
  - 订阅 [LOYALTY_CHANGED]，translate 把 {person_id, loyalty, delta, reason}
    翻成桌宠 WS 载荷：{"event":"emotion","data":{...,"metric":"loyalty",
    "emotion":"obedient","feedback":"顺从"}}——与好感桥的亲昵载荷刻意区分，
    桌宠侧（app.js 按 data.metric / detectRise）据此走"顺从"而非"亲昵"表现；
  - headless 下用 InMemoryPetSink 捕获，生产态由 mount 时注入真实 sink。

挂载：本桥已并入 services/bridges/__init__.py 的 build_bridge_network（R3.3-78 完成态）；
mount_loyalty_bridge(net) 保留为独立挂载入口（调用方在 build_bridge_network(...) 之后调用）：
    net = build_bridge_network(...)
    mount_loyalty_bridge(net)
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges.arbiter import PRI_EXPRESSION

logger = logging.getLogger(__name__)

# R3.3-78 事件类型（统一登记在 events.py 注册表，避免拼写漂移；此处 re-export 保持向后兼容）
from services.bridges.events import LOYALTY_CHANGED  # noqa: F401


class LoyaltyPetBridge(Bridge):
    """忠诚度变化 → 桌宠顺从向表现（cosmetic 层，仲裁最低优先级）。"""

    name = "loyalty_pet"

    def __init__(self, bus, pet_sink, metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.sink = pet_sink  # callable(event_dict)
        self.last_loyalty: float | None = None
        self.last_feedback: str = "顺从"

    def subscribe_events(self) -> list[str]:
        return [LOYALTY_CHANGED]

    def translate_in(self, data: dict):
        pid = data.get("person_id")
        if not pid:
            return None  # 去噪：没有对象身份不联动
        loyalty = data.get("loyalty")
        if loyalty is None:
            return None
        return {
            "person_id": pid,
            "loyalty": float(loyalty),
            "delta": float(data.get("delta") or 0.0),
            "reason": data.get("reason") or "",
        }

    def constrain(self, translated: dict) -> dict:
        # 与既有桥一致：数值钳制在 0~100
        translated["loyalty"] = max(0.0, min(100.0, translated["loyalty"]))
        return translated

    def handle(self, t: dict, raw: dict) -> bool:
        loyalty = t["loyalty"]
        self.last_loyalty = loyalty
        self.last_feedback = "顺从"

        # 表现软写入走仲裁（最低优先级，与好感桥同档，互不抢主线）
        self.claim("pet", "emotion", "obedient", PRI_EXPRESSION,
                   meta={"loyalty": loyalty, "delta": t["delta"],
                         "reason": t["reason"]})

        # 投到桌宠表现出口：metric="loyalty" 显式标记反制线，
        # 桌宠侧 app.js 据此走"顺从"反馈（与好感桥的亲昵载荷不共用表达）。
        event = {
            "event": "emotion",
            "data": {
                "affection": None,            # 不强制好感值，桌宠侧 null 即忽略
                "loyalty": loyalty,
                "metric": "loyalty",
                "emotion": "obedient",        # 顺从向情绪（桌宠 applyEmotion 走 idle 默认档）
                "feedback": "顺从",
                "delta": t["delta"],
                "reason": t["reason"],
            },
        }
        self.sink(event)
        if self.metrics:
            self.metrics.record_event_out(self.name)
        return True


def mount_loyalty_bridge(net: dict) -> dict:
    """把 LoyaltyPetBridge 挂进既有桥网（build_bridge_network 返回的 net dict）。

    不动 services/bridges/__init__.py（并行子代理收尾中）；挂载动作由调用方在
    build_bridge_network(...) 之后调本函数完成——即"接入 build_bridge_network 的
    2 行挂载指令"：
        from services.bridges import build_bridge_network
        net = build_bridge_network(...)
        from services.bridges.loyalty_pet_bridge import mount_loyalty_bridge
        mount_loyalty_bridge(net)
    """
    bus = net["bus"]
    sink = net.get("pet_sink")
    if sink is None:
        from services.bridges.pet_sink import make_default_sink
        sink = make_default_sink()
    bridge = LoyaltyPetBridge(bus, sink,
                              metrics=net.get("metrics"),
                              arbiter=net.get("arbiter"))
    bridge.setup()
    net.setdefault("bridges", {})[bridge.name] = bridge
    return net
