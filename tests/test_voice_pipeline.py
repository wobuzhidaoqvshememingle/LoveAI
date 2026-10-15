# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - VoicePipeline 全双工语音管线单元测试

覆盖：
  - VAD 桩实现（SilentVAD / TriggerVAD）
  - ASR 桩实现（EchoASR）
  - LLM 桩实现（StubLLM）
  - TTS 桩实现（StubTTS）
  - VoicePipeline 生命周期：start / stop / interrupt
  - 完整对话流：speech_start → speech_end → ASR → LLM → TTS
  - Barge-in 打断：AI 播报中检测到用户开口 → tts.stop() 被调用
  - SpeechService.get_voice_pipeline() 接线
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.voice_pipeline import (  # noqa: E402
    PipelineState,
    VADEvent,
    VADEventType,
    SilentVAD,
    TriggerVAD,
    EchoASR,
    StubLLM,
    StubTTS,
    VoicePipeline,
    VADInterface,
    ASRInterface,
    LLMInterface,
    TTSInterface,
)


# ======================================================================
# VAD 桩测试
# ======================================================================

class TestSilentVAD(unittest.TestCase):
    def test_no_events_by_default(self):
        vad = SilentVAD()
        for _ in range(100):
            vad.feed_audio(b"\x00\x00" * 1024)
        events = vad.get_events()
        self.assertEqual(len(events), 0)

    def test_inject_event(self):
        vad = SilentVAD()
        vad.inject_event(VADEvent(VADEventType.SPEECH_START, timestamp_ms=100))
        events = vad.get_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, VADEventType.SPEECH_START)
        self.assertEqual(events[0].timestamp_ms, 100)

    def test_reset_clears_events(self):
        vad = SilentVAD()
        vad.inject_event(VADEvent(VADEventType.SPEECH_START))
        vad.reset()
        events = vad.get_events()
        self.assertEqual(len(events), 0)


class TestTriggerVAD(unittest.TestCase):
    def test_emits_speech_start_then_end(self):
        vad = TriggerVAD(silence_frames_before_speech=2, speech_duration_frames=3)
        # 前2帧静音
        for _ in range(2):
            vad.feed_audio(b"\x00\x00" * 1024)
        events = vad.get_events()
        # 第2帧后应该有 speech_start
        start_events = [e for e in events if e.type == VADEventType.SPEECH_START]
        self.assertEqual(len(start_events), 1)

        # 再喂3帧语音
        for _ in range(3):
            vad.feed_audio(b"\xff\x00" * 1024)
        events = vad.get_events()
        end_events = [e for e in events if e.type == VADEventType.SPEECH_END]
        self.assertEqual(len(end_events), 1)

    def test_reset_reenables_trigger(self):
        vad = TriggerVAD(silence_frames_before_speech=1, speech_duration_frames=1)
        vad.feed_audio(b"\x00\x00" * 100)  # silence
        vad.feed_audio(b"\xff\x00" * 100)  # speech start
        vad.feed_audio(b"\xff\x00" * 100)  # speech end
        vad.reset()
        vad.feed_audio(b"\x00\x00" * 100)
        events = vad.get_events()
        start_events = [e for e in events if e.type == VADEventType.SPEECH_START]
        self.assertEqual(len(start_events), 1)


# ======================================================================
# ASR 桩测试
# ======================================================================

class TestEchoASR(unittest.TestCase):
    def test_returns_default_text(self):
        asr = EchoASR(default_text="测试文本")
        result = asr.transcribe(b"\x00\x00" * 1000)
        self.assertEqual(result, "测试文本")

    def test_records_call_count(self):
        asr = EchoASR()
        asr.transcribe(b"a")
        asr.transcribe(b"bb")
        self.assertEqual(asr._call_count, 2)

    def test_reset(self):
        asr = EchoASR()
        asr.transcribe(b"x")
        asr.reset()
        self.assertEqual(asr._call_count, 0)


# ======================================================================
# LLM 桩测试
# ======================================================================

class TestStubLLM(unittest.TestCase):
    def test_returns_reply_text(self):
        llm = StubLLM(reply_text="我在听")
        result = llm.generate_reply("你好")
        self.assertEqual(result, "我在听")

    def test_records_last_input(self):
        llm = StubLLM()
        llm.generate_reply("今天好累")
        self.assertEqual(llm.last_user_input, "今天好累")

    def test_call_count(self):
        llm = StubLLM()
        llm.generate_reply("a")
        llm.generate_reply("b")
        llm.generate_reply("c")
        self.assertEqual(llm._call_count, 3)


# ======================================================================
# TTS 桩测试
# ======================================================================

