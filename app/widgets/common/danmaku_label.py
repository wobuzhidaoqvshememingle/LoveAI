"""
弹幕标签控件
- 4 种情绪颜色样式：positive / neutral / negative / hidden
- 圆角边框 + 半透明背景 + 白色文字
- 淡入动画（QGraphicsOpacityEffect + QPropertyAnimation）
"""

from PySide6.QtWidgets import QLabel, QGraphicsOpacityEffect
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve


# 情绪类型 -> (背景色, 边框色)
_EMOTION_STYLES = {
    "positive": ("#4caf50", "#66bb6a"),   # 绿色
    "neutral":  ("#ffc107", "#ffd54f"),   # 黄色
    "negative": ("#f44336", "#ef5350"),   # 红色
    "hidden":   ("#9c27b0", "#ab47bc"),   # 紫色
}


class DanmakuLabel(QLabel):
    """弹幕情绪标签"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._emotion_type = "neutral"
        self._bg_color = QColor("#ffc107")
        self._border_color = QColor("#ffd54f")

        # 透明度动画
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(400)
        self._fade_anim.setEasingCurve(QEasingCurve.InOutQuad)

        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(28)
        self.setMaximumHeight(28)

    def set_emotion(self, emotion_type: str, text: str):
        """设置情绪类型和内容文字"""
        self._emotion_type = emotion_type
        bg, border = _EMOTION_STYLES.get(emotion_type, _EMOTION_STYLES["neutral"])
        self._bg_color = QColor(bg)
        self._border_color = QColor(border)

        self.setText(text)
        self._apply_style()
        self._play_fade_in()

    def _apply_style(self):
        """根据当前情绪色应用 QSS 样式"""
        bg_rgba = f"rgba({self._bg_color.red()}, {self._bg_color.green()}, {self._bg_color.blue()}, 0.35)"
        border_rgb = f"{self._border_color.red()}, {self._border_color.green()}, {self._border_color.blue()}"
        self.setStyleSheet(
            f"QLabel {{"
            f"  background-color: {bg_rgba};"
            f"  border: 1px solid rgb({border_rgb});"
            f"  border-radius: 12px;"
            f"  color: #ffffff;"
            f"  padding: 2px 12px;"
            f"  font-size: 11px;"
            f"}}"
        )

    def _play_fade_in(self):
        """播放淡入动画"""
        self._fade_anim.stop()
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
