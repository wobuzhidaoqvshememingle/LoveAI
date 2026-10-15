# -*- coding: utf-8 -*-
"""benchmark 主入口：配对跑 A（贪心）与 B（助推-滑翔），真实运行产出指标。

运行（项目根目录）：
    & $py benchmark\\run_benchmark.py --small
    & $py benchmark\\run_benchmark.py --scenarios 5 --seed 42

输出（benchmark\\results\\）：
    benchmark_last.json / benchmark_last.md / benchmark_last.csv
    （另存一份带时间戳的归档）

指标口径（全部真实运行产出）：
  * 全任务达成率 = 完成任务数 / (小+中+主) 任务总数。
  * 整集100%达成 = 该场景全部任务完成（布尔），跨场景给比例。
  * 平均折扣 discount_steps = 迂回动作额外耗时步之和（零折扣=0）。
    口径：每发生一次 PlannedAction.detour（变轨迂回），环境记 +2 步额外时间；
    不迂回则 +0。该值越大=绕路越多；贪心 detour 恒 None，故 A 组此值恒 0。
  * 主线损伤度 = 累积 |负 mainline_impact|（硬推失败真伤主线时累加）；
    mainline_damage_events = 选中"伤主线动作"且失败的次数。
  * 结局好感 = final_affection（0..100）与 final_stage（0..9）。
  * 耗时 = 单次决策 wall ms 均值/合计 + 整场景 wall ms。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# 允许直接 `python benchmark\run_benchmark.py` 运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from benchmark.sim_environment import SimEnvironment, RunResult  # noqa: E402
from benchmark.scenario_generator import (  # noqa: E402
    build_small_scenario,
    build_scenario_set,
    inject_scenario_chats,
    DIFFICULTY_PARAMS,
)
from benchmark.policies import GreedyPolicy, load_b_policy  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------------ #
# 单次配对：同场景同 seed，A/B 各跑一遍
# ------------------------------------------------------------------ #
def run_pair(scenario, seed: int) -> dict:
    """对一个场景跑 A 与（若就绪）B，返回 {"A": RunResult, "B": RunResult|None, "chat": {...}}。"""
    # --- 真注入聊天记录到临时 SQLite（证明注入是真注入）---
    tmpdir = tempfile.mkdtemp(prefix="loveai_bench_chat_")
    chat_meta = {}
    try:
        from models.database import Database

        db_path = os.path.join(tmpdir, "bench_chat.db")
        db = Database(db_path)
        db.init_tables()
        chat_meta = inject_scenario_chats(db, "bench_p1", "测试对象", scenario)
        try:
            db.engine.dispose()
        except Exception:
            pass
    except Exception as e:  # noqa: BLE001
        chat_meta = {"injected": 0, "error": str(e)}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # --- A 组贪心 ---
    env_a = SimEnvironment(scenario, seed=seed)
    res_a: RunResult = env_a.run(GreedyPolicy())
    res_a.chat_injected = chat_meta.get("injected", 0)
    res_a.chat_proactive_ratio = chat_meta.get("proactive_ratio")
    res_a.chat_recalc_total = chat_meta.get("recalc_total")

    # --- B 组长期规划（模块未就绪则 None，不伪造数字）---
    b_policy = load_b_policy()
    res_b: RunResult | None = None
    if b_policy is not None:
        env_b = SimEnvironment(scenario, seed=seed)
        res_b = env_b.run(b_policy)
        res_b.chat_injected = chat_meta.get("injected", 0)
        res_b.chat_proactive_ratio = chat_meta.get("proactive_ratio")
        res_b.chat_recalc_total = chat_meta.get("recalc_total")

    return {"A": res_a, "B": res_b, "chat": chat_meta, "category": getattr(scenario, "category", "uncategorized")}


# ------------------------------------------------------------------ #
# 聚合
# ------------------------------------------------------------------ #
def _mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else 0.0


def _std(xs: list[float]) -> float:
    return statistics.stdev(xs) if len(xs) > 1 else 0.0


def _binom_two_sided_p(k: int, n: int) -> float:
    """符号检验双侧 p 值（H0: P(B>A)=0.5）。精确二项分布。"""
    if n == 0:
        return 1.0
    # 双侧：取偏离 0.5 更极端一侧的两倍概率
    k = min(k, n - k)
    p_extreme = 0.0
    for i in range(k + 1):
        p_extreme += math.comb(n, i) * (0.5 ** n)
    return min(1.0, 2.0 * p_extreme)


def sign_test(pairs: list[dict]) -> dict:
    """配对符号检验：对 completion_rate / final_affection / mainline_damage 计数 B 优于 A。

    对 mainline_damage，"B 优于 A" = B < A（损伤越小越好）。
    对 completion_rate / final_affection，"B 优于 A" = B > A。
    """
    out = {}
    metrics = [
        ("completion_rate", "higher_better"),
        ("final_affection", "higher_better"),
        ("mainline_damage", "lower_better"),
    ]
    for key, direction in metrics:
        b_better = b_equal = a_better = 0
        n_pairs = 0
        for p in pairs:
            a, b = p["A"], p["B"]
            if b is None:
                continue
            n_pairs += 1
            av, bv = getattr(a, key), getattr(b, key)
            if direction == "higher_better":
                if bv > av + 1e-9:
                    b_better += 1
                elif bv < av - 1e-9:
                    a_better += 1
                else:
                    b_equal += 1
            else:  # lower_better
                if bv < av - 1e-9:
                    b_better += 1
                elif bv > av + 1e-9:
                    a_better += 1
                else:
                    b_equal += 1
        n_eff = b_better + a_better  # 符号检验剔除平局
        p = _binom_two_sided_p(b_better, n_eff) if n_eff > 0 else 1.0
        out[key] = {
            "n_pairs": n_pairs,
            "B_better": b_better,
            "equal": b_equal,
            "A_better": a_better,
            "sign_test_p_two_sided": round(p, 4),
            "direction": direction,
        }
    return out


def aggregate(results: list[dict]) -> dict:
    """results: [{A:RunResult, B:RunResult|None}, ...]"""
    a_rows = [r["A"] for r in results]
    b_rows = [r["B"] for r in results if r["B"] is not None]

    def block(rows: list[RunResult], label: str) -> dict:
        if not rows:
            return {"policy": label, "note": "B 模块未就绪，无数字（不伪造）"}
        return {
            "policy": label,
            "n_runs": len(rows),
            "completion_rate_mean": round(_mean([r.completion_rate for r in rows]), 4),
            "completion_rate_std": round(_std([r.completion_rate for r in rows]), 4),
            "full_set_achieved_ratio": round(
                sum(1 for r in rows if r.full_set_achieved) / len(rows), 4),
            "discount_steps_mean": round(_mean([r.discount_steps for r in rows]), 3),
            "mainline_damage_mean": round(_mean([r.mainline_damage for r in rows]), 4),
            "mainline_damage_events_mean": round(_mean([r.mainline_damage_events for r in rows]), 3),
            "hard_push_events_mean": round(_mean([r.hard_push_events for r in rows]), 3),
            "final_affection_mean": round(_mean([r.final_affection for r in rows]), 2),
            "final_stage_mean": round(_mean([r.final_stage for r in rows]), 2),
            "decision_ms_avg_mean": round(_mean([r.decision_ms_avg for r in rows]), 3),
            "wall_ms_mean": round(_mean([r.wall_ms for r in rows]), 2),
        }

    return {
        "A": block(a_rows, "A_greedy"),
        "B": block(b_rows, "B_boost_glide"),
    }


# ------------------------------------------------------------------ #
# 落盘
# ------------------------------------------------------------------ #
def _block_for_rows(rows: list, label: str) -> dict:
    if not rows:
        return {"policy": label, "note": "无数据"}
    return {
        "policy": label,
        "n_runs": len(rows),
        "completion_rate_mean": round(_mean([r.completion_rate for r in rows]), 4),
        "completion_rate_std": round(_std([r.completion_rate for r in rows]), 4),
        "full_set_achieved_ratio": round(
            sum(1 for r in rows if r.full_set_achieved) / len(rows), 4),
        "discount_steps_mean": round(_mean([r.discount_steps for r in rows]), 3),
        "mainline_damage_mean": round(_mean([r.mainline_damage for r in rows]), 4),
        "mainline_damage_events_mean": round(_mean([r.mainline_damage_events for r in rows]), 3),
        "hard_push_events_mean": round(_mean([r.hard_push_events for r in rows]), 3),
        "final_affection_mean": round(_mean([r.final_affection for r in rows]), 2),
        "final_stage_mean": round(_mean([r.final_stage for r in rows]), 2),
        "decision_ms_avg_mean": round(_mean([r.decision_ms_avg for r in rows]), 3),
        "wall_ms_mean": round(_mean([r.wall_ms for r in rows]), 2),
    }


def write_outputs(agg: dict, pairs: list[dict], out_stem: str = "benchmark_last") -> dict:
    json_path = RESULTS_DIR / f"{out_stem}.json"
    md_path = RESULTS_DIR / f"{out_stem}.md"
    csv_path = RESULTS_DIR / f"{out_stem}.csv"

    # 按场景类别分组
    cat_map: dict[str, list[dict]] = {}
    for p in pairs:
        cat = p.get("category", "uncategorized")
        cat_map.setdefault(cat, []).append(p)

    cat_tables = {}
    for cat, ps in cat_map.items():
        a_rows = [x["A"] for x in ps]
        b_rows = [x["B"] for x in ps if x["B"] is not None]
        cat_tables[cat] = {
            "n": len(ps),
            "A": _block_for_rows(a_rows, "A_greedy"),
            "B": _block_for_rows(b_rows, "B_boost_glide") if b_rows else {"note": "B 未就绪"},
        }

    sig = sign_test(pairs)
    manifest = [
        {"scenario": p["A"].scenario, "seed": p["A"].seed,
         "category": p.get("category", "?")}
        for p in pairs
    ]

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "本报告数字全部由真实运行产生；转移模型为合成假设，非真实用户数据。",
        "difficulty_params": DIFFICULTY_PARAMS,
        "aggregate": agg,
        "by_category": cat_tables,
        "sign_test": sig,
        "scenario_manifest": manifest,
        "per_pair": [
            {
                "scenario": p["A"].scenario,
                "seed": p["A"].seed,
                "category": p.get("category", "?"),
                "chat": p["chat"],
                "A": p["A"].as_dict(),
                "B": p["B"].as_dict() if p["B"] is not None else None,
            }
            for p in pairs
        ],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Markdown ---
    a = agg["A"]; b = agg["B"]
    lines = [
        "# LoveAI-v2 合成数据 Benchmark 报告",
        "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 场景数：{len(pairs)}（配对 A/B，同 seed）",
        "- 注意：数字全部真实运行产出；环境转移模型为**合成假设**，非真实用户数据。",
        "",
        "## 总览 A/B 对比表",
        "",
        "| 指标 | A_greedy | B_boost_glide |",
        "|---|---|---|",
    ]
    metric_rows = [
        ("全任务达成率 (mean±std)", "completion_rate_mean", "completion_rate_std"),
        ("整集100%达成比例", "full_set_achieved_ratio", None),
        ("平均折扣（迂回额外步，0=零折扣）", "discount_steps_mean", None),
        ("主线损伤度（累积|负impact|）", "mainline_damage_mean", None),
        ("伤主线硬推次数", "mainline_damage_events_mean", None),
        ("硬推总次数", "hard_push_events_mean", None),
        ("结局好感总分", "final_affection_mean", None),
        ("结局关系阶段", "final_stage_mean", None),
        ("单次决策 wall ms", "decision_ms_avg_mean", None),
        ("整场景 wall ms", "wall_ms_mean", None),
    ]
    for label, k, kstd in metric_rows:
        av = a.get(k, "—")
        bv = b.get(k, "—")
        if kstd and isinstance(av, float):
            av = f"{a[k]}±{a[kstd]}"
        if kstd and isinstance(bv, float):
            bv = f"{b[k]}±{b[kstd]}"
        lines.append(f"| {label} | {av} | {bv} |")

    # 按类别分表
    lines += ["", "## 分场景类别 A/B 对比", ""]
    for cat, tbl in cat_tables.items():
        lines += [f"### {cat}（n={tbl['n']}）", "",
                  "| 指标 | A_greedy | B_boost_glide |", "|---|---|---|"]
        ba, bb = tbl["A"], tbl["B"]
        for label, k, kstd in metric_rows:
            av = ba.get(k, "—"); bv = bb.get(k, "—") if isinstance(bb, dict) else "—"
            if kstd and isinstance(av, float):
                av = f"{ba[k]}±{ba[kstd]}"
            if kstd and isinstance(bv, float):
                bv = f"{bb[k]}±{bb[kstd]}"
            lines.append(f"| {label} | {av} | {bv} |")
        lines.append("")

    # 符号检验
    lines += ["## 配对符号检验（B vs A）", "",
              "| 指标 | B 优 | 平 | A 优 | 双侧 p |", "|---|---|---|---|---|"]
    for key, s in sig.items():
        lines.append(
            f"| {key} | {s['B_better']} | {s['equal']} | {s['A_better']} | {s['sign_test_p_two_sided']} |"
        )
    lines += [
        "",
        "## 难度参数表（可审计）",
        "",
        "| 参数 | easy | medium | hard |",
        "|---|---|---|---|",
    ]
    for k in ["npc_count", "npc_stance", "npc_influence", "conflict_pairs",
              "has_incident", "has_mutation", "horizon_mult"]:
        lines.append(f"| {k} | {DIFFICULTY_PARAMS['easy'][k]} | {DIFFICULTY_PARAMS['medium'][k]} | {DIFFICULTY_PARAMS['hard'][k]} |")
    lines += [
        "",
        "## 口径说明",
        "",
        "- **折扣 discount_steps**（忠实化修正）：按 detour.type 计额外时间步——equivalent=0、stagger=+1、buffer=+2、reform=+1。A 不迂回恒 0。",
        "- **主线损伤度**：硬推失败且任务 mainline_impact<0 时累加 |mainline_impact|。",
        "- **全任务达成率** = 完成任务数 / (小+中+主) 任务总数；整集100%达成 = 该场景全部任务完成。",
        "- **符号检验**：H0=P(B 优于 A)=0.5，精确二项双侧；平局剔除。",
        "",
    ]
    if b.get("note"):
        lines += [f"> B 组状态：{b['note']}", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")

    # --- CSV（扁平一行一策略一类别）---
    import csv
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["category", "policy", "n_runs", "completion_rate",
                    "completion_rate_std", "full_set_ratio", "discount_steps",
                    "mainline_damage", "hard_push_events", "final_affection",
                    "final_stage", "decision_ms_avg", "wall_ms"])
        for cat, tbl in cat_tables.items():
            for block in (tbl["A"], tbl["B"]):
                if "note" in block:
                    w.writerow([cat, block.get("policy", "?"), 0, block.get("note", "")] + [""] * 9)
                    continue
                w.writerow([
                    cat, block["policy"], block["n_runs"],
                    block["completion_rate_mean"], block.get("completion_rate_std", 0),
                    block["full_set_achieved_ratio"], block["discount_steps_mean"],
                    block["mainline_damage_mean"], block["hard_push_events_mean"],
                    block["final_affection_mean"], block["final_stage_mean"],
                    block["decision_ms_avg_mean"], block["wall_ms_mean"],
                ])

    return {"json": str(json_path), "md": str(md_path), "csv": str(csv_path)}


# ------------------------------------------------------------------ #
# main
# ------------------------------------------------------------------ #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--small", action="store_true", help="最小闭环：1 个场景 1 个 seed")
    ap.add_argument("--scenarios", type=int, default=1, help="场景数（配对跑）")
    ap.add_argument("--seed", type=int, default=0, help="起始种子")
    ap.add_argument("--difficulty", type=str, default="medium",
                    choices=["easy", "medium", "hard"], help="难度档")
    args = ap.parse_args()

    n = 1 if args.small else max(1, args.scenarios)
    scenarios = build_scenario_set(n=n, seed=args.seed, difficulty=args.difficulty)

    t0 = time.perf_counter()
    pairs = []
    for sc in scenarios:
        pairs.append(run_pair(sc, seed=sc.seed))
    agg = aggregate(pairs)
    paths = write_outputs(agg, pairs)
    total_wall = (time.perf_counter() - t0) * 1000.0

    # 打印对比表到控制台
    a = agg["A"]; b = agg["B"]
    print("=" * 64)
    print(f"LoveAI-v2 benchmark  | 场景数={len(pairs)}  总耗时={total_wall:.1f} ms")
    print("=" * 64)
    print(f"{'指标':<28}{'A_greedy':>16}{'B_boost_glide':>16}")
    print("-" * 64)
    rows = [
        ("全任务达成率", "completion_rate_mean"),
        ("整集100%达成比例", "full_set_achieved_ratio"),
        ("平均折扣(迂回步)", "discount_steps_mean"),
        ("主线损伤度", "mainline_damage_mean"),
        ("伤主线硬推次数", "mainline_damage_events_mean"),
        ("硬推总次数", "hard_push_events_mean"),
        ("结局好感总分", "final_affection_mean"),
        ("结局关系阶段", "final_stage_mean"),
        ("单次决策ms", "decision_ms_avg_mean"),
        ("整场景wall_ms", "wall_ms_mean"),
    ]
    for label, k in rows:
        av = a.get(k, "—"); bv = b.get(k, "—")
        print(f"{label:<28}{str(av):>16}{str(bv):>16}")
    print("-" * 64)
    # 聊天注入证明
    chat = pairs[0]["chat"]
    print(f"[注入证明] 注入聊天 {chat.get('injected',0)} 行 → "
          f"真实 AffectionEngine: proactive_ratio={chat.get('proactive_ratio')}, "
          f"recalc_total={chat.get('recalc_total')}, stage={chat.get('recalc_stage')}")
    if b.get("note"):
        print(f"[B 组] {b['note']}")
    print("=" * 64)
    print(f"结果文件:\n  {paths['json']}\n  {paths['md']}\n  {paths['csv']}")


if __name__ == "__main__":
    main()