class TestStubTTS(unittest.TestCase):
    def test_speak_sets_speaking(self):
        tts = StubTTS()
        tts.speak("你好")
        self.assertTrue(tts.is_speaking())
        self.assertEqual(len(tts.spoken_texts), 1)
        self.assertEqual(tts.spoken_texts[0], "你好")

    def test_stop_clears_speaking(self):
        tts = StubTTS()
        tts.speak("测试")
        self.assertTrue(tts.is_speaking())
        tts.stop()
        self.assertFalse(tts.is_speaking())
        self.assertEqual(tts.stop_count, 1)

    def test_stop_when_not_speaking_no_count(self):
        tts = StubTTS()
        tts.stop()
        self.assertEqual(tts.stop_count, 0)

    def test_finish_speaking(self):
        tts = StubTTS()
        tts.speak("播完")
        tts.finish_speaking()
        self.assertFalse(tts.is_speaking())
        # 自然播完不算 stop
        self.assertEqual(tts.stop_count, 0)


# ======================================================================
# VoicePipeline 生命周期测试
# ======================================================================

class TestVoicePipelineLifecycle(unittest.TestCase):
    def test_default_stubs_create(self):
        pipeline = VoicePipeline()
        self.assertIsInstance(pipeline.vad, SilentVAD)
        self.assertIsInstance(pipeline.asr, EchoASR)
        self.assertIsInstance(pipeline.llm, StubLLM)
        self.assertIsInstance(pipeline.tts, StubTTS)

    def test_start_sets_listening_state(self):
        pipeline = VoicePipeline()
        self.assertEqual(pipeline.state, PipelineState.IDLE)
        pipeline.start()
        self.assertEqual(pipeline.state, PipelineState.LISTENING)
        self.assertTrue(pipeline.is_running)
        pipeline.stop()

    def test_stop_returns_idle(self):
        pipeline = VoicePipeline()
        pipeline.start()
        pipeline.stop()
        self.assertEqual(pipeline.state, PipelineState.IDLE)
        self.assertFalse(pipeline.is_running)

    def test_double_start_no_crash(self):
        pipeline = VoicePipeline()
        pipeline.start()
        pipeline.start()  # 不应崩溃
        self.assertTrue(pipeline.is_running)
        pipeline.stop()

    def test_stop_when_not_running_no_crash(self):
        pipeline = VoicePipeline()
        pipeline.stop()  # 不应崩溃
        self.assertEqual(pipeline.state, PipelineState.IDLE)

    def test_interrupt_returns_listening(self):
        vad = SilentVAD()
        tts = StubTTS()
        pipeline = VoicePipeline(vad=vad, tts=tts)
        pipeline.start()
        # 模拟正在播报
        tts.speak("正在说...")
        pipeline._set_state(PipelineState.SPEAKING_AI)
        # interrupt
        pipeline.interrupt()
        self.assertEqual(pipeline.state, PipelineState.LISTENING)
        self.assertFalse(tts.is_speaking())
        pipeline.stop()


# ======================================================================
# VoicePipeline 完整对话流测试
# ======================================================================

class TestVoicePipelineConversation(unittest.TestCase):
    def test_full_utterance_flow(self):
        """完整流程：用户说话 → ASR → LLM → TTS 播报"""
        vad = SilentVAD()
        asr = EchoASR(default_text="你好呀")
        llm = StubLLM(reply_text="你好，很高兴见到你")
        tts = StubTTS()

        pipeline = VoicePipeline(vad=vad, asr=asr, llm=llm, tts=tts)
        pipeline.start()

        # 用户开始说话
        vad.inject_event(VADEvent(VADEventType.SPEECH_START))
        pipeline.feed_audio(b"\x01\x02" * 500)  # 模拟语音帧
        self.assertEqual(pipeline.state, PipelineState.SPEAKING_USER)

        # 用户说完
        vad.inject_event(VADEvent(VADEventType.SPEECH_END))
        pipeline.feed_audio(b"")  # 触发事件处理

        # 验证 ASR 被调用、LLM 被调用、TTS 被调用
        self.assertEqual(llm.last_user_input, "你好呀")
        self.assertEqual(len(tts.spoken_texts), 1)
        self.assertEqual(tts.spoken_texts[0], "你好，很高兴见到你")

        pipeline.stop()

    def test_empty_asr_result_skips_llm(self):
        """ASR 返回空文本时，不应调用 LLM 和 TTS。"""
        vad = SilentVAD()
        asr = EchoASR(default_text="")  # 空文本
        llm = StubLLM()
        tts = StubTTS()

        pipeline = VoicePipeline(vad=vad, asr=asr, llm=llm, tts=tts)
        pipeline.start()

        vad.inject_event(VADEvent(VADEventType.SPEECH_START))
        pipeline.feed_audio(b"\x01\x02" * 100)
        vad.inject_event(VADEvent(VADEventType.SPEECH_END))
        pipeline.feed_audio(b"")

        # LLM 不应被调用
        self.assertEqual(llm._call_count, 0)
        self.assertEqual(len(tts.spoken_texts), 0)

        pipeline.stop()


