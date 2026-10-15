# -*- coding: utf-8 -*-
"""LoveAI v2.0 - 迷信借力打力模块（R3.3-46）。

原则：
  - 不否定对方迷信（不说"那是假的"），顺势接住，把话题引向关系/行动；
  - 对方说"我们星座很配"，不拆台，而是问"那你觉得接下来该怎么让它更稳"；
  - 与 strategy_advisor 对接：识别到迷信内容时，优先用 playalong 话术。

支持类型：
  bazi         八字/命理/合婚/五行
  zodiac       西洋星座（白羊~双鱼、上升/月亮星座）
  mbti         MBTI 十六型
  tarot        塔罗/抽牌
  fengshui     风水/桃花位/摆件
  zodiac_cn    生肖/本命年/犯太岁
  mercury      水逆/水星逆行
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# 检测类型 → belief_bridge.json 的 systems 键映射
_TYPE_TO_JSON_SYSTEM: dict[str, str] = {
    "zodiac": "xingzuo",
    "mercury": "xingzuo",
    "mbti": "mbti",
    "bazi": "bazi",
    "tarot": "tarot",
    "fengshui": "fengshui",
    "zodiac_cn": "shuxiang",
}


# ------------------------------------------------------------------ #
# 识别词表
# ------------------------------------------------------------------ #
_ZODIAC_SIGNS = [
    "白羊", "金牛", "双子", "巨蟹", "狮子", "处女",
    "天秤", "天蝎", "射手", "摩羯", "水瓶", "双鱼",
]
_MBTI_TYPES = [
    "INTJ", "INTP", "ENTJ", "ENTP", "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ", "ISTP", "ISFP", "ESTP", "ESFP",
]
_ZODIAC_CN = [
    "属鼠", "属牛", "属虎", "属兔", "属龙", "属蛇",
    "属马", "属羊", "属猴", "属鸡", "属狗", "属猪",
]

# 类型 → 关键词列表（任一命中即识别）
_DETECT_RULES: dict[str, list[str]] = {
    "bazi": ["八字", "命理", "合婚", "生辰八字", "五行缺", "五行喜", "缺金", "缺木",
             "缺水", "缺火", "缺土", "大运", "流年"],
    "zodiac": (["星座", "上升星座", "月亮星座", "太阳星座", "星盘", "星座运势",
                "星座配对", "星座很配", "星座不合"]
               + _ZODIAC_SIGNS),
    "mbti": ["MBTI", "mbti", "十六型", "十六型人格"] + _MBTI_TYPES,
    "tarot": ["塔罗", "牌阵", "抽牌", "大阿卡纳", "小阿卡纳", "韦特塔罗", "塔罗牌"],
    "fengshui": ["风水", "财位", "桃花位", "桃花煞", "摆件", "貔貅", "鱼缸",
                 "文昌塔", "葫芦挂", "门神"],
    "zodiac_cn": (["生肖", "本命年", "犯太岁", "太岁", "相冲", "相害", "六合",
                   "三合"] + _ZODIAC_CN),
    "mercury": ["水逆", "水星逆行", "金星逆行", "逆行"],
}


# ------------------------------------------------------------------ #
# 接住话术模板（每类 3 条）
# ------------------------------------------------------------------ #
_PLAYALONG_TEMPLATES: dict[str, list[str]] = {
    "bazi": [
        "八字合不合其实是个引子，关键是你们俩遇到事的时候肯不肯一起扛。你觉得我们接下来在哪件事上最该一起对一下？",
        "五行这东西我不跟你抬杠，但命理再好，日子也是过出来的。你觉得我们接下来做什么会更顺？",
        "合婚这事先放一边，我更想知道你信这个是因为家里催，还是你自己也在意？咱们顺着你的想法聊。",
    ],
    "zodiac": [
        "难怪跟你聊天这么舒服，那你觉得我们接下来该怎么让这个配对更稳？",
        "星座这东西我不否认，那你信这个的话，你觉得我们俩最该避的坑是什么？",
        "既然你说我们星座配，那正好——你最近有没有想一起做但一直没做的事？趁这个「配对期」把它安排了？",
    ],
    "mbti": [
        "INFJ 确实想得深，难怪你之前说那句话我琢磨了半天。你要不要说说你当时到底在想什么？",
        "MBTI 我觉得是个聊自己的好入口。那按你这个类型，你最烦对方做什么？我记下来。",
        "既然你是这个型，那我们相处的时候，你希望我是直接一点还是委婉一点？你说，我照做。",
    ],
    "tarot": [
        "塔罗抽出来的结果我不跟你争，但牌面说到底是提醒。那你觉得这张牌在提醒我们俩什么？",
        "你抽到什么牌我先不问，我更想听你看完牌之后心里第一冒出来的那个人是谁、那件事是什么。",
        "牌可以抽，但日子还是要过。你要不要把牌上说的那句「建议」翻译成我们下周具体能做的一件事？",
    ],
    "fengshui": [
        "风水这东西信则有，那你觉得我们俩之间那个「桃花位」现在卡在哪？是距离还是聊天节奏？",
        "摆件我不反对，但关系的风水其实就是——谁主动、谁回应。你觉得我们现在这俩位置顺吗？",
        "既然你讲风水，那正好：你最近有没有觉得家里/工位哪个地方让你心情不好？咱们先把那个调了再说。",
    ],
    "zodiac_cn": [
        "生肖合不合我不抬杠，那按你这个属相，你觉得跟我在一起你最需要的是空间还是确定感？",
        "本命年犯太岁这说法我听过，那你今年有没有特别想完成的一件事？我陪你把它做了，比化太岁管用。",
        "属相相冲不相冲，最后还是看人。你觉得我们俩这半年，是越处越顺还是越处越累？按真实感觉说。",
    ],
    "mercury": [
        "水逆背锅这事我懂，那你觉得最近这次「逆行」里，我们俩哪句话其实是被冤枉的？",
        "水逆期别做重大决定，那正好——这周我们只聊轻松的，不吵架不摊牌。你觉得行不？",
        "逆行期沟通容易出岔子，那我们定个规矩：任何一句话，拿不准就发两遍确认，行不行？",
    ],
}


# ------------------------------------------------------------------ #
# belief_bridge.json 可选加载（存在则合并进接住话术，不存在用内置默认）
# ------------------------------------------------------------------ #
_BB_CACHE: Optional[dict[str, Any]] = None
_BB_TRIED: bool = False


def _load_belief_bridge() -> Optional[dict[str, Any]]:
    """惰性加载 knowledge/belief_bridge.json。加载失败/不存在返回 None。

    内置 _PLAYALONG_TEMPLATES 始终兜底，JSON 只是「加料」。
    """
    global _BB_CACHE, _BB_TRIED
    if _BB_TRIED:
        return _BB_CACHE
    _BB_TRIED = True
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        # services/ -> project_root/ -> knowledge/belief_bridge.json
        path = os.path.join(os.path.dirname(here), "knowledge", "belief_bridge.json")
        if not os.path.exists(path):
            logger.info("belief_bridge.json 不存在(%s)，使用内置默认话术", path)
            return None
        with open(path, "r", encoding="utf-8") as f:
            _BB_CACHE = json.load(f)
        logger.info("belief_bridge.json 加载成功，systems=%s",
                    list((_BB_CACHE.get("systems") or {}).keys()))
    except Exception as e:  # noqa: BLE001
        logger.warning("belief_bridge.json 加载失败，用内置默认: %s", e)
        _BB_CACHE = None
    return _BB_CACHE


def _belief_bridge_candidates(stype: str, user_intent: str = "") -> list[str]:
    """从 belief_bridge.json 取 opener+bridge 作为附加接住话术。"""
    bb = _load_belief_bridge()
    if not bb:
        return []
    system_key = _TYPE_TO_JSON_SYSTEM.get(stype, "general")
    sys = (bb.get("systems") or {}).get(system_key) or {}
    out: list[str] = []
    # opener：先顺势接住对方的迷信框架
    for o in (sys.get("opener") or [])[:2]:
        out.append(str(o))
    # bridge：把话头桥到关系/具体行动
    for b in (sys.get("bridge") or [])[:2]:
        out.append(str(b))
    # 若用户有明确意图，挑一条匹配的 advice_template 翻译进去
    if user_intent:
        for at in (sys.get("advice_templates") or []):
            advice = at.get("advice", "")
            if any(k in advice for k in (user_intent, "主动", "约", "边界")):
                # 取翻译后的那条（键名形如 as_xingzuo）
                translated = next((v for kk, v in at.items()
                                    if kk.startswith("as_")), "")
                if translated:
                    out.append(str(translated))
                break
    return out


# ------------------------------------------------------------------ #
# 识别
# ------------------------------------------------------------------ #
def detect_superstition(text: str) -> Optional[dict[str, str]]:
    """识别对方提到的迷信内容。

    Returns:
        {"type": "...", "quote": "命中原文片段"}；不含迷信返回 None。
    """
    if not text:
        return None
    t = str(text)
    for stype, keywords in _DETECT_RULES.items():
        for kw in keywords:
            idx = t.find(kw)
            if idx >= 0:
                # 截取关键词前后 12 字作为 quote
                start = max(0, idx - 8)
                end = min(len(t), idx + len(kw) + 12)
                return {"type": stype, "quote": t[start:end].strip()}
    return None


# ------------------------------------------------------------------ #
# 生成接住话术
# ------------------------------------------------------------------ #
def generate_playalong_response(detected: dict[str, str],
                                user_intent: str = "") -> list[str]:
    """根据识别结果返回 3 条候选话术。

    Args:
        detected: detect_superstition 的返回值。
        user_intent: 用户当前意图（"升温"/"吵架修复"/"约见面"等，可选）。
                     目前不强行改写模板，只做占位扩展；保留参数便于后续增强。
    """
    if not detected:
        return []
    stype = detected.get("type", "")
    templates = list(_PLAYALONG_TEMPLATES.get(stype, []))
    # belief_bridge.json 加料：opener + bridge（存在时合并）
    try:
        templates.extend(_belief_bridge_candidates(stype, user_intent=user_intent))
    except Exception as e:  # noqa: BLE001
        logger.debug("belief_bridge 合并失败（忽略）: %s", e)
    # 如果用户有明确意图，追加一条把意图显式带出来的话术
    if user_intent:
        templates.append(
            f"你刚才说的这事我接住了。那回到我们俩——你现在是不是想{user_intent}？"
            f"咱直接聊这个，比绕一圈快。"
        )
    return templates


# ------------------------------------------------------------------ #
# 给 strategy_advisor 用的便捷入口
# ------------------------------------------------------------------ #
def maybe_playalong(text: str, user_intent: str = "") -> Optional[dict[str, Any]]:
    """一站式：检测 + 生成话术。未命中返回 None。"""
    det = detect_superstition(text)
    if not det:
        return None
    return {
        "detected": det,
        "candidates": generate_playalong_response(det, user_intent=user_intent),
    }


__all__ = [
    "detect_superstition", "generate_playalong_response", "maybe_playalong",
]
