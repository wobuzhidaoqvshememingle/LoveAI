# -*- coding: utf-8 -*-
"""
LoveAI-v2 本地语音识别（STT）服务
================================
基于 faster-whisper（ctranslate2，纯 CPU int8），不依赖网络 API / torch。

关键设计：**绕过 PyAV 解码**。faster-whisper 1.2.1 的 audio.py 调
`av.open(..., metadata_errors="ignore")`，而本机 av19 不认这个参数会直接报错。
因此本服务自己把音频读成 float32 mono numpy 数组再喂给 whisper（whisper 支持
ndarray 输入，会跳过 decode_audio），彻底不碰 av。

输入两种：
- transcribe_pcm16(pcm_bytes, 16000)：桥接录音上行的原始 16k PCM16，直接转 float32。
- transcribe_wav(path)：自己用 wave 模块读 wav 转 float32。

模型：base（约 145MB，首次从 hf-mirror 下载一次后缓存）。
环境变量：
- LOVEAI_STT_MODEL  默认 base（中文建议 small，更准但更慢）
- LOVEAI_STT_LANG   默认 zh
"""
from __future__ import annotations

import logging
import os
import threading
import wave
from typing import Optional

# 国内网络：HF 镜像 + 禁 xet（xet 对镜像报 401）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import numpy as np  # noqa: E402

logger = logging.getLogger("LoveAI.stt")


class WhisperSTT:
    """faster-whisper 本地中文识别。线程安全（一把锁包住推理）。"""

    def __init__(
        self,
        model_size: Optional[str] = None,
        language: Optional[str] = None,
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self.model_size = model_size or os.environ.get("LOVEAI_STT_MODEL", "base")
        self.language = language or os.environ.get("LOVEAI_STT_LANG", "zh")
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()
        self._tried_load = False

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("faster-whisper 不可用，STT 降级: %s", e)
            return False

    def status(self) -> dict:
        """可观测状态：区分「依赖没装」与「模型权重没下」两类不可用。

        返回 {"available": bool, "reason": str|None, "hint": str|None}。
        上层（语音桥/设置页）据此给用户明确提示，而不是静默返回空文本。
        """
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except Exception as e:  # noqa: BLE001
            return {"available": False, "reason": f"依赖未安装: {e}",
                    "hint": "请安装依赖：pip install faster-whisper==1.2.1"}
        try:
            from huggingface_hub import try_to_load_from_cache  # type: ignore
            cached = try_to_load_from_cache(
                f"Systran/faster-whisper-{self.model_size}", "model.bin")
            if cached is None or isinstance(cached, str) and cached.startswith("O_"):
                return {"available": False, "reason": f"模型权重未下载: whisper-{self.model_size}",
                        "hint": "首次使用需联网下载权重（约150MB），或运行 scripts/download_stt_model.py 预下"}
        except ImportError:
            pass  # 老版本 huggingface_hub 无该 API，交由加载阶段兜底
        except Exception:
            pass  # 缓存探测本身失败不作为不可用依据
        return {"available": True, "reason": None, "hint": None}

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        if self._tried_load:
            return None
        self._tried_load = True
        try:
            from faster_whisper import WhisperModel
            logger.info("加载 Whisper 模型: %s (%s/%s)…", self.model_size, self.device, self.compute_type)
            self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            logger.info("Whisper 模型加载完成")
        except Exception as e:  # noqa: BLE001
            logger.error("Whisper 模型加载失败: %s", e)
            self._model = None
        return self._model

    # ------------------------------------------------------------------ #
    # 直接喂 float32 mono numpy（16kHz），绕过 PyAV
    # ------------------------------------------------------------------ #
    def _transcribe_array(self, audio: np.ndarray) -> str:
        model = self._ensure_model()
        if model is None or audio.size == 0:
            return ""
        audio = np.ascontiguousarray(audio, dtype=np.float32)  # VAD onnx 要 float32
        with self._lock:
            try:
                segments, info = model.transcribe(
                    audio,  # float32 mono @16k
                    language=self.language,
                    vad_filter=True,
                    beam_size=1,
                )
                text = "".join(seg.text for seg in segments).strip()
                logger.info("STT 结果: %r", text)
                return text
            except Exception as e:  # noqa: BLE001
                logger.warning("STT 转写失败: %s", e)
                return ""

    def transcribe_pcm16(self, pcm_bytes: bytes, sample_rate: int = 16000) -> str:
        """内存里的 16kHz 单声道 PCM16 bytes -> 文本。"""
        if not pcm_bytes:
            return ""
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return self._transcribe_array(samples)

    def transcribe_wav(self, wav_path: str) -> str:
        """读 wav 文件（自己用 wave 模块），转 float32 16k mono 再喂 whisper。"""
        try:
            with wave.open(wav_path, "rb") as wf:
                sr = wf.getframerate()
                n = wf.getnframes()
                raw = wf.readframes(n)
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
        except Exception as e:  # noqa: BLE001
            logger.warning("读 wav 失败 %s: %s", wav_path, e)
            return ""
        if sw != 2:
            logger.warning("STT 暂只支持 PCM16 wav (samplewidth=%s)", sw)
            return ""
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            data = data.reshape(-1, ch).mean(axis=1)
        # 重采样到 16k（简单线性）
        if sr != 16000:
            idx = np.arange(0, len(data), sr / 16000.0)
            idx = idx[idx < len(data) - 1]
            x0 = idx.astype(np.int64)
            frac = idx - x0
            data = data[x0] * (1 - frac) + data[np.clip(x0 + 1, 0, len(data) - 1)] * frac
        return self._transcribe_array(data)


_singleton: Optional[WhisperSTT] = None
_singleton_lock = threading.Lock()


def get_stt() -> WhisperSTT:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = WhisperSTT()
        return _singleton


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    stt = WhisperSTT()
    print("available =", stt.available())
    if len(sys.argv) > 1:
        print("转写:", stt.transcribe_wav(sys.argv[1]))
