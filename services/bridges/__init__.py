# -*- coding: utf-8 -*-
"""R3.3-77 模块间算法桥接层（网状多维交互联动）。

子模块：
  events   : 桥事件类型 + 载荷 schema 注册表
  bus      : BridgeBus（在 EventBus 之上组合扩展，schema 校验/多跳防环）
  base     : Bridge 基类（语义转换/约束通道/双向/仲裁/指标）
  metrics  : 每桥指标收集器
  arbiter  : 冲突指令仲裁器（主线不损优先级）
  pet_sink : 桌宠表现出口（真实 WS 惰性广播 / 内存 sink 捕获）

本包只做"桥"：模块之间不裸调用，而是经事件驱动 + 语义转换 + 约束通道联动。
服务层纯 Python，不 import PySide6。
"""
from __future__ import annotations

from services.bridges.bus import BridgeBus
from services.bridges.metrics import MetricsCollector
from services.bridges.arbiter import ConflictArbiter
from services.bridges.base import Bridge
from services.bridges import events  # noqa: F401

from services.bridges.task_affection_bridge import TaskAffectionBridge
from services.bridges.task_compass_bridge import TaskCompassBridge
from services.bridges.affection_pet_bridge import AffectionPetBridge
from services.bridges.memory_task_bridge import MemoryTaskBridge
from services.bridges.portrait_script_bridge import PortraitScriptBridge
from services.bridges.relationship_task_bridge import RelationshipTaskBridge
# R3.3-78：忠诚度（反制线）→ 桌宠顺从向表现桥（与 AffectionPetBridge 引导线并列，互不共用表达）
from services.bridges.loyalty_pet_bridge import LoyaltyPetBridge
# R3.3-80：复杂度引擎/里程碑模板桥接层（新增，独立文件，不碰并行代理已有桥）
from services.bridges.family_system_bridge import FamilySystemBridge
from services.bridges.milestone_task_bridge import MilestoneTaskBridge
from services.bridges.trauma_portrait_bridge import TraumaPortraitBridge
from services.bridges.complexity_publisher import publish_complexity_profile

__all__ = [
    "BridgeBus", "MetricsCollector", "ConflictArbiter", "Bridge", "events",
    "TaskAffectionBridge", "TaskCompassBridge", "AffectionPetBridge",
    "MemoryTaskBridge", "PortraitScriptBridge", "RelationshipTaskBridge",
    "LoyaltyPetBridge",
    "FamilySystemBridge", "MilestoneTaskBridge", "TraumaPortraitBridge",
    "publish_complexity_profile",
    "build_bridge_network", "shutdown_bridge_network",
]


def build_bridge_network(*, affection, task_engine, compass, memory,
                        portrait, script_lib=None, pet_sink=None, graph=None,
                        bus: BridgeBus | None = None) -> dict:
    """一键装配桥接网络（注册表）。

    传入各模块的【真实实例】，返回 dict：
      {bus, metrics, arbiter, bridges: {name: bridge}, pet_sink, graph}
    headless/demo/测试/生产都走这里，保证真接线、可观测、可断言。
    """
    metrics = MetricsCollector()
    arbiter = ConflictArbiter(metrics=metrics)
    bus = bus or BridgeBus(metrics=metrics)
    if bus.metrics is not metrics:
        # 外部传入的 bus 已有自己的 metrics，统一指向它
        metrics = bus.metrics
        arbiter.metrics = metrics

    if pet_sink is None:
        from services.bridges.pet_sink import make_default_sink
        pet_sink = make_default_sink()

    bridges: dict[str, Bridge] = {}

    def _add(b: Bridge) -> Bridge:
        b.arbiter = arbiter
        b.metrics = metrics
        b.setup()
        bridges[b.name] = b
        return b

    _add(TaskAffectionBridge(bus, affection, task_engine, metrics, arbiter))
    _add(TaskCompassBridge(bus, compass, affection, task_engine, metrics, arbiter))
    _add(AffectionPetBridge(bus, affection, pet_sink, metrics, arbiter))
    # R3.3-78：忠诚→桌宠桥（反制线；只依赖 bus+pet_sink，注册失败不炸既有桥网）
    try:
        _add(LoyaltyPetBridge(bus, pet_sink, metrics, arbiter))
    except Exception as _e:  # noqa: BLE001
        import logging as _lg
        _lg.getLogger(__name__).warning("LoyaltyPetBridge 注册失败，跳过: %s", _e)
    _add(MemoryTaskBridge(bus, memory, task_engine, metrics, arbiter))
    if portrait is not None:
        _add(PortraitScriptBridge(bus, portrait, script_lib, metrics, arbiter))
    if graph is not None:
        _add(RelationshipTaskBridge(bus, graph, task_engine, metrics, arbiter))

    # ---- R3.3-80：复杂度引擎/里程碑模板桥接层（独立 if 块，注册失败容错不炸）----
    # 只用已有的 bus/task_engine/portrait/graph；graph=None 时 family_system 仍落任务不接星图
    try:
        _add(FamilySystemBridge(bus, task_engine, graph, metrics, arbiter))
    except Exception as _e:  # noqa: BLE001
        import logging as _lg
        _lg.getLogger(__name__).warning("FamilySystemBridge 注册失败，跳过: %s", _e)
    try:
        _add(MilestoneTaskBridge(bus, task_engine, None, metrics, arbiter))
    except Exception as _e:  # noqa: BLE001
        import logging as _lg
        _lg.getLogger(__name__).warning("MilestoneTaskBridge 注册失败，跳过: %s", _e)
    try:
        _add(TraumaPortraitBridge(bus, task_engine, portrait, metrics, arbiter))
    except Exception as _e:  # noqa: BLE001
        import logging as _lg
        _lg.getLogger(__name__).warning("TraumaPortraitBridge 注册失败，跳过: %s", _e)

    net = {"bus": bus, "metrics": metrics, "arbiter": arbiter,
           "bridges": bridges, "pet_sink": pet_sink, "graph": graph}
    net["shutdown"] = lambda: shutdown_bridge_network(net)
    return net


def shutdown_bridge_network(net: dict) -> None:
    """幂等关闭桥网（退出路径调用）：错误隔离，绝不阻断退出。"""
    try:
        arb = net.get("arbiter")
        if arb is not None:
            arb.reset()
    except Exception:
        pass
    try:
        mc = net.get("metrics")
        if mc is not None:
            mc.reset()
    except Exception:
        pass
