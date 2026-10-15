# -*- coding: utf-8 -*-
"""
LoveAI 演示数据种子脚本（一次性，幂等）

用途：为 5 个新 Qt 组件（关系时间线 / 任务看板 / 数字分身 / 右侧快捷面板 /
资料优化卡）提供可真实渲染的数据。

做法：
- 固定 id 创建/重建一个演示对象「演示·林小晚」，并写 settings.active_person_id 置为当前；
- 写 18 维 current_affection（雷达 + 快捷面板四维聚合来源）；
- 写 14 天 affection_snapshots，曲线含多次 |Δ|>=5 的阶段跃迁/回落（时间线标注）；
- 写 7 个任务，覆盖 pending / in_progress / completed / abandoned 四档（看板四列）。

重复执行：先按固定 id 删除旧演示数据再插入，不会产生重复行；不影响其它真实对象。

用法（项目根目录，用 venv）：
    .venv\\Scripts\\python.exe scripts\\seed_demo_data.py
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PERSON_ID = "demo_linwan_001"
PERSON_NAME = "演示·林小晚"

# 18 维（与 services.affection_engine.AffectionEngine.DIMENSION_NAMES 对齐）
DIM_KEYS = [
    "physical_attraction", "chat_resonance", "humor", "thoughtfulness",
    "trust", "respect", "common_interest", "emotional_value",
    "future_alignment", "family", "spending", "lifestyle",
    "independence", "security", "growth", "romance",
    "emotional_stability", "intimacy",
]

# 每个维度相对总分的固定偏移，保证雷达有起伏且均值贴近总分
_DIM_OFFSETS = [
    8, 6, 4, 3, 5, 2, -2, 6,
    -6, -8, -4, -3, 1, 7, 2, 9, -1, 10,
]

# 14 天总分曲线（升序，最早→今天），含多次 |Δ|>=5
TOTAL_CURVE = [38, 41, 44, 47, 45, 52, 58, 55, 50, 57, 63, 61, 66, 62]

# 任务：(id, 标题, 类型, 难度, 状态, 总步数, 当前步, 截止偏移天, 创建于N小时前)
TASKS = [
    ("demo_t1", "主动开启一次轻松话题", "daily", "normal", "pending", 3, 0, 2, 2),
    ("demo_t2", "安排一次线下咖啡邀约", "stage", "hard", "pending", 4, 0, 5, 4),
    ("demo_t3", "连续三天的早安问候", "daily", "normal", "in_progress", 3, 1, 1, 6),
    ("demo_t4", "准备一份走心小礼物", "challenge", "hard", "in_progress", 5, 2, 6, 9),
    ("demo_t5", "记住她的一个喜好并提起", "daily", "normal", "completed", 2, 2, -3, 30),
    ("demo_t6", "完成首次深度聊天", "stage", "normal", "completed", 4, 4, -6, 60),
    ("demo_t7", "凌晨连发三条追问（反面教材）", "challenge", "hard", "abandoned", 3, 1, -2, 80),
]


def resolve_db_path() -> str:
    cfg_path = os.path.join(ROOT, "config.json")
    rel = "data/loveai.db"
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        rel = (cfg.get("data") or {}).get("db_path") or rel
    except Exception:
        pass
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel.replace("/", os.sep))


def scores_for(total: float) -> dict:
    out = {}
    for k, off in zip(DIM_KEYS, _DIM_OFFSETS):
        out[k] = round(max(0.0, min(100.0, total + off)), 1)
    return out


def main() -> int:
    db_path = resolve_db_path()
    print(f"[seed] 数据库: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1) 幂等：清掉旧演示数据
    for table, col in (
        ("tasks", "person_id"),
        ("affection_snapshots", "person_id"),
        ("current_affection", "person_id"),
        ("affection_events", "person_id"),
        ("affection_stage_log", "person_id"),
        ("chat_messages", "person_id"),
    ):
        cur.execute(f"DELETE FROM {table} WHERE {col}=:p", {"p": PERSON_ID})
    cur.execute("DELETE FROM persons WHERE id=:p", {"p": PERSON_ID})

    # 2) 演示对象
    today = datetime.now().date()
    cur.execute(
        """INSERT INTO persons
           (id, nickname, wechat_nickname, gender, birthday, occupation,
            relationship_stage, soul_path, portrait_path, is_active, created_at, updated_at)
           VALUES (:id,:nick,:nick,'female','1999-06-18','插画师',
                   6,'外表开朗、慢热，重视真诚与情绪价值；讨厌查户口式提问和油腻套路。',
                   NULL,1,datetime('now'),datetime('now'))""",
        {"id": PERSON_ID, "nick": PERSON_NAME},
    )

    # 3) 当前好感度（总分取曲线末点）
    total_now = float(TOTAL_CURVE[-1])
    cur.execute(
        """INSERT INTO current_affection (person_id, scores_json, total_score, updated_at)
           VALUES (:p,:s,:t,datetime('now'))""",
        {"p": PERSON_ID, "s": json.dumps(scores_for(total_now), ensure_ascii=False),
         "t": total_now},
    )

    # 4) 14 天快照（升序）
    for i, total in enumerate(TOTAL_CURVE):
        d = (today - timedelta(days=len(TOTAL_CURVE) - 1 - i)).strftime("%Y-%m-%d")
        cur.execute(
            """INSERT INTO affection_snapshots (person_id, date, scores_json, total_score, created_at)
               VALUES (:p,:d,:s,:t,:d || ' 21:30:00')""",
            {"p": PERSON_ID, "d": d,
             "s": json.dumps(scores_for(float(total)), ensure_ascii=False), "t": float(total)},
        )
        # 阶段跃迁日志（|Δ|>=5），便于时间线与勋章联动
        if i > 0 and abs(TOTAL_CURVE[i] - TOTAL_CURVE[i - 1]) >= 5:
            cur.execute(
                """INSERT INTO affection_stage_log
                   (person_id, stage_from, stage_to, score_at, changed_at)
                   VALUES (:p,:f,:to,:t,:d || ' 21:30:00')""",
                {"p": PERSON_ID, "f": int(TOTAL_CURVE[i - 1] // 10),
                 "to": int(total // 10), "t": float(total), "d": d},
            )

    # 5) 任务（四档）
    for (tid, title, ttype, diff, status, steps, cur_step, deadline_off, hrs_ago) in TASKS:
        deadline = (today + timedelta(days=deadline_off)).strftime("%Y-%m-%d")
        created = (datetime.now() - timedelta(hours=hrs_ago)).strftime("%Y-%m-%d %H:%M:%S")
        completed_at = created
        if status == "completed":
            completed_at = (datetime.now() - timedelta(hours=max(2, hrs_ago - 6))).strftime("%Y-%m-%d %H:%M:%S")
        else:
            completed_at = None
        steps_json = json.dumps([{"n": x + 1} for x in range(steps)], ensure_ascii=False)
        cur.execute(
            """INSERT INTO tasks
               (id, person_id, title, description, task_type, difficulty, estimated_time,
                reward_desc, affection_reward, deadline, completion_condition, auto_keywords,
                guide_direction, steps_json, current_step, status, created_at, completed_at)
               VALUES (:id,:p,:title,'',:ttype,:diff,'','',0,:deadline,'','','',
                       :steps,:cur_step,:status,:created,:completed)""",
            {"id": tid, "p": PERSON_ID, "title": title, "ttype": ttype, "diff": diff,
             "deadline": deadline, "steps": steps_json, "cur_step": cur_step,
             "status": status, "created": created, "completed": completed_at},
        )

    # 6) 置为当前激活对象
    cur.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) "
        "VALUES ('active_person_id', :v, datetime('now'))",
        {"v": PERSON_ID},
    )

    conn.commit()

    # 汇总校验
    n_snap = cur.execute("SELECT COUNT(*) FROM affection_snapshots WHERE person_id=?",
                         (PERSON_ID,)).fetchone()[0]
    n_task = cur.execute("SELECT status, COUNT(*) FROM tasks WHERE person_id=? GROUP BY status",
                         (PERSON_ID,)).fetchall()
    active = cur.execute("SELECT value FROM settings WHERE key='active_person_id'").fetchone()[0]
    conn.close()

    print(f"[seed] 演示对象: {PERSON_NAME} ({PERSON_ID})")
    print(f"[seed] 当前好感度总分: {total_now}；快照 {n_snap} 条")
    print("[seed] 任务分档:", {r[0]: r[1] for r in n_task})
    print(f"[seed] settings.active_person_id = {active}")
    print("[seed] 完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
