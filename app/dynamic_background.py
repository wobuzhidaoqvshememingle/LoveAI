# -*- coding: utf-8 -*-
"""
LoveAI v3.0 动态粒子背景（UI层，可 import PySide6）。

R3.3-21 动效背景：
- QWidget + QPainter 缓慢流动渐变 + 少量粒子连线；CPU 轻量（30fps 上限，窗口隐藏即停表）；
- set_enabled(False) 时退化为纯色底（不动画、不连线）；
- set_opacity(0-100) 控制粒子/连线的不透明度（由 settings_dialog 的透明度滑条写入）；
- 不参与任何后端逻辑；offscreen / 无 Qt 环境可安全 import。
"""
import math
import random

try:
    from PySide6.QtWidgets import QWidget
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient
    _HAS_QT = True
except Exception:  # offscreen 测试环境兜底
    _HAS_QT = False
    QWidget = object


# 关掉动效时的纯色底（深靛蓝，与主窗口主题一致）
_SOLID_COLOR = QColor(15, 15, 26) if _HAS_QT else None


class DynamicBackground(QWidget if _HAS_QT else object):
    """缓慢流动的渐变/粒子背景。

    性能约束：
    - QTimer 33ms（~30fps 上限），窗口不可见时停表（hideEvent），不做全屏重绘以外的工作；
    - 粒子默认 40 个，两两连线 O(n²) 但 n 小、且只画距离 <120px 的边；
    - 不用 QPropertyAnimation。
    """

    TIMER_INTERVAL_MS = 33          # ~30fps 上限
    LINK_DISTANCE_PX = 120          # 连线距离阈值（画布像素）

    def __init__(self, parent=None, particle_count: int = 40):
        if _HAS_QT:
            super().__init__(parent)
        self._n = int(particle_count)
        self._particles = []
        self._t = 0.0
        self._enabled = True          # 动效开关：False = 纯色
        self._opacity = 90            # 粒子/连线不透明度 0-100
        for _ in range(self._n):
            self._particles.append([random.random(), random.random(),
                                    random.uniform(-0.003, 0.003),
                                    random.uniform(-0.003, 0.003)])
        if _HAS_QT:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self._timer = QTimer(self)
            self._timer.setInterval(self.TIMER_INTERVAL_MS)
            self._timer.timeout.connect(self._tick)
            # 仅在可见时才跑表；showEvent 里再 start()
            if self.isVisible():
                self._timer.start()

    # ------------------------------------------------------------------ #
    # 对外 API（main_window / settings_dialog 接线用）
    # ------------------------------------------------------------------ #
    def set_enabled(self, on: bool) -> None:
        """动效总开关：True=渐变+粒子流动；False=纯色底，停表。"""
        self._enabled = bool(on)
        if _HAS_QT:
            if not self._enabled:
                self._timer.stop()
            elif self.isVisible():
                self._timer.start()
            self.update()

    def is_enabled(self) -> bool:
        return self._enabled

    def set_opacity(self, value: int) -> None:
        """粒子/连线不透明度（0-100）。由 settings_dialog 透明度滑条写入。"""
        try:
            self._opacity = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            self._opacity = 90
        if _HAS_QT:
            self.update()

    def opacity(self) -> int:
        return self._opacity

    # ------------------------------------------------------------------ #
    # 动画 tick：只动位置 + requestUpdate；不可见时由 Qt 不发 paintEvent，
    # 且我们在 hideEvent 直接停表，保证隐藏时 CPU 占用 ~0。
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        self._t += 0.008
        for p in self._particles:
            p[0] += p[2]
            p[1] += p[3]
            if p[0] < 0 or p[0] > 1:
                p[2] *= -1
            if p[1] < 0 or p[1] > 1:
                p[3] *= -1
        self.update()

    if _HAS_QT:
        def showEvent(self, event):
            super().showEvent(event)
            if self._enabled and not self._timer.isActive():
                self._timer.start()

        def hideEvent(self, event):
            super().hideEvent(event)
            self._timer.stop()

        def paintEvent(self, event):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()

            # 开关关闭：纯色，一笔 fillRect 完事
            if not self._enabled:
                p.fillRect(self.rect(), _SOLID_COLOR)
                p.end()
                return

            # 开启：缓慢呼吸的对角渐变底
            breath = (math.sin(self._t) + 1) / 2.0   # 0..1
            c1 = QColor(12, 12 + int(18 * breath), 30)
            c2 = QColor(28, 26 + int(14 * breath), 52)
            grad = QLinearGradient(0.0, 0.0, float(w), float(h))
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            p.fillRect(self.rect(), grad)

            op = self._opacity / 100.0               # 0..1
            # 粒子位置（单位坐标 -> 像素）
            pts = [(int(x * w), int(y * h)) for x, y, _, _ in self._particles]
            # 短距离连线（淡紫）
            for i in range(len(pts)):
                xi, yi = pts[i]
                for j in range(i + 1, len(pts)):
                    xj, yj = pts[j]
                    d = math.hypot(xi - xj, yi - yj)
                    if d < self.LINK_DISTANCE_PX:
                        alpha = int(110 * op * (1 - d / self.LINK_DISTANCE_PX))
                        if alpha > 4:
                            p.setPen(QPen(QColor(168, 85, 247, alpha), 1))
                            p.drawLine(xi, yi, xj, yj)
            # 粒子点（蓝）
            dot_alpha = int(190 * op)
            p.setPen(QPen(QColor(59, 130, 246, dot_alpha), 2))
            for x, y in pts:
                p.drawPoint(x, y)
            p.end()


# 别名：需求/测试里按 DynamicBackgroundWidget 名字实例化
DynamicBackgroundWidget = DynamicBackground


if __name__ == "__main__":
    import sys
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = DynamicBackgroundWidget()
    w.resize(800, 600)
    w.show()
    sys.exit(app.exec())
