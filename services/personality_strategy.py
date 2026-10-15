# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 人格倾向识别与策略适配（14 种）

把"人格类型"从硬编码标签升级为"基于画像特征自动推断的倾向评分"：
  - 从 21 维画像中抽取可观测信号（依恋三维 / CBT 认知扭曲 / 防御性沟通 /
    黑暗三角 / 情绪模式 / 冲突风格 / 边界 / 自我分化 / 信任度 / pua_score）；
  - 每种人格倾向是一组"加权特征签名"，命中越多、分越高；
  - 输出是"表现出 XX 倾向"，不是医学诊断。

覆盖（与 knowledge/disciplines/psychology/personality_types.md 对齐）：
  A 簇：偏执 PPD / 分裂样 schizoid / 分裂型 schizotypal
  B 簇：反社会 ASPD / 边缘 BPD / 表演 HPD / 自恋 NPD
  C 簇：回避 AvPD / 依赖 DPD / 强迫 OCPD
  常见特质：抑郁 depressive / 被动攻击 passive_aggressive / 高敏感 hsp / 述情障碍 alexithymia

导向：修复引导。策略用于理解对方行为背后的恐惧与需求、引导更健康的互动，
不标签化、不歧视、不输出操控手段。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ====================================================================== #
# 一、特征抽取：把嵌套画像归一为可打分的数值/类别信号
# ====================================================================== #
def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _cnt(d: Any, *keys: str) -> float:
    """从 dict 中取多个子键的数值之和（兼容中文/英文键名）。"""
    if not isinstance(d, dict):
        return 0.0
    total = 0.0
    for k in keys:
        total += _num(d.get(k, 0))
    return total


def extract_features(persona: dict) -> dict:
    """从画像抽取统一特征向量（0-2 量级为主，便于加权）。

    Returns:
        dict: 各信号的数值/类别取值。
    """
    persona = persona or {}

    # 依恋三维
    attach = persona.get("attachment") or {}
    if isinstance(attach, dict):
        att_avoid = _num(attach.get("avoidance_score"), 50)
        att_anx = _num(attach.get("anxiety_score"), 50)
        att_type = str(attach.get("primary_type") or attach.get("type") or "")
    else:
        att_avoid = att_anx = 50.0
        att_type = str(attach or "")

    psych = persona.get("psychology") or {}
    cbt = psych.get("cbt_distortions") or {}
    defensive = psych.get("defensive_communication") or {}
    dark = psych.get("dark_triad_tendencies") or {}

    emo = persona.get("emotion_pattern") or {}
    emo_vol = str(emo.get("volatility") or "")
    emo_pat = str(emo.get("pattern") or "")

    conflict = persona.get("conflict_style") or {}
    conflict_key = str(conflict.get("primary_key") or conflict.get("primary") or "")

    bounds = persona.get("boundaries") or {}
    boundary = str(bounds.get("personal_boundary") or bounds or "")

    diff = persona.get("self_differentiation") or {}
    diff_level = str(diff.get("level") or diff or "")

    trust = persona.get("trustworthiness") or {}
    trust_level = str(trust.get("level") or "")
    if not trust_level:
        ts = _num(trust.get("overall_trust_score"), -1)
        if ts >= 0:
            trust_level = "high" if ts >= 80 else "medium" if ts >= 60 else "low" if ts >= 40 else "critical"

    rw = persona.get("risk_warning") or {}
    pua = _num(rw.get("pua_score"), 0)

    return {
        # 依恋
        "att_avoid": att_avoid,
        "att_anx": att_anx,
        "att_type": att_type,
        # CBT 认知扭曲（命中次数）
        "cbt_blackwhite": _cnt(cbt, "非黑即白"),
        "cbt_catastrophe": _cnt(cbt, "灾难化"),
        "cbt_mindread": _cnt(cbt, "读心术"),
        "cbt_should": _cnt(cbt, "应该陈述"),
        "cbt_personalize": _cnt(cbt, "个人化"),
        # 防御性沟通
        "def_devalue": _cnt(defensive, "贬低感受"),
        "def_deny": _cnt(defensive, "否定现实"),
        "def_shiftblame": _cnt(defensive, "推卸责任"),
        "def_moral": _cnt(defensive, "道德绑架"),
        # 黑暗三角
        "dark_narc": _cnt(dark, "自恋", "narcissism"),
        "dark_manip": _cnt(dark, "操纵", "manipulation"),
        "dark_callous": _cnt(dark, "冲动无情", "psychopathy", "无情"),
        # 情绪
        "emo_vol": emo_vol,            # 高/中/低
        "emo_pat": emo_pat,            # 积极主导/偏积极/消极倾向/...
        # 冲突 / 边界 / 分化 / 信任
        "conflict_key": conflict_key,
        "boundary": boundary,
        "diff_level": diff_level,
        "trust_level": trust_level,
        "pua": pua,
    }


