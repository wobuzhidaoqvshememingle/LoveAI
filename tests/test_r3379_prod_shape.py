# -*- coding: utf-8 -*-
"""R3.3-79 桥接层 × 生产 RelationshipGraph 真实 dataclass 形状 集成测试（headless 真接线）。

背景：tests\test_r3379_npc_bridge.py 用 RichNPCGraph 子类（dict/list 形状：
get_factions()->list、ethical_marks=[{kind,label}]、get_target_family()->dict）
只覆盖了 legacy dict 路径。本文件用【生产 services.relationship_graph.RelationshipGraph】
真实形状覆盖：
  - get_factions() 返回 dict（fid -> faction dict）
  - node.ethical_marks 是 EthicalMark dataclass（字段 flag/label/risk_hint/opportunity_hint，
    无 kind 字段）
  - get_target_family() 返回 FamilyContext dataclass
断言桥经 getattr/_field 双态取值真实消费生产形状；legacy dict 兼容由 test_r3379_npc_bridge.py 覆盖。
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

from services.relationship_graph import (  # noqa: E402
    RelationshipGraph, FamilyContext, EthicalMark)
from services import script_library  # noqa: E402
from services.bridges import build_bridge_network, events as ev  # noqa: E402
from test_bridges import BridgesHarness, PID  # noqa: E402


class TestR3379ProductionShape(unittest.TestCase):
    def setUp(self):
        self.base = BridgesHarness()
        self.graph = self._seed_production_graph()
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

    def _seed_production_graph(self) -> RelationshipGraph:
        """生产图（非子类）：NPC 节点 + NPC↔NPC 边 + 派系 + 家庭 + 伦理 dataclass。"""
        g = RelationshipGraph(center_id="me")
        self.nid_ex = g.add_node("前任", "前任", intimacy=45)
        self.nid_hm = g.add_node("她妈", "家人", intimacy=50)
        self.nid_hd = g.add_node("她爸", "家人", intimacy=35)
        self.nid_bf = g.add_node("闺蜜", "朋友", intimacy=70)
        self.nid_mm = g.add_node("我妈", "家人", intimacy=55)
        self.nid_md = g.add_node("我爸", "家人", intimacy=40)
        g.adjust_stance(self.nid_ex, -0.7)
        g.adjust_stance(self.nid_hm, -0.5)
        g.adjust_stance(self.nid_hd, -0.4)
        g.adjust_stance(self.nid_bf, 0.6)
        g.adjust_stance(self.nid_mm, 0.5)
        g.adjust_stance(self.nid_md, 0.4)
        # NPC↔NPC 边（生产 add_npc_edge）
        g.add_npc_edge(self.nid_ex, self.nid_hm, strength=0.8, stance=-0.6,
                       relation_type="前任")
        g.add_npc_edge(self.nid_ex, self.nid_hd, strength=0.7, stance=-0.5,
                       relation_type="前任")
        g.add_npc_edge(self.nid_bf, self.nid_mm, strength=0.7, stance=0.5,
                       relation_type="朋友")
        g.add_npc_edge(self.nid_bf, self.nid_md, strength=0.6, stance=0.4,
                       relation_type="朋友")
        # 家庭背景（生产 FamilyContext 经 set_family_dict 写入图级）
        g.set_family_dict(None, {
            "family_structure": "双亲", "parents_status": "离婚中",
            "siblings": ["姐姐"], "evidence": ["我爸在闹离婚"]})
        # 伦理标记（生产 EthicalMark dataclass，无 kind）
        g.mark_ethical(self.nid_ex, flag="ex_network_active",
                       label="前任网络活跃",
                       risk_hint="前任仍在频繁联系，需留意三角张力",
                       opportunity_hint="前任立场若松动，可转化为背书",
                       evidence=["前任还在她家附近活动"])
        g.mark_ethical(self.nid_bf, flag="sibling_affection",
                       label="兄弟姐妹好感",
                       risk_hint="亲属对我方示好，需明确边界、不模糊关系",
                       opportunity_hint="")
        g.compute_factions()
        return g

    # ------------------------------------------------------------------ #
    def test_01_production_shapes_are_dataclasses(self):
        """形状基线：生产图真实返回类型符合预期（dict/dataclass/dataclass）。"""
        facs = self.graph.get_factions()
        self.assertIsInstance(facs, dict)
        fam = self.graph.get_target_family()
        self.assertIsInstance(fam, FamilyContext)
        self.assertEqual(fam.parents_status, "离婚中")
        self.assertEqual(fam.siblings, ["姐姐"])
        marks = self.graph.nodes[self.nid_ex].ethical_marks
        self.assertTrue(marks)
        self.assertIsInstance(marks[0], EthicalMark)
        self.assertTrue(hasattr(marks[0], "risk_hint"))
        self.assertFalse(hasattr(marks[0], "kind"))

    def test_02_factions_dict_has_opposition_and_support(self):
        """get_factions() dict 的 values 含反对派/助攻派与意见领袖。"""
        facs = list(self.graph.get_factions().values())
        labels = [f["label"] for f in facs]
        self.assertIn("反对派", labels)
        self.assertIn("助攻派", labels)
        opp = next(f for f in facs if f["label"] == "反对派")
        sup = next(f for f in facs if f["label"] == "助攻派")
        self.assertEqual({*opp["members"]},
                         {self.nid_ex, self.nid_hm, self.nid_hd})
        self.assertEqual({*sup["members"]},
                         {self.nid_bf, self.nid_mm, self.nid_md})
        self.assertEqual(opp["opinion_leader"], self.nid_ex)
        self.assertEqual(sup["opinion_leader"], self.nid_bf)

    def test_03_bridge_consumes_dataclass_into_tasks(self):
        """方向A：生产 dataclass 形状 → 派系/伦理/家庭软任务真实派生。"""
        before = len(self.tasks.get_active_tasks(PID))
        self.bus.reset_pass()
        self.bus.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": self.graph.influential_with_network()})
        after = len(self.tasks.get_active_tasks(PID))
        self.assertGreater(after, before, "生产形状 NPC 活数据未注入任务")
        titles = [t["title"] for t in self.tasks.get_active_tasks(PID)]
        self.assertTrue(any("反对派立场" in x for x in titles), titles)
        self.assertTrue(any("前任网络活跃" in x and "风险提示" in x for x in titles),
                        titles)
        self.assertTrue(any("兄弟姐妹好感" in x for x in titles), titles)
        self.assertTrue(any("不卷入" in x for x in titles), titles)
        # 无价值判断
        for x in titles:
            self.assertFalse(any(w in x for w in ("不对", "违背", "不道德")), x)

    def test_04_completion_writes_stance_on_production_graph(self):
        """方向B：完成任务 → adjust_stance 立场回写 + 事件 + 派系重算（生产图）。"""
        got_stance, got_fac = [], []
        self.bus.subscribe(ev.NPC_STANCE_CHANGED, lambda d: got_stance.append(d))
        self.bus.subscribe(ev.NPC_FACTION_UPDATED, lambda d: got_fac.append(d))
        self.bus.reset_pass()
        self.bus.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": self.graph.influential_with_network()})
        ex_titles = [t for t, rec in self.bridge._created.items()
                     if rec.get("nid") == self.nid_ex and "竞争预警" in t]
        self.assertTrue(ex_titles, self.bridge._created)
        before_stance = self.graph.nodes[self.nid_ex].stance
        self.bus.reset_pass()
        self.bus.publish(ev.TASKS_COMPLETED, {
            "person_id": PID, "task_id": "tp1", "title": ex_titles[0]})
        after_stance = self.graph.nodes[self.nid_ex].stance
        self.assertAlmostEqual(after_stance - before_stance, 0.15, places=9)
        self.assertTrue(got_stance)
        self.assertEqual(got_stance[-1]["npc_name"], "前任")
        self.assertTrue(got_fac)
        # 派系快照是 dict（生产形状），反对派 stance 已随成员上修减弱
        new_facs = got_fac[-1]["factions"]
        self.assertIsInstance(new_facs, dict)
        new_opp = next(f for f in new_facs.values() if f["label"] == "反对派")
        old_opp = next(f for f in self.graph.factions.values()
                       if f["label"] == "反对派")
        # 事件载荷里的反对派 stance 应高于初始 -0.533（立场减弱）
        self.assertGreater(new_opp["stance"], -0.533)
        self.assertTrue(self.bridge.network_summary()["last_stance_changes"])

    def test_05_familycontext_single_parent_path(self):
        """FamilyContext dataclass：单亲 → 边界感软任务（生产形状）。"""
        g = RelationshipGraph(center_id="me")
        nid = g.add_node("前任", "前任", 30)
        g.adjust_stance(nid, -0.7)
        g.set_family_dict(None, {"family_structure": "单亲",
                                 "parents_status": "未知"})
        net2 = build_bridge_network(
            affection=self.base.affection, task_engine=self.base.tasks,
            compass=self.base.compass, memory=self.base.memory,
            portrait=self.base.portrait, script_lib=script_library,
            graph=g, pet_sink=self.base.pet_sink)
        bus2 = net2["bus"]
        bus2.reset_pass()
        bus2.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": g.influential_with_network()})
        titles = [t["title"] for t in self.tasks.get_active_tasks(PID)]
        self.assertTrue(any("单亲家庭背景" in x for x in titles), titles)
        self.assertFalse(any("不卷入" in x for x in titles), titles)

    def test_06_unknown_family_not_fabricated(self):
        """生产图无家庭证据：get_target_family 全"未知"，不派生家庭软任务。"""
        g = RelationshipGraph(center_id="me")
        nid = g.add_node("前任", "前任", 30)
        g.adjust_stance(nid, -0.7)
        fam = g.get_target_family()
        self.assertEqual(fam.family_structure, "未知")
        self.assertEqual(fam.parents_status, "未知")
        net2 = build_bridge_network(
            affection=self.base.affection, task_engine=self.base.tasks,
            compass=self.base.compass, memory=self.base.memory,
            portrait=self.base.portrait, script_lib=script_library,
            graph=g, pet_sink=self.base.pet_sink)
        bus2 = net2["bus"]
        bus2.reset_pass()
        bus2.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": g.influential_with_network()})
        titles = [t["title"] for t in self.tasks.get_active_tasks(PID)]
        self.assertFalse(any("不卷入" in x or "单亲" in x for x in titles),
                         titles)


if __name__ == "__main__":
    unittest.main(verbosity=2)
