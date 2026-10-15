"""
LoveAI-v2 代聊悬浮窗（ChatOverlay）
- 无边框 / 置顶 / Tool 窗口
- 弹幕区 + 3 张回复建议卡片 + 底部输入框
- 跟随微信窗口右侧，整窗拖拽
"""

import os
import logging

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QScrollArea, QFrame, QSizePolicy,
)

from app.service_facade import ServiceFacade
from app.signals import get_signals
from app.widgets.common.danmaku_label import DanmakuLabel
from app.widgets.common.gilded_button import GildedButton

logger = logging.getLogger(__name__)


def _load_qss(window: QWidget) -> None:
    """加载 main.qss（可选）。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
        qss_path = os.path.join(root, "resources", "qss", "main.qss")
        if os.path.exists(qss_path):
            with open(qss_path, encoding="utf-8") as f:
                window.setStyleSheet(f.read())
    except Exception:
        logger.debug("QSS 加载失败", exc_info=True)


class _ReplyCard(QGroupBox):
    """单张回复建议卡片：回复文本 + 风格标签 + 预期反应 + 好感度概率 + 填入按钮。"""

    def __init__(self, reply_text: str = "", style: str = "", reaction: str = "",
                 prob: str = "", parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QGroupBox { color: #f4d03f; border: 1px solid #8a7a20;"
            "border-radius: 6px; margin-top: 6px; padding: 6px; }"
        )
        v = QVBoxLayout(self)
        v.setSpacing(4)

        self._text_lbl = QLabel(reply_text or "（生成中…）")
        self._text_lbl.setWordWrap(True)
        self._text_lbl.setStyleSheet("color: #e0e0e0; background: transparent;")
        v.addWidget(self._text_lbl)

        meta = QHBoxLayout()
        self._style_lbl = QLabel(f"<span style='color:#ffd54f'>[{style}]</span>" if style else "")
        self._reaction_lbl = QLabel(f"预期: {reaction}" if reaction else "")
        self._prob_lbl = QLabel(f"好感度 +{prob}" if prob else "")
        for w in (self._style_lbl, self._reaction_lbl, self._prob_lbl):
            w.setStyleSheet("color: #b0b0c0; background: transparent; font-size: 9pt;")
            meta.addWidget(w)
        meta.addStretch(1)
        v.addLayout(meta)

        self._fill_btn = GildedButton("填入微信")
        self._fill_btn.set_gold_level(2)
        self._fill_btn.setFixedHeight(26)
        v.addWidget(self._fill_btn)

    def update_content(self, reply_text: str, style: str = "",
                       reaction: str = "", prob: str = "") -> None:
        self._text_lbl.setText(reply_text or "（无内容）")
        self._style_lbl.setText(f"<span style='color:#ffd54f'>[{style}]</span>" if style else "")
        self._reaction_lbl.setText(f"预期: {reaction}" if reaction else "")
        self._prob_lbl.setText(f"好感度 +{prob}" if prob else "")


class ChatOverlay(QWidget):
    """代聊悬浮窗。"""

    def __init__(self, facade: ServiceFacade, parent: QWidget = None):
        super().__init__(parent)
        self._facade = facade
        self._signals = get_signals()
        self._drag_pos: QPoint | None = None
        self._current_reply_texts: list[str] = ["", "", ""]

        # 窗口标志：无边框 + 置顶 + Tool（不显示任务栏）
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setFixedWidth(360)
        self.setStyleSheet("background-color: #0f0f1a;")
        _load_qss(self)

        self._build_ui()
        self._connect_signals()

        # 跟随微信窗口的定时器
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(500)
        self._follow_timer.timeout.connect(self._follow_wechat)
        self._follow_timer.start()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # 顶部：昵称 + 关闭按钮
        top = QHBoxLayout()
        self._name_label = QLabel("代聊 · 未选择对象")
        self._name_label.setStyleSheet("color: #f4d03f; font-weight: bold;")
        top.addWidget(self._name_label, stretch=1)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            "QPushButton { color: #ef5350; background: transparent; border: none; font-weight: bold; }"
            "QPushButton:hover { color: #ff8a80; }"
        )
        close_btn.clicked.connect(self.hide)
        top.addWidget(close_btn)
        v.addLayout(top)

        # 弹幕区（可滚动）
        self._danmaku_scroll = QScrollArea()
        self._danmaku_scroll.setWidgetResizable(True)
        self._danmaku_scroll.setFixedHeight(110)
        self._danmaku_scroll.setStyleSheet(
            "QScrollArea { border: 1px dashed #3a3a5a; background: #0a0a12; }"
        )
        self._danmaku_container = QWidget()
        self._danmaku_container.setObjectName("danmakuArea")
        self._danmaku_container.setStyleSheet("background: #0a0a12;")
        self._danmaku_layout = QVBoxLayout(self._danmaku_container)
        self._danmaku_layout.setContentsMargins(6, 6, 6, 6)
        self._danmaku_layout.setSpacing(4)
        self._danmaku_layout.addStretch(1)
        self._danmaku_scroll.setWidget(self._danmaku_container)
        v.addWidget(self._danmaku_scroll)

        # 回复建议区（3 张卡片）
        self._reply_cards: list[_ReplyCard] = []
        for i in range(3):
            card = _ReplyCard()
            self._reply_cards.append(card)
            self._danmaku_layout.parentWidget()  # no-op，保持引用
            v.addWidget(card)
            card._fill_btn.clicked.connect(lambda _checked, idx=i: self._fill_to_wechat(idx))

        # 底部输入框
        bottom = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("输入回复，回车发送到微信…")
        self._input.setStyleSheet(
            "QLineEdit { background: #16213e; color: #e0e0e0; border: 1px solid #8a7a20; padding: 5px; }"
        )
        self._input.returnPressed.connect(self._on_send)
        bottom.addWidget(self._input, stretch=1)
        send_btn = GildedButton("发")
        send_btn.set_gold_level(2)
        send_btn.setFixedWidth(40)
        send_btn.clicked.connect(self._on_send)
        bottom.addWidget(send_btn)
        v.addLayout(bottom)

    # ------------------------------------------------------------------ #
    def _connect_signals(self) -> None:
        self._signals.new_message.connect(self._on_new_message)
        self._signals.analysis_ready.connect(self._on_analysis_ready)
        self._signals.person_switched.connect(self._on_person_switched)
        self._signals.hide_all_windows.connect(self.hide)
        self._signals.show_all_windows.connect(self.show)

    # ------------------------------------------------------------------ #
    # 微信跟随
    # ------------------------------------------------------------------ #
    def _follow_wechat(self) -> None:
        try:
            resp = self._facade.get_wechat_window_info()
            if not resp.get("ok"):
                return
            info = resp.get("data") or {}
            if not info.get("found"):
                return
            wx_x = int(info.get("x", 0))
            wx_y = int(info.get("y", 0))
            wx_w = int(info.get("width", 0))
            wx_h = int(info.get("height", 0))
            # 放到微信右侧
            target_x = wx_x + wx_w + 8
            target_y = wx_y + 20
            self.move(target_x, target_y)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 信号回调
    # ------------------------------------------------------------------ #
    def _on_new_message(self, msg: dict) -> None:
        """收到新消息：弹一条弹幕并触发分析。"""
        content = msg.get("content", "")
        if not content:
            return
        direction = msg.get("direction", "in")
        emotion = "positive" if direction == "out" else "neutral"

        danmaku = DanmakuLabel(self._danmaku_container)
        danmaku.set_emotion(emotion, f"{'我:' if direction=='out' else '对方:'} {content[:60]}")
        # 插入到 stretch 之前
        self._danmaku_layout.insertWidget(self._danmaku_layout.count() - 1, danmaku)

        # 3 秒后淡出移除
        def _remove():
            try:
                self._danmaku_layout.removeWidget(danmaku)
                danmaku.deleteLater()
            except Exception:
                pass
        QTimer.singleShot(3000, _remove)

        # 触发分析
        pid = msg.get("person_id") or self._current_pid()
        if pid:
            self._facade.analyze_chat(content, pid)

    def _on_analysis_ready(self, payload: dict) -> None:
        data = payload.get("data") or {}
        # 期望 data.replies = [{text, style, reaction, prob}, ...]
        replies = data.get("replies") or data.get("reply_options") or []
        for i, card in enumerate(self._reply_cards):
            if i < len(replies):
                r = replies[i]
                if isinstance(r, dict):
                    card.update_content(
                        r.get("text", r.get("reply", "")),
                        r.get("style", ""),
                        r.get("reaction", ""),
                        str(r.get("prob", r.get("affection_delta", ""))),
                    )
                    self._current_reply_texts[i] = r.get("text", r.get("reply", ""))
                else:
                    card.update_content(str(r))
                    self._current_reply_texts[i] = str(r)

    def _on_person_switched(self, payload: dict) -> None:
        pid = payload.get("person_id")
        if not pid:
            return
        try:
            r = self._facade.get_person(pid)
            if r.get("ok"):
                p = r.get("data") or {}
                name = p.get("nickname") or p.get("wechat_nickname") or pid
                self._name_label.setText(f"代聊 · {name}")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    def _current_pid(self) -> str | None:
        try:
            r = self._facade.get_active_person()
            if r.get("ok"):
                return (r.get("data") or {}).get("active_person_id")
        except Exception:
            pass
        return None

    def _fill_to_wechat(self, idx: int) -> None:
        text = self._current_reply_texts[idx] if 0 <= idx < len(self._current_reply_texts) else ""
        if not text:
            return
        r = self._facade.send_reply_to_wechat(text)
        if not r.get("ok"):
            logger.warning("填入微信失败: %s", r.get("error"))

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        r = self._facade.send_reply_to_wechat(text)
        if r.get("ok"):
            self._input.clear()

    # ------------------------------------------------------------------ #
    # 整窗拖拽
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
        self._drag_pos = None
