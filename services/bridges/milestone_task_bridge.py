# -*- coding: utf-8 -*-
"""R3.3-80 B/D 类桥：里程碑任务链 ↔ 任务系统（真写入）。

订阅两类事件：
  - COMPLEXITY_PROFILE_UPDATED（D 类）：取 rebound_pattern → 调
        MilestoneChain().plan_breakup_cycle_intervention(rebound_pattern) 落复合循环
        干预任务。文案全部落在止损 / 自我观察 / 冷静期，绝不教唆复合或报复。
  - STAGE_CHANGED（B 类）：关系阶段推进 → 调
        MilestoneChain().create_milestone_tasks(task_engine, person_id, stage)
        落该阶段可解锁的里程碑任务。

任务落库只走 task_engine.add_custom_task（铁律：不改 task_engine.py），同标题去重由其保证。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev
from services.milestone_task_templates import MilestoneChain

logger = logging.getLogger(__name__)


class MilestoneTaskBridge(Bridge):
    name = "milestone_task"

    def __init__(self, bus, task_engine, chain: MilestoneChain | None = None,
                 metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.task_engine = task_engine
        self.chain = chain or MilestoneChain()
        self.last_rebound: dict = {}
        self.last_stage: str | None = None
        self.created_titles: list[str] = []

    def subscribe_events(self) -> list[str]:
        return [ev.COMPLEXITY_PROFILE_UPDATED, ev.STAGE_CHANGED]

    # ------------------------------------------------------------------ #
    def translate_in(self, data: dict):
        et = data.get("_event_type")
        pid = data.get("person_id")
        if not pid:
            return None
        if et == ev.STAGE_CHANGED:
            stage = data.get("stage") or data.get("new_stage")
            if not stage:
                return None
            return {"dir": "stage", "person_id": pid, "stage": stage}
        if et == ev.COMPLEXITY_PROFILE_UPDATED:
            rebound = data.get("rebound_pattern")
            if rebound is None:
                profile = data.get("profile") or {}
                rebound = profile.get("rebound_pattern")
            if not rebound:
                return None
            return {"dir": "rebound", "person_id": pid, "rebound": rebound}
        return None

    def constrain(self, translated):
        return translated

    # ------------------------------------------------------------------ #
    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]

        # ---- B 类：阶段推进 → 落该阶段里程碑任务 ----
        if t["dir"] == "stage":
            stage = t["stage"]
            self.last_stage = stage
            created = self.chain.create_milestone_tasks(
                self.task_engine, pid, stage)
            for c in created:
                self.created_titles.append(c.get("title", ""))
            # 无模板（未知阶段名）也算一次联动尝试（记录到 last_stage）
            return True

        # ---- D 类：复合/拉扯循环 → 落止损/自我观察/冷静期干预任务 ----
        rebound = t["rebound"] or {}
        self.last_rebound = rebound
        try:
            plans = self.chain.plan_breakup_cycle_intervention(rebound)
        except Exception as e:  # noqa: BLE001
            logger.warning("plan_breakup_cycle_intervention 失败: %s", e)
            plans = []
        acted = False
        for plan in plans:
            title = plan.get("title", "")
            if not title:
                continue
            created = self.task_engine.add_custom_task(
                pid, title, source="milestone_breakup_cycle",
                affection_reward=float(plan.get("reward", 3.0)))
            if created:
                self.created_titles.append(title)
                acted = True
        # 有循环/纠缠/新欢信号即算一次联动（即使 cycle_count<2 无干预模板）
        has_signal = bool(rebound.get("cycle_count") or rebound.get("entangled")
                          or rebound.get("ex_new_partner"))
        return acted or has_signal

    # ------------------------------------------------------------------ #
    def network_summary(self) -> dict:
        return {
            "last_stage": self.last_stage,
            "last_rebound": self.last_rebound,
            "created_tasks": list(self.created_titles),
        }
