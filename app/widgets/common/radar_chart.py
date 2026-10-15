"""
18维雷达图控件
- 绘制 5 层同心多边形网格 + 18 条轴线 + 数据多边形 + 维度标签
- 鼠标悬停显示维度名称和分数
"""

import math

from PySide6.QtWidgets import QWidget, QToolTip
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QPolygonF, QFont
from PySide6.QtCore import Qt, QPointF, QRectF


class RadarChart(QWidget):
    """18 维雷达图"""

    NUM_AXES = 18
    NUM_RINGS = 5  # 同心多边形层数

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels = [""] * self.NUM_AXES
        self._values = [0.0] * self.NUM_AXES
        self._grid_color = QColor("#3a3a5a")
        self._axis_color = QColor("#4a4a6a")
        self._fill_color = QColor(212, 175, 55, 80)   # 半透明金色
        self._border_color = QColor("#d4af37")
        self._label_color = QColor("#b0b0c0")

        self.setMinimumSize(280, 280)
        self.setMouseTracking(True)

    def set_data(self, labels: list, values: list):
        """设置雷达图数据
        :param labels: 维度名称列表，长度应为 18
        :param values: 分数列表，范围 0-100，长度应为 18
        """
        if len(labels) == self.NUM_AXES and len(values) == self.NUM_AXES:
            self._labels = list(labels)
            self._values = [max(0.0, min(100.0, float(v))) for v in values]
            self.update()
        else:
            # 长度不匹配时按数量自适应，最多取 NUM_AXES 个
            n = min(len(labels), len(values), self.NUM_AXES)
            self._labels = list(labels[:n]) + [""] * (self.NUM_AXES - n)
            self._values = [max(0.0, min(100.0, float(v))) for v in values[:n]]
            self._values += [0.0] * (self.NUM_AXES - n)
            self.update()

    # ---- 几何计算 ----
    def _center_radius(self):
        w = self.width()
        h = self.height()
        cx = w / 2.0
        cy = h / 2.0
        # 留出标签空间
        r = min(w, h) / 2.0 - 30.0
        return cx, cy, r

    def _axis_point(self, index: int, ratio: float):
        """计算第 index 条轴上 ratio(0~1) 处的点坐标"""
        cx, cy, r = self._center_radius()
        # 从正上方开始，顺时针
        angle = -math.pi / 2.0 + 2.0 * math.pi * index / self.NUM_AXES
        x = cx + r * ratio * math.cos(angle)
        y = cy + r * ratio * math.sin(angle)
        return QPointF(x, y)

    # ---- 绘制 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        cx, cy, r = self._center_radius()

        # --- 1. 同心多边形网格 ---
        grid_pen = QPen(self._grid_color, 1, Qt.DashLine)
        painter.setPen(grid_pen)
        for ring in range(1, self.NUM_RINGS + 1):
            ratio = ring / self.NUM_RINGS
            polygon = QPolygonF()
            for i in range(self.NUM_AXES):
                polygon.append(self._axis_point(i, ratio))
            polygon.append(polygon.at(0))  # 闭合
            painter.drawPolyline(polygon)

        # --- 2. 轴线 ---
        axis_pen = QPen(self._axis_color, 1)
        painter.setPen(axis_pen)
        for i in range(self.NUM_AXES):
            outer = self._axis_point(i, 1.0)
            painter.drawLine(QPointF(cx, cy), outer)

        # --- 3. 数据多边形 ---
        data_pen = QPen(self._border_color, 2)
        data_brush = QBrush(self._fill_color)
        painter.setPen(data_pen)
        painter.setBrush(data_brush)
        data_poly = QPolygonF()
        for i in range(self.NUM_AXES):
            ratio = self._values[i] / 100.0
            data_poly.append(self._axis_point(i, ratio))
        if self.NUM_AXES > 0:
            data_poly.append(data_poly.at(0))
            painter.drawPolygon(data_poly)

        # --- 4. 维度标签 ---
        painter.setPen(self._label_color)
        font = QFont("Microsoft YaHei", 8)
        painter.setFont(font)
        for i in range(self.NUM_AXES):
            if not self._labels[i]:
                continue
            # 标签放在轴外
            angle = -math.pi / 2.0 + 2.0 * math.pi * i / self.NUM_AXES
            lx = cx + (r + 18) * math.cos(angle)
            ly = cy + (r + 18) * math.sin(angle)
            # 根据角度调整对齐方式
            align = Qt.AlignCenter
            if math.cos(angle) > 0.3:
                align = Qt.AlignLeft | Qt.AlignVCenter
            elif math.cos(angle) < -0.3:
                align = Qt.AlignRight | Qt.AlignVCenter
            rect = QRectF(lx - 30, ly - 10, 60, 20)
            painter.drawText(rect, align, self._labels[i])

        painter.end()

    # ---- 鼠标悬停提示 ----
    def mouseMoveEvent(self, event):
        pos = event.pos()
        cx, cy, r = self._center_radius()
        # 计算鼠标所在角度，判断靠近哪条轴
        dx = pos.x() - cx
        dy = pos.y() - cy
        dist = math.hypot(dx, dy)
        if dist > r * 1.15:
            QToolTip.hideText()
            return
        # 角度换算到轴索引
        angle = math.atan2(dy, dx) + math.pi / 2.0  # 从顶部起算
        if angle < 0:
            angle += 2 * math.pi
        idx = int(round(angle / (2 * math.pi) * self.NUM_AXES)) % self.NUM_AXES
        label = self._labels[idx] if idx < len(self._labels) else ""
        value = self._values[idx] if idx < len(self._values) else 0
        if label:
            QToolTip.showText(
                event.globalPos(),
                f"{label}: {value:.0f}分",
                self,
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)
