"""
LoveAI v2.0 - 语音识别服务

基于 faster-whisper 的本地离线语音识别，集成录音控制与微信文本填入。
所有重依赖（faster-whisper / pyaudio / sounddevice）均为可选，
未安装时优雅降级，不影响主程序启动。

设计约束：
- 本模块不继承 QObject，不 import PySide6；signals 通过构造参数注入。
- faster-whisper / pyaudio / sounddevice 全部 try/except 懒加载。
- 临时录音文件用 tempfile 生成，用完可清理。
- 服务层不直接操作 UI，识别结果通过 wechat_service.fill_text() 填入。
"""
import logging
import os
import subprocess
import sys
import tempfile
import threading
import wave
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SpeechService:
    """语音识别服务：本地 Whisper 转写 + 录音控制 + 微信填入。

    典型调用链路（悬浮球语音按钮）：
        1. start_recording()   — 开始录音
        2. stop_recording()    — 停止并存为临时 WAV
        3. transcribe_and_fill() — 识别文字并填入微信输入框

    所有重依赖均为可选：
    - faster-whisper 未安装时，transcribe 返回空文本 + error；
    - pyaudio / sounddevice 均未安装时，start_recording 返回错误。
    """

    #: 默认录音采样率（Hz）
    _DEFAULT_SAMPLE_RATE = 16000
    #: 每次读取的音频帧数（pyaudio stream.read 参数）
    _CHUNK_SIZE = 1024
    #: 录音线程 join 超时（秒）
    _RECORD_JOIN_TIMEOUT = 2.0

    def __init__(
        self,
        db: Any = None,
        config: Any = None,
        wechat_service: Any = None,
        signals: Any = None,
    ) -> None:
        """初始化语音服务。

        Args:
            db: Database 实例（预留，当前不直接读写库；如需写历史可在此扩展）。
            config: ConfigService 实例，读取 speech.* 配置项。
            wechat_service: WeChatService 实例，提供 fill_text(text) 方法。
            signals: AppSignals 实例，用于状态推送（注入，本模块不 import PySide6）。
        """
        # 依赖注入
        self.db = db
        self.config = config
        self.wechat_service = wechat_service
        self.signals = signals

        # Whisper 模型（懒加载，首次转写时才真正加载）
        self.model: Any = None
        self.model_available: bool = False

        # 全双工语音管线（懒加载，首次调用 get_voice_pipeline() 时才创建）
        self._voice_pipeline: Optional[Any] = None

        # 录音运行态
        self.is_recording: bool = False
        self.audio_frames: List[bytes] = []
        self._temp_wav: Optional[str] = None
        self.sample_rate: int = self._DEFAULT_SAMPLE_RATE

        # 录音库运行句柄（pyaudio / sounddevice 二选一）
        self._pa: Any = None            # pyaudio.PyAudio() 实例
        self._stream: Any = None         # pyaudio.Stream 实例
        self._record_thread: Optional[threading.Thread] = None
        self._recording_lib: Optional[str] = None  # 'pyaudio' / 'sounddevice' / None

        # 从配置读取模型大小（不主动加载模型，仅记录配置）
        self._model_size: str = "base"
        self._device: str = "cpu"
        if self.config is not None:
            try:
                self._model_size = str(self.config.get("speech.model_size", "base"))
                self._device = str(self.config.get("speech.device", "cpu"))
            except Exception:
                self._model_size = "base"
                self._device = "cpu"

        logger.info(
            "SpeechService 初始化完成 | model_size=%s | device=%s | "
            "wechat_injected=%s",
            self._model_size, self._device,
            self.wechat_service is not None,
        )

    # ------------------------------------------------------------------ #
    # Whisper 模型懒加载
    # ------------------------------------------------------------------ #
    def _ensure_model(self) -> bool:
        """懒加载 faster-whisper 模型。

        - 模型已加载且可用，直接返回 True。
        - 首次调用时尝试 ``from faster_whisper import WhisperModel``，
          按配置 ``speech.model_size``（默认 base）加载本地目录
          ``models_local/whisper-{size}``。
        - 设备与计算精度从 ``speech.device``（默认 cpu）读取，
          compute_type 固定 int8（CPU 推理最稳）。
        - 缺库 / 模型目录不存在 / 加载异常，均置 model_available=False 并返回 False，
          不抛异常。

        Returns:
            模型就绪返回 True，否则 False。
        """
        if self.model is not None and self.model_available:
            return True

        # 1) 尝试导入 faster_whisper
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError:
            logger.warning("faster-whisper 未安装，语音识别不可用")
            self.model_available = False
            return False
        except Exception as e:
            logger.warning("faster-whisper 导入异常：%s", e)
            self.model_available = False
            return False

        # 2) 解析模型路径与设备
        # === 随包运行时接入（新增）：安装目录 models_local 优先、包内兜底 ===
        try:
            from services import runtime_paths as _rp
            model_dir = _rp.resolve_model_path(f"models_local/whisper-{self._model_size}")
        except Exception:
            model_dir = os.path.join("models_local", f"whisper-{self._model_size}")
        # 兼容传入相对路径时基于工作目录；不存在则直接判定不可用（不联网下载）
        if not os.path.isdir(model_dir):
            logger.warning("Whisper 模型目录不存在：%s（跳过加载，不自动下载）", model_dir)
            self.model_available = False
            return False

        device = self._device if self._device in ("cpu", "cuda") else "cpu"
        compute_type = "int8"

        # 3) 加载模型
        try:
            logger.info("正在加载 Whisper 模型：%s（device=%s, compute_type=%s）",
                        model_dir, device, compute_type)
            self.model = WhisperModel(
                model_dir, device=device, compute_type=compute_type
            )
            self.model_available = True
            logger.info("Whisper 模型加载成功")
            return True
        except Exception as e:
            logger.warning("Whisper 模型加载失败：%s", e)
            self.model = None
            self.model_available = False
            return False

    # ------------------------------------------------------------------ #
    # 录音控制
    # ------------------------------------------------------------------ #
    def start_recording(self) -> Dict[str, Any]:
        """开始录音（采集系统麦克风音频到内存缓冲）。

        - 优先使用 pyaudio；不可用时尝试 sounddevice。
        - 两种库均未安装时返回 ``{"recording": False, "error": "录音库未安装"}``。
        - 已在录音中则直接返回成功，不重复开启。
        - 录音本身不依赖 Whisper 模型（转写才需要），因此模型不可用时仍可录音。

        Returns:
            ``{"recording": True}`` 或 ``{"recording": False, "error": ...}``。
        """
        if self.is_recording:
            logger.debug("已经在录音中，忽略重复 start")
            return {"recording": True}

        # 尝试 pyaudio
        try:
            import pyaudio  # type: ignore
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self._CHUNK_SIZE,
            )
            self._recording_lib = "pyaudio"
        except ImportError:
            self._pa = None
            self._stream = None
            self._recording_lib = None
        except Exception as e:
            logger.warning("pyaudio 打开音频流失败：%s", e)
            self._safe_close_pyaudio()
            self._recording_lib = None

        # pyaudio 不可用 → 尝试 sounddevice
        if self._recording_lib is None:
            try:
                import sounddevice as sd  # type: ignore
                import numpy as np  # type: ignore
                # sounddevice 用回调方式采集，这里包装成线程友好的缓冲
                self._sd = sd
                self._sd_np = np
                self._sd_stream = sd.RawInputStream(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=self._CHUNK_SIZE,
                    callback=self._sd_callback,
                )
                self._sd_stream.start()
                self._recording_lib = "sounddevice"
            except ImportError:
                self._recording_lib = None
            except Exception as e:
                logger.warning("sounddevice 打开音频流失败：%s", e)
                self._safe_close_sounddevice()
                self._recording_lib = None

        # 两种录音库都不可用
        if self._recording_lib is None:
            logger.warning("pyaudio / sounddevice 均未安装，无法录音")
            return {"recording": False, "error": "录音库未安装"}

        # 启动采集线程
        self.audio_frames = []
        self.is_recording = True

        if self._recording_lib == "pyaudio":
            self._record_thread = threading.Thread(
                target=self._record_loop_pyaudio,
                name="SpeechRecord",
                daemon=True,
            )
            self._record_thread.start()
        # sounddevice 走回调，无需额外线程

        logger.info("录音开始（库=%s, 采样率=%d）", self._recording_lib, self.sample_rate)
        self._emit_speech_status("recording_started")
        return {"recording": True}

    def _record_loop_pyaudio(self) -> None:
        """pyaudio 采集循环（运行在 daemon 线程中）。

        持续从音频流读取 CHUNK_SIZE 帧，追加到 self.audio_frames。
        溢出异常不中断录音；其他异常退出循环。
        """
        stream = self._stream
        if stream is None:
            return
        while self.is_recording:
            try:
                data = stream.read(self._CHUNK_SIZE, exception_on_overflow=False)
                self.audio_frames.append(data)
            except Exception as e:
                logger.debug("pyaudio 读取音频帧异常（已捕获）：%s", e)
                break

    def _sd_callback(self, indata, frames_count, time_info, status) -> None:
        """sounddevice 音频回调（由 PortAudio 线程调用）。

        将采集到的 int16 字节数据追加到 audio_frames。
        """
        try:
            # indata 是 numpy 数组，转成 bytes 以便直接拼进 WAV
            self.audio_frames.append(bytes(indata))
        except Exception as e:
            logger.debug("sounddevice 回调异常：%s", e)

    def stop_recording(self) -> Dict[str, Any]:
        """停止录音并保存为临时 WAV 文件。

        - 停止采集线程 / 关闭音频流；
        - 用标准库 wave 模块写 16kHz mono 16bit WAV；
        - 临时文件路径保存在 self._temp_wav，供后续 transcribe 使用。

        Returns:
            ``{"wav_path": str, "duration": float}``；未在录音时返回 error。
        """
        if not self.is_recording:
            return {"wav_path": None, "duration": 0.0, "error": "未在录音"}

        # 1) 停止采集
        self.is_recording = False

        # 等待 pyaudio 采集线程退出
        if self._record_thread is not None and self._record_thread.is_alive():
            self._record_thread.join(timeout=self._RECORD_JOIN_TIMEOUT)
        self._record_thread = None

        # 关闭音频流
        if self._recording_lib == "pyaudio":
            self._safe_close_pyaudio()
        elif self._recording_lib == "sounddevice":
            self._safe_close_sounddevice()

        # 2) 计算时长（16-bit = 2 bytes/采样点，mono）
        raw = b"".join(self.audio_frames)
        duration = len(raw) / float(self.sample_rate * 2) if raw else 0.0

        if not raw:
            logger.warning("录音内容为空")
            self._temp_wav = None
            return {"wav_path": None, "duration": 0.0, "error": "录音内容为空"}

        # 3) 写入临时 WAV
        try:
            fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="loveai_speech_")
            os.close(fd)  # 关闭 OS 句柄，交给 wave 模块重开
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(self.sample_rate)
                wf.writeframes(raw)
            self._temp_wav = wav_path
            logger.info("录音已保存：%s（时长 %.2fs）", wav_path, duration)
            self._emit_speech_status("recording_stopped",
                                     info=f"wav={wav_path}, duration={duration:.2f}")
            return {"wav_path": wav_path, "duration": duration}
        except Exception as e:
            logger.error("保存 WAV 失败：%s", e)
            self._temp_wav = None
            return {"wav_path": None, "duration": duration, "error": f"保存 WAV 失败: {e}"}

    # ------------------------------------------------------------------ #
    # 语音转文字
    # ------------------------------------------------------------------ #
    def transcribe(self, wav_path: Optional[str] = None) -> Dict[str, Any]:
        """语音转文字（核心方法）。

        Args:
            wav_path: 音频文件路径；为 None 时使用 self._temp_wav（最近一次录音产物）。

        Returns:
            成功：``{"text": str, "language": str, "duration": float,
                     "segments": [{"start","end","text"}, ...]}``。
            失败：``{"text": "", "error": "..."}``。
        """
        # 确定待识别文件
        path = wav_path or self._temp_wav
        if not path or not os.path.isfile(path):
            logger.warning("transcribe：音频文件不存在：%s", path)
            return {"text": "", "error": "音频文件不存在"}

        # 确保模型就绪
        if not self._ensure_model():
            return {"text": "", "error": "语音模型未就绪"}

        # 调用 Whisper 转写
        try:
            logger.info("开始转写：%s", path)
            segments_iter, info = self.model.transcribe(
                path, language="zh", beam_size=5
            )
            # segments 是生成器，必须消费才能拿到文本
            seg_list: List[Dict[str, Any]] = []
            full_text_parts: List[str] = []
            for seg in segments_iter:
                seg_list.append({
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": seg.text,
                })
                full_text_parts.append(seg.text)
            full_text = "".join(full_text_parts).strip()
            logger.info("转写完成：%d 段，%d 字", len(seg_list), len(full_text))
            return {
                "text": full_text,
                "language": getattr(info, "language", "zh"),
                "duration": getattr(info, "duration", 0.0),
                "segments": seg_list,
            }
        except Exception as e:
            logger.error("Whisper 转写异常：%s", e)
            return {"text": "", "error": f"转写失败: {e}"}

    def transcribe_and_fill(self, wav_path: Optional[str] = None) -> Dict[str, Any]:
        """语音识别并自动填入微信（悬浮球语音的完整链路）。

        流程：
        1. 调用 transcribe() 得到文本；
        2. 若文本非空且 wechat_service 已注入，调用
           ``wechat_service.fill_text(text)`` 填入微信输入框；
        3. 识别完成后清理本次临时 WAV（外部传入的 wav_path 不清理）。

        Args:
            wav_path: 可选音频路径；默认用 self._temp_wav。

        Returns:
            ``{"text": str, "filled": bool, "error": str(可选)}``。
        """
        result = self.transcribe(wav_path)
        text = result.get("text", "")
        filled = False

        if text and self.wechat_service is not None:
            try:
                filled = bool(self.wechat_service.fill_text(text))
            except Exception as e:
                logger.warning("填入微信失败：%s", e)
                filled = False
        elif not text:
            logger.info("识别文本为空，跳过填入微信")

        # 清理临时 WAV（仅当用的是本次录音产生的临时文件）
        if wav_path is None and self._temp_wav:
            self._cleanup_temp_wav()

        out: Dict[str, Any] = {"text": text, "filled": filled}
        if "error" in result:
            out["error"] = result["error"]
        return out

    def transcribe_file(self, file_path: str) -> Dict[str, Any]:
        """识别外部音频文件（mp3 / wav / m4a 等）。

        由 faster-whisper 内部通过 ffmpeg 解码非 WAV 格式；
        若系统无 ffmpeg，仅 WAV 可被识别。

        Args:
            file_path: 外部音频文件的绝对或相对路径。

        Returns:
            与 transcribe() 相同格式；文件不存在时返回 error。
        """
        if not file_path or not os.path.isfile(file_path):
            logger.warning("transcribe_file：文件不存在：%s", file_path)
            return {"text": "", "error": f"文件不存在: {file_path}"}
        return self.transcribe(file_path)

    # ------------------------------------------------------------------ #
    # 全双工语音管线（R3.3-14）
    # ------------------------------------------------------------------ #
    def get_voice_pipeline(self, llm_gateway: Any = None) -> Optional[Any]:
        """懒加载全双工语音管线实例。

        返回一个 ``VoicePipeline`` 实例，各阶段默认用桩实现，
        ASR 自动对接当前 SpeechService 的 transcribe() 能力。

        Args:
            llm_gateway: 可选，LLMGateway 实例；传入则 LLM 阶段对接真实网关，
                         否则用 StubLLM 桩实现。

        Returns:
            VoicePipeline 实例；任何失败都返回 None（不崩溃）。
        """
        # 已缓存过直接返回
        if hasattr(self, "_voice_pipeline") and self._voice_pipeline is not None:
            return self._voice_pipeline

        try:
            from services.voice_pipeline import (
                VoicePipeline,
                EnergyVAD,
                SpeechServiceASRAdapter,
                LLMGatewayAdapter,
                StubLLM,
                StubTTS,
            )

            # ASR：用现有 SpeechService 包装
            asr = SpeechServiceASRAdapter(self)

            # LLM：有 gateway 就用真的，没有就桩
            if llm_gateway is not None:
                llm = LLMGatewayAdapter(llm_gateway, system_prompt="你是温柔、有耐心的恋爱关系分析助手（游戏系统精灵），陪用户复盘与真实对象的互动、给出现实可执行的建议；你是预测与反馈工具，不是用户的恋爱对象。")
            else:
                llm = StubLLM()

            # R3.3-36 VAD：生产从 SilentVAD（永不触发、对话空转）换成 EnergyVAD
            # （纯 PCM16 RMS，喂真实麦克风帧就会真触发 speech_start/end）。
            # TTS：默认桩。
            vad = EnergyVAD()
            tts = StubTTS()

            # R3.3-36 中文 EoT：生产必须打开（之前默认 False，验收官实测
            # pipeline._use_chinese_eot=False，中文连接词尾巴被错误切轮）。
            self._voice_pipeline = VoicePipeline(
                vad=vad, asr=asr, llm=llm, tts=tts,
                use_chinese_eot=True,
            )
            logger.info("VoicePipeline 懒加载成功（llm=%s）",
                        "real" if llm_gateway is not None else "stub")
            return self._voice_pipeline

        except Exception as e:
            logger.warning("VoicePipeline 初始化失败（已降级为 None）：%s", e)
            self._voice_pipeline = None
            return None

    # ------------------------------------------------------------------ #
    # 状态 / 播放 / 清理
    # ------------------------------------------------------------------ #
    def get_status(self) -> Dict[str, Any]:
        """返回语音服务状态快照。

        Returns:
            ``{"available": bool, "recording": bool, "model_size": str,
               "recording_lib_available": bool}``。
        """
        return {
            "available": self.model_available,
            "recording": self.is_recording,
            "model_size": self._model_size,
            "recording_lib_available": self._check_recording_lib(),
        }

    def play_audio(self, wav_path: str) -> bool:
        """播放音频文件（调用系统默认播放器）。

        Windows 下使用 os.startfile；其他平台回退到 subprocess 调用
        xdg-open / open。仅做简单播放，不做进度控制。

        Args:
            wav_path: 音频文件路径。

        Returns:
            成功触发播放返回 True，否则 False。
        """
        if not wav_path or not os.path.isfile(wav_path):
            logger.warning("play_audio：文件不存在：%s", wav_path)
            return False
        try:
            if os.name == "nt":
                os.startfile(wav_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", wav_path])
            else:
                subprocess.Popen(["xdg-open", wav_path])
            return True
        except Exception as e:
            logger.warning("播放音频失败：%s", e)
            return False

    def cleanup(self) -> None:
        """彻底清理：停止录音 + 删除临时 WAV + 释放音频设备。

        建议在应用退出时调用，避免设备句柄泄漏。
        """
        if self.is_recording:
            self.is_recording = False
            if self._record_thread is not None and self._record_thread.is_alive():
                self._record_thread.join(timeout=self._RECORD_JOIN_TIMEOUT)
            self._safe_close_pyaudio()
            self._safe_close_sounddevice()
        self._cleanup_temp_wav()
        # 模型显式置空，下次 _ensure_model 会重新加载
        self.model = None
        self.model_available = False
        logger.info("SpeechService 资源已清理")

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _cleanup_temp_wav(self) -> None:
        """删除 self._temp_wav 指向的临时文件（存在时）。"""
        if self._temp_wav and os.path.isfile(self._temp_wav):
            try:
                os.remove(self._temp_wav)
                logger.debug("临时录音文件已删除：%s", self._temp_wav)
            except OSError as e:
                logger.debug("删除临时录音文件失败：%s", e)
        self._temp_wav = None
        self.audio_frames = []

    def _safe_close_pyaudio(self) -> None:
        """安全关闭 pyaudio 流与设备句柄（任何异常都吞掉）。"""
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
        except Exception as e:
            logger.debug("关闭 pyaudio stream 异常：%s", e)
        try:
            if self._pa is not None:
                self._pa.terminate()
        except Exception as e:
            logger.debug("终止 pyaudio 异常：%s", e)
        self._stream = None
        self._pa = None

    def _safe_close_sounddevice(self) -> None:
        """安全关闭 sounddevice 流。"""
        try:
            sd_stream = getattr(self, "_sd_stream", None)
            if sd_stream is not None:
                sd_stream.stop()
                sd_stream.close()
        except Exception as e:
            logger.debug("关闭 sounddevice stream 异常：%s", e)
        self._sd_stream = None

    def _check_recording_lib(self) -> bool:
        """探测录音库是否可用（pyaudio 或 sounddevice 任一可导入即可）。

        注意：此方法仅做导入探测，不打开设备；
        首次调用会尝试 import，结果不缓存（成本极低）。
        """
        try:
            import pyaudio  # type: ignore  # noqa: F401
            return True
        except ImportError:
            pass
        try:
            import sounddevice  # type: ignore  # noqa: F401
            return True
        except ImportError:
            return False

    def _emit_speech_status(self, status: str, info: str = "", error: str = "") -> None:
        """安全地通过 signals 推送语音状态（信号不存在也不抛异常）。"""
        if self.signals is None:
            return
        try:
            payload: Dict[str, Any] = {"status": status}
            if info:
                payload["info"] = info
            if error:
                payload["error"] = error
            # 复用 error_occurred 或自定义语音信号都可；
            # 这里优先尝试 speech_status，其次回退 error_occurred 兜底
            emitter = getattr(self.signals, "speech_status", None) or \
                      getattr(self.signals, "error_occurred", None)
            if emitter is not None:
                emitter.emit(payload)
        except Exception as e:
            logger.debug("推送语音状态信号失败：%s", e)
