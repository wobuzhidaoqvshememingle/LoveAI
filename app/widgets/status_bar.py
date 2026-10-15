"""
LoveAI-v2 迷你状态栏（StatusBarMini）
- 200×32 窄条，无边框 / 置顶 / Tool
- 显示：当前对象昵称 + 好感度分数 + 待办任务数 + 微信状态指示灯
- 点击展开 / 收起；响应 person_switched / affection_changed / wechat_status 信号
"""

import os
import logging

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import (
    QPainter, QBrush, QColor, QMouseEvent,
)
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QSizePolicy,
)

from app.service_facade import ServiceFacade
from app.signals import get_signals

logger = logging.getLogger(__name__)


def _load_qss(window: QWidget) -> None:
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        qss_path = os.path.join(root, "resources", "qss", "main.qss")
        if os.path.exists(qss_path):
            with open(qss_path, encoding="utf-8") as f:
                window.setStyleSheet(f.read())
    except Exception:
        logger.debug("QSS 加载失败", exc_info=True)


class _Dot(QLabel):
    """圆形状态指示灯。"""

    def __init__(self, color: str = "#4caf50", parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setFixedSize(10, 10)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(self._color))
        p.setPen(Qt.NoPen)
        p.drawEllipse(0, 0, self.width(), self.height())
        p.end()


