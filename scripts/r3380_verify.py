# -*- coding: utf-8 -*-
"""R3.3-80 桥接层真接线全链路演示（交付核心证据）。

跑法（项目根）：
    & ".venv\\Scripts\\python.exe" scripts\\r3380_verify.py

流程：
  1. 搭真实模块（真实 TaskEngine + 临时 sqlite + build_bridge_network + 真实星图）；
  2. 构造真实感中文聊天消息（祖辈话语权+继子女+家庭负债+分手复合循环+前任带新欢+创伤信号）；
  3. publish_complexity_profile → 桥消费 → 任务落库；
  4. 主动发一次 STAGE_CHANGED → B 类里程碑任务落库；
  5. 打印任务表落库标题 + 各桥 network_summary/metrics + 星图节点数；
  6. 逐条断言，全部 PASS 才返回 0。
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
from services.relationship_graph import RelationshipGraph  # noqa: E402
from services.six_layer_persona import SixLayerProfile  # noqa: E402
from services import script_library  # noqa: E402
from services.bridges import build_bridge_network, events as ev  # noqa: E402
from services.bridges.pet_sink import InMemoryPetSink  # noqa: E402
from services.bridges.complexity_publisher import publish_complexity_profile  # noqa: E402

PID = "r3380_demo"


def main() -> int:
    tmpdir = tempfile.mkdtemp(prefix="loveai_r3380_verify_")
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = ""):
        mark = "PASS" if cond else "FAIL"
        if not cond:
            failures.append(f"{name}: {detail}")
        print(f"  [{mark}] {name} {detail}")

    db = Database(os.path.join(tmpdir, "r3380.db"))
    db.init_tables()
    db.execute("INSERT INTO persons(id, nickname) VALUES(:p,:n)",
               {"p": PID, "n": "验证对象"})
    affection = AffectionEngine(db)
    tasks = TaskEngine(db, affection, guide_engine=None, signals=None)
    memory = MemoryService(db)
    compass = DirectionCompass(db=None)
    portrait = SixLayerProfile(person_id=PID)
    graph = RelationshipGraph(center_id="me")
    pet_sink = InMemoryPetSink()
    low = {d: 25.0 for d in affection.WEIGHTS}
    import json as _json
    db.execute(
        "INSERT OR REPLACE INTO current_affection(person_id, scores_json) "
        "VALUES(:p,:s)", {"p": PID, "s": _json.dumps(low)})

    net = build_bridge_network(
        affection=affection, task_engine=tasks, compass=compass,
        memory=memory, portrait=portrait, script_lib=script_library,
        graph=graph, pet_sink=pet_sink)
    bus = net["bus"]

    print("=" * 72)
    print("R3.3-80 桥接层真接线全链路演示")
    print("=" * 72)

    # ---- 真实感中文聊天消息（覆盖 A/D/F + 负债/养老信号）----
    messages = [
        {"content": "我奶奶在家里特别说了算，她要是不同意，这门事基本就黄了"},
        {"content": "我爷爷之前还挺看好我们，但最近有点嫌弃我工作一般"},
        {"content": "她是离异带个娃过来的，那个孩子是她和前任的孩子"},
        {"content": "那个孩子现在见都不想见我，挺抵触，不肯叫人"},
        {"content": "家里还背着房贷，老人养老压力也大，最近还钱压力挺大"},
        {"content": "上次她突然说分手吧，别联系了"},
        {"content": "结果没几天又加回来，说我们和好吧"},
        {"content": "后来又吵了一大架，她说散了吧结束吧"},
        {"content": "没过多久我们又在一起了，中间还断联复联、反复拉扯"},
        {"content": "我都听说她现在有新男友了，我还在这反复"},
        {"content": "我从小就是讨好型人格，一直委屈自己、从来都是我让步"},
        {"content": "一不回消息我就慌，特别怕被抛弃、怕你不要我"},
    ]

    graph_before = len(graph.nodes)
    print(f"\n[1] 发布前星图节点数: {graph_before}")

    # ---- 发布复杂度画像（事件 → 桥消费 → 落库）----
    print("\n[2] publish_complexity_profile → 桥消费 → 任务落库")
    bus.reset_pass()
    profile = publish_complexity_profile(bus, PID, messages, persona_result=None)

    print("\n  复杂度画像关键检测：")
    fs = profile["family_system"]
    print(f"    A 祖辈话语权: detected={fs['grandparent_influence']['detected']} "
          f"who={fs['grandparent_influence']['who']} stance={fs['grandparent_influence']['stance']}")
    print(f"    A 继子女: detected={fs['stepchildren']['detected']} "
          f"acceptance={fs['stepchildren']['acceptance_level']}")
    print(f"    A 家庭负债: detected={fs['family_debt']['detected']} "
          f"level={fs['family_debt']['level']}")
    print(f"    C 关系状态: {profile['relationship_state']['status']}")
    rb = profile["rebound_pattern"]
    print(f"    D 复合循环: cycle_count={rb['cycle_count']} entangled={rb['entangled']} "
          f"ex_new={rb['ex_new_partner']} escalation={rb['escalation_level']}")
    td = profile["trauma_drivers"]
    print(f"    F 创伤类型: {[t['type'] for t in td['traumas']]}")

    # ---- B 类：阶段推进 ----
    print("\n[3] STAGE_CHANGED(stage=对象) → B 类里程碑任务落库")
    bus.reset_pass()
    bus.publish(ev.STAGE_CHANGED, {"person_id": PID, "stage": "对象"})

    # ---- 任务表落库标题 ----
    rows = tasks.get_active_tasks(PID)
    titles = [r.get("title") or "" for r in rows]
    print(f"\n[4] 任务表真实落库标题（共 {len(titles)} 条）：")
    for t in titles:
        print(f"    - {t}")

    # ---- 星图节点 ----
    graph_after = len(graph.nodes)
    node_names = [getattr(n, "name", "") for n in graph.nodes.values()]
    print(f"\n[5] 星图节点数: {graph_before} → {graph_after}")
    print(f"    节点名: {node_names}")

    # ---- 桥 metrics / summary ----
    print("\n[6] 各桥 network_summary：")
    for name in ("family_system", "milestone_task", "trauma_portrait"):
        b = net["bridges"].get(name)
        if b is not None and hasattr(b, "network_summary"):
            print(f"    {name}: {json.dumps(b.network_summary(), ensure_ascii=False)}")
    print("\n[7] 桥 metrics 转换统计：")
    summ = net["metrics"].summary()
    for name, rep in summ.get("bridges", {}).items():
        if name in ("family_system", "milestone_task", "trauma_portrait"):
            print(f"    {name}: conversions={rep.get('conversions')} "
                  f"failures={rep.get('failures')} "
                  f"failure_rate={rep.get('failure_rate')}")

    # ---- 断言 ----
    print("\n" + "=" * 72)
    print("断言：")
    check("A 祖辈见家长任务落库", any("见家长支线" in t and "祖辈" in t for t in titles))
    check("A 继子女任务落库", any("继子女接受度" in t for t in titles))
    check("A 负债节奏提示任务落库", any("关系节奏调整提示" in t for t in titles))
    check("D 冷静期/止损干预任务落库", any("冷静期" in t for t in titles))
    check("D 纠缠/新欢叠加任务落库", any("纠缠" in t or "视奸" in t for t in titles))
    check("F 边界练习任务落库", any("边界练习" in t for t in titles))
    check("F 稳定锚点任务落库", any("稳定锚点" in t for t in titles))
    check("B 里程碑任务落库", any(t.startswith("[里程碑]") for t in titles))
    check("星图节点数增加", graph_after > graph_before,
          f"{graph_before}->{graph_after}")
    check("星图写入祖辈节点", "奶奶" in node_names or "爷爷" in node_names,
          f"{node_names}")
    check("family_system 桥有转换", summ["bridges"].get("family_system", {}).get("conversions", 0) >= 1)
    check("milestone_task 桥有转换", summ["bridges"].get("milestone_task", {}).get("conversions", 0) >= 1)
    check("trauma_portrait 桥有转换", summ["bridges"].get("trauma_portrait", {}).get("conversions", 0) >= 1)

    print("\n" + "=" * 72)
    if failures:
        print(f"结果: {len(failures)} 条 FAIL")
        for f in failures:
            print(f"  - {f}")
        rc = 1
    else:
        print("结果: 全部 PASS ✅")
        rc = 0

    try:
        db.engine.dispose()
    except Exception:
        pass
    shutil.rmtree(tmpdir, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
