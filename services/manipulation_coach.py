# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 操控识别后的反制 + 引导变好教练（ManipulationCoach）

设计原则（手段中立）：
  - 手段本身中立；系统不替用户做道德裁决，所有路线以条件-效果-代价呈现，选择权在用户。
  - 对识别到的操控模式，如实呈现：是什么 → 可反制的非对抗步骤 → 每步的预期效果与代价；
  - 同时给出"引导对方变好"的路线（对方有改变意愿时），预期效果与所需时间；
  - 判断权在用户：继续、反制、引导、还是离开，由用户自选，系统提供完整信息与执行支持。

输入：persona（含 dark_triad_tendencies / defensive_communication）、近期财务/辱骂信号。
输出：manipulation_patterns / counter_strategies / coaching / neutral_note。
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 操控模式识别（基于画像信号，即时，不依赖3月基线）
# --------------------------------------------------------------------------- #
def _detect_patterns(persona: dict, finance_risk_flags: list) -> list[dict]:
    psych = (persona or {}).get("psychology") or {}
    dark = psych.get("dark_triad_tendencies") or {}
    defensive = psych.get("defensive_communication") or {}

    patterns: list[dict] = []
    if "煤气灯贬低" in defensive or "贬低感受" in defensive:
        patterns.append({
            "key": "gaslighting",
            "name": "煤气灯式贬低",
            "evidence": "对方反复把问题归因到你'不够好/性格有问题/你太敏感'，让你自我怀疑",
        })
    if dark.get("操纵"):
        patterns.append({
            "key": "money_control",
            "name": "金钱/条件式控制",
            "evidence": "以'不满意就分手/证明实力/报销'等条件驱动你单向投入",
        })
    if dark.get("辱骂贬低"):
        patterns.append({
            "key": "verbal_degradation",
            "name": "辱骂/贬低人格",
            "evidence": "使用'废物/死人/狗都不如'等贬损性语言",
        })
    if finance_risk_flags:
        patterns.append({
            "key": "one_directional_spend",
            "name": "单向投入累积",
            "evidence": "财务信号显示你承担的转账/礼物显著多于对方",
        })
    return patterns


