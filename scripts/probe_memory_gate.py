# -*- coding: utf-8 -*-
"""
探针：记忆闸门真接通（问题6）

不走 mock 拦截，从生产入口 MemoryService.guarded_write_calibration() 真走：
  - 短词「嗯」      → 被拒（不写库）
  - 脏话句         → noisy=True，路由 summary 层（不进 vector）
  - 正常句         → 路由 vector 层
  - logs/memory_gate_rejected.log 有被拒记录

跑法：.venv\\Scripts\\python.exe scripts\\probe_memory_gate.py
"""
import os
import sys
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.memory_service import MemoryService  # noqa: E402

FAILS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}{(' | ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


class SqliteDB:
    """最小 sqlite3 包装，对齐项目 db.execute/fetch_all/fetch_one 接口。"""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or {})
        self.conn.commit()
        return cur

    def fetch_all(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or {})
        return [dict(r) for r in cur.fetchall()]

    def fetch_one(self, sql, params=None):
        cur = self.conn.cursor()
        cur.execute(sql, params or {})
        r = cur.fetchone()
        return dict(r) if r else None


def main() -> int:
    print("=" * 68)
    print("探针：记忆闸门生产路由（问题6）——guarded_write_calibration 真走")
    print("=" * 68)

    db = SqliteDB()
    svc = MemoryService(db=db)

    # 每条校准数据
    raw_items = [
        {"text": "嗯", "speaker": "other", "source": "chat",
         "confidence": 0.9, "timestamp": "2026-10-10 10:00:00"},          # 短词 → 拒
        {"text": "你他妈就是个废物，滚", "speaker": "other", "source": "chat",
         "confidence": 0.9, "timestamp": "2026-10-10 10:01:00"},          # 脏话 → summary
        {"text": "她每周三晚上固定去瑜伽课，不喜欢被临时打扰",
         "speaker": "other", "source": "chat",
         "confidence": 0.8, "timestamp": "2026-10-10 10:02:00"},          # 正常 → vector
    ]

    print("\n[1] guarded_write_calibration 真调用 gate.process_batch()：")
    result = svc.guarded_write_calibration("probe_person", raw_items)
    print("   accepted =", result["accepted"], " rejected =", result["rejected"])
    print("   written_ids =", result["written_ids"])

    accepted = result["accepted_items"]
    rejected = result["rejected_items"]

    print("\n[2] 短词「嗯」被拒：")
    short_rejected = any("嗯" in (r["raw"].get("text") or "") for r in rejected)
    check("短词/语气词被闸门拒绝", short_rejected,
          f"reasons={[r['reason'] for r in rejected]}")

    print("\n[3] 脏话句路由 summary（noisy=True，不进 vector）：")
    prof = [a for a in accepted if "废物" in (a["raw"].get("text") or "")]
    check("脏话句被接受且 noisy=True", len(prof) == 1 and prof[0]["noisy"] is True,
          f"layer={prof[0]['target_layer'] if prof else None}")
    check("脏话句 target_layer == summary",
          bool(prof) and prof[0]["target_layer"] == "summary")

    print("\n[4] 正常句路由 vector：")
    norm = [a for a in accepted if "瑜伽" in (a["raw"].get("text") or "")]
    check("正常句 target_layer == vector",
          bool(norm) and norm[0]["target_layer"] == "vector",
          f"layer={norm[0]['target_layer'] if norm else None}")

    print("\n[5] 真写库了（written_ids 数量 = accepted 数量）：")
    check("written_ids 数 == accepted 数",
          len(result["written_ids"]) == result["accepted"],
          f"{len(result['written_ids'])} vs {result['accepted']}")

    print("\n[6] logs/memory_gate_rejected.log 有内容：")
    log_path = os.path.join(_ROOT, "logs", "memory_gate_rejected.log")
    has_log = os.path.exists(log_path) and os.path.getsize(log_path) > 0
    check("被拒日志文件存在且非空", has_log, f"path={log_path}")
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            tail = [ln for ln in f.read().splitlines() if ln.strip()][-2:]
        for ln in tail:
            print("   log>", ln[:90])

    print("\n" + "=" * 68)
    if FAILS:
        print(f"结果：{len(FAILS)} 项失败 -> {FAILS}")
        return 1
    print("结果：全部 PASS —— 记忆闸门在生产入口真的过滤 + 分层写库了。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
