# -*- coding: utf-8 -*-
"""桥：记忆服务 ↔ 任务线（双向）。

方向 A（记忆 → 任务线索）：学到新事实 → 由事实派生一条跟进任务
    （记住她喜欢X → 用X开启话题）。
方向 B（任务 → 记忆沉淀）：任务完成 → 把这次成功打法作为 conclusion 写进长期记忆，
    下次同类场景可被检索复用。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev

logger = logging.getLogger(__name__)


class MemoryTaskBridge(Bridge):
    name = "memory_task"

    def __init__(self, bus, memory, task_engine, metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.memory = memory
        self.task_engine = task_engine
        self.learned: list[str] = []

    def subscribe_events(self) -> list[str]:
        return [ev.MEMORY_FACT_LEARNED, ev.TASKS_COMPLETED, ev.TASK_COMPLETED_BRIDGE]

    def translate_in(self, data: dict):
        et = data.get("_event_type")
        pid = data.get("person_id")
        if not pid:
            return None
        if et == ev.MEMORY_FACT_LEARNED:
            return {"dir": "memory_to_task", "person_id": pid,
                    "content": data.get("content", "")}
        if et in (ev.TASKS_COMPLETED, ev.TASK_COMPLETED_BRIDGE):
            return {"dir": "task_to_memory", "person_id": pid,
                    "title": data.get("title", "未命名任务"),
                    "task_id": data.get("task_id", "")}
        return None

    def constrain(self, translated):
        # 约束：内容长度去噪，过长截断，空内容不联动
        c = translated.get("content") or translated.get("title") or ""
        if not str(c).strip():
            return None
        return translated

    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]
        if t["dir"] == "memory_to_task":
            content = str(t["content"])[:40]
            self.learned.append(content)
            title = f"用「{content}」作为话题切入点跟进一次"
            created = self.task_engine.add_custom_task(
                pid, title, source="bridge_memory_hint", affection_reward=2.0)
            # add_custom_task 对重复标题返回 None（去重），仍算一次成功联动
            return True
        # 方向 B：任务完成 → 沉淀为结论记忆
        title = t["title"]
        try:
            self.memory.add_conclusion(
                pid, content=f"已验证有效打法：{title}",
                category="tactic", importance=0.6)
            return True
        except Exception as e:  # 记忆表异常不炸业务
            logger.warning("记忆沉淀失败: %s", e)
            return False
