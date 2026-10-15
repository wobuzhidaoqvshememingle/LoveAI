# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 人物状态快照（Person State Snapshot · 中心数据池 + 阀门机制）

目的：消除模块孤岛。每次分析后 / 数字分身调用前，从数据库与各服务
实时拉取该对象的**完整、最新**状态，汇总成一份快照，供数字分身预测、
策略生成、任务调度等统一消费。

【不可变原则 · IMPORTANT】
快照一经构建即为**不可变对象**：模块只从快照**读取**数据，不允许在
构建完成后修改 snap["xxx"]=... ；任何模块的新计算结果都应作为 extra
交回编排器，由编排器**重新构建一份新快照**。这避免多模块并发写同一份
中心数据池导致的紊乱。

【阀门机制 · 字段所有权】
- DB 拉取字段（extra **不可覆盖**）：profile / affection / stage /
  recent_chats / tasks / memory —— 这些只能由本模块从数据库实时拉取；
  调用方若在 extra 中传入，将被拒绝并打 warning。
- 模块专属字段（extra **可传入**）：financial / loss_aversion /
  strategy / manipulation_risk / no_reply_status / emotion /
  personality / social_position / style_profile —— 各模块自己计算后塞入。
- extra 传入**不在白名单**的字段：打 warning 但不拒绝（向后兼容）。

【版本与时间戳】
- 快照整体带 snapshot_version（进程内递增整数）与 built_at（构建时间）；
- 每个字段带 updated_at；DB 拉取字段用构建时刻，extra 字段取调用方传值
  或构建时刻。未来可据此比较多份快照哪个更新。

【单模块隔离】任何字段拉取/校验失败，标注 estimated=True，绝不抛异常。

服务层纯 Python：不 import PySide6。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ============================================================
# Blackboard Keys 注册表（中心池字段标准化）
# 每个 Key：类型 / 取值范围 / 写入者模块 / 触发条件 / 默认值
# 所有模块读写中心池必须通过此表，不随意写字段。
# ============================================================
BLACKBOARD_KEYS = {
    "affection.total":       {"type": "float", "range": [0, 100], "writer": "affection_engine",
                               "trigger": "对话/任务完成后", "default": 50.0},
    "stage":                 {"type": "int", "range": [1, 11], "writer": "affection_engine",
                               "trigger": "好感度跨越阈值", "default": 1},
    "emotion.pad":           {"type": "dict", "range": {"p": [0,1], "a": [0,1], "d": [0,1]},
                               "writer": "emotion", "trigger": "收到新消息", "default": {"p":0.5,"a":0.5,"d":0.5}},
    "strategy.direction":     {"type": "enum", "range": ["推进","给空间","反制","引导投入","修复"],
                               "writer": "strategy_advisor", "trigger": "emotion/persona/state变化", "default": "引导投入"},
    "strategy.style":        {"type": "enum", "range": ["warm_water","direct"],
                               "writer": "strategy_advisor", "trigger": "策略生成", "default": "warm_water"},
    "loss_aversion.loss_index":{"type": "float", "range": [0,100], "writer": "loss_aversion",
                               "trigger": "financial更新", "default": 50.0},
    "financial.contribution":{"type": "dict", "range": {}, "writer": "financial_analyzer",
                               "trigger": "提取到财务信号", "default": {}},
    "tasks.active":          {"type": "list", "range": [], "writer": "task_engine",
                               "trigger": "策略/状态变化", "default": []},
    "twin.prediction":       {"type": "dict", "range": {}, "writer": "digital_twin",
                               "trigger": "快照更新后", "default": {}},
    "relationship_axes":     {"type": "dict", "range": {"Trust":None,"Attraction":None,"Comfort":None,
                               "Investment":None,"Respect":None,"Emotional":None},
                               "writer": "affection_engine", "trigger": "好感度更新", "default": {}},
    "love_triangle":          {"type": "dict", "range": {"intimacy":[0,100],"passion":[0,100],"commitment":[0,100]},
                               "writer": "persona_analyzer", "trigger": "画像更新", "default": {}},
}

def known_key(name: str) -> bool:
    """该字段是否在 Blackboard Key 注册表内。"""
    return name in BLACKBOARD_KEYS

# ---- 字段所有权（阀门白名单）----
DB_OWNED_FIELDS = frozenset({
    "profile", "affection", "stage", "recent_chats", "tasks", "memory",
})
EXTRA_ALLOWED_FIELDS = frozenset({
    "financial", "loss_aversion", "strategy", "manipulation_risk",
    "no_reply_status", "emotion", "personality", "social_position",
    "style_profile",
})

# 进程内快照版本计数器（多份快照可比较新旧）
_snapshot_counter = 0


def _next_version() -> int:
    global _snapshot_counter
    _snapshot_counter += 1
    return _snapshot_counter


def _safe(db, sql, params):
    try:
        cur = db.execute(sql, params) if hasattr(db, "execute") else None
        return cur.fetchall() if cur else []
    except Exception as e:  # noqa: BLE001
        logger.debug("快照读取失败 sql=%s: %s", sql[:40], e)
        return []


