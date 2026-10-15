# -*- coding: utf-8 -*-
"""R3.3-79 NPC 网络↔任务决策 双向联动 集成测试（headless 真接线）。

覆盖：
  a) 预置关系图 → compute_factions 产出"反对派/助攻派"，成员与 opinion_leader 正确
  b) publish NPC_INFLUENCE_CHANGED → 派生 faction / ethics / family 类软任务
  c) 完成方向 A 创建的任务 → 对应 NPC stance 上修，NPC_STANCE_CHANGED /
     NPC_FACTION_UPDATED 事件被总线订阅者收到，派系重算
  d) family_context 场景：无家庭上下文不派生"不卷入"任务；设置后派生
  e) 回归：tests/test_bridges.py 全绿（尤其 test_08/test_09 不回归）

说明（接口偏差）：生产 services.relationship_graph.RelationshipGraph 暂未实现
派系/立场/家庭/伦理这层 NPC 活数据面。本测试在不改动 relationship_graph.py 的
前提下，用 RichNPCGraph(RelationshipGraph) 子类补齐该面；桥经 getattr duck-typing
消费这些接口——生产图没有这些方法时安全跳过，未来图补齐后零改动即生效。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_TESTS = os.path.dirname(os.path.abspath(__file__))
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.relationship_graph import RelationshipGraph  # noqa: E402
from services import script_library  # noqa: E402
from services.bridges import build_bridge_network, events as ev  # noqa: E402
from test_bridges import BridgesHarness, PID  # noqa: E402


class RichNPCGraph(RelationshipGraph):
    """在只读 RelationshipGraph 之上补齐 R3.3-79 的派系/立场/家庭/伦理活数据面。

    节点动态属性：stance(-1..1)、ethical_marks([{kind,label}])。
    这是测试侧的合规实现；生产 RelationshipGraph 未来补齐同名方法后，桥自动启用。
    """

    def __init__(self, center_id: str = "me"):
        super().__init__(center_id=center_id)
        self._family: dict = {}
        self._factions: list = []

    # ---- 节点/边构造 ---- #
    def add_npc_node(self, name, relation_type="其他", intimacy=30.0, stance=0.0):
        nid = self.add_node(name, relation_type=relation_type, intimacy=intimacy)
        node = self.nodes[nid]
        node.stance = stance
        node.ethical_marks = []
        return nid

    def _nid(self, name):
        for nid, n in self.nodes.items():
            if n.name == name:
                return nid
        return None

    def add_npc_edge(self, a_name, b_name, strength=0.5, stance=0.0):
        a, b = self._nid(a_name), self._nid(b_name)
        self.add_edge(a, b, strength=strength, rt="npc")
        self.edges[-1].stance = stance

    # ---- 立场回写 ---- #
    def adjust_stance(self, nid, delta):
        node = self.nodes.get(nid)
        if node is not None:
            node.stance = getattr(node, "stance", 0.0) + delta

    # ---- 派系（并查集聚类，>=2 节点才成派）---- #
    def compute_factions(self):
        parent = {nid: nid for nid in self.nodes}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for e in self.edges:
            s, t = e.source_id, e.target_id
            if s in parent and t in parent:
                rs, rt = find(s), find(t)
                if rs != rt:
                    parent[rs] = rt
        clusters: dict = {}
        for nid in self.nodes:
            clusters.setdefault(find(nid), []).append(nid)
        self._factions = []
        for members in clusters.values():
            if len(members) < 2:
                continue
            stances = [getattr(self.nodes[m], "stance", 0.0) for m in members]
            avg = sum(stances) / len(stances)
            leader = max(members, key=lambda m: abs(getattr(self.nodes[m], "stance", 0.0)))
            name = "反对派" if avg < -0.1 else ("助攻派" if avg > 0.1 else "中立派")
            self._factions.append({"name": name, "stance": avg,
                                   "members": members, "opinion_leader": leader})
        return self._factions

    def get_factions(self):
        return list(self._factions)

    # ---- 家庭上下文 ---- #
    def set_family_dict(self, key, value):
        if key is None:
            self._family = dict(value or {})
        else:
            self._family[key] = value

    def get_target_family(self):
        return dict(self._family)


class TestR3379NPCBridge(unittest.TestCase):
    def setUp(self):
        # 每个用例一套真实模块 + 全新临时 DB（避免任务标题去重串味）
        self.base = BridgesHarness()
        self.graph = self._seed_graph()
        self.net = build_bridge_network(
            affection=self.base.affection, task_engine=self.base.tasks,
            compass=self.base.compass, memory=self.base.memory,
            portrait=self.base.portrait, script_lib=script_library,
            graph=self.graph, pet_sink=self.base.pet_sink)
        self.bus = self.net["bus"]
        self.bridge = self.net["bridges"]["npc_task"]
        self.tasks = self.base.tasks

    def tearDown(self):
        self.base.cleanup()

    def _seed_graph(self):
        g = RichNPCGraph("me")
        self.nid_xy = g.add_npc_node("小雨", "暧昧对象", 50.0, stance=0.3)
        self.nid_ex = g.add_npc_node("前任", "前任", 30.0, stance=-0.7)
        self.nid_hp = g.add_npc_node("她父母", "家人", 40.0, stance=-0.5)
        self.nid_br = g.add_npc_node("闺蜜", "朋友", 60.0, stance=0.6)
        self.nid_mp = g.add_npc_node("我父母", "家人", 55.0, stance=0.5)
        g.add_npc_edge("前任", "她父母", strength=0.8, stance=-0.6)
        g.add_npc_edge("闺蜜", "我父母", strength=0.7, stance=0.5)
        g.nodes[self.nid_ex].ethical_marks = [
            {"kind": "risk_hint", "label": "前任仍在频繁联系"}]
        g.nodes[self.nid_br].ethical_marks = [
            {"kind": "opportunity_hint", "label": "闺蜜愿帮忙说好话"}]
        g.set_family_dict(None, {"parents_status": "离婚中"})
        g.compute_factions()
        return g

    # ------------------------------------------------------------------ #
    def test_a_factions_clustered(self):
        facs = self.graph.get_factions()
        names = [f["name"] for f in facs]
        self.assertIn("反对派", names, facs)
        self.assertIn("助攻派", names, facs)
        opp = [f for f in facs if f["name"] == "反对派"][0]
        aid = [f for f in facs if f["name"] == "助攻派"][0]
        self.assertIn(self.nid_ex, opp["members"])
        self.assertIn(self.nid_hp, opp["members"])
        self.assertIn(self.nid_br, aid["members"])
        self.assertIn(self.nid_mp, aid["members"])
        self.assertIsNotNone(opp["opinion_leader"])
        self.assertIsNotNone(aid["opinion_leader"])

    def test_b_injection_faction_ethics_family(self):
        before = len(self.tasks.get_active_tasks(PID))
        self.bus.reset_pass()
        self.bus.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": [
                {"name": "前任", "relation_type": "前任", "affection_delta": -5},
                {"name": "闺蜜", "relation_type": "朋友", "affection_delta": 2}]})
        after = len(self.tasks.get_active_tasks(PID))
        self.assertGreater(after, before, "NPC 影响未注入任务")
        titles = [t["title"] for t in self.tasks.get_active_tasks(PID)]
        self.assertTrue(any("反对派立场" in x for x in titles), titles)
        self.assertTrue(any("风险提示" in x for x in titles), titles)
        self.assertTrue(any("可利用机会" in x for x in titles), titles)
        self.assertTrue(any("不卷入" in x for x in titles), titles)
        self.assertGreater(len(self.bridge._created), 0)

    def test_c_completion_writes_stance_and_publishes(self):
        received = []
        self.bus.subscribe(ev.NPC_STANCE_CHANGED, lambda d: received.append(("stance", d)))
        self.bus.subscribe(ev.NPC_FACTION_UPDATED, lambda d: received.append(("factions", d)))
        self.bus.reset_pass()
        self.bus.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": [{"name": "前任", "relation_type": "前任", "affection_delta": -5}]})
        ex_titles = [t for t, rec in self.bridge._created.items()
                     if rec.get("nid") == self.nid_ex]
        self.assertTrue(ex_titles, self.bridge._created)
        title = ex_titles[0]
        before_stance = self.graph.nodes[self.nid_ex].stance
        self.bus.reset_pass()
        self.bus.publish(ev.TASKS_COMPLETED,
                         {"person_id": PID, "task_id": "tc1", "title": title})
        after_stance = self.graph.nodes[self.nid_ex].stance
        self.assertGreater(after_stance, before_stance,
                           f"stance 未上修: {before_stance} -> {after_stance}")
        self.assertTrue(any(k == "stance" for k, _ in received), received)
        self.assertTrue(any(k == "factions" for k, _ in received), received)
        summ = self.bridge.network_summary()
        self.assertTrue(summ["last_stance_changes"], summ)
        self.assertTrue(summ["last_factions"], summ)

    def test_d_family_context_gated(self):
        # 全新小图：无家庭上下文
        g2 = RichNPCGraph("me")
        g2.add_npc_node("前任", "前任", 30.0, stance=-0.7)
        net2 = build_bridge_network(
            affection=self.base.affection, task_engine=self.base.tasks,
            compass=self.base.compass, memory=self.base.memory,
            portrait=self.base.portrait, script_lib=script_library,
            graph=g2, pet_sink=self.base.pet_sink)
        bus2 = net2["bus"]
        bus2.reset_pass()
        bus2.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": [{"name": "前任", "relation_type": "前任"}]})
        titles = [t["title"] for t in self.tasks.get_active_tasks(PID)]
        self.assertFalse(any("不卷入" in x for x in titles),
                         f"无家庭上下文不应产生不卷入任务: {titles}")
        # 设置家庭上下文后 → 派生中性"不卷入"任务
        g2.set_family_dict(None, {"parents_status": "离婚中"})
        bus2.reset_pass()
        bus2.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": [{"name": "前任", "relation_type": "前任"}]})
        titles = [t["title"] for t in self.tasks.get_active_tasks(PID)]
        self.assertTrue(any("不卷入" in x for x in titles), titles)


if __name__ == "__main__":
    unittest.main()
