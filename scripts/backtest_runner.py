# -*- coding: utf-8 -*-
"""
backtest_runner.py — 参数化、可复用的 LoveAI 回测运行器（v1）。

把 06-12 单事件模板 与 _bt_run.py 批量管线 抽成"一条命令跑一个事件"的通用脚本：

  事件定位   ：按关键词 / 日期区间在【正式库只读】chat_messages 里捞事件消息，
              打印事发时刻，辅助确定 cutoff。
  时间截断   ：正式库只读复制成临时库 -> 删除 cutoff 之后消息 -> 清空好感/快照表
              （事发前只能用"默认分 + 时长 bonus"，口径与既有回测一致并如实标注）。
  事发前画像 ：PersonaAnalyzer（本地规则，不调外部 LLM）在截断库上重跑。
  方向罗盘   ：DirectionCompass + TaskEngine.generate_recommended_tasks 给出算法预测方向/策略。
  现实对拍   ：从【正式库只读】回读 cutoff 之后窗口内的真实消息，做现实走向摘要，
              并给出"命中/偏离"判定建议（人工最终确认）。
  落盘       ：事发前画像 json + 运行日志 txt + 结构化摘要 json（含关键数字/方向分布/现实回读/判定建议）。

安全红线（与既有回测一致）：
  * 正式库 data/loveai.db 全程只读（副本截断写法；现实回读用 uri mode=ro）。
  * 不修改任何既有回测脚本/产物；本脚本只在 --out-dir 写自己的新文件。
  * 不调用外部 LLM；所有数字均为本地规则实测，可复核。

用法（PowerShell，中文输出一律 *> 落盘再回读）：
  .venv\\Scripts\\python.exe scripts\\backtest_runner.py \\
      --cutoff "2026-08-25 18:32:00" --label "换头像分手疑问" --mmdd 0825

  # 先定位事件（不截断、不写库）：
  .venv\\Scripts\\python.exe scripts\\backtest_runner.py --locate --keyword "分手了" \\
      --date-lo "2026-08-25 00:00:00" --date-hi "2026-08-26 00:00:00"
"""
import os
import sys
import io
import json
import shutil
import sqlite3
import argparse
import traceback
from datetime import datetime, timedelta

# Windows stdout 中文兜底（落盘为主，这里只防编码异常）
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# ---------------------------------------------------------------------------
# 默认路径（与既有回测一致；可用命令行覆盖）
# ---------------------------------------------------------------------------
DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.environ.get("LOVEAI_ROOT", DEFAULT_ROOT)
SRC_DB = os.path.join(ROOT, "data", "loveai.db")
DEFAULT_OUT_DIR = os.path.join(DEFAULT_ROOT, "benchmark_out")
DEFAULT_PID = "demo0001"
DEFAULT_NICKNAME = "示例对象"

sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# 轻量 DB 包装（与 _bt_run.py 完全一致的写法）
# ---------------------------------------------------------------------------
class LoveAIDB:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def fetch_all(self, sql, params=None):
        rows = self.conn.execute(sql, params or {}).fetchall()
        return [dict(r) for r in rows]

    def fetch_one(self, sql, params=None):
        r = self.conn.execute(sql, params or {}).fetchone()
        return dict(r) if r else None

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params or ())


def ro_conn():
    """正式库只读连接（uri mode=ro，绝不写正式库）。"""
    c = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# 事件定位：按关键词 / 日期区间在正式库只读里捞事件消息
# ---------------------------------------------------------------------------
def locate_event(keyword, date_lo, date_hi, pid=DEFAULT_PID, limit=30):
    print(f"== 事件定位（正式库只读） keyword={keyword!r} {date_lo} ~ {date_hi} ==")
    c = ro_conn()
    sql = ("SELECT created_at, direction, content FROM chat_messages "
           "WHERE person_id=? AND created_at>=? AND created_at<=? ")
    params = [pid, date_lo, date_hi]
    if keyword:
        sql += "AND content LIKE ? "
        params.append(f"%{keyword}%")
    sql += "ORDER BY created_at ASC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in c.execute(sql, params).fetchall()]
    c.close()
    if not rows:
        print("  (无匹配消息)")
        return rows
    for r in rows:
        who = "她" if r.get("direction") == "received" else "我"
        txt = (r.get("content") or "").replace("\n", " ")
        print(f"  [{r.get('created_at')}] [{who}] {txt}")
    print(f"  共 {len(rows)} 条命中（上限 {limit}）")
    return rows


