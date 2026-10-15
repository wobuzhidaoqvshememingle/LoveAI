# -*- coding: utf-8 -*-
"""空状态/错误状态辅助控件（UI层，可 import PySide6）。offscreen 安全。"""
try:
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
    _HAS_QT = True
except Exception:
    _HAS_QT = False
    QLabel = object; QWidget = object


def _qt_ready() -> bool:
    if not _HAS_QT:
        return False
    try:
        from PySide6.QtWidgets import QApplication
        return QApplication.instance() is not None
    except Exception:
        return False


def make_empty_state(text: str, emoji: str = "📭"):
    if not _qt_ready():
        return {"type":"empty","text":f"{emoji} {text}"}
    lab = QLabel(f"{emoji}  {text}")
    lab.setObjectName("emptyState")
    lab.setWordWrap(True)
    return lab


def make_error_state(text: str, code: str = ""):
    msg = f"⚠️ {text}" + (f"（错误码 {code}）" if code else "")
    if not _qt_ready():
        return {"type":"error","text":msg}
    lab = QLabel(msg)
    lab.setObjectName("errorState")
    lab.setWordWrap(True)
    return lab


# 各面板空状态文案（供 UI 直接取用）
EMPTY_TEXTS = {
    "tasks": "还没有任务，去聊天分析页生成第一个任务吧",
    "chats": "还没有聊天记录，粘贴或截图导入开始分析",
    "profile": "还没有人物数据，先导入聊天记录生成画像",
    "graph": "还没有识别到关系网络，导入更多聊天记录后自动提取",
}