# --------------------------------------------------------------------------- #
# 反制策略（非对抗，打破操控循环）
# --------------------------------------------------------------------------- #
def _counter_strategies(patterns: list[dict]) -> list[dict]:
    """渐进式、条件式反制——不突然断供，而是改变供给规则。

    核心逻辑：突然 100→0 会让纯索取者立刻跑掉、失去引导窗口；
    改为渐进减量 + 条件交换 + 波动式满足，用对方的操控手法反制对方，
    同时把"对方是否开始投入"作为是否有引导价值的筛选信号。
    """
    steps: list[dict] = []

    pkeys = {p["key"] for p in patterns}
    if "gaslighting" in pkeys:
        steps.append({
            "principle": "现实检验锚定（煤气灯反制）",
            "how": "用客观记录（聊天截图/时间线）温和地锚定事实，不争辩对错："
                   "'我翻了记录，那天说的是X，我们对一下'；不被'你记错了/你想多了'带偏。",
            "effect": "你不再自我怀疑，操控失去抓手。",
            "likely_reaction": "对方继续否认/转移话题，模式会更明显。",
            "if_runs_or_escalates": "若对方因你对记录而暴怒/倒打一耙，说明煤气灯已固化，引导空间小。",
        })

    # —— 渐进式反制7步（金钱控制/单向投入场景）—— #
    steps.append({
        "principle": "1. 渐进式减量（不突然断供）",
        "how": "不要从 100% 立刻降到 0%，而是 100→70→50→30 阶梯减量，"
               "每减一档后观察对方反应再决定下一档。",
        "effect": "给关系留缓冲，避免对方一眼看穿被'断供'而立即跑掉。",
        "likely_reaction": "减量后就冷淡/威胁分手→纯索取；能接受甚至主动付出→有引导空间。",
        "if_runs_or_escalates": "若减到某一档对方直接跑掉，说明关系本质是纯索取；跑掉是筛选结果，不是失败。",
    })
    steps.append({
        "principle": "2. 条件交换替代无条件给予",
        "how": "不是不给，而是'给可以，但你也要做点什么'：对方要红包→"
               "'可以啊，那你这周主动约我一次见面'；要礼物→'好，那你帮我个小忙先'。",
        "effect": "用互惠+登门槛，把'纯索取'改成'小投入交换'，让对方开始付出。",
        "likely_reaction": "愿意配合做小投入→可继续引导；拒绝交换、只要不给就不高兴→索取本质。",
        "if_runs_or_escalates": "若对方拒绝任何对等交换，说明只想要单向供给。",
    })
    steps.append({
        "principle": "3. 可得性波动（间歇性强化反用）",
        "how": "不每次都满足、也不每次都拒绝，而是'有时满足、有时延迟、有时拒绝'，"
               "让对方摸不准规律——用对方的间歇性强化手法反制对方。",
        "effect": "不确定性会增加对方投入来'争取'满足，反而提升其投入意愿。",
        "likely_reaction": "对方会更主动/更上心→这是被强化的投入；对方直接消失→本就只图确定索取。",
        "if_runs_or_escalates": "波动期跑掉的，是无法承受'不被稳稳满足'的纯索取者。",
    })
    steps.append({
        "principle": "4. 延迟满足测试",
        "how": "对方提要求时不立刻拒绝，而是'过两天''等我忙完这阵''下次发工资'。",
        "effect": "观察对方是理解等待，还是立刻翻脸/威胁/冷暴力；延迟本身降低即时满足预期。",
        "likely_reaction": "能体谅等待→有感情基础；一提要求不能立刻满足就炸→情绪操控。",
        "if_runs_or_escalates": "延迟即翻脸，说明对方要的是'即时兑现'不是关系。",
    })
    steps.append({
        "principle": "5. 小额测试 + 梯度承诺",
        "how": "先满足一个小要求，看是否感恩、是否得寸进尺；小满足后立刻要更大的→登门槛被滥用；"
               "有正向反馈→再逐步引导双向投入。",
        "effect": "用最小成本测出对方是'得寸进尺'还是'懂得回馈'。",
        "likely_reaction": "小满足后立刻索要更大→触发登门槛滥用警告；有感谢/回馈→有引导价值。",
        "if_runs_or_escalates": "小满足即被当理所当然并加码，是典型操控路径。",
    })
    steps.append({
        "principle": "6. 引导对方投入（反制的核心目标）",
        "how": "终极目标不是'不给钱'，而是'让对方也开始投入'：用富兰克林效应请对方帮小忙"
               "（'我下周搬家你能搭把手吗'），对方会合理化'我帮他=我在乎他'；"
               "用承诺一致性先让对方做小承诺（'明天叫我起床'）→兑现→再大一点。",
        "effect": "一旦对方在时间/情感/行动上开始投入，沉没成本使其不愿轻易跑掉，关系才可能转向健康。",
        "likely_reaction": "愿意投入→关系有正向重塑空间；始终只出嘴不出力→投入完全单向。",
        "if_runs_or_escalates": "对方只接受不付出，说明其沉没成本为零、随时可撤。",
    })
    steps.append({
        "principle": "7. 如实呈现跑掉风险（筛选而非失败）",
        "how": "在渐进减量/波动过程中，对方可能在某个点就跑了——这是可预见结果，不是操作失误。",
        "effect": "你提前知道并接受这个概率，不会因对方突然离开而自我怀疑或急着加码挽回。",
        "likely_reaction": "因'你不再立刻满足'就跑→证明关系本质是纯索取；不跑且开始投入→才是有引导价值的关系。",
        "if_runs_or_escalates": "跑掉=筛选完成；留下并投入=进入引导路线。两条路都有价值，都不是失败。",
    })

    steps.append({
        "principle": "'我信息'设边界（非指责）",
        "how": "用'我感到/我需要'代替'你总是/你又'：'我被说废物时很难受，我需要被尊重'；"
               "只陈述你的感受与底线，不要求对方立刻改。",
        "effect": "降低对方防御，让边界可被接收。",
        "likely_reaction": "初期可能说你'矫情'；坚持几次才有效。",
        "if_runs_or_escalates": "边界被反复无视，说明对方无意尊重你的底线。",
    })
    if "verbal_degradation" in {p["key"] for p in patterns}:
        steps.append({
            "principle": "对辱骂的平稳止损（非报复）",
            "how": "被辱骂时不回骂、不自我归罪，平静暂停：'你这么说我无法继续聊，我先冷静'；"
                   "事后再谈是否可接受。",
            "effect": "切断'辱骂-你自我怀疑'的强化回路。",
            "likely_reaction": "对方可能变本加厉测试底线；你需要一致重复这个动作。",
            "if_runs_or_escalates": "辱骂在你设边界后仍不收敛，是关系红线。",
        })
    return steps


