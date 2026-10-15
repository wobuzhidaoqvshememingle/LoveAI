# -*- coding: utf-8 -*-
"""桥：任务线 ↔ 好感度引擎（双向）。

方向 A（任务 → 好感）：任务完成事件 → affection.add_bonus 真实加分；
    读完真实总分/阶段后 publish AFFECTION_LEVEL_UPDATED，触发下游多跳。
方向 B（好感 → 任务）：好感等级变化 → 反哺任务难度/节奏：
    高分上行加一条"推进"任务，低分下行加一条"修复/给空间"任务（真实写 tasks 表，
    自带按标题去重，不刷屏）。软节奏写入走仲裁钩子（好感=PRI_AFFECTION）。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev
from services.bridges.arbiter import PRI_AFFECTION

logger = logging.getLogger(__name__)


class TaskAffectionBridge(Bridge):
    name = "task_affection"

    def __init__(self, bus, affection, task_engine, metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.affection = affection
        self.task_engine = task_engine

    def subscribe_events(self) -> list[str]:
        return [ev.TASKS_COMPLETED, ev.TASK_COMPLETED_BRIDGE,
                ev.AFFECTION_LEVEL_UPDATED]

    # ---- 方向 A：任务完成 → 好感加分 ---- #
    def translate_in(self, data: dict):
        et = data.get("_event_type") or data.get("event")
        pid = data.get("person_id")
        if not pid:
            return None
        # 任务完成类事件
        if et in (ev.TASKS_COMPLETED, ev.TASK_COMPLETED_BRIDGE):
            reward = float(data.get("reward") or data.get("affection_reward") or 3.0)
            return {"dir": "task_to_affection", "person_id": pid,
                    "reward": reward, "title": data.get("title", "")}
        # 好感变化类事件（方向 B 入口）
        if et == ev.AFFECTION_LEVEL_UPDATED:
            return {"dir": "affection_to_task", "person_id": pid,
                    "total": float(data.get("total") or 0.0),
                    "trend": data.get("trend") or "flat"}
        return None

    def constrain(self, translated):
        # 约束通道：奖励幅度钳制在 [0.5, 10]（单次任务奖励不允许刷分/不允许为负）
        if translated["dir"] == "task_to_affection":
            translated["reward"] = max(0.5, min(10.0, float(translated["reward"])))
        return translated

    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]
        if t["dir"] == "task_to_affection":
            # 真实写：好感加分
            self.affection.add_bonus(pid, t["reward"], reason="桥接:任务完成")
            scores = self.affection.get_current(pid)
            total = self.affection.get_total(scores)
            stage = self.affection.get_stage(total)
            # 向下游发下一跳（事件驱动，不轮询）
            self.bus.publish(ev.AFFECTION_LEVEL_UPDATED, {
                "person_id": pid, "total": round(total, 1),
                "stage": stage, "trend": "up",
                "delta": t["reward"], "reason": "任务完成桥接加分"})
            if self.metrics:
                self.metrics.record_event_out(self.name)
            return True

        # 方向 B：好感 → 任务节奏/难度
        total = t["total"]
        if total >= 70 and t["trend"] in ("up", "up_slow"):
            title = "趁势推进一次（把关系往下一步引）"
            reward = 4.0
        elif total < 40:
            title = "降温修复：低压力给空间，不追问不逼表态"
            reward = 2.0
        else:
            return False  # 中段平稳，无需调速（去噪：不产生联动）
        # 软节奏写入走仲裁（主线任务结构本身不被此覆盖）
        self.claim("task_engine", "rhythm_hint", title, PRI_AFFECTION,
                   meta={"total": total, "trend": t["trend"]})
        created = self.task_engine.add_custom_task(
            pid, title, source="bridge_affection_rhythm", affection_reward=reward)
        return created is not None
