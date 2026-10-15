"""
好感度环控件
- 绘制背景圆环 + 金色渐变进度圆环 + 中心分数文字
- 支持 QPropertyAnimation 平滑过渡动画
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import (
    QPainter, QPen, QColor, QLinearGradient, QFont, QPainterPath
)
from PySide6.QtCore import (
    Qt, Property, QPropertyAnimation, QEasingCurve, QRectF, Signal
)


class AffectionRing(QWidget):
    """好感度环形进度控件"""

    value_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0          # 当前显示值（动画中间值）
        self._target_value = 0.0   # 目标值
        self._max_score = 100.0
        self._ring_width = 12       # 环形宽度 px
        self._bg_color = QColor("#2a2a4a")
        self._color_start = QColor("#d4af37")
        self._color_end = QColor("#f4d03f")
        self._text_color = QColor("#f4d03f")

        # 动画对象
        self._animation = QPropertyAnimation(self, b"value", self)
        self._animation.setDuration(800)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

        self.setMinimumSize(120, 120)

    # ---- Qt Property 供动画驱动 ----
    @Property(float)
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = v
        self.update()

    # ---- 公开接口 ----
    def set_value(self, score: float, max_score: float = 100):
        """设置好感度分数，带动画平滑过渡"""
        self._max_score = max_score if max_score > 0 else 100.0
        self._target_value = score
        self._animation.stop()
        self._animation.setStartValue(self._value)
        self._animation.setEndValue(score)
        self._animation.start()
        self.value_changed.emit(score)

    def set_ring_width(self, width: int):
        """设置环形宽度"""
        self._ring_width = width
        self.update()

    def set_colors(self, bg: str, start: str, end: str):
        """自定义配色"""
        self._bg_color = QColor(bg)
        self._color_start = QColor(start)
        self._color_end = QColor(end)
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        # 正方形绘制区域
        side = min(w, h)
        margin = self._ring_width / 2 + 4
        rect = QRectF(
            (w - side) / 2 + margin,
            (h - side) / 2 + margin,
            side - 2 * margin,
            side - 2 * margin,
        )

        # --- 背景圆环 ---
        bg_pen = QPen(self._bg_color)
        bg_pen.setWidth(self._ring_width)
        bg_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(bg_pen)
        painter.drawArc(rect, 0, 360 * 16)

        # --- 进度圆环（金色渐变） ---
        ratio = 0.0
        if self._max_score > 0:
            ratio = max(0.0, min(1.0, self._value / self._max_score))
        span_angle = int(ratio * 360 * 16)  # 1/16 度单位

        if span_angle > 0:
            # 线性渐变沿圆弧方向近似：用径向渐变 + 渐变色
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, self._color_start)
            gradient.setColorAt(1.0, self._color_end)
            progress_pen = QPen(gradient, self._ring_width)
            progress_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(progress_pen)
            # 从顶部 (-90度) 开始，顺时针
            painter.drawArc(rect, -90 * 16, -span_angle)

        # --- 中心文字 ---
        painter.setPen(self._text_color)
        font = QFont("Microsoft YaHei", 14, QFont.Bold)
        painter.setFont(font)
        display_text = f"{self._value:.0f}"
        painter.drawText(self.rect(), Qt.AlignCenter, display_text)

        painter.end()