# --------------------------------------------------------------------------- #
# 引导变好（对方有改变意愿时）
# --------------------------------------------------------------------------- #
def _coaching(patterns: list[dict]) -> list[dict]:
    return [
        {
            "track": "正强化健康互动",
            "how": "当对方出现一次尊重你、主动投入、不再贬低时，明确给予积极反馈："
                   "'你刚才那样说我，我感觉很好'——正强化（而非等他做到完美）。",
            "expected": "健康行为被重复；南风替代北风，关系温度回升。",
            "time": "通常需要数周到数月的一致反馈，才看得出是否真有改变意愿。",
        },
        {
            "track": "情感投标双向化",
            "how": "留意对方是否回应你的连接信号（Gottman 投标）：你分享小事时，"
                   "他是认真追问还是敷衍；双向投标才是健康关系的地基。",
            "expected": "若对方愿意转向你，关系可修复；若长期单向，说明改变意愿不足。",
            "time": "2-4 周的日常观察即可有初步判断。",
        },
        {
            "track": "可达成小目标",
            "how": "不要一次性要求对方'彻底改变'；先定一个小而具体的目标"
                   "（如'一周不使用贬损词'），做到就正向强化。",
            "expected": "小成功积累成行为习惯；避免大目标落空引发的挫败。",
            "time": "每个小目标 1-2 周复盘一次。",
        },
    ]


# --------------------------------------------------------------------------- #
# 当前实时状态评估（基于最近消息，而非历史人格标签）
# --------------------------------------------------------------------------- #
_EMOTION_KEYWORDS = [
    ("愤怒", ("废物", "死人", "滚", "吼", "发脾气", "气死", "吵", "骂")),
    ("冷漠/回避", ("不回", "没兴趣", "随便", "算了", "没必要", "忙", "别烦")),
    ("焦虑/追问", ("你在哪", "为什么不回", "是不是不爱", "在干嘛", "想你", "你是不是")),
    ("开心/热情", ("哈哈", "好呀", "想见你", "亲爱的", "宝宝", "好开心")),
    ("疲惫/emo", ("累", "不想说话", "烦", "没意思", "唉", "心情差")),
]


