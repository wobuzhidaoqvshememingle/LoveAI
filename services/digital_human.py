# -*- coding: utf-8 -*-
"""
LoveAI-v2 数字人（P3）可选模块
================================
目标：真人照片 + 音频 → 会说话的数字人视频。做成**可选项**——
配置高（有 GPU + 装好了推理服务）才启用，否则自动降级回现在的 2D Live2D/立绘口型。

为什么不直接在本 venv 装 SadTalker/Wav2Lip：
  - 这俩都依赖 torch（CPU wheel 约 200MB+，CUDA 更大），本 venv 是 Python 3.14，
    torch / face-alignment / face_detection 的预编译 wheel 未必齐全，装起来极易踩坑；
  - Wav2Lip/SadTalker 推理对显存敏感，本机 GTX 1050 4GB 跑实时数字人勉强，
    更适合做成「帧序列推送」而不是真实时。
  - 因此这里只定义干净接口 + 一个外置数字人服务的 HTTP 对接，
    并在没装/没配时明确 report mode="degraded_2d"，桌宠继续用 2D 口型。

对接方式（与语音克隆同理，解耦）：
  跑一个独立的数字人推理服务（Wav2Lip/SadTalker/MuseTalk 的 fastapi 包装），
  POST {image_url, audio_url} -> 返回渲染好的视频/帧目录。桥接只负责转发。

环境变量：
- LOVEAV_DH_ENABLE   1/0，默认 auto（探测到服务才启用）
- LOVEAI_DH_URL      数字人推理服务地址，默认 http://127.0.0.1:9881
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("LoveAI.digital_human")


class DigitalHumanProvider:
    """数字人 provider 基类。"""
    mode = "base"

    def available(self) -> bool:
        return False

    async def render(self, image_path: str, audio_path: str) -> Optional[str]:
        """渲染一段说话视频，返回产物 URL/路径；不支持则返回 None（调用方降级 2D）。"""
        return None


class Degraded2D(DigitalHumanProvider):
    """降级：不渲染真数字人，桌宠用 Live2D/序列帧口型。"""
    mode = "degraded_2d"

    def available(self) -> bool:
        return True

    async def render(self, image_path: str, audio_path: str) -> Optional[str]:
        return None


class RemoteDigitalHuman(DigitalHumanProvider):
    """对接外置数字人推理服务（Wav2Lip/MuseTalk/SadTalker 的 fastapi 包装）。"""
    mode = "remote_video"

    def __init__(self, url: Optional[str] = None) -> None:
        self.url = (url or os.environ.get("LOVEAI_DH_URL", "http://127.0.0.1:9881")).rstrip("/")

    def available(self) -> bool:
        flag = os.environ.get("LOVEAI_DH_ENABLE", "auto").strip().lower()
        if flag == "0":
            return False
        import urllib.request
        try:
            urllib.request.urlopen(self.url, timeout=1.5)
            return True
        except Exception:
            return False

    async def render(self, image_path: str, audio_path: str) -> Optional[str]:
        import aiohttp
        payload = {"image_path": image_path, "audio_path": audio_path}
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.url + "/render", json=payload) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("video_url")


def build_digital_human() -> DigitalHumanProvider:
    remote = RemoteDigitalHuman()
    try:
        if remote.available():
            logger.info("数字人模式: remote_video（外置推理服务已连通）")
            return remote
    except Exception:
        pass
    logger.info("数字人模式: degraded_2d（未配置/未装数字人推理服务，沿用 2D 口型）")
    return Degraded2D()
