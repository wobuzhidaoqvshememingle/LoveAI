# -*- coding: utf-8 -*-
"""R3.3-79 活关系星图·NPC次级网络与家庭/伦理关系建模 单元测试。

覆盖：
  - add_npc_edge 建边带 stance
  - compute_factions 聚类出 反对派/助攻派 并识别意见领袖
  - FamilyContext 缺失标"未知"；set_family_dict 写入
  - mark_ethical 标记与 risk_hint/opportunity_hint 存在且不含价值判断
  - adjust_stance 钳制 [-1,1]
  - to_dict 全 JSON 可序列化且含新键
  - layout_graph 透传新字段
  - extract_from_chats 从含家庭线索的消息提取 siblings / parents_status
"""
import os, sys, json, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.relationship_graph import (  # noqa: E402
    RelationshipGraph, FamilyContext, EthicalMark,
    extract_from_chats, layout_graph, STANCE_MAP,
)


class TestR3379Family(unittest.TestCase):
    def test_family_default_unknown(self):
        f = FamilyContext()
        d = f.to_dict()
        self.assertEqual(d["family_structure"], "未知")
        self.assertEqual(d["parents_status"], "未知")
        self.assertEqual(d["siblings"], [])
        self.assertEqual(d["atmosphere"], "未知")
        self.assertEqual(d["evidence"], [])
        self.assertFalse(d["manual"])

    def test_set_family_dict_graph_level(self):
        g = RelationshipGraph("me")
        g.set_family_dict(None, {"siblings": ["姐姐"], "parents_status": "离婚中",
                                 "evidence": ["我姐总说我"]})
        fam = g.get_target_family()
        self.assertIn("姐姐", fam.siblings)
        self.assertEqual(fam.parents_status, "离婚中")
        # to_dict 里图级 family_context 落盘
        d = g.to_dict()
        self.assertEqual(d["family_context"]["parents_status"], "离婚中")

    def test_set_family_dict_node_level_and_priority(self):
        g = RelationshipGraph("me")
        nid = g.add_node("小丽", "朋友", 60)
        g.set_family_dict(nid, {"family_structure": "重组"})
        # 节点 family 非全未知 → get_target_family(nid) 返回节点 family
        fam = g.get_target_family(nid)
        self.assertEqual(fam.family_structure, "重组")
        # 图级仍全未知
        self.assertEqual(g.get_target_family().family_structure, "未知")

    def test_get_target_family_all_unknown(self):
        g = RelationshipGraph("me")
        fam = g.get_target_family()
        self.assertEqual(fam.parents_status, "未知")


class TestR3379NpcEdgesAndFactions(unittest.TestCase):
    def _build(self):
        g = RelationshipGraph("me")
        # center=me；目标对象
        target = g.add_node("小丽", "暧昧对象", 70)
        # 反对派网络：前任 + 前任父母（NPC↔NPC 边，stance 负）
        ex = g.add_node("前任", "前任", 50)
        ex_dad = g.add_node("前任爸", "其他", 30)
        ex_mom = g.add_node("前任妈", "其他", 30)
        g.adjust_stance(ex, -0.7)
        g.adjust_stance(ex_dad, -0.5)
        g.adjust_stance(ex_mom, -0.4)
        g.add_npc_edge(ex, ex_dad, 0.8, -0.6, "家人")
        g.add_npc_edge(ex, ex_mom, 0.7, -0.5, "家人")
        g.add_npc_edge(ex_dad, ex_mom, 0.6, -0.4, "家人")
        # 助攻派网络：闺蜜 + 我父母（NPC↔NPC 边，stance 正）
        bestie = g.add_node("闺蜜", "朋友", 80)
        my_dad = g.add_node("我爸", "家人", 60)
        my_mom = g.add_node("我妈", "家人", 60)
        g.adjust_stance(bestie, 0.6)
        g.adjust_stance(my_dad, 0.5)
        g.adjust_stance(my_mom, 0.4)
        g.add_npc_edge(bestie, my_dad, 0.7, 0.5, "朋友")
        g.add_npc_edge(bestie, my_mom, 0.7, 0.4, "朋友")
        # center→NPC 边（不参与派系聚类）
        g.add_edge("me", target, 0.9)
        g.add_edge("me", ex, 0.3)
        g.add_edge("me", bestie, 0.5)
        return g, ex, bestie

    def test_add_npc_edge_carries_stance(self):
        g = RelationshipGraph("me")
        a = g.add_node("A", "其他", 30)
        b = g.add_node("B", "其他", 30)
        g.add_npc_edge(a, b, 0.6, -0.5, "朋友")
        e = g.edges[-1]
        self.assertEqual((e.source_id, e.target_id), (a, b))
        self.assertAlmostEqual(e.stance, -0.5)
        self.assertEqual(e.relation_type, "朋友")

    def test_compute_factions_clusters_and_leaders(self):
        g, ex, bestie = self._build()
        factions = g.compute_factions()
        labels = {f["label"] for f in factions.values()}
        self.assertIn("反对派", labels)
        self.assertIn("助攻派", labels)
        opp = [f for f in factions.values() if f["label"] == "反对派"][0]
        sup = [f for f in factions.values() if f["label"] == "助攻派"][0]
        self.assertLess(opp["stance"], -0.2)
        self.assertGreater(sup["stance"], 0.2)
        self.assertEqual(opp["opinion_leader"], ex)      # |stance| 最大
        self.assertEqual(sup["opinion_leader"], bestie)
        self.assertGreaterEqual(opp["size"], 2)
        self.assertTrue(opp["risk_hint"])
        self.assertTrue(sup["opportunity_hint"])
        # 节点 faction 已回写
        self.assertEqual(g.nodes[ex].faction, opp["id"])
        self.assertEqual(g.nodes[bestie].faction, sup["id"])
        # get_factions 返回缓存
        self.assertEqual(g.get_factions(), factions)

    def test_isolated_npc_not_faction(self):
        g = RelationshipGraph("me")
        g.add_node("独行侠", "其他", 20)
        factions = g.compute_factions()
        self.assertEqual(factions, {})


