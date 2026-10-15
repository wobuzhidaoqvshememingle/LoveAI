# -*- coding: utf-8 -*-
r"""R3.3-79 活关系星图·NPC次级网络与家庭/伦理关系建模 —— 端到端真接线演示（headless）。

跑法（项目根）：
    & ".venv\Scripts\python.exe" scripts\demo_r3379.py

流程（覆盖需求验收点 1-6）：
  1. 真实 RelationshipGraph：建图 → add_npc_edge（NPC↔NPC 边带 stance/strength/relation_type）；
  2. compute_factions 派系聚类（反对派/助攻派 + 意见领袖）；
  3. 家庭背景字段：set_family_dict 手工录入 + extract_from_chats 聊天自动提取，缺失标"未知"；
  4. 伦理关系标记：mark_ethical（risk_hint/opportunity_hint，无价值判断）；
  5. 桥接层（R3.3-77 成果）真接线：NPC 网络活数据 → 任务决策注入（派系/家庭/伦理软任务）；
  6. 方向 B：任务完成 → NPC 立场回写星图 + NPC_STANCE_CHANGED / NPC_FACTION_UPDATED 事件 + 派系重算；
  7. layout_graph 透传新字段 + to_dict JSON 可序列化。
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402
from services.memory_service import MemoryService  # noqa: E402
from services.direction_compass import DirectionCompass  # noqa: E402
from services.relationship_graph import (  # noqa: E402
    RelationshipGraph, FamilyContext, EthicalMark, extract_from_chats,
    layout_graph)
from services.six_layer_persona import SixLayerProfile  # noqa: E402
from services import script_library  # noqa: E402
from services.bridges import build_bridge_network, events as ev  # noqa: E402
from services.bridges.pet_sink import InMemoryPetSink  # noqa: E402

PID = "r3379_demo"


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="loveai_r3379_demo_")
    failures: list[str] = []
    printed: list[str] = []

    def check(name: str, cond: bool, detail: str = ""):
        mark = "PASS" if cond else "FAIL"
        if not cond:
            failures.append(f"{name}: {detail}")
        printed.append(f"  [{mark}] {name} {detail}")

    def show(name: str, payload) -> None:
        printed.append(f"  --- {name}: {json.dumps(payload, ensure_ascii=False)}")

    try:
        db = Database(os.path.join(tmpdir, "r3379.db"))
        db.init_tables()
        db.execute("INSERT INTO persons(id, nickname) VALUES(:p, :n)",
                   {"p": PID, "n": "小雨"})
        import json as _json
        low = {d: 25.0 for d in AffectionEngine.WEIGHTS}
        db.execute(
            "INSERT OR REPLACE INTO current_affection(person_id, scores_json) "
            "VALUES(:p, :s)", {"p": PID, "s": _json.dumps(low)})

        print("=" * 68)
        print("R3.3-79 活关系星图·NPC次级网络与家庭/伦理关系建模 演示")
        print("=" * 68)

        # ============ 1) 建图 + NPC↔NPC 边 ============ #
        g = RelationshipGraph(center_id="me")
        n_ex = g.add_node("前任", "前任", intimacy=45)
        n_her_mom = g.add_node("她妈", "家人", intimacy=50)
        n_her_dad = g.add_node("她爸", "家人", intimacy=35)
        n_bff = g.add_node("闺蜜", "朋友", intimacy=70)
        n_my_mom = g.add_node("我妈", "家人", intimacy=55)
        n_my_dad = g.add_node("我爸", "家人", intimacy=40)
        # 节点立场（对"我方与目标在一起"的立场）
        g.adjust_stance(n_ex, -0.7)
        g.adjust_stance(n_her_mom, -0.5)
        g.adjust_stance(n_her_dad, -0.4)
        g.adjust_stance(n_bff, 0.6)
        g.adjust_stance(n_my_mom, 0.5)
        g.adjust_stance(n_my_dad, 0.4)
        # NPC↔NPC 边（前任↔现任家人；闺蜜↔我方父母）
        g.add_npc_edge(n_ex, n_her_mom, strength=0.8, stance=-0.6, relation_type="前任")
        g.add_npc_edge(n_ex, n_her_dad, strength=0.7, stance=-0.5, relation_type="前任")
        g.add_npc_edge(n_bff, n_my_mom, strength=0.7, stance=0.5, relation_type="朋友")
        g.add_npc_edge(n_bff, n_my_dad, strength=0.6, stance=0.4, relation_type="朋友")
        npc_edges = [e for e in g.edges
                     if e.source_id != "me" and e.target_id != "me"]
        check("NPC↔NPC 边真实生成(4条)", len(npc_edges) == 4,
              f"非星形边={len(npc_edges)} 条，均带 stance="
              f"{[e.stance for e in npc_edges]}")

        # ============ 2) 派系聚类 + 意见领袖 ============ #
        factions = g.compute_factions()
        labels = [f["label"] for f in factions.values()]
        check("派系聚类出反对派/助攻派",
              "反对派" in labels and "助攻派" in labels,
              f"labels={labels}")
        opp = next(f for f in factions.values() if f["label"] == "反对派")
        sup = next(f for f in factions.values() if f["label"] == "助攻派")
        check("反对派成员=前任+她父母",
              {g.nodes[m].name for m in opp["members"]} == {"前任", "她妈", "她爸"},
              f"members={[g.nodes[m].name for m in opp['members']]}")
        check("助攻派成员=闺蜜+我方父母",
              {g.nodes[m].name for m in sup["members"]} == {"闺蜜", "我妈", "我爸"},
              f"members={[g.nodes[m].name for m in sup['members']]}")
        check("意见领袖已识别(前任/闺蜜)",
              g.nodes[opp["opinion_leader"]].name == "前任"
              and g.nodes[sup["opinion_leader"]].name == "闺蜜",
              f"opp_leader={g.nodes[opp['opinion_leader']].name}, "
              f"sup_leader={g.nodes[sup['opinion_leader']].name}")
        show("factions", factions)

        # ============ 3) 家庭背景字段 ============ #
        g.set_family_dict(None, {
            "family_structure": "双亲", "parents_status": "离婚中",
            "siblings": ["姐姐"], "evidence": ["我爸在闹离婚"],
        })
        tf = g.get_target_family()
        check("家庭字段可手工录入", tf.parents_status == "离婚中"
              and tf.family_structure == "双亲" and tf.siblings == ["姐姐"],
              f"family_structure={tf.family_structure} parents_status="
              f"{tf.parents_status} siblings={tf.siblings}")
        # 聊天自动提取（persona_analyzer.extract_family_context 真接线）
        ex = extract_from_chats(PID, [{"content": "我妈昨天问起你，我爸在闹离婚，我姐总说我"}])
        auto_fam = g.get_target_family()
        check("聊天自动提取家庭线索", auto_fam.parents_status == "离婚中"
              and "姐姐" in auto_fam.siblings and auto_fam.family_structure != "未知",
              f"auto family_structure={auto_fam.family_structure} "
              f"parents_status={auto_fam.parents_status} "
              f"siblings={auto_fam.siblings}")
        # 缺失标"未知"：新建空图
        g2 = RelationshipGraph(center_id="me")
        empty = g2.get_target_family()
        check("缺失家庭字段标'未知'不臆造",
              empty.family_structure == "未知" and empty.parents_status == "未知"
              and empty.siblings == [] and empty.evidence == [],
              f"family_structure={empty.family_structure} "
              f"parents_status={empty.parents_status}")

        # ============ 4) 伦理关系标记 ============ #
        g.mark_ethical(n_ex, flag="ex_network_active", label="前任网络活跃",
                       risk_hint="前任仍在频繁联系，需留意三角张力",
                       opportunity_hint="前任立场若松动，可转化为背书",
                       evidence=["前任还在她家附近活动"])
        g.mark_ethical(n_bff, flag="sibling_affection", label="兄弟姐妹好感",
                       risk_hint="亲属对我方示好，需明确边界、不模糊关系",
                       opportunity_hint="")
        m1, m2 = g.nodes[n_ex].ethical_marks[0], g.nodes[n_bff].ethical_marks[0]
        check("伦理标记带风险/机会提示且无价值判断",
              m1.flag == "ex_network_active" and m1.risk_hint
              and m2.flag == "sibling_affection" and m2.risk_hint
              and not any(w in (m1.risk_hint + m1.opportunity_hint + m2.risk_hint)
                          for w in ("不对", "违背", "不道德")),
              f"ex={m1.label}: {m1.risk_hint} | sibling={m2.label}: {m2.risk_hint}")

        # ============ 5) NPC 网络 → 任务决策（桥接层真接线） ============ #
        affection = AffectionEngine(db)
        task_engine = TaskEngine(db, affection, guide_engine=None, signals=None)
        memory = MemoryService(db)
        compass = DirectionCompass(db=None)
        portrait = SixLayerProfile(person_id=PID)
        sink = InMemoryPetSink()
        net = build_bridge_network(
            affection=affection, task_engine=task_engine, compass=compass,
            memory=memory, portrait=portrait, script_lib=script_library,
            graph=g, pet_sink=sink)
        bus = net["bus"]
        npc_bridge = net["bridges"]["npc_task"]

        influential = g.influential_with_network()
        before = len(task_engine.get_active_tasks(PID))
        bus.reset_pass()
        bus.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID, "influential": influential})
        after = len(task_engine.get_active_tasks(PID))
        check("NPC网络→任务决策真实注入(含派系/家庭/伦理源)",
              after > before, f"任务数 {before} -> {after}")
        active = [t.get("title") for t in task_engine.get_active_tasks(PID)]
        has_faction = any("反对派立场" in t for t in active)
        has_family = any("不卷入" in t or "单亲" in t for t in active)
        has_ethics = any("风险提示" in t or "明确边界" in t for t in active)
        check("派系活数据进任务决策", has_faction,
              f"样例: {[t for t in active if '反对派立场' in t][:1]}")
        check("家庭背景活数据进任务决策", has_family,
              f"样例: {[t for t in active if '不卷入' in t or '单亲' in t][:1]}")
        check("伦理标记活数据进任务决策", has_ethics,
              f"样例: {[t for t in active if '风险提示' in t or '明确边界' in t][:1]}")
        show("influential_with_network(前2条摘要)", [
            {k: i[k] for k in ("name", "relation_type", "faction_label",
                               "faction_stance", "family", "ethical_marks")}
            if i.get("faction_label") else i.get("name")
            for i in influential[:2]])

        # ============ 6) 方向 B：任务完成 → 立场回写 + 事件 ============ #
        stance_before = g.nodes[n_ex].stance
        # 完成方向 A 创建的"前任"竞争预警任务
        created_titles = list(npc_bridge._created.keys())
        target_title = next(t for t in created_titles if "前任" in t and "竞争预警" in t)
        got_stance = []
        got_faction = []
        bus.subscribe(ev.NPC_STANCE_CHANGED, lambda d: got_stance.append(d))
        bus.subscribe(ev.NPC_FACTION_UPDATED, lambda d: got_faction.append(d))
        bus.reset_pass()
        bus.publish(ev.TASKS_COMPLETED, {
            "person_id": PID, "task_id": "t_r3379",
            "title": target_title})
        stance_after = g.nodes[n_ex].stance
        check("任务结果回写NPC立场(前任 -0.7 -> -0.55)",
              abs((stance_after - stance_before) - 0.15) < 1e-9,
              f"{stance_before} -> {stance_after}")
        check("NPC_STANCE_CHANGED 事件真实发出",
              len(got_stance) >= 1 and got_stance[-1]["npc_name"] == "前任"
              and abs(got_stance[-1]["delta"] - 0.15) < 1e-9,
              f"last={got_stance[-1] if got_stance else None}")
        check("NPC_FACTION_UPDATED 事件真实发出(派系重算)",
              len(got_faction) >= 1 and got_faction[-1]["factions"],
              f"重算后派系数={len(got_faction[-1]['factions']) if got_faction else 0}")
        new_opp = next(f for f in g.get_factions().values()
                       if f["label"] == "反对派")
        check("反对派 stance 随成员上修而减弱",
              new_opp["stance"] > opp["stance"],
              f"{round(opp['stance'], 3)} -> {round(new_opp['stance'], 3)}")
        show("network_summary", npc_bridge.network_summary())

        # ============ 7) 布局透传 + JSON 可序列化 ============ #
        laid = g.relayout(iterations=120, seed=7, dims=3)
        node0 = laid["nodes"][0]
        has_passthrough = ("family" in node0 and "ethical_marks" in node0
                           and "faction" in node0 and "faction_stance" in node0
                           and "stance" in node0)
        edge0 = laid["edges"][0]
        check("layout_graph 透传新字段(节点/边)",
              has_passthrough and "stance" in edge0,
              f"node0 keys={sorted(node0.keys())}, edge0 has stance={('stance' in edge0)}")
        raw_json = json.dumps(g.to_dict(), ensure_ascii=False)
        d = json.loads(raw_json)
        check("to_dict 全 JSON 可序列化且含新键",
              "factions" in d and "family_context" in d
              and all("family" in n and "ethical_marks" in n and "faction_stance" in n
                      for n in d["nodes"]),
              f"顶层键={sorted(d.keys())}")
        check("前端可消费：已布局图带 family/伦理/派系/stance 字段",
              laid["layout"]["n_nodes"] >= 6 and laid["layout"]["n_edges"] >= 4,
              f"n_nodes={laid['layout']['n_nodes']} n_edges={laid['layout']['n_edges']}")

        # ============ 指标 ============ #
        s = net["metrics"].summary()
        print(f"\n[指标] 桥数={s['total_bridges']} 事件入={s['total_events_in']} "
              f"成功转换={s['total_conversions']} 仲裁={s['arbitrated_writes']}")
        print("\n" + "=" * 68)
        print("\n".join(printed))
        print("=" * 68)
        if failures:
            print("结果：FAIL")
            for f in failures:
                print("  ! " + f)
            return 1
        print("结果：ALL PASS —— R3.3-79 全链路真接线验证通过")
        return 0
    finally:
        try:
            db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