# ---------------------------------------------------------------------------
# 现实对拍：从正式库只读回读 cutoff 之后窗口内的真实消息
# ---------------------------------------------------------------------------
def read_reality(cutoff, hours=48, pid=DEFAULT_PID, head=8, tail=6,
                 conflict_kw=("滚", "离婚", "分手", "对不起", "抱歉", "我错了", "爱你", "么么哒")):
    end = (datetime.strptime(cutoff, "%Y-%m-%d %H:%M:%S") + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    c = ro_conn()
    rows = [dict(r) for r in c.execute(
        "SELECT created_at, direction, content FROM chat_messages "
        "WHERE person_id=? AND created_at>=? AND created_at<? ORDER BY created_at ASC",
        (pid, cutoff, end)).fetchall()]
    c.close()

    n_she = sum(1 for r in rows if r.get("direction") == "received")
    n_me = sum(1 for r in rows if r.get("direction") == "sent")

    def fmt(r):
        who = "她" if r.get("direction") == "received" else "我"
        return f"[{r.get('created_at')}] [{who}] {(r.get('content') or '').strip()[:80]}"

    print(f"\n== 现实走向回读（正式库只读）窗口 {cutoff} ~ {end}（{hours}h） ==")
    print(f"  事发后消息共 {len(rows)} 条 (她={n_she} / 我={n_me})")
    print("  -- 前几条 --")
    for r in rows[:head]:
        print("   " + fmt(r))
    hits = [r for r in rows if any(k in (r.get("content") or "") for k in conflict_kw)]
    print(f"  -- 关键情绪词命中 {len(hits)} 条 --")
    for r in hits[:12]:
        print("   " + fmt(r))
    print("  -- 收尾几条 --")
    for r in rows[-tail:]:
        print("   " + fmt(r))

    return {
        "window": [cutoff, end], "hours": hours,
        "total": len(rows), "she": n_she, "me": n_me,
        "head": [fmt(r) for r in rows[:head]],
        "conflict_hits": [fmt(r) for r in hits[:12]],
        "tail": [fmt(r) for r in rows[-tail:]],
    }


# ---------------------------------------------------------------------------
# 主流程：单事件回测
# ---------------------------------------------------------------------------
def run_event(cutoff, mmdd, label, pid=DEFAULT_PID, out_dir=DEFAULT_OUT_DIR,
              nickname=DEFAULT_NICKNAME, reality_hours=48, top_n=5):
    os.makedirs(out_dir, exist_ok=True)
    TMP_DB = os.path.join(out_dir, f"_run_{mmdd}.db")
    PERSONA_JSON = os.path.join(out_dir, f"_run_{mmdd}_persona.json")
    SUMMARY_JSON = os.path.join(out_dir, f"_run_{mmdd}_summary.json")

    print("\n" + "#" * 70)
    print(f"### 事件 {mmdd}: {label}")
    print(f"### cutoff(截断点) = {cutoff}  | 临时库 = {TMP_DB}")
    print("#" * 70)
    print(f"[正式库只读] {SRC_DB}  存在={os.path.exists(SRC_DB)}")

    # 1) 临时库 + 时间截断（先复制再写副本，正式库只读）
    if os.path.exists(TMP_DB):
        os.remove(TMP_DB)
    shutil.copy(SRC_DB, TMP_DB)
    conn = sqlite3.connect(TMP_DB)
    conn.execute("DELETE FROM chat_messages WHERE person_id=? AND created_at >= ?", (pid, cutoff))
    # 好感/快照表清空 -> 事发前只能用默认分 + 时长 bonus（如实标注）
    try:
        conn.execute("DELETE FROM current_affection WHERE person_id=?", (pid,))
    except Exception as e:
        print("(current_affection 跳过:", e, ")")
    try:
        conn.execute("DELETE FROM affection_snapshots WHERE person_id=?", (pid,))
    except Exception as e:
        print("(affection_snapshots 跳过:", e, ")")
    conn.commit()
    n_before = conn.execute("SELECT COUNT(*) c FROM chat_messages WHERE person_id=?", (pid,)).fetchone()[0]
    n_received = conn.execute("SELECT COUNT(*) c FROM chat_messages WHERE person_id=? AND direction='received'", (pid,)).fetchone()[0]
    n_sent = conn.execute("SELECT COUNT(*) c FROM chat_messages WHERE person_id=? AND direction='sent'", (pid,)).fetchone()[0]
    conn.close()
    print(f"[回测库] 事发前消息 {n_before} 条 (她={n_received} / 我={n_sent})  截断点 {cutoff}")

    # 2) 事发前画像
    from services.persona_analyzer import PersonaAnalyzer
    db = LoveAIDB(TMP_DB)
    pa = PersonaAnalyzer(db)
    print("\n== 事发前画像 (PersonaAnalyzer, 本地规则) ==")
    res = pa.analyze(pid, {"nickname": nickname})
    if "error" in res:
        print("画像失败:", res)
        return {"error": res}
    with open(PERSONA_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)

    att = res.get("attachment") or {}
    tl = res.get("three_layer") or {}
    fj = tl.get("final_judgment") or {}
    print("primary_type:", att.get("primary_type"),
          "| avoidance:", att.get("avoidance_score"),
          "| anxiety:", att.get("anxiety_score"),
          "| security:", att.get("security_score"))
    print("three_layer combined:", fj.get("combined_score"),
          "| calibrated:", fj.get("calibrated_score"))
    print("risk_warning:", (res.get("risk_warning") or {}).get("overall_level"),
          "| stage:", (res.get("relationship_stage") or {}).get("stage"))
    print("satisfaction:", (res.get("relationship_satisfaction") or {}).get("score"),
          "| commitment:", (res.get("commitment") or {}).get("score"),
          "| trust:", (res.get("trust") or {}).get("score"),
          "| bank:", (res.get("emotional_bank") or {}).get("balance"))
    print("conflict_style:", (res.get("conflict_style") or {}).get("primary"),
          "| repair_rate:", (res.get("conflict_repair") or {}).get("repair_rate"))
    print("画像已存:", PERSONA_JSON)

    # 3) 事发前好感（默认分 50 + 时长 bonus，无快照）
    from services.affection_engine import AffectionEngine
    aff = AffectionEngine(db)
    scores = aff.get_current(pid)
    total = aff.get_total_with_bonus(pid, scores)
    stage = int(aff.get_stage(total))
    print(f"\n== 事发前好感 (口径: 默认维度分50 + 关系时长bonus, 无快照) ==\ntotal={total} | stage={stage}")

    # 4) 画像注入 task_engine 磁盘加载
    import services.task_engine as te_mod
    te_mod.load_persona_from_disk = lambda pid: res

    # 5) 推荐任务（事发前）-> 取方向/策略
    print("\n== generate_recommended_tasks (事发前) 取方向/策略 ==")
    from services.strategy_advisor import StrategyAdvisor
    sa = StrategyAdvisor(db=None)
    te = te_mod.TaskEngine(db, aff, strategy_advisor=sa)
    recs = []
    dir_counter = {}
    try:
        recs = te.generate_recommended_tasks(pid, stage=stage, top_n=top_n)
        print("返回条数:", len(recs))
        for i, r in enumerate(recs, 1):
            d = r.get("matched_direction")
            s = r.get("matched_strategy")
            dir_counter[(d, s)] = dir_counter.get((d, s), 0) + 1
            print(f"\n[{i}] title: {r.get('title')}")
            print("    direction:", d, "| strategy:", s, "| direction_sem:", r.get("direction_sem"),
                  "| score:", r.get("score"))
            print("    advance_trigger:", r.get("advance_trigger"))
        print("\n-- 方向/策略 分布(top%d) --" % top_n)
        for (d, s), c in sorted(dir_counter.items(), key=lambda x: -x[1]):
            print(f"   {d} / {s}  x{c}")
    except Exception as e:
        print("推荐异常:", e)
        traceback.print_exc()

    # 6) 策略顾问独立输出前几条
    advisor_rec = []
    print("\n== StrategyAdvisor.recommended (事发前, 前6条) ==")
    try:
        sres = sa.analyze(res, relation_stage=stage)
        rec = sres.get("recommended") if isinstance(sres, dict) else None
        if rec:
            for x in rec[:6]:
                print("   -", x)
                advisor_rec.append(x)
    except Exception as e:
        print("策略异常:", e)

    # 7) 现实对拍（正式库只读回读）
    reality = read_reality(cutoff, hours=reality_hours, pid=pid)

    # 8) 判定建议（启发式，人工最终确认）
    dominant = sorted(dir_counter.items(), key=lambda x: -x[1])
    dominant_dir = dominant[0][0][0] if dominant else None
    push_kw = ("滚", "离婚", "分手", "傻逼", "烦", "没必要")
    soften_kw = ("爱你", "么么哒", "抱歉", "对不起", "我错了", "好啦", "好了")
    rtext = " ".join(reality.get("conflict_hits", []) + reality.get("tail", []))
    pushed = any(k in rtext for k in push_kw)
    softened = any(k in rtext for k in soften_kw)
    if dominant_dir and ("后撤" in str(dominant_dir) or "慢热" in str(dominant_dir) or "留灯" in str(dominant_dir)):
        if softened and not pushed:
            verdict = "倾向命中（算法建议给空间/不追问，现实未升级冲突、后续自然回暖）—— 待人工确认"
        elif pushed:
            verdict = "待判定（算法建议后撤，但现实出现升级信号，需人工读完整窗口判定是用户施压所致还是算法失准）"
        else:
            verdict = "待人工判定（算法方向=" + str(dominant_dir) + "，现实证据不足以自动下结论）"
    else:
        verdict = "待人工判定（算法主方向=" + str(dominant_dir) + "，需人工读现实窗口做命中/偏离判断）"

    print("\n== 判定建议（启发式，人工最终确认）==")
    print("   算法主方向/策略:", dominant[0][0] if dominant else None, f"x{dominant[0][1] if dominant else 0}")
    print("   现实情绪信号: 升级词命中=", pushed, " 软化词命中=", softened)
    print("   建议:", verdict)

    # 9) 结构化摘要落盘
    summary = {
        "event": {"mmdd": mmdd, "label": label, "cutoff": cutoff, "person_id": pid},
        "before_counts": {"total": n_before, "she": n_received, "me": n_sent},
        "persona": {
            "primary_type": att.get("primary_type"),
            "avoidance": att.get("avoidance_score"), "anxiety": att.get("anxiety_score"),
            "security": att.get("security_score"),
            "combined": fj.get("combined_score"), "calibrated": fj.get("calibrated_score"),
            "risk_level": (res.get("risk_warning") or {}).get("overall_level"),
            "stage": (res.get("relationship_stage") or {}).get("stage"),
            "satisfaction": (res.get("relationship_satisfaction") or {}).get("score"),
            "commitment": (res.get("commitment") or {}).get("score"),
            "trust": (res.get("trust") or {}).get("score"),
            "emotional_bank": (res.get("emotional_bank") or {}).get("balance"),
            "conflict_style": (res.get("conflict_style") or {}).get("primary"),
            "repair_rate": (res.get("conflict_repair") or {}).get("repair_rate"),
        },
        "affection": {"total": total, "stage": stage,
                      "caliber": "默认维度分50+关系时长bonus, 无快照(非实测快照)"},
        "algo_direction": {f"{d}/{s}": c for (d, s), c in dir_counter.items()},
        "algo_dominant": list(dominant[0][0]) if dominant else None,
        "advisor_recommended": advisor_rec,
        "reality": reality,
        "verdict_suggestion": verdict,
        "files": {"tmp_db": TMP_DB, "persona_json": PERSONA_JSON},
    }
    with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print("\n摘要已存:", SUMMARY_JSON)
    # 先关闭临时库句柄（Windows 下未关闭则无法删除副本）
    try:
        db.conn.close()
    except Exception:
        pass
    # 用完即删临时库（正式库副本，非必须保留）
    try:
        os.remove(TMP_DB)
        print("临时库已清理:", TMP_DB)
    except Exception as e:
        print("(临时库清理跳过:", e, ")")
    return summary


def main():
    ap = argparse.ArgumentParser(description="LoveAI 参数化回测运行器")
    ap.add_argument("--cutoff", help='截断点/事发时刻前, 如 "2026-08-25 18:32:00"')
    ap.add_argument("--mmdd", help="输出标签后缀, 如 0825（默认从 cutoff 取 MMDD）")
    ap.add_argument("--label", default="(未命名事件)", help="事件人类可读标题")
    ap.add_argument("--person-id", default=DEFAULT_PID, help=f"对方 person_id, 默认 {DEFAULT_PID}")
    ap.add_argument("--nickname", default=DEFAULT_NICKNAME, help="昵称(传给 PersonaAnalyzer)")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="输出目录")
    ap.add_argument("--reality-hours", type=float, default=48, help="事发后现实回读窗口(小时)")
    ap.add_argument("--top-n", type=int, default=5, help="推荐任务条数")
    # 定位模式
    ap.add_argument("--locate", action="store_true", help="只定位事件(只读, 不截断不写库)")
    ap.add_argument("--keyword", help="定位用关键词")
    ap.add_argument("--date-lo", help="定位区间起")
    ap.add_argument("--date-hi", help="定位区间止")
    args = ap.parse_args()

    if args.locate:
        locate_event(args.keyword, args.date_lo, args.date_hi, pid=args.person_id)
        return

    if not args.cutoff:
        ap.error("非定位模式必须提供 --cutoff，如 --cutoff '2026-08-25 18:32:00'")

    mmdd = args.mmdd
    if not mmdd:
        mmdd = args.cutoff[5:7] + args.cutoff[8:10]

    run_event(cutoff=args.cutoff, mmdd=mmdd, label=args.label, pid=args.person_id,
              out_dir=args.out_dir, nickname=args.nickname,
              reality_hours=args.reality_hours, top_n=args.top_n)


if __name__ == "__main__":
    main()
