"""
按钮控件（视觉风格借鉴 Cherry Studio 深色主题）
- 深色背景 + 中性灰描边 + 浅色文字
- hover 背景微亮，pressed 背景微暗
- level 1 = ghost/次级（导航、次要操作）
- level 2 = primary（主操作，indigo 实心）
- level 3 = strong（强调，亮 indigo）
- 保留 set_gold_level(level) 接口以兼容现有调用
"""

from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import QIcon


# 等级 -> 配色（Cherry Studio dark palette 提取）
#   主背景 #1f1f22 / 卡片 #26262b / 强调 indigo #6366f1
_LEVELS = {
    1: {
        "border": "rgba(255, 255, 255, 0.10)",
        "color": "#c8c8cf",
        "hover_border": "rgba(255, 255, 255, 0.18)",
        "bg": "rgba(255, 255, 255, 0.04)",
        "hover_bg": "rgba(255, 255, 255, 0.08)",
        "pressed_bg": "rgba(255, 255, 255, 0.03)",
    },
    2: {
        "border": "#6366f1",
        "color": "#ffffff",
        "hover_border": "#818cf8",
        "bg": "#6366f1",
        "hover_bg": "#7c7ff5",
        "pressed_bg": "#4f46e5",
    },
    3: {
        "border": "#818cf8",
        "color": "#ffffff",
        "hover_border": "#a5b4fc",
        "bg": "#7c7ff5",
        "hover_bg": "#8b8ef7",
        "pressed_bg": "#6366f1",
    },
}


class GildedButton(QPushButton):
    """按钮（命名保留以兼容旧调用；视觉为 Cherry Studio 深色风）"""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._gold_level = 2
        self.setMinimumHeight(34)
        self.setCursor(Qt_ArrowPointingCursor())
        self.set_gold_level(self._gold_level)

    def set_gold_level(self, level: int):
        """设置视觉等级 1-3（命名保留，原'金色亮度'已改为中性/indigo）"""
        self._gold_level = max(1, min(3, level))
        self._apply_style()

    def set_icon_with_text(self, icon: QIcon, text: str):
        """同时设置图标和文字"""
        self.setIcon(icon)
        self.setText(text)

    def _apply_style(self):
        """根据等级应用 QSS"""
        s = _LEVELS[self._gold_level]
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {s['bg']};"
            f"  border: 1px solid {s['border']};"
            f"  color: {s['color']};"
            f"  border-radius: 8px;"
            f"  padding: 6px 16px;"
            f"  font-family: 'Microsoft YaHei', 'Segoe UI';"
            f"  font-size: 10pt;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background-color: {s['hover_bg']};"
            f"  border: 1px solid {s['hover_border']};"
            f"  color: #ffffff;"
            f"}}"
            f"QPushButton:pressed {{"
            f"  background-color: {s['pressed_bg']};"
            f"}}"
            f"QPushButton:checked {{"
            f"  background-color: rgba(99, 102, 241, 0.28);"
            f"  border: 1px solid #6366f1;"
            f"  color: #ffffff;"
            f"}}"
            f"QPushButton:disabled {{"
            f"  color: #555560;"
            f"  border: 1px solid rgba(255,255,255,0.06);"
            f"  background-color: rgba(255,255,255,0.03);"
            f"}}"
        )


def Qt_ArrowPointingCursor():
    """延迟导入避免顶层循环；返回 PointingHandCursor。"""
    from PySide6.QtCore import Qt
    return Qt.PointingHandCursor
