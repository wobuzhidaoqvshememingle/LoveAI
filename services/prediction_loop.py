# -*- coding: utf-8 -*-
"""
LoveAI v3.2.0 - 预测-反馈闭环（PredictionLoop 状态机 + ReactionInventory）

【借鉴卡片】
- M2-1（ECMWF 集合预报 + EnKF 观测同化）：观测独立于预报，同化只更新
  "初始场"（snapshot 先验），不直接改模型参数（twin prompt）。
- M2-2（做市商库存偏移 Ho-Stoll）：不预测方向，只对已发生的反应做无偏
  库存对冲——维护最近 N 条反应标签的 EMA offset，预测先验 = base + offset。

【硬约束】
- 本模块**不修改 digital_twin.py 的任何算法**，只在 facade 层接线。
- assimilate() 只写 snapshot 先验（内存 dict），绝不回写 twin system prompt；
  twin prompt 的变更登记在 twin_calibration.log，由人工每 50 条审阅。
- 纯 Python，不 import PySide6，不 import digital_twin（避免循环依赖）。

【状态机】
    PREDICTING → AWAITING_REACTION → OBSERVING → ASSIMILATING → PREDICTING
       (生成预测)     (等对方真实反应)     (观测到反应)  (同化进 snapshot)
"""
from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class LoopState(str, Enum):
    """预测闭环四态。"""
    PREDICTING = "PREDICTING"            # 正在生成预测（twin.predict_reaction）
    AWAITING_REACTION = "AWAITING_REACTION"  # 预测已发，等对方真实反应
    OBSERVING = "OBSERVING"             # 观测到对方反应，正在打标签
    ASSIMILATING = "ASSIMILATING"        # 同化进 snapshot 先验（不改 twin prompt）


# --------------------------------------------------------------------------- #
# ReactionInventory（M2-2 做市商库存偏移）
# --------------------------------------------------------------------------- #
class ReactionInventory:
    """最近 N 条对方反应标签的 EMA 库存偏移。

    标签约定：
        +1.0 = 积极（主动/热情/破冰/接受）
         0.0 = 中性（平淡/客套/正常应答）
        -1.0 = 消极（冷淡/敷衍/冲突/回避）

    offset = EMA(labels, α=0.2)，范围 [-1, +1]。
    预测时：predicted_score = base_score + offset * OFFSET_GAIN。
    offset **绝不写回 twin 参数**，只作为 snapshot 的一个独立 key。
    """

    DEFAULT_WINDOW: int = 10
    DEFAULT_ALPHA: float = 0.2
    # offset 放大系数：offset=+1 时对 0-100 预测分加 8 分
    OFFSET_GAIN: float = 8.0

    def __init__(self, window: int = DEFAULT_WINDOW, alpha: float = DEFAULT_ALPHA) -> None:
        self.window: int = int(window)
        self.alpha: float = float(alpha)
        self._labels: list[float] = []
        self._offset: float = 0.0
        self._lock = threading.Lock()

    def record(self, label: float) -> float:
        """记录一条反应标签，返回更新后的 offset。"""
        try:
            v = max(-1.0, min(1.0, float(label)))
        except Exception:
            v = 0.0
        with self._lock:
            self._labels.append(v)
            if len(self._labels) > self.window:
                self._labels = self._labels[-self.window:]
            # EMA 更新：新 offset = (1-α) * old + α * label
            self._offset = (1.0 - self.alpha) * self._offset + self.alpha * v
        return self._offset

    @property
    def offset(self) -> float:
        """当前库存偏移 [-1, +1]。"""
        return self._offset

    def adjusted_score(self, base_score: float) -> float:
        """把库存偏移叠加到 base_score（0-100）上，输出调整后分数。"""
        try:
            base = float(base_score)
        except Exception:
            base = 50.0
        return round(max(0.0, min(100.0, base + self._offset * self.OFFSET_GAIN)), 1)

    def snapshot(self) -> dict:
        """导出当前库存状态（写进 person_state_snapshot 的独立 key）。"""
        return {
            "offset": round(self._offset, 3),
            "window": self.window,
            "alpha": self.alpha,
            "recent_labels": list(self._labels),
            "updated_at": time.time(),
        }


