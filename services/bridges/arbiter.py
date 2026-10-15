# -*- coding: utf-8 -*-
"""R3.3-77 冲突指令仲裁器（一致性约束 / 主线不损）。

网状联动下，多座桥可能对【同一个可写目标的同一个软键】下发相互冲突的值。
本仲裁器按文档化优先级裁决，保证多跳后主线上限不被破坏：

    优先级（数字越小越高）：
        1 任务线(task)      —— 主线上限，最高，不可被任何下游桥改写
        2 好感(affection)
        3 罗盘(compass)     —— 结局对齐建议（软提示）
        4 表现微调(expression) —— 桌宠表现，最 cosmetic，最低

设计：
- 只仲裁"软提示"类写入（如任务节奏/推进姿态）；主线上限任务结构本身不走仲裁，
  因为任务线恒为最高优先级，下游天然无法覆盖。
- settle 窗口：同一 (target, key) 在一次 settle 内登记多个候选，裁决出一个生效值。
- 高优先级源胜出；同优先级后到者覆盖先到者（确定性：按登记顺序，后者赢，
  便于测试断言）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---- 优先级常量（数字越小越优先）---- #
PRI_TASK = 1
PRI_AFFECTION = 2
PRI_COMPASS = 3
PRI_EXPRESSION = 4

_PRIORITY_NAME = {
    PRI_TASK: "task",
    PRI_AFFECTION: "affection",
    PRI_COMPASS: "compass",
    PRI_EXPRESSION: "expression",
}


class ConflictArbiter:
    def __init__(self, metrics=None) -> None:
        # (target, key) -> list of (priority, source_bridge, value, meta)
        self._claims: dict[tuple, list[tuple]] = {}
        self.metrics = metrics
        self.last_decision: dict | None = None

    # ------------------------------------------------------------------ #
    def claim(self, target: str, key: str, value, priority: int,
              source_bridge: str, meta: dict | None = None) -> dict:
        """登记一条软写入诉求。返回裁决结果 dict。

        立即裁决：与已登记的同键候选比较，返回当前应生效的值；
        若本候选优先级更高则反超，否则维持原胜者。
        """
        bucket_key = (target, key)
        claim = (int(priority), source_bridge, value, meta or {})
        self._claims.setdefault(bucket_key, []).append(claim)

        # 选胜者：优先级最小者胜；同优先级最后登记的胜（后者覆盖）
        winner = self._pick_winner(self._claims[bucket_key])
        is_winner = (winner == claim)

        decision = {
            "target": target, "key": key,
            "value": winner[2], "winner_bridge": winner[1],
            "winner_priority": winner[0],
            "winner_priority_name": _PRIORITY_NAME.get(winner[0], str(winner[0])),
            "is_winner": is_winner,
            "contenders": [
                {"bridge": c[1], "priority": c[0], "value": c[2]}
                for c in self._claims[bucket_key]
            ],
        }
        self.last_decision = decision
        if not is_winner and self.metrics is not None:
            self.metrics.record_arbitrated(1)
            logger.info("仲裁压制 %s.%s：%s(pri=%s) 被 %s(pri=%s) 覆盖",
                        target, key, source_bridge, priority,
                        winner[1], winner[0])
        return decision

    @staticmethod
    def _pick_winner(claims: list[tuple]):
        """优先级最小者胜；同优先级最后登记（列表末尾）胜。"""
        best = claims[0]
        for c in claims[1:]:
            if c[0] < best[0]:
                best = c
            elif c[0] == best[0]:
                best = c  # 后者覆盖
        return best

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self._claims.clear()
        self.last_decision = None
