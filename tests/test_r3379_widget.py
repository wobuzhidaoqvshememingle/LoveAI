# -*- coding: utf-8 -*-
"""R3.3-79 最小兼容测试：
- 已布局图透传新字段（family / ethical_marks / faction / faction_stance / edge.stance）；
- build_node_summary 含 派系/家庭/伦理 关键词，且不含价值判断词；
- 旧图数据（无新字段）set_graph 不抛异常、paintEvent 离屏截图不抛异常。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.widgets.common.relationship_graph_widget import (  # noqa: E402
    RelationshipGraphWidget, build_node_summary,
)


def _rich_layouted_graph():
    """构造已布局图：覆盖全部新字段。"""
    return {
        "center": "me",
        "nodes": [
            {"id": "me", "name": "我", "relation_type": "其他", "intimacy": 100,
             "x": 0.5, "y": 0.5},
            # 派系：stance=-0.5 -> 红环
            {"id": "n_opp", "name": "反对派成员", "relation_type": "朋友",
             "intimacy": 50, "x": 0.2, "y": 0.25,
             "faction": "opposition", "faction_label": "反对派",
             "faction_stance": -0.5},
            # 家庭：单亲 + 离婚中
            {"id": "n_fam", "name": "单亲家庭NPC", "relation_type": "朋友",
             "intimacy": 60, "x": 0.8, "y": 0.2,
             "family": {"family_structure": "单亲",
                        "parents_status": "离婚中",
                        "siblings": "未知"}},
            # 伦理标记
            {"id": "n_eth", "name": "伦理标记NPC", "relation_type": "暧昧对象",
             "intimacy": 70, "x": 0.25, "y": 0.8,
             "ethical_marks": [{"flag": "sibling_affection",
                                "label": "兄弟姐妹好感",
                                "risk_hint": "需留意亲属间情感张力",
                                "opportunity_hint": ""}]},
            # 派系：stance=0.5 -> 绿环
            {"id": "n_ally", "name": "盟友", "relation_type": "同事",
             "intimacy": 55, "x": 0.8, "y": 0.78,
             "faction": "ruling", "faction_label": "执政派",
             "faction_stance": 0.5},
        ],
        "edges": [
            # NPC↔NPC 带 stance
            {"source_id": "n_opp", "target_id": "n_ally", "strength": 0.6,
             "stance": -0.6},  # 敌对 -> 红
            {"source_id": "n_fam", "target_id": "n_eth", "strength": 0.5,
             "stance": 0.6},   # 友好 -> 绿
            {"source_id": "me", "target_id": "n_opp", "strength": 0.4,
             "stance": 0.0},   # 中性 -> 紫
            {"source_id": "me", "target_id": "n_fam", "strength": 0.7},  # 无 stance
        ],
    }


class TestR3379Widget(unittest.TestCase):
    def setUp(self):
        self.w = RelationshipGraphWidget()
        self.w.resize(400, 300)

    # ------------------------------------------------------------------ #
    def test_set_graph_preserves_new_fields(self):
        """set_graph 后 _graph.nodes/edges 保留全部新字段。"""
        self.w.set_graph(_rich_layouted_graph())
        nodes = {n["id"]: n for n in self.w._graph["nodes"]}

        # 派系
        self.assertEqual(nodes["n_opp"]["faction"], "opposition")
        self.assertEqual(nodes["n_opp"]["faction_label"], "反对派")
        self.assertEqual(nodes["n_opp"]["faction_stance"], -0.5)
        # 家庭
        self.assertEqual(nodes["n_fam"]["family"]["family_structure"], "单亲")
        self.assertEqual(nodes["n_fam"]["family"]["parents_status"], "离婚中")
        # 伦理
        self.assertEqual(nodes["n_eth"]["ethical_marks"][0]["label"],
                         "兄弟姐妹好感")

        # 边 stance 保留
        edge_stances = {
            (e["source_id"], e["target_id"]): e.get("stance")
            for e in self.w._graph["edges"]
        }
        self.assertEqual(edge_stances[("n_opp", "n_ally")], -0.6)
        self.assertEqual(edge_stances[("n_fam", "n_eth")], 0.6)
        self.assertNotIn("stance",
                         [e for e in self.w._graph["edges"]
                          if e["source_id"] == "me"
                          and e["target_id"] == "n_fam"][0])

    def test_paint_offscreen_no_raise(self):
        """新字段图 -> 离屏截图（全分支：派系环/家庭字/伦理点/边着色）不抛异常。"""
        self.w.set_graph(_rich_layouted_graph())
        pm = self.w.grab()
        self.assertFalse(pm.isNull())

    # ------------------------------------------------------------------ #
    def test_build_node_summary_contains_keywords(self):
        node = {
            "id": "n_fam", "name": "测试NPC", "relation_type": "朋友",
            "intimacy": 50,
            "faction": "opposition", "faction_label": "反对派",
            "family": {"family_structure": "单亲", "parents_status": "离婚中",
                       "siblings": "未知"},
            "ethical_marks": [{"flag": "sibling_affection",
                               "label": "兄弟姐妹好感",
                               "risk_hint": "需留意亲属间情感张力",
                               "opportunity_hint": ""}],
        }
        s = build_node_summary(node)
        self.assertIn("派系", s)
        self.assertIn("反对派", s)
        self.assertIn("家庭", s)
        self.assertIn("单亲", s)
        self.assertIn("离婚中", s)
        self.assertIn("伦理标记", s)
        self.assertIn("兄弟姐妹好感", s)
        # 前端不新增价值判断文案
        for bad in ("不对", "违背伦理", "不道德", "有罪", "罪恶"):
            self.assertNotIn(bad, s)

    def test_build_node_summary_old_node_no_extra(self):
        """旧节点（无新字段）摘要只含基础三项。"""
        s = build_node_summary({"id": "x", "name": "老王",
                                "relation_type": "朋友", "intimacy": 40})
        self.assertNotIn("派系", s)
        self.assertNotIn("家庭", s)
        self.assertNotIn("伦理", s)
        self.assertIn("老王", s)

    def test_build_node_summary_family_all_unknown(self):
        """family 全"未知" -> 不输出家庭行。"""
        s = build_node_summary({"id": "x", "name": "老王",
                                "relation_type": "朋友", "intimacy": 40,
                                "family": {"family_structure": "未知",
                                           "parents_status": "未知"}})
        self.assertNotIn("家庭", s)

    # ------------------------------------------------------------------ #
    def test_old_graph_compat_no_raise(self):
        """旧图数据（无任何新字段）set_graph + paint 不抛异常。"""
        old = {
            "center": "me",
            "nodes": [
                {"id": "me", "name": "我", "relation_type": "其他",
                 "intimacy": 100, "x": 0.5, "y": 0.5},
                {"id": "n1", "name": "我妈", "relation_type": "家人",
                 "intimacy": 80, "x": 0.2, "y": 0.25},
            ],
            "edges": [{"source_id": "me", "target_id": "n1", "strength": 0.9}],
        }
        self.w.set_graph(old)
        pm = self.w.grab()
        self.assertFalse(pm.isNull())


if __name__ == "__main__":
    unittest.main()
