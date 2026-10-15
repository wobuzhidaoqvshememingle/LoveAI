# -*- coding: utf-8 -*-
"""桥：好感度引擎 ↔ 桌宠表现（双向）。

方向 A（好感 → 桌宠表现）：好感等级变化 → 映射成桌宠情绪事件并投到表现出口
    （生产态走 app.pet_ws_server._broadcast，headless 走内存 sink 捕获）。
方向 B（桌宠上行 → 好感微反馈）：用户与桌宠交互（抚摸/点击）→ 极小幅度真实加分，
    再发 AFFECTION_LEVEL_UPDATED 闭环节点。
表现是最 cosmetic 的一层：仲裁优先级最低（PRI_EXPRESSION）。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev
from services.bridges.arbiter import PRI_EXPRESSION

logger = logging.getLogger(__name__)

# 好感总分 → 桌宠情绪映射（确定性，符合游戏化恋爱氛围）
def _affection_to_emotion(total: float) -> str:
    if total >= 75:
        return "happy"
    if total >= 55:
        return "curious"
    if total >= 40:
        return "neutral"
    return "sad"


class AffectionPetBridge(Bridge):
    name = "affection_pet"

    def __init__(self, bus, affection, pet_sink,
                 metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.affection = affection
        self.sink = pet_sink  # callable(event_dict)
        self.last_emotion: str | None = None

    def subscribe_events(self) -> list[str]:
        return [ev.AFFECTION_LEVEL_UPDATED, ev.AFFECTION_CHANGED,
                ev.PET_USER_INTERACTION]

    def translate_in(self, data: dict):
        et = data.get("_event_type")
        pid = data.get("person_id")
        if et == ev.PET_USER_INTERACTION:
            if not pid:
                return None
            return {"dir": "pet_to_affection", "person_id": pid,
                    "kind": data.get("kind", "pat"),
                    "intensity": float(data.get("intensity") or 1.0)}
        if et in (ev.AFFECTION_LEVEL_UPDATED, ev.AFFECTION_CHANGED):
            if not pid:
                return None
            total = float(data.get("total") or 0.0)
            return {"dir": "affection_to_pet", "person_id": pid, "total": total}
        return None

    def constrain(self, translated):
        if translated["dir"] == "pet_to_affection":
            # 约束：单次互动微反馈钳制在 [0.1, 1.0]，防止桌宠刷好感
            translated["intensity"] = max(0.1, min(1.0, translated["intensity"]))
        return translated

    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]
        if t["dir"] == "affection_to_pet":
            total = t["total"]
            emotion = _affection_to_emotion(total)
            self.last_emotion = emotion
            # 表现软写入走仲裁（最低优先级，不影响主线）
            self.claim("pet", "emotion", emotion, PRI_EXPRESSION,
                       meta={"total": total})
            # 真实投到桌宠表现出口（sink 在 headless 下捕获事件流）
            event = {"event": "emotion",
                     "data": {"affection": total, "emotion": emotion}}
            self.sink(event)
            return True

        # 方向 B：桌宠交互 → 好感微反馈
        micro = round(t["intensity"] * 0.5, 2)  # 0.05~0.5 分
        self.affection.add_bonus(pid, micro, reason=f"桥接:桌宠互动{t['kind']}")
        scores = self.affection.get_current(pid)
        total = self.affection.get_total(scores)
        self.bus.publish(ev.AFFECTION_LEVEL_UPDATED, {
            "person_id": pid, "total": round(total, 1),
            "trend": "up", "delta": micro, "reason": "桌宠互动微反馈"})
        if self.metrics:
            self.metrics.record_event_out(self.name)
        return True