# ====================================================================== #
# 二、人格倾向签名（加权特征）+ 策略/任务档案
# ====================================================================== #
# 每个签名是一组 (feature, 比较, 阈值, 权重)；命中即累加权重。
# features 里数值类为连续值，类别类用字符串等于判断。
# 设计原则：
#   - 核心信号给高权重（决定识别），次要信号给低权重（微调）；
#   - 相近人格（PPD/AvPD/DPD、BPD/HSP/HPD）用区分信号互相拉开；
#   - 只有总分 >= MIN_SCORE 才算"表现出倾向"，避免误报。

MIN_SCORE = 3.0  # 最低入选分

# key -> (中文名, 签名列表, 策略 recs, forbids, note模板, tasks)
# 签名比较算子：">=", "<=", "==", "has"
_PERSONALITY_PROFILES: dict[str, dict[str, Any]] = {
    # ---------------- A 簇 ----------------
    "ppd": {
        "label": "偏执倾向",
        "signals": [
            ("trust_level", "==", "low", 2.5),
            ("trust_level", "==", "critical", 3.0),
            ("cbt_mindread", ">=", 1, 2.0),     # 把恶意读出来
            ("def_deny", ">=", 1, 1.0),        # 反复核查/否认现实
            ("cbt_blackwhite", ">=", 1, 1.0),
            ("att_anx", ">=", 55, 1.0),        # 警觉
        ],
        "exclude": [("dark_callous", ">=", 2)],  # 无情=ASPD，不是PPD
        "recs": [
            "用事实和时间线回应质问，不情绪化",
            "主动、可预期地同步行程，不突然消失",
            "一次只澄清一个事实，不堆一堆辩解",
            "在对方质问峰值时先接住，暂缓深入",
        ],
        "forbids": [
            "突然玩消失不解释行踪",
            "说对方太敏感或被害妄想",
            "反将一军质问对方为什么不信你",
            "半真半假、开玩笑式隐瞒",
        ],
        "tasks": [
            "通过记录对方每次猜疑的具体触发点和原话，"
            "建立事实清单，下次用事实而非情绪回应，"
            "最终在不被质问带跑的情况下慢慢积累信任",
            "通过主动、提前同步自己的行程和安排（不突然消失），"
            "用稳定可预期的行动代替口头辩解，"
            "逐步降低对方的被背叛警觉",
            "通过在对方连珠炮质问时只平静澄清一个核心事实，"
            "不堆辩解、不反诘，理解这是对方保护自己的方式，"
            "最终让关系里少一点信任战争",
        ],
    },
    "schizoid": {
        "label": "分裂样倾向",
        "signals": [
            ("att_avoid", ">=", 70, 3.0),
            ("boundary", "==", "清晰", 1.5),
            ("emo_pat", "==", "中性平稳", 1.5),
            ("diff_level", "==", "高", 1.0),
        ],
        "exclude": [("att_anx", ">=", 55)],  # 高焦虑=不是无欲
        "recs": [
            "用共同做事代替纯情感对话",
            "简洁具体低情绪浓度沟通",
            "接受对方用行动而非甜言蜜语表达关心",
        ],
        "forbids": [
            "逼问对方到底爱不爱我",
            "用大哭大闹逼对方给情绪回应",
            "强行安排大量社交活动",
        ],
        "tasks": [
            "通过约对方一起做一件具体的事（做饭/散步/看片），"
            "在并肩做事中自然靠近，而不是逼对方谈感受，"
            "最终用行动建立连接",
            "通过观察并记录对方用行动（帮忙做事）表达关心的瞬间，"
            "正向反馈这些瞬间，理解这就是他的爱语",
        ],
    },
    "schizotypal": {
        "label": "分裂型倾向",
        "signals": [
            ("att_anx", ">=", 60, 2.0),
            ("cbt_mindread", ">=", 1, 1.5),
            ("cbt_catastrophe", ">=", 1, 1.5),
            ("trust_level", "==", "low", 1.0),
        ],
        "exclude": [("dark_callous", ">=", 2)],
        "recs": [
            "先温和接纳对方独特感受，再轻轻拉回具体事实",
            "把相处放在低压力一对一环境",
        ],
        "forbids": [
            "当众嘲笑或纠正对方的古怪信念",
            "说对方胡思乱想或迷信",
        ],
        "tasks": [
            "通过在对方分享直觉或感受时先接住不否定，"
            "再温和问一个落地问题，帮助对方把奇特感受连回现实",
        ],
    },

    # ---------------- B 簇 ----------------
    "aspd": {
        "label": "反社会倾向",
        "signals": [
            ("dark_callous", ">=", 2, 3.0),    # 无情/无动于衷
            ("dark_manip", ">=", 1, 2.0),
            ("def_shiftblame", ">=", 2, 1.5),  # 推卸=缺乏悔意
            ("conflict_key", "==", "destructive", 1.0),
            ("conflict_key", "==", "competing", 1.0),
        ],
        "exclude": [],
        "recs": [
            "一切重要约定落到文字，留痕",
            "清晰冷静对事不对人，不被魅力或暴怒带节奏",
            "评估关系安全度，必要时寻求外部支持",
        ],
        "forbids": [
            "以为能用爱感化对方",
            "妥协底线换取和平",
            "替对方还债背锅或掩盖错误",
        ],
        "tasks": [
            "通过记录对方欺骗/违约/攻击行为的事实清单（时间+内容），"
            "作为自己是否继续这段关系的决策依据，而不是凭情绪和好",
        ],
    },
    "bpd": {
        "label": "边缘倾向",
        "signals": [
            ("emo_vol", "==", "高", 3.0),
            ("att_avoid", ">=", 60, 1.5),
            ("att_anx", ">=", 60, 1.5),        # 双高=恐惧型
            ("cbt_blackwhite", ">=", 1, 2.0),
            ("cbt_catastrophe", ">=", 1, 1.5),
            ("conflict_key", "==", "emotional_outburst", 1.5),
        ],
        "exclude": [("dark_callous", ">=", 2)],
        "recs": [
            "保持情绪稳定做关系的锚点，不被摆荡带走",
            "情绪峰值先安抚，平静后再复盘具体问题",
            "承诺要兑现，一致性比热情更重要",
        ],
        "forbids": [
            "忽冷忽热或玩消失",
            "用冷战惩罚对方",
            "对方崩溃时说你冷静点别闹",
        ],
        "tasks": [
            "通过在对方情绪爆发时保持冷静不被带跑，"
            "练习做关系中的稳定锚点，平静后一起回顾触发点",
            "通过在对方情绪平稳时温和而清晰地设定边界，"
            "区分安抚情绪与无底线妥协",
        ],
    },
    "hpd": {
        "label": "表演倾向",
        "signals": [
            ("emo_vol", "==", "高", 1.5),
            ("boundary", "==", "模糊", 1.5),
            ("diff_level", "==", "低", 1.5),
            ("cbt_should", ">=", 1, 1.0),
        ],
        "exclude": [("cbt_blackwhite", ">=", 1), ("dark_callous", ">=", 2)],
        "recs": [
            "给予真诚但不过度的关注",
            "用引导式提问从讲故事引向真实感受",
            "接住情绪后轻轻落到具体事实",
        ],
        "forbids": [
            "当众冷落或比对方更抢戏",
            "无节制地给夸张赞美",
            "嘲笑对方的夸张表达",
        ],
        "tasks": [
            "通过给对方真诚但不过度的关注，"
            "把话题从发生了什么引导到你当时真正的感受是什么，"
            "引导一次有深度的对话",
        ],
    },
    "npd": {
        "label": "自恋倾向",
        "signals": [
            ("dark_narc", ">=", 2, 3.0),
            ("def_devalue", ">=", 1, 2.0),
            ("dark_manip", ">=", 1, 1.5),
            ("pua", ">=", 5, 1.5),
        ],
        "exclude": [("dark_callous", ">=", 2)],  # 无情主导=ASPD
        "recs": [
            "识别煤气灯话术，守住自己的认知边界",
            "保持自我价值感，不因被贬低而自我怀疑",
            "对方平稳时用非暴力沟通表达真实感受",
        ],
        "forbids": [
            "当众挑战或让对方丢脸",
            "在被贬低时自我怀疑",
            "用热情感动、过度付出去拯救",
        ],
        "tasks": [
            "通过记录对方贬低/煤气灯/冷热交替的具体话术和时间，"
            "建立事实清单，用事实而非情绪回应",
            "通过在对方说你想多了/我没说过时不立即自我怀疑，"
            "保留聊天记录做事实锚点，守住自己的认知",
        ],
    },

    # ---------------- C 簇 ----------------
    "avpd": {
        "label": "回避型人格倾向",
        "signals": [
            ("att_avoid", ">=", 55, 2.0),
            ("att_anx", ">=", 55, 2.0),       # 想靠近又怕被拒
            ("cbt_catastrophe", ">=", 1, 1.5),  # 怕负面评价
            ("cbt_personalize", ">=", 1, 1.5),  # 觉得自己不行
            ("diff_level", "==", "低", 1.0),
        ],
        "exclude": [("dark_narc", ">=", 2)],
        "recs": [
            "给明确具体、不附加条件的肯定",
            "批评对事不对人并配上肯定",
            "在安全氛围里温和邀请对方表达真实想法",
        ],
        "forbids": [
            "用激将法说对方没用",
            "拿对方和别人比较",
            "嘲笑对方鼓起勇气后的表达",
        ],
        "tasks": [
            "通过主动给一次针对具体行为的真诚肯定（不空夸），"
            "并明确告诉对方这不会附加条件，逐步降低被嫌弃恐惧",
            "通过邀请对方做一件小事并认真听取他的想法，"
            "鼓励他表达真实意见而不被评价",
        ],
    },
    "dpd": {
        "label": "依赖倾向",
        "signals": [
            ("att_anx", ">=", 60, 2.5),
            ("cbt_personalize", ">=", 1, 2.0),   # 自我贬低/内疚
            ("boundary", "==", "模糊", 1.5),
            ("diff_level", "==", "低", 1.5),
            ("conflict_key", "==", "avoiding", 1.0),  # 顺从
        ],
        "exclude": [("att_avoid", ">=", 60)],  # 高回避=不是依附
        "recs": [
            "温和地把决定权还给对方：你怎么想，我想听你的",
            "对方自己做决定后具体肯定",
            "表达我需要你，但你也可以有自己的生活",
        ],
        "forbids": [
            "事事替对方做决定",
            "说对方怎么什么都不会",
            "用分手威胁对方",
        ],
        "tasks": [
            "通过鼓励对方独立做一个小决定（吃什么/去哪/选哪个），"
            "忍住不替对方拿主意，在他决定后具体肯定，"
            "逐步建立他独自决策的信心",
            "通过当对方凡事问你定的时候，反问一句你倾向哪个，"
            "把选择权和责任慢慢还给他，"
            "引导健康的依赖而非依附",
        ],
    },
    "ocpd": {
        "label": "强迫型倾向",
        "signals": [
            ("cbt_should", ">=", 2, 3.0),      # 应该/必须/规定
            ("diff_level", "==", "高", 1.0),
            ("conflict_key", "==", "compromising", 0.5),
        ],
        "exclude": [],
        "recs": [
            "先认可对方的责任感和安排",
            "引导偶尔做一件差不多就行的事",
            "在对方有掌控感时提议放松",
        ],
        "forbids": [
            "当众嘲笑对方刻板较真",
            "故意把事情搞乱来整对方",
            "说对方是控制狂或强迫症",
        ],
        "tasks": [
            "通过和对方一起做一件不查攻略、不追求完美的事，"
            "体验不完美也可以很开心，引导放下一点标准",
            "通过在对方挑细节时温和提醒这次的目标是开心不是满分，"
            "同时主动肯定他的负责",
        ],
    },

    # ---------------- 常见特质 ----------------
    "depressive": {
        "label": "抑郁倾向",
        "signals": [
            ("emo_pat", "==", "消极倾向", 3.0),
            ("cbt_personalize", ">=", 1, 2.0),
            ("cbt_catastrophe", ">=", 1, 1.5),
        ],
        "exclude": [("emo_vol", "==", "高")],  # 高波动摆荡=BPD
        "recs": [
            "温和陪伴，不说你想开点",
            "用极小的事带来一点掌控感和愉悦",
            "对方自我贬低时给一个具体反例",
        ],
        "forbids": [
            "说对方想太多或振作一点",
            "指责对方把负能量带给你",
            "忽视持续低落或自伤信号",
        ],
        "tasks": [
            "通过陪对方做一件极小的、有一点愉悦或掌控感的事"
            "（散步/晒太阳/吃喜欢的），不追求治愈，只是陪着",
        ],
    },
    "passive_aggressive": {
        "label": "被动攻击倾向",
        "signals": [
            ("conflict_key", "==", "cold_war", 2.5),
            ("conflict_key", "==", "avoiding", 1.5),
            ("def_shiftblame", ">=", 1, 1.5),
        ],
        "exclude": [("dark_callous", ">=", 2), ("dark_narc", ">=", 2)],
        "recs": [
            "温和但直接把隐形需求摆上台面",
            "鼓励对方直接说不，并保证不惩罚",
            "不接受我没事就翻篇",
        ],
        "forbids": [
            "比对方更强制催得更紧",
            "讽刺回去",
            "替对方把该做的都做了",
        ],
        "tasks": [
            "通过在对方拖延或遗忘时平静地问一次真实意愿，"
            "鼓励他直接表达不想做或有困难，"
            "并在他直接表达时正面接住不惩罚，"
            "强化直接说比拖延更有效",
        ],
    },
    "hsp": {
        "label": "高敏感倾向",
        "signals": [
            ("emo_vol", "==", "高", 2.5),
            ("att_anx", ">=", 50, 1.5),
        ],
        # HSP 需与 BPD/HPD 区分：无黑暗三角、无非黑即白/灾难化主导
        "require_not": [
            ("cbt_blackwhite", ">=", 1),
            ("dark_callous", ">=", 2),
            ("dark_narc", ">=", 2),
        ],
        "recs": [
            "重要谈话选安静低干扰环境，语气温和",
            "主动确认对方状态，给足缓冲和恢复时间",
            "冲突后给独处恢复，再温和复盘",
        ],
        "forbids": [
            "说对方太敏感或这点事至于吗",
            "在对方过载时逼他当场解决",
            "提高音量吵架",
        ],
        "tasks": [
            "通过重要谈话前主动确认环境和对方状态、给足缓冲，"
            "并在冲突后尊重他的独处恢复时间，"
            "不把敏感当冷漠",
        ],
    },
    "alexithymia": {
        "label": "述情障碍倾向",
        "signals": [
            ("emo_pat", "==", "中性平稳", 2.5),
            ("diff_level", "==", "高", 1.5),
            ("boundary", "==", "清晰", 1.0),
        ],
        "exclude": [("att_anx", ">=", 55)],  # 高焦虑=不是钝感
        "recs": [
            "用具体可观察的事实代替抽象情绪词",
            "教对方命名情绪：你现在是不是有点烦",
            "欣赏对方用行动和解决问题表达关心",
        ],
        "forbids": [
            "反复逼问对方到底什么感觉",
            "说对方冷血或没有感情",
            "把对方给方案当成不解风情而否定",
        ],
        "tasks": [
            "通过用具体事实描述自己的状态并帮对方给情绪命名，"
            "而不是要他自己悟，并在他用行动关心时明确反馈我感受到了",
        ],
    },
}


