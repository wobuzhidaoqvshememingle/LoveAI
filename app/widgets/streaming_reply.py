# -*- coding: utf-8 -*-
"""流式回复控件（UI层，可 import PySide6）。QThread + signal 逐字更新。offscreen 安全。"""
try:
    from PySide6.QtCore import QThread, Signal, QCoreApplication, QTimer, Qt
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
    _HAS_QT = True
except Exception:
    _HAS_QT = False
    QThread = object
    class Signal:  # 占位
        def __init__(self, *a): pass
        def emit(self, *a): pass
        def connect(self, *a): pass
    QWidget = object

def _qt_ready():
    if not _HAS_QT: return False
    try: return QCoreApplication.instance() is not None
    except Exception: return False


class StreamingThread(QThread if _HAS_QT else object):
    """生成回复并分段 emit update_text；非流式 LLM 时一次性 emit 全文（降级）。"""
    update_text = Signal(str) if _HAS_QT else None
    finished_all = Signal(str) if _HAS_QT else None

    def __init__(self, generator=None, parent=None):
        if _HAS_QT:
            super().__init__(parent)
        self._gen = generator or (lambda: ["你好","，","我","觉得","..."])

    def run(self):
        try:
            chunks = self._gen()
            full = "".join(chunks) if isinstance(chunks, list) else str(chunks)
            if _HAS_QT:
                if isinstance(chunks, list):
                    buf = ""
                    for c in chunks:
                        buf += c
                        self.update_text.emit(buf)
                else:
                    self.update_text.emit(full)
                self.finished_all.emit(full)
        except Exception:
            # 降级：一次性返回空，不崩溃
            if _HAS_QT:
                self.finished_all.emit("")


class StreamingReplyWidget(QWidget if _HAS_QT else object):
    """流式回复气泡：借鉴 Cherry Studio，AI 消息无气泡，末尾带闪烁光标 ▋。"""

    def __init__(self, parent=None):
        if not _qt_ready():
            return
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._lab = QLabel("")
        self._lab.setWordWrap(True)
        # AI 消息文字色（与主窗口 consult 页一致）
        self._lab.setStyleSheet("color: #e5e5e8; background: transparent; font-size: 10pt;")
        lay.addWidget(self._lab)
        self._base_text = ""
        self._cursor_on = True
        # 闪烁光标：每 530ms 切换一次，视觉效果，不影响业务
        self._timer = QTimer(self)
        self._timer.setInterval(530)
        self._timer.timeout.connect(self._toggle_cursor)
        self._timer.start()

    def _toggle_cursor(self):
        """切换光标显隐（纯视觉）。"""
        if not _HAS_QT or getattr(self, "_lab", None) is None:
            return
        self._cursor_on = not self._cursor_on
        self._render()

    def set_text(self, t: str):
        if _HAS_QT and getattr(self, "_lab", None) is not None:
            self._base_text = t or ""
            self._render()

    def _render(self):
        """渲染当前文字 + 光标。"""
        cursor = "▋" if self._cursor_on else " "
        self._lab.setText(f"{self._base_text}{cursor}")
