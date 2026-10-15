# -*- coding: utf-8 -*-
"""
danjuan_incremental_import.py —— 蛋卷（或任意微信联系人）聊天消息增量导入脚本。

用途
----
把一次新导出/解密好的微信消息库（表 ``danjuan_msgs``，列与
``data/danjuan_import_20261013`` 阶段对齐：local_id INTEGER PK / create_time
INTEGER(unix 秒) / real_sender_id TEXT / source TEXT / local_type INTEGER /
content TEXT / db TEXT）增量合并进 LoveAI 正式库 ``data/loveai.db`` 的
``chat_messages`` 表。

去重键
------
``wechat_msg_id == str(local_id)``。正式库 chat_messages.wechat_msg_id 列存的就是
源库 local_id 的文本形态（全量导入后 11,608 条全部为 TEXT 且一一对应）。已存在即跳过，
不存在才插入。

方向映射
--------
real_sender_id == '551'  -> direction='received'（她发的）
real_sender_id in {'2','15'} -> direction='sent'（我发的；'15' 是撤回系统消息 sender）
其他取值按 'sent' 处理并在日志里计数告警。

时间映射
--------
create_time（unix 秒，本地时区 UTC+8）-> 'YYYY-MM-DD HH:MM:SS' 文本。

类型映射
--------
local_type 原值转文本写入 msg_type（与全量导入保持一致：1/47/3/34/43/50/10000 等）。

典型用法
--------
1) 纯 dry-run（只看计划，不写库）：
   python scripts/danjuan_incremental_import.py \
       --src C:\\path\\to\\new_decrypted.db --dry-run

2) 输入是加密库，先解密再 dry-run：
   python scripts/danjuan_incremental_import.py \
       --src C:\\path\\to\\encrypted.db --key <64-hex-key> --dry-run

3) 真实增量写入（自动备份正式库）：
   python scripts/danjuan_incremental_import.py \
       --src C:\\path\\to\\new_decrypted.db --bak

   真实写入前会把 data/loveai.db 备份为 data/loveai.db.bak_YYYYMMDD_HHMMSS。
   本次任务只跑 dry-run，不执行真实写入。
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sqlite3
import sys
import traceback

# ---------------------------------------------------------------------------
# 路径约定：scripts/ 的上一级即项目根
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_TARGET = os.path.join(PROJECT_ROOT, "data", "loveai.db")
DEFAULT_PERSON_ID = "demo0001"
DEFAULT_LOG = os.path.join(PROJECT_ROOT, "data", "danjuan_incremental_run.log")

# 方向映射
SENDER_RECEIVED = "551"
SENDER_SENT = {"2", "15"}

SOURCE_TABLE = "danjuan_msgs"
SOURCE_COLUMNS = ("local_id", "create_time", "real_sender_id", "local_type", "content")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def ts_to_dt(epoch_int) -> str:
    """unix 秒 -> 'YYYY-MM-DD HH:MM:SS'（本地时区，Windows 上即 UTC+8）。"""
    return datetime.datetime.fromtimestamp(int(epoch_int)).strftime("%Y-%m-%d %H:%M:%S")


def direction_for(real_sender_id) -> str:
    sid = str(real_sender_id)
    if sid == SENDER_RECEIVED:
        return "received"
    # '2' / '15' 以及任何其他未识别 sender 一律按 sent 处理（与全量导入保持一致）
    return "sent"


def backup_target(target_path: str, log) -> str:
    """把正式库备份为 target_path.bak_YYYYMMDD_HHMMSS，返回备份路径。"""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = f"{target_path}.bak_{ts}"
    shutil.copy2(target_path, bak_path)
    log(f"[备份] 正式库已备份: {target_path} -> {bak_path}")
    # 顺手把 -wal / -shm 也清理掉（避免恢复时混淆）；不存在则忽略
    for suffix in ("-wal", "-shm"):
        side = bak_path + suffix
        if os.path.exists(side):
            try:
                os.remove(side)
                log(f"[备份] 清理伴随文件: {side}")
            except OSError as e:
                log(f"[备份] 清理伴随文件失败(忽略): {side}: {e}")
    return bak_path


def maybe_decrypt(src_path: str, key: str | None, log) -> str:
    """若 --key 给出且 src 非 plain，则调用 services.wechat_db_decryptor 解密到
    src_path + '.decrypted.db'，返回明文库路径；否则原样返回 src_path。"""
    if not key:
        return src_path

    # 延迟导入：避免在无 cryptography 环境下 import 失败影响 dry-run
    sys.path.insert(0, PROJECT_ROOT)
    try:
        from services.wechat_db_decryptor import decrypt_db, detect_encryption_type  # type: ignore
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"--key 已提供但无法导入 services.wechat_db_decryptor: {e}"
        ) from e

    enc_type = detect_encryption_type(src_path)
    log(f"[解密] 检测到源库加密类型: {enc_type}")
    if enc_type == "plain":
        log("[解密] 源库已是明文，跳过解密，直接使用。")
        return src_path

    out_path = src_path + ".decrypted.db"
    log(f"[解密] 开始解密: {src_path} -> {out_path} (type={enc_type})")
    decrypt_db(src_path, key, out_path, encryption_type=enc_type)
    log(f"[解密] 完成: {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# 核心：读取源库、对照目标库、返回 (to_insert_rows, existing_count, stats)
# ---------------------------------------------------------------------------
def plan_import(src_db: str, person_id: str, target_db: str, log):
    """返回 (to_insert, existing_count, stats_dict)。

    to_insert: list of (person_id, direction, content, msg_type, wechat_msg_id, created_at)
    existing_count: 目标库已存在的该 person 消息条数（按 wechat_msg_id 集合大小计）
    stats: dict
    """
    # 1) 目标库：读取已存在的 wechat_msg_id 集合
    tcon = sqlite3.connect(f"file:{target_db.replace(chr(92), '/')}?mode=ro", uri=True)
    tcur = tcon.cursor()
    tcur.execute(
        "SELECT wechat_msg_id FROM chat_messages WHERE person_id=?", (person_id,)
    )
    existing_ids = {row[0] for row in tcur.fetchall() if row[0] is not None}
    existing_count = len(existing_ids)
    tcon.close()
    log(f"[读目标] person={person_id} 已存在 wechat_msg_id 数: {existing_count}")

    # 2) 源库：读取全部行
    scon = sqlite3.connect(f"file:{src_db.replace(chr(92), '/')}?mode=ro", uri=True)
    scur = scon.cursor()
    # 先确认表存在
    scur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (SOURCE_TABLE,),
    )
    if scur.fetchone() is None:
        scon.close()
        raise RuntimeError(
            f"源库 {src_db} 中找不到表 {SOURCE_TABLE!r}；"
            f"请确认 --src 指向解密后的校正库（表结构对齐 danjuan_msgs）。"
        )

    cols = ", ".join(SOURCE_COLUMNS)
    scur.execute(f"SELECT {cols} FROM {SOURCE_TABLE} ORDER BY local_id")
    rows = scur.fetchall()
    scon.close()
    log(f"[读源库] {src_db} 共 {len(rows)} 行")

    # 3) 去重 + 映射
    to_insert = []
    skipped = 0
    dir_recv = 0
    dir_sent = 0
    unknown_senders: dict[str, int] = {}
    date_min = None
    date_max = None

    for local_id, create_time, real_sender_id, local_type, content in rows:
        wid = str(local_id)
        if wid in existing_ids:
            skipped += 1
            continue

        direction = direction_for(real_sender_id)
        sid = str(real_sender_id)
        if sid != SENDER_RECEIVED and sid not in SENDER_SENT:
            unknown_senders[sid] = unknown_senders.get(sid, 0) + 1

        if direction == "received":
            dir_recv += 1
        else:
            dir_sent += 1

        created_at = ts_to_dt(create_time)
        if date_min is None or created_at < date_min:
            date_min = created_at
        if date_max is None or created_at > date_max:
            date_max = created_at

        to_insert.append(
            (person_id, direction, content, str(local_type), wid, created_at)
        )

    stats = {
        "src_total": len(rows),
        "skipped_existing": skipped,
        "to_insert": len(to_insert),
        "existing_count": existing_count,
        "dir_recv_new": dir_recv,
        "dir_sent_new": dir_sent,
        "unknown_senders": unknown_senders,
        "date_min": date_min,
        "date_max": date_max,
    }
    return to_insert, existing_count, stats


# ---------------------------------------------------------------------------
# 真实写入（单事务）
# ---------------------------------------------------------------------------
def execute_insert(target_db: str, person_id: str, to_insert, log) -> int:
    wcon = sqlite3.connect(target_db, timeout=30)
    try:
        wcon.execute("BEGIN")
        insert_sql = (
            "INSERT INTO chat_messages "
            "(person_id, direction, content, msg_type, wechat_msg_id, created_at, "
            "emotion, emotion_score) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)"
        )
        wcon.executemany(insert_sql, to_insert)
        inserted = wcon.total_changes  # not exact after commit; use rowcount below
        cur = wcon.execute("SELECT changes()")
        inserted = cur.fetchone()[0]
        wcon.commit()
        log(f"[写入] COMMIT 成功，插入 {inserted} 行")
        return inserted
    except Exception as e:
        wcon.rollback()
        log(f"[写入] !!! ROLLBACK: {e}")
        log(traceback.format_exc())
        raise
    finally:
        wcon.close()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="蛋卷聊天消息增量导入（按 wechat_msg_id=local_id 去重）"
    )
    parser.add_argument("--src", required=True, help="新解密库路径（表 danjuan_msgs）")
    parser.add_argument("--key", default=None, help="可选：加密库 64-hex 密钥")
    parser.add_argument("--person", default=DEFAULT_PERSON_ID, help=f"person_id（默认 {DEFAULT_PERSON_ID}）")
    parser.add_argument("--target", default=DEFAULT_TARGET, help=f"正式库路径（默认 {DEFAULT_TARGET}）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    parser.add_argument("--bak", action="store_true", help="真实写入前备份正式库")
    parser.add_argument("--log", default=DEFAULT_LOG, help=f"日志输出路径（默认 {DEFAULT_LOG}）")
    args = parser.parse_args(argv)

    lines: list[str] = []

    def log(s=""):
        lines.append(str(s))

    log("=" * 72)
    log("蛋卷聊天消息增量导入")
    log("时间: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 72)
    log(f"  src      : {args.src}")
    log(f"  key      : {'<给定>' if args.key else '<未给>'}")
    log(f"  person   : {args.person}")
    log(f"  target   : {args.target}")
    log(f"  dry_run  : {args.dry_run}")
    log(f"  bak      : {args.bak}")
    log(f"  log      : {args.log}")

    try:
        # 0) 前置检查
        if not os.path.isfile(args.src):
            raise RuntimeError(f"--src 文件不存在: {args.src}")
        if not os.path.isfile(args.target):
            raise RuntimeError(f"正式库不存在: {args.target}")

        # 1) 可选解密
        src_db = maybe_decrypt(args.src, args.key, log)

        # 2) 计划
        to_insert, existing_count, stats = plan_import(
            src_db, args.person, args.target, log
        )

        # 3) 打印计划摘要
        log("")
        log("-" * 72)
        log("导入计划摘要")
        log("-" * 72)
        log(f"  源库总行数          : {stats['src_total']}")
        log(f"  目标库已存在条数    : {stats['existing_count']}")
        log(f"  已存在(跳过)        : {stats['skipped_existing']}")
        log(f"  待插入(新增)        : {stats['to_insert']}")
        log(f"  新增方向分布        : received={stats['dir_recv_new']}, sent={stats['dir_sent_new']}")
        log(f"  新增日期范围        : {stats['date_min']} ~ {stats['date_max']}")
        if stats["unknown_senders"]:
            log(f"  !! 未识别 sender 分布(按 sent 处理): {stats['unknown_senders']}")

        # 示例行
        log("")
        log("示例新增行（最多 5 条）:")
        for row in to_insert[:5]:
            # row = (person_id, direction, content, msg_type, wechat_msg_id, created_at)
            pid, direction, content, mtype, wid, created_at = row
            content_preview = (content or "")[:40].replace("\n", " ")
            log(f"  - wid={wid} dir={direction} type={mtype} at={created_at} content={content_preview!r}")

        # 4) 执行
        if args.dry_run:
            log("")
            log("[DRY-RUN] 不写库，结束。")
        else:
            log("")
            if args.bak:
                backup_target(args.target, log)
            else:
                log("[警告] 未指定 --bak，正式库将直接被写入。建议加 --bak。")
            execute_insert(args.target, args.person, to_insert, log)
            log("")
            log("[完成] 真实写入结束。")

        # 5) 落盘日志
        os.makedirs(os.path.dirname(args.log), exist_ok=True)
        with open(args.log, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        # 同时打到 stdout（Windows 控制台中文可能乱码，以日志文件为准）
        try:
            print("\n".join(lines))
        except UnicodeEncodeError:
            # 控制台编码不支持中文时，只打摘要
            print(f"[dry_run={args.dry_run}] to_insert={stats['to_insert']} "
                  f"skipped={stats['skipped_existing']} existing={stats['existing_count']}")
            print(f"日志已写入: {args.log}")
        return 0

    except Exception as e:  # noqa: BLE001
        log("")
        log(f"[错误] {e}")
        log(traceback.format_exc())
        os.makedirs(os.path.dirname(args.log), exist_ok=True)
        with open(args.log, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        try:
            print("\n".join(lines))
        except Exception:  # noqa: BLE001
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
