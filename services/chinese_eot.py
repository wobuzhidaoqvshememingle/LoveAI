# -*- coding: utf-8 -*-
"""
LoveAI v2.0 — 中文 EoT（End of Turn）二次判断（R3.3-36）

不要直接套用英文 EoT（只看 silence）。中文有自己的结束信号：

  a) 句末标点（。？！…!?）出现 + 沉默 ≥ 800ms           → 真结束，eot=True
  b) 疑问/连接词尾巴（然后/就是说/那个/对吧...）出现     → 延长判定，不立刻切
  c) 短应答词（嗯/哦/好/对）+ 沉默 ≤ 500ms             → backchannel，不切轮次
  d) 句中逗号（，,）后沉默                              → 不切轮次，≥3s 才算真正结束

返回：{"eot": bool, "reason": str, "confidence": float}
"""
from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)


# 句末强标点（中文 + 英文）
_SENTENCE_END_PUNCTS = "。？！…!?"

# 句中弱标点（出现后短沉默不切轮次）
_COMMA_PUNCTS = "，,"

# 连接词 / 口头禅尾巴——出现时用户很可能还在想下一句
_CONNECTOR_TAILS = (
    "然后呢", "然后", "就是说", "那个", "这个", "对吧", "是吧", "你知道",
    "所以", "那", "嗯...", "呃", "就是", "对吧？", "是吧？",
)

# 短应答词
_SHORT_ACKS = {"嗯", "嗯嗯", "哦", "噢", "啊", "好", "好吧", "对", "对对", "是", "是的", "行"}


# 阈值
END_PUNCT_SILENCE_MS = 800       # a) 句末标点后需要的沉默
COMMA_LONG_SILENCE_MS = 3000     # d) 逗号后要沉默 3s 才算真结束
SHORT_ACK_BACKCHANNEL_MS = 500   # c) 短应答后 500ms 内算 backchannel
CONNECTOR_EXTEND_MS = 1500       # b) 连接词尾巴至少再等 1.5s


def detect_chinese_eot(
    asr_text: str,
    silence_ms: int,
    last_token_ts: float,
    now: float,
) -> Dict[str, object]:
    """判断当前用户这一轮是否结束。

    参数：
        asr_text       当前已识别的完整文本（不含标点也可以）
        silence_ms     距离上次用户发声的毫秒数
        last_token_ts  最后一个 token 的时间戳（秒，Unix 或单调时钟）
        now            当前时间戳（秒，与 last_token_ts 同坐标系）

    返回 dict：
        eot        True = 该切轮次了；False = 继续等
        reason     人可读的判定理由
        confidence 0.0~1.0，越高越确定
    """
    text = (asr_text or "").strip()
    s = int(silence_ms)

    # 空文本：不可能结束
    if not text:
        return {"eot": False, "reason": "empty_text", "confidence": 0.0}

    last_char = text[-1]

    # ---- c) 短应答词：500ms 内算 backchannel，不切 ----
    if text in _SHORT_ACKS and s <= SHORT_ACK_BACKCHANNEL_MS:
        return {
            "eot": False,
            "reason": "short_ack_backchannel",
            "confidence": 0.9,
        }

    # ---- b) 连接词尾巴：不立刻切，再等等 ----
    if text.endswith(_CONNECTOR_TAILS):
        # 只有沉默足够长才认为真结束；否则继续等
        if s < CONNECTOR_EXTEND_MS:
            return {
                "eot": False,
                "reason": "connector_tail_waiting",
                "confidence": 0.8,
            }
        # 沉默已经够长，算结束
        return {
            "eot": True,
            "reason": "connector_tail_long_silence",
            "confidence": 0.6,
        }

    # ---- a) 句末强标点 + 沉默 ≥800ms → 真结束 ----
    if last_char in _SENTENCE_END_PUNCTS:
        if s >= END_PUNCT_SILENCE_MS:
            return {
                "eot": True,
                "reason": "sentence_end_punctuation",
                "confidence": 0.95,
            }
        # 有句末标点但沉默还不够，继续等
        return {
            "eot": False,
            "reason": "punctuation_but_silence_short",
            "confidence": 0.4,
        }

    # ---- d) 句中逗号：短沉默不算，≥3s 才算 ----
    if last_char in _COMMA_PUNCTS:
        if s >= COMMA_LONG_SILENCE_MS:
            return {
                "eot": True,
                "reason": "comma_long_silence",
                "confidence": 0.7,
            }
        return {
            "eot": False,
            "reason": "comma_mid_sentence",
            "confidence": 0.85,
        }

    # ---- 兜底：没标点，按「沉默够长」保守判定 ----
    # 中文无标点场景下，沉默 >= 1500ms 倾向认为结束
    if s >= 1500:
        return {
            "eot": True,
            "reason": "long_silence_no_punct",
            "confidence": 0.5,
        }
    return {
        "eot": False,
        "reason": "still_listening",
        "confidence": 0.2,
    }