class StatusBarMini(QWidget):
    """迷你状态栏。"""

    def __init__(self, facade: ServiceFacade, parent: QWidget = None):
        super().__init__(parent)
        self._facade = facade
        self._signals = get_signals()
        self._drag_pos: QPoint | None = None
        self._expanded = False
        self._base_width = 200
        self._expanded_width = 380

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setFixedHeight(32)
        self.setFixedWidth(self._base_width)
        self.setStyleSheet(
            "background-color: #16213e; border: 1px solid #d4af37; border-radius: 4px;"
        )
        _load_qss(self)

        self._build_ui()
        self._connect_signals()

        # 30s 刷新一次任务数 / 微信状态
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(30_000)
        self._refresh_timer.timeout.connect(self._refresh_all)
        self._refresh_timer.start()

        # 初次拉取
        QTimer.singleShot(300, self._refresh_all)

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)

        self._name_lbl = QLabel("未选择对象")
        self._name_lbl.setStyleSheet("color: #f4d03f; font-size: 9pt;")
        h.addWidget(self._name_lbl, stretch=1)

        # R3.3-78：好感与忠诚分开展示（两个标签，不合并成一个数字）
        self._affection_lbl = QLabel("❤50")
        self._affection_lbl.setStyleSheet("color: #ffd54f; font-size: 8pt;")
        h.addWidget(self._affection_lbl)

        self._loyalty_lbl = QLabel("忠50")
        self._loyalty_lbl.setStyleSheet("color: #8ecae6; font-size: 8pt;")
        h.addWidget(self._loyalty_lbl)

        self._tasks_lbl = QLabel("待办 0")
        self._tasks_lbl.setStyleSheet("color: #b0b0c0; font-size: 9pt;")
        h.addWidget(self._tasks_lbl)

        self._wechat_dot = _Dot("#9e9e9e")
        h.addWidget(self._wechat_dot)

        # 展开后的附加信息（默认隐藏）
        self._extra_lbl = QLabel("")
        self._extra_lbl.setStyleSheet("color: #a0d0a0; font-size: 9pt;")
        self._extra_lbl.setVisible(False)
        h.addWidget(self._extra_lbl)

    # ------------------------------------------------------------------ #
    def _connect_signals(self) -> None:
        self._signals.person_switched.connect(lambda _p: self._refresh_all())
        self._signals.affection_changed.connect(self._on_affection_changed)
        # R3.3-78：忠诚度独立信号 → 只刷新忠诚标签
        self._signals.loyalty_changed.connect(self._on_loyalty_changed)
        self._signals.wechat_status.connect(self._on_wechat_status)
        self._signals.hide_all_windows.connect(self.hide)
        self._signals.show_all_windows.connect(self.show)

    # ------------------------------------------------------------------ #
    def _refresh_all(self) -> None:
        self._refresh_person()
        self._refresh_affection()
        self._refresh_tasks()
        self._refresh_wechat()

    def _refresh_person(self) -> None:
        try:
            r = self._facade.get_active_person()
            if not r.get("ok"):
                return
            data = r.get("data") or {}
            p = data.get("person")
            if p:
                name = p.get("nickname") or p.get("wechat_nickname") or "?"
                self._name_lbl.setText(name)
        except Exception:
            pass

    def _refresh_affection(self) -> None:
        try:
            r = self._facade.get_active_person()
            pid = (r.get("data") or {}).get("active_person_id") if r.get("ok") else None
            if not pid:
                return
            ar = self._facade.get_affection(pid)
            if ar.get("ok"):
                data = ar.get("data") or {}
                total = data.get("total", 50)
                self._affection_lbl.setText(f"❤{float(total):.0f}")
                # R3.3-78：忠诚独立展示，不与好感合并
                try:
                    loy = data.get("loyalty", 50)
                    self._loyalty_lbl.setText(f"忠{float(loy):.0f}")
                except Exception:
                    pass
        except Exception:
            pass

    def _refresh_tasks(self) -> None:
        try:
            r = self._facade.get_active_person()
            pid = (r.get("data") or {}).get("active_person_id") if r.get("ok") else None
            if not pid:
                return
            tr = self._facade.list_tasks(pid, "pending")
            if tr.get("ok"):
                cnt = len(tr.get("data") or [])
                self._tasks_lbl.setText(f"待办 {cnt}")
        except Exception:
            pass

    def _refresh_wechat(self) -> None:
        try:
            r = self._facade.get_wechat_status()
            if r.get("ok"):
                self._apply_wechat_status(r.get("data") or {})
        except Exception:
            pass

    def _apply_wechat_status(self, info: dict) -> None:
        status = (info.get("status") or "").lower()
        if status == "connected":
            self._wechat_dot.set_color("#4caf50")
        elif status in ("error", "disconnected"):
            self._wechat_dot.set_color("#ef5350")
        else:
            self._wechat_dot.set_color("#9e9e9e")

    # ------------------------------------------------------------------ #
    # 信号回调
    # ------------------------------------------------------------------ #
    def _on_affection_changed(self, payload: dict) -> None:
        total = payload.get("total")
        if total is not None:
            try:
                self._affection_lbl.setText(f"❤{float(total):.0f}")
            except Exception:
                pass
        # 若信号顺带带了 loyalty，一并刷新双轨
        loy = payload.get("loyalty")
        if loy is not None:
            try:
                self._loyalty_lbl.setText(f"忠{float(loy):.0f}")
            except Exception:
                pass

    def _on_loyalty_changed(self, payload: dict) -> None:
        loy = payload.get("loyalty")
        if loy is not None:
            try:
                self._loyalty_lbl.setText(f"忠{float(loy):.0f}")
            except Exception:
                pass

    def _on_wechat_status(self, payload: dict) -> None:
        self._apply_wechat_status(payload)

    # ------------------------------------------------------------------ #
    # 点击展开 / 收起
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        moved = 0
        if self._drag_pos is not None:
            moved = (event.globalPosition().toPoint() -
                     (self.frameGeometry().topLeft() + self._drag_pos)).manhattanLength()
            self._drag_pos = None
        # 短距离点击 => 展开 / 收起
        if moved < 5 and event.button() == Qt.LeftButton:
            self._toggle_expand()
        super().mouseReleaseEvent(event)

    def _toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self.setFixedWidth(self._expanded_width if self._expanded else self._base_width)
        self._extra_lbl.setVisible(self._expanded)
        if self._expanded:
            try:
                r = self._facade.get_wechat_status()
                info = r.get("data") or {} if r.get("ok") else {}
                self._extra_lbl.setText(f"微信: {info.get('status','?')}")
            except Exception:
                self._extra_lbl.setText("")
