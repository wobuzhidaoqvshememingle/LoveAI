"""
数字分身管理面板（需求 26.2.10）
- 展示当前分身模式、人格加载状态、当前情绪、关联对象与好感度同步等校准指标
- 三种可切换模式（伴侣 / 成长 / 对手），切换走 ServiceFacade.switch_twin_mode
- 状态来自 ServiceFacade.get_twin_panel（services.batch8_workflow.get_twin_status）
- 深色 indigo 主题
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QButtonGroup,
    QFrame, QScrollArea,
)

logger = logging.getLogger(__name__)

# 分身模式元数据：mode -> (中文名, 图标, 说明)
_MODE_META = {
    "partner":   ("伴侣分身", "💞", "模拟对方的视角与反应，帮你预判她会怎么想、怎么回"),
    "growth":    ("成长分身", "🌱", "陪你练习话术、复盘表现，专注自我成长与心态建设"),
    "adversary": ("对手分身", "🥊", "反方视角压力测试，提前发现话术与关系策略的漏洞"),
}


class DigitalTwinPanel(QWidget):
    """数字分身管理面板。"""

    mode_changed = Signal(str)   # 分身模式切换成功后发出新 mode

    def __init__(self, parent=None):
        super().__init__(parent)
        self._facade = None
        self._person_id: str = ""
        self._mode = "partner"
        self._mode_buttons: dict[str, QPushButton] = {}
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._build_ui()
        self.setStyleSheet("background: transparent;")

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        page = QVBoxLayout(self)
        page.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        v = QVBoxLayout(host)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(14)

        # ===== 当前状态卡 =====
        self._current_card = QFrame()
        self._current_card.setStyleSheet(
            "QFrame { background: rgba(99,102,241,0.10);"
            "border: 1px solid rgba(99,102,241,0.35); border-radius: 14px; }"
        )
        cc = QVBoxLayout(self._current_card)
        cc.setContentsMargins(18, 16, 18, 16)
        cc.setSpacing(8)
        cap = QLabel("当前分身")
        cap.setStyleSheet("color:#a1a1aa; font-size:10pt; background:transparent;")
        cc.addWidget(cap)
        self._current_title = QLabel("💞 伴侣分身")
        self._current_title.setStyleSheet(
            "color:#ffffff; font-size:20pt; font-weight:700; background:transparent;")
        cc.addWidget(self._current_title)
        self._current_desc = QLabel("")
        self._current_desc.setWordWrap(True)
        self._current_desc.setStyleSheet("color:#c8c8cf; font-size:10pt; background:transparent;")
        cc.addWidget(self._current_desc)
        v.addWidget(self._current_card)

        # ===== 状态 / 校准指标 =====
        cal_box = QFrame()
        cal_box.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.03);"
            "border:1px solid rgba(255,255,255,0.08); border-radius:12px; }"
        )
        cb = QVBoxLayout(cal_box)
        cb.setContentsMargins(16, 14, 16, 14)
        cb.setSpacing(10)
        cb.addWidget(self._section_label("状态与校准"))
        self._cal_persona = self._make_status_row(cb, "人格加载")
        self._cal_emotion = self._make_status_row(cb, "当前情绪")
        self._cal_person = self._make_status_row(cb, "关联对象")
        self._cal_affection = self._make_status_row(cb, "好感度同步")
        v.addWidget(cal_box)

        # ===== 模式切换 =====
        v.addWidget(self._section_label("切换分身模式"))
        for mode, (name, icon, desc) in _MODE_META.items():
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setMinimumHeight(64)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(self._mode_btn_qss(False))
            btn.setText("")
            # 用内部布局呈现 标题 + 说明
            btn_lay = QVBoxLayout(btn)
            btn_lay.setContentsMargins(14, 8, 14, 8)
            t = QLabel(f"{icon} {name}")
            t.setStyleSheet("color:#e5e5e8; font-size:12pt; font-weight:600; background:transparent;")
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet("color:#a1a1aa; font-size:9pt; background:transparent;")
            btn_lay.addWidget(t)
            btn_lay.addWidget(d)
            btn.clicked.connect(lambda _c=False, m=mode: self._on_switch(m))
            self._mode_group.addButton(btn)
            self._mode_buttons[mode] = btn
            v.addWidget(btn)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet("color:#71717a; font-size:9pt; background:transparent;")
        v.addWidget(self._hint_label)
        v.addStretch(1)

        scroll.setWidget(host)
        page.addWidget(scroll)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lb = QLabel(f"<h3 style='color:#e5e5e8'>{text}</h3>")
        lb.setStyleSheet("background:transparent;")
        return lb

    def _make_status_row(self, parent_layout, title: str) -> QLabel:
        row = QHBoxLayout()
        k = QLabel(title)
        k.setStyleSheet("color:#a1a1aa; background:transparent;")
        row.addWidget(k)
        row.addStretch(1)
        val = QLabel("—")
        val.setStyleSheet("color:#e5e5e8; font-weight:600; background:transparent;")
        row.addWidget(val)
        parent_layout.addLayout(row)
        return val

    @staticmethod
    def _mode_btn_qss(active: bool) -> str:
        if active:
            return (
                "QPushButton { background: rgba(99,102,241,0.22);"
                "border:1px solid #6366f1; border-radius:12px; text-align:left; }"
            )
        return (
            "QPushButton { background: rgba(255,255,255,0.03);"
            "border:1px solid rgba(255,255,255,0.08); border-radius:12px; text-align:left; }"
            "QPushButton:hover { background: rgba(255,255,255,0.06);"
            "border:1px solid rgba(99,102,241,0.45); }"
        )

    # ------------------------------------------------------------------ #
    # 数据加载
    # ------------------------------------------------------------------ #
    def load_from_facade(self, facade, person_id: str, mode: str | None = None) -> None:
        self._facade = facade
        self._person_id = person_id or ""
        if mode:
            self._mode = mode
        persona_loaded = False
        emotion = "未知"
        try:
            if facade is not None:
                resp = facade.get_twin_panel(self._person_id, self._mode)
                if resp.get("ok"):
                    data = resp.get("data") or {}
                    cur = data.get("current") or {}
                    self._mode = cur.get("mode") or self._mode
                    persona_loaded = bool(cur.get("persona_loaded"))
                    emotion = str(cur.get("current_emotion") or "未知")
        except Exception as e:  # noqa: BLE001
            logger.debug("分身面板加载失败: %s", e)

        # 关联对象昵称
        person_name = "未选择"
        affection_txt = "未同步"
        try:
            if facade is not None and self._person_id:
                pr = facade.get_person(self._person_id)
                if pr.get("ok"):
                    p = pr.get("data") or {}
                    person_name = p.get("nickname") or p.get("wechat_nickname") or self._person_id
                ar = facade.get_affection(self._person_id)
                if ar.get("ok"):
                    total = (ar.get("data") or {}).get("total")
                    if total is not None:
                        affection_txt = f"{float(total):.0f} / 100"
        except Exception as e:  # noqa: BLE001
            logger.debug("分身校准数据取数降级: %s", e)

        self._apply_view(persona_loaded, emotion, person_name, affection_txt)

    def _apply_view(self, persona_loaded: bool, emotion: str,
                    person_name: str, affection_txt: str) -> None:
        name, icon, desc = _MODE_META.get(self._mode, (self._mode, "🤖", ""))
        self._current_title.setText(f"{icon} {name}")
        self._current_desc.setText(desc)

        ok_color = "#34d399"
        self._cal_persona.setText("✓ 已加载" if persona_loaded else "✗ 未加载")
        self._cal_persona.setStyleSheet(
            f"color:{ok_color if persona_loaded else '#f87171'}; font-weight:600; background:transparent;")
        self._cal_emotion.setText(emotion or "未知")
        self._cal_person.setText(person_name)
        self._cal_affection.setText(affection_txt)

        for m, btn in self._mode_buttons.items():
            active = (m == self._mode)
            btn.setChecked(active)
            btn.setStyleSheet(self._mode_btn_qss(active))

    # ------------------------------------------------------------------ #
    def _on_switch(self, mode: str) -> None:
        if mode == self._mode:
            return
        if self._facade is None:
            return
        try:
            resp = self._facade.switch_twin_mode(mode)
            if resp.get("ok"):
                self._mode = ((resp.get("data") or {}).get("mode")) or mode
                self._hint_label.setStyleSheet(
                    "color:#34d399; font-size:9pt; background:transparent;")
                cn = _MODE_META.get(self._mode, (self._mode, "", ""))[0]
                self._hint_label.setText(f"✓ 已切换到「{cn}」")
                self.load_from_facade(self._facade, self._person_id, self._mode)
                self.mode_changed.emit(self._mode)
            else:
                self._hint_label.setStyleSheet(
                    "color:#f87171; font-size:9pt; background:transparent;")
                self._hint_label.setText(f"切换失败：{resp.get('error', '未知错误')}")
        except Exception as e:  # noqa: BLE001
            logger.debug("切换分身模式异常: %s", e)
            self._hint_label.setStyleSheet(
                "color:#f87171; font-size:9pt; background:transparent;")
            self._hint_label.setText(f"切换异常：{e}")