def _validate_extra(key: str, val: Any) -> Any:
    """对 extra 字段做基本类型校验；失败返回带 estimated 的占位，不拒绝。"""
    now = time.time()
    if key == "loss_aversion":
        # 仅当非dict、或 loss_index 存在但非数值时判无效；缺 loss_index 但有其他有效信息仍保留
        if not isinstance(val, dict):
            logger.debug("loss_aversion 校验失败（非dict），标 estimated")
            return {"estimated": True, "updated_at": now}
        li = val.get("loss_index")
        if li is not None and not isinstance(li, (int, float)):
            logger.debug("loss_aversion.loss_index 非数值，标 estimated")
            return {"estimated": True, "updated_at": now}
    elif key == "strategy":
        if not isinstance(val, dict):
            logger.debug("strategy 校验失败，标 estimated")
            return {"estimated": True, "updated_at": now}
    elif key == "affection":
        # 仅防御：DB 字段正常不会走这，extra 若传 affection 也会被阀门拦
        if isinstance(val, dict):
            t = val.get("total")
            if not isinstance(t, (int, float)) or not (0 <= t <= 100):
                return {"estimated": True, "updated_at": now}
    return val


def build_person_snapshot(
    db: Any,
    person_id: str,
    extra: Optional[dict] = None,
    memory_service: Any = None,
    recent_chats_limit: int = 20,
    update_chain: Optional[list] = None,
) -> dict:
    """构建人物完整状态快照（带阀门/版本/时间戳/循环链）。

    Args:
        db: Database 实例。
        person_id: 对象 ID。
        extra: 本次分析的瞬时产物（各模块计算好的）。仅 EXTRA_ALLOWED_FIELDS
            内字段会被采纳；DB_OWNED_FIELDS 被拒绝；其余字段打 warning。
        memory_service: 可选 MemoryService。
        recent_chats_limit: 最近对话条数（默认 20）。
        update_chain: 触发本次快照更新的模块链（循环检测预留），如 ["emotion","strategy"]。

    Returns:
        完整快照 dict；不可变；缺失/失败字段标 estimated。
    """
    extra = extra or {}
    now = time.time()
    snap: dict = {"person_id": person_id}
    # 字段时间戳独立存放（保持各字段外形向后兼容：列表/整型不变）
    ts: dict = {}

    # ---- 基本信息 / 画像 ----
    rows = _safe(db, "SELECT nickname,gender,occupation,relationship_stage FROM persons WHERE id=:p",
                 {"p": person_id})
    p = rows[0] if rows else None
    if p:
        snap["profile"] = {
            "nickname": p[0], "gender": p[1], "occupation": p[2],
            "relationship_stage": p[3],
        }
    else:
        snap["profile"] = {"nickname": "未知", "estimated": True}
    ts["profile"] = now

    # ---- 好感度 + 阶段（实时推导）----
    aff_rows = _safe(db, "SELECT total_score, scores_json FROM current_affection WHERE person_id=:p",
                     {"p": person_id})
    if aff_rows:
        total = aff_rows[0][0] or 50.0
        try:
            scores = json.loads(aff_rows[0][1]) if aff_rows[0][1] else {}
        except Exception:
            scores = {}
        snap["affection"] = {"total": total, "scores": scores}
        # 阶段由好感度实时推导（不硬依赖 persons.relationship_stage）
        snap["stage"] = _stage_from_affection(total)
    else:
        snap["affection"] = {"total": 50.0, "estimated": True}
        snap["stage"] = snap["profile"].get("relationship_stage", 0)
    ts["affection"] = now
    ts["stage"] = now

    # ---- 最近对话（20 条）----
    msgs = _safe(
        db,
        "SELECT direction, content FROM chat_messages WHERE person_id=:p "
        "ORDER BY id DESC LIMIT :n",
        {"p": person_id, "n": recent_chats_limit},
    )
    snap["recent_chats"] = [
        {"direction": m[0], "content": (m[1] or "")[:200]} for m in reversed(msgs)
    ]
    ts["recent_chats"] = now

    # ---- 活跃任务 top5 ----
    tasks = _safe(
        db,
        "SELECT title, status FROM tasks WHERE person_id=:p AND status IN ('active','in_progress','paused') "
        "ORDER BY rowid DESC LIMIT 5",
        {"p": person_id},
    )
    snap["tasks"] = [{"title": t[0], "status": t[1]} for t in tasks]
    ts["tasks"] = now

    # ---- 长记忆 ----
    if memory_service is not None:
        try:
            snap["memory"] = {"summary": memory_service.get_memory_context(person_id, top_k=10)}
        except Exception as e:  # noqa: BLE001
            logger.debug("快照拉取记忆失败: %s", e)
            snap["memory"] = {"estimated": True}
    else:
        snap["memory"] = {"estimated": True}
    ts["memory"] = now

    # ---- extra 阀门：白名单校验 + DB 字段拒绝覆盖 ----
    for key, val in extra.items():
        if key in DB_OWNED_FIELDS:
            logger.warning("快照阀门：extra 试图覆盖 DB 字段 '%s'，已拒绝", key)
            continue
        if key not in EXTRA_ALLOWED_FIELDS:
            logger.warning("快照阀门：extra 非法字段 '%s'，仅记录不采纳", key)
            continue
        if val is None:
            snap[key] = {"estimated": True}
            ts[key] = now
            continue
        val = _validate_extra(key, val)
        snap[key] = val
        ts[key] = now

    # 缺失的 extra 字段占位
    for key in EXTRA_ALLOWED_FIELDS:
        if key not in snap:
            snap[key] = {"estimated": True}
            ts[key] = now

    # ---- 版本 / 时间戳 / 循环链 ----
    snap["snapshot_version"] = _next_version()
    snap["built_at"] = now
    snap["updated_at_map"] = ts
    snap["update_chain"] = list(update_chain or [])

    return snap


def _stage_from_affection(total: float) -> int:
    """由好感度总分粗推关系阶段（1-11 级）。"""
    try:
        total = float(total)
    except Exception:
        return 0
    if total >= 90:
        return 11
    if total >= 80:
        return 9
    if total >= 70:
        return 7
    if total >= 60:
        return 5
    if total >= 50:
        return 3
    return 1
