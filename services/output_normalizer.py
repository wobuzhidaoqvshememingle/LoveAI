# -*- coding: utf-8 -*-
"""LoveAI v2.0 - 模型效果对齐层 + 质量底线校验（R3.3-49 / E22-E23）。

不管哪个模型（L1 小模型 / L2 本地 / L3 云端）输出，都过一遍本层后处理，
让小模型（Qwen-1.5B/0.5B）的输出经过润色后不比大模型差太多。

对外三个入口：
  - normalize_output(raw, *, topic="", min_len=8, max_len=400) -> str
        ① 剥无意义开场白 ② 统一数字/百分比格式 ③ 长度控制 ④ 中文语气统一；
  - quality_check(response, *, topic="") -> tuple[bool, str]
        空/太短/复读/乱码/话题不相关 → (False, 原因)；
  - safe_generate(raw, *, topic="", rule_fallback="") -> str
        normalize + quality_check 一条龙；不通过时回落 rule_fallback。

纯 Python，不依赖 PySide6 / 网络。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional


# --------------------------------------------------------------------------- #
# E22 统一润色
# --------------------------------------------------------------------------- #
# 无意义开场白（小模型爱加的客套/元叙述）
_PREAMBLE_PATTERNS = [
    r"^(好的|好的，?我来|好的，?让我|好的，?那我|好的，?我帮你|好的我帮你|好的那我|"
    r"行|没问题|下面|接下来|作为AI|"
    r"我来分析一下|我来帮你分析一下|那我来帮你(分析|看看)|那我来(分析|看看)|"
    r"我(来)?帮你(分析|看看|分析分析)?|我来(分析|帮你|为你|给你)?|让我(来|为你)?)[,，。：:\s]*",
    r"^(当然(可以|可以啦|没问题)?[，,!！\s]*)",
    r"^(嗯|呃|那个|这个)[，,]\s*",
    # 验收官第五轮补："嗯好的我帮你分析一下：..." 这类"嗯"直接接"好的"无逗号的场景
    r"^(嗯|呃)[，,]?\s*好的[，,]?\s*(我来|我帮你|让我|那我)?\s*(分析|看看|处理|为你)[：:，,。\s]*",
    # 剥残：第一刀留下的「一下这个问题。」「一下。」「一下：」类尾巴
    r"^一下(这个|那个)?(问题|情况|事情)?[。:：,，\s]*",
    # 剥残2：「这个情况：」「这个问题：」单独开头的突兀残尾（只剥带冒号的，句号结尾的保留）
    r"^(这个|那个)(情况|问题|事情|方面)[：:]\s*",
    # 剥残3：「那我来分析分析...」「那我来帮你看看...」第二刀残留
    # 注意：长词在前，短词在后，否则"分析"会先匹配，"分析分析"被截断
    r"^那我来(帮你)?(分析分析|分析|看看)[，,。：:\s]*",
    # 剥残4：只剥"分析一下"/"分析分析"本身，不要把后面的内容也剥了
    r"^分析(一下|分析)[，,。：:\s]*",
]

# 结尾赘语
_TRAILING_PATTERNS = [
    r"\s*希望对你有帮助[。!！~～]*$",
    r"\s*以上是我的(分析|建议|回答)[。!！~～]*$",
    r"\s*(如果(你|您)还有|有什么问题)[，,].{0,20}(告诉我|随时问我)[。!！~～]*$",
]


def _strip_preamble(text: str) -> str:
    """剥掉小模型爱加的「好的我来分析...」开场白。"""
    out = text.strip()
    changed = True
    # 反复剥，直到不再变化（可能叠加多层）
    for _ in range(3):
        if not changed:
            break
        changed = False
        for pat in _PREAMBLE_PATTERNS:
            new = re.sub(pat, "", out).strip()
            if new != out:
                out = new
                changed = True
    for pat in _TRAILING_PATTERNS:
        out = re.sub(pat, "", out).strip()
    return out


def _normalize_numbers(text: str) -> str:
    """统一数字格式：百分比统一成「XX%」，阿拉伯数字与中文数字不混排时保持。"""
    # 1. 把「百分之五十」这类统一成 50%（常见词表）
    cn_num = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    # 「百分之X」→「X%」
    def _pct(m: re.Match) -> str:
        word = m.group(1)
        if word in cn_num:
            return f"{cn_num[word]}0%" if word in ("一", "二", "两") \
                else f"{cn_num[word] * 10}%"
        return m.group(0)
    text = re.sub(r"百分之([零一二两三四五六七八九])", _pct, text)
    # 2. 「50％」全角 → 半角
    text = text.replace("％", "%")
    # 3. 数字与百分号之间不要空格：「50 %」→「50%」
    text = re.sub(r"(\d)\s+%", r"\1%", text)
    return text


def _control_length(text: str, min_len: int, max_len: int) -> str:
    """长度控制：太短补一句衔接，太长在句末截断。"""
    out = text.strip()
    n = len(out)
    if n > max_len:
        # 在 max_len 附近找句末标点截断
        cut = out[:max_len]
        for sep in ("。", "！", "？", ".", "!", "?", "；", ";"):
            idx = cut.rfind(sep)
            if idx > max_len * 0.6:
                cut = cut[:idx + 1]
                break
        out = cut
    elif n and n < min_len:
        # 太短：补一句温和衔接（不改变原意，只让它不突兀）
        out = out.rstrip("。!！?？") + "。"
    return out


def _unify_chinese_tone(text: str) -> str:
    """中文语气统一：去多余感叹号堆叠、半角标点在中文语境下转全角。"""
    # 多个感叹号 → 一个
    text = re.sub(r"！{2,}", "！", text)
    text = re.sub(r"!{2,}", "！", text)
    # 中文句子里的半角逗号/句号 → 全角（粗略：紧邻中文字符时）
    text = re.sub(r"(?<=[\u4e00-\u9fff]),", "，", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\.(?=[\u4e00-\u9fff])", "。", text)
    # 去掉首尾多余空白
    return text.strip()


def normalize_output(raw: Optional[str], topic: str = "",
                     min_len: int = 8, max_len: int = 400) -> str:
    """统一润色层入口。任何模型输出都过一遍。"""
    if not raw:
        return ""
    out = str(raw)
    out = _strip_preamble(out)
    out = _normalize_numbers(out)
    out = _unify_chinese_tone(out)
    out = _control_length(out, min_len, max_len)
    return out.strip()


# --------------------------------------------------------------------------- #
# E23 质量底线校验
# --------------------------------------------------------------------------- #
# 乱码检测：连续非常用字符/替换符
_REPLACEMENT = "\ufffd"


def _is_garbled(text: str) -> bool:
    """简单乱码检测：替换符过多，或连续 4 个以上非常见 Unicode。"""
    if not text:
        return False
    if _REPLACEMENT in text:
        return True
    bad = 0
    for ch in text:
        cat = unicodedata.category(ch)
        # Cc/Cs/Co/Cn 控制符/私用区视为异常
        if cat in ("Cc", "Cs", "Co", "Cn"):
            bad += 1
            if bad >= 3:
                return True
    return False


def _is_repetition(text: str) -> bool:
    """复读机检测：连续重复同一句子/短语 ≥3 次。"""
    if len(text) < 6:
        return False
    # 取最短重复单元（2-8字），看是否连续重复≥3次
    for L in range(2, 9):
        unit = text[:L]
        if len(text) >= L * 3 and text[:L * 3] == unit * 3:
            return True
    # 句子级：把文本按标点切，看有没有连续3句完全相同
    sents = [s for s in re.split(r"[。！？!?\n]", text) if s.strip()]
    for i in range(len(sents) - 2):
        if sents[i] == sents[i + 1] == sents[i + 2] and len(sents[i].strip()) >= 2:
            return True
    return False


def _topic_relevant(response: str, topic: str) -> bool:
    """简单关键词重叠：response 与 topic 的字符 2-gram 有交集即视为相关。

    topic 为空时不做相关性硬卡（避免误伤无主题闲聊）。
    """
    if not topic:
        return True
    def _grams(s: str) -> set:
        s = re.sub(r"[\s，。！？、,.!?~…—\-「」『』《》()（）]", "", s)
        return {s[i:i + 2] for i in range(len(s) - 1)}
    rg = _grams(response)
    tg = _grams(topic)
    if not rg or not tg:
        return True  # 任一方太短，不硬判
    return len(rg & tg) >= 1


def quality_check(response: Optional[str], topic: str = "") -> tuple[bool, str]:
    """质量底线校验。返回 (是否通过, 原因)。

    不通过的原因：
      empty          空
      too_short      <5 字
      garbled        乱码
      repetition     复读机
      off_topic      与输入话题不相关
    """
    if response is None:
        return False, "empty"
    text = str(response).strip()
    if not text:
        return False, "empty"
    if len(text) < 5:
        return False, "too_short"
    if _is_garbled(text):
        return False, "garbled"
    if _is_repetition(text):
        return False, "repetition"
    if not _topic_relevant(text, topic):
        return False, "off_topic"
    return True, "ok"


# --------------------------------------------------------------------------- #
# 一条龙：normalize + quality_check + rule_fallback
# --------------------------------------------------------------------------- #
_DEFAULT_RULE = "我在。你想聊什么，慢慢说，我听着。"


def safe_generate(raw: Optional[str], topic: str = "",
                  rule_fallback: str = "",
                  min_len: int = 8, max_len: int = 400) -> str:
    """润色 + 质量校验；不通过时回落 rule_fallback（默认一句安全回应）。

    小模型输出经本函数后：开场白被剥、数字格式统一、长度受控、
    若仍不达标则用规则兜底，绝不把乱码/复读直接抛给用户。
    """
    normalized = normalize_output(raw, topic=topic, min_len=min_len, max_len=max_len)
    ok, _reason = quality_check(normalized, topic=topic)
    if ok:
        return normalized
    fb = (rule_fallback or _DEFAULT_RULE).strip()
    # 兜底本身再过一遍质检，避免兜底也踩雷
    ok2, _ = quality_check(fb, topic=topic)
    return fb if ok2 else _DEFAULT_RULE


__all__ = [
    "normalize_output", "quality_check", "safe_generate",
]
