# -*- coding: utf-8 -*-
"""
LoveAI v2.0 — 全双工实时语音管线骨架（R3.3-14）

对标 livekit-agents 的全双工语音管线，当前为架构骨架：
  - 每个阶段（VAD / ASR / LLM / TTS）都是可注入的抽象接口
  - 默认提供离线桩实现，不依赖任何重库即可跑通单测
  - LiveKit 标注为「可插拔后端」：装上 livekit-agents 后替换对应实现即可启用

管线阶段顺序：
    麦克风 → VAD(端点检测) → ASR(语音转文本) → LLM(生成回复) → TTS(文本转语音) → 扬声器
                                      ↑                                    |
                                      └────── barge-in 打断 ←────────────┘
                                    (AI 播报中检测到用户开口 → 立刻停 TTS)

生命周期状态机：
    IDLE ──start()──> LISTENING ──VAD.speech_start──> SPEAKING_USER
    SPEAKING_USER ──VAD.speech_end──> PROCESSING ──LLM done──> SPEAKING_AI
    SPEAKING_AI ──TTS done──> LISTENING
    SPEAKING_AI ──VAD.speech_start──> LISTENING  (barge-in! 立刻停 TTS)
    any ──stop()──> IDLE
    any ──interrupt()──> LISTENING  (只打断当前播报/处理，不退出管线)

LiveKit 插拔点说明：
    - VAD:  可替换为 livekit.agents.vad.SileroVADAdapter
    - ASR:  可替换为 livekit.agents.stt.WhisperSTT 或 DeepgramSTT
    - LLM:  可替换为 livekit.agents.llm.ChatContext + 任意 LLM 插件
    - TTS:  可替换为 livekit.agents.tts.TTSAdapter
    - 整体:  用 livekit.agents.AgentSession + VoicePipelineAgent 替换本类
    以上均为标注，不 import livekit，离线环境零依赖。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# R3.3-35/36：全双工智能轮次 + 中文 EoT（接线点）。
# 这两个模块本身零重依赖；import 失败时降级为「不启用」，绝不阻塞管线构造。
try:
    from services.smart_turn import SmartTurn, TurnAction  # type: ignore
    from services.chinese_eot import detect_chinese_eot  # type: ignore
    _SMART_TURN_AVAILABLE = True
except Exception as _e:  # pragma: no cover - 防御性
    SmartTurn = None  # type: ignore
    TurnAction = None  # type: ignore
    detect_chinese_eot = None  # type: ignore
    _SMART_TURN_AVAILABLE = False
    logging.getLogger(__name__).info(
        "SmartTurn/ChineseEoT 模块导入失败，管线降级为旧式 barge-in: %s", _e)

logger = logging.getLogger(__name__)


# ======================================================================
# 枚举与数据结构
# ======================================================================

class PipelineState(Enum):
    """管线运行状态。"""
    IDLE = "idle"
    LISTENING = "listening"          # 监听中，等待用户开口
    SPEAKING_USER = "speaking_user"  # 用户正在说话（VAD 检测到语音）
    PROCESSING = "processing"        # ASR→LLM 处理中
    SPEAKING_AI = "speaking_ai"      # AI 正在播报


class VADEventType(Enum):
    """VAD 输出事件类型。"""
    SILENCE = "silence"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


class VADEvent:
    """VAD 输出的单个事件。"""
    __slots__ = ("type", "timestamp_ms", "confidence")

    def __init__(
        self,
        type: VADEventType,
        timestamp_ms: int = 0,
        confidence: float = 1.0,
    ) -> None:
        self.type = type
        self.timestamp_ms = timestamp_ms
        self.confidence = confidence

    def __repr__(self) -> str:
        return f"VADEvent({self.type.value}, t={self.timestamp_ms}ms, conf={self.confidence:.2f})"


# ======================================================================
# 抽象接口 —— 每个阶段一个
# ======================================================================

class VADInterface(ABC):
    """语音活动检测（Voice Activity Detection）抽象接口。

    输入：音频帧（bytes / numpy array）
    输出：VADEvent 流（SILENCE / SPEECH_START / SPEECH_END）

    实现方需要维护内部状态（能量阈值、hangover 等），
    通过 feed_audio() 逐帧喂入，通过 get_events() 取出产生的事件。
    """

    @abstractmethod
    def feed_audio(self, chunk: bytes) -> None:
        """喂入一帧音频（通常 20-30ms 的 PCM16 数据）。"""
        ...

    @abstractmethod
    def get_events(self) -> List[VADEvent]:
        """取出自上次调用以来产生的所有 VAD 事件。"""
        ...

    @abstractmethod
    def reset(self) -> None:
        """重置内部状态（管线重启时调用）。"""
        ...


class ASRInterface(ABC):
    """语音识别（Automatic Speech Recognition）抽象接口。

    输入：一段完整的音频（端点检测结束后的语音段）
    输出：识别出的文本字符串

    也支持流式模式：feed_partial() 喂入部分音频，get_partial_result() 拿中间结果。
    """

    @abstractmethod
    def transcribe(self, audio_data: bytes) -> str:
        """转写一整段完整语音，返回文本。"""
        ...

    def transcribe_stream(self, audio_chunk: bytes) -> Optional[str]:
        """流式转写：喂入一块音频，可能返回中间结果或 None。

        默认实现：不支持流式，返回 None。子类可覆盖。
        """
        return None

    @abstractmethod
    def reset(self) -> None:
        """重置识别器内部缓冲。"""
        ...


class LLMInterface(ABC):
    """大语言模型对话抽象接口。

    输入：用户文本
    输出：AI 回复文本

    实现方负责管理对话历史、人设 system prompt 等。
    """

    @abstractmethod
    def generate_reply(self, user_text: str) -> str:
        """根据用户输入生成回复文本。"""
        ...

    def generate_reply_stream(self, user_text: str) -> Any:
        """流式生成回复：返回一个迭代器，逐 token 产出文本片段。

        默认实现：不支持流式，返回 None。子类可覆盖。
        """
        return None

    def reset(self) -> None:
        """重置对话历史。默认空实现。"""
        pass


class TTSInterface(ABC):
    """文本转语音（Text-to-Speech）抽象接口。

    输入：文本
    输出：播放音频（异步）

    关键要求：必须支持 stop() 立即中断当前播报（barge-in 用）。
    """

    @abstractmethod
    def speak(self, text: str) -> None:
        """开始播报一段文本（异步播放，调用方不阻塞）。"""
        ...

    @abstractmethod
    def stop(self) -> None:
        """立即停止当前播报（barge-in 触发）。"""
        ...

    @abstractmethod
    def is_speaking(self) -> bool:
        """当前是否正在播报。"""
        ...

    def reset(self) -> None:
        """重置 TTS 状态。默认空实现。"""
        pass


# ======================================================================
# 离线桩实现 —— 不依赖任何重库，可跑通单测
# ======================================================================

class SilentVAD(VADInterface):
    """桩 VAD：永远输出静音，不触发语音端点。

    用于测试管线空转 / 不触发对话的场景。
    可通过 inject_event() 手动注入事件来模拟用户说话。
    """

    def __init__(self) -> None:
        self._event_queue: queue.Queue[VADEvent] = queue.Queue()
        self._chunk_count = 0

    def feed_audio(self, chunk: bytes) -> None:
        self._chunk_count += 1

    def get_events(self) -> List[VADEvent]:
        events: List[VADEvent] = []
        while not self._event_queue.empty():
            events.append(self._event_queue.get_nowait())
        return events

    def reset(self) -> None:
        with self._event_queue.mutex:
            self._event_queue.queue.clear()
        self._chunk_count = 0

    def inject_event(self, event: VADEvent) -> None:
        """测试辅助：手动注入一个 VAD 事件。"""
        self._event_queue.put(event)


class TriggerVAD(SilentVAD):
    """触发式桩 VAD：前 N 帧静音，之后自动输出 speech_start + speech_end。

    模拟「用户说了一句话」的完整周期。
    """

    def __init__(self, silence_frames_before_speech: int = 3, speech_duration_frames: int = 5) -> None:
        super().__init__()
        self._silence_before = silence_frames_before_speech
        self._speech_frames = speech_duration_frames
        self._fired = False

    def feed_audio(self, chunk: bytes) -> None:
        super().feed_audio(chunk)
        if self._fired:
            return
        if self._chunk_count == self._silence_before:
            self.inject_event(VADEvent(VADEventType.SPEECH_START, timestamp_ms=self._chunk_count * 20))
        elif self._chunk_count == self._silence_before + self._speech_frames:
            self.inject_event(VADEvent(VADEventType.SPEECH_END, timestamp_ms=self._chunk_count * 20))
            self._fired = True

    def reset(self) -> None:
        super().reset()
        self._fired = False


class EnergyVAD(SilentVAD):
    """能量 VAD（纯 PCM16 RMS，零重依赖）——生产默认 VAD。

    与 SilentVAD「永远静音、永不触发」不同，本实现真的按音频能量切端点：
      - 连续帧 RMS >= threshold        → speech_start（用户开口）
      - 说话中连续 hangover_frames 帧 RMS < threshold → speech_end（用户说完）

    这样生产路径（get_voice_pipeline）喂真实麦克风帧时，对话能真的触发，
    不再空转。阈值/hangover 都给了保守默认值，离线探针里喂「大音量帧 +
    静音帧」即可复现 speech_start → speech_end → flush 全链路。
    """

    def __init__(self,
                 threshold: float = 250.0,
                 hangover_frames: int = 8,
                 frame_ms: int = 20) -> None:
        super().__init__()
        self.threshold = float(threshold)
        self.hangover_frames = int(hangover_frames)
        self.frame_ms = int(frame_ms)
        self._speaking = False
        self._silence_run = 0

    @staticmethod
    def _rms_pcm16(chunk: bytes) -> float:
        """从 PCM16 小端 mono 字节算信号能量 RMS（sqrt(mean(x^2))）。

        不减均值：真实静音近似 0、人声/噪声幅度大，直接用信号能量最稳；
        恒定大音量方波也算「有声音」（探针/真麦克风都成立）。奇数字节容错。
        """
        if not chunk:
            return 0.0
        n = len(chunk) // 2
        if n == 0:
            return 0.0
        import array as _array
        samples = _array.array("h")
        try:
            samples.frombytes(chunk[: n * 2])
        except Exception:  # noqa: BLE001
            return 0.0
        if not samples:
            return 0.0
        return float(sum(s * s for s in samples) / len(samples)) ** 0.5

    def feed_audio(self, chunk: bytes) -> None:
        super().feed_audio(chunk)
        rms = self._rms_pcm16(chunk)
        now_ms = self._chunk_count * self.frame_ms
        if not self._speaking:
            if rms >= self.threshold:
                self._speaking = True
                self._silence_run = 0
                self.inject_event(VADEvent(VADEventType.SPEECH_START,
                                           timestamp_ms=now_ms))
        else:
            if rms < self.threshold:
                self._silence_run += 1
                if self._silence_run >= self.hangover_frames:
                    self._speaking = False
                    self._silence_run = 0
                    self.inject_event(VADEvent(VADEventType.SPEECH_END,
                                               timestamp_ms=now_ms))
            else:
                self._silence_run = 0

    def reset(self) -> None:
        super().reset()
        self._speaking = False
        self._silence_run = 0


class EchoASR(ASRInterface):
    """桩 ASR：返回固定文本，不做真实识别。

    可配置返回什么文本，方便测试不同输入。
    """

    def __init__(self, default_text: str = "你好") -> None:
        self._default_text = default_text
        self._call_count = 0
        self.last_audio_length = 0

    def transcribe(self, audio_data: bytes) -> str:
        self._call_count += 1
        self.last_audio_length = len(audio_data)
        logger.debug("EchoASR 转写（桩）→ %r", self._default_text)
        return self._default_text

    def reset(self) -> None:
        self._call_count = 0
        self.last_audio_length = 0


class StubLLM(LLMInterface):
    """桩 LLM：返回固定回复文本。

    可配置回复内容，记录调用次数，方便测试断言。
    """

    def __init__(self, reply_text: str = "收到，我在听。") -> None:
        self._reply_text = reply_text
        self._call_count = 0
        self.last_user_input = ""

    def generate_reply(self, user_text: str) -> str:
        self._call_count += 1
        self.last_user_input = user_text
        logger.debug("StubLLM 生成回复（桩）→ %r", self._reply_text)
        return self._reply_text

    def reset(self) -> None:
        self._call_count = 0
        self.last_user_input = ""


class StubTTS(TTSInterface):
    """桩 TTS：记录 speak/stop 调用，不真正播放音频。

    用于测试 barge-in 打断逻辑：验证 stop() 是否被正确调用。
    """

    def __init__(self) -> None:
        self._speaking = False
        self._spoken_texts: List[str] = []
        self._stop_count = 0

    def speak(self, text: str) -> None:
        self._speaking = True
        self._spoken_texts.append(text)
        logger.debug("StubTTS 播报（桩）→ %r", text)
        # 桩实现：模拟播报瞬间完成（实际全双工中这里是异步播放）
        # 但为了测试 barge-in，我们保持 speaking=True 直到 stop() 被调用
        # 真实场景由 TTS 实现内部的播放线程结束后置 False

    def stop(self) -> None:
        if self._speaking:
            self._stop_count += 1
            logger.debug("StubTTS 播报被打断（stop #%d）", self._stop_count)
        self._speaking = False

    def is_speaking(self) -> bool:
        return self._speaking

    def reset(self) -> None:
        self._speaking = False
        self._spoken_texts.clear()
        self._stop_count = 0

    # --- 测试辅助方法 ---
    @property
    def spoken_texts(self) -> List[str]:
        return self._spoken_texts

    @property
    def stop_count(self) -> int:
        return self._stop_count

    def finish_speaking(self) -> None:
        """测试辅助：模拟 TTS 自然播完（不经过 stop()）。"""
        self._speaking = False


# ======================================================================
# 适配层 —— 把现有服务包装成管线接口
# ======================================================================

class SpeechServiceASRAdapter(ASRInterface):
    """把现有 SpeechService 的 transcribe() 包装成 ASRInterface。

    用法：
        speech_svc = SpeechService(...)
        asr = SpeechServiceASRAdapter(speech_svc)

    注意：现有 transcribe() 是文件级的（传 wav_path），
    这里把内存中的 bytes 临时写盘再转写，是一个过渡方案。
    流式 ASR 需要换用真实的流式 Whisper 实现。
    """

    def __init__(self, speech_service: Any) -> None:
        self._svc = speech_service
        self._temp_dir: Optional[str] = None

    def transcribe(self, audio_data: bytes) -> str:
        import os
        import tempfile
        import wave

        if not audio_data:
            return ""

        # 写临时 WAV
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="voice_pipe_asr_")
        os.close(fd)
        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(audio_data)
            result = self._svc.transcribe(wav_path)
            return result.get("text", "")
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

    def reset(self) -> None:
        # SpeechService 无状态缓冲，无需重置
        pass


class LLMGatewayAdapter(LLMInterface):
    """把现有 LLMGateway.chat() 包装成 LLMInterface。

    用法：
        gateway = LLMGateway(...)
        llm = LLMGatewayAdapter(gateway, system_prompt="你是一个温柔的恋爱助手...")
    """

    def __init__(self, llm_gateway: Any, system_prompt: str = "") -> None:
        self._gw = llm_gateway
        self._system_prompt = system_prompt

    def generate_reply(self, user_text: str) -> str:
        try:
            return self._gw.chat(
                system_prompt=self._system_prompt,
                user_prompt=user_text,
            )
        except Exception as e:
            logger.warning("LLMGatewayAdapter 调用失败：%s", e)
            return ""


# ======================================================================
# VoicePipeline —— 全双工管线总类
# ======================================================================

# 流式钩子类型：回调函数签名
StreamHook = Callable[[str], None]


class VoicePipeline:
    """全双工实时语音管线总类。

    用法：
        pipeline = VoicePipeline(
            vad=SilentVAD(),
            asr=EchoASR("今天好累"),
            llm=StubLLM("辛苦了，早点休息。"),
            tts=StubTTS(),
        )
        pipeline.start()
        # ... 喂音频帧 ...
        pipeline.stop()

    阶段可注入：每个阶段都传接口实现，默认全桩，离线可跑。
    Barge-in：AI 播报期间 VAD 检测到 speech_start → 立刻调用 tts.stop()
    """

    def __init__(
        self,
        vad: Optional[VADInterface] = None,
        asr: Optional[ASRInterface] = None,
        llm: Optional[LLMInterface] = None,
        tts: Optional[TTSInterface] = None,
        # 流式钩子（可选）
        on_asr_partial: Optional[StreamHook] = None,
        on_llm_token: Optional[StreamHook] = None,
        on_state_change: Optional[Callable[[PipelineState], None]] = None,
        use_chinese_eot: bool = False,
    ) -> None:
        self.vad: VADInterface = vad or SilentVAD()
        self.asr: ASRInterface = asr or EchoASR()
        self.llm: LLMInterface = llm or StubLLM()
        self.tts: TTSInterface = tts or StubTTS()

        # 流式钩子
        self.on_asr_partial = on_asr_partial
        self.on_llm_token = on_llm_token
        self.on_state_change = on_state_change

        # 运行态
        self._state: PipelineState = PipelineState.IDLE
        self._lock = threading.Lock()
        self._running = False

        # 当前语音段累积的音频数据（VAD speech_start → speech_end 之间）
        self._current_utterance: List[bytes] = []

        # 统计
        self._utterance_count = 0
        self._barge_in_count = 0
        self._backchannel_count = 0

        # --- R3.3-35 SmartTurn 全双工轮次状态机（接线）---
        self._smart_turn: Any = SmartTurn() if SmartTurn is not None else None
        # 最近一次 ASR partial 文本（barge-in 时用来判断是不是短应答词）
        self._last_partial_asr: str = ""

        # --- R3.3-36 中文 EoT 二次切轮判断（接线）---
        # use_chinese_eot=True 时，VAD.speech_end 后先跑 detect_chinese_eot()：
        # eot=False 则「延迟切轮」——把文本挂起等更多 silence，不立刻 flush 给 LLM。
        self._use_chinese_eot: bool = bool(use_chinese_eot) and (
            detect_chinese_eot is not None)
        self._pending_text: str = ""        # EoT 判定未结束时挂起的文本
        self._pending_audio: bytes = b""    # 对应音频（供后续 flush）
        self._last_speech_ts: float = 0.0   # 最近一帧语音的单调时钟（算 silence_ms）
        self._eot_grace_deadline: float = 0.0  # 挂起后最迟强制 flush 的时刻

        logger.info(
            "VoicePipeline 初始化 | vad=%s | asr=%s | llm=%s | tts=%s | "
            "smart_turn=%s | chinese_eot=%s",
            type(self.vad).__name__, type(self.asr).__name__,
            type(self.llm).__name__, type(self.tts).__name__,
            self._smart_turn is not None, self._use_chinese_eot,
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    @property
    def state(self) -> PipelineState:
        """当前管线状态（只读快照）。"""
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """启动管线。进入 LISTENING 状态。"""
        with self._lock:
            if self._running:
                logger.debug("管线已在运行，忽略重复 start")
                return
            self._running = True
            self._set_state(PipelineState.LISTENING)
            self.vad.reset()
            self.asr.reset()
            self.tts.reset()
        logger.info("VoicePipeline 启动 → LISTENING")

    def stop(self) -> None:
        """停止管线。回到 IDLE，停止 TTS 播报。"""
        with self._lock:
            if not self._running:
                return
            self._running = False
            # 停掉 TTS
            try:
                self.tts.stop()
            except Exception as e:
                logger.debug("stop TTS 异常：%s", e)
            self._set_state(PipelineState.IDLE)
            self._current_utterance.clear()
        logger.info("VoicePipeline 停止 → IDLE")

    def interrupt(self) -> None:
        """打断当前播报/处理，但不退出管线（回到 LISTENING）。

        与 stop() 的区别：interrupt 只停当前轮次，管线继续监听；
        stop() 是彻底关闭管线。
        """
        with self._lock:
            if not self._running:
                return
            # 停 TTS
            try:
                self.tts.stop()
            except Exception:
                pass
            self._current_utterance.clear()
            self._set_state(PipelineState.LISTENING)
        logger.info("VoicePipeline 被手动 interrupt → LISTENING")

    # ------------------------------------------------------------------ #
    # 音频喂入（主入口）
    # ------------------------------------------------------------------ #

    def feed_audio(self, chunk: bytes) -> None:
        """喂入一帧麦克风音频。

        管线内部处理流程：
        1. 喂给 VAD，拿 VAD 事件
        2. 根据事件更新状态机
        3. SPEECH_START：开始累积音频 + 触发 barge-in（如果正在播报）
        4. SPEECH_END：把累积的音频交给 ASR→LLM→TTS
        """
        if not self._running:
            return

        # R3.3-36：若有 EoT 挂起文本且已到宽限 deadline，强制 flush（防卡死）
        self._flush_pending_if_due()

        # 1. 喂 VAD
        self.vad.feed_audio(chunk)

        # 2. 处理 VAD 事件
        events = self.vad.get_events()
        for event in events:
            self._handle_vad_event(event, chunk)

        # 3. 如果正在累积用户语音，追加音频
        if self._state == PipelineState.SPEAKING_USER:
            self._current_utterance.append(chunk)

    # ------------------------------------------------------------------ #
    # 内部事件处理
    # ------------------------------------------------------------------ #

    def _handle_vad_event(self, event: VADEvent, current_chunk: bytes) -> None:
        """处理单个 VAD 事件，驱动状态机。"""
        if event.type == VADEventType.SPEECH_START:
            self._on_speech_start(current_chunk)
        elif event.type == VADEventType.SPEECH_END:
            self._on_speech_end()
        # SILENCE 事件不做特殊处理

    def _on_speech_start(self, chunk: bytes) -> None:
        """VAD 检测到用户开始说话。"""
        with self._lock:
            # 记录语音帧时间戳（供中文 EoT 算 silence_ms）
            self._last_speech_ts = time.monotonic()

            # Barge-in：如果 AI 正在播报，交给 SmartTurn 决定是「真打断」还是「只回短应答」
            if self._state == PipelineState.SPEAKING_AI:
                if self._smart_turn is not None:
                    action = self._decide_barge_in()
                else:
                    action = TurnAction.INTERRUPT_TTS if TurnAction else None

                if action == TurnAction.SPEAK_BACKCHANNEL:
                    # 用户只是「嗯/哦」短应答：不打断 TTS，AI 继续说，回一个短应答即可
                    self._backchannel_count += 1
                    logger.info(
                        "SmartTurn：用户短应答 backchannel（第 %d 次），不打断 TTS",
                        self._backchannel_count)
                    # 不调 tts.stop()；AI 继续播报
                else:
                    # INTERRUPT_TTS，或信息不足(CONTINUE_LISTEN)时按真打断处理（保持旧行为）
                    self._barge_in_count += 1
                    logger.info("Barge-in 触发！打断 AI 播报（第 %d 次）",
                                self._barge_in_count)
                    try:
                        self.tts.stop()
                    except Exception as e:
                        logger.warning("barge-in stop TTS 异常：%s", e)

            # 切换到用户说话状态
            self._current_utterance = [chunk]
            self._set_state(PipelineState.SPEAKING_USER)

    def _decide_barge_in(self) -> Any:
        """R3.3-35：AI 播报中用户开口，问 SmartTurn 该真打断还是只回短应答。

        - 短应答词（嗯/哦/对，≤3字）→ SPEAK_BACKCHANNEL（不打断 TTS）
        - 说话够长/不是短词      → INTERRUPT_TTS（真打断）
        - 信息不足（无 partial 文本、刚开始说）→ CONTINUE_LISTEN，
          调用方按旧行为真打断（保持单测兼容）。
        """
        try:
            # 把管线当前状态同步给 SmartTurn：AI 正在播报
            if self._smart_turn.state is not None and hasattr(self._smart_turn.state, "value"):
                from services.smart_turn import TurnState as _TS
                self._smart_turn.state = _TS.SPEAKING_AI_TURN
            obs = {
                "vad_state": "speech_start",
                "asr_text": self._last_partial_asr or "",
                "tts_active": bool(self.tts.is_speaking()),
                "speech_duration_ms": 0,   # speech_start 瞬间时长=0
                "last_user_ms": 0,
            }
            return self._smart_turn.update(obs)
        except Exception as e:  # pragma: no cover - 防御
            logger.debug("SmartTurn 决策异常，按真打断处理: %s", e)
            return TurnAction.INTERRUPT_TTS

    def _on_speech_end(self) -> None:
        """VAD 检测到用户说话结束。走 ASR→LLM→TTS 流程。"""
        with self._lock:
            if self._state != PipelineState.SPEAKING_USER:
                return

            # 拼接待识别音频
            audio_bytes = b"".join(self._current_utterance)
            self._current_utterance.clear()

            if not audio_bytes:
                self._set_state(PipelineState.LISTENING)
                return

            self._set_state(PipelineState.PROCESSING)

        # --- 以下在锁外执行（ASR/LLM/TTS 可能耗时）---

        # 1. ASR
        try:
            user_text = self.asr.transcribe(audio_bytes)
        except Exception as e:
            logger.warning("ASR 失败：%s", e)
            user_text = ""

        if not user_text.strip():
            with self._lock:
                self._set_state(PipelineState.LISTENING)
            return

        # --- R3.3-36 中文 EoT 二次切轮判断（接线点）---
        # VAD.speech_end 只代表「停了一下」，中文常带连接词尾巴/逗号后停顿。
        # eot=False 时延迟切轮：把文本挂起，等更多 silence，不立刻 flush 给 LLM。
        if self._use_chinese_eot:
            hold = self._maybe_hold_for_eot(user_text, audio_bytes)
            if hold:
                return  # 已挂起，回到 LISTENING 等更多语音/沉默

        logger.info("用户说：%s", user_text)
        self._respond_to_user(user_text)

    # ------------------------------------------------------------------ #
    # R3.3-36 中文 EoT：是否挂起本轮（延迟切轮）
    # ------------------------------------------------------------------ #
    def _maybe_hold_for_eot(self, user_text: str, audio_bytes: bytes) -> bool:
        """中文 EoT 二次判断。返回 True=已挂起（延迟切轮），False=应立刻 flush。

        - eot=True → 返回 False（立刻走 ASR→LLM→TTS）；
        - eot=False → 把文本挂起到 self._pending_text，回到 LISTENING 等更多语音/沉默；
          设置一个兜底 deadline，超过后即便 EoT 还没判 True 也强制 flush（防卡死）。
        """
        try:
            silence_ms = int((time.monotonic() - self._last_speech_ts) * 1000) \
                if self._last_speech_ts else 0
            verdict = detect_chinese_eot(
                asr_text=user_text, silence_ms=silence_ms,
                last_token_ts=self._last_speech_ts, now=time.monotonic(),
            )
        except Exception as e:  # pragma: no cover - 防御
            logger.debug("中文 EoT 判断异常，按立刻切轮处理: %s", e)
            return False

        if verdict.get("eot"):
            logger.info("中文 EoT=True（%s），立刻 flush 本轮", verdict.get("reason"))
            return False

        # eot=False：挂起，等更多 silence
        self._pending_text = user_text
        self._pending_audio = audio_bytes
        self._eot_grace_deadline = time.monotonic() + 3.0  # 最多再等 3 秒强制 flush
        with self._lock:
            self._set_state(PipelineState.LISTENING)
        logger.info("中文 EoT=False（%s），延迟切轮，挂起文本：%r",
                    verdict.get("reason"), user_text)
        return True

    def _flush_pending_if_due(self) -> None:
        """若有挂起文本且已到宽限 deadline，强制 flush 给 LLM（防 EoT 卡死）。"""
        if not self._pending_text:
            return
        if time.monotonic() < self._eot_grace_deadline:
            return
        text, audio = self._pending_text, self._pending_audio
        self._pending_text = ""
        self._pending_audio = b""
        logger.info("EoT 宽限到期，强制 flush 挂起文本：%r", text)
        self._respond_to_user(text)

    # ------------------------------------------------------------------ #
    # 真正走 ASR→LLM→TTS 的响应链
    # ------------------------------------------------------------------ #
    def _respond_to_user(self, user_text: str) -> None:
        """对一段用户文本跑 LLM→TTS（锁外执行，可能耗时）。"""
        # 2. LLM
        try:
            ai_reply = self.llm.generate_reply(user_text)
        except Exception as e:
            logger.warning("LLM 失败：%s", e)
            ai_reply = ""

        if not ai_reply.strip():
            with self._lock:
                self._set_state(PipelineState.LISTENING)
            return

        logger.info("AI 回复：%s", ai_reply)

        # 3. TTS
        with self._lock:
            if not self._running:
                return
            self._set_state(PipelineState.SPEAKING_AI)

        try:
            self.tts.speak(ai_reply)
        except Exception as e:
            logger.warning("TTS 失败：%s", e)
            with self._lock:
                self._set_state(PipelineState.LISTENING)
            return

        self._utterance_count += 1

        # 注意：真实全双工中，TTS 播完后由 TTS 实现的回调通知「播完了」
        # 桩实现里，我们假设 speak() 是阻塞的（同步播报），播完就回 LISTENING
        # 真实场景中应该注册 on_playback_finished 回调
        with self._lock:
            if self._running and self._state == PipelineState.SPEAKING_AI:
                # 检查 TTS 是否真的播完了
                if not self.tts.is_speaking():
                    self._set_state(PipelineState.LISTENING)

    # ------------------------------------------------------------------ #
    # 状态变更通知
    # ------------------------------------------------------------------ #

    def _set_state(self, new_state: PipelineState) -> None:
        """内部：切换状态并触发回调。"""
        old = self._state
        self._state = new_state
        if old != new_state and self.on_state_change is not None:
            try:
                self.on_state_change(new_state)
            except Exception as e:
                logger.debug("on_state_change 回调异常：%s", e)

    # ------------------------------------------------------------------ #
    # 统计 / 调试
    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """返回管线运行统计。"""
        return {
            "state": self._state.value,
            "running": self._running,
            "utterance_count": self._utterance_count,
            "barge_in_count": self._barge_in_count,
            "backchannel_count": self._backchannel_count,
            "pending_text": bool(self._pending_text),
            "chinese_eot": self._use_chinese_eot,
            "vad_type": type(self.vad).__name__,
            "asr_type": type(self.asr).__name__,
            "llm_type": type(self.llm).__name__,
            "tts_type": type(self.tts).__name__,
        }


# ======================================================================
# LiveKit 适配器 —— 懒加载 import livekit，未安装时上层降级
# ======================================================================

class LiveKitPipelineAdapter:
    """LiveKit 全双工管线适配器（R3.3-14）。

    本类把 `livekit-agents` 的 VoicePipelineAgent / AgentSession 包装成与
    `VoicePipeline` 一致的对外接口（start / stop / interrupt / feed_audio / state），
    让上层业务无感切换后端。

    **降级约定**：未安装 livekit 时自动退回桩实现——
    构造函数不直接抛 ImportError，而是设置 `self._livekit_available = False`，
    并在真正需要连服务器的 `connect()` 阶段才抛 ImportError，由上层 catch 后
    回落到 `VoicePipeline`（离线桩）。这样单元测试里即使没装 livekit 也能实例化。

    用法（真要连服务器时才用）：
        adapter = LiveKitPipelineAdapter(url=..., token=...)
        adapter.connect()        # 这里才会真正 import livekit 并连 server
        adapter.start()
        ...
        adapter.stop()

    单测里只验证「能被实例化、能读到降级标志」，不连真实 server（没 key）。
    """

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        vad: Optional[VADInterface] = None,
        asr: Optional[ASRInterface] = None,
        llm: Optional[LLMInterface] = None,
        tts: Optional[TTSInterface] = None,
    ) -> None:
        self.url = url
        self.token = token
        self.vad = vad
        self.asr = asr
        self.llm = llm
        self.tts = tts

        # 懒加载探测：能不能 import livekit？
        self._livekit_available: bool = False
        self._import_error: Optional[Exception] = None
        try:
            import livekit  # noqa: F401
            import livekit.agents  # noqa: F401
            self._livekit_available = True
        except ImportError as e:
            self._livekit_available = False
            self._import_error = e
            logger.info(
                "LiveKitPipelineAdapter: 未安装 livekit（%s），将在 connect() 时降级到桩实现。",
                e,
            )

        # 运行态
        self._state: PipelineState = PipelineState.IDLE
        self._running = False
        self._agent_session: Any = None  # livekit AgentSession 实例（连上后才有）
        self._connected: bool = False    # 是否已真正连上 server
        self._connect_error: Optional[Exception] = None

    # ------------------------------------------------------------------ #
    # 探测 / 状态
    # ------------------------------------------------------------------ #

    @property
    def livekit_available(self) -> bool:
        """当前环境是否装上了 livekit-agents。"""
        return self._livekit_available

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ #
    # 生命周期（与 VoicePipeline 对齐）
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """启动适配器。未连 livekit server 时只切状态，不真正连。"""
        self._running = True
        self._state = PipelineState.LISTENING
        logger.info("LiveKitPipelineAdapter.start() → LISTENING (livekit_available=%s)",
                    self._livekit_available)

    def stop(self) -> None:
        self._running = False
        self._state = PipelineState.IDLE
        self._agent_session = None
        logger.info("LiveKitPipelineAdapter.stop() → IDLE")

    def interrupt(self) -> None:
        self._state = PipelineState.LISTENING
        if self.tts is not None:
            try:
                self.tts.stop()
            except Exception:
                pass

    def feed_audio(self, chunk: bytes) -> None:
        """喂入音频帧。离线模式下直接透传给桩 VAD，便于单测走通状态机。"""
        if self.vad is not None:
            self.vad.feed_audio(chunk)

    # ------------------------------------------------------------------ #
    # 真正连 livekit server（未装时抛 ImportError 由上层降级）
    # ------------------------------------------------------------------ #

    def connect(self) -> Any:
        """真正建立到 LiveKit Room 的连接。

        接线策略（R3.3-14 / C7）：
          - 未装 livekit：抛 ImportError，由上层 catch 后回落到离线 VoicePipeline；
          - 已装 livekit：尝试真实构造 AgentSession / VoicePipelineAgent 对象。
            * 即使没有 LIVEKIT_URL/TOKEN，也要能把对象构造出来（只构造不真正连）；
            * 真正连 server 失败（无 URL / 网络不通）不抛栈，降级为「已构造未连接」，
              后续 start()/stop() 只切本地状态、不真推流，保证不崩。

        Returns:
            构造好的 AgentSession（可能处于未连接状态）。
        """
        if not self._livekit_available:
            raise ImportError(
                "livekit / livekit-agents 未安装，无法 connect()。"
                "请执行 `pip install livekit livekit-agents`，"
                "或回退到离线 VoicePipeline（SilentVAD+EchoASR+StubLLM+StubTTS）。"
                f"原始导入错误：{self._import_error!r}"
            )

        # 已装 livekit：真实构造对象（构造不等于连 server）
        self._connected = False

        # 红断言修复（R3.3-14）：装了 livekit 但没给 url/token，绝不能静默
        # 返回一个「看似已就绪」的空 session 冒充连过。必须显式 raise，让上层
        # 据此降级到离线 VoicePipeline；否则 UI 会以为全双工后端已接通。
        if not (self.url and self.token):
            raise NotImplementedError(
                "livekit-agents 已安装，但未提供 LIVEKIT_URL / LIVEKIT_TOKEN；"
                "拒绝静默空连。请配置端点，或回退到离线 VoicePipeline"
                "（EnergyVAD+EchoASR+LLMGateway+EdgeTTS）。"
            )

        try:
            from livekit.agents import AgentSession  # type: ignore
            # VoicePipelineAgent 依赖 vad/stt/llm/tts 适配器；这里只构造一个最小 session。
            self._agent_session = AgentSession()
            logger.info("LiveKit AgentSession 对象已构造（未连接 server）")
        except Exception as e:  # noqa: BLE001
            # 构造都失败：降级为「不可用」，但不抛栈——start/stop 仍可空转
            logger.warning("LiveKit AgentSession 构造失败，降级为离线空转: %s", e)
            self._agent_session = None
            self._connect_error = e
            return None

        # 走到这里说明 url/token 都有：尝试真正连 server
        try:
            # 真实房间连接（异步，这里只触发，不等结果）
            self._agent_session.connect(url=self.url, token=self.token)  # type: ignore[attr-defined]
            self._connected = True
            logger.info("LiveKit 已连接 server: %s", self.url)
        except Exception as e:  # noqa: BLE001
            # 连不上 server 不抛栈：标记未连接，start/stop 空转
            logger.warning("LiveKit 连不上 server（%s），降级为已构造未连接", e)
            self._connect_error = e
            self._connected = False

        return self._agent_session


# ======================================================================
# LiveKit 插拔点说明（纯注释，不 import livekit）
# ======================================================================
#
# 【如何切换到 LiveKit 全双工后端】
#
# 当离线环境解除、可以 pip install livekit-agents 后：
#
# 1. VAD 替换：
#    from livekit.agents import vad as lk_vad
#    vad = lk_vad.SileroVADAdapter()  # 实现 VADInterface 的包装类
#
# 2. ASR 替换：
#    from livekit.agents.stt import WhisperSTT
#    asr = WhisperSTT(model="base")  # 包装成 ASRInterface
#
# 3. LLM 替换：
#    from livekit.agents.llm import ChatContext
#    # 直接复用现有 LLMGateway，无需替换
#
# 4. TTS 替换：
#    from livekit.agents.tts import TTSAdapter
#    tts = TTSAdapter(...)  # 实现 TTSInterface
#
# 5. （可选）整体替换：
#    from livekit.agents import VoicePipelineAgent
#    agent = VoicePipelineAgent(vad=..., stt=..., llm=..., tts=...)
#    # VoicePipelineAgent 自带全双工 + barge-in，可完全替代 VoicePipeline 类
#
# 当前骨架的意义：
#    - 先把接口定下来，业务逻辑（人设、对话流）不绑定具体 SDK
#    - 以后换 LiveKit 或换别的框架，只换实现类，管线逻辑不变
# ======================================================================
