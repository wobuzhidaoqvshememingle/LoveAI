"""
右侧快捷面板（需求 26.3.14）
- 好感度四维（信任 / 吸引 / 情感 / 承诺）：复用 AffectionRing 迷你环
- 最近 3 条任务（状态点 + 标题）
- 快捷动作按钮（截图分析 / 代聊 / 查档案），点击经 action_triggered 交给主窗口联动
- 数据来自 ServiceFacade.get_quick_panel；四维若服务未直接给出，
  则按好感度引擎的维度分组由 get_affection 的 18 维真实分数聚合，保证有数可看
- 深色 indigo 主题

本模块是维度分层中的**观测层**（UI 好感度四维：信任/吸引/情感/承诺），
本层维度命名空间见 services/person_state_snapshot.py 的 DimensionLayerRegistry。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame,
)

from app.widgets.common.affection_ring import AffectionRing

logger = logging.getLogger(__name__)

# 四维：key -> (中文名, 由 18 维聚合时使用的维度)
_AXES = [
    ("trust",      "信任", ["trust", "security", "thoughtfulness", "respect"]),
    ("attraction", "吸引", ["physical_attraction", "humor", "romance", "chat_resonance"]),
    ("emotion",    "情感", ["emotional_value", "emotional_stability", "intimacy"]),
    ("commitment", "承诺", ["future_alignment", "family", "growth", "lifestyle"]),
]

_STATUS_STYLE = {
    "pending":     ("○", "#a1a1aa"),
    "in_progress": ("◐", "#6366f1"),
    "completed":   ("●", "#34d399"),
    "abandoned":   ("✕", "#71717a"),
    "failed":      ("✕", "#71717a"),
}


class QuickPanel(QWidget):
    """主窗口右侧常驻快捷面板。"""

    action_triggered = Signal(str)   # 快捷动作名（截图分析/代聊/查档案）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("quickPanel")
        self.setStyleSheet(
            "QWidget#quickPanel { background-color:#1a1a1d;"
            "border-left: 1px solid rgba(255,255,255,0.06); }"
        )
        self._rings: dict[str, AffectionRing] = {}
        self._axis_value_labels: dict[str, QLabel] = {}
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        v = QVBoxLayout(host)
        v.setContentsMargins(12, 14, 12, 14)
        v.setSpacing(12)

        title = QLabel("快捷面板")
        title.setStyleSheet("color:#e5e5e8; font-size:12pt; font-weight:700; background:transparent;")
        v.addWidget(title)

        # ===== 好感度四维 =====
        axes_card = self._card()
        ac = QVBoxLayout(axes_card)
        ac.setContentsMargins(12, 10, 12, 12)
        ac.setSpacing(8)
        ac.addWidget(self._hint_label("好感度四维"))
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (key, cn, _dims) in enumerate(_AXES):
            ring = AffectionRing()
            ring.setMinimumSize(56, 56)
            ring.setFixedSize(64, 64)
            ring.set_ring_width(8)
            name_lb = QLabel(cn)
            name_lb.setAlignment(Qt.AlignCenter)
            name_lb.setStyleSheet("color:#c8c8cf; font-size:9pt; background:transparent;")
            val_lb = QLabel("--")
            val_lb.setAlignment(Qt.AlignCenter)
            val_lb.setStyleSheet("color:#f4d03f; font-size:9pt; font-weight:600; background:transparent;")
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.addWidget(ring, 0, Qt.AlignCenter)
            cell.addWidget(name_lb)
            cell.addWidget(val_lb)
            r, c = divmod(i, 2)
            grid.addLayout(cell, r, c)
            self._rings[key] = ring
            self._axis_value_labels[key] = val_lb
        ac.addLayout(grid)
        v.addWidget(axes_card)

        # ===== 最近任务 =====
        task_card = self._card()
        tc = QVBoxLayout(task_card)
        tc.setContentsMargins(12, 10, 12, 12)
        tc.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(self._hint_label("最近任务"))
        head.addStretch(1)
        tc.addLayout(head)
        self._task_box = QVBoxLayout()
        self._task_box.setSpacing(6)
        tc.addLayout(self._task_box)
        self._task_empty = QLabel("暂无任务")
        self._task_empty.setStyleSheet("color:#71717a; font-size:9pt; background:transparent;")
        tc.addWidget(self._task_empty)
        v.addWidget(task_card)

        # ===== 快捷动作 =====
        action_card = self._card()
        al = QVBoxLayout(action_card)
        al.setContentsMargins(12, 10, 12, 12)
        al.setSpacing(8)
        al.addWidget(self._hint_label("快捷动作"))
        self._action_box = QVBoxLayout()
        self._action_box.setSpacing(6)
        al.addLayout(self._action_box)
        v.addWidget(action_card)

        v.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll)

    @staticmethod
    def _card() -> QFrame:
        f = QFrame()
        f.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.03);"
            "border:1px solid rgba(255,255,255,0.08); border-radius:12px; }"
        )
        return f

    @staticmethod
    def _hint_label(text: str) -> QLabel:
        lb = QLabel(f"<b style='color:#e5e5e8'>{text}</b>")
        lb.setStyleSheet("background:transparent;")
        return lb

    # ------------------------------------------------------------------ #
    # 数据加载
    # ------------------------------------------------------------------ #
    def load_from_facade(self, facade, person_id: str) -> None:
        recent: list = []
        mini_axes: dict = {}
        actions: list = []
        scores: dict = {}
        try:
            if facade is not None and person_id:
                resp = facade.get_quick_panel(person_id)
                if resp.get("ok"):
                    data = resp.get("data") or {}
                    recent = data.get("recent_tasks") or []
                    mini_axes = data.get("mini_axes") or {}
                    actions = data.get("quick_actions") or []
                ar = facade.get_affection(person_id)
                if ar.get("ok"):
                    scores = (ar.get("data") or {}).get("scores") or {}
        except Exception as e:  # noqa: BLE001
            logger.debug("快捷面板加载失败: %s", e)

        self._render_axes(mini_axes, scores)
        self._render_tasks(recent)
        self._render_actions(actions)

    # ------------------------------------------------------------------ #
    def _render_axes(self, mini_axes: dict, scores: dict) -> None:
        for key, cn, dims in _AXES:
            value = None
            if isinstance(mini_axes, dict) and key in mini_axes:
                try:
                    value = float(mini_axes[key])
                except (ValueError, TypeError):
                    value = None
            if value is None:
                vals = []
                for d in dims:
                    if d in scores:
                        try:
                            vals.append(float(scores[d]))
                        except (ValueError, TypeError):
                            pass
                value = round(sum(vals) / len(vals), 1) if vals else 0.0
            value = max(0.0, min(100.0, value))
            self._rings[key].set_value(value)
            self._axis_value_labels[key].setText(f"{value:.0f}")

    def _render_tasks(self, tasks: list) -> None:
        # 清空旧任务
        self._clear_layout(self._task_box)
        tasks = tasks or []
        self._task_empty.setVisible(not tasks)
        for t in tasks[:3]:
            if not isinstance(t, dict):
                continue
            status = str(t.get("status") or "pending")
            icon, color = _STATUS_STYLE.get(status, ("○", "#a1a1aa"))
            row = QHBoxLayout()
            dot = QLabel(icon)
            dot.setStyleSheet(f"color:{color}; background:transparent;")
            row.addWidget(dot)
            title = QLabel(str(t.get("title") or "任务"))
            title.setStyleSheet("color:#c8c8cf; font-size:9pt; background:transparent;")
            title.setWordWrap(False)
            title.setStyleSheet("color:#c8c8cf; font-size:9pt; background:transparent;")
            row.addWidget(title, stretch=1)
            wrap = QWidget()
            wrap.setStyleSheet("background:transparent;")
            wrap.setLayout(row)
            self._task_box.addWidget(wrap)

    def _render_actions(self, actions: list) -> None:
        self._clear_layout(self._action_box)
        for name in (actions or ["截图分析", "代聊", "查档案"]):
            btn = QPushButton(str(name))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { background: rgba(99,102,241,0.16); color:#e5e5e8;"
                "border:1px solid rgba(99,102,241,0.35); border-radius:8px; padding:7px 10px;"
                "text-align:left; }"
                "QPushButton:hover { background: rgba(99,102,241,0.30); color:#fff; }"
            )
            btn.clicked.connect(lambda _c=False, n=str(name): self.action_triggered.emit(n))
            self._action_box.addWidget(btn)

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            child = item.layout()
            if child is not None:
                QuickPanel._clear_layout(child)
