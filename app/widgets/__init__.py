"""
LoveAI-v2 widgets 包导出
- MainWindow: 主窗口（QMainWindow）
- ChatOverlay: 代聊悬浮窗
- ConsultMini: 咨询小窗
- FloatBall: 悬浮球（Qt3D 桌宠的轻量备用方案）
- StatusBarMini: 迷你状态栏
"""

from .main_window import MainWindow
from .chat_overlay import ChatOverlay
from .consult_mini import ConsultMini
from .float_ball import FloatBall
from .status_bar import StatusBarMini

__all__ = [
    "MainWindow",
    "ChatOverlay",
    "ConsultMini",
    "FloatBall",
    "StatusBarMini",
]
