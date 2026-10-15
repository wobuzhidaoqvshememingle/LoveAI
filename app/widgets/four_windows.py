# -*- coding: utf-8 -*-
"""四档窗口框架（UI层）：主窗口已有，这里做悬浮球/overlay代聊/TOP2小窗基础框架。offscreen 安全。"""
try:
    from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QPushButton, QLineEdit
    from PySide6.QtCore import Qt, QApplication
    _HAS_QT = True
except Exception:
    _HAS_QT = False
    QWidget = object

def _qt_ready():
    if not _HAS_QT: return False
    try: return QApplication.instance() is not None
    except Exception: return False


class FloatingBall(QWidget if _HAS_QT else object):
    def __init__(self, parent=None):
        if not _qt_ready():
            return
        super().__init__(parent)
        self.setFixedSize(56, 56)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("⚽"))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)

    def quick_actions(self):
        return ["截图分析", "代聊", "查档案", "打开主窗口"]


class OverlayChat(QWidget if _HAS_QT else object):
    """悬浮在微信旁显示3条回复建议（不实际操作微信）。"""
    def __init__(self, parent=None):
        if not _qt_ready():
            return
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        lay = QVBoxLayout(self)
        self._replies = QLabel("回复建议 1\n回复建议 2\n回复建议 3")
        lay.addWidget(self._replies)

    def set_replies(self, replies: list):
        if _HAS_QT: self._replies.setText("\n".join(replies))


class Top2SmallWindow(QWidget if _HAS_QT else object):
    """小窗：输入框+分析结果，随时问私教。"""
    def __init__(self, parent=None):
        if not _qt_ready():
            return
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self._in = QLineEdit(); self._out = QLabel("分析结果将在这里显示")
        lay.addWidget(self._in); lay.addWidget(self._out)


class WindowSwitcher:
    """窗口切换：main / ball / overlay / top2。offscreen 安全。"""
    def __init__(self):
        self.windows = {}
    def register(self, name, widget):
        self.windows[name] = widget
    def switch_to(self, name: str):
        if name not in self.windows: return False
        for n, w in self.windows.items():
            if w is not None and hasattr(w, "hide"):
                try: w.hide()
                except Exception: pass
        w = self.windows[name]
        if w is not None and hasattr(w, "show"):
            try: w.show()
            except Exception: pass
        return True
