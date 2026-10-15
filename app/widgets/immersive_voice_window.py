# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 沉浸式语音交互桌面（R3.3-49）

全屏暗色背景（深灰蓝，不要纯黑）：
  - 右侧 40% 宽：立绘区域（QLabel 显示 2D 序列帧）
  - 中间：语音球（圆形，随 VAD 音量缩放/脉动）
  - 底部：字幕条（显示当前 ASR 识别文字）

CPU 轻量：
  - 立绘用 QLabel.setPixmap() 切帧，不做 GPU 动画
  - 语音球用 QPainter 在 paintEvent 里画椭圆，半径由当前电平决定
  - 动画由 services.pet_animator.PetAnimator (QTimer ~30fps) 驱动

对接：
  - 外部调用 set_vad_level() 喂入麦克风音量 -> 语音球缩放
  - 外部调用 set_subtitle() 喂入 ASR 文本 -> 底部字幕
  - 外部调用 set_tts_level() 喂入 TTS 振幅 -> 立绘口型同步
  - 外部调用 set_expression() 切换立绘表情
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget, QHBoxLayout

# 让本模块既能 `python -m` 跑，也能被打包后 import
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.pet_animator import PetAnimator  # noqa: E402

logger = logging.getLogger(__name__)

# 背景色：深灰蓝（不要纯黑）
_BG_COLOR = QColor(24, 28, 42)
# 语音球颜色
_BALL_COLOR = QColor(120, 180, 255, 220)
_BALL_EDGE = QColor(180, 220, 255, 255)
# 字幕条
_SUBTITLE_BG = "rgba(255,255,255,24)"
_SUBTITLE_COLOR = "#e8eef7"


class _VoiceBall(QWidget):
    """中间语音球：根据 level (0..1) 画一个半径脉动的圆。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._level: float = 0.0
        self._smooth: float = 0.0
        self.setMinimumSize(280, 280)
        # 平滑插值 timer
        self._t = QTimer(self)
        self._t.setInterval(33)
        self._t.timeout.connect(self._tick)
        self._t.start()

    def set_level(self, level: float) -> None:
        try:
            self._level = max(0.0, min(1.0, float(level)))
        except (TypeError, ValueError):
            self._level = 0.0

    def _tick(self) -> None:
        # 指数平滑，避免球一卡一卡
        self._smooth += (self._level - self._smooth) * 0.25
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt 命名)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        cx, cy = w // 2, h // 2
        base_r = min(w, h) * 0.22
        r = base_r * (1.0 + 0.6 * self._smooth)
        # 外圈光晕
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(120, 180, 255, int(40 + 60 * self._smooth)))
        p.drawEllipse(int(cx - r * 1.25), int(cy - r * 1.25),
                      int(r * 2.5), int(r * 2.5))
        # 主球
        p.setBrush(_BALL_COLOR)
        p.setPen(_BALL_EDGE)
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))
        p.end()


class ImmersiveVoiceWindow(QWidget):
    """沉浸式语音交互主窗口。"""

    #: 用户按 Esc 或关闭时发出
    closed = Signal()

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        sprite_dir: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("沉浸式语音")
        self.setStyleSheet(f"background-color: rgb({_BG_COLOR.red()},{_BG_COLOR.green()},{_BG_COLOR.blue()});")

        # 动画控制器
        self.animator = PetAnimator(sprite_dir=sprite_dir, parent=self)
        self.animator.frame_changed.connect(self._on_frame)

        self._build_ui()
        self._refresh_portrait()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 中间区域（语音球 + 字幕）
        mid = QWidget()
        mid_lay = QVBoxLayout(mid)
        mid_lay.setContentsMargins(20, 40, 20, 20)
        self.ball = _VoiceBall(mid)
        mid_lay.addStretch(2)
        mid_lay.addWidget(self.ball, alignment=Qt.AlignHCenter)
        mid_lay.addStretch(1)

        # 字幕条
        self.subtitle = QLabel("点击开始说话…")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet(
            f"background: {_SUBTITLE_BG}; color: {_SUBTITLE_COLOR};"
            " font-size: 18px; padding: 14px 20px; border-radius: 10px;"
        )
        self.subtitle.setMinimumHeight(60)
        mid_lay.addWidget(self.subtitle)

        root.addWidget(mid, stretch=6)  # 中间 60%

        # 右侧立绘（40%）
        self.portrait = QLabel("立绘加载中…")
        self.portrait.setAlignment(Qt.AlignCenter)
        self.portrait.setStyleSheet("background: transparent; color: #888;")
        root.addWidget(self.portrait, stretch=4)

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动动画与全屏显示。"""
        self.showFullScreen()
        self.animator.start()

    def closeEvent(self, event):  # noqa: N802
        self.animator.stop()
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    # ---- 对接语音管线 ----
    def set_vad_level(self, level: float) -> None:
        """喂入 VAD 麦克风音量 (0..1)，语音球脉动。"""
        self.ball.set_level(level)

    def set_subtitle(self, text: str) -> None:
        """喂入 ASR 实时识别文本。"""
        self.subtitle.setText(text or "")

    def set_expression(self, expr: str) -> None:
        """切换立绘表情（happy/sad/thinking/surprised/neutral）。"""
        self.animator.set_expression(expr)

    def set_tts_level(self, level: float) -> None:
        """喂入 TTS 播放振幅 (0..1)，驱动口型同步。"""
        self.animator.set_tts_level(level)

    # ------------------------------------------------------------------
    # 帧刷新
    # ------------------------------------------------------------------
    def _on_frame(self, state: dict) -> None:
        self._refresh_portrait(state)

    def _refresh_portrait(self, state: Optional[dict] = None) -> None:
        """根据当前状态合成一张立绘 QPixmap 并显示。"""
        st = state or self.animator.current_state()
        sprites: dict = st.get("sprites", {})
        expr = st.get("expression", "neutral")
        eyes = st.get("eyes", "open")
        mouth = st.get("mouth", "rest")
        scale_y = st.get("scale_y", 1.0)

        # 1) 基础表情帧
        base_path = sprites.get(expr) or sprites.get("neutral")
        if not base_path or not os.path.exists(base_path):
            self.portrait.setText("（无立绘资源）")
            return

        pix = QPixmap(base_path)
        if pix.isNull():
            self.portrait.setText("（立绘加载失败）")
            return

        # 2) 叠加眼睛（闭眼时盖一张 eyes_closed）
        if eyes == "closed":
            eye_path = sprites.get("eyes_closed")
            if eye_path and os.path.exists(eye_path):
                eye = QPixmap(eye_path)
                if not eye.isNull():
                    painter = QPainter(pix)
                    painter.drawPixmap(0, 0, eye)
                    painter.end()

        # 3) 叠加嘴型（TTS 播放中）
        if mouth and mouth != "rest":
            m_path = sprites.get(f"mouth_{mouth}")
            if m_path and os.path.exists(m_path):
                m = QPixmap(m_path)
                if not m.isNull():
                    painter = QPainter(pix)
                    painter.drawPixmap(0, 0, m)
                    painter.end()

        # 4) Y 轴呼吸缩放
        # （QLabel 不能直接缩放 pixmap 的 Y，这里简单用 scaled 保留 X）
        target_w = self.portrait.width() or 260
        target_h = int(target_w * pix.height() / max(1, pix.width()) * scale_y)
        pix = pix.scaled(target_w, target_h,
                         Qt.KeepAspectRatio, Qt.SmoothTransformation)

        self.portrait.setPixmap(pix)
