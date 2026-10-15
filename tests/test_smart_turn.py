# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - SmartTurn 全双工状态机单测（R3.3-35）

5 个典型场景：
  S1  用户正常说完 → FLUSH_TURN（沉默 600ms 后提交 LLM）
  S2  AI 播报中用户长句 barge-in → INTERRUPT_TTS
  S3  AI 播报中用户短应答词（"嗯"）→ SPEAK_BACKCHANNEL，不打断
  S4  AI TTS 自然播完（tts_active True→False）→ END_TURN
  S5  barge-in 后用户继续说完 → OVERLAP_TALK → FLUSH_TURN
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.smart_turn import SmartTurn, TurnState, TurnAction  # noqa: E402


class TestSmartTurnNormalFlow(unittest.TestCase):
    """S1：用户正常说完 → FLUSH_TURN。"""

    def test_user_finish_flushes_turn(self):
        stm = SmartTurn()
        # 开口
        act = stm.update({"vad_state": "speech_start", "asr_text": "", "tts_active": False,
                          "last_user_ms": 0, "speech_duration_ms": 0})
        self.assertEqual(stm.state, TurnState.SPEAKING_USER_TURN)

        # 说话中
        stm.update({"vad_state": "speech", "asr_text": "今天好累", "tts_active": False,
                    "last_user_ms": 0, "speech_duration_ms": 800})
        self.assertEqual(stm.state, TurnState.SPEAKING_USER_TURN)

        # 说完 + 沉默 600ms
        act = stm.update({"vad_state": "silence", "asr_text": "今天好累", "tts_active": False,
                          "last_user_ms": 650, "speech_duration_ms": 0})
        self.assertEqual(act, TurnAction.FLUSH_TURN)
        self.assertEqual(stm.state, TurnState.THINKING)
        self.assertEqual(stm.flush_count, 1)


class TestSmartTurnBargeIn(unittest.TestCase):
    """S2：AI 播报中用户长句 barge-in → INTERRUPT_TTS。"""

    def test_long_user_speech_interrupts_ai(self):
        stm = SmartTurn()
        # 先把状态推到 AI 播报
        stm.state = TurnState.SPEAKING_AI_TURN

        # 用户开口 200ms（不到阈值，先不打断）
        act = stm.update({"vad_state": "speech", "asr_text": "等", "tts_active": True,
                          "last_user_ms": 0, "speech_duration_ms": 200})
        self.assertEqual(act, TurnAction.CONTINUE_LISTEN)

        # 用户说到 500ms，文本已经超过 3 字 → 真打断
        act = stm.update({"vad_state": "speech", "asr_text": "等一下我有话说", "tts_active": True,
                          "last_user_ms": 0, "speech_duration_ms": 500})
        self.assertEqual(act, TurnAction.INTERRUPT_TTS)
        self.assertEqual(stm.state, TurnState.OVERLAP_TALK)
        self.assertEqual(stm.barge_in_count, 1)


class TestSmartTurnBackchannel(unittest.TestCase):
    """S3：AI 播报中用户短应答词 → SPEAK_BACKCHANNEL，不打断。"""

    def test_short_ack_does_not_interrupt(self):
        stm = SmartTurn()
        stm.state = TurnState.SPEAKING_AI_TURN

        act = stm.update({"vad_state": "speech", "asr_text": "嗯", "tts_active": True,
                          "last_user_ms": 0, "speech_duration_ms": 100})
        self.assertEqual(act, TurnAction.SPEAK_BACKCHANNEL)
        self.assertEqual(stm.state, TurnState.BACKCHANNEL_WAIT)
        self.assertEqual(stm.backchannel_count, 1)

        # 短应答完了，AI 继续说
        act = stm.update({"vad_state": "silence", "asr_text": "嗯", "tts_active": True,
                          "last_user_ms": 200, "speech_duration_ms": 0})
        self.assertEqual(act, TurnAction.CONTINUE_LISTEN)
        self.assertEqual(stm.state, TurnState.SPEAKING_AI_TURN)


class TestSmartTurnAiEnd(unittest.TestCase):
    """S4：AI TTS 自然播完 → END_TURN。"""

    def test_ai_tts_finish_emits_end_turn(self):
        stm = SmartTurn()
        stm.state = TurnState.SPEAKING_AI_TURN

        # tts_active 由 True→False
        act = stm.update({"vad_state": "silence", "asr_text": "", "tts_active": False,
                          "last_user_ms": 0, "speech_duration_ms": 0})
        self.assertEqual(act, TurnAction.END_TURN)
        self.assertEqual(stm.state, TurnState.LISTENING_PASSIVE)
        self.assertEqual(stm.end_turn_count, 1)


class TestSmartTurnOverlapThenFlush(unittest.TestCase):
    """S5：barge-in 后用户继续说完 → OVERLAP_TALK → FLUSH_TURN。"""

    def test_overlap_then_flush(self):
        stm = SmartTurn()
        stm.state = TurnState.SPEAKING_AI_TURN

        # barge-in
        stm.update({"vad_state": "speech", "asr_text": "我不同意这个说法", "tts_active": True,
                    "last_user_ms": 0, "speech_duration_ms": 600})
        self.assertEqual(stm.state, TurnState.OVERLAP_TALK)

        # 用户继续说
        stm.update({"vad_state": "speech", "asr_text": "我不同意这个说法，因为...", "tts_active": False,
                    "last_user_ms": 0, "speech_duration_ms": 1200})
        self.assertEqual(stm.state, TurnState.OVERLAP_TALK)

        # 用户说完 + 沉默 600ms → FLUSH
        act = stm.update({"vad_state": "silence", "asr_text": "我不同意这个说法，因为...",
                          "tts_active": False, "last_user_ms": 700, "speech_duration_ms": 0})
        self.assertEqual(act, TurnAction.FLUSH_TURN)
        self.assertEqual(stm.state, TurnState.THINKING)


if __name__ == "__main__":
    unittest.main()
