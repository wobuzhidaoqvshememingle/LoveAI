# -*- coding: utf-8 -*-
"""桥：人物侧写（六层画像）↔ 话术库（双向）。

方向 A（侧写 → 话术倾向）：侧写成熟度变化 → 按依恋/成熟度选话术风格，
    调 script_library.pick_scripts 真实挑话术（行为变化体现在 style 选择与返回文本）。
方向 B（话术使用 → 侧写证据）：挑过话术后 → 把"观察到的沟通偏好"作为证据
    写回六层画像（L1_SURFACE 表层印象），再 publish PORTRAIT_MATURED 闭环节点。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev

logger = logging.getLogger(__name__)


class PortraitScriptBridge(Bridge):
    name = "portrait_script"

    def __init__(self, bus, portrait, script_lib=None,
                 metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.portrait = portrait          # SixLayerProfile 实例
        self.script_lib = script_lib      # services.script_library 模块
        self.last_choice: dict | None = None

    def subscribe_events(self) -> list[str]:
        return [ev.PORTRAIT_MATURED, ev.COMPASS_RECALIBRATED]

    def translate_in(self, data: dict):
        et = data.get("_event_type")
        pid = data.get("person_id")
        if not pid:
            return None
        # 罗盘对齐结果也可触发话术风格微调（漂移→松弛，对齐→自然）
        if et == ev.COMPASS_RECALIBRATED:
            drift = data.get("drift")
            return {"dir": "pick", "person_id": pid,
                    "style": "松弛" if drift else "温柔",
                    "scene": "修复" if drift else "升温"}
        if et == ev.PORTRAIT_MATURED:
            maturity = data.get("maturity")
            style = "温柔" if (maturity is None or maturity >= 0.5) else "松弛"
            return {"dir": "pick", "person_id": pid,
                    "style": style, "scene": data.get("scene", "关心")}
        return None

    def constrain(self, translated):
        if translated is None:
            return None
        # 约束：风格白名单（与 script_library 的 style 口径对齐）
        if translated.get("style") not in ("温柔", "中", "幽默", "松弛"):
            translated["style"] = "温柔"
        return translated

    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]
        scene = t.get("scene", "破冰")
        style = t.get("style", "温柔")
        picked: list[str] = []
        if self.script_lib is not None:
            try:
                picked = self.script_lib.pick_scripts(scene, k=2, style=style)
                if not picked:  # style 过滤空 → 兜底不按风格（仍真实挑取）
                    picked = self.script_lib.pick_scripts(scene, k=2)
            except Exception as e:  # 话术库异常不炸业务
                logger.warning("挑话术失败: %s", e)
        self.last_choice = {"scene": scene, "style": style, "picked": picked}

        # 方向 B：把"沟通偏好观察"写回六层画像（真实证据写入）
        try:
            from services.six_layer_persona import Layer
            self.portrait.add_evidence(Layer.L1_SURFACE, {
                "source": "bridge_portrait_script",
                "quote": f"适配话术风格={style}（场景={scene}）",
                "weight": 0.5})
        except Exception as e:  # 画像异常不炸业务
            logger.warning("画像证据写入失败: %s", e)

        # 闭环节点：发一次成熟度事件（带当前待补层）
        # v3.2.0 Bug#5 修复：出口门禁——跑完自动读 pending_layers，
        # 有未达标层就显式 ERROR 日志报告"哪层没达标、缺什么证据"，不再静默 publish 放行。
        try:
            pending = self.portrait.pending_layers()
        except Exception as e:
            pending = []
            logger.error("读取 pending_layers 失败（出口门禁异常）: %s", e)
        if pending:
            # 显式对账：逐层列证据数
            detail = []
            for layer_name in pending:
                try:
                    evs = self.portrait.evidence.get(layer_name, [])
                    detail.append(f"{layer_name}(当前{len(evs)}条,缺{3 - len(evs)}条)")
                except Exception:
                    detail.append(f"{layer_name}(证据读取失败)")
            logger.error(
                "[出口门禁] 人物 %s 测写未达标层: %s（缺证据，需补跑或显式报告）",
                pid, "; ".join(detail))
        self.bus.publish(ev.PORTRAIT_MATURED, {
            "person_id": pid, "pending_layers": pending,
            "maturity": round(1.0 - len(pending) / 6.0, 3)})
        if self.metrics:
            self.metrics.record_event_out(self.name)
        return True
