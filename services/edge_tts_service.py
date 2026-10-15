"""edge-tts 语音合成服务封装。

独立模块：仅依赖 edge-tts 异步库，不依赖 Qt / PySide6 / 主程序。
对外暴露 synthesize(text, out_path) 协程方法，输出 MP3 文件。
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

import edge_tts

logger = logging.getLogger(__name__)


class EdgeTTSService:
    """edge-tts 封装。默认语音 zh-CN-XiaoxiaoNeural（温柔女声）。"""

    DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
    DEFAULT_RATE = "+0%"
    DEFAULT_PITCH = "+0Hz"

    def __init__(
        self,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
        pitch: Optional[str] = None,
    ) -> None:
        self.voice = voice or os.environ.get("EDGE_TTS_VOICE", self.DEFAULT_VOICE)
        self.rate = rate or os.environ.get("EDGE_TTS_RATE", self.DEFAULT_RATE)
        self.pitch = pitch or os.environ.get("EDGE_TTS_PITCH", self.DEFAULT_PITCH)
        logger.info(
            "EdgeTTSService 初始化: voice=%s rate=%s pitch=%s",
            self.voice, self.rate, self.pitch,
        )

    async def synthesize(self, text: str, out_path: str | os.PathLike) -> str:
        """把 text 合成 MP3 写到 out_path，返回 out_path 字符串。"""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if not text or not text.strip():
            raise ValueError("synthesize: text 不能为空")

        communicate = edge_tts.Communicate(
            text=text,
            voice=self.voice,
            rate=self.rate,
            pitch=self.pitch,
        )
        # edge-tts 的 .save 是异步方法
        await communicate.save(str(out_path))

        size = out_path.stat().st_size if out_path.exists() else 0
        logger.info(
            "edge-tts 合成完成: text=%r -> %s (%d bytes)",
            text[:30], out_path, size,
        )
        if size < 1024:
            raise RuntimeError(f"edge-tts 输出文件过小: {out_path} ({size} bytes)")
        return str(out_path)


if __name__ == "__main__":
    # 简单冒烟测试：直接跑 python services/edge_tts_service.py
    logging.basicConfig(level=logging.INFO)
    async def _demo():
        svc = EdgeTTSService()
        out = await svc.synthesize("你好呀，今天过得怎么样？", "data/voice_cache/_selftest.mp3")
        print("OK ->", out)
    asyncio.run(_demo())