# ====================================================================== #
# 三、人格 × 依恋组合微调
# ====================================================================== #
# (人格, 依恋桶) -> 追加到 note 的组合说明；recs/forbids 在基础上再微调
_COMBINATION_NOTES: dict[tuple[str, str], str] = {
    ("npd", "avoidant"): "组合为疏离型自恋：既贬低又情感抽离，用我不需要你维持优越感。不要把他的抽离当成需要空间，不追、不讨好。",
    ("npd", "anxious"): "组合为脆弱型自恋：持续需要赞美，被忽视会暴怒。给适度真诚肯定但不跪舔，不把他的情绪当自己的责任。",
    ("bpd", "fearful"): "组合为最不稳定的边缘+恐惧型：理想化-贬低与靠近-推开满格叠加。一致性是唯一解药，稳定本身就是修复，同时评估关系长期成本。",
    ("dpd", "anxious"): "组合为极度黏人：既怕被抛弃又做不了主。给安全感的同时温柔坚定地把小决策还给他，警惕他把全部情感重量压到你身上。",
    ("ppd", "avoidant"): "组合为多疑+退缩：既不信任又不沟通。不要逼问（他更缩），用稳定可预期的日常行动慢慢积累信任。",
    ("aspd", "avoidant"): "组合为冷酷操控：疏离、精打细算、把你当工具。安全优先，不幻想感化，重要利益留痕。",
    ("hpd", "anxious"): "组合为表演+索关注黏人：情绪夸张又追着要关注。给真诚但不过量的关注，不被戏剧化情绪绑架。",
    ("avpd", "anxious"): "组合为怕被拒又怕被抛下：想靠近又觉得自己不配。明确、反复、不附加条件地肯定他。",
    ("ocpd", "anxious"): "组合为越不安越抓规则：焦虑越高越抠细节。先认可他的负责，在他安全时慢慢引入弹性。",
    ("passive_aggressive", "avoidant"): "组合为冷战+拖延：表面随便你，行动上拖延遗忘。温和直接把隐形问题摆上台面，鼓励直接表达。",
    ("hsp", "anxious"): "组合为易被情绪淹没：对氛围和你语气极敏感又怕被抛弃。低刺激沟通，吵架后给足恢复时间。",
    ("alexithymia", "avoidant"): "组合为用事代替情：疏离又说不出感受。不逼他谈情绪，欣赏他用行动表达的爱。",
}


