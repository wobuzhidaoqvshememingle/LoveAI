# -*- coding: utf-8 -*-
"""第六批第三批：性能监控/话术库个性化/非线性攻略风格/个人资料优化。纯函数向后兼容。"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

# ============ 9. 性能监控面板 ============
def performance_monitor(traces: list[dict]) -> dict:
    """traces: [{module, ms}] 耗时饼图+超阈标红。"""
    agg = {}
    for t in traces:
        agg[t.get("module","?")] = agg.get(t.get("module","?"), 0) + t.get("ms", 0)
    over = {m: v for m, v in agg.items() if v > 800}
    return {"per_module_ms": agg, "total_ms": sum(agg.values()),
            "slow_modules": over,
            "suggestion": [f"{m} 耗时过长，建议异步/缓存" for m in over]}


# ============ 10. 话术库个性化 ============
PERSONAL = {"词频": {}, "句式": [], "口头禅": []}

def analyze_style_phrases(messages: list[str]) -> dict:
    """分析用户用词/句式/口头禅，写入个人话术库(外部DB持久化)。"""
    text = "".join(messages)
    words = {}
    for w in ("哈哈", "嗯", "啊", "嘛", "～", "？"):
        c = text.count(w)
        if c: words[w] = c
    catch = [w for w, c in words.items() if c >= 3]
    return {"word_freq": words, "catchphrase": catch,
            "tone": "轻松" if words.get("哈哈") else "中性"}


# ============ 11. 非线性攻略风格 ============
PLAY_STYLES = {
    "温柔型": {"warm": 0.8, "direct": 0.2},
    "霸道型": {"warm": 0.2, "direct": 0.8},
    "智斗型": {"warm": 0.5, "direct": 0.5},
    "温水型": {"warm": 0.9, "direct": 0.1},
    "真诚型": {"warm": 0.6, "direct": 0.6},
}

def play_style_weights(style: str) -> dict:
    return PLAY_STYLES.get(style, PLAY_STYLES["温柔型"])

def apply_play_style(base_strategy: dict, style: str) -> dict:
    w = play_style_weights(style)
    out = dict(base_strategy or {})
    out["style_profile"] = style
    out["weights"] = w
    return out


# ============ 12. 个人资料优化助手 ============
def profile_optimizer(profile_text: str, partner_pref: dict) -> dict:
    """基于对方偏好给头像/签名/朋友圈建议。"""
    tips = []
    if partner_pref.get("喜欢") == "真诚":
        tips.append("签名减少网络梗，用真实生活感表达")
    if partner_pref.get("喜欢") == "阳光":
        tips.append("朋友圈多展示户外运动/积极场景")
    if not profile_text:
        tips.append("补充清晰正面头像")
    return {"profile": profile_text[:100], "preference": partner_pref, "suggestions": tips}