class TestR3379EthicalAndStance(unittest.TestCase):
    def test_mark_ethical_neutral_hints(self):
        g = RelationshipGraph("me")
        nid = g.add_node("姐姐", "家人", 50)
        g.mark_ethical(nid, "sibling_affection", "兄弟姐妹好感",
                       risk_hint="边界需保持清晰", opportunity_hint="可争取其支持",
                       evidence=["你姐总夸你"])
        marks = g.nodes[nid].ethical_marks
        self.assertEqual(len(marks), 1)
        m = marks[0]
        self.assertEqual(m.flag, "sibling_affection")
        self.assertTrue(m.risk_hint)
        self.assertTrue(m.opportunity_hint)
        # 禁止价值判断措辞
        blob = (m.risk_hint + m.opportunity_hint + m.label)
        for bad in ["不对", "违背伦理", "不道德", "错误", "可耻"]:
            self.assertNotIn(bad, blob)

    def test_adjust_stance_clamps(self):
        g = RelationshipGraph("me")
        nid = g.add_node("X", "其他", 30)
        v = g.adjust_stance(nid, 0.7)
        self.assertAlmostEqual(v, 0.7)
        v = g.adjust_stance(nid, 0.6)   # 应钳到 1.0
        self.assertAlmostEqual(v, 1.0)
        v = g.adjust_stance(nid, -3.0)  # 应钳到 -1.0
        self.assertAlmostEqual(v, -1.0)
        # 节点不存在返回 0.0
        self.assertEqual(g.adjust_stance("nope", 0.5), 0.0)


class TestR3379SerializationAndLayout(unittest.TestCase):
    def test_to_dict_json_serializable_and_new_keys(self):
        g = RelationshipGraph("me")
        nid = g.add_node("小丽", "朋友", 60)
        g.mark_ethical(nid, "ex_network_active", "前任网络活跃",
                       risk_hint="前任仍有联络", opportunity_hint="可观察其动向")
        g.set_family_dict(nid, {"siblings": ["哥哥"]})
        g.compute_factions()  # 空派系也应安全
        d = g.to_dict()
        s = json.dumps(d, ensure_ascii=False)  # 不抛异常即通过
        self.assertIn("family_context", d)
        self.assertIn("factions", d)
        node = d["nodes"][0]
        for k in ("family", "ethical_marks", "faction", "faction_stance", "stance"):
            self.assertIn(k, node)
        self.assertIn("哥哥", node["family"]["siblings"])

    def test_layout_passes_through_new_fields(self):
        g = RelationshipGraph("me")
        a = g.add_node("A", "朋友", 60)
        b = g.add_node("B", "朋友", 40)
        g.add_edge("me", a, 0.8)
        g.add_npc_edge(a, b, 0.6, 0.3, "朋友")
        laid = layout_graph(g.to_dict(), iterations=10, seed=1)
        node = [n for n in laid["nodes"] if n["id"] == a][0]
        for k in ("family", "ethical_marks", "faction", "faction_stance", "stance"):
            self.assertIn(k, node)
        edge = laid["edges"][0]
        self.assertIn("stance", edge)
        # 旧键不丢
        for k in ("id", "name", "relation_type", "intimacy", "x", "y", "size", "color"):
            self.assertIn(k, node)
        for k in ("source", "target", "strength", "relation_type", "width"):
            self.assertIn(k, edge)


class TestR3379Extract(unittest.TestCase):
    def test_extract_family_clues_and_npc_edges(self):
        msgs = [
            {"content": "我妈昨天问起你"},
            {"content": "我爸在闹离婚"},
            {"content": "我姐总说我太挑"},
        ]
        r = extract_from_chats("me", msgs)
        fc = r["family_context"]
        self.assertIn("姐姐", fc["siblings"])
        self.assertNotEqual(fc["parents_status"], "未知")
        # 消息里确实出现了 NPC 称谓节点
        names = [n["name"] for n in r["nodes"]]
        self.assertTrue(any("我妈" in x or "我姐" in x for x in names))

    def test_extract_no_evidence_stays_unknown(self):
        r = extract_from_chats("me", [{"content": "今天天气不错"}])
        self.assertEqual(r["family_context"]["parents_status"], "未知")
        self.assertEqual(r["family_context"]["siblings"], [])

    def test_stance_map_defaults(self):
        # 家人/朋友/前任 等默认立场符合 STANCE_MAP
        self.assertAlmostEqual(STANCE_MAP["前任"], -0.6)
        self.assertAlmostEqual(STANCE_MAP["朋友"], 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
