"""
个人资料优化建议卡片（需求 23.7）
- 展示 ServiceFacade.optimize_profile 返回的头像 / 签名 / 朋友圈优化建议
- 可按「对方偏好」重新生成；每条建议可单独复制，也可一键复制全部 / 一键采纳
- 「采纳」当前落地为把整理后的优化文案复制到剪贴板（后端暂无资料回写接口），
  并给出明确状态提示，不做任何静默假写
- 深色 indigo 主题
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QWidget, QApplication,
)

logger = logging.getLogger(__name__)

# 偏好下拉项：显示名 -> partner_pref 入参
_PREF_OPTIONS = [
    ("未指定偏好", {}),
    ("偏好真诚", {"喜欢": "真诚"}),
    ("偏好阳光", {"喜欢": "阳光"}),
]


class ProfileOptimizeCard(QFrame):
    """个人资料优化建议卡。"""

    adopted = Signal(str)   # 一键采纳时发出整理后的文案（供上层扩展回写）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._facade = None
        self._person_id: str = ""
        self._suggestions: list[str] = []
        self.setObjectName("profileOptCard")
        self.setStyleSheet(
            "QFrame#profileOptCard { background: rgba(255,255,255,0.03);"
            "border:1px solid rgba(255,255,255,0.08); border-radius:12px; }"
        )
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("<b style='color:#e5e5e8'>个人资料优化建议</b>")
        title.setStyleSheet("background:transparent;")
        head.addWidget(title)
        head.addStretch(1)
        v.addLayout(head)

        # 偏好选择 + 生成
        row = QHBoxLayout()
        row.addWidget(self._muted("对方偏好"))
        self._pref_combo = QComboBox()
        for name, _pref in _PREF_OPTIONS:
            self._pref_combo.addItem(name)
        self._pref_combo.setStyleSheet(
            "QComboBox { background: rgba(255,255,255,0.06); color:#e5e5e8;"
            "border:1px solid rgba(255,255,255,0.10); padding:4px 8px; border-radius:8px; }"
        )
        row.addWidget(self._pref_combo, stretch=1)
        self._gen_btn = QPushButton("生成建议")
        self._gen_btn.setCursor(Qt.PointingHandCursor)
        self._gen_btn.setStyleSheet(
            "QPushButton { background:#6366f1; color:#fff; border:none;"
            "border-radius:8px; padding:6px 14px; font-weight:600; }"
            "QPushButton:hover { background:#7c7ff5; }"
        )
        self._gen_btn.clicked.connect(self._regenerate)
        row.addWidget(self._gen_btn)
        v.addLayout(row)

        # 当前资料摘要
        self._profile_summary = QLabel("当前资料：—")
        self._profile_summary.setWordWrap(True)
        self._profile_summary.setStyleSheet(
            "color:#a1a1aa; font-size:9pt; background:transparent;")
        v.addWidget(self._profile_summary)

        # 建议列表容器
        self._tips_box = QVBoxLayout()
        self._tips_box.setSpacing(6)
        v.addLayout(self._tips_box)

        self._empty_label = QLabel("选择对方偏好后点击「生成建议」")
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet(
            "color:#71717a; font-size:9pt; padding:8px;"
            "border:1px dashed rgba(255,255,255,0.10); border-radius:8px;"
            "background:transparent;")
        v.addWidget(self._empty_label)

        # 底部操作
        foot = QHBoxLayout()
        foot.addStretch(1)
        self._copy_all_btn = QPushButton("复制全部")
        self._copy_all_btn.setCursor(Qt.PointingHandCursor)
        self._copy_all_btn.clicked.connect(self._copy_all)
        foot.addWidget(self._copy_all_btn)
        self._adopt_btn = QPushButton("一键采纳")
        self._adopt_btn.setCursor(Qt.PointingHandCursor)
        self._adopt_btn.setStyleSheet(
            "QPushButton { background:#6366f1; color:#fff; border:none;"
            "border-radius:8px; padding:6px 16px; font-weight:600; }"
            "QPushButton:hover { background:#7c7ff5; }"
        )
        self._adopt_btn.clicked.connect(self._adopt_all)
        foot.addWidget(self._adopt_btn)
        v.addLayout(foot)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#34d399; font-size:9pt; background:transparent;")
        v.addWidget(self._status)

        self._set_buttons_enabled(False)

    @staticmethod
    def _muted(text: str) -> QLabel:
        lb = QLabel(text)
        lb.setStyleSheet("color:#a1a1aa; background:transparent;")
        return lb

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self._copy_all_btn.setEnabled(enabled)
        self._adopt_btn.setEnabled(enabled)

    # ------------------------------------------------------------------ #
    # 数据
    # ------------------------------------------------------------------ #
    def load_from_facade(self, facade, person_id: str, partner_pref: dict | None = None) -> bool:
        self._facade = facade
        self._person_id = person_id or ""
        pref = partner_pref if partner_pref is not None else _PREF_OPTIONS[
            self._pref_combo.currentIndex()][1]
        try:
            if facade is None:
                self._render([], "")
                return False
            resp = facade.optimize_profile(self._person_id, pref)
            if not resp.get("ok"):
                self._status.setStyleSheet("color:#f87171; font-size:9pt; background:transparent;")
                self._status.setText(f"生成失败：{resp.get('error', '未知错误')}")
                self._render([], "")
                return False
            data = resp.get("data") or {}
            tips = [str(x) for x in (data.get("suggestions") or []) if str(x).strip()]
            profile = str(data.get("profile") or "")
            self._render(tips, profile)
            return bool(tips)
        except Exception as e:  # noqa: BLE001
            logger.debug("资料优化建议加载失败: %s", e)
            self._render([], "")
            return False

    def _regenerate(self) -> None:
        self._status.setText("")
        self.load_from_facade(self._facade, self._person_id)

    def _render(self, tips: list[str], profile: str) -> None:
        self._suggestions = tips
        # 清空旧建议行
        while self._tips_box.count():
            item = self._tips_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # 资料摘要
        if profile.strip():
            short = profile.strip()
            self._profile_summary.setText(
                "当前资料：" + (short if len(short) <= 60 else short[:60] + "…"))
        else:
            self._profile_summary.setText("当前资料：暂未填写")

        if not tips:
            self._empty_label.setText("暂无针对性建议，可切换对方偏好后重新生成")
            self._empty_label.show()
            self._set_buttons_enabled(False)
            return
        self._empty_label.hide()
        self._set_buttons_enabled(True)
        for tip in tips:
            self._tips_box.addWidget(self._make_tip_row(tip))

    def _make_tip_row(self, tip: str) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet("background:transparent;")
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        lb = QLabel(f"💡 {tip}")
        lb.setWordWrap(True)
        lb.setStyleSheet(
            "color:#c8c8cf; font-size:9pt; background:rgba(255,255,255,0.03);"
            "border:1px solid rgba(255,255,255,0.06); border-radius:8px; padding:6px 8px;")
        h.addWidget(lb, stretch=1)
        cp = QPushButton("复制")
        cp.setCursor(Qt.PointingHandCursor)
        cp.setFixedHeight(30)
        cp.clicked.connect(lambda _c=False, t=tip: self._copy_text(t, "已复制该条建议"))
        h.addWidget(cp, 0, Qt.AlignTop)
        return wrap

    # ------------------------------------------------------------------ #
    def _assembled_text(self) -> str:
        title = "【个人资料优化建议】"
        lines = [f"{i}. {t}" for i, t in enumerate(self._suggestions, 1)]
        return "\n".join([title] + lines)

    def _copy_text(self, text: str, ok_msg: str) -> None:
        QApplication.clipboard().setText(text)
        self._status.setStyleSheet("color:#34d399; font-size:9pt; background:transparent;")
        self._status.setText("✓ " + ok_msg)

    def _copy_all(self) -> None:
        if self._suggestions:
            self._copy_text(self._assembled_text(), "已复制全部优化建议")

    def _adopt_all(self) -> None:
        if not self._suggestions:
            return
        text = self._assembled_text()
        # 后端暂无资料回写接口：把可直接粘贴的整理文案放入剪贴板，并明确提示
        QApplication.clipboard().setText(text)
        self.adopted.emit(text)
        self._status.setStyleSheet("color:#34d399; font-size:9pt; background:transparent;")
        self._status.setText("✓ 已采纳：优化文案已复制到剪贴板，可粘贴到头像 / 签名 / 朋友圈编辑处。")
