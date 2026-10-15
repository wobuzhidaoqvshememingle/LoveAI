# -*- coding: utf-8 -*-
"""桥：任务线 ↔ 方向罗盘（双向）。

方向 A（任务/好感 → 罗盘重校准）：任务完成或好感变化 → 用真实好感分组装 snapshot，
    调 direction_compass.evaluate 真实重算三重闸门/漂移/结局倒推缺口，
    再 publish COMPASS_RECALIBRATED，把缺口/漂移向下游广播。
方向 B（罗盘 → 任务选取）：罗盘重校准后 → 取最该补的缺口，落地一条对齐任务；
    漂移下行时软写入"给空间"节奏提示（走仲裁，罗盘=PRI_COMPASS）。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev
from services.bridges.arbiter import PRI_COMPASS

logger = logging.getLogger(__name__)


class TaskCompassBridge(Bridge):
    name = "task_compass"

    def __init__(self, bus, compass, affection, task_engine,
                 metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.compass = compass
        self.affection = affection
        self.task_engine = task_engine
        self.last_verdict: dict | None = None

    def subscribe_events(self) -> list[str]:
        return [ev.TASKS_COMPLETED, ev.TASK_COMPLETED_BRIDGE,
                ev.AFFECTION_LEVEL_UPDATED, ev.COMPASS_RECALIBRATED]

    def translate_in(self, data: dict):
        et = data.get("_event_type")
        pid = data.get("person_id")
        if not pid:
            return None
        if et in (ev.TASKS_COMPLETED, ev.TASK_COMPLETED_BRIDGE,
                  ev.AFFECTION_LEVEL_UPDATED):
            return {"dir": "recalibrate", "person_id": pid,
                    "trigger": et, "total": data.get("total")}
        if et == ev.COMPASS_RECALIBRATED:
            raw_drift = data.get("drift")
            if isinstance(raw_drift, bool):
                drift = {"drift": raw_drift, "reasons": []}
            elif not isinstance(raw_drift, dict):
                drift = {}
            else:
                drift = raw_drift
            return {"dir": "apply_gaps", "person_id": pid,
                    "gaps": data.get("gaps") or [],
                    "drift": drift}
        return None

    def constrain(self, translated):
        return translated

    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]
        if t["dir"] == "recalibrate":
            # 读真实好感，组装罗盘 snapshot
            scores = self.affection.get_current(pid)
            total = self.affection.get_total(scores)
            # 用好感总分近似一个"双向奔赴/满意度"指标喂给罗盘（确定性映射）
            ratio = max(0.0, min(1.0, total / 100.0))
            snapshot = {
                "metrics": {"mutual": ratio, "satisfaction": ratio,
                            "stability": ratio},
                "affection_trend": "up" if t.get("total") is None or t.get("total", 0) >= total else "flat",
                "strategy_text": "推进 修复 双向",
            }
            verdict = self.compass.evaluate(pid, snapshot)
            self.last_verdict = verdict
            self.bus.publish(ev.COMPASS_RECALIBRATED, {
                "person_id": pid,
                "aligned": bool(verdict.get("aligned")),
                "drift": verdict.get("drift") or {"drift": False},
                "gaps": verdict.get("gaps") or [],
                "alignment_score": verdict.get("alignment_score"),
                "trigger": t["trigger"]})
            if self.metrics:
                self.metrics.record_event_out(self.name)
            return True

        # 方向 B：罗盘缺口 → 落地对齐任务
        drift = t["drift"] or {}
        if drift.get("drift"):
            # 漂移下行：软写入"给空间"节奏（仲裁：罗盘优先级低于好感）
            self.claim("task_engine", "rhythm_hint", "给空间·暂停激进推进",
                       PRI_COMPASS, meta={"reason": drift.get("reasons")})
            self.task_engine.add_custom_task(
                pid, "回到结局倒推：先对齐方向再推进（给空间复盘）",
                source="bridge_compass_drift", affection_reward=1.0)
            return True
        gaps = t["gaps"] or []
        if not gaps:
            return False
        top = gaps[0]
        dim_name = top.get("name", "关系对齐")
        title = f"补齐「{dim_name}」这一结局缺口"
        created = self.task_engine.add_custom_task(
            pid, title, source="bridge_compass_gap", affection_reward=2.0)
        return created is not None