# --------------------------------------------------------------------------- #
# PredictionLoop（M2-1 集合预报同化的最小落地形态）
# --------------------------------------------------------------------------- #
class PredictionLoop:
    """预测-反馈闭环状态机（per person_id）。

    用法：
        loop = PredictionLoop(person_id="u_123")
        pred = loop.begin_predict(base_prediction={"likely_reaction": "积极", "confidence": 0.5})
        # ... 等对方真实反应到来 ...
        obs = loop.observe(label=+1.0, note="对方主动分享了周末计划")
        snap_update = loop.assimilate()   # 只更新 snapshot 先验，不碰 twin prompt

    关键边界：
    - assimilate() 返回的 dict 会被 facade 合并进 person_state_snapshot，
      作为 "twin.prior_offset" / "twin.loop_state" 等独立 key；
    - 任何 twin system prompt 的变更**不在本模块内触发**，只记录
      twin_calibration_pending=True 等待人工审阅。
    """

    def __init__(self, person_id: str,
                 inventory: Optional[ReactionInventory] = None) -> None:
        self.person_id: str = person_id
        self.state: LoopState = LoopState.PREDICTING
        self.inventory: ReactionInventory = inventory or ReactionInventory()
        self._current_prediction: Optional[dict] = None
        self._predicted_at: float = 0.0
        self._last_observation: Optional[dict] = None
        self._lock = threading.Lock()

    # ---- 状态迁移 ---- #
    def begin_predict(self, base_prediction: dict) -> dict:
        """PREDICTING → AWAITING_REACTION。记录基础预测 + 库存偏移调整。"""
        with self._lock:
            self.state = LoopState.AWAITING_REACTION
            base_conf = float(base_prediction.get("confidence", 0.5))
            adjusted = self.inventory.adjusted_score(base_conf * 100.0)
            out = dict(base_prediction)
            out["confidence"] = round(adjusted / 100.0, 3)
            out["_offset"] = self.inventory.offset
            out["_loop_state"] = self.state.value
            self._current_prediction = out
            self._predicted_at = time.time()
        logger.debug("PredictionLoop[%s] PREDICTING→AWAITING_REACTION offset=%.3f",
                     self.person_id, self.inventory.offset)
        return out

    def observe(self, label: float, note: str = "") -> dict:
        """AWAITING_REACTION → OBSERVING。记录对方真实反应标签。"""
        with self._lock:
            self.state = LoopState.OBSERVING
            offset = self.inventory.record(label)
            obs = {
                "label": label,
                "note": note[:200],
                "offset_after": round(offset, 3),
                "observed_at": time.time(),
                "lag_sec": round(time.time() - self._predicted_at, 1) if self._predicted_at else None,
            }
            self._last_observation = obs
        logger.debug("PredictionLoop[%s] AWAITING→OBSERVING label=%s offset=%.3f",
                     self.person_id, label, offset)
        return obs

    def assimilate(self) -> dict:
        """OBSERVING → ASSIMILATING → PREDICTING。

        只更新 snapshot 先验（返回的 dict 由 facade 合并进 person snapshot），
        **不修改 twin system prompt**。
        """
        with self._lock:
            self.state = LoopState.PREDICTING  # 同化完立即回到待预测态
            snap_update = {
                "twin.loop_state": "PREDICTING",
                "twin.prior_offset": self.inventory.offset,
                "twin.last_observation": self._last_observation,
                # 不在这里触发 twin prompt 变更；每 50 条由人工审阅
                "twin_calibration_pending": True,
                "assimilated_at": time.time(),
            }
        logger.debug("PredictionLoop[%s] OBSERVING→ASSIMILATING→PREDICTING (snapshot-only)",
                     self.person_id)
        return snap_update

    # ---- 只读视图 ---- #
    def status(self) -> dict:
        return {
            "person_id": self.person_id,
            "state": self.state.value,
            "inventory": self.inventory.snapshot(),
            "current_prediction": self._current_prediction,
            "last_observation": self._last_observation,
        }


# --------------------------------------------------------------------------- #
# 全局 per-person 注册表（进程内单例，避免重复建 Inventory）
# --------------------------------------------------------------------------- #
_LOOP_REGISTRY: dict[str, PredictionLoop] = {}
_REG_LOCK = threading.Lock()


def get_loop(person_id: str) -> PredictionLoop:
    """按 person_id 取/建 PredictionLoop（进程内缓存）。"""
    if not person_id:
        # 无 person_id 时返回一次性实例（不进注册表）
        return PredictionLoop(person_id="__anonymous__")
    with _REG_LOCK:
        if person_id not in _LOOP_REGISTRY:
            _LOOP_REGISTRY[person_id] = PredictionLoop(person_id=person_id)
        return _LOOP_REGISTRY[person_id]


def reset_loop(person_id: str) -> None:
    """清空某人物的闭环状态（测试/重置用）。"""
    with _REG_LOCK:
        _LOOP_REGISTRY.pop(person_id, None)