# ======================================================================
# Barge-in 打断测试（核心！）
# ======================================================================

class TestBargeIn(unittest.TestCase):
    def test_barge_in_stops_tts(self):
        """AI 正在播报时，用户开口 → TTS.stop() 被调用。"""
        vad = SilentVAD()
        asr = EchoASR(default_text="打断你")
        llm = StubLLM(reply_text="不会被播报完")
        tts = StubTTS()

        pipeline = VoicePipeline(vad=vad, asr=asr, llm=llm, tts=tts)
        pipeline.start()

        # 先让 AI 播报一段
        tts.speak("我在说话，不要打断我")
        pipeline._set_state(PipelineState.SPEAKING_AI)
        self.assertTrue(tts.is_speaking())
        self.assertEqual(pipeline.state, PipelineState.SPEAKING_AI)

        # 用户突然开口 → barge-in！
        vad.inject_event(VADEvent(VADEventType.SPEECH_START))
        pipeline.feed_audio(b"\x01\x02" * 200)

        # 验证：TTS 被 stop 了
        self.assertFalse(tts.is_speaking())
        self.assertGreaterEqual(tts.stop_count, 1)
        # 状态切回用户说话
        self.assertEqual(pipeline.state, PipelineState.SPEAKING_USER)
        # barge_in_count 应该 >= 1
        self.assertGreaterEqual(pipeline._barge_in_count, 1)

        pipeline.stop()

    def test_barge_in_count_increments(self):
        """多次 barge-in 计数正确。"""
        vad = SilentVAD()
        pipeline = VoicePipeline(vad=vad, tts=StubTTS())
        pipeline.start()

        # 第一次 barge-in
        pipeline.tts.speak("第一次")
        pipeline._set_state(PipelineState.SPEAKING_AI)
        vad.inject_event(VADEvent(VADEventType.SPEECH_START))
        pipeline.feed_audio(b"\x00" * 10)
        self.assertEqual(pipeline._barge_in_count, 1)

        # 恢复播报
        pipeline.tts.speak("第二次")
        pipeline._set_state(PipelineState.SPEAKING_AI)
        vad.inject_event(VADEvent(VADEventType.SPEECH_START))
        pipeline.feed_audio(b"\x00" * 10)
        self.assertEqual(pipeline._barge_in_count, 2)

        pipeline.stop()


# ======================================================================
# 状态回调测试
# ======================================================================

class TestStateCallbacks(unittest.TestCase):
    def test_on_state_change_called(self):
        states_seen = []
        def callback(state):
            states_seen.append(state)

        pipeline = VoicePipeline(
            on_state_change=callback,
        )
        pipeline.start()
        # start 会触发 IDLE→LISTENING
        self.assertIn(PipelineState.LISTENING, states_seen)

        pipeline.stop()
        # stop 会触发 → IDLE
        self.assertIn(PipelineState.IDLE, states_seen)


# ======================================================================
# SpeechService.get_voice_pipeline() 接线测试
# ======================================================================

class TestSpeechServiceVoicePipelineWiring(unittest.TestCase):
    def test_get_voice_pipeline_returns_instance(self):
        """SpeechService.get_voice_pipeline() 应返回 VoicePipeline 实例。"""
        from services.speech_service import SpeechService
        svc = SpeechService()
        pipeline = svc.get_voice_pipeline()
        self.assertIsNotNone(pipeline)
        from services.voice_pipeline import VoicePipeline
        self.assertIsInstance(pipeline, VoicePipeline)

    def test_get_voice_pipeline_cached(self):
        """第二次调用应返回同一个实例（懒加载缓存）。"""
        from services.speech_service import SpeechService
        svc = SpeechService()
        p1 = svc.get_voice_pipeline()
        p2 = svc.get_voice_pipeline()
        self.assertIs(p1, p2)

    def test_get_voice_pipeline_failure_returns_none(self):
        """导入失败时应返回 None，不崩溃。"""
        from services.speech_service import SpeechService
        svc = SpeechService()
        # 模拟导入失败
        with patch("builtins.__import__", side_effect=ImportError("mock fail")):
            pipeline = svc.get_voice_pipeline()
            self.assertIsNone(pipeline)


# ======================================================================
# 统计信息测试
# ======================================================================

class TestVoicePipelineStats(unittest.TestCase):
    def test_get_stats(self):
        pipeline = VoicePipeline()
        stats = pipeline.get_stats()
        self.assertIn("state", stats)
        self.assertIn("running", stats)
        self.assertIn("utterance_count", stats)
        self.assertIn("barge_in_count", stats)
        self.assertIn("vad_type", stats)
        self.assertEqual(stats["state"], "idle")
        self.assertFalse(stats["running"])


if __name__ == "__main__":
    unittest.main()
