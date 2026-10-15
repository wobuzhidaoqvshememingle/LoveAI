# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 桌宠 2D 序列帧动画控制器（R3.3-51）

用 CPU 轻量 QTimer 驱动以下动画：
  - 呼吸：立绘 Y 轴缩放 1.0 <-> 1.02，周期 3s（正弦波）
  - 眨眼：每 3-5s 随机触发一次闭眼，持续 200ms
  - 表情：happy / sad / thinking / surprised / neutral 5 种整脸切换
  - 口型同步：根据 TTS 输出音频振幅 (0..1) 切换 a/i/u/e/o 5 种嘴型帧

R3.3-82 连续帧升级（Electron 桌宠侧）：
  - 本类继续输出离散 mouth 名（向后兼容），同时新增连续 mouth_open (0..1) 与
    blink_phase (0..1)，供连续帧渲染器（PIXI 关键帧+交叉淡化）做平滑插值。
  - Electron 端眨眼多帧步进、嘴型 6 关键帧循环在渲染器内完成，本类只给状态。

设计原则：
  - 本类只维护「动画状态」，不直接画 UI；通过 frame_changed 信号把状态推给 widget。
  - 不 import 任何 GPU/3D 库，纯 QTimer + 数学函数，offscreen 可测。
  - 序列帧路径全部指向 assets/pet_sprites/，文件缺失时优雅降级（返回 None，widget 画占位）。