def assess_current_state(recent_messages: list[str] | None) -> dict:
    """从最近聊天记录提取当前情绪/依恋激活/近期趋势。

    Args:
        recent_messages: 最近若干条对方/我方文本（按时间正序）。
    Returns:
        {emotion, attachment_activation, trend, evidence}
    """
    msgs = [m for m in (recent_messages or []) if m]
    text = " ".join(msgs)
    emotion = "平稳/中性"
    for label, kws in _EMOTION_KEYWORDS:
        if any(k in text for k in kws):
            emotion = label
            break

    # 当前依恋激活状态（此刻的，不是固定标签）——先判焦虑追问，再判回避疏离
    att = "安全/中性"
    if any(k in text for k in ("你在哪", "为什么不回", "是不是不爱", "在干嘛")):
        att = "焦虑激活（此刻在追问/粘人）"
    elif any(k in text for k in ("不回", "算了", "忙", "别烦", "没必要")):
        att = "回避激活（此刻在疏离）"

    # 近期趋势：后几条 vs 前几条的温度粗判（升温/转冷/稳定）
    trend = "稳定"
    if len(msgs) >= 4:
        early, late = msgs[: len(msgs) // 2], msgs[len(msgs) // 2:]
        warm = ("哈哈", "好呀", "亲爱的", "宝宝", "想见你", "想你")
        cold = ("不回", "算了", "忙", "滚", "废物", "没必要")
        late_warm = sum(any(k in m for k in warm) for m in late)
        early_cold = sum(any(k in m for k in cold) for m in early)
        late_cold = sum(any(k in m for k in cold) for m in late)
        early_warm = sum(any(k in m for k in warm) for m in early)
        if late_warm > early_warm and late_cold == 0:
            trend = "近期转热/主动靠近"
        elif late_cold > early_warm or (early_warm >= 1 and late_cold > 0):
            trend = "近期转冷/越来越冷淡"

    return {"emotion": emotion, "attachment_activation": att,
            "trend": trend, "evidence": text[:60]}


def _feedback_branches(state: dict, style: str) -> list[dict]:
    """根据当前状态给出反馈分支：对方不同反应→不同下一步。"""
    branches = [
        {
            "when": "对方对你的减量/条件交换表示理解、甚至主动约你/关心你",
            "then": "放慢减量节奏，正强化这次健康互动，引导对方继续投入（富兰克林/承诺一致性）",
        },
        {
            "when": "对方一提要求不被立刻满足就翻脸、威胁分手、辱骂或冷暴力",
            "then": "判定为纯索取/操控固化——按温水里的刚猛支点明确止损一次；不急于加码挽回",
        },
        {
            "when": "对方无明显反应（不冷不热、不主动也不跑）",
            "then": "维持当前减量档位观察一周，不主动加码也不撤退；看是否出现主动投入的信号",
        },
        {
            "when": "对方反过来投入时间/情感/小行动",
            "then": "进入引导变好路线：正强化 + 情感投标双向化 + 可达成小目标",
        },
    ]
    return branches


# --------------------------------------------------------------------------- #
# 策略风格判断：刚猛（direct）vs 温水煮青蛙（warm_water）
# --------------------------------------------------------------------------- #
def _judge_style(
    patterns: list[dict],
    stage: int = 6,
    user_goal: str = "引导修复",
    user_ability: str = "一般",
) -> dict:
    """根据 5 维选择整体反制风格。

    Args:
        patterns: 识别到的操控模式列表。
        stage: 关系阶段 1-8。
        user_goal: 用户目标（引导修复/反制驯服/立刻止损）。
        user_ability: 用户执行能力（强/一般/弱）。
    Returns:
        {style, reason, key_moment_direct, combo_note}
        style: "warm_water"（渐进式）或 "direct"（刚猛）。
    """
    pkeys = {p["key"] for p in patterns}
    reasons: list[str] = []
    direct_score = 0
    warm_score = 0

    # 1) 关系阶段
    if stage <= 3:
        warm_score += 2
        reasons.append(f"关系处于初识/暧昧期(stage{stage})，刚猛易吓跑对方")
    elif stage >= 6:
        warm_score += 1
        reasons.append(f"长期/稳定期(stage{stage})有感情基础兜底，可适度刚猛")

    # 2) 对方人格/操控类型
    if "gaslighting" in pkeys or "money_control" in pkeys:
        warm_score += 2
        reasons.append("对方属金钱/煤气灯操控型，突然断供会直接跑，需渐进式")
    if "verbal_degradation" in pkeys:
        direct_score += 1
        reasons.append("存在辱骂越界，关键节点需明确止损")

    # 3) 问题严重程度
    severe = ("verbal_degradation" in pkeys
              and "money_control" in pkeys)
    if severe:
        direct_score += 1
        reasons.append("辱骂+金钱操控并存，属重度，需在底线节点刚猛")

    # 4) 用户目标
    if user_goal in ("引导修复", "反制驯服"):
        warm_score += 2
        reasons.append(f"用户目标为「{user_goal}」，需保留引导窗口")
    elif user_goal == "立刻止损":
        direct_score += 3
        reasons.append("用户目标为「立刻止损」，刚猛为主")

    # 5) 用户执行能力
    if user_ability == "强":
        direct_score += 1
        reasons.append("用户执行力强，可承受刚猛步骤")
    else:
        warm_score += 1
        reasons.append("用户执行能力一般，给更细更慢的渐进步骤")

    style = "direct" if direct_score > warm_score else "warm_water"

    # 关键节点刚猛（温水为主但保留一个刚猛支点）
    key_moment_direct = (
        "整体渐进式，但在对方辱骂/触及你的底线（如人格贬损）那一次"
        "明确、简短地刚猛止损一次——这是温水里的一个刚猛支点（阿伦森效应：先暖后立边界更被记住）。"
        if style == "warm_water"
        else "整体直接设边界、快速减量；仅在对方愿意做小投入的瞬间短暂回温，避免一味硬邦邦。"
    )

    style_name = "温水煮青蛙（渐进式）" if style == "warm_water" else "刚猛（直接止损）"
    return {
        "style": style,
        "style_name": style_name,
        "reason": "；".join(reasons),
        "key_moment_direct": key_moment_direct,
    }


def build_counter_manipulation(
    persona: dict | None = None,
    finance_risk_flags: list | None = None,
    stage: int = 6,
    user_goal: str = "引导修复",
    user_ability: str = "一般",
    recent_messages: list[str] | None = None,
) -> dict:
    """主入口：基于画像 + 当前实时状态构建动态反制 + 引导方案。

    输出新增动态字段（向后兼容）：
      current_state / strategy_style / expected_reaction /
      feedback_branches / validity_window。
    """
    persona = persona or {}
    finance_risk_flags = finance_risk_flags or []
    patterns = _detect_patterns(persona, finance_risk_flags)
    if not patterns:
        return {
            "triggered": False,
            "manipulation_patterns": [],
            "current_state": {},
            "strategy_style": {},
            "counter_strategies": [],
            "coaching": [],
            "feedback_branches": [],
            "neutral_note": "",
        }

    # 当前实时状态（情绪/依恋激活/趋势），不依赖历史标签
    current_state = assess_current_state(recent_messages)
    style = _judge_style(patterns, stage=stage, user_goal=user_goal, user_ability=user_ability)
    counter = _counter_strategies(patterns)
    coaching = _coaching(patterns)
    branches = _feedback_branches(current_state, style["style"])

    # 情绪适配：若此刻对方愤怒/冷漠，先降温/给空间，不急着推进反制
    emotion_note = ""
    if current_state["emotion"] == "愤怒":
        emotion_note = "对方此刻在气头上：本轮先共情降温、不辩对错，反制步骤放到情绪平复后再执行。"
    elif "回避" in current_state["attachment_activation"]:
        emotion_note = "对方此刻处于回避激活：轻量接触、不追问、给空间，反制节奏放慢一档。"

    expected_reaction = (
        "渐进减量/条件交换后，对方大概率出现三种反应之一：理解并配合（有引导空间）、"
        "立刻翻脸威胁（纯索取信号）、不冷不热（观察期）——按下方反馈分支对应处理。"
    )
    neutral_note = (
        f"当前状态：情绪「{current_state['emotion']}」、依恋激活「{current_state['attachment_activation']}」、"
        f"趋势「{current_state['trend']}」。"
        f"本次采用「{style['style_name']}」——依据：{style['reason']}。"
        f"{style['key_moment_direct']} {emotion_note}"
        f" 预期反应：{expected_reaction}"
        " 路线每条均附预期效果与代价；是否继续/反制/引导/离开由你自选，系统不做道德裁决。"
    )
    return {
        "triggered": True,
        "manipulation_patterns": patterns,
        "current_state": current_state,
        "strategy_style": style,
        "counter_strategies": counter,
        "coaching": coaching,
        "expected_reaction": expected_reaction,
        "feedback_branches": branches,
        "validity_window": "接下来 24-72 小时，或直到对方对你的减量/条件交换给出明确反应为止",
        "neutral_note": neutral_note,
    }
