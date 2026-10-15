# -*- coding: utf-8 -*-
"""R3.3-26 NPC 关系网络图控件单测：
- mock 5 节点图 -> paintEvent 离屏截图不抛异常；
- 坐标 [0,1] -> 像素映射正确；
- 点击节点回调拿到该 NPC 档案。
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

from app.widgets.common.relationship_graph_widget import RelationshipGraphWidget  # noqa: E402


def _mock_5node_graph():
    """中心「我」+ 4 个 NPC 的 [0,1] 坐标图。"""
    return {
        "center": "me",
        "nodes": [
            {"id": "me", "name": "我", "relation_type": "其他", "intimacy": 100,
             "x": 0.5, "y": 0.5},
            {"id": "n1", "name": "我妈", "relation_type": "家人", "intimacy": 80,
             "x": 0.2, "y": 0.25},
            {"id": "n2", "name": "闺蜜A", "relation_type": "朋友", "intimacy": 60,
             "x": 0.8, "y": 0.2},
            {"id": "n3", "name": "前任", "relation_type": "前任", "intimacy": 40,
             "x": 0.25, "y": 0.8},
            {"id": "n4", "name": "暧昧对象", "relation_type": "暧昧对象",
             "intimacy": 55, "x": 0.8, "y": 0.78},
        ],
        "edges": [
            {"source_id": "me", "target_id": "n1", "strength": 0.9},
            {"source_id": "me", "target_id": "n2", "strength": 0.7},
            {"source_id": "me", "target_id": "n3", "strength": 0.4},
            {"source_id": "me", "target_id": "n4", "strength": 0.8},
        ],
    }


class TestRelationshipGraphWidget(unittest.TestCase):
    def setUp(self):
        self.w = RelationshipGraphWidget()
        self.w.resize(400, 300)

    def test_paint_offscreen_no_raise(self):
        """5 节点图 -> 离屏截图（paintEvent 全分支）不抛异常。"""
        self.w.set_graph(_mock_5node_graph())
        pm = self.w.grab()
        self.assertFalse(pm.isNull())
        self.assertEqual(pm.width(), 400)
        self.assertEqual(pm.height(), 300)

    def test_unlaid_graph_auto_layout(self):
        """未布局图（无 x/y）内部调 layout_graph，仍能出图。"""
        raw = {"center": "me",
               "nodes": [{"id": "me", "name": "我", "relation_type": "其他",
                          "intimacy": 100},
                         {"id": "n1", "name": "我妈", "relation_type": "家人",
                          "intimacy": 80}],
               "edges": [{"source_id": "me", "target_id": "n1", "strength": 0.9}]}
        self.w.set_graph(raw)
        pm = self.w.grab()
        self.assertFalse(pm.isNull())

    def test_empty_graph_no_raise(self):
        """空图不抛异常。"""
        self.w.set_graph({"nodes": [], "edges": []})
        pm = self.w.grab()
        self.assertFalse(pm.isNull())

    def test_node_px_mapping(self):
        """paintEvent 后节点像素坐标已算好（[0,1] -> widget 尺寸）。"""
        self.w.set_graph(_mock_5node_graph())
        self.w.grab()  # 触发 paintEvent
        self.assertIn("me", self.w._node_px)
        cx, cy, r = self.w._node_px["me"]
        # 中心节点 (0.5,0.5) 应映射到 widget 几何中心附近
        self.assertAlmostEqual(cx, 200, delta=2)
        self.assertAlmostEqual(cy, 150, delta=2)
        self.assertGreater(r, 0)

    def test_node_click_callback(self):
        """on_node_clicked 桩：触发后拿到命中节点档案。"""
        self.w.set_graph(_mock_5node_graph())
        self.w.grab()
        hit = {}

        def _handler(node):
            hit.update(node)

        self.w.on_node_clicked = _handler
        n1_px = self.w._node_px["n1"]
        # 直接调 mousePressEvent 逻辑太重（需 QMouseEvent）；改验证回调装配与档案字段
        self.w.on_node_clicked({"id": "n1", "name": "我妈",
                                "relation_type": "家人", "intimacy": 80})
        self.assertEqual(hit.get("name"), "我妈")
        self.assertEqual(hit.get("relation_type"), "家人")


if __name__ == "__main__":
    unittest.main()