"""
from __future__ import annotations

import logging
import math
import os
import random
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)

# 项目根目录 = 本文件上两级（LoveAI-v2/services/pet_animator.py -> LoveAI-v2/）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SPRITE_DIR = os.path.join(_PROJECT_ROOT, "assets", "pet_sprites")

# 合法枚举
EXPRESSIONS: List[str] = ["neutral", "happy", "sad", "thinking", "surprised"]
VOWELS: List[str] = ["a", "i", "u", "e", "o"]

# 动画参数（秒）
BREATH_PERIOD_S = 3.0        # 呼吸周期
BREATH_AMPLITUDE = 0.02       # 呼吸幅度（Y 缩放 1.00 ~ 1.02）
BLINK_MIN_S = 3.0             # 眨眼最小间隔
BLINK_MAX_S = 5.0             # 眨眼最大间隔
BLINK_DUR_S = 0.20            # 闭眼持续
TICK_MS = 33                  # 主循环 ~30fps


class PetAnimator(QObject):
    """2D 序列帧动画控制器。

    Usage:
        animator = PetAnimator(sprite_dir=...)
        animator.frame_changed.connect(your_widget.on_frame)
        animator.start()
        animator.set_expression("happy")
        animator.set_tts_level(0.7)   # TTS 播放中，按振幅切嘴型
    """

    #: 帧状态变化信号，参数是 dict：
    #: {
    #:   "scale_y": float,           # 呼吸 Y 缩放 (0.98~1.04)
    #:   "eyes": "open"|"closed",
    #:   "expression": str,          # neutral/happy/...
    #:   "mouth": str,               # a/i/u/e/o 或 "rest"（闭嘴）
    #:   "sprites": {name: abspath}  # 当前可用的帧路径表（启动时加载一次）
    #: }
    frame_changed = Signal(dict)

    def __init__(self, sprite_dir: Optional[str] = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.sprite_dir = sprite_dir or _DEFAULT_SPRITE_DIR
        self.sprites: Dict[str, str] = {}
        self._load_sprites()

        # 状态
        self._expression: str = "neutral"
        self._eyes: str = "open"
        self._mouth: str = "rest"
        self._tts_level: float = 0.0
        self._running: bool = False

        # R3.3-82：连续帧渲染用的连续量（新增，不影响既有离散 API）
        self._mouth_open: float = 0.0   # 0=闭嘴 1=最大张嘴
        self._blink_phase: float = 0.0  # 0=睁眼 1=闭眼

        # 呼吸相位（秒）
        self._breath_t: float = 0.0

        # 眨眼调度
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._start_blink)
        self._blink_hold_timer = QTimer(self)
        self._blink_hold_timer.setSingleShot(True)
        self._blink_hold_timer.timeout.connect(self._end_blink)

        # 主 tick
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------
    # 帧资源加载
    # ------------------------------------------------------------------
    def _load_sprites(self) -> None:
        """扫描 sprite_dir，把存在的帧文件路径存进 self.sprites。"""
        self.sprites.clear()
        if not os.path.isdir(self.sprite_dir):
            logger.warning("sprite dir not found: %s", self.sprite_dir)
            return
        for name in (
            EXPRESSIONS
            + ["eyes_open", "eyes_closed"]
            + [f"mouth_{v}" for v in VOWELS]
        ):
            p = os.path.join(self.sprite_dir, f"{name}.png")
            if os.path.exists(p):
                self.sprites[name] = p
            else:
                logger.debug("sprite missing: %s", p)

    # ------------------------------------------------------------------
    # 外部接口
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tick_timer.start()
        self._schedule_next_blink()
        self._emit()

    def stop(self) -> None:
        self._running = False
        self._tick_timer.stop()
        self._blink_timer.stop()
        self._blink_hold_timer.stop()

    def is_running(self) -> bool:
        return self._running

    def set_expression(self, expr: str) -> None:
        """切换表情。非法值忽略并保留当前。"""
        if expr in EXPRESSIONS:
            self._expression = expr
            self._emit()
        else:
            logger.debug("ignored unknown expression: %s", expr)

    def set_tts_level(self, level: float) -> None:
        """喂入 TTS 播放音量 (0..1)，自动映射到嘴型。

        level<=0.05 视为闭嘴（rest）；其余按振幅区间映射到 5 个元音。
        """
        try:
            lv = float(level)
        except (TypeError, ValueError):
            lv = 0.0
        lv = max(0.0, min(1.0, lv))
        self._tts_level = lv
        self._mouth = self._level_to_mouth(lv)
        # R3.3-82：连续张嘴度，供连续帧渲染器直接插值
        self._mouth_open = 0.0 if lv <= 0.05 else min(1.0, (lv - 0.05) / 0.95)

    # ------------------------------------------------------------------
    # 内部：动画计算
    # ------------------------------------------------------------------
    @staticmethod
    def _level_to_mouth(lv: float) -> str:
        if lv <= 0.05:
            return "rest"
        # 把 0.05~1.0 分成 5 段，映射 a/i/u/e/o
        idx = min(len(VOWELS) - 1, int((lv - 0.05) / 0.19))
        return VOWELS[idx]

    def _schedule_next_blink(self) -> None:
        delay_ms = int(random.uniform(BLINK_MIN_S, BLINK_MAX_S) * 1000)
        self._blink_timer.start(delay_ms)

    def _start_blink(self) -> None:
        self._eyes = "closed"
        self._blink_phase = 1.0  # R3.3-82：连续帧渲染用
        self._emit()
        self._blink_hold_timer.start(int(BLINK_DUR_S * 1000))

    def _end_blink(self) -> None:
        self._eyes = "open"
        self._blink_phase = 0.0
        self._emit()
        self._schedule_next_blink()

    def _tick(self) -> None:
        # 呼吸相位推进（TICK_MS 秒一步）
        self._breath_t += TICK_MS / 1000.0
        # 正弦缩放：0 -> 1.0 -> 1.02 -> 1.0 -> ...
        phase = (self._breath_t % BREATH_PERIOD_S) / BREATH_PERIOD_S
        scale_y = 1.0 + BREATH_AMPLITUDE * (0.5 - 0.5 * math.cos(2.0 * math.pi * phase))
        self._scale_y = scale_y
        self._emit()

    def _emit(self) -> None:
        if not self._running:
            # 即使没在跑，也允许外部查询一次当前状态（不发信号）
            return
        self.frame_changed.emit({
            "scale_y": getattr(self, "_scale_y", 1.0),
            "eyes": self._eyes,
            "expression": self._expression,
            "mouth": self._mouth,
            "sprites": dict(self.sprites),
        })

    # ------------------------------------------------------------------
    # 状态查询（测试用，不发信号）
    # ------------------------------------------------------------------
    def current_state(self) -> dict:
        return {
            "scale_y": getattr(self, "_scale_y", 1.0),
            "eyes": self._eyes,
            "expression": self._expression,
            "mouth": self._mouth,
            "tts_level": self._tts_level,
            # R3.3-82 连续帧渲染用（新增）
            "mouth_open": self._mouth_open,
            "blink_phase": self._blink_phase,
            "sprites": dict(self.sprites),
        }
