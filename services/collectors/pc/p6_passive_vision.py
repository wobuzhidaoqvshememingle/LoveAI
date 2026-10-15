# -*- coding: utf-8 -*-
"""P6 被动只读视觉采集：用户自己点、自己翻，工具绝不模拟任何点击/滚动。

仅按节奏在后台截取微信窗口画面，走本地 OCR（rapidocr）解析气泡，默认不上传任何截图。
两种模式：
  * folder 模式（离线、可 fixture 自测）：对已截好的图片目录逐张 OCR。
  * live 模式（被动）：按 interval 间隔后台抓屏，用户自行切换会话/滚动历史，
    工具只看不碰——不点击、不滚动、不输入。

合规：截图/录屏类操作 2026-01 起被微信列入违规打击，本通道只做低频被动观察，
单轮限量、不 7×24 常驻；本地 OCR，默认不外传。
"""
from __future__ import annotations

import logging
import os
import time
import random
from typing import Callable, List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_LOW,
                    PLAT_PC, ok, missing)
from .. import env, vision

logger = logging.getLogger(__name__)

_IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

# 被动抓屏节奏（秒）：低频、拟人、单轮限量，绝不 7×24
DEFAULT_INTERVAL = 6.0          # 默认抓屏间隔
DEFAULT_MAX_CAPTURES = 15       # 单轮最多抓多少张（约 1.5 分钟）
PAUSE_JITTER = (0.8, 2.0)       # 每次抓屏前后随机停顿，避免机械节奏


class P6PassiveVisionAdapter(SourceAdapter):
    id = "P6_passive_vision"
    name = "被动只读视觉采集(后台截图+本地OCR)"
    platform = PLAT_PC
    requires = frozenset({"vision"})
    risk_level = RISK_LOW
    default_enabled = True
    local_runnable = True
    success_prior = 0.6
    produces_plaintext = True

    # ------------------------------------------------------------------ #
    # 可用性：本地 OCR 必须可用；且（给了 folder）或（检测到微信窗口）
    # ------------------------------------------------------------------ #
    def available(self, folder: Optional[str] = None) -> Availability:
        if not env.ocr_available():
            return missing(
                "本地 OCR 引擎不可用",
                ["安装本地 OCR：pip install rapidocr-onnxruntime",
                 "或在设置中配置可用的本地视觉模型（默认不上传截图）"])
        has_window = bool(env.wechat_processes())
        if folder and os.path.isdir(folder):
            return ok(
                f"本地 OCR 可用，截图目录就绪：{folder}",
                mode="folder", folder=folder, wechat_window=has_window)
        if has_window:
            return ok(
                "本地 OCR 可用，检测到微信进程，可被动后台截图（用户自行翻页）",
                mode="live", wechat_window=True)
        return missing(
            "本地 OCR 可用，但既未提供截图目录、也未检测到运行中的微信窗口",
            ["传 folder=<已截好的聊天截图目录> 做离线解析",
             "或登录并打开微信聊天窗口后用 collect(live=True) 被动抓屏",
             "注意：本通道不点击/不滚动，需用户自己翻到目标会话"])

    # ------------------------------------------------------------------ #
    # 采集
    # ------------------------------------------------------------------ #
    def collect(self, folder: Optional[str] = None, live: bool = False,
                interval: float = DEFAULT_INTERVAL,
                max_captures: int = DEFAULT_MAX_CAPTURES,
                out_dir: Optional[str] = None,
                peer_name: Optional[str] = None,
                grabber: Optional[Callable[[str], str]] = None,
                sleeper: Optional[Callable[[float], None]] = None,
                **opts) -> List[Message]:
        if live:
            return self._collect_live(interval=interval,
                                      max_captures=max_captures,
                                      out_dir=out_dir or self._default_out_dir(),
                                      peer_name=peer_name,
                                      grabber=grabber, sleeper=sleeper)
        if not folder:
            raise ValueError("P6 离线模式需要 folder=<截图目录>；实时被动模式用 live=True")
        return self._parse_folder(folder, peer_name=peer_name)

    # 离线：对已截好的图片目录逐张 OCR（fixture 自测走这里）
    def _parse_folder(self, folder: str,
                      peer_name: Optional[str] = None) -> List[Message]:
        paths = self._gather(folder)
        out: List[Message] = []
        for p in paths:
            try:
                width = vision.image_width_of(p)
                result = vision.ocr_image(p)
                msgs = vision.parse_chat_ocr(result, width, source_id=self.id,
                                             peer_name=peer_name)
                out.extend(msgs)
            except Exception as e:  # 单张失败不阻断整批
                logger.warning("P6 OCR 跳过 %s: %s", p, e)
        return out

    # 实时被动：只按间隔抓屏，绝不操作微信
    def _collect_live(self, interval: float, max_captures: int,
                      out_dir: str, peer_name: Optional[str],
                      grabber: Optional[Callable[[str], str]],
                      sleeper: Optional[Callable[[float], None]]) -> List[Message]:
        grab = grabber or self._default_grab
        sleep = sleeper or time.sleep
        os.makedirs(out_dir, exist_ok=True)
        n = max(1, min(int(max_captures), DEFAULT_MAX_CAPTURES))
        out: List[Message] = []
        for i in range(n):
            try:
                path = grab(out_dir)
            except Exception as e:
                logger.warning("P6 第 %d 次抓屏失败（可能无截图后端/窗口最小化）：%s", i, e)
                break
            if not path or not os.path.isfile(path):
                break
            try:
                width = vision.image_width_of(path)
                result = vision.ocr_image(path)
                out.extend(vision.parse_chat_ocr(
                    result, width, source_id=self.id, peer_name=peer_name))
            except Exception as e:
                logger.warning("P6 解析 %s 失败：%s", path, e)
            if i < n - 1:
                sleep(interval + random.uniform(*PAUSE_JITTER))
        # 跨屏重复气泡由上层 store / P7.merge_messages 去重，这里不臆造时间
        return out

    # ------------------------------------------------------------------ #
    # 工具方法
    # ------------------------------------------------------------------ #
    @staticmethod
    def _gather(folder: str) -> List[str]:
        if not folder or not os.path.isdir(folder):
            return []
        out = [os.path.join(folder, fn) for fn in sorted(os.listdir(folder))
               if fn.lower().endswith(_IMG_EXT)]
        return out

    @staticmethod
    def _default_out_dir() -> str:
        base = os.path.join(env.project_root(), "tmp", "p6_passive_shots")
        return os.path.join(base, time.strftime("%Y%m%d_%H%M%S"))

    @staticmethod
    def _default_grab(out_dir: str) -> str:
        """默认被动抓屏：整屏截图（不激活/不点击微信窗口）。

        优先 mss，其次 PIL.ImageGrab。抓屏内容默认只存本地 out_dir，不上传。
        """
        idx = getattr(P6PassiveVisionAdapter, "_grab_counter", 0) + 1
        P6PassiveVisionAdapter._grab_counter = idx
        path = os.path.join(out_dir, f"passive_{idx:03d}.png")
        try:
            import mss  # type: ignore
            import mss.tools  # type: ignore
            with mss.mss() as sct:
                mon = sct.monitors[0]
                shot = sct.grab(mon)
                mss.tools.to_png(shot.rgb, shot.size, output=path)
            return path
        except Exception:
            pass
        from PIL import ImageGrab  # type: ignore
        img = ImageGrab.grab()
        img.save(path)
        return path
