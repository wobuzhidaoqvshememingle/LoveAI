# -*- coding: utf-8 -*-
"""关系网络图服务层测试。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.relationship_graph import (RelationshipGraph, extract_from_chats,
    REL_COLORS, get_influential_people, social_alert)


class TestRelationshipGraph(unittest.TestCase):
    def test_crud(self):
        g = RelationshipGraph("me")
        nid = g.add_node("小李","朋友",60)
        g.add_edge("me", nid, 0.8)
        self.assertEqual(len(g.nodes),1)
        g.set_relation_type(nid,"暧昧对象")
        self.assertEqual(g.nodes[nid].relation_type,"暧昧对象")
        g.delete_node(nid)
        self.assertEqual(len(g.nodes),0)

    def test_extract_appellation(self):
        msgs = [{"content":"我妈昨天问起你"},{"content":"我朋友也这么说"}]
        r = extract_from_chats("me", msgs)
        names = [n["name"] for n in r["nodes"]]
        self.assertTrue(any("我妈" in n or "我朋友" in n for n in names))

    def test_colors_complete(self):
        for k in ("朋友","家人","同事","前任","暧昧对象","其他"):
            self.assertIn(k, REL_COLORS)

    def test_widget_import_offscreen(self):
        # 实例化带 QTimer 的 QWidget 前必须存在 offscreen QApplication
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        self.assertIsNotNone(app)
        from app.widgets.common.relationship_graph_widget import RelationshipGraphWidget
        w = RelationshipGraphWidget(graph={"nodes": [], "edges": []})
        w.zoom(1.5)

    def test_influential(self):
        graph = {"nodes":[{"name":"小丽","relation_type":"朋友","intimacy":60},
                          {"name":"前任","relation_type":"前任","intimacy":40}]}
        inf = get_influential_people("me", graph)
        self.assertTrue(any(i["relation_type"]=="前任" for i in inf))
        self.assertTrue(any("先搞定闺蜜" in i["influence_on_strategy"] for i in inf))

    def test_social_alert_red(self):
        inf = [{"name":"前任","relation_type":"前任"}]
        self.assertEqual(social_alert(inf)["level"], "红")
        self.assertEqual(social_alert([])["level"], "无")


if __name__ == "__main__":
    unittest.main(verbosity=2)
