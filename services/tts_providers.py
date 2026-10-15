# -*- coding: utf-8 -*-
"""
LoveAI-v2 语音合成（TTS）Provider 抽象
=======================================
- EdgeTTSProvider：默认，微软 edge-tts（zh-CN-XiaoxiaoNeural），已跑通。
- CloneTTSProvider：语音克隆（P1）。不本地训练大模型，而是对接一个**已经在跑的
  克隆推理服务**（GPT-SoVITS / CosyVoice 自带的 HTTP API）。用户自备模型与 10~30s
  干净人声样本，把服务地址填进环境变量即可让桌宠拥有专属声音。

为什么做成「外置服务」而不是把 GPT-SoVITS 装进本 venv：
  GPT-SoVITS / CosyVoice 需要 Python 3.10~3.12 + torch + 数 GB 模型，
  本项目 venv 是 Python 3.14，强行装会污染整个环境且编译极慢。
  用 HTTP 接口解耦：克隆服务可以跑在任意独立环境/另一台机器，
  本桥接只负责「POST 文本 → 拿回音频文件」。

环境变量：
- LOVEAI_TTS_BACKEND   edge|clone|auto（默认 auto：clone 可达就用 clone，否则 edge）
- LOVEAI_CLONE_URL     克隆推理服务地址，默认 http://127.0.0.1:9880
                       （GPT-SoVITS api_v2 / CosyVoice fastapi 风格）
- LOVEAI_CLONE_TEXT    要克隆的参考文本（参考音频说了什么）
- LOVEAI_CLONE_AUDIO   参考音频文件路径（10~30s 干净人声 wav）
- LOVEAI_CLONE_VOICE   edge-tts 女声名（edge 后端用），默认 zh-CN-XiaoxiaoNeural
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("LoveAI.tts_provider")


class TTSProvider:
    name = "base"

    async def synthesize(self, text: str, out_path: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def available(self) -> bool:
        return True


class EdgeTTSProvider(TTSProvider):
    name = "edge"

    def __init__(self, voice: Optional[str] = None) -> None:
        self.voice = voice or os.environ.get("LOVEAI_CLONE_VOICE", "zh-CN-XiaoxiaoNeural")

    async def synthesize(self, text: str, out_path: str) -> str:
        from services.edge_tts_service import EdgeTTSService
        svc = EdgeTTSService(voice=self.voice)
        return await svc.synthesize(text, out_path)


class CloneTTSProvider(TTSProvider):
    """对接外置语音克隆 HTTP 服务（GPT-SoVITS api_v2 / CosyVoice）。"""
    name = "clone"

    def __init__(
        self,
        url: Optional[str] = None,
        ref_text: Optional[str] = None,
        ref_audio: Optional[str] = None,
    ) -> None:
        self.url = (url or os.environ.get("LOVEAI_CLONE_URL", "http://127.0.0.1:9880")).rstrip("/")
        self.ref_text = ref_text or os.environ.get("LOVEAI_CLONE_TEXT", "")
        self.ref_audio = ref_audio or os.environ.get("LOVEAI_CLONE_AUDIO", "")

    def available(self) -> bool:
        import urllib.request
        # 探活：克隆服务根路径通常 200/404，只要能连通就算可用
        try:
            urllib.request.urlopen(self.url, timeout=1.5)
            return True
        except Exception:
            # 404 也算“端口在听”
            try:
                urllib.request.urlopen(self.url + "/", timeout=1.5)
                return True
            except Exception:
                return False

    async def synthesize(self, text: str, out_path: str) -> str:
        import aiohttp
        # GPT-SoVITS api_v2 协议；CosyVoice 用户可自行改本函数的 payload 字段。
        payload = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": self.ref_audio,
            "prompt_text": self.ref_text,
            "prompt_lang": "zh",
            "speed_factor": 1.0,
            "media_type": "wav",
        }
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.url + "/tts", json=payload) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"克隆 TTS 服务返回 {resp.status}")
                data = await resp.read()
        if len(data) < 1024:
            raise RuntimeError(f"克隆 TTS 输出过小 ({len(data)} bytes)")
        Path(out_path).write_bytes(data)
        logger.info("克隆 TTS: %r -> %s (%d bytes)", text[:20], out_path, len(data))
        return out_path


def build_tts_provider(prefer: Optional[str] = None) -> TTSProvider:
    prefer = (prefer or os.environ.get("LOVEAI_TTS_BACKEND", "auto")).strip().lower()
    if prefer == "edge":
        return EdgeTTSProvider()
    if prefer == "clone":
        return CloneTTSProvider()
    # auto：clone 可达就用 clone，否则 edge
    clone = CloneTTSProvider()
    try:
        if clone.available():
            logger.info("TTS 后端选择: clone（外置克隆服务已连通）")
            return clone
    except Exception:
        pass
    logger.info("TTS 后端选择: edge-tts（默认）")
    return EdgeTTSProvider()
