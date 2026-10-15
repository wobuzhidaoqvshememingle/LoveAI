# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 第四批架构增强（战略层/执行轨迹/情绪动力学/状态变化日志/护栏闸口）

全部为新建独立模块或纯函数，向后兼容，不改动既有主流程关键输出。
服务层纯 Python：不 import PySide6。
"""
from __future__ import annotations
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ============ 1. 战略层 relationship_strategist ============
STAGE_GOALS = {
    "初识": {"primary": "建立舒适与信任", "axes_focus": ["Trust", "Comfort"]},
    "暧昧": {"primary": "提升亲密与激情", "axes_focus": ["Attraction", "Emotional"]},
    "热恋": {"primary": "巩固投入与承诺", "axes_focus": ["Investment", "Commitment"]},
    "稳定": {"primary": "维护激情与承诺", "axes_focus": ["passion", "commitment"]},
    "矛盾期": {"primary": "修复信任与情绪联结", "axes_focus": ["Trust", "Emotional"]},
}


def assess_stage(stage_num: int, recent_conflict: bool = False) -> str:
    if recent_conflict:
        return "矛盾期"
    return {1: "初识", 2: "初识", 3: "暧昧", 5: "暧昧", 7: "热恋", 9: "稳定", 11: "稳定"}.get(stage_num, "初识")


def relationship_strategist(axes: dict, stage_num: int, recent_conflict: bool = False) -> dict:
    """输出战略方向（约束后续策略选择/任务规划）。"""
    stage = assess_stage(stage_num, recent_conflict)
    goal = STAGE_GOALS.get(stage, STAGE_GOALS["初识"])
    health = round(sum(axes.values()) / max(1, len(axes)), 1) if axes else 50.0
    risks = [a for a, v in (axes or {}).items() if v < 35]
    return {
        "stage": stage, "stage_num": stage_num,
        "goal": goal["primary"], "axes_focus": goal["axes_focus"],
        "health_score": health, "risk_axes": risks,
        "strategy_constraint": f"本阶段优先提升 {'/'.join(goal['axes_focus'])}",
    }


# ============ 2. 执行轨迹 execution_trace ============
class ExecutionTracer:
    """记录每次分析的模块Trace，可写SQLite表 execution_traces（表不存在则仅内存）。"""

    def __init__(self, db: Any = None):
        self._db = db
        self._buf: list[dict] = []

    def log(self, module: str, input_summary: str, output_summary: str, ok: bool, ms: float) -> None:
        rec = {"ts": time.time(), "module": module, "in": input_summary[:120],
               "out": output_summary[:120], "ok": ok, "ms": round(ms, 2)}
        self._buf.append(rec)
        try:
            if self._db is not None and hasattr(self._db, "execute"):
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS execution_traces(ts REAL, module TEXT, "
                    "in_sum TEXT, out_sum TEXT, ok INTEGER, ms REAL)")
                self._db.execute(
                    "INSERT INTO execution_traces(ts,module,in_sum,out_sum,ok,ms) "
                    "VALUES(:ts,:m,:i,:o,:ok,:ms)",
                    {"ts": rec["ts"], "m": module, "i": rec["in"], "o": rec["out"],
                     "ok": 1 if ok else 0, "ms": rec["ms"]})
        except Exception as e:
            logger.debug("执行轨迹写库失败(仅内存): %s", e)

    def recent_failures(self, n: int = 3) -> list[dict]:
        return [r for r in self._buf if not r["ok"]][-n:]


# ============ 4. 情绪动力学 emotion_dynamics ============
EMOTION_HALF_LIFE_HOURS = {"愤怒": 24.0, "焦虑": 18.0, "委屈": 36.0, "开心": 12.0}
BURST_THRESHOLD = 3  # 同类负面事件累积到3次触发爆发


def decay_emotion(label: str, intensity: float, age_hours: float) -> float:
    """情绪衰减：按半衰期折半。"""
    hl = EMOTION_HALF_LIFE_HOURS.get(label, 24.0)
    return round(intensity * (0.5 ** (age_hours / hl)), 3)


def accumulate_emotion(history: list[str], label: str) -> dict:
    """同类负面事件累积；达阈值提示可能爆发。"""
    n = sum(1 for x in history if x == label)
    return {"count": n, "burst_risk": n >= BURST_THRESHOLD,
            "advice": "情绪已累积接近爆发阈值，建议先降温不追问" if n >= BURST_THRESHOLD
                      else "情绪可继续观察"}


# ============ 5. 状态变化日志 state_changes ============
class StateChangeLog:
    """中心池每次写操作记录 时间/变化量/原因/触发模块；可写 state_changes 表。"""

    def __init__(self, db: Any = None):
        self._db = db
        self._buf: list[dict] = []

    def record(self, field: str, delta: float, reason: str, source_module: str) -> None:
        rec = {"ts": time.time(), "field": field, "delta": delta,
               "reason": reason, "source": source_module}
        self._buf.append(rec)
        try:
            if self._db is not None and hasattr(self._db, "execute"):
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS state_changes(ts REAL, field TEXT, "
                    "delta REAL, reason TEXT, source TEXT)")
                self._db.execute(
                    "INSERT INTO state_changes(ts,field,delta,reason,source) "
                    "VALUES(:ts,:f,:d,:r,:s)",
                    {"ts": rec["ts"], "f": field, "d": delta, "r": reason, "s": source})
        except Exception as e:
            logger.debug("状态变化日志写库失败(仅内存): %s", e)

    def recent(self, field: str = None, n: int = 10) -> list[dict]:
        rows = self._buf if field is None else [r for r in self._buf if r["field"] == field]
        return rows[-n:]


# ============ 3. 护栏三闸口 ============
def gate_check(scene: str, modules: list[str], reply: str, strategy_direction: str) -> dict:
    """三闸口质检，返回 pass/notes。任何一项不通过给 notes 但不中断流程。"""
    notes = []
    # 闸口1：模块匹配场景
    if scene == "冲突修复" and "strategy_advisor" not in modules:
        notes.append("闸口1：冲突修复场景缺策略顾问模块")
    # 闸口2：关键数据充分性（示例：无情感标签视为数据不足）
    # 闸口3：回复符合策略方向
    if strategy_direction == "给空间" and any(k in reply for k in ("你怎么了", "为什么不回")):
        notes.append("闸口3：给空间策略下回复含追问，可能不妥")
    return {"pass": len(notes) == 0, "notes": notes}