# ====================================================================== #
# 四、识别：特征 -> 倾向评分排序
# ====================================================================== #
def _compare(val: Any, op: str, thr: Any) -> bool:
    try:
        if op == ">=":
            return float(val) >= float(thr)
        if op == "<=":
            return float(val) <= float(thr)
        if op == "==":
            return str(val).strip() == str(thr).strip()
    except (TypeError, ValueError):
        # 类别型 == 比较
        return str(val).strip() == str(thr).strip()
    return False


def detect_personality(persona: dict) -> list[dict]:
    """基于画像特征计算 14 种人格倾向的得分，返回降序列表。

    Returns:
        list[dict]: 每项 {key, label, score, matched:[命中的信号描述]}，按 score 降序。
    """
    feat = extract_features(persona)
    results: list[dict] = []
    for key, prof in _PERSONALITY_PROFILES.items():
        # 排除信号：命中排除项直接 0 分（避免相近人格混淆）
        excluded = any(_compare(feat.get(f, ""), op, thr)
                       for f, op, thr, *_ in prof.get("exclude", []))
        if excluded:
            results.append({"key": key, "label": prof["label"], "score": 0.0, "matched": []})
            continue
        score = 0.0
        matched: list[str] = []
        for sig in prof["signals"]:
            f, op, thr, w = sig
            if _compare(feat.get(f, ""), op, thr):
                score += w
                matched.append(f"{f}{op}{thr}")
        # require_not：这些特征一旦出现就大幅扣分（HSP/BPD 区分用）
        for f, op, thr, *_ in prof.get("require_not", []):
            if _compare(feat.get(f, ""), op, thr):
                score -= 3.0
        results.append({"key": key, "label": prof["label"], "score": round(score, 2), "matched": matched})

    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def primary_tendency(persona: dict) -> Optional[dict]:
    """返回得分最高且过阈的人格倾向；无明显倾向返回 None。"""
    ranked = detect_personality(persona)
    if not ranked or ranked[0]["score"] < MIN_SCORE:
        return None
    top = ranked[0]
    # 若前两名分差很小（<1.0），仍取最高，但标注次高
    top["runner_up"] = ranked[1]["label"] if len(ranked) > 1 else None
    return top


