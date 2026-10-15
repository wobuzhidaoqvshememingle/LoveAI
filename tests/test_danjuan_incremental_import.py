# -*- coding: utf-8 -*-
"""
tests/test_danjuan_incremental_import.py —— 增量导入脚本的单测。

覆盖：
  1. 去重：源库里已存在于目标库的 wechat_msg_id 被跳过；
  2. 方向映射：real_sender_id '551'->received, '2'/'15'->sent，未知 sender 按 sent；
  3. 时间/类型映射：create_time unix 秒 -> 'YYYY-MM-DD HH:MM:SS'，local_type 原值文本化；
  4. dry-run 不写库：目标库行数保持不变；
  5. 真实写入（无 --bak）：新行被插入，且 wechat_msg_id 文本形态与源 local_id 一致。

所有库都用 pytest tmp_path 临时构造，不触碰正式库 data/loveai.db。
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys

import pytest


# ---------------------------------------------------------------------------
# 动态加载 scripts/danjuan_incremental_import.py
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "danjuan_incremental_import.py")

spec = importlib.util.spec_from_file_location("danjuan_incremental_import", SCRIPT_PATH)
dji = importlib.util.module_from_spec(spec)
# 避免脚本在 import 时副作用；模块顶层没有副作用，安全
spec.loader.exec_module(dji)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------
PERSON = "test_person_demo0001"


def _make_target(tmp_path, existing_rows):
    """构造正式库。existing_rows: list of (wechat_msg_id, direction, content, msg_type, created_at)。"""
    db = tmp_path / "loveai_test.db"
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute(
        """CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id TEXT,
            direction TEXT,
            content TEXT,
            msg_type TEXT DEFAULT 'text',
            emotion TEXT,
            emotion_score REAL,
            wechat_msg_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )"""
    )
    for wid, direction, content, mtype, created_at in existing_rows:
        cur.execute(
            "INSERT INTO chat_messages (person_id, direction, content, msg_type, wechat_msg_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (PERSON, direction, content, mtype, wid, created_at),
        )
    con.commit()
    con.close()
    return str(db)


def _make_source(tmp_path, rows):
    """构造源库。rows: list of (local_id, create_time, real_sender_id, local_type, content)。"""
    db = tmp_path / "src.db"
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute(
        """CREATE TABLE danjuan_msgs (
            local_id INTEGER PRIMARY KEY, create_time INTEGER, real_sender_id TEXT,
            source TEXT, local_type INTEGER, content TEXT, db TEXT)"""
    )
    for local_id, ct, sender, ltype, content in rows:
        cur.execute(
            "INSERT INTO danjuan_msgs (local_id, create_time, real_sender_id, source, local_type, content, db) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (local_id, ct, sender, "test", ltype, content, "test"),
        )
    con.commit()
    con.close()
    return str(db)


# 2026-01-09 16:29:12 CST 的 unix 秒（与全量导入第一条对齐，方便断言）
T1 = 1767947352
T2 = 1767947706
T3 = 1768000000
T4 = 1768100000


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
def test_ts_to_dt_format():
    s = dji.ts_to_dt(T1)
    assert s == "2026-01-09 16:29:12"


@pytest.mark.parametrize(
    "sender, expected",
    [("551", "received"), ("2", "sent"), ("15", "sent"), ("999", "sent")],
)
def test_direction_mapping(sender, expected):
    assert dji.direction_for(sender) == expected


def test_plan_import_dedup_and_mapping(tmp_path):
    target = _make_target(
        tmp_path,
        # 已有 local_id=1
        [("1", "sent", "旧消息1", "1", "2026-01-09 16:29:12")],
    )
    src = _make_source(
        tmp_path,
        [
            (1, T1, "2", 1, "已存在的消息(应跳过)"),     # 已存在
            (2, T2, "551", 1, "她发的新消息"),           # 新 received
            (3, T3, "2", 3, "我发的新图片"),             # 新 sent
            (4, T4, "15", 10000, "撤回系统消息"),         # 新 sent (sender=15)
        ],
    )

    log = []
    to_insert, existing_count, stats = dji.plan_import(src, PERSON, target, log.append)

    assert existing_count == 1
    assert stats["src_total"] == 4
    assert stats["skipped_existing"] == 1
    assert stats["to_insert"] == 3
    assert stats["dir_recv_new"] == 1
    assert stats["dir_sent_new"] == 2

    # to_insert 行的形态：(person_id, direction, content, msg_type, wechat_msg_id, created_at)
    by_wid = {row[4]: row for row in to_insert}
    assert set(by_wid.keys()) == {"2", "3", "4"}

    row2 = by_wid["2"]
    assert row2[0] == PERSON
    assert row2[1] == "received"
    assert row2[2] == "她发的新消息"
    assert row2[3] == "1"          # msg_type = str(local_type)
    assert row2[5] == dji.ts_to_dt(T2)

    row3 = by_wid["3"]
    assert row3[1] == "sent"
    assert row3[3] == "3"          # local_type=3 文本化

    row4 = by_wid["4"]
    assert row4[1] == "sent"        # sender=15 -> sent
    assert row4[3] == "10000"


def test_dry_run_does_not_write(tmp_path):
    target = _make_target(tmp_path, [("1", "sent", "旧消息1", "1", "2026-01-09 16:29:12")])
    src = _make_source(
        tmp_path,
        [(2, T2, "551", 1, "新消息")],
    )
    log_path = str(tmp_path / "dryrun.log")

    before = sqlite3.connect(target).execute(
        "SELECT COUNT(*) FROM chat_messages WHERE person_id=?", (PERSON,)
    ).fetchone()[0]

    rc = dji.main([
        "--src", src,
        "--person", PERSON,
        "--target", target,
        "--dry-run",
        "--log", log_path,
    ])
    assert rc == 0

    after = sqlite3.connect(target).execute(
        "SELECT COUNT(*) FROM chat_messages WHERE person_id=?", (PERSON,)
    ).fetchone()[0]
    assert before == after == 1, "dry-run 不应写入目标库"


def test_real_insert_writes_new_rows(tmp_path):
    target = _make_target(tmp_path, [("1", "sent", "旧消息1", "1", "2026-01-09 16:29:12")])
    src = _make_source(
        tmp_path,
        [
            (1, T1, "2", 1, "已存在"),
            (2, T2, "551", 1, "她发的新消息"),
            (3, T3, "2", 47, "我发的表情"),
        ],
    )
    log_path = str(tmp_path / "real.log")

    rc = dji.main([
        "--src", src,
        "--person", PERSON,
        "--target", target,
        "--log", log_path,
        # 故意不加 --bak：测试场景临时库，无需备份
    ])
    assert rc == 0

    con = sqlite3.connect(target)
    total = con.execute(
        "SELECT COUNT(*) FROM chat_messages WHERE person_id=?", (PERSON,)
    ).fetchone()[0]
    assert total == 3, "1 旧 + 2 新 = 3"

    # 校验新行内容
    rows = con.execute(
        "SELECT wechat_msg_id, direction, msg_type, content, created_at FROM chat_messages "
        "WHERE person_id=? ORDER BY wechat_msg_id",
        (PERSON,),
    ).fetchall()
    con.close()

    assert rows[0] == ("1", "sent", "1", "旧消息1", "2026-01-09 16:29:12")
    assert rows[1][0] == "2"
    assert rows[1][1] == "received"
    assert rows[1][2] == "1"
    assert rows[1][3] == "她发的新消息"
    assert rows[1][4] == dji.ts_to_dt(T2)
    assert rows[2][0] == "3"
    assert rows[2][1] == "sent"
    assert rows[2][2] == "47"


def test_missing_source_table_raises(tmp_path):
    target = _make_target(tmp_path, [])
    # 源库没有 danjuan_msgs 表
    src = tmp_path / "bad.db"
    sqlite3.connect(src).execute("CREATE TABLE other (x INTEGER)").connection.commit()
    log_path = str(tmp_path / "err.log")

    rc = dji.main([
        "--src", str(src),
        "--person", PERSON,
        "--target", target,
        "--dry-run",
        "--log", log_path,
    ])
    assert rc == 1
    # 日志里应包含"找不到表"
    with open(log_path, encoding="utf-8") as f:
        content = f.read()
    assert "danjuan_msgs" in content
