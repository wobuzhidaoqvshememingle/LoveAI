# -*- coding: utf-8 -*-
"""
LoveAI v2.0 — Smart Turn 全双工状态机（R3.3-35）

对标 LiveKit Agents 的「智能轮次管理」：在 VAD/ASR/LLM/TTS 之上加一层
轻量状态机，决定每一个观测（observation）到来时应该采取什么动作。

状态枚举（TurnState）：
    LISTENING_PASSIVE  空闲监听，无人说话
    LISTENING_ACTIVE   用户刚开口，正在监听其说话内容
    THINKING           用户说完，等 LLM 生成
    SPEAKING_USER_TURN 用户正在说话（VAD speech 期间）
    SPEAKING_AI_TURN   AI 正在播报
    OVERLAP_TALK       双方叠话（barge-in 发生后的过渡态）
    BACKCHANNEL_WAIT   用户发出短应答词，AI 等一下再继续说

动作枚举（TurnAction）：
    CONTINUE_LISTEN   什么都不做，继续监听
    INTERRUPT_TTS     立刻打断 AI 播报（barge-in）
    FLUSH_TURN        把当前用户话轮提交给 LLM
    SPEAK_BACKCHANNEL AI 发一个短应答（"嗯/对"）但不抢话
    END_TURN          当前 AI 播报自然结束，收回轮次

判定规则（R3.3-35）：
    R1  用户说话持续 > 300ms 且文本长度 > 3 字  → INTERRUPT_TTS（真打断）
    R2  AI 播报中用户插入 ≤ 3 字短词             → SPEAK_BACKCHANNEL（不打断）
    R3  用户说完后沉默 ≥ 600ms                  → FLUSH_TURN（提交给 LLM）
    R4  AI TTS 自然播完（tts_active 由 True→False）→ END_TURN
    R5  短应答后用户继续沉默/说话，回到对应状态

observation 字段（dict）：
    vad_state          "silence" | "speech_start" | "speech" | "speech_end"
    asr_text           当前已识别文本（partial 或 final）
    tts_active         AI TTS 当前是否在播报
    last_user_ms       距离上次用户语音结束的毫秒数（speech_end 后计时）
    speech_duration_ms 自 speech_start 起用户持续说话的毫秒数
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ======================================================================
# 枚举
# ======================================================================

class TurnState(Enum):
    LISTENING_PASSIVE = "listening_passive"
    LISTENING_ACTIVE = "listening_active"
    THINKING = "thinking"
    SPEAKING_USER_TURN = "speaking_user_turn"
    SPEAKING_AI_TURN = "speaking_ai_turn"
    OVERLAP_TALK = "overlap_talk"
    BACKCHANNEL_WAIT = "backchannel_wait"


class TurnAction(Enum):
    CONTINUE_LISTEN = "continue_listen"
    INTERRUPT_TTS = "interrupt_tts"
    FLUSH_TURN = "flush_turn"
    SPEAK_BACKCHANNEL = "speak_backchannel"
    END_TURN = "end_turn"


# ======================================================================
# 阈值
# ======================================================================

BARGE_IN_MIN_MS = 300        # 用户说话超过 300ms 才算真打断
SHORT_ACK_MAX_CHARS = 3      # ≤3 字视为短应答词
END_TURN_SILENCE_MS = 600    # 用户说完后沉默 600ms 触发 FLUSH_TURN

# 常见中文短应答词（backchannel）—— 命中即按短词处理
_SHORT_ACK_WORDS = {"嗯", "嗯嗯", "哦", "噢", "啊", "好", "好吧", "对", "对对", "是", "是的", "行", "ok", "OK"}


# ======================================================================
# 状态机
# ======================================================================

class SmartTurn:
    """全双工轮次状态机。无外部依赖，纯函数式 update。

    用法：
        stm = SmartTurn()
        for obs in audio_stream_observations:
            action = stm.update(obs)
            dispatch(action, obs)
    """

    def __init__(
        self,
        barge_in_min_ms: int = BARGE_IN_MIN_MS,
        short_ack_max_chars: int = SHORT_ACK_MAX_CHARS,
        end_turn_silence_ms: int = END_TURN_SILENCE_MS,
    ) -> None:
        self.state: TurnState = TurnState.LISTENING_PASSIVE
        self.barge_in_min_ms = barge_in_min_ms
        self.short_ack_max_chars = short_ack_max_chars
        self.end_turn_silence_ms = end_turn_silence_ms

        # 统计
        self.barge_in_count = 0
        self.backchannel_count = 0
        self.flush_count = 0
        self.end_turn_count = 0

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #

    def update(self, observation: Dict[str, Any]) -> TurnAction:
        """根据观测推进状态机，返回本轮该执行的动作。"""
        vad_state: str = observation.get("vad_state", "silence")
        asr_text: str = observation.get("asr_text", "") or ""
        tts_active: bool = bool(observation.get("tts_active", False))
        last_user_ms: int = int(observation.get("last_user_ms", 0) or 0)
        speech_duration_ms: int = int(observation.get("speech_duration_ms", 0) or 0)

        # ---- R4：AI 自然播完 ----
        if self.state == TurnState.SPEAKING_AI_TURN and not tts_active:
            self.state = TurnState.LISTENING_PASSIVE
            self.end_turn_count += 1
            logger.debug("[SmartTurn] AI 播报自然结束 → END_TURN")
            return TurnAction.END_TURN

        # ---- R2 / R1：AI 正在播报，用户开口 ----
        if self.state == TurnState.SPEAKING_AI_TURN and vad_state in ("speech_start", "speech"):
            return self._handle_user_interrupting_ai(asr_text, speech_duration_ms)

        # ---- BACKCHANNEL_WAIT：短应答后，看用户下一步 ----
        if self.state == TurnState.BACKCHANNEL_WAIT:
            return self._handle_backchannel_followup(vad_state, tts_active)

        # ---- OVERLAP_TALK：叠话过渡态，等用户 speech_end 后 flush ----
        if self.state == TurnState.OVERLAP_TALK:
            if vad_state == "speech_end" or (vad_state == "silence" and last_user_ms >= self.end_turn_silence_ms):
                self.state = TurnState.THINKING
                self.flush_count += 1
                logger.debug("[SmartTurn] 叠话结束 → FLUSH_TURN")
                return TurnAction.FLUSH_TURN
            return TurnAction.CONTINUE_LISTEN

        # ---- 用户说话中 ----
        if vad_state == "speech_start":
            self.state = TurnState.LISTENING_ACTIVE
            self.state = TurnState.SPEAKING_USER_TURN
            return TurnAction.CONTINUE_LISTEN

        if self.state == TurnState.SPEAKING_USER_TURN:
            if vad_state == "speech_end" or (vad_state == "silence" and last_user_ms >= self.end_turn_silence_ms):
                # R3：用户说完，沉默 600ms 触发 FLUSH
                self.state = TurnState.THINKING
                self.flush_count += 1
                logger.debug("[SmartTurn] 用户说完（沉默 %dms）→ FLUSH_TURN", last_user_ms)
                return TurnAction.FLUSH_TURN
            return TurnAction.CONTINUE_LISTEN

        # ---- THINKING：等 LLM，TT S一旦 active 就切到 AI 播报 ----
        if self.state == TurnState.THINKING and tts_active:
            self.state = TurnState.SPEAKING_AI_TURN
            return TurnAction.CONTINUE_LISTEN

        # ---- LISTENING_PASSIVE / LISTENING_ACTIVE：默认继续 ----
        return TurnAction.CONTINUE_LISTEN

    # ------------------------------------------------------------------ #
    # 内部：AI 播报中用户插话
    # ------------------------------------------------------------------ #

    def _handle_user_interrupting_ai(self, asr_text: str, speech_duration_ms: int) -> TurnAction:
        text = asr_text.strip()
        is_short = self._is_short_ack(text)

        # R2：短应答词 → SPEAK_BACKCHANNEL，不打断
        if is_short:
            self.state = TurnState.BACKCHANNEL_WAIT
            self.backchannel_count += 1
            logger.debug("[SmartTurn] 用户短应答 %r → SPEAK_BACKCHANNEL", text)
            return TurnAction.SPEAK_BACKCHANNEL

        # R1：说话持续 > 300ms 且不是短词 → 真打断
        if speech_duration_ms >= self.barge_in_min_ms:
            self.state = TurnState.OVERLAP_TALK
            self.barge_in_count += 1
            logger.debug("[SmartTurn] 用户 barge-in（%dms, text=%r）→ INTERRUPT_TTS",
                         speech_duration_ms, text)
            return TurnAction.INTERRUPT_TTS

        # 才刚开始说，不到 300ms：先按 CONTINUE_LISTEN，等下一帧判断
        return TurnAction.CONTINUE_LISTEN

    def _handle_backchannel_followup(self, vad_state: str, tts_active: bool) -> TurnAction:
        """短应答之后：用户继续说话 → 切回 USER_TURN；用户沉默/AI 还在说 → 让 AI 继续。"""
        if vad_state in ("speech_start", "speech"):
            # 用户接着说长句了，按真处理
            self.state = TurnState.SPEAKING_USER_TURN
            return TurnAction.CONTINUE_LISTEN
        if vad_state == "speech_end" or vad_state == "silence":
            # 短应答完了，AI 继续说
            if tts_active:
                self.state = TurnState.SPEAKING_AI_TURN
            else:
                self.state = TurnState.LISTENING_PASSIVE
            return TurnAction.CONTINUE_LISTEN
        return TurnAction.CONTINUE_LISTEN

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #

    def _is_short_ack(self, text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        if len(t) > self.short_ack_max_chars:
            return False
        return t in _SHORT_ACK_WORDS

    # ------------------------------------------------------------------ #
    # 供上层手动切态（例如 LLM 生成完主动通知）
    # ------------------------------------------------------------------ #

    def mark_llm_done(self) -> None:
        """上层 LLM 生成完毕、即将开始 TTS 时调用。"""
        if self.state == TurnState.THINKING:
            # 等下一帧 observation 里 tts_active=True 再切 SPEAKING_AI_TURN
            pass

    def reset(self) -> None:
        self.state = TurnState.LISTENING_PASSIVE
        self.barge_in_count = 0
        self.backchannel_count = 0
        self.flush_count = 0
        self.end_turn_count = 0