# ====================================================================== #
# 五、策略与任务生成
# ====================================================================== #
def _attachment_bucket(persona: dict) -> str:
    """从画像依恋字段判断四型桶，用于组合微调。"""
    feat = extract_features(persona)
    a, x = feat["att_avoid"], feat["att_anx"]
    t = feat["att_type"]
    if "恐惧" in t or "混乱" in t or "fearful" in t.lower():
        return "fearful"
    if "回避" in t or "avoidant" in t.lower():
        return "avoidant"
    if "焦虑" in t or "anxious" in t.lower():
        return "anxious"
    if "安全" in t or "secure" in t.lower():
        return "secure"
    # 无类型字段时按三维分
    if a >= 60 and x >= 60:
        return "fearful"
    if a >= 60:
        return "avoidant"
    if x >= 60:
        return "anxious"
    return "secure"


# --------------------------------------------------------------------------- #
# 三层校准结论(final_judgment)优先覆盖：按画像类型分支（规格 3.1 / 约束②全类型通用）
# --------------------------------------------------------------------------- #
# 根因：旧启发式只看旧数值特征（emo_vol/att_avoid/cbt计数），从不读
# three_layer.final_judgment，把"17次压力屈服/0主动修复"误读成 BPD 边缘倾向8.0，
# 对疏离型回避输出"情绪爆发时保持冷静/设定边界"——与校准结论直接冲突。
# 解法：画像已含三层校准结论时，依恋类型以 final_judgment.attachment_lean 为准，
# 按类型选择任务/策略框架；旧启发式仅在"恐惧/混乱(双高)"或无校准结论时运行，
# BPD 类任务只在恐惧型分支才允许出现。全程本地规则，无付费 LLM。

