"""
关系时间线控件（需求 23.8）
- 从 ServiceFacade.get_affection_history 拉取真实好感度每日快照，按时间绘制折线/节点
- 相邻快照总分 |Δ| >= 5 时，自动标注「关系阶段变化」（绿色▲上升 / 红色▼下降）
- QPainter 原生自绘：坐标轴、网格、节点、面积填充、悬浮提示
- 深色 indigo / 烫金主题，与 AffectionRing / RadarChart 风格一致
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QPainterPath, QPolygonF, QFontMetrics,
)
from PySide6.QtWidgets import QWidget, QToolTip

logger = logging.getLogger(__name__)

# 好感度 10 个区间（与 services.affection_engine.AffectionEngine.INTERVALS 对齐）
STAGE_NAMES = [
    "陌生人", "认识", "有点印象", "不反感", "普通朋友",
    "有好感", "暧昧中", "热恋", "深度绑定", "灵魂伴侣",
]

# 触发「关系阶段变化」标注的最小分差
STAGE_DELTA_THRESHOLD = 5.0


def _stage_name(score: float) -> str:
    """按总分映射关系阶段名（0-100 分，每 10 分一档）。"""
    idx = int(max(0.0, min(100.0, float(score))) // 10)
    if idx >= len(STAGE_NAMES):
        idx = len(STAGE_NAMES) - 1
    return STAGE_NAMES[idx]


class TimelineWidget(QWidget):
    """好感度快照曲线 / 关系阶段时间线。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[dict] = []          # [{date, score, delta, stage}]
        self._node_positions: list[QPointF] = []
        self._hover_index: int = -1

        # 配色（与好感度环一致的金色曲线 + indigo 网格）
        self._grid_color = QColor("#3a3a5a")
        self._axis_text = QColor("#a1a1aa")
        self._line_start = QColor("#d4af37")
        self._line_end = QColor("#f4d03f")
        self._fill = QColor(212, 175, 55, 46)
        self._node_color = QColor("#f4d03f")
        self._up_color = QColor("#34d399")
        self._down_color = QColor("#ef5350")

        self.setMinimumHeight(250)
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent;")

    # ------------------------------------------------------------------ #
    # 数据接口
    # ------------------------------------------------------------------ #
    def set_snapshots(self, snapshots: list[dict]) -> None:
        """填充快照数据。

        :param snapshots: 每项至少含 date(str) 与 total_score(float)；
                          可选 scores(dict)。按日期升序，自动计算相邻 Δ 与阶段。
        """
        pts: list[dict] = []
        prev_score: float | None = None
        for raw in (snapshots or []):
            try:
                score = float(raw.get("total_score", raw.get("score", 50.0)))
            except (ValueError, TypeError):
                score = 50.0
            score = max(0.0, min(100.0, score))
            date = str(raw.get("date", ""))[:10]
            delta = 0.0 if prev_score is None else round(score - prev_score, 1)
            pts.append({
                "date": date,
                "score": score,
                "delta": delta,
                "stage": _stage_name(score),
            })
            prev_score = score
        self._points = pts
        self._hover_index = -1
        self.update()

    def load_from_facade(self, facade, person_id: str, days: int = 30) -> bool:
        """从 ServiceFacade 拉好感度历史并渲染；成功返回 True，任何异常空态兜底。"""
        try:
            if facade is None or not person_id:
                self.set_snapshots([])
                return False
            resp = facade.get_affection_history(person_id, days)
            if not resp.get("ok"):
                self.set_snapshots([])
                return False
            self.set_snapshots(resp.get("data") or [])
            return bool(self._points)
        except Exception as e:  # noqa: BLE001
            logger.debug("时间线加载失败（空态兜底）: %s", e)
            self.set_snapshots([])
            return False

    def is_empty(self) -> bool:
        return not self._points

    # ------------------------------------------------------------------ #
    # 几何
    # ------------------------------------------------------------------ #
    def _plot_rect(self) -> QRectF:
        m_left, m_right, m_top, m_bottom = 42.0, 16.0, 30.0, 28.0
        return QRectF(m_left, m_top,
                      max(10.0, self.width() - m_left - m_right),
                      max(10.0, self.height() - m_top - m_bottom))

    def _x_for_index(self, i: int, rect: QRectF) -> float:
        n = len(self._points)
        if n == 1:
            return rect.left() + rect.width() / 2.0
        return rect.left() + rect.width() * i / (n - 1)

    def _y_for_score(self, score: float, rect: QRectF) -> float:
        return rect.bottom() - rect.height() * max(0.0, min(100.0, score)) / 100.0

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):  # noqa: N802 - Qt 虚函数
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        rect = self._plot_rect()

        # 标题 / 图例
        p.setPen(QColor("#e5e5e8"))
        p.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        p.drawText(QRectF(rect.left(), 4, rect.width(), 18),
                   Qt.AlignLeft | Qt.AlignVCenter, "好感度曲线 · 关系阶段")
        p.setFont(QFont("Microsoft YaHei", 8))
        p.setPen(self._up_color)
        p.drawText(QRectF(rect.left(), 4, rect.width(), 18),
                   Qt.AlignRight | Qt.AlignVCenter, "▲/▼ 阶段变化(|Δ|≥5)")

        # 空态
        if not self._points:
            p.setPen(QColor("#71717a"))
            p.setFont(QFont("Microsoft YaHei", 10))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "暂无好感度快照\n完成几轮聊天分析后将自动生成每日曲线")
            p.end()
            return

        self._draw_grid(p, rect)
        self._draw_curve(p, rect)
        self._draw_stage_markers(p, rect)
        p.end()

    def _draw_grid(self, p: QPainter, rect: QRectF) -> None:
        # 横向网格 + Y 轴刻度（0/25/50/75/100）
        pen = QPen(self._grid_color, 1, Qt.DashLine)
        p.setPen(pen)
        p.setFont(QFont("Microsoft YaHei", 8))
        for mark in (0, 25, 50, 75, 100):
            y = self._y_for_score(mark, rect)
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(self._axis_text)
            p.drawText(QRectF(0, y - 9, rect.left() - 6, 18),
                       Qt.AlignRight | Qt.AlignVCenter, str(mark))
            p.setPen(pen)
        # 坐标轴线
        axis_pen = QPen(QColor("#4a4a6a"), 1)
        p.setPen(axis_pen)
        p.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.left(), rect.bottom()))
        p.drawLine(QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.bottom()))

        # X 轴日期（稀疏标注，最多约 7 个，避免拥挤）
        n = len(self._points)
        step = max(1, (n + 6) // 7)
        p.setPen(self._axis_text)
        p.setFont(QFont("Microsoft YaHei", 8))
        for i in range(0, n, step):
            x = self._x_for_index(i, rect)
            mmdd = self._points[i]["date"][5:] if len(self._points[i]["date"]) >= 10 else ""
            p.drawText(QRectF(x - 44, rect.bottom() + 6, 88, 18),
                       Qt.AlignCenter, mmdd)
        # 末点日期始终标注
        if n and (n - 1) % step != 0:
            x = self._x_for_index(n - 1, rect)
            mmdd = self._points[-1]["date"][5:] if len(self._points[-1]["date"]) >= 10 else ""
            p.drawText(QRectF(x - 44, rect.bottom() + 6, 88, 18),
                       Qt.AlignCenter, mmdd)

    def _draw_curve(self, p: QPainter, rect: QRectF) -> None:
        n = len(self._points)
        self._node_positions = []
        pts: list[QPointF] = []
        for i, d in enumerate(self._points):
            pt = QPointF(self._x_for_index(i, rect), self._y_for_score(d["score"], rect))
            pts.append(pt)
            self._node_positions.append(pt)

        # 面积填充
        fill_poly = QPolygonF(pts)
        fill_poly.append(QPointF(rect.right(), rect.bottom()))
        fill_poly.append(QPointF(rect.left(), rect.bottom()))
        p.setBrush(QBrush(self._fill))
        p.setPen(Qt.NoPen)
        p.drawPolygon(fill_poly)

        # 折线（金色）
        path = QPainterPath(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        line_pen = QPen(self._line_end, 2)
        line_pen.setJoinStyle(Qt.RoundJoin)
        line_pen.setCapStyle(Qt.RoundCap)
        p.setPen(line_pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

        # 节点
        for i, pt in enumerate(pts):
            is_hover = (i == self._hover_index)
            r = 4.5 if is_hover else 3.2
            p.setBrush(QBrush(self._node_color))
            p.setPen(QPen(QColor("#1f1f22"), 1.4))
            p.drawEllipse(pt, r, r)

    def _draw_stage_markers(self, p: QPainter, rect: QRectF) -> None:
        """|Δ|>=5 的点画阶段变化标记 + 文字标注；密集时按层级上下错开避免重叠。"""
        fm = QFontMetrics(QFont("Microsoft YaHei", 8))
        th = 18
        # 先收集标注点，并为同一方向（上/下）的连续标注轮换 3 个高度层级
        markers = [i for i, d in enumerate(self._points)
                   if i > 0 and abs(d["delta"]) >= STAGE_DELTA_THRESHOLD]
        side_counter = {"up": 0, "down": 0}
        for i in markers:
            d = self._points[i]
            pt = self._node_positions[i]
            up = d["delta"] > 0
            side = "up" if up else "down"
            level = side_counter[side] % 3
            side_counter[side] += 1
            color = self._up_color if up else self._down_color
            arrow = "▲" if up else "▼"
            text = f"{arrow}{abs(d['delta']):.0f} {d['stage']}"
            tw = fm.horizontalAdvance(text) + 12
            x = pt.x() - tw / 2
            extra = level * 20
            if up:
                y = pt.y() - th - 8 - extra
                if y < rect.top() - 2:           # 顶部放不下则落到节点下方
                    y = pt.y() + 8
            else:
                y = pt.y() + 8 + extra
                if y + th > rect.bottom() + 2:   # 底部放不下则抬到节点上方
                    y = pt.y() - th - 8
            tag_rect = QRectF(x, y, tw, th)
            p.setBrush(QBrush(QColor(31, 31, 34, 232)))
            p.setPen(QPen(color, 1.2))
            p.drawRoundedRect(tag_rect, 6, 6)
            p.setPen(color)
            p.setFont(QFont("Microsoft YaHei", 8, QFont.Bold))
            p.drawText(tag_rect, Qt.AlignCenter, text)
            # 连接线（虚线）从标注贴近节点的一边连到节点
            line_y = (y + th) if (y + th) < pt.y() else y
            p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 150),
                          1, Qt.DotLine))
            p.drawLine(QPointF(pt.x(), line_y), QPointF(pt.x(), pt.y()))

    # ------------------------------------------------------------------ #
    # 悬浮提示
    # ------------------------------------------------------------------ #
    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._node_positions:
            super().mouseMoveEvent(event)
            return
        nearest, best = -1, 1e9
        for i, pt in enumerate(self._node_positions):
            dx = event.position().x() - pt.x()
            dy = event.position().y() - pt.y()
            dist = dx * dx + dy * dy
            if dist < best:
                nearest, best = i, dist
        if best <= 18 * 18:
            if nearest != self._hover_index:
                self._hover_index = nearest
                self.update()
            d = self._points[nearest]
            delta_txt = ""
            if nearest > 0:
                dv = d["delta"]
                if abs(dv) >= STAGE_DELTA_THRESHOLD:
                    delta_txt = f"<br><b style='color:{'#34d399' if dv > 0 else '#ef5350'}'>阶段变化 {'↑' if dv > 0 else '↓'}{abs(dv):.0f}</b>"
                elif dv != 0:
                    delta_txt = f"<br>较前日 {dv:+.1f}"
            QToolTip.showText(
                event.globalPosition().toPoint(),
                f"<div style='color:#e5e5e8'>"
                f"<b>{d['date']}</b> · {d['stage']}<br>"
                f"好感度：<b style='color:#f4d03f'>{d['score']:.0f}</b>"
                f"{delta_txt}</div>",
                self,
            )
        else:
            if self._hover_index != -1:
                self._hover_index = -1
                self.update()
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover_index = -1
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)
