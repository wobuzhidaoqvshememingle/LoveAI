# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 判断/推荐反馈 CLI（数据飞轮 · 命令行入口）

用户每收到一轮 AI 判断/推荐后，用本脚本点“对/错/存疑”：

记录一条反馈（最低要求交互入口）::

    python scripts/feedback_cli.py record \
        --person demo0001 --type compass --judgment strategy_20261013T210615_511b38f3 \
        --feedback wrong --note "对方其实很主动，罗盘方向反了"

查看某对象最近反馈::

    python scripts/feedback_cli.py list --person demo0001 --limit 10

查看某对象反馈统计（对/错/存疑 计数与命中率）::

    python scripts/feedback_cli.py stats --person demo0001

说明：
- judgment_id 由输出侧（如 StrategyAdvisor.analyze()）生成并随结果返回，
  CLI 只是把用户的点击写回 feedback_records；
- 不联网、不付费，纯本地 SQLite。
"""
from __future__ import annotations

import os
import sys
import json
import argparse

# 允许直接 `python scripts/feedback_cli.py` 运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.feedback_service import (  # noqa: E402
    FeedbackService,
    VALID_FEEDBACKS,
    JUDGMENT_TYPES,
)


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_record(args) -> int:
    svc = FeedbackService(args.db)
    # 支持简写：w=wrong / r=right / d=doubt
    alias = {"r": "right", "w": "wrong", "d": "doubt",
             "对": "right", "错": "wrong", "存疑": "doubt"}
    fb = alias.get(args.feedback, args.feedback)
    try:
        result = svc.record_feedback(
            person_id=args.person,
            judgment_type=args.type,
            judgment_id=args.judgment,
            feedback=fb,
            note=args.note or "",
        )
    except ValueError as e:
        print(f"[校验失败] {e}", file=sys.stderr)
        return 2
    _print(result)
    return 0 if result.get("ok") else 1


def cmd_list(args) -> int:
    svc = FeedbackService(args.db)
    rows = svc.list_feedback(args.person, limit=args.limit)
    _print({"person_id": args.person, "count": len(rows), "records": rows})
    return 0


def cmd_stats(args) -> int:
    svc = FeedbackService(args.db)
    stats = svc.get_stats(args.person)
    stats["by_type"] = svc.get_stats_by_type(args.person)
    _print(stats)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="LoveAI 判断/推荐反馈 CLI（对/错/存疑）")
    p.add_argument("--db", default=None,
                   help="SQLite 库路径，默认 data/loveai.db（冻结态走 runtime_paths）")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("record", help="记录一条反馈")
    pr.add_argument("--person", required=True, help="聊天对象 ID，如 demo0001")
    pr.add_argument("--type", required=True,
                    help=f"判断/推荐类型，如 {JUDGMENT_TYPES}")
    pr.add_argument("--judgment", required=True,
                    help="输出侧生成的 judgment_id（随本轮 AI 结果返回）")
    pr.add_argument("--feedback", required=True,
                    help=f"反馈：{VALID_FEEDBACKS}（支持简写 r/w/d 或 对/错/存疑）")
    pr.add_argument("--note", default="", help="可选备注：为什么觉得错/存疑")
    pr.set_defaults(func=cmd_record)

    pl = sub.add_parser("list", help="查看某对象最近反馈")
    pl.add_argument("--person", required=True)
    pl.add_argument("--limit", type=int, default=20)
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("stats", help="查看某对象反馈统计")
    ps.add_argument("--person", required=True)
    ps.set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