# 依恋型基础话术（从原 build_strategy 内部 _FALLBACK 上提为模块级，供两入口复用）
_ATTACHMENT_FALLBACK: dict[str, tuple[list[str], list[str], str, str]] = {
    "avoidant": (
        ["给对方空间，不追问不黏人", "用低压力方式保持联系（分享有趣内容而非查岗）", "等对方主动靠近时再热情回应"],
        ["频繁发消息追问", "逼对方谈感受", "用冷战惩罚对方的冷淡"],
        "对方呈现回避型依恋倾向：需要空间、不喜欢被追问。给空间不等于疏远，保持低压力的温暖连接即可。",
        "fallback_avoidant",
    ),
    "anxious": (
        ["主动表达在意，给确定感", "回复及时，不让对方悬着", "定期确认关系状态，减少不安全感"],
        ["忽冷忽热", "用冷淡测试对方", "翻旧账引发焦虑"],
        "对方呈现焦虑型依恋倾向：需要确定感和回应。稳定的回应比偶尔的浪漫更重要。",
        "fallback_anxious",
    ),
    "fearful": (
        ["节奏放慢，建立安全感", "不逼对方敞开心扉，等对方自己说", "一致性强，说到做到"],
        ["推进太快", "逼谈未来", "在对方退缩时质问"],
        "对方呈现恐惧型依恋倾向：既渴望亲密又害怕受伤。慢就是快，用一致性建立信任。",
        "fallback_fearful",
    ),
}

