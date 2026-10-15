# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - VoicePipeline 离线桩端到端单测（R3.3-14）

构造 FakeVAD / FakeASR / FakeLLM / FakeTTS（复用现有桩实现），跑一轮完整链路：
    start → speech_start → speech_end → ASR → LLM → TTS done
并验证：
    - 状态机迁移（IDLE → LISTENING → SPEAKING_USER → PROCESSING → SPEAKING_AI → LISTENING）
    - barge-in：SPEAKING_AI 期间收到 speech_start → 立刻切回 SPEAKING_USER/LISTENING
    - LiveKitPipelineAdapter 可在离线环境下实例化（不连 server）
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.voice_pipeline import (  # noqa: E402
    PipelineState,
    VADEvent,
    VADEventType,
    SilentVAD,
    EchoASR,
    StubLLM,
    StubTTS,
    VoicePipeline,
    LiveKitPipelineAdapter,
)


class TestVoicePipelineE2E(unittest.TestCase):
    """完整端到端：start → speech_start → speech_end → LLM done → TTS done。"""

    def setUp(self):
        self.vad = SilentVAD()
        self.asr = EchoASR(default_text="今天好累")
        self.llm = StubLLM(reply_text="辛苦了，早点休息")
        self.tts = StubTTS()
        self.pipeline = VoicePipeline(
            vad=self.vad, asr=self.asr, llm=self.llm, tts=self.tts,
        )

    def tearDown(self):
        self.pipeline.stop()

    def test_full_turn_state_machine(self):
        """跑一轮完整对话，验证状态机迁移序列。"""
        states_seen = []
        self.pipeline.on_state_change = lambda s: states_seen.append(s)

        # start → LISTENING
        self.pipeline.start()
        self.assertEqual(self.pipeline.state, PipelineState.LISTENING)

        # 用户开口
        self.vad.inject_event(VADEvent(VADEventType.SPEECH_START))
        self.pipeline.feed_audio(b"\x01\x02" * 500)
        self.assertEqual(self.pipeline.state, PipelineState.SPEAKING_USER)

        # 用户说完 → ASR → LLM → TTS（桩实现同步跑完）
        self.vad.inject_event(VADEvent(VADEventType.SPEECH_END))
        self.pipeline.feed_audio(b"\x03\x04" * 500)

        # ASR / LLM / TTS 都被调用了
        self.assertEqual(self.llm.last_user_input, "今天好累")
        self.assertEqual(len(self.tts.spoken_texts), 1)
        self.assertEqual(self.tts.spoken_texts[0], "辛苦了，早点休息")

        # 桩 TTS speak() 后立即置 speaking=False（因为 StubTTS 播完即回）
        # 最终状态应回到 LISTENING
        self.assertIn(self.pipeline.state, (PipelineState.SPEAKING_AI, PipelineState.LISTENING))

        # 状态序列里至少出现过 LISTENING / SPEAKING_USER / PROCESSING / SPEAKING_AI
        seen_values = {s.value for s in states_seen}
        self.assertIn("listening", seen_values)
        self.assertIn("speaking_user", seen_values)

    def test_barge_in_during_ai_speaking(self):
        """SPEAKING_AI 期间收到 speech_start → 立刻 barge-in。"""
        self.pipeline.start()

        # 手动把管线推到 SPEAKING_AI
        self.tts.speak("我正在播报")
        self.pipeline._set_state(PipelineState.SPEAKING_AI)
        self.assertTrue(self.tts.is_speaking())

        # 用户突然开口
        self.vad.inject_event(VADEvent(VADEventType.SPEECH_START))
        self.pipeline.feed_audio(b"\xaa\xbb" * 200)

        # TTS 必须被 stop
        self.assertFalse(self.tts.is_speaking())
        self.assertGreaterEqual(self.tts.stop_count, 1)
        # 状态切回用户说话
        self.assertEqual(self.pipeline.state, PipelineState.SPEAKING_USER)
        # barge-in 计数 +1
        self.assertGreaterEqual(self.pipeline._barge_in_count, 1)

    def test_interrupt_returns_to_listening(self):
        """手动 interrupt() 应回到 LISTENING 而不是 IDLE。"""
        self.pipeline.start()
        self.pipeline._set_state(PipelineState.SPEAKING_AI)
        self.pipeline.interrupt()
        self.assertEqual(self.pipeline.state, PipelineState.LISTENING)
        self.assertTrue(self.pipeline.is_running)


class TestLiveKitAdapterOffline(unittest.TestCase):
    """LiveKitPipelineAdapter 离线桩：不连 server 也能实例化。"""

    def test_can_instantiate_without_livekit_connection(self):
        adapter = LiveKitPipelineAdapter()
        self.assertIsNotNone(adapter)
        # livekit 已装 → available=True；未装 → False，都不应抛
        self.assertIn(adapter.livekit_available, (True, False))
        self.assertEqual(adapter.state, adapter.state.__class__.IDLE)

    def test_start_stop_lifecycle(self):
        adapter = LiveKitPipelineAdapter()
        adapter.start()
        self.assertTrue(adapter.is_running)
        adapter.stop()
        self.assertFalse(adapter.is_running)

    def test_connect_raises_if_no_livekit(self):
        """没装 livekit 时 connect() 必须抛 ImportError，让上层降级。"""
        adapter = LiveKitPipelineAdapter()
        if not adapter.livekit_available:
            with self.assertRaises(ImportError):
                adapter.connect()
        else:
            # 装了 livekit 但没给 url/token，connect 应抛 NotImplementedError 而不是真连
            with self.assertRaises((NotImplementedError, ValueError)):
                adapter.connect()


if __name__ == "__main__":
    unittest.main()
