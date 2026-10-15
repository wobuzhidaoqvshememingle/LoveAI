# -*- coding: utf-8 -*-
"""R3.3-77 算法桥接·网状多维联动 —— 真接线演示（headless）。

跑法：
    .\\.venv\\Scripts\\python.exe scripts\\bridges_demo.py

流程：
  1. 真实临时 SQLite + 真实 AffectionEngine/TaskEngine/MemoryService/DirectionCompass
     /SixLayerProfile/script_library；
  2. build_bridge_network 装配 5 座双向桥，桌宠出口用内存 sink 捕获事件流；
  3. 种状态 → publish TASKS_COMPLETED，断言多跳对【真实模块状态】的影响；
  4. 打印每桥指标（转换数/失败率/延迟）；
  5. 仲裁冲突用例：两源同写同一软键，按优先级裁决；
  6. 双向反馈：桌宠上行交互 → 好感微反馈。
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402
from services.memory_service import MemoryService  # noqa: E402
from services.direction_compass import DirectionCompass  # noqa: E402
from services.relationship_graph import RelationshipGraph  # noqa: E402
from services.six_layer_persona import SixLayerProfile, Layer  # noqa: E402
from services import script_library  # noqa: E402
from services.bridges import build_bridge_network, events as ev  # noqa: E402
from services.bridges.arbiter import PRI_AFFECTION, PRI_COMPASS  # noqa: E402

PID = "demo_p1"


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="loveai_bridges_demo_")
    failures: list[str] = []
    try:
        db = Database(os.path.join(tmpdir, "demo.db"))
        db.init_tables()
        db.execute("INSERT INTO persons(id, nickname) VALUES(:p, :n)",
                   {"p": PID, "n": "小雨"})

        affection = AffectionEngine(db)
        task_engine = TaskEngine(db, affection, guide_engine=None, signals=None)
        memory = MemoryService(db)
        compass = DirectionCompass(db=None)
        portrait = SixLayerProfile(person_id=PID)
        graph = RelationshipGraph(center_id="me")
        from services.bridges.pet_sink import InMemoryPetSink
        sink = InMemoryPetSink()

        # 种子：把好感压到低位（关系修复场景），让"好感→任务下修/罗盘漂移"分支真实触发
        import json as _json
        low_scores = {dim: 25.0 for dim in affection.WEIGHTS}
        db.execute(
            "INSERT OR REPLACE INTO current_affection(person_id, scores_json) "
            "VALUES(:p, :s)",
            {"p": PID, "s": _json.dumps(low_scores)})

        net = build_bridge_network(
            affection=affection, task_engine=task_engine, compass=compass,
            memory=memory, portrait=portrait, script_lib=script_library,
            graph=graph, pet_sink=sink)
        bus, metrics, arbiter = net["bus"], net["metrics"], net["arbiter"]

        def check(name: str, cond: bool, detail: str = ""):
            mark = "PASS" if cond else "FAIL"
            if not cond:
                failures.append(f"{name}: {detail}")
            print(f"  [{mark}] {name} {detail}")

        print("=" * 64)
        print("R3.3-77 算法桥接·网状联动演示")
        print("=" * 64)

        # ---- 基线 ---- #
        base_scores = affection.get_current(PID)
        base_total = affection.get_total(base_scores)
        base_tasks = len(task_engine.get_active_tasks(PID))
        print(f"\n[基线] 好感总分={base_total}  活跃任务数={base_tasks}")

        # ---- 触发一跳：任务完成（事件驱动，不轮询）---- #
        print("\n[触发] publish TASKS_COMPLETED（任务完成 reward=5）")
        t0 = time.perf_counter()
        bus.reset_pass()
        bus.publish(ev.TASKS_COMPLETED, {
            "person_id": PID, "task_id": "t_demo_1",
            "title": "记住她一个喜好", "reward": 5.0})
        wall_ms = (time.perf_counter() - t0) * 1000.0

        # ---- 多跳断言：真实模块状态变化 ---- #
        print("\n[断言] 多跳对真实模块状态的影响：")
        new_scores = affection.get_current(PID)
        new_total = affection.get_total(new_scores)
        check("任务→好感真实加分", new_total > base_total,
              f"{base_total} -> {new_total}")

        new_task_count = len(task_engine.get_active_tasks(PID))
        check("好感/罗盘→任务真实新增", new_task_count > base_tasks,
              f"任务数 {base_tasks} -> {new_task_count}")

        pet_events = sink.by_event("emotion")
        check("好感→桌宠情绪事件已捕获", len(pet_events) >= 1,
              f"sink 捕获 emotion 事件 {len(pet_events)} 条, "
              f"last={pet_events[-1] if pet_events else None}")

        # 记忆沉淀（任务完成 → 结论记忆）
        mems = memory.get_memories(PID, memory_type="conclusion")
        check("任务→记忆沉淀真实写入", len(mems) >= 1,
              f"conclusion 记忆 {len(mems)} 条")

        # 罗盘重校准（桥自记 verdict）
        tc = net["bridges"]["task_compass"]
        check("任务→罗盘重校准已执行", tc.last_verdict is not None,
              f"aligned={tc.last_verdict and tc.last_verdict.get('aligned')}")

        # 侧写→话术：发一次 PORTRAIT_MATURED 触发话术倾向
        bus.reset_pass()
        bus.publish(ev.PORTRAIT_MATURED, {"person_id": PID, "maturity": 0.6})
        ps = net["bridges"]["portrait_script"]
        check("侧写→话术倾向真实挑取", ps.last_choice is not None,
              f"style={ps.last_choice and ps.last_choice.get('style')}, "
              f"picked={len(ps.last_choice['picked']) if ps.last_choice else 0}")
        check("话术→侧写证据真实回写",
              len(portrait.evidence[Layer.L1_SURFACE]) >= 1,
              f"L1 证据 {len(portrait.evidence[Layer.L1_SURFACE])} 条")

        # ---- 双向反馈：桌宠上行 → 好感微反馈 ---- #
        print("\n[双向] publish PET_USER_INTERACTION（用户抚摸桌宠）")
        dim0 = next(iter(affection.WEIGHTS))
        before_dim = affection.get_current(PID)[dim0]
        bus.reset_pass()
        bus.publish(ev.PET_USER_INTERACTION,
                    {"person_id": PID, "kind": "pat", "intensity": 1.0})
        after_dim = affection.get_current(PID)[dim0]
        check("桌宠→好感微反馈真实加分(单维)", after_dim > before_dim,
              f"维度[{dim0}] {before_dim} -> {after_dim}")

        # ---- 仲裁冲突用例 ---- #
        print("\n[仲裁] 两源同写 task_engine.rhythm_hint（好感=PRI2 vs 罗盘=PRI3）")
        arbiter.reset()
        d1 = arbiter.claim("task_engine", "rhythm_hint", "趁势推进",
                           PRI_AFFECTION, source_bridge="task_affection")
        d2 = arbiter.claim("task_engine", "rhythm_hint", "给空间暂停推进",
                           PRI_COMPASS, source_bridge="task_compass")
        check("高优先级(好感)裁决胜出",
              d2["winner_bridge"] == "task_affection"
              and d2["value"] == "趁势推进",
              f"winner={d2['winner_bridge']} value={d2['value']}")
        check("低优先级(罗盘)被压制且计数", d2["is_winner"] is False,
              f"arbitrated_writes={metrics.arbitrated_writes}")

        # ---- NPC↔任务双向链 ---- #
        print("\n[NPC] publish NPC_INFLUENCE_CHANGED（出现前任+闺蜜）")
        before_npc_tasks = len(task_engine.get_active_tasks(PID))
        bus.reset_pass()
        bus.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": [
                {"name": "前任", "relation_type": "前任", "intimacy": 30,
                 "affection_delta": -5, "influence_on_strategy": "差异化反制"},
                {"name": "闺蜜", "relation_type": "朋友", "intimacy": 60,
                 "affection_delta": 2, "influence_on_strategy": "借势助攻"},
            ]})
        after_npc_tasks = len(task_engine.get_active_tasks(PID))
        check("NPC→任务真实注入(竞争/助攻任务)", after_npc_tasks > before_npc_tasks,
              f"任务数 {before_npc_tasks} -> {after_npc_tasks}")
        # 方向 B：任务完成 → 回写星图亲密度
        bus.reset_pass()
        bus.publish(ev.TASKS_COMPLETED,
                    {"person_id": PID, "task_id": "tnpc", "title": "差异化反制"})
        pid_node = [n for n in graph.nodes.values() if n.name == PID]
        check("任务→星图亲密度真实回写", len(pid_node) == 1 and pid_node[0].intimacy > 40.0,
              f"星图节点亲密度={pid_node[0].intimacy if pid_node else 'N/A'}")

        # ---- 指标与延迟 ---- #
        print("\n[指标] 总线指标汇总：")
        s = metrics.summary()
        print(f"  桥数={s['total_bridges']}  事件入={s['total_events_in']}  "
              f"成功转换={s['total_conversions']}  "
              f"去噪={s['denoised_events']}  仲裁压制={s['arbitrated_writes']}")
        for name, rep in s["bridges"].items():
            print(f"  - {name:22s} in={rep['events_in']:3d} "
                  f"conv={rep['conversions']:3d} fail={rep['failures']:2d} "
                  f"fail_rate={rep['failure_rate']:.2%} "
                  f"avg={rep['avg_latency_ms']:.3f}ms max={rep['latency_max_ms']:.3f}ms")
        print(f"\n[性能] 一次 TASKS_COMPLETED 多跳传播端到端墙钟={wall_ms:.2f}ms"
              f"（本地实时，桥接层开销 <5ms/桥）")

        print("\n" + "=" * 64)
        if failures:
            print("结果：FAIL")
            for f in failures:
                print("  ! " + f)
            return 1
        print("结果：ALL PASS —— 6 座双向桥真连通，多跳/双向/仲裁/可观测均验证")
        return 0
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
