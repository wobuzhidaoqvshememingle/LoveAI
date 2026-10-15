# -*- coding: utf-8 -*-
"""
R3.3-26 NPC 关系网络图控件（UI层，可 import PySide6）。

- 调 services.relationship_graph.layout_graph（Fruchterman-Reingold）拿节点坐标；
- QPainter 自绘：节点圆（关系类型着色）+ 边（强度定粗细/透明度）+ 标签 + 中心「我」节点；
- 节点坐标 [0,1] -> widget 尺寸映射（留 padding）；
- 点击节点弹出该 NPC 档案摘要（名字/关系类型/亲密度）；
- 静态图、不跑定时器（零空闲 CPU）；offscreen 可安全实例化/截图。
"""
from __future__ import annotations

import math

try:
    from PySide6.QtWidgets import QWidget, QMessageBox
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont
    _HAS_QT = True
except Exception:  # offscreen 兜底
    _HAS_QT = False
    QWidget = object

_DEFAULT_COLORS = {"朋友": "#3b82f6", "家人": "#10b981", "同事": "#6b7280",
                   "前任": "#ef4444", "暧昧对象": "#ec4899", "其他": "#a855f7"}

_PADDING = 40  # 四周留白（像素），给标签留位置


def _family_short_text(family) -> str:
    """从 family dict 里取非"未知"的字段值，用 ' · ' 拼接成一行短标签。
    都为"未知"/空 -> 返回 ""（不画标签）。"""
    if not isinstance(family, dict):
        return ""
    parts = []
    for v in family.values():
        if v in (None, "", "未知"):
            continue
        parts.append(str(v))
    return " · ".join(parts)


def build_node_summary(node: dict) -> str:
    """构造节点点击摘要文本（纯函数，offscreen 可测）。

    中性透传数据层字段：派系 / 家庭 / 伦理标记；前端不新增任何价值判断文案。
    """
    if not isinstance(node, dict):
        return ""
    lines = [
        f"姓名：{node.get('name', node.get('id', '—'))}",
        f"关系类型：{node.get('relation_type', '—')}",
        f"亲密度：{node.get('intimacy', '—')}",
    ]
    # 派系
    faction = node.get("faction")
    faction_label = node.get("faction_label")
    if faction or faction_label:
        if faction_label and faction and faction_label != faction:
            lines.append(f"派系：{faction_label}（{faction}）")
        else:
            lines.append(f"派系：{faction_label or faction}")
    # 家庭
    fam_text = _family_short_text(node.get("family"))
    if fam_text:
        lines.append(f"家庭：{fam_text}")
    # 伦理标记（直接展示数据层给的中性提示文案）
    marks = node.get("ethical_marks") or []
    if isinstance(marks, list):
        for m in marks:
            if not isinstance(m, dict):
                continue
            label = m.get("label") or m.get("flag") or ""
            risk = m.get("risk_hint") or ""
            if label and risk:
                lines.append(f"伦理标记：{label}（{risk}）")
            elif label:
                lines.append(f"伦理标记：{label}")
            elif risk:
                lines.append(f"伦理标记：{risk}")
    return "\n".join(lines)


