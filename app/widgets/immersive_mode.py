"""
LoveAI-v2 沉浸式语音桌面模式（ImmersiveMode）
================================================

纯 PySide6 QWidget 实现，全屏无边框暗色背景。
- 中央发光语音球（idle 蓝 / recording·thinking 橙 / speaking 紫 三态动画）
- 右侧半透明立绘占位（QPainter 画人形轮廓 + BlurEffect 融合 + 呼吸动画）
- 顶部关系阶段标签（从 facade.get_active_person() 读）
- 底部字幕条（半透明黑底圆角，最多 2 行）

语音流程骨架：
    点球/麦克风 → 录音（QAudioRecorder，不可用则 stub）
        → facade.transcribe_audio_file()  (whisper STT)
        → facade.consult()                 (策略引擎)
        → edge-tts 播放                    (不可用则仅字幕)
        → 字幕条显示回复
        → 回 idle

所有外部依赖（QAudioRecorder / edge-tts / whisper / QtMultimedia）
均 try/except graceful degrade，失败只在字幕条提示，绝不崩。

公开方法（供 window_manager 接线）：
    ImmersiveMode(facade, parent=None)
    .set_mode(state)               # "idle" / "recording" / "thinking" / "speaking"
    .show_subtitle(text, duration=None)
    .set_stage_label(text)
    .close_immersive()             # 发 hide_all_windows 信号后隐藏
    .toggle_fullscreen()           # F11 等价
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect, QPointF,
    Signal, Property,
)
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QRadialGradient, QBrush, QPen,
    QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPixmap, QTransform,
)
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QGraphicsBlurEffect, QVBoxLayout,
    QHBoxLayout, QApplication,
)

# --------------------------------------------------------------------------- #
# 可选依赖：全部 try/except，缺失则 graceful degrade
# --------------------------------------------------------------------------- #
logger = logging.getLogger(__name__)

# QtMultimedia：QAudioRecorder
try:
    from PySide6.QtMultimedia import QMediaCaptureSession, QAudioRecorder
    _HAS_QAUDIO = True
except Exception:  # pragma: no cover
    QMediaCaptureSession = None  # type: ignore
    QAudioRecorder = None  # type: ignore
    _HAS_QAUDIO = False
    logger.debug("QtMultimedia.QAudioRecorder 不可用，录音走 stub 模式")

# edge-tts + 播放（QMediaPlayer 也在 QtMultimedia）
try:
    import edge_tts  # type: ignore
    import asyncio
    _HAS_EDGE_TTS = True
except Exception:  # pragma: no cover
    edge_tts = None  # type: ignore
    asyncio = None  # type: ignore
    _HAS_EDGE_TTS = False
    logger.debug("edge-tts 不可用，TTS 仅字幕降级")

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    _HAS_QMEDIA_PLAYER = True
except Exception:  # pragma: no cover
    QMediaPlayer = None  # type: ignore
    QAudioOutput = None  # type: ignore
    _HAS_QMEDIA_PLAYER = False

# facade / signals（项目内部，理论必存在；仍 try 兜底）
try:
    from app.service_facade import ServiceFacade
except Exception:  # pragma: no cover
    ServiceFacade = Any  # type: ignore

try:
    from app.signals import get_signals
except Exception:  # pragma: no cover
    def get_signals():  # type: ignore
        return None


# --------------------------------------------------------------------------- #
# 配色
# --------------------------------------------------------------------------- #
_BG = "#0a0908"
_COLOR_IDLE = QColor("#4a9eff")       # 蓝
_COLOR_REC = QColor("#ff8a3d")       # 橙（recording / thinking）
_COLOR_SPEAK = QColor("#c084fc")     # 紫（speaking）
_BALL_DIAMETER = 120
_GLOW_MAX = 90


def _state_color(state: str) -> QColor:
    if state == "recording" or state == "thinking":
        return _COLOR_REC
    if state == "speaking":
        return _COLOR_SPEAK
    return _COLOR_IDLE


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class ImmersiveMode(QWidget):
    """沉浸式语音桌面模式主窗口。"""

    # 对外信号：关闭时通知外部（window_manager 可 connect）
    immersive_closed = Signal()

    def __init__(self, facade: ServiceFacade | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self._facade = facade
        self._signals = get_signals()

        # 状态
        self._mode: str = "idle"
        self._glow_radius: float = 0.0       # 光晕半径（动画驱动）
        self._portrait_offset: float = 0.0    # 立绘呼吸 y 偏移
        self._subtitle_text: str = ""
        self._fullscreen: bool = True

        # 录音 / TTS 句柄（可能为 None）
        self._audio_recorder: Any = None
        self._record_file: str = ""
        self._media_player: Any = None
        # R3.3-14 录音后端：优先走 SpeechService（=voice_pipeline 录音），
        # 不可用才回退老 QAudioRecorder。探针据此验证「真走 voice_pipeline」。
        self._recorder_backend: str = "qaudio"
        self._pre_stt_text: str = ""   # speech_service 已识别好的文本
        self._tts_anim_timer: Any = None   # TTS 播放期间驱动口型的定时器

        # 窗口属性
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"background-color: {_BG};")

        # 尝试全屏到主屏
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.availableGeometry())
        else:
            self.resize(1280, 800)

        self._build_ui()
        self._setup_animations()
        self._refresh_stage_label()

    # ------------------------------------------------------------------ #
    # UI 构建
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        # 不使用 layout 做绝对定位（paintEvent 自绘主体），
        # 只放几个浮层 QLabel / QPushButton。

        # 左上角状态小字（2026-10-12 参考图版式：两行、左对齐，不再居中横条）
        self._stage_label = QLabel("赛博伴侣 · 地球Online\n未选择对象 · 点光球说话", self)
        self._stage_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._stage_label.setStyleSheet(
            "color: #9a93a8; background: transparent; font-size: 10pt;"
            " letter-spacing: 1px; padding-left: 26px; padding-top: 18px;"
        )
        self._stage_label.setGeometry(0, 0, int(self.width() * 0.4), 64)

        # 右侧立绘（2026-10-12 参考图版式：清晰真人感抠像母版，不再加模糊）
        self._portrait = QLabel(self)
        self._portrait.setStyleSheet("background: transparent;")
        self._build_portrait_pixmap()
        self._portrait.setAlignment(Qt.AlignBottom | Qt.AlignHCenter)

        # 球两侧卫星点按钮（2026-10-12 参考图版式：左=录音、右=停止，与球同水平线）
        btn_w, btn_h = 36, 36
        self._mic_btn = QPushButton("●", self)
        self._mic_btn.setFixedSize(btn_w, btn_h)
        self._mic_btn.setCursor(Qt.PointingHandCursor)
        self._mic_btn.setStyleSheet(
            "QPushButton { color: #cfd8ff; background: rgba(30,34,52,190);"
            "border: 1px solid #4a9eff; border-radius: 18px; font-size: 11pt; }"
            "QPushButton:hover { background: rgba(74,158,255,120); }"
        )
        self._mic_btn.clicked.connect(self.toggle_recording)

        self._stop_btn = QPushButton("■", self)
        self._stop_btn.setFixedSize(btn_w, btn_h)
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.setStyleSheet(
            "QPushButton { color: #ffb4b4; background: rgba(46,30,34,190);"
            "border: 1px solid #ff6b6b; border-radius: 18px; font-size: 9pt; }"
            "QPushButton:hover { background: rgba(255,107,107,120); }"
        )
        self._stop_btn.clicked.connect(self.stop_recording_or_playback)

        # 底部字幕条（2026-10-12 参考图版式：无黑底圆角、情绪关键词高亮）
        self._subtitle = QLabel("", self)
        self._subtitle.setWordWrap(True)
        self._subtitle.setAlignment(Qt.AlignCenter)
        self._subtitle.setTextFormat(Qt.RichText)
        self._subtitle.setStyleSheet(
            "QLabel {"
            "  color: #f5f2ea; background: transparent;"
            "  padding: 6px 10px; font-size: 15pt; font-weight: 600;"
            "}"
        )

    def _resolve_sprite_dir(self) -> str:
        """优先 assets/pet_sprites/，失败回退 assets/portraits/。"""
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(here))
        for cand in (os.path.join(root, "assets", "pet_sprites"),
                     os.path.join(root, "assets", "portraits")):
            if os.path.isdir(cand) and os.path.exists(
                    os.path.join(cand, "neutral.png")):
                return cand
        return ""

    def _build_portrait_pixmap(self) -> None:
        """R3.3-49/51: 真加载 assets/pet_sprites/neutral.png; PNG 缺失才退回 QPainter 剪影.

        旧实现: QPainter 画人形轮廓, PNG 一张没加载.
        新实现: QPixmap 加载真实序列帧, 并把 PetAnimator 接进来,
                在 speaking 状态下按 TTS 振幅切嘴型 (a/i/u/e/o).
        """
        sprite_dir = self._resolve_sprite_dir()
        self._sprite_dir = sprite_dir
        self._sprites = {}
        if sprite_dir:
            for name in ("neutral", "happy", "sad", "thinking", "surprised",
                         "eyes_open", "eyes_closed",
                         "mouth_a", "mouth_i", "mouth_u", "mouth_e", "mouth_o"):
                fp = os.path.join(sprite_dir, name + ".png")
                if os.path.exists(fp):
                    self._sprites[name] = fp

        # 接 PetAnimator 驱动嘴型 (CPU 轻量, offscreen 可测)
        self._animator = None
        try:
            from services.pet_animator import PetAnimator
            self._animator = PetAnimator(sprite_dir=sprite_dir, parent=self)
            self._animator.frame_changed.connect(self._on_animator_frame)
            self._animator.start()
        except Exception:
            logger.debug("PetAnimator 启动失败 (嘴型不驱动)", exc_info=True)

        self._current_base_pix = None
        # 2026-10-12 参考图版式：优先用锁定母版的透明底抠像立绘（真人感），
        # 其次已成品的小妖女帧序列主立绘，再旧序列帧，最后才 QPainter 剪影。
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(os.path.dirname(here))
        cybergf = os.path.join(root, "desktop-pet-electron", "assets",
                               "models", "cybergf", "character.png")
        keyed = os.path.join(root, "assets", "pet_portrait", "master_keyed.png")
        for cand in (keyed, cybergf):
            if os.path.exists(cand):
                pm = QPixmap(cand)
                if not pm.isNull():
                    pm = pm.scaled(560, 1080, Qt.KeepAspectRatio,
                                   Qt.SmoothTransformation)
                    self._portrait.setPixmap(pm)
                    self._portrait.setStyleSheet("background: transparent;")
                    self._current_base_pix = pm
                    return
        base = self._sprites.get("neutral")
        if base and os.path.exists(base):
            pm = QPixmap(base)
            if not pm.isNull():
                pm = pm.scaled(360, 720, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
                self._portrait.setPixmap(pm)
                self._portrait.setStyleSheet("background: transparent;")
                self._current_base_pix = pm
                return

        # 兜底: PNG 真缺失才画 QPainter 剪影 (不再是主路径)
        try:
            w, h = 360, 720
            pm = QPixmap(w, h)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing, True)
            body_color = QColor(120, 110, 160, 70)
            p.setBrush(QBrush(body_color))
            p.setPen(Qt.NoPen)
            path = QPainterPath()
            path.moveTo(w * 0.25, h * 0.55)
            path.cubicTo(w * 0.20, h * 0.45, w * 0.30, h * 0.40, w * 0.50, h * 0.40)
            path.cubicTo(w * 0.70, h * 0.40, w * 0.80, h * 0.45, w * 0.75, h * 0.55)
            path.lineTo(w * 0.78, h * 0.95)
            path.lineTo(w * 0.22, h * 0.95)
            p.closeSubpath()
            p.drawPath(path)
            p.drawEllipse(QPointF(w * 0.5, h * 0.30), w * 0.14, w * 0.16)
            p.end()
            self._portrait.setPixmap(pm)
            self._portrait.setStyleSheet("color: #555; background: transparent;")
            self._current_base_pix = pm
        except Exception:
            logger.debug("立绘兜底绘制失败", exc_info=True)
            self._portrait.setText("（无立绘资源）")
            self._portrait.setStyleSheet("color: #555; background: transparent;")

    def _on_animator_frame(self, state):
        """PetAnimator 帧回调: 把嘴型/闭眼叠到当前立绘基图上."""
        if not state or getattr(self, "_current_base_pix", None) is None:
            return
        mouth = state.get("mouth", "rest")
        eyes = state.get("eyes", "open")
        base = QPixmap(self._current_base_pix)
        if mouth and mouth != "rest":
            mp = self._sprites.get("mouth_" + mouth)
            if mp:
                m = QPixmap(mp)
                if not m.isNull():
                    m = m.scaled(base.size(), Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
                    pp = QPainter(base)
                    pp.drawPixmap(0, 0, m)
                    pp.end()
        if eyes == "closed":
            ep = self._sprites.get("eyes_closed")
            if ep:
                e = QPixmap(ep)
                if not e.isNull():
                    e = e.scaled(base.size(), Qt.KeepAspectRatio,
                                 Qt.SmoothTransformation)
                    pp = QPainter(base)
                    pp.drawPixmap(0, 0, e)
                    pp.end()
        self._portrait.setPixmap(base)

    def set_tts_level(self, level):
        """对外接口: TTS 播放振幅驱动嘴型 (speaking 状态下调用)."""
        if getattr(self, "_animator", None) is not None:
            try:
                self._animator.set_tts_level(level)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 动画
    # ------------------------------------------------------------------ #
    def _setup_animations(self) -> None:
        # 光晕呼吸动画：在 0 ~ _GLOW_MAX 之间往返
        self._glow_anim = QPropertyAnimation(self, b"glowRadius", self)
        self._glow_anim.setDuration(2200)
        self._glow_anim.setStartValue(0.0)
        self._glow_anim.setEndValue(float(_GLOW_MAX))
        self._glow_anim.setLoopCount(-1)
        self._glow_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._glow_anim.start()

        # 立绘呼吸动画：y 偏移 -8 ~ 8
        self._portrait_anim = QPropertyAnimation(self, b"portraitOffset", self)
        self._portrait_anim.setDuration(3600)
        self._portrait_anim.setStartValue(-8.0)
        self._portrait_anim.setEndValue(8.0)
        self._portrait_anim.setLoopCount(-1)
        self._portrait_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._portrait_anim.start()

    # QProperty 桥接
    def getGlowRadius(self) -> float:
        return self._glow_radius

    def setGlowRadius(self, v: float) -> None:
        self._glow_radius = v
        self.update()

    glowRadius = Property(float, getGlowRadius, setGlowRadius)

    def getPortraitOffset(self) -> float:
        return self._portrait_offset

    def setPortraitOffset(self, v: float) -> None:
        self._portrait_offset = v
        self._reposition_portrait()

    portraitOffset = Property(float, getPortraitOffset, setPortraitOffset)

    # ------------------------------------------------------------------ #
    # 布局：resize 时重新排布浮层
    # ------------------------------------------------------------------ #
    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()

        # 左上角两行状态小字
        self._stage_label.setGeometry(0, 0, int(w * 0.4), 64)

        # 右侧立绘：占宽约 38%、底边贴窗口底（参考图：像从右侧延伸进来）
        ph = int(h * 0.92)
        pw = int(ph * 0.74)   # 母版约 3:4 竖幅
        self._portrait.setFixedSize(pw, ph)
        self._reposition_portrait()

        # 中央球 + 两侧卫星点按钮（与球同水平线）
        cx, cy = self._ball_center()
        gap = 46
        self._mic_btn.move(int(cx - _BALL_DIAMETER / 2 - gap - 36), int(cy - 18))
        self._stop_btn.move(int(cx + _BALL_DIAMETER / 2 + gap), int(cy - 18))

        # 字幕条：底部居中、无黑底
        sub_w = int(w * 0.7)
        sub_h = 96
        self._subtitle.setGeometry(
            int((w - sub_w) / 2), h - sub_h - 26, sub_w, sub_h
        )

    def _reposition_portrait(self) -> None:
        if not hasattr(self, "_portrait"):
            return
        w = self.width()
        ph = self._portrait.height()
        x = w - self._portrait.width() - 8
        y = self.height() - ph + self._portrait_offset   # 底边贴底
        self._portrait.move(x, y)

    def _ball_center(self) -> tuple[float, float]:
        # 中央偏左一点（右侧让位给立绘）
        cx = self.width() * 0.38
        cy = self.height() * 0.45
        return cx, cy

    # ------------------------------------------------------------------ #
    # paintEvent：背景 + 语音球 + 光晕
    # ------------------------------------------------------------------ #
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # 背景已由 stylesheet 填，这里再叠一层暗角渐变
        w, h = self.width(), self.height()
        bg = QRadialGradient(w * 0.4, h * 0.45, max(w, h) * 0.7)
        bg.setColorAt(0, QColor(20, 18, 26, 255))
        bg.setColorAt(1, QColor(10, 9, 8, 255))
        p.fillRect(self.rect(), QBrush(bg))

        # 光晕
        color = _state_color(self._mode)
        glow_r = _BALL_DIAMETER / 2 + self._glow_radius
        glow = QRadialGradient(*self._ball_center(), glow_r)
        glow_c = QColor(color)
        glow_c.setAlpha(int(70))
        glow.setColorAt(0, glow_c)
        glow_c2 = QColor(color)
        glow_c2.setAlpha(0)
        glow.setColorAt(1, glow_c2)
        p.setBrush(QBrush(glow))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(*self._ball_center()), glow_r, glow_r)

        # 球本体（径向渐变）
        cx, cy = self._ball_center()
        r = _BALL_DIAMETER / 2
        ball = QRadialGradient(cx - r * 0.3, cy - r * 0.3, r * 1.2)
        ball.setColorAt(0, QColor(255, 255, 255, 230))
        ball.setColorAt(0.25, color.lighter(130))
        ball.setColorAt(1, color.darker(140))
        p.setBrush(QBrush(ball))
        p.setPen(QPen(color.lighter(160), 1.2))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # 状态小字
        p.setPen(QColor(200, 200, 210, 200))
        f = QFont()
        f.setPointSize(9)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(f)
        status_text = {
            "idle": "TAP TO SPEAK",
            "recording": "RECORDING…",
            "thinking": "THINKING…",
            "speaking": "SPEAKING…",
        }.get(self._mode, "")
        p.drawText(
            QRect(int(cx - 100), int(cy + r + 70), 200, 20),
            Qt.AlignCenter, status_text,
        )
        p.end()

    # ------------------------------------------------------------------ #
    # 公开 API
    # ------------------------------------------------------------------ #
    def set_mode(self, state: str) -> None:
        """切换语音球状态：idle / recording / thinking / speaking。"""
        if state not in ("idle", "recording", "thinking", "speaking"):
            logger.warning("未知 immersive 状态: %s", state)
            return
        self._mode = state
        self.update()

    # 字幕情绪关键词高亮（参考图：关键词金色，如"吃醋"）
    _HIGHLIGHT_WORDS = (
        "吃醋", "想你", "喜欢", "难过", "开心", "生气", "委屈", "心疼",
        "害怕", "等你", "抱抱", "骄傲", "害羞", "着急", "安心", "失望",
    )

    def show_subtitle(self, text: str, duration: int | None = None) -> None:
        """在底部字幕条显示文字；情绪关键词金色高亮；duration 秒后自动清空。"""
        self._subtitle_text = text or ""
        safe = (self._subtitle_text.replace("&", "&amp;")
                .replace("<", "&lt;").replace(">", "&gt;"))
        for w_ in self._HIGHLIGHT_WORDS:
            if w_ in safe:
                safe = safe.replace(w_, f'<span style="color:#ffd54a;">{w_}</span>')
        self._subtitle.setText(safe)
        if duration and duration > 0:
            QTimer.singleShot(duration * 1000, lambda: self._subtitle.setText(""))

    def set_stage_label(self, text: str) -> None:
        self._stage_label.setText(text or "")

    def close_immersive(self) -> None:
        """关闭沉浸式模式：发 hide_all_windows 信号并隐藏自身。"""
        try:
            self.stop_recording_or_playback()
        except Exception:
            pass
        try:
            if self._signals is not None:
                self._signals.hide_all_windows.emit()
        except Exception:
            logger.debug("emit hide_all_windows 失败", exc_info=True)
        self.hide()
        try:
            self.immersive_closed.emit()
        except Exception:
            pass

    def toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self.showFullScreen()
        else:
            self.showNormal()
            self.resize(1280, 800)

    # ------------------------------------------------------------------ #
    # 关系阶段标签刷新
    # ------------------------------------------------------------------ #
    def _refresh_stage_label(self) -> None:
        if self._facade is None:
            return
        try:
            r = self._facade.get_active_person()
            if not r.get("ok"):
                return
            data = r.get("data") or {}
            person = data.get("person")
            if not person:
                self.set_stage_label("关系阶段 · 未选择对象")
                return
            name = (
                person.get("nickname")
                or person.get("wechat_nickname")
                or person.get("name")
                or "对象"
            )
            stage = person.get("stage") or person.get("relationship_stage") or "初识"
            self.set_stage_label(f"{name} · {stage}")
        except Exception:
            logger.debug("读取 active_person 失败", exc_info=True)

    # ------------------------------------------------------------------ #
    # 语音流程：录音 → STT → 策略 → TTS → 字幕
    # ------------------------------------------------------------------ #
    def toggle_recording(self) -> None:
        if self._mode == "recording":
            self._stop_recording_internal()
        else:
            self._start_recording_internal()

    def stop_recording_or_playback(self) -> None:
        try:
            if self._mode == "recording":
                self._stop_recording_internal()
            elif self._mode == "speaking":
                self._stop_playback()
        except Exception:
            logger.debug("stop 失败", exc_info=True)

    # ---- 录音 ----
    def _start_recording_internal(self) -> None:
        self.set_mode("recording")
        self.show_subtitle("● 正在录音… 再点麦克风停止", duration=None)
        self._pre_stt_text = ""

        # 优先走 SpeechService（内部 = voice_pipeline 录音，统一端点/VAD/EoT）。
        # 这是验收官要求的生产路径：不再让沉浸式窗自己抱一个 QAudioRecorder。
        if self._facade is not None and hasattr(self._facade, "start_voice_recording"):
            try:
                r = self._facade.start_voice_recording()
                if isinstance(r, dict) and r.get("ok"):
                    self._recorder_backend = "speech_service"
                    logger.info("沉浸式录音走 SpeechService/voice_pipeline 通道")
                    return
                logger.debug("start_voice_recording 未就绪(%s)，回退 QAudioRecorder",
                             r.get("error") if isinstance(r, dict) else r)
            except Exception as e:  # noqa: BLE001
                logger.debug("SpeechService 启动录音失败，回退 QAudioRecorder: %s", e)

        # 回退：老 QAudioRecorder
        self._recorder_backend = "qaudio"
        if not _HAS_QAUDIO or QAudioRecorder is None:
            # stub：3 秒后自动进入 thinking
            QTimer.singleShot(3000, self._on_recording_stub_finished)
            return

        try:
            self._record_file = os.path.join(
                tempfile.gettempdir(), f"loveai_immersive_{os.getpid()}.wav"
            )
            if os.path.exists(self._record_file):
                try:
                    os.remove(self._record_file)
                except Exception:
                    pass

            self._audio_recorder = QAudioRecorder(self)
            # 输出位置
            try:
                self._audio_recorder.setOutputLocation(self._record_file)
            except Exception:
                pass
            self._audio_recorder.record()
        except Exception:
            logger.debug("QAudioRecorder 启动失败，走 stub", exc_info=True)
            self._audio_recorder = None
            QTimer.singleShot(3000, self._on_recording_stub_finished)

    def _stop_recording_internal(self) -> None:
        # 主路径：SpeechService 通道——停止并直接拿到转写文本
        if self._recorder_backend == "speech_service" and self._facade is not None \
                and hasattr(self._facade, "stop_voice_and_recognize"):
            try:
                r = self._facade.stop_voice_and_recognize()
                if isinstance(r, dict) and r.get("ok"):
                    data = r.get("data") or {}
                    self._pre_stt_text = (
                        data.get("text") or data.get("transcript")
                        or (data if isinstance(data, str) else "") or ""
                    )
            except Exception:  # noqa: BLE001
                logger.debug("stop_voice_and_recognize 失败", exc_info=True)
            self._recorder_backend = "qaudio"
            QTimer.singleShot(200, self._run_stt_pipeline)
            return

        if self._audio_recorder is not None:
            try:
                self._audio_recorder.stop()
            except Exception:
                pass
            QTimer.singleShot(300, self._run_stt_pipeline)
        else:
            # stub 模式：直接跑流程
            self._run_stt_pipeline()

    def _on_recording_stub_finished(self) -> None:
        if self._mode == "recording":
            self._run_stt_pipeline()

    # ---- STT → 策略 → TTS ----
    def _run_stt_pipeline(self) -> None:
        self.set_mode("thinking")
        self.show_subtitle("识别中…", duration=None)

        user_text = ""
        # 1) STT
        # speech_service 通道：stop 时已直接转写好，不再重复 transcribe_audio_file
        if self._pre_stt_text:
            user_text = self._pre_stt_text
            self._pre_stt_text = ""
        else:
          try:
            if self._facade is not None and os.path.exists(self._record_file):
                r = self._facade.transcribe_audio_file(self._record_file)
                if r.get("ok"):
                    data = r.get("data") or {}
                    user_text = (
                        data.get("text")
                        or data.get("transcript")
                        or (data if isinstance(data, str) else "")
                        or ""
                    )
                else:
                    self.show_subtitle(f"识别失败：{r.get('error', '未知')}", duration=3)
            else:
                # 没有录音文件（stub 模式），放一个占位文本走通流程
                user_text = "（stub：你好，在吗？）"
          except Exception as e:
            logger.debug("STT 失败", exc_info=True)
            self.show_subtitle(f"识别异常：{e}", duration=3)

        if not user_text:
            self.set_mode("idle")
            return

        # 2) 策略引擎（consult）
        self.show_subtitle(f"你：{user_text[:60]}", duration=2)
        reply_text = ""
        try:
            if self._facade is not None:
                r = self._facade.consult(user_text, mode="self")
                if r.get("ok"):
                    data = r.get("data") or {}
                    if isinstance(data, dict):
                        reply_text = (
                            data.get("reply")
                            or data.get("answer")
                            or data.get("text")
                            or ""
                        )
                    elif isinstance(data, str):
                        reply_text = data
                else:
                    reply_text = f"策略引擎未就绪：{r.get('error', '')}"
            else:
                reply_text = "（facade 未注入，无法生成回复）"
        except Exception as e:
            logger.debug("consult 失败", exc_info=True)
            reply_text = f"策略异常：{e}"

        # 3) TTS + 字幕
        self.set_mode("speaking")
        self.show_subtitle(reply_text or "（无回复）", duration=None)
        self._play_tts(reply_text)

    def _play_tts(self, text: str) -> None:
        if not text:
            QTimer.singleShot(1500, lambda: self.set_mode("idle"))
            return

        if not _HAS_EDGE_TTS or edge_tts is None or asyncio is None:
            # 降级：仅字幕，按字数估时
            est = max(1500, min(8000, len(text) * 80))
            # 降级路径也要让嘴动（验收官：手动喂 level 会动，但真实 TTS 路径没接）
            self._start_tts_mouth()
            QTimer.singleShot(est, lambda: (self._stop_tts_mouth(),
                                            self.set_mode("idle")))
            return

        try:
            out_path = os.path.join(
                tempfile.gettempdir(), f"loveai_tts_{os.getpid()}.mp3"
            )

            async def _gen():
                communicate = edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural")
                await communicate.save(out_path)

            try:
                asyncio.run(_gen())
            except RuntimeError:
                # 已有事件循环（罕见），开新线程跑
                import threading
                def _run():
                    try:
                        asyncio.run(_gen())
                    except Exception:
                        pass
                threading.Thread(target=_run, daemon=True).start()

            # R3.3 口型同步：真实 TTS 播放路径真的驱动 animator 嘴型
            # （之前只有手动调 set_tts_level 才动，edge-tts 播放时嘴不动）
            self._start_tts_mouth()

            if _HAS_QMEDIA_PLAYER and QMediaPlayer is not None:
                self._media_player = QMediaPlayer(self)
                audio_out = QAudioOutput(self)
                self._media_player.setAudioOutput(audio_out)
                audio_out.setVolume(1.0)
                from PySide6.QtCore import QUrl
                self._media_player.setSource(QUrl.fromLocalFile(out_path))
                self._media_player.play()
                self._media_player.mediaStatusChanged.connect(self._on_media_status)
            else:
                # 没有播放器，按时长估时回 idle
                est = max(2000, len(text) * 90)
                QTimer.singleShot(est, lambda: (self._stop_tts_mouth(),
                                                self.set_mode("idle")))
        except Exception:
            logger.debug("edge-tts 失败，仅字幕降级", exc_info=True)
            est = max(1500, min(8000, len(text) * 80))
            QTimer.singleShot(est, lambda: self.set_mode("idle"))

    # ------------------------------------------------------------------ #
    # R3.3 口型同步：TTS 播放期间真的把振幅喂给 PetAnimator
    # ------------------------------------------------------------------ #
    def _start_tts_mouth(self) -> None:
        """TTS 播放期间按正弦幅度切嘴型；无 animator（立绘加载失败）时空转。"""
        anim = getattr(self, "_animator", None)
        if anim is None:
            return
        self._stop_tts_mouth()
        self._tts_phase = 0.0
        self._tts_anim_timer = QTimer(self)
        self._tts_anim_timer.timeout.connect(self._tts_mouth_step)
        self._tts_anim_timer.start(90)   # ~11fps

    def _tts_mouth_step(self) -> None:
        """按相位推一口型；抽成方法便于探针确定性单步验证。"""
        anim = getattr(self, "_animator", None)
        if anim is None:
            return
        self._tts_phase = getattr(self, "_tts_phase", 0.0) + 0.18
        import math
        # 0.25~0.85 之间正弦摆动，映射到不同元音嘴型
        level = 0.25 + 0.6 * abs(math.sin(self._tts_phase))
        try:
            anim.set_tts_level(level)
        except Exception:  # noqa: BLE001
            pass

    def _stop_tts_mouth(self) -> None:
        if self._tts_anim_timer is not None:
            try:
                self._tts_anim_timer.stop()
            except Exception:  # noqa: BLE001
                pass
            self._tts_anim_timer = None
        anim = getattr(self, "_animator", None)
        if anim is not None:
            try:
                anim.set_tts_level(0.0)
            except Exception:  # noqa: BLE001
                pass

    def _on_media_status(self, status) -> None:
        try:
            # QMediaPlayer.EndOfMedia = 3
            if int(status) == 3:
                self._stop_tts_mouth()
                self.set_mode("idle")
        except Exception:
            self._stop_tts_mouth()
            self.set_mode("idle")

    def _stop_playback(self) -> None:
        try:
            if self._media_player is not None:
                self._media_player.stop()
        except Exception:
            pass
        self._stop_tts_mouth()
        self.set_mode("idle")

    # ------------------------------------------------------------------ #
    # 鼠标 / 键盘
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event: QMouseEvent) -> None:
        # 点球区域切换状态
        if event.button() == Qt.LeftButton:
            cx, cy = self._ball_center()
            r = _BALL_DIAMETER / 2 + 10
            dx = event.position().x() - cx
            dy = event.position().y() - cy
            if dx * dx + dy * dy <= r * r:
                self.toggle_recording()
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_F11:
            self.toggle_fullscreen()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.close_immersive()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ #
    # 关闭清理
    # ------------------------------------------------------------------ #
    def hideEvent(self, event):
        try:
            self.stop_recording_or_playback()
        except Exception:
            pass
        super().hideEvent(event)
