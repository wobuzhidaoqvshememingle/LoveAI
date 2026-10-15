# -*- coding: utf-8 -*-
"""R3.3-80 F 类桥：创伤驱动画像 ↔ 侧写/话术通道 + 任务线（真写入）。

订阅 COMPLEXITY_PROFILE_UPDATED，translate_in 提取 trauma_drivers：
  - 把 trauma → strategy_adaptations 以软提示 claim 到 portrait/script 通道
    （参考 portrait_script_bridge 的 claim/写证据用法；portrait 实例传入时才写真证据）；
  - 对"讨好型损耗 / 被抛弃恐惧"落"边界练习 / 稳定锚点"类任务（add_custom_task）。

铁律：伦理/心理只做客观行为关联与策略适配提示，不贴道德标签、不评判；任务文案
落在自我观察与边界建设，绝不教唆。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev
from services.bridges.arbiter import PRI_COMPASS

logger = logging.getLogger(__name__)

PRI_TRAUMA = PRI_COMPASS  # 创伤适配软提示与罗盘同级


class TraumaPortraitBridge(Bridge):
    name = "trauma_portrait"

    # 特定创伤类型 → 落地任务模板（None 表示该类型不落任务）
    _TRAUMA_TASK = {
        "讨好型损耗": ("边界练习：本周练习一次温和而明确的拒绝，不委屈自己", 3.0),
        "被抛弃恐惧": ("稳定锚点：保持可预期的回应节奏，不玩'消失试探'", 3.0),
    }

    def __init__(self, bus, task_engine, portrait=None,
                 metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.task_engine = task_engine
        self.portrait = portrait          # SixLayerProfile 实例，可为 None
        self.last_trauma: dict = {}
        self.claimed_adaptations: list[str] = []
        self.created_titles: list[str] = []

    def subscribe_events(self) -> list[str]:
        return [ev.COMPLEXITY_PROFILE_UPDATED]

    # ------------------------------------------------------------------ #
    def translate_in(self, data: dict):
        pid = data.get("person_id")
        if not pid:
            return None
        trauma = data.get("trauma_drivers")
        if trauma is None:
            profile = data.get("profile") or {}
            trauma = profile.get("trauma_drivers")
        if not trauma:
            return None
        return {"person_id": pid, "trauma_drivers": trauma}

    def constrain(self, translated):
        return translated

    # ------------------------------------------------------------------ #
    def _soft_task(self, pid: str, title: str, reward: float) -> bool:
        self.claim("task_engine", "trauma_task", title, PRI_TRAUMA,
                   meta={"source": "bridge_trauma"})
        created = self.task_engine.add_custom_task(
            pid, title, source="bridge_trauma", affection_reward=reward)
        if created:
            self.created_titles.append(title)
            return True
        return False

    # ------------------------------------------------------------------ #
    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]
        trauma = t["trauma_drivers"] or {}
        self.last_trauma = trauma

        # ---- trauma → strategy_adaptations 软提示 claim 到 portrait/script 通道 ----
        adaptations = trauma.get("strategy_adaptations") or []
        for adp in adaptations:
            if not adp:
                continue
            self.claim("portrait_script", "trauma_adaptation", adp, PRI_TRAUMA,
                       meta={"person_id": pid})
            self.claimed_adaptations.append(adp)

        # portrait 实例传入 → 把"创伤行为链路"作为证据真实写回六层画像
        if self.portrait is not None and trauma.get("traumas"):
            try:
                from services.six_layer_persona import Layer
                chain_txt = "；".join(
                    f"{x.get('type')}:{x.get('behavior_link','')}"
                    for x in trauma["traumas"][:3])
                self.portrait.add_evidence(Layer.L1_SURFACE, {
                    "source": "bridge_trauma_portrait",
                    "quote": f"创伤行为链路：{chain_txt}",
                    "weight": 0.5})
            except Exception as e:  # noqa: BLE001
                logger.warning("trauma 写画像证据失败: %s", e)

        # ---- 讨好型损耗 / 被抛弃恐惧 → 落边界练习 / 稳定锚点任务 ----
        acted = False
        for tr in trauma.get("traumas") or []:
            ttype = tr.get("type", "")
            tpl = self._TRAUMA_TASK.get(ttype)
            if tpl:
                title, reward = tpl
                if self._soft_task(pid, title, reward):
                    acted = True

        return acted or bool(trauma.get("traumas"))  # 有创伤画像即算一次联动

    # ------------------------------------------------------------------ #
    def network_summary(self) -> dict:
        return {
            "trauma_types": [x.get("type") for x in self.last_trauma.get("traumas", [])],
            "claimed_adaptations": list(self.claimed_adaptations),
            "created_tasks": list(self.created_titles),
        }