# 疏离回避型低压力任务（复用 avoidant 话术语义；修复引导导向，不贴标签）
_AVOIDANT_LOWPRESSURE_TASKS: list[str] = [
    # 留灯式：连接频率保持稳定、不升温也不撤退，只降情感浓度与追问强度，
    # 内容低压+埋根基（"在的，不急，你忙你的"），避免被高回避型误读为"对方也要走"。
    "通过保持稳定而轻量的存在感——照常偶尔分享一条轻松日常，用一句'在的，不急，你忙你的'"
    "式的低压回应，只降情感浓度和追问强度、不降连接本身，让她知道你随时在但不逼近",
    "通过在她冷淡或退缩时仍留一盏低压的灯（不追问、不质问、也不突然消失），"
    "用可预期的稳定节奏传递'我不会因你退缩就走开'，消解她抢先撤退的冲动",
    "通过记录她放松状态下主动开口或语气软化的信号、在她自然靠近时温和接住，"
    "其余时间保持稳定的轻连接而非忽冷忽热，逐步在安全感里重建连接",
]

# 焦虑型稳定感任务（本地模板，参照 direction_compass / strategy_advisor 焦虑词表）
_ANXIOUS_STABILITY_TASKS: list[str] = [
    "通过保持可预期的回复节奏、不让她长时间悬着等待，用及时而稳定的回应给她确定感，"
    "理解她的反复追问源于对被抛下的焦虑而非要控制你",
    "通过主动而明确地表达在意、定期确认关系状态，不玩消失也不忽冷忽热，"
    "用一致性的行动持续积累她的安全感，逐步减少她的反复确认",
]


def _llm_override(persona: dict) -> dict:
    """读取三层校准结论 three_layer.final_judgment（本地规则校准，非真 LLM）。

    Returns:
        dict: {present, attachment_lean, corrected, proactive_repair,
               pressure_yield, confidence}。无 final_judgment 时 present=False。
    """
    fj = (persona.get("three_layer") or {}).get("final_judgment") or {}
    if not isinstance(fj, dict) or not fj:
        return {"present": False, "attachment_lean": "", "corrected": False,
                "proactive_repair": None, "pressure_yield": None, "confidence": ""}
    corr = fj.get("yield_attribution_correction") or {}
    return {
        "present": True,
        "attachment_lean": str(fj.get("attachment_lean", "") or ""),
        "corrected": bool(corr.get("corrected")),
        "proactive_repair": corr.get("proactive_repair_count"),
        "pressure_yield": corr.get("pressure_yield_count"),
        "confidence": str(fj.get("confidence", "") or ""),
    }


def _calibrated_bucket(persona: dict, ov: dict) -> Optional[str]:
    """按三层校准结论把画像分到任务桶。

    Returns:
        str | None: "avoidant_lowpressure" / "anxious_stability" /
        "secure_none" / "fearful_old"；None=无校准结论，回退旧启发式。
    """
    lean = ov.get("attachment_lean") or ""
    if not lean:
        return None
    feat = extract_features(persona)
    a, x = feat["att_avoid"], feat["att_anx"]

    # 疏离回避型：lean 含"回避"且不含"焦虑" → 硬压 BPD/边缘，走给空间低压力任务
    if "回避" in lean and "焦虑" not in lean:
        return "avoidant_lowpressure"
    # 焦虑型：lean 含"焦虑"且不含"回避" → 稳定感/确定性任务
    if "焦虑" in lean and "回避" not in lean:
        return "anxious_stability"
    # 恐惧/混乱(双高)：保留旧启发式，BPD 类仅在此分支可出
    if "恐惧" in lean or "混乱" in lean:
        return "fearful_old"
    # 安全型：双低 → 无专属任务
    if "安全" in lean:
        return "secure_none"
    # lean 存在但无法归类 → 用依恋分数兜底
    if a >= 60 and x >= 60:
        return "fearful_old"
    if a >= 60:
        return "avoidant_lowpressure"
    if x >= 60:
        return "anxious_stability"
    return "secure_none"


