# -*- coding: utf-8 -*-
"""R3.3-06 PsyDI 渐进式人格探测（live 实现，替代已移除的死服务 psydi_probe.py）。

核心理念（对标 PsyDI / arXiv:2408.03337）：不甩问卷，在真实聊天中**渐进**补全人格画像。
与已删除的 `backups/dead_services/psydi_probe.py` 骨架相比，本 live 版补齐四块：
  ① 探测主题分阶（低敏日常偏好 → 中敏关系模式 → 高敏依恋/黑暗），不到熟不问扎心题；
  ② 每轮聊天后从**对方文本 + 已算出的心理学证据**提取信号（不重复造关键词轮子）；
  ③ 渐进累积：进度/已探明维度/置信度随轮次增长，写进 profile.psychology.progressive_probe；
  ④ 探测结果参与「鉴别对话引导」= 输出 next_probe 供回复建议消费（接到 persona 读边）。

纯本地、确定性、无 LLM 依赖、fail-closed（解析失败一律 hold，不宁可信其有）。
输入证据复用 persona_analyzer 已算出的 psychology / attachment 子账本，不另起炉灶。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# 分阶维度注册表：每个维度 = 敏感度层级 + 诊断权重 + 自然话术 + 行为观察点
#   tier 1 低敏（日常偏好破冰） / tier 2 中敏（关系模式） / tier 3 高敏（依恋·黑暗）
#   未到上层置信度门槛前，不探下层高敏维度 → 渐进，不一上来问扎心题。
# ------------------------------------------------------------------ #
DIMENSION_REGISTRY: dict[str, dict] = {
    "values_money": {
        "tier": 1, "label": "价值观·金钱与付出观", "weight": 0.78,
        "question": "约会开销你一般怎么看？AA、轮流、还是该某一方多承担？",
        "observation": "观察：是否把花钱=爱、是否要求单方面付出、谈钱时大方还是算计。",
    },
    "values_commitment": {
        "tier": 1, "label": "价值观·承诺与婚姻观", "weight": 0.85,
        "question": "你理想中，两个人从在一起到结婚大概是什么节奏？怎么看「只恋爱不结婚」？",
        "observation": "观察：谈未来/名分/见家长时是积极、回避还是画饼不落地。",
    },
    "boundary_control": {
        "tier": 2, "label": "边界·控制与侵入倾向", "weight": 0.80,
        "question": "你会希望对方跟你报备行踪、手机互相看吗？为什么？",
        "observation": "观察：是否要求随时报备、查岗、限制社交；把「控制」包装成「在乎」。",
    },
    "cognitive_bias": {
        "tier": 2, "label": "认知偏差·非黑即白/读心/灾难化", "weight": 0.70,
        "question": "上次我们有点不愉快，你当时心里第一反应是什么？",
        "observation": "观察：是否常说「每次都/从来/肯定会/你就是想…」这类绝对化、读心、灾难化措辞。",
    },
    "attachment_anxiety": {
        "tier": 3, "label": "依恋·焦虑/被抛弃恐惧", "weight": 0.95,
        "question": "如果我半天没回消息，你一般会怎么想、会怎么做？",
        "observation": "观察：是否频繁追问/反复确认/怕被抛弃；我回慢时焦虑抗议还是淡定。",
    },
    "attachment_avoidance": {
        "tier": 3, "label": "依恋·回避倾向", "weight": 0.95,
        "question": "你觉得两个人在一起需要各自保留多少私人空间？忙的时候会希望先别被打扰吗？",
        "observation": "观察：吵架后愿沟通修复还是沉默/消失；聊亲密感是否主动后撤。",
    },
    "dark_narcissism": {
        "tier": 3, "label": "黑暗三联征·自恋/特权感", "weight": 0.90,
        "question": "你觉得一段关系里哪些事是男生/女生「本来就该」主动做的？",
        "observation": "观察：是否把自己放优先、要求对方迁就却不 reciprocate；贬低/轻视他人倾向。",
    },
    "dark_manipulation": {
        "tier": 3, "label": "黑暗三联征·操纵/煤气灯", "weight": 0.92,
        "question": "你和朋友意见不一样时，通常怎么让对方听你的？",
        "observation": "观察：是否爱用「你要是不…我就…」、否定对方记忆、让对方自我怀疑。",
    },
}

# 单条信号带来的置信度增量；置信度上限 1.0。
_BUMP = 0.15
# 某维度达到该置信度视为「已探明」，计入 probed 并解锁下一阶。
_COVERED = 0.60
# 所有可探维度信息增益都低于该值 → 画像基本完整，停止追问。
_DONE_GAIN = 0.15

# 把 persona_analyzer 已算出的心理学/依恋子账本 → 本模块维度（复用，不重造关键词表）。
# key=本模块维度, value=(来源dict路径, 命中即 bump 的判据)。
_EVIDENCE_MAP = {
    "dark_narcissism": ("dark_triad_tendencies", "自恋"),
    "dark_manipulation": ("dark_triad_tendencies", "操纵"),
    "cognitive_bias": ("cbt_distortions", None),       # 有任一认知扭曲键即 bump
    "boundary_control": ("defensive_communication", None),
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _extract_signals(partner_text: str, psychology: dict, attachment: dict) -> dict[str, float]:
    """从「对方文本 + 已算心理学证据」提取各维度的本轮信号强度（0~1）。

    复用 persona_analyzer 已产出的 psychology/attachment 账本（dark_triad_tendencies /
    cbt_distortions / defensive_communication / 依恋），不重复造关键词轮子；
    values_* 无现成账本，仅做极轻量原文探针（最小关键词，fail-closed）。
    返回 {维度: 本轮信号强度(0 表示本轮无信号)}。
    """
    sig: dict[str, float] = {k: 0.0 for k in DIMENSION_REGISTRY}
    psych = psychology if isinstance(psychology, dict) else {}
    att = attachment if isinstance(attachment, dict) else {}
    text = partner_text or ""

    # 1) 复用已算心理学证据账本
    for dim, (src_key, probe_val) in _EVIDENCE_MAP.items():
        src = psych.get(src_key)
        if not isinstance(src, dict) or not src:
            continue
        if probe_val is None:
            # 任一非空键命中即 bump（强度随命中键数略增，封顶）
            sig[dim] = _clamp01(0.25 + 0.1 * max(0, len([v for v in src.values() if v])))
        elif probe_val in src and src.get(probe_val):
            sig[dim] = 0.45

    # 2) 复用依恋账本（attachment 已由 _analyze_attachment 产出）
    att_anx = att.get("anxiety") or att.get("anxious")
    att_avo = att.get("avoidance") or att.get("avoidant")
    if isinstance(att_anx, (int, float)) and att_anx > 0.3:
        sig["attachment_anxiety"] = _clamp01(0.3 + 0.3 * att_anx)
    if isinstance(att_avo, (int, float)) and att_avo > 0.3:
        sig["attachment_avoidance"] = _clamp01(0.3 + 0.3 * att_avo)

    # 3) values_* 极轻量原文探针（最小关键词，命中弱信号，不做强判断）
    if any(w in text for w in ("AA", "aa", "轮流请", "各付各", "他该", "男生该", "花钱")):
        sig["values_money"] = 0.3
    if any(w in text for w in ("结婚", "见家长", "名分", "一辈子", "谈多久")):
        sig["values_commitment"] = 0.3
    return sig


def update(state: Optional[dict], partner_text: str, psychology: dict,
           attachment: dict, turn: int) -> dict:
    """渐进累积：把本轮信号折叠进探测状态，返回新状态（纯函数，无副作用）。

    Args:
        state: psychology.progressive_probe 既有状态（首轮可为 None/{}）。
        partner_text: 本轮对方(received)文本。
        psychology: persona_analyzer 本轮算出的 psychology dict。
        attachment: 本轮算出的 attachment dict。
        turn: 第几轮探测调用（轮次计数）。

    Returns:
        新状态 dict：{turns, current_tier, dimensions{dim:{confidence,signals,last_turn}},
                     probed[], next_probe{...}}
    """
    st: dict = dict(state) if isinstance(state, dict) else {}
    dims = {k: dict(v) for k, v in st.get("dimensions", {}).items()
            if isinstance(v, dict)}
    for k in DIMENSION_REGISTRY:
        dims.setdefault(k, {"confidence": 0.0, "signals": 0, "last_turn": 0})

    sig = _extract_signals(partner_text, psychology, attachment)
    for dim, strength in sig.items():
        if strength > 0:
            d = dims[dim]
            d["confidence"] = _clamp01(d["confidence"] + _BUMP * (0.5 + strength))
            d["signals"] = int(d.get("signals", 0)) + 1
            d["last_turn"] = turn

    # 已探明集合：置信度 >= _COVERED
    probed = sorted([k for k, v in dims.items() if v["confidence"] >= _COVERED])

    # 当前可达阶 = 最高一个「下层已基本探明」的阶
    current_tier = _highest_eligible_tier(dims)

    new_state = {
        "turns": int(st.get("turns", 0)) + 1,
        "current_tier": current_tier,
        "dimensions": dims,
        "probed": probed,
    }
    new_state["next_probe"] = _next_probe(new_state)
    return new_state


def _highest_eligible_tier(dims: dict) -> int:
    """当前可达阶：tier1 永远可达；tier2 需 tier1 两维都探明；tier3 需 tier2 两维都探明。"""
    tier_of = {k: v["tier"] for k, v in DIMENSION_REGISTRY.items()}
    def tier_done(t: int) -> bool:
        members = [k for k, tt in tier_of.items() if tt == t]
        return all(dims[k]["confidence"] >= _COVERED for k in members)
    if tier_done(1) and tier_done(2):
        return 3
    if tier_done(1):
        return 2
    return 1


def _eligible_dims(dims: dict) -> list[str]:
    """按渐进规则，当前允许探的维度（不越级探高敏）。"""
    cur = _highest_eligible_tier(dims)
    tier_of = {k: v["tier"] for k, v in DIMENSION_REGISTRY.items()}
    return [k for k, t in tier_of.items() if t <= cur]


def _info_gain(key: str, dims: dict) -> float:
    conf = dims[key]["confidence"]
    return round(DIMENSION_REGISTRY[key]["weight"] * (1.0 - conf), 4)


def _next_probe(state: dict) -> dict:
    """下一步探测建议：在当前可达维度里选信息增益最大者；全部探明则 done。"""
    dims = state["dimensions"]
    eligible = _eligible_dims(dims)
    scores = sorted(((k, _info_gain(k, dims)) for k in eligible),
                    key=lambda kv: kv[1], reverse=True)
    top_key, top_score = scores[0]
    top_conf = dims[top_key]["confidence"]
    spec = DIMENSION_REGISTRY[top_key]
    done = top_score < _DONE_GAIN

    candidates = [
        {"target_dimension": k, "label": DIMENSION_REGISTRY[k]["label"],
         "info_gain": s, "question": DIMENSION_REGISTRY[k]["question"]}
        for k, s in scores[1:3]
    ]
    if done:
        return {
            "target_dimension": None, "label": "画像已较完整",
            "question": "不再追加探测问题，转入长期行为验证与一致性观察，避免过度追问引发防备。",
            "observation": "观察新行为是否与既有画像一致；矛盾出现时再局部重探。",
            "info_gain": top_score, "known_confidence": top_conf,
            "why": "当前阶所有可达维度均已较充分，继续提问的边际信息增益已很低。",
            "done": True, "candidates": candidates,
        }
    return {
        "target_dimension": top_key, "label": spec["label"],
        "question": spec["question"], "observation": spec["observation"],
        "info_gain": top_score, "known_confidence": top_conf,
        "current_tier": state.get("current_tier", 1),
        "why": (f"「{spec['label']}」当前置信度仅 {top_conf:.2f}、诊断权重 "
                f"{spec['weight']}，是当前渐进阶段信息增益最大的缺口"),
        "done": False, "candidates": candidates,
    }


def next_probe(profile_or_state: Optional[dict]) -> dict:
    """对外读边：从 profile.psychology.progressive_probe 读出下一步探测引导。

    输入可为完整 profile（自动取 psychology.progressive_probe）或直接 state。
    无任何状态时给出初始建议（从低敏 tier1 开始）。
    """
    if not isinstance(profile_or_state, dict):
        profile_or_state = {}
    # 兼容：传进来的可能是完整 profile
    if "dimensions" not in profile_or_state and "psychology" in profile_or_state:
        psych = profile_or_state.get("psychology") or {}
        profile_or_state = psych.get("progressive_probe") or {}
    state = profile_or_state if isinstance(profile_or_state, dict) else {}
    if not state.get("dimensions"):
        return _next_probe({"dimensions": {k: {"confidence": 0.0} for k in DIMENSION_REGISTRY},
                           "current_tier": 1})
    return _next_probe(state)


if __name__ == "__main__":  # pragma: no cover
    import json
    demo = next_probe({})
    print(json.dumps(demo, ensure_ascii=False, indent=2))
