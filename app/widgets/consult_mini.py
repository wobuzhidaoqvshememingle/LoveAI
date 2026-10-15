"""
LoveAI-v2 咨询小窗（ConsultMini）
- 无边框 / 置顶 / Tool 窗口，340×500
- 顶部标题 + 模式切换 + 关闭按钮
- 咨询历史 QTextEdit（只读）+ 底部输入框
- 整窗拖拽，open_window("consult_mini") 信号触发 show()
"""

import os
import logging

from PySide6.QtCore import Qt, QPoint, QThread, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QTextEdit,
)

from app.service_facade import ServiceFacade
from app.signals import get_signals
from app.widgets.common.gilded_button import GildedButton

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


class _ConsultThread(QThread):
    """后台线程：调用 facade.consult，避免阻塞 UI。"""
    finished = Signal(dict)   # 回传 facade 返回的 dict

    def __init__(self, facade: ServiceFacade, question: str, mode: str, parent=None):
        super().__init__(parent)
        self._facade = facade
        self._question = question
        self._mode = mode

    def run(self) -> None:
        try:
            result = self._facade.consult(self._question, self._mode)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        self.finished.emit(result)


class ConsultMini(QWidget):
    """情感咨询小窗。"""

    def __init__(self, facade: ServiceFacade, parent: QWidget = None):
        super().__init__(parent)
        self._facade = facade
        self._signals = get_signals()
        self._drag_pos: QPoint | None = None
        self._thread: _ConsultThread | None = None

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setFixedSize(340, 500)
        self.setStyleSheet("background-color: #0f0f1a;")
        _load_qss(self)

        self._build_ui()
        self._connect_signals()
        self._load_history()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # 顶部：标题 + 模式切换 + 关闭
        top = QHBoxLayout()
        title = QLabel("TOP二私教咨询")
        title.setStyleSheet("color: #f4d03f; font-weight: bold; font-size: 11pt;")
        top.addWidget(title, stretch=1)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("主窗口模式", "self")
        self._mode_combo.addItem("目标窗口模式", "other")
        self._mode_combo.setStyleSheet(
            "QComboBox { background: #16213e; color: #f4d03f; border: 1px solid #8a7a20; padding: 2px 4px; }"
        )
        top.addWidget(self._mode_combo)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            "QPushButton { color: #ef5350; background: transparent; border: none; font-weight: bold; }"
            "QPushButton:hover { color: #ff8a80; }"
        )
        close_btn.clicked.connect(self.hide)
        top.addWidget(close_btn)
        v.addLayout(top)

        # 历史
        self._history = QTextEdit()
        self._history.setReadOnly(True)
        self._history.setStyleSheet(
            "QTextEdit { background: #0a0a12; color: #e0e0e0; border: 1px solid #3a3a5a; border-radius: 4px; }"
        )
        v.addWidget(self._history, stretch=1)

        # 输入行
        bottom = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText("输入困惑，回车发送…")
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
        self._signals.open_window.connect(self._on_open_window)
        self._signals.hide_all_windows.connect(self.hide)
        self._signals.show_all_windows.connect(self.show)

    def _on_open_window(self, name: str) -> None:
        if name == "consult_mini":
            self.show()
            self.raise_()
            self.activateWindow()

    # ------------------------------------------------------------------ #
    def _load_history(self) -> None:
        try:
            r = self._facade.get_consult_history()
            if r.get("ok"):
                items = r.get("data") or []
                html_parts = []
                for it in items:
                    if isinstance(it, dict):
                        q = it.get("question", it.get("q", ""))
                        a = it.get("answer", it.get("a", it.get("reply", "")))
                        if q:
                            html_parts.append(f"<p style='color:#f4d03f'><b>我:</b> {q}</p>")
                        if a:
                            html_parts.append(f"<p style='color:#a0d0a0'><b>AI:</b> {a}</p>")
                    elif isinstance(it, str):
                        html_parts.append(f"<p style='color:#b0b0c0'>{it}</p>")
                if html_parts:
                    self._history.setHtml("".join(html_parts))
        except Exception:
            logger.debug("加载咨询历史失败", exc_info=True)

    def _on_send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        mode = self._mode_combo.currentData() or "self"
        self._history.append(f"<p style='color:#f4d03f'><b>我:</b> {text}</p>")
        self._input.clear()

        if self._thread is not None and self._thread.isRunning():
            self._history.append("<p style='color:#ef5350'>（上一条还在思考中…）</p>")
            return

        self._thread = _ConsultThread(self._facade, text, mode, self)
        self._thread.finished.connect(self._on_consult_done)
        self._thread.start()

    def _on_consult_done(self, result: dict) -> None:
        if result.get("ok"):
            data = result.get("data") or {}
            answer = data.get("answer") or data.get("reply") or str(data)
            self._history.append(f"<p style='color:#a0d0a0'><b>AI:</b> {answer}</p>")
        else:
            self._history.append(f"<p style='color:#ef5350'><b>错误:</b> {result.get('error','')}</p>")

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