class RelationshipGraphWidget(QWidget if _HAS_QT else object):
    """中心「我」+ 周围 NPC 节点的关系网络图。"""

    def __init__(self, parent=None, graph: dict | None = None):
        if _HAS_QT:
            super().__init__(parent)
        self._graph: dict = {"center": "", "nodes": [], "edges": [], "colors": dict(_DEFAULT_COLORS)}
        self._node_px: dict[str, tuple[float, float, float]] = {}  # id -> (px, py, radius)
        self._zoom = 1.0  # 兼容旧 API：zoom(factor) 缩放节点视觉尺寸
        # 节点点击回调：fn(node_dict)；默认弹 QMessageBox。测试可替换为断言桩。
        self.on_node_clicked = None
        self.set_graph(graph or {"nodes": [], "edges": []})

    def zoom(self, factor: float) -> None:
        """兼容旧 API：节点视觉缩放，夹在 0.5-2.0。"""
        self._zoom = max(0.5, min(2.0, self._zoom * float(factor)))
        if _HAS_QT:
            self.update()

    # ------------------------------------------------------------------ #
    def set_graph(self, graph: dict) -> None:
        """接受两种形态：
        A) 已布局：nodes 自带 x,y ∈ [0,1]；edges 用 source_id/target_id；
        B) 未布局：nodes 只有 id/name/relation_type/intimacy —— 内部调 layout_graph。
        """
        graph = graph if isinstance(graph, dict) else {}
        nodes = list(graph.get("nodes", []) or [])
        edges = list(graph.get("edges", []) or [])
        needs_layout = nodes and all(("x" not in n) for n in nodes)

        if needs_layout:
            try:
                from services.relationship_graph import layout_graph
                laid = layout_graph(
                    {"center": graph.get("center", ""), "nodes": nodes,
                     "edges": edges},
                    iterations=120, seed=42, dims=2)
                nodes = []
                for n in laid.get("nodes", []):
                    nodes.append({
                        **n,  # 透传 family / ethical_marks / faction / faction_stance 等新字段
                        "id": n["id"],
                        "name": n.get("name", n["id"]),
                        "relation_type": n.get("relation_type", "其他"),
                        "intimacy": n.get("intimacy", 30),
                        "x": n.get("x", 0.0) * 0.5 + 0.5,
                        "y": n.get("y", 0.0) * 0.5 + 0.5})
                edges = [{**e,  # 透传 stance 等新字段
                          "source_id": e.get("source"),
                          "target_id": e.get("target"),
                          "strength": e.get("strength", 0.5)}
                         for e in laid.get("edges", [])]
            except Exception:
                # layout 不可用：均匀撒圆，保证能画
                cx = cy = 0.5
                for i, n in enumerate(nodes):
                    if n.get("id") == graph.get("center"):
                        n.update(x=cx, y=cy)
                    else:
                        ang = 2 * math.pi * i / max(1, len(nodes))
                        n.update(x=0.5 + 0.4 * math.cos(ang),
                                 y=0.5 + 0.4 * math.sin(ang))

        self._graph = {
            "center": graph.get("center", ""),
            "colors": {**_DEFAULT_COLORS, **(graph.get("colors") or {})},
            "nodes": nodes,
            "edges": edges,
        }
        if _HAS_QT:
            self.update()

    # ------------------------------------------------------------------ #
    def _to_px(self, nx: float, ny: float) -> tuple[float, float]:
        """单位坐标 [0,1] -> widget 像素坐标（留 padding）。"""
        w, h = max(self.width(), 1), max(self.height(), 1)
        x = _PADDING + max(0.0, min(1.0, float(nx))) * (w - 2 * _PADDING)
        y = _PADDING + max(0.0, min(1.0, float(ny))) * (h - 2 * _PADDING)
        return x, y

    if _HAS_QT:
        def paintEvent(self, event):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            # 底
            p.fillRect(self.rect(), QColor(15, 15, 26, 230))

            nodes = self._graph.get("nodes", [])
            edges = self._graph.get("edges", [])
            colors = self._graph.get("colors", {})
            center_id = self._graph.get("center", "")
            by_id = {n.get("id"): n for n in nodes}

            # 边：强度 -> 线宽 + 透明度
            for e in edges:
                s = by_id.get(e.get("source_id") or e.get("source"))
                t = by_id.get(e.get("target_id") or e.get("target"))
                if not s or not t:
                    continue
                strength = float(e.get("strength", 0.5) or 0.5)
                sx, sy = self._to_px(s.get("x", 0.5), s.get("y", 0.5))
                tx, ty = self._to_px(t.get("x", 0.5), t.get("y", 0.5))
                alpha = int(60 + 140 * max(0.0, min(1.0, strength)))
                # R3.3-79: stance 着色（无 stance 键 -> 维持原紫色）
                base_color = QColor(168, 85, 247)
                if "stance" in e:
                    try:
                        sv = float(e.get("stance"))
                    except (TypeError, ValueError):
                        sv = None
                    if sv is not None and sv < -0.2:
                        base_color = QColor(239, 68, 68)
                    elif sv is not None and sv > 0.2:
                        base_color = QColor(16, 185, 129)
                base_color.setAlpha(alpha)
                pen = QPen(base_color, max(1.0, 0.5 + strength * 2.5))
                p.setPen(pen)
                p.drawLine(QPointF(sx, sy), QPointF(tx, ty))

            # 节点
            self._node_px = {}
            for n in nodes:
                nid = n.get("id")
                is_center = (nid == center_id)
                cx, cy = self._to_px(n.get("x", 0.5), n.get("y", 0.5))
                intimacy = float(n.get("intimacy", 30) or 30)
                radius = (6 + intimacy / 100.0 * 10) if not is_center else 12
                radius *= self._zoom
                col = QColor(colors.get(n.get("relation_type", "其他"), "#a855f7"))
                p.setBrush(QBrush(col))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(cx, cy), radius, radius)
                # 中心「我」加金色描边
                if is_center:
                    p.setPen(QPen(QColor("#d4af37"), 2))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(QPointF(cx, cy), radius + 3, radius + 3)
                # R3.3-79: 派系环（不改变节点本体颜色）
                if n.get("faction") and n.get("faction_stance") is not None:
                    try:
                        fs = float(n.get("faction_stance"))
                    except (TypeError, ValueError):
                        fs = None
                    if fs is not None:
                        ring = None
                        if fs <= -0.2:
                            ring = QColor(239, 68, 68)
                        elif fs >= 0.2:
                            ring = QColor(16, 185, 129)
                        if ring is not None:
                            p.setPen(QPen(ring, 2))
                            p.setBrush(Qt.NoBrush)
                            p.drawEllipse(QPointF(cx, cy), radius + 5, radius + 5)
                # R3.3-79: 伦理标记琥珀色小圆点（节点右上）
                if n.get("ethical_marks"):
                    p.setBrush(QBrush(QColor(245, 158, 11)))
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(QPointF(cx + radius * 0.7, cy - radius * 0.7),
                                  2.5, 2.5)
                self._node_px[nid] = (cx, cy, radius)
                # 标签
                p.setPen(QPen(QColor(220, 220, 230), 1))
                f = QFont(); f.setPointSize(9)
                if is_center:
                    f.setBold(True)
                p.setFont(f)
                name = n.get("name", nid)
                p.drawText(int(cx - 30), int(cy + radius + 14), 60, 16,
                           Qt.AlignCenter, str(name))
                # R3.3-79: 家庭背景小字标签（名字下方，7pt 灰）
                fam_text = _family_short_text(n.get("family"))
                if fam_text:
                    sf = QFont(); sf.setPointSize(7)
                    p.setFont(sf)
                    p.setPen(QPen(QColor(150, 150, 160), 1))
                    p.drawText(int(cx - 40), int(cy + radius + 30), 80, 12,
                               Qt.AlignCenter, fam_text)
            p.end()

        def mousePressEvent(self, event):
            """点击节点 -> 弹 NPC 档案摘要（可被 on_node_clicked 替换）。"""
            pos = event.position()
            mx, my = pos.x(), pos.y()
            hit = None
            for n in self._graph.get("nodes", []):
                px, py, r = self._node_px.get(n.get("id"), (0, 0, 0))
                if math.hypot(mx - px, my - py) <= max(r + 8, 14):
                    hit = n
                    break
            if hit is None:
                return super().mousePressEvent(event)
            handler = getattr(self, "on_node_clicked", None)
            if callable(handler):
                try:
                    handler(hit)
                    return
                except Exception:
                    pass
            # 默认行为：弹档案摘要（含派系/家庭/伦理标记，中性透传）
            try:
                QMessageBox.information(self, "NPC 档案", build_node_summary(hit))
            except Exception:
                pass