def build_strategy(persona: dict) -> tuple[list[str], list[str], str, Optional[str]]:
    """识别画像的主导人格倾向，返回 (recs, forbids, note, key)。

    无明显倾向时返回 ([], [], "", None)。
    note 一律使用"表现出 XX 倾向"的修复引导措辞。
    """
    # 三层校准结论优先：按校准依恋类型分支，避免旧启发式与校准结论冲突
    ov = _llm_override(persona)
    cbucket = _calibrated_bucket(persona, ov)
    if cbucket == "avoidant_lowpressure":
        r, f, n, k = _ATTACHMENT_FALLBACK["avoidant"]
        return list(r), list(f), n, k
    if cbucket == "anxious_stability":
        r, f, n, k = _ATTACHMENT_FALLBACK["anxious"]
        return list(r), list(f), n, k
    if cbucket == "secure_none":
        return [], [], "", None
    # fearful_old 或无校准结论：走旧启发式

    tend = primary_tendency(persona)
    if not tend:
        # Fallback: 人格倾向未达阈值，但依恋类型已判断出来时，按依恋类型给基本策略
        # 仅在 attachment 是 dict（有真实分数）时触发；字符串形式由 strategy_advisor 自己处理
        attach_raw = persona.get("attachment")
        if not isinstance(attach_raw, dict):
            return [], [], "", None
        bucket = _attachment_bucket(persona)
        if bucket in _ATTACHMENT_FALLBACK:
            r, f, n, k = _ATTACHMENT_FALLBACK[bucket]
            return list(r), list(f), n, k
        return [], [], "", None

    key = tend["key"]
    prof = _PERSONALITY_PROFILES[key]
    recs = list(prof["recs"])
    forbids = list(prof["forbids"])

    bucket = _attachment_bucket(persona)
    combo = _COMBINATION_NOTES.get((key, bucket), "")

    note = (
        f"人格倾向审计：对方表现出{prof['label']}（倾向评分{tend['score']}）。"
        "这是基于长期行为模式的推断，不是医学诊断——这些模式往往源于对方早年形成的自我保护方式。"
        "建议用以下方式理解和互动：" + "；".join(recs) + "。"
    )
    if combo:
        note += f" 依恋叠加：{combo}"
    ru = tend.get("runner_up")
    if ru:
        note += f"（同时略有{ru}的影子，策略以主导倾向为主即可。）"
    return recs, forbids, note, key


def build_tasks(persona: dict) -> tuple[str, list[str]]:
    """识别主导人格倾向，返回 (note, task_titles)。无倾向返回 ("", [])。

    三层校准结论优先：按校准依恋类型分支（疏离回避→给空间低压力；焦虑→稳定感；
    安全→无专属任务），避免旧启发式对疏离回避误出 BPD 边缘类任务。
    """
    ov = _llm_override(persona)
    cbucket = _calibrated_bucket(persona, ov)
    if cbucket == "avoidant_lowpressure":
        if ov.get("corrected"):
            note = (
                "[人格专属任务] 校准后依恋倾向为「疏离型回避」：她的服软被归因纠正为"
                f"压力性屈服{ov.get('pressure_yield') or 0}次、主动修复{ov.get('proactive_repair') or 0}次——"
                "这是回避退缩而非情绪不稳型画像。对高回避型，连接必须稳定可预期、只降低情感施压"
                "与追问强度，绝不能靠突然冷落来腾空间（那会被误读成'对方也要离开'而触发抢先撤退）。"
                "以下为留灯式低压力引导：保持稳定轻连接，给空间但不切断连接，"
                "在她舒适距离里埋下安全感与主动靠近的根基。"
            )
        else:
            note = (
                "[人格专属任务] 校准后依恋倾向为「疏离型回避」：需要空间、不喜欢被追问，"
                "但连接要稳定可预期、只降施压强度，不能靠突然冷落来腾空间。"
                "以下为留灯式引导：保持稳定轻连接、不升温也不撤退，"
                "用可预期的低压陪伴让她在舒适距离里重建信任。"
            )
        return note, list(_AVOIDANT_LOWPRESSURE_TASKS)
    if cbucket == "anxious_stability":
        return (
            "[人格专属任务] 校准后依恋倾向为「焦虑型」：需要稳定回应与确定感来消解被抛下的焦虑。"
            "以下任务用一致性建立安全感，而非被她的追问牵着走或用冷战惩罚她的不安。",
            list(_ANXIOUS_STABILITY_TASKS),
        )
    if cbucket == "secure_none":
        return "", []
    # fearful_old 或无校准结论 → 旧启发式
    tend = primary_tendency(persona)
    if not tend:
        return "", []
    key = tend["key"]
    prof = _PERSONALITY_PROFILES[key]
    tasks = list(prof.get("tasks", []))
    note = (
        f"[人格专属任务] 对方表现出{prof['label']}（评分{tend['score']}）。"
        "以下任务用于引导更健康的互动方式，而非给对方贴标签。"
    )
    return note, tasks


# 供 strategy_advisor / task_engine 直接按 key 取档案（测试与扩展用）
def get_profile(key: str) -> Optional[dict]:
    return _PERSONALITY_PROFILES.get(key)


def all_keys() -> list[str]:
    return list(_PERSONALITY_PROFILES.keys())
