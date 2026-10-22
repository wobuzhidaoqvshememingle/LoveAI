# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 策略适配矩阵（因人施策 · 因时制宜）

把"心理学技巧"从固定模板升级为"按对方 21 维画像交叉匹配"：
同一个任务，对回避型和对焦虑型给出完全不同的推荐技巧与禁忌动作；
同时随关系阶段（初识/暧昧/热恋/矛盾/稳定）调整推进节奏，
并在画像更新后自动重算、检测策略漂移。

服务层纯 Python 实现：
  - 不 import PySide6，不继承 QObject；
  - 信号/通知完全由调用方（background_scheduler / UI）自行处理；
  - 数据库通过构造参数 db 注入，可空（无 db 时仅做内存计算）。

本模块是维度分层中的**诊断层**（六维人格：依恋/冲突/爱/边界/分化/情绪稳定），
本层维度命名空间见 person_state_snapshot.py 的 DimensionLayerRegistry。

学术依据（在匹配表 reason 与代码注释中标注来源）：
  - 依恋理论（Bowlby 1969 / Ainsworth 1978）：回避型/焦虑型/安全型/恐惧型的行为模式；
  - Bowen 家庭系统理论（1978）：自我分化 differentiation、情绪融合 emotional fusion；
  - Gottman 冲突研究：四骑士（批评/鄙视/辩护/冷战）与修复尝试 repair attempt；
  - 爱的五种语言（Gary Chapman, 1992）：肯定言辞/精心时刻/接受礼物/服务行动/身体接触。
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

from services import psychology_laws  # v2: 心理学定律数据层（修复引导导向）
from services import personality_strategy  # v4: 人格倾向识别与策略适配（14种）

if TYPE_CHECKING:
    from models.database import Database

logger = logging.getLogger(__name__)


# ====================================================================== #
# 维度归一化映射（兼容真实画像的中文取值 与 测试用的英文扁平键）
# ====================================================================== #
# attachment.type 真实取值：焦虑型 / 回避型 / 安全型 / 恐惧型
_ATTACHMENT_MAP: dict[str, str] = {
    "回避": "avoidant", "avoidant": "avoidant", "avoid": "avoidant",
    "焦虑": "anxious", "anxious": "anxious",
    "恐惧": "fearful", "fearful": "fearful", "fear": "fearful",
    "disorganized": "fearful", "混乱": "fearful",
    "安全": "secure", "secure": "secure",
}

# conflict_style.primary_key 真实取值：constructive/destructive/cold_war/emotional_outburst
# 归一到 Rahim/Gottman 的四类应对：avoiding/competing/compromising/collaborating
_CONFLICT_MAP: dict[str, str] = {
    "cold_war": "avoiding", "冷战": "avoiding", "逃避": "avoiding",
    "avoiding": "avoiding", "withdraw": "avoiding", "withdrawing": "avoiding",
    "退缩": "avoiding", "回避": "avoiding",
    "destructive": "competing", "攻击": "competing", "competing": "competing",
    "emotional_outburst": "competing", "爆发": "competing", "竞争": "competing",
    "compromising": "compromising", "妥协": "compromising", "让步": "compromising",
    "constructive": "collaborating", "合作": "collaborating",
    "collaborating": "collaborating", "建设": "collaborating", "双赢": "collaborating",
}

# love_languages.primary_key 真实取值
_LOVE_MAP: dict[str, str] = {
    "words_of_affirmation": "words_of_affirmation", "肯定言辞": "words_of_affirmation",
    "肯定": "words_of_affirmation", "言辞": "words_of_affirmation",
    "quality_time": "quality_time", "精心时刻": "quality_time", "精心": "quality_time",
    "时刻": "quality_time", "陪伴": "quality_time",
    "receiving_gifts": "receiving_gifts", "接受礼物": "receiving_gifts",
    "礼物": "receiving_gifts",
    "acts_of_service": "acts_of_service", "服务行动": "acts_of_service",
    "服务": "acts_of_service", "行动": "acts_of_service",
    "physical_touch": "physical_touch", "身体接触": "physical_touch",
    "身体": "physical_touch", "接触": "physical_touch",
}

# boundaries.personal_boundary 真实取值：清晰/中等/模糊
_BOUNDARY_MAP: dict[str, str] = {
    "清晰": "high", "高": "high", "high": "high",
    "模糊": "low", "低": "low", "low": "low",
    "中等": "medium", "中": "medium", "medium": "medium",
}

# self_differentiation.level 真实取值：自我分化高/中/低
_DIFF_MAP: dict[str, str] = {
    "高": "high", "high": "high",
    "低": "low", "low": "low",
    "中": "medium", "medium": "medium",
}

# 情绪稳定性（无独立字段，由 emotion_pattern.volatility / pattern 推断）
_EMO_STABILITY_MAP: dict[str, str] = {
    "高": "high", "high": "high", "stable": "high", "稳定": "high",
    "低": "low", "low": "low", "unstable": "low", "不稳定": "low",
    "神经质": "low", "neurotic": "low",
    "中": "medium", "medium": "medium",
}


def _norm(value: Any, mapping: dict[str, str], default: str) -> str:
    """把任意取值（中文/英文/嵌套dict里的字符串）归一到 mapping 的 value。

    采用"子串包含"匹配，兼容 "回避型"/"avoidant"/"cold_war" 等写法；
    命中不到则回退 default，保证画像缺字段时系统不崩。
    """
    if value is None:
        return default
    if isinstance(value, dict):
        # 传入的是整个维度 dict，尝试取常见子键
        for k in ("type", "primary", "primary_key", "level", "value", "pattern"):
            if k in value and value[k]:
                return _norm(value[k], mapping, default)
        return default
    s = str(value).strip().lower()
    if not s:
        return default
    # 先精确命中
    for k, v in mapping.items():
        if s == k.lower():
            return v
    # 再子串命中（中文 key 不需要 lower 也能包含）
    for k, v in mapping.items():
        if k.lower() in s:
            return v
    return default


# ====================================================================== #
# 策略匹配矩阵（核心；每条都标注学术来源）
# ====================================================================== #
class StrategyAdvisor:
    """策略适配矩阵：按对方人格维度交叉匹配推荐/禁忌技巧。

    用法：
        advisor = StrategyAdvisor(db)
        result = advisor.analyze(persona_dict, relation_stage=4)
        # result -> {recommended, forbidden, reasoning, style_tips, conflict_approach}
    """

    # ---- 维度 → 匹配表 -------------------------------------------------- #
    # 依恋类型（依恋理论 Bowlby/Ainsworth）
    ATTACHMENT_MATRIX: dict[str, dict[str, Any]] = {
        "avoidant": {
            "recommended": ["给对方空间", "低压沟通", "间接表达关心", "行动胜于言语"],
            "forbidden": ["逼问", "冷战惩罚", "情感施压", "过度热情"],
            "reason": "回避型面对压力会退缩，需要安全感但不喜欢被情感淹没"
                      "（依恋理论 Bowlby/Ainsworth）",
        },
        "anxious": {
            "recommended": ["及时回应", "明确表达爱意", "持续确认", "稳定可预测"],
            "forbidden": ["忽冷忽热", "玩消失", "欲擒故纵", "冷战"],
            "reason": "焦虑型需要安全感，冷淡会加剧焦虑和追问"
                      "（依恋理论 Bowlby/Ainsworth）",
        },
        "secure": {
            "recommended": ["自然互动", "真诚沟通", "共同成长"],
            "forbidden": ["过度操控", "套路", "忽冷忽热"],
            "reason": "安全型能识别套路，真诚最有效（依恋理论）",
        },
        "fearful": {
            "recommended": ["温和稳定", "逐步建立信任", "清晰沟通边界"],
            "forbidden": ["突然亲密", "情感爆发", "逼迫承诺"],
            "reason": "恐惧型既渴望亲密又害怕受伤，需要稳定节奏"
                      "（依恋理论 fearful/disorganized）",
        },
    }

    # 冲突处理风格（Gottman / Rahim 应对模式）
    CONFLICT_MATRIX: dict[str, dict[str, Any]] = {
        "avoiding": {
            "recommended": ["创造安全氛围", "非暴力沟通", "延后讨论", "用文字代替当面"],
            "forbidden": ["当场对峙", "逼表态", "翻旧账", "情绪对抗"],
            "reason": "逃避型面对冲突会 shutdown，需要降低威胁感（Gottman 修复尝试）",
        },
        "competing": {
            "recommended": ["冷静拆解", "就事论事", "设定讨论时间", "理性锚点"],
            "forbidden": ["情绪化对抗", "翻旧账", "人身攻击", "硬碰硬"],
            "reason": "攻击型需要理性锚点，情绪对抗会升级（Gottman 四骑士）",
        },
        "compromising": {
            "recommended": ["寻找中间地带", "明确双方需求", "公平交换"],
            "forbidden": ["单方面让步", "回避核心问题"],
            "reason": "妥协型重视公平，需要把双方需求摆上台面（Rahim 妥协模式）",
        },
        "collaborating": {
            "recommended": ["深度对话", "共同解决", "双赢思维"],
            "forbidden": ["零和博弈", "单方面决定"],
            "reason": "合作型欢迎共同解题，零和思维会破坏协作意愿（Rahim 合作模式）",
        },
    }

    # 爱的五种语言（Gary Chapman）
    LOVE_MATRIX: dict[str, dict[str, Any]] = {
        "words_of_affirmation": {
            "recommended": ["口头赞美", "写情话", "公开认可"],
            "forbidden": ["贬低", "沉默", "忽视成就"],
            "reason": "对方通过「被肯定」接收爱，言辞是她的爱语（Chapman 肯定言辞）",
        },
        "quality_time": {
            "recommended": ["专注陪伴", "共同活动", "放下手机"],
            "forbidden": ["分心", "敷衍", "总是迟到"],
            "reason": "对方通过「高质量共处」接收爱，分心等于没陪（Chapman 精心时刻）",
        },
        "receiving_gifts": {
            "recommended": ["用心礼物", "纪念日惊喜", "小信物"],
            "forbidden": ["忘记纪念日", "敷衍礼物"],
            "reason": "对方通过「心意的实物象征」接收爱（Chapman 接受礼物）",
        },
        "acts_of_service": {
            "recommended": ["帮忙做事", "分担责任", "行动表达"],
            "forbidden": ["只说不做", "把付出当理所当然"],
            "reason": "对方通过「替我做事」接收爱，行动比情话管用（Chapman 服务行动）",
        },
        "physical_touch": {
            "recommended": ["拥抱", "牵手", "肢体安抚"],
            "forbidden": ["身体疏远", "拒绝亲密"],
            "reason": "对方通过身体接触接收爱，肢体安抚是主通道（Chapman 身体接触）",
        },
    }

    # 边界感（Bowen 自我分化的人际表现）
    BOUNDARY_MATRIX: dict[str, dict[str, Any]] = {
        "high": {
            "recommended": ["尊重边界", "循序渐进", "明确询问同意"],
            "forbidden": ["过度侵入", "查岗", "突然造访", "未经同意翻看手机"],
            "reason": "高边界者需要被尊重自主，侵入会触发防御",
        },
        "low": {
            "recommended": ["适度亲密", "主动融入", "自然互动"],
            "forbidden": ["过度疏远", "过于客气"],
            "reason": "低边界者习惯亲密融合，过于客气反而显生分",
        },
        "medium": {
            "recommended": ["自然互动", "适度亲密"],
            "forbidden": ["过度侵入", "过度疏远"],
            "reason": "中等边界，保持自然节奏即可",
        },
    }

    # 自我分化（Bowen 家庭系统理论）
    DIFFERENTIATION_MATRIX: dict[str, dict[str, Any]] = {
        "low": {
            "recommended": ["温和坚定", "不被情绪带走", "保持自我", "设立健康边界"],
            "forbidden": ["硬碰硬", "冷战", "情绪融合", "过度牺牲"],
            "reason": "低分化者容易情绪融合，需要对方保持分化（Bowen 自我分化理论）",
        },
        "high": {
            "recommended": ["平等对话", "理性讨论", "各自保持独立"],
            "forbidden": ["过度操控", "情感绑架"],
            "reason": "高分化者尊重独立，操控与绑架会引发反感（Bowen）",
        },
        "medium": {
            "recommended": ["平等沟通", "共情与理性并重"],
            "forbidden": ["情绪融合", "过度操控"],
            "reason": "中等分化，共情与理性兼顾即可",
        },
    }

    # 情绪稳定性（大五神经质维度；低稳定=高神经质）
    EMOTION_STABILITY_MATRIX: dict[str, dict[str, Any]] = {
        "low": {
            "recommended": ["情绪安抚", "稳定陪伴", "避免刺激", "规律节奏"],
            "forbidden": ["突然变化", "激烈争吵", "情绪化表达", "制造惊喜惊吓"],
            "reason": "高神经质者对刺激敏感，需要可预测的稳定节奏（大五人格）",
        },
        "high": {
            "recommended": ["自然互动", "真诚沟通"],
            "forbidden": ["过度戏剧化"],
            "reason": "高稳定者情绪耐受力强，正常真诚沟通即可（大五人格）",
        },
        "medium": {
            "recommended": ["稳定节奏", "适度共情"],
            "forbidden": ["突然刺激"],
            "reason": "中等稳定性，保持可预期节奏、适度共情即可",
        },
    }

    # ================================================================== #
    # 攻略目的映射表：每个技巧 → 为什么这么做能提升好感度/推进关系
    # 核心原则：所有手段都是攻略手段，最终目的是让关系更好
    # ================================================================== #
    _TECHNIQUE_PURPOSES: dict[str, str] = {
        # 依恋类型相关
        "给对方空间": "理解对方在亲密压力下会退缩（多源于不安全感），给她舒适的距离但不切断连接，让她在安全中逐步建立信任，等她准备好时自然地主动靠近",
        "低压沟通": "降低对方防御心理，让沟通更顺畅，逐步推进亲密",
        "间接表达关心": "用不施压的方式传递在意，让对方感受到温暖而不窒息",
        "行动胜于言语": "用实际行动积累信任，比空口承诺更能提升好感",
        "及时回应": "稳定对方焦虑情绪，建立安全感，推进亲密",
        "明确表达爱意": "直接回应对方的情感需求，消除不确定感，提升好感",
        "持续确认": "反复给对方安全感，让关系在稳定中升温",
        "稳定可预测": "建立可靠感，让对方敢投入更多情感",
        "自然互动": "不刻意套路，真诚最能打动安全型，稳步提升好感",
        "真诚沟通": "建立深度信任，为关系打下坚实基础",
        "共同成长": "制造共同经历，拉近距离，推进关系到下一阶段",
        "温和稳定": "让恐惧型在安全氛围中逐步放下防备，慢慢靠近",
        "逐步建立信任": "一点点积累安全感，让对方敢于敞开心扉",
        "清晰沟通边界": "建立健康互动模式，让关系更可持续发展",
        # 精神操控/自恋倾向相关（基于画像中 pua_score / 暗黑三联征自动触发）
        "识别煤气灯效应": "识别对方扭曲事实的话术，守住自己的认知，不被带偏",
        "守住底线不被操控": "明确哪些互动方式让你受伤，温和而稳定地守住认知边界，引导对方建立更健康的相处方式",
        "保持自我价值感": "不接受贬低，你的价值不由对方评价，在被贬低时稳住自己的认知",
        "不被冷热交替操控": "理解忽冷忽热多源于对方的防御机制或不安全感，保持你自己稳定的节奏，不被情绪带走，引导对方学会稳定表达",
        "记录对方操控模式": "记下贬低/煤气灯/冷热交替的具体规律，理解这些行为背后的心理根源，用事实而非情绪应对",
        "尊重对方改变的节奏": "理解对方的模式源于其成长经历，改变需要对方自愿；在守住边界的前提下引导更健康的互动，而不是强行用付出去治愈对方",
        # 冲突风格相关
        "创造安全氛围": "降低冲突威胁感，让对方愿意沟通，修复关系裂痕",
        "非暴力沟通": "避免激化矛盾，让双方在平和中解决问题，修复后关系更紧密",
        "延后讨论": "先冷却情绪再沟通，避免冲动说错话，为修复创造条件",
        "用文字代替当面": "给逃避型缓冲空间，降低面对面压力，让沟通更容易推进",
        "冷静拆解": "用理性引导对方从情绪中抽离，找到问题核心，共同解决",
        "就事论事": "避免人身攻击，聚焦问题本身，让对方愿意配合解决",
        "设定讨论时间": "控制冲突节奏，避免无限争吵，高效解决问题后关系更稳固",
        "理性锚点": "给强势型一个台阶下，让讨论回到理性轨道，推进问题解决",
        "寻找中间地带": "满足双方核心需求，让妥协成为双赢，关系更平衡",
        "明确双方需求": "把诉求摆上台面，避免互相猜，减少未来冲突",
        "公平交换": "建立互惠模式，让对方感到被尊重，关系更健康",
        "深度对话": "拉近距离，让合作型感受到诚意，关系更深一层",
        "共同解决": "制造并肩作战的感觉，增强默契和亲密感",
        "双赢思维": "让对方感受到你不是在争输赢，而是在为双方好，提升信任",
        # 爱的语言相关
        "口头赞美": "用言辞肯定对方，满足被认可的需求，提升好感",
        "写情话": "用文字传递爱意，加深情感连接",
        "公开认可": "让对方感受到被重视，满足其被肯定的需求",
        "专注陪伴": "放下手机全身心陪伴，让对方感受到爱，提升亲密感",
        "共同活动": "制造高质量共处时光，拉近距离，推进关系",
        "放下手机": "表达全身心投入，让对方感受到被重视",
        "用心礼物": "用实物传递心意，让爱可视化，提升好感",
        "纪念日惊喜": "制造仪式感，让对方感受到被放在心上",
        "小信物": "用小物件承载思念，随时想起你，保持情感连接",
        "帮忙做事": "用行动表达爱意，满足服务型爱语的需求",
        "分担责任": "减轻对方负担，让对方感到被支持，提升亲密",
        "行动表达": "用实际行动证明在意，比言语更有说服力",
        "拥抱": "用身体接触传递温暖，满足触觉型爱语需求，快速拉近距离",
        "牵手": "公开表达亲密，让关系更明确，提升归属感",
        "肢体安抚": "用触摸平复情绪，建立身体层面的安全感和亲密感",
        # 边界感相关
        "尊重边界": "让对方感到被尊重，反而更愿意靠近你",
        "循序渐进": "节奏稳扎稳打，避免吓跑对方，稳步推进关系",
        "明确询问同意": "建立尊重感，让对方感到安全，愿意放开自己",
        "适度亲密": "根据对方边界调整亲密度，让关系自然升温",
        "主动融入": "拉近心理距离，让低边界者感到被接纳",
        "自然互动": "保持舒服的距离感，关系在轻松中推进",
        # 自我分化相关
        "温和坚定": "不被对方情绪带走，同时保持善意，让对方学会尊重你的底线",
        "不被情绪带走": "保持情绪稳定，避免情绪化争吵，修复后关系更健康",
        "保持自我": "不因为迎合而失去自己，反而更有吸引力，关系更平衡",
        "设立健康边界": "设定底线+引导对方尊重底线，让关系更可持续",
        "平等对话": "尊重对方独立性，让高分化者感到被平等对待",
        "理性讨论": "用逻辑沟通避免情绪内耗，高效解决问题",
        "各自保持独立": "给彼此空间又不失连接，关系在平衡中发展",
        "平等沟通": "互相尊重，关系更平等健康",
        "共情与理性并重": "兼顾情绪和道理，让沟通既有温度又有效率",
        # 情绪稳定性相关
        "情绪安抚": "先处理情绪再处理问题，让对方感到被理解，关系更紧密",
        "稳定陪伴": "在对方情绪波动时不离不弃，建立深层信任",
        "避免刺激": "不给情绪不稳的对方制造额外压力，关系在平稳中推进",
        "规律节奏": "保持可预期的互动，让对方有安全感，稳步升温",
        # 可信度/反伪装相关
        "留意兑现细节": "通过观察小承诺的兑现情况，逐步建立信任维度",
        "小额试探": "通过小承诺建立信任闭环，引导对方养成兑现习惯",
        "观察实际兑现": "用行为验证判断对方真实意图，为后续推进策略提供依据",
        "保持观察": "通过持续观察判断对方真实自我，为精准推进策略提供依据",
        "不急于投入": "减少过度投入+回到平衡，减少压力让关系回到平衡，反而更容易推进",
        "交叉验证": "通过多场景验证判断对方真实意图，避免误判后走弯路",
        "在不投入资源前提下观察2周": "用低成本观察窗口收集真实行为数据，为制定精准推进策略提供依据",
        "观察验证": "通过无利益场景的真实表现判断对方真实自我，据此调整推进策略",
        "引导真实展现": "创造安全氛围让对方放下伪装，展现真实自我，再决定如何推进关系",
        "设定清晰底线": "设定底线+引导对方尊重底线，底线是为了关系健康，不是为了疏远",
        "通过小承诺建立信任闭环": "引导对方养成兑现习惯，逐步提升信任维度",
        "小范围验证": "用低成本方式测试对方反应，根据结果调整推进节奏",
        "低压关心": "用不施压的方式传递关心，让对方感受到温暖而不窒息",
        # 投入平衡 / 双向奔赴相关
        "请对方帮你一个小忙": "创造低压力的机会让对方自然地表达关心（如帮个小忙），让对方在付出中感到被需要，从而自然地增加双向投入",
        "引导对方主动发起一次约会": "自然表达你有空的时间窗口，把邀约的选择权交给对方，让对方在舒适节奏中主动发起一次约会，逐步形成双向奔赴",
        "适度保持自己的节奏，不过度投入": "把节奏调整到双方都舒适的位置，专注你自己的生活而不是过度紧绷地追逐，让对方在舒适距离中重新感知你的好，关系更自然地靠近",
        "减少主动联系频率，观察对方是否会主动找你": "把主动联系调整到双方都舒适的节奏，观察对方在低压下的自然回应，理解对方的投入意愿，据此把关系调整到双向平衡",
        "维持双向奔赴的健康节奏": "保持当前平衡的投入节奏，让关系在互相滋养中稳步升温",
        "适当接受对方的好意，但观察是否有后续索取目的": "自然接受对方的好意，同时温和地了解对方付出背后的真实需求，引导关系回到双向奔赴",
        "回报对方的投入，但保持平衡": "对等回报让关系更健康，不欠人情也不超额回报",
        "如果对方投入后有索取，评估是否合理，不合理则温和拒绝": "守住投入平衡的底线，避免单方面接受好处后被索取绑定",
        "观察索取目的+引导双向奔赴": "看清对方付出的真实动机，把关系引导回互相投资的健康轨道",
    }

    # 禁忌原因映射表：每个禁忌技巧 → 用了会导致什么负面效果
    _FORBIDDEN_REASONS: dict[str, str] = {
        # 依恋类型相关
        "逼问": "回避型面对压力会退缩，导致好感度下降",
        "冷战惩罚": "焦虑型需要安全感，冷战会加剧焦虑和追问，关系倒退",
        "情感施压": "给对方造成心理负担，反而想逃，关系降温",
        "过度热情": "回避型会被浓烈情感吓到，反而疏远，好感下降",
        "忽冷忽热": "焦虑型需要安全感，冷淡会加剧焦虑和追问，关系倒退",
        "玩消失": "会触发焦虑型的被抛弃恐惧，导致信任崩塌",
        "欲擒故纵": "安全型能识别套路，真诚最有效，套路会让对方反感",
        "突然亲密": "恐惧型会被突然的亲密吓到，反而后退，关系倒退",
        "情感爆发": "恐惧型承受不住强烈情绪，会关闭心门",
        "逼迫承诺": "恐惧型害怕被绑定，逼承诺会让对方想逃",
        "过度操控": "任何类型都反感被操控，会触发抵触情绪",
        "套路": "安全型能识别套路，真诚最有效，套路会让对方失去信任",
        # 冲突风格相关
        "当场对峙": "逃避型面对当场对峙会 shutdown，沟通彻底失败",
        "逼表态": "给逃避型太大压力，反而更回避，问题更难解决",
        "翻旧账": "让对方感到被攻击，关闭沟通意愿，冲突升级",
        "情绪对抗": "攻击型会被激怒，冲突升级，两败俱伤",
        "情绪化对抗": "强势型好辩，情绪化会被抓住把柄，争论升级",
        "人身攻击": "严重伤害对方自尊，信任崩塌，关系难以修复",
        "硬碰硬": "强势型不服软，硬碰硬只会两败俱伤",
        "单方面让步": "妥协型重视公平，单方面让步会累积不满",
        "回避核心问题": "问题不解决会反复爆发，关系持续内耗",
        "零和博弈": "合作型欢迎共同解题，零和思维会破坏协作意愿",
        "单方面决定": "不尊重对方意见，会引发不满和抵触",
        # 爱的语言相关
        "贬低": "严重伤害对方自尊，直接打击好感",
        "沉默": "对肯定言辞型等于冷暴力，关系迅速降温",
        "忽视成就": "让对方感到不被重视，好感下降",
        "分心": "对精心时刻型等于没陪，会感到被敷衍",
        "敷衍": "礼物型看重心意，敷衍礼物比不送还伤人",
        "总是迟到": "不尊重对方时间，让对方感到被忽视",
        "忘记纪念日": "破坏仪式感，让对方感到不被放在心上",
        "敷衍礼物": "心意不足的礼物反而会起反效果",
        "只说不做": "服务型看重行动，只说不做等于没表达爱",
        "把付出当理所当然": "消耗对方的善意，好感持续下降",
        "身体疏远": "身体接触型需要亲密，疏远会让对方感到被拒绝",
        "拒绝亲密": "直接打击身体接触型的核心需求",
        # 边界感相关
        "过度侵入": "高边界者需要自主，侵入会触发防御和疏远",
        "查岗": "严重侵犯边界，触发不信任感",
        "突然造访": "突然造访会打破对方的边界感，让对方感到被冒犯，信任受损",
        "未经同意翻看手机": "严重侵犯隐私，信任崩塌",
        "过度疏远": "低边界者习惯亲密融合，过于客气反而显生分",
        "过于客气": "太见外会让对方感到被推开",
        # 自我分化相关
        "硬碰硬": "低分化者情绪容易融合，硬碰硬会让双方都失控",
        "情绪融合": "你也被情绪带走，就没人能稳住局面了",
        "过度牺牲": "牺牲式付出会累积怨气，最终爆发伤害关系",
        "过度操控": "高分化者尊重独立，操控与绑架会引发强烈反感",
        "情感绑架": "用道德绑架对方，会引发反感和逃离",
        # 情绪稳定性相关
        "突然变化": "高神经质者对突变敏感，会引发焦虑和不安",
        "激烈争吵": "情绪不稳的对方承受不住，会崩溃或逃避",
        "情绪化表达": "给低稳定性的对方造成更大情绪波动",
        "制造惊喜惊吓": "惊吓对高神经质者是负面刺激，反而引发焦虑",
        "过度戏剧化": "高稳定者觉得没必要，反而觉得你小题大做",
        "突然刺激": "中等稳定性者也承受不住突然的强烈刺激",
        # 可信度/反伪装相关
        "大额投入": "在信任未建立前大额投入，一旦判断失误损失过大",
        "轻易托付": "过早全盘托付会让关系失衡，也不利于你看清对方真实一面；信任应通过兑现一点点积累",
        "全盘信任": "盲目信任会让你失去观察和理解对方的机会，也不利于建立健康的双向关系",
        "快速推进关系": "信任未建立前快速推进，容易走弯路",
        "情感托付": "在对方真实自我未验证前情感托付，风险过高",
        "借钱或担保": "金钱是最大利益考验，过早涉及金钱风险极高",
        "无条件妥协": "无条件妥协会让你的需求长期被忽略，积累不满，关系失衡",
        "快速定终身": "在充分了解前就承诺终身，容易后悔",
        "轻信口头承诺": "不验证承诺就当真，容易被画饼误导",
        # 投入平衡 / 双向奔赴相关
        "继续单方面超额付出": "单方面超额付出会让关系失去平衡，你的真实需求长期被忽略，互动难以持续健康",
        "无底线讨好": "无底线讨好会让你的需求被长期忽略，互动失去平等，关系难以健康持续",
        "单方面接受大额好处": "单方面接受大额好处容易让关系滑向利益交换，失去情感层面的平衡",
        # 精神操控/自恋倾向相关禁忌
        "和对方争对错讲道理": "在对方情绪性贬低时争对错，只会让冲突升级、让对方更防御，问题无法真正解决；先稳住自己的认知，等双方平静后再沟通",
        "在被贬低时自我怀疑": "贬低往往是对方自身不安全感的投射，不是对你的真实评价；自我怀疑会动摇你的认知边界，关系更难修复",
        "用热情感动对方": "过度付出与讨好无法换来真正的改变，反而会让互动失衡，双方都更难建立健康关系",
        "用冷战反制": "冷战无论对谁都是伤害沟通的方式，会让对方感到被抛弃，关系裂痕加深",
        "为对方的承诺反复找证据": "反复替对方圆谎会模糊真实问题，让信任无法建立；温和地面对承诺兑现情况，引导对方养成兑现习惯",
    }

    def __init__(self, db: "Database" = None) -> None:
        """初始化策略顾问。

        Args:
            db: Database 实例（execute/fetch_one/fetch_all）；可选。
                传入后自动建 strategy_history 表并启用持久化与因时制宜检测。
        """
        self.db = db
        self._ensure_table()
        logger.info("StrategyAdvisor 初始化完成 db=%s", "on" if db else "off")

    # ------------------------------------------------------------------ #
    # 持久化：建表
    # ------------------------------------------------------------------ #
    def _ensure_table(self) -> None:
        """若 db 可用，创建 strategy_history 表（幂等）。"""
        if self.db is None:
            return
        try:
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    attachment_type TEXT,
                    conflict_style TEXT,
                    recommended_strategies_json TEXT,
                    forbidden_strategies_json TEXT,
                    reasoning TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                )
                """
            )
        except Exception as e:  # noqa: BLE001 - 建表失败不影响纯计算
            logger.warning("strategy_history 建表失败: %s", e)

    # ------------------------------------------------------------------ #
    # 维度提取与归一化（防御性，缺字段走默认值）
    # ------------------------------------------------------------------ #
    def _extract_dimensions(self, persona: dict) -> dict:
        """从 21 维画像中提取六个策略相关维度，归一化到内部键。

        同时兼容两种画像形态：
          1) 真实 PersonaAnalyzer 输出（嵌套 dict，中文取值）；
          2) 扁平测试画像（英文键，如 attachment="avoidant"）。

        Returns:
            dict: {
                attachment, conflict, love, boundary, differentiation,
                emotion_stability,
                raw: {各维度原始可读标签，用于生成 reasoning}
            }
        """
        persona = persona or {}

        # 依恋：可能是 str("回避型") 或 dict({"type":"回避型"})
        # v2 优先：若画像含三维评分（avoidance_score/anxiety_score），由分数直接判定主类型
        attach_raw = persona.get("attachment")
        three_dim_bucket = self._attachment_bucket_from_scores(attach_raw)
        if three_dim_bucket is not None:
            attachment = three_dim_bucket
        else:
            attachment = _norm(attach_raw, _ATTACHMENT_MAP, "secure")

        # 冲突风格：conflict_style
        conflict_raw = persona.get("conflict_style")
        conflict = _norm(conflict_raw, _CONFLICT_MAP, "collaborating")

        # 爱的语言：love_languages（真实）或 love_language（扁平测试）
        love_raw = persona.get("love_languages") or persona.get("love_language")
        love = _norm(love_raw, _LOVE_MAP, "words_of_affirmation")

        # 边界：boundaries（真实）或 boundary（扁平测试）
        boundary_raw = persona.get("boundaries") or persona.get("boundary")
        if isinstance(boundary_raw, dict) and "personal_boundary" in boundary_raw:
            boundary_raw = boundary_raw.get("personal_boundary")
        boundary = _norm(boundary_raw, _BOUNDARY_MAP, "medium")

        # 自我分化：self_differentiation（真实）或 differentiation（扁平测试）
        diff_raw = persona.get("self_differentiation") or persona.get("differentiation")
        differentiation = _norm(diff_raw, _DIFF_MAP, "medium")

        # 情绪稳定性：emotional_stability（扁平）或 从 emotion_pattern 推断
        es_raw = persona.get("emotional_stability")
        if es_raw is None:
            emo_pat = persona.get("emotion_pattern")
            if isinstance(emo_pat, dict):
                # volatility="高" → 稳定性低；消极倾向 → 稳定性偏低
                vol = str(emo_pat.get("volatility", ""))
                pat = str(emo_pat.get("pattern", ""))
                if vol == "高" or pat == "消极倾向":
                    es_raw = "low"
                elif vol == "低":
                    es_raw = "high"
                else:
                    es_raw = "medium"
        emotion_stability = _norm(es_raw, _EMO_STABILITY_MAP, "high")

        # 生成原始可读标签（用于 reasoning）
        def _label(raw: Any, keys: tuple[str, ...]) -> str:
            if isinstance(raw, dict):
                for k in keys:
                    if raw.get(k):
                        return str(raw[k])
            return str(raw) if raw not in (None, "") else "未标注"

        raw = {
            "attachment": _label(attach_raw, ("primary_type", "type")),
            "conflict": _label(conflict_raw, ("primary", "primary_key")),
            "love": _label(love_raw, ("primary", "primary_key")),
            "boundary": _label(boundary_raw, ("personal_boundary", "level")),
            "differentiation": _label(diff_raw, ("level",)),
            "emotion_stability": _label(es_raw, ()),
        }

        # v2：可信度维度（trustworthiness）——缺省为 None 表示画像未提供，不影响旧逻辑
        trust_level = None
        tw_raw = persona.get("trustworthiness")
        if isinstance(tw_raw, dict) and tw_raw:
            tv = tw_raw.get("level")
            if tv in ("high", "medium", "low", "critical"):
                trust_level = tv
            else:
                try:
                    s = float(tw_raw.get("overall_trust_score", 50))
                    trust_level = ("high" if s >= 80 else "medium" if s >= 60
                                   else "low" if s >= 40 else "critical")
                except (TypeError, ValueError):
                    trust_level = None

        # v2：行为验证（自述 vs 实证）是否不一致
        bv_raw = persona.get("behavior_verification")
        bv_inconsistent = bool(isinstance(bv_raw, dict) and bv_raw.get("consistent") is False)

        return {
            "attachment": attachment,
            "conflict": conflict,
            "love": love,
            "boundary": boundary,
            "differentiation": differentiation,
            "emotion_stability": emotion_stability,
            "trust_level": trust_level,
            "behavior_inconsistent": bv_inconsistent,
            "raw": raw,
        }

    @staticmethod
    def _attachment_bucket_from_scores(attach_raw: Any) -> Optional[str]:
        """若画像含三维依恋评分，由分数判定策略矩阵桶（avoidant/anxious/secure/fearful）。

        与 PersonaAnalyzer._analyze_attachment 的混合型识别保持一致：
          - 高回避(>=60)+低焦虑(<40) -> avoidant
          - 低回避(<40)+高焦虑(>=60) -> anxious
          - 双高(>=60)               -> fearful
          - 双低(<40)                -> secure
          - 其他(混合型)              -> 按倾向更强的轴归类，双中高则归 fearful
        画像未提供三维字段时返回 None（走旧的字符串归一化逻辑）。
        """
        if not isinstance(attach_raw, dict):
            return None
        if "avoidance_score" not in attach_raw and "anxiety_score" not in attach_raw:
            return None
        try:
            a = float(attach_raw.get("avoidance_score", 50))
            x = float(attach_raw.get("anxiety_score", 50))
        except (TypeError, ValueError):
            return None
        if a >= 60 and x < 40:
            return "avoidant"
        if x >= 60 and a < 40:
            return "anxious"
        if a >= 60 and x >= 60:
            return "fearful"
        if a < 40 and x < 40:
            return "secure"
        # 混合型：双中高(>=55) 归 fearful，否则取更强轴
        if a >= 55 and x >= 55:
            return "fearful"
        return "avoidant" if a >= x else "anxious"

    # ------------------------------------------------------------------ #
    # 关系阶段修饰（因时制宜）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _stage_guidance(relation_stage: int) -> str:
        """根据关系阶段（0-10）返回节奏说明。

        阶段划分（与 TaskEngine.STAGE_GOALS 对齐）：
          0-2 初识期：保守，先建舒适感；
          3-5 暧昧/矛盾期：可适度推进，矛盾期优先修复；
          6-7 热恋期：可更亲密；
          8-10 稳定期：侧重维护与仪式感。
        """
        if relation_stage <= 2:
            return "当前为初识期（stage≤2），推荐策略偏保守，以建立舒适感与信任为主，避免过度推进或暴露需求感。"
        if relation_stage <= 5:
            if relation_stage in (3, 4):
                return "当前为暧昧/修复期（stage 3-5），可适度推进；若刚经历矛盾，优先修复关系、重建安全感，再谈升温。"
            return "当前为暧昧升温期（stage 5），可适度制造独处与推进机会。"
        if relation_stage <= 7:
            return "当前为热恋期（stage 6-7），可使用更亲密、更高浓度的技巧。"
        return "当前为稳定期（stage 8-10），侧重长期维护、仪式感与共同未来规划。"

    # ------------------------------------------------------------------ #
    # 核心：策略匹配
    # ------------------------------------------------------------------ #
    def analyze(self, persona: dict, relation_stage: int = 5) -> dict:
        """核心方法：根据对方画像 + 关系阶段，交叉匹配推荐/禁忌技巧。

        Args:
            persona: 21 维人物画像 dict（嵌套或扁平均可，缺字段走默认值）。
            relation_stage: 关系阶段 0-10（0 初识 … 10 终身伴侣）。

        Returns:
            dict: {
                "recommended": [技巧名...],     # 按多维共同推荐频率排序
                "forbidden":   [禁忌技巧...],   # 去重合并
                "reasoning":   str,             # 为什么推荐这些（基于对方什么特征）
                "style_tips":  str,             # 沟通风格建议
                "conflict_approach": str,      # 冲突处理建议
            }
        """
        dims = self._extract_dimensions(persona)

        # 逐维度取匹配表
        hits: list[dict[str, Any]] = [
            ("依恋类型", self.ATTACHMENT_MATRIX.get(dims["attachment"], {})),
            ("冲突风格", self.CONFLICT_MATRIX.get(dims["conflict"], {})),
            ("爱的语言", self.LOVE_MATRIX.get(dims["love"], {})),
            ("边界感", self.BOUNDARY_MATRIX.get(dims["boundary"], {})),
            ("自我分化", self.DIFFERENTIATION_MATRIX.get(dims["differentiation"], {})),
            ("情绪稳定性", self.EMOTION_STABILITY_MATRIX.get(dims["emotion_stability"], {})),
        ]

        # 合并推荐：按跨维度出现频率排序（被多个维度共同推荐的技巧排前面）
        rec_counter: Counter = Counter()
        reasons: list[str] = []
        for dim_name, matrix in hits:
            if not matrix:
                continue
            for tip in matrix.get("recommended", []):
                rec_counter[tip] += 1
            if matrix.get("reason"):
                reasons.append(f"{dim_name}：{matrix['reason']}")

        recommended = [tip for tip, _ in rec_counter.most_common()]

        # 给推荐技巧追加攻略目的说明（格式：技巧名（目的：xxx））
        recommended_annotated: list[str] = []
        for tip in recommended:
            purpose = self._TECHNIQUE_PURPOSES.get(tip)
            if purpose:
                recommended_annotated.append(f"{tip}（目的：{purpose}）")
            else:
                recommended_annotated.append(tip)

        # 合并禁忌：去重，并追加禁忌原因说明
        forbidden: list[str] = []
        forbidden_raw: list[str] = []  # 原始禁忌名（用于内部匹配）
        seen = set()
        for _, matrix in hits:
            for tip in matrix.get("forbidden", []):
                if tip not in seen:
                    seen.add(tip)
                    forbidden_raw.append(tip)
                    reason = self._FORBIDDEN_REASONS.get(tip)
                    if reason:
                        forbidden.append(f"{tip}（禁忌原因：{reason}）")
                    else:
                        forbidden.append(tip)

        # ---- v2：可信度维度影响策略（仅当画像提供 trustworthiness 时介入，保证向后兼容）----
        trust_recs, trust_forb, trust_note = self._trust_guidance(dims.get("trust_level"))
        for tip in trust_recs:
            if tip not in rec_counter and tip not in recommended:
                recommended.append(tip)
                purpose = self._TECHNIQUE_PURPOSES.get(tip)
                if purpose:
                    recommended_annotated.append(f"{tip}（目的：{purpose}）")
                else:
                    recommended_annotated.append(tip)
        for tip in trust_forb:
            if tip not in seen:
                seen.add(tip)
                forbidden_raw.append(tip)
                reason = self._FORBIDDEN_REASONS.get(tip)
                if reason:
                    forbidden.append(f"{tip}（禁忌原因：{reason}）")
                else:
                    forbidden.append(tip)

        # ---- 反伪装审计：根据 deception_risk 等级调整策略（无该字段则不介入）----
        dec_recs, dec_forb, dec_note = self._deception_guidance(persona)
        for tip in dec_recs:
            if tip not in rec_counter and tip not in recommended:
                recommended.append(tip)
                purpose = self._TECHNIQUE_PURPOSES.get(tip)
                if purpose:
                    recommended_annotated.append(f"{tip}（目的：{purpose}）")
                else:
                    recommended_annotated.append(tip)
        for tip in dec_forb:
            if tip not in seen:
                seen.add(tip)
                forbidden_raw.append(tip)
                reason = self._FORBIDDEN_REASONS.get(tip)
                if reason:
                    forbidden.append(f"{tip}（禁忌原因：{reason}）")
                else:
                    forbidden.append(tip)

        # ---- 精神操控/自恋倾向：NPD 防御策略置顶（基于 pua_score 自动触发）----
        mp_recs, mp_forb, mp_note = self._manipulation_guidance(persona)
        for tip in reversed(mp_recs):
            if tip not in rec_counter and tip not in recommended:
                recommended.insert(0, tip)
                purpose = self._TECHNIQUE_PURPOSES.get(tip)
                annotated = f"{tip}（目的：{purpose}）" if purpose else tip
                recommended_annotated.insert(0, annotated)
        for tip in mp_forb:
            if tip not in seen:
                seen.add(tip)
                forbidden_raw.append(tip)
                reason = self._FORBIDDEN_REASONS.get(tip)
                forbidden.append(f"{tip}（禁忌原因：{reason}）" if reason else tip)

        # ---- 人格倾向：基于画像特征自动识别 14 种人格倾向，叠加专属策略（v4 新增）----
        ps_recs, ps_forb, ps_note = self._apply_personality_strategy(persona)
        for tip in reversed(ps_recs):
            if tip not in rec_counter and tip not in recommended:
                recommended.insert(0, tip)
                # 人格策略为自明的沟通建议，无 _TECHNIQUE_PURPOSES 条目，原样插入
                recommended_annotated.insert(0, tip)
        for tip in ps_forb:
            if tip not in seen:
                seen.add(tip)
                forbidden_raw.append(tip)
                forbidden.append(tip)

        # ---- 投入平衡：引导双向奔赴（无该字段则不介入）----
        inv_recs, inv_forb, inv_note = self._investment_balance_guidance(persona)
        for tip in inv_recs:
            if tip not in rec_counter and tip not in recommended:
                recommended.append(tip)
                purpose = self._TECHNIQUE_PURPOSES.get(tip)
                if purpose:
                    recommended_annotated.append(f"{tip}（目的：{purpose}）")
                else:
                    recommended_annotated.append(tip)
        for tip in inv_forb:
            if tip not in seen:
                seen.add(tip)
                forbidden_raw.append(tip)
                reason = self._FORBIDDEN_REASONS.get(tip)
                if reason:
                    forbidden.append(f"{tip}（禁忌原因：{reason}）")
                else:
                    forbidden.append(tip)

        # ---- 无事献殷勤检测：高风险时观察索取目的+引导双向奔赴 ----
        uw_recs, uw_forb, uw_note = self._unwarranted_guidance(persona)
        for tip in uw_recs:
            if tip not in rec_counter and tip not in recommended:
                recommended.append(tip)
                purpose = self._TECHNIQUE_PURPOSES.get(tip)
                if purpose:
                    recommended_annotated.append(f"{tip}（目的：{purpose}）")
                else:
                    recommended_annotated.append(tip)
        for tip in uw_forb:
            if tip not in seen:
                seen.add(tip)
                forbidden_raw.append(tip)
                reason = self._FORBIDDEN_REASONS.get(tip)
                if reason:
                    forbidden.append(f"{tip}（禁忌原因：{reason}）")
                else:
                    forbidden.append(tip)

        # ---- 已读不回/不回复场景：按 5 种原因叠加专属策略（v5 新增）----
        nr_recs, nr_forb, nr_note = self._apply_no_reply_strategy(persona)
        for tip in nr_recs:
            if tip not in rec_counter and tip not in recommended:
                recommended.append(tip)
                purpose = self._TECHNIQUE_PURPOSES.get(tip)
                if purpose:
                    recommended_annotated.append(f"{tip}（目的：{purpose}）")
                else:
                    recommended_annotated.append(tip)
        for tip in nr_forb:
            if tip not in seen:
                seen.add(tip)
                forbidden_raw.append(tip)
                forbidden.append(tip)

        # ---- 心理学定律：按画像自动匹配定律，引用进 reasoning / 推荐技巧 ----
        law_recs, law_forb, law_note = self._apply_psychology_laws(persona, dims, relation_stage)
        for tip in law_recs:
            if tip not in rec_counter and tip not in recommended:
                recommended.append(tip)
                purpose = psychology_laws.LAW_TIP_PURPOSES.get(tip)
                if purpose:
                    recommended_annotated.append(f"{tip}（目的：{purpose}）")
                else:
                    recommended_annotated.append(tip)
        for tip in law_forb:
            if tip not in seen:
                seen.add(tip)
                forbidden_raw.append(tip)
                warning = psychology_laws.LAW_MISUSE_WARNINGS.get(tip)
                if warning:
                    forbidden.append(f"{tip}（定律误用警告：{warning}）")
                else:
                    forbidden.append(tip)

        # 沟通风格建议（style_tips）：由依恋+情绪稳定性决定措辞
        style_tips = self._build_style_tips(dims)
        # 冲突处理建议：由冲突风格+自我分化决定
        conflict_approach = self._build_conflict_approach(dims)

        # reasoning：说明基于对方的什么特征推荐了什么
        raw = dims["raw"]
        feature_line = (
            f"对方画像：依恋={raw['attachment']}、冲突风格={raw['conflict']}、"
            f"主要爱语={raw['love']}、边界感={raw['boundary']}、"
            f"自我分化={raw['differentiation']}、情绪稳定性={raw['emotion_stability']}。"
        )
        rec_line = "推荐技巧：" + ("、".join(recommended_annotated) if recommended_annotated else "无")
        forb_line = "禁忌技巧：" + ("、".join(forbidden) if forbidden else "无")
        stage_line = self._stage_guidance(relation_stage)
        reason_line = "；".join(reasons)
        reasoning = f"{feature_line}\n{rec_line}。{forb_line}。\n{stage_line}\n依据：{reason_line}。"

        # v2：可信度对策略的影响
        if trust_note:
            reasoning += f"\n可信度影响：{trust_note}"
        # v2：行为验证提醒（自述与行为不一致时，引导真实展现+验证推进）
        if dims.get("behavior_inconsistent"):
            reasoning += ("\n行为验证提醒：对方自述与行为存在不一致，"
                          "建议以行为实证为参考调整推进节奏，同时创造安全氛围引导对方展现真实自我，"
                          "根据验证结果制定下一步推进策略。")

        # 反伪装审计提醒（仅当画像提供 deception_risk 时介入）
        if dec_note:
            reasoning += f"\n行为一致性审计：{dec_note}"

        # 精神操控审计提醒（仅当 pua_score 达标时介入）
        if mp_note:
            reasoning += f"\n{mp_note}"

        # 人格倾向审计提醒（仅当识别到主导人格倾向时介入）
        if ps_note:
            reasoning += f"\n{ps_note}"

        # 投入平衡提醒（仅当画像提供 investment_balance 时介入）
        if inv_note:
            reasoning += f"\n{inv_note}"

        # 无事献殷勤提醒（仅当检测到高风险时介入）
        if uw_note:
            reasoning += f"\n{uw_note}"

        # 不回复场景提醒（仅当检测到不回复场景时介入）
        if nr_note:
            reasoning += f"\n{nr_note}"

        # 心理学定律引用（命中定律时追加）
        if law_note:
            reasoning += f"\n{law_note}"

        # ---- 数据飞轮（P0）：为这一轮策略推荐生成稳定 judgment_id ----
        # 输出侧打标：UI / CLI 据此收集用户“对/错/存疑”反馈。
        # 惰性导入 + 容错：feedback_service 异常绝不能拖垮策略计算本身。
        judgment_type = "strategy"
        try:
            from services.feedback_service import FeedbackService
            judgment_id = FeedbackService.generate_judgment_id(judgment_type)
        except Exception as e:  # noqa: BLE001
            logger.debug("生成 judgment_id 失败（忽略）: %s", e)
            judgment_id = ""

        return {
            "recommended": recommended_annotated,
            "recommended_raw": recommended,  # 原始无注释版本，供内部匹配使用
            "forbidden": forbidden,
            "forbidden_raw": forbidden_raw,  # 原始无注释版本，供内部匹配使用
            "reasoning": reasoning,
            "style_tips": style_tips,
            "conflict_approach": conflict_approach,
            "trust_level": dims.get("trust_level"),
            "behavior_inconsistent": dims.get("behavior_inconsistent", False),
            "judgment_type": judgment_type,   # 本轮判断/推荐类型（数据飞轮）
            "judgment_id": judgment_id,        # 本轮推荐的稳定标识（数据飞轮）
            "_dims": dims,  # 供 record/detect 内部使用
        }

    # ------------------------------------------------------------------ #
    # 风格 / 冲突建议生成
    # ------------------------------------------------------------------ #
    def _build_style_tips(self, dims: dict) -> str:
        """根据依恋类型 + 情绪稳定性生成沟通风格建议。"""
        att = dims["attachment"]
        es = dims["emotion_stability"]
        tips: list[str] = []
        if att == "avoidant":
            tips.append("对回避型：措辞间接、低压，给足空间，别连环追问，用行动而非浓烈情话表达关心")
        elif att == "anxious":
            tips.append("对焦虑型：回应要及时、态度要明确，多用肯定句和确定性承诺，别玩消失或忽冷忽热")
        elif att == "fearful":
            tips.append("对恐惧型：语气温和稳定、节奏放慢，避免突然升温或逼对方表态")
        else:
            tips.append("对安全型：自然真诚即可，不必刻意套路")
        if es == "low":
            tips.append("对方情绪稳定性偏低：先安抚情绪再讲道理，避免突然变化和激烈措辞")
        elif es == "high":
            tips.append("对方情绪稳定：可以直接理性沟通")
        return "；".join(tips)

    def _build_conflict_approach(self, dims: dict) -> str:
        """根据冲突风格 + 自我分化生成冲突处理建议。

        攻略导向：冲突不是输赢，而是暴露问题→修复→关系更紧密的机会。
        """
        cf = dims["conflict"]
        df = dims["differentiation"]
        tips: list[str] = []
        if cf == "avoiding":
            tips.append("对方逃避冲突：别当场逼表态，先降温、用文字铺垫，选双方都放松时再聊——目标是修复裂痕，让关系比争吵前更紧密")
        elif cf == "competing":
            tips.append("对方强势好辩：就事论事、给理性锚点，别被带情绪，避免硬碰硬和人身攻击——冲突修复后关系更紧密、信任更稳固")
        elif cf == "compromising":
            tips.append("对方愿意妥协：明确各自底线后找中间地带，别让单方面一直让步——通过冲突找到双方都舒服的相处模式，关系更健康紧密")
        else:
            tips.append("对方愿意合作：拉齐目标、共同找双赢方案——把冲突变成共同解决问题的契机，修复后关系更紧密")
        if df == "low":
            tips.append("对方自我分化低、易被情绪裹挟：你要保持温和坚定、不被带着走，先稳住局面再修复——修复后关系更紧密、信任更深")
        return "；".join(tips)

    # ------------------------------------------------------------------ #
    # 可信度维度 -> 策略保守度（v2 新增）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _trust_guidance(level: Optional[str]) -> tuple[list[str], list[str], str]:
        """根据综合可信度等级返回 (追加推荐, 追加禁忌, 说明)。

        level: high/medium/low/critical/None（None=画像未提供，不介入）。
        攻略导向原则：低可信度不等于放弃，而是通过小承诺建立信任闭环，
        引导对方养成兑现习惯，逐步提升信任维度。
        """
        if level == "high":
            return [], [], "可信度高：对方言行一致、承诺兑现，可正常推进关系，策略可大胆深入。"
        if level == "medium":
            return ["留意兑现细节"], ["轻信口头承诺"], (
                "可信度中等：基本可信，建议对承诺细节保持验证，"
                "通过小承诺逐步巩固信任，稳步推进关系。")
        if level == "low":
            return ["小额试探", "观察实际兑现", "通过小承诺建立信任闭环"], [
                "大额投入", "轻易托付", "全盘信任"], (
                "可信度偏低：建议通过小承诺建立信任闭环——先通过低成本小事观察对方兑现情况，"
                "引导对方养成兑现习惯，逐步提升信任维度，再根据表现稳步推进关系。")
        if level == "critical":
            return ["观察验证", "引导真实展现", "设定清晰底线", "小范围验证"], [
                "大额投入", "情感托付", "借钱或担保", "无条件妥协"], (
                "可信度严重不足：建议保持观察、通过无利益场景验证对方真实意图，"
                "创造安全氛围引导对方展现真实自我，根据验证结果决定推进策略。"
                "同时设定清晰底线——底线是为了关系健康可持续，不是为了疏远对方。")
        return [], [], ""

    # ------------------------------------------------------------------ #
    @staticmethod
    def _deception_guidance(persona: dict) -> tuple[list[str], list[str], str]:
        """根据画像中的 deception_risk 等级返回策略调整。

        仅当画像提供 deception_risk 且 data_sufficient=True 时介入；
        否则返回 ([], [], "")，行为与旧版完全一致（向后兼容）。

        攻略导向原则：伪装风险高不等于撤离，而是通过观察验证判断对方真实自我，
        引导对方展现真实一面，根据验证结果决定推进策略。如果真实自我可接受，
        则继续推进；如果不可接受，再考虑调整。
        """
        risk = (persona or {}).get("deception_risk")
        if not isinstance(risk, dict):
            return [], [], ""
        if not risk.get("data_sufficient", False):
            return [], [], ""  # 数据不足，不瞎猜、不介入

        level = str(risk.get("risk_level", "未知"))
        score = risk.get("risk_score", 0)

        if level == "低":
            return [], [], "行为一致性良好，可以正常推进关系。"
        if level == "中":
            return ["小范围验证"], ["轻信口头承诺"], (
                "检测到一些不一致信号，建议通过小范围互动进一步观察，"
                "根据对方反应调整推进节奏。")
        if level == "高":
            return ["保持观察", "不急于投入", "交叉验证", "观察验证", "引导真实展现"], [
                "大额投入", "快速推进关系", "情感托付", "借钱或担保"], (
                f"行为一致性审计评分{score}（高风险）：检测到多个不一致信号，"
                "建议保持观察、通过无利益场景验证对方真实意图，"
                "创造安全氛围引导对方展现真实自我，根据验证结果决定推进策略。")
        if level == "极高":
            return ["观察验证", "引导真实展现", "设定清晰底线", "小范围验证"], [
                "大额投入", "情感托付", "借钱或担保", "无条件妥协", "快速定终身"], (
                f"行为一致性审计评分{score}（极高风险）：存在较多行为不一致和目的导向行为，"
                "建议谨慎推进，通过持续验证判断对方真实自我。"
                "如果真实自我可接受则继续推进，同时设定清晰底线保护关系健康；"
                "如果真实自我不可接受，再根据验证结果调整策略方向。")
        return [], [], ""

    # ------------------------------------------------------------------ #
    # 精神操控/自恋倾向 -> 防御策略（基于 pua_score / 暗黑三联征自动触发）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _manipulation_guidance(persona: dict) -> tuple[list[str], list[str], str]:
        """检测到精神操控/自恋倾向时，叠加 NPD 特有防御策略。

        触发条件（基于画像自动推导，不硬编码标签）：
          - risk_warning.pua_score >= 5（自恋+操纵+煤气灯+推卸责任命中）
          - 或 psychology.dark_triad_tendencies 中 自恋/操纵 同时出现
        与「普通回避型」策略的本质区别：
          普通回避型 = 给空间/低压沟通；
          操控型 = 先识别煤气灯/守住底线/保持自我价值，再谈给空间。
        """
        pua = 0
        rw = (persona or {}).get("risk_warning") or {}
        try:
            pua = float(rw.get("pua_score", 0) or 0)
        except (TypeError, ValueError):
            pua = 0
        psych = (persona or {}).get("psychology") or {}
        dark = psych.get("dark_triad_tendencies") or {}
        narc = dark.get("自恋", dark.get("narcissism", 0))
        manip = dark.get("操纵", dark.get("manipulation", 0))
        defensive = psych.get("defensive_communication") or {}
        gas = defensive.get("否定现实", 0) + defensive.get("贬低感受", 0)

        triggered = pua >= 5 or (narc >= 2 and manip >= 1) or (narc >= 2 and gas >= 2)
        if not triggered:
            return [], [], ""

        recs = [
            "识别煤气灯效应", "守住底线不被操控", "保持自我价值感",
            "不被冷热交替操控", "记录对方操控模式", "尊重对方改变的节奏",
        ]
        forbids = [
            "和对方争对错讲道理", "在被贬低时自我怀疑",
            "用热情感动对方", "用冷战反制", "为对方的承诺反复找证据",
        ]
        note = (
            f"精神操控审计：pua_score={pua:.0f}（自恋{narc}/操纵{manip}/煤气灯{gas}）。"
            "检测到贬低-煤气灯-冷热交替式互动模式。理解这些模式往往源于对方童年创伤形成的防御机制"
            "（NPD倾向），不是你做错了。你的首要任务是：①识别煤气灯话术、守住自己的认知边界，不被带偏；"
            "②保持自我价值感；③在对方情绪平稳时用非暴力沟通表达你的真实感受，引导对方看到更健康的互动方式；"
            "④尊重对方改变的节奏，不要用过度付出去'拯救'对方。对方的道歉多为快速平息冲突，"
            "而非真正反思，承诺兑现需要通过后续行为逐步验证。"
        )
        return recs, forbids, note

    # ------------------------------------------------------------------ #
    # 人格倾向：基于画像特征自动识别（14 种），叠加专属策略与禁忌（v4 新增）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply_personality_strategy(persona: dict) -> tuple[list[str], list[str], str]:
        """基于画像特征自动识别人格倾向，叠加该倾向的专属沟通策略与禁忌。

        识别逻辑在 services/personality_strategy.py：从依恋三维 / CBT 认知扭曲 /
        防御性沟通 / 黑暗三角 / 情绪模式 / 冲突风格 / 边界 / 自我分化 / 信任度等
        可观测信号加权打分，**不硬编码标签**；只取得分最高且过阈的主导倾向。

        与 NPD/BPD 既有路径的关系：
          - 本方法是 14 种人格的统一入口（NPD/BPD 也在其中）；
          - 既有 _manipulation_guidance（NPD 煤气灯防御）仍独立运行，二者叠加；
          - 输出一律用"表现出 XX 倾向"，是行为模式推断，不是医学诊断。
        """
        try:
            recs, forbids, note, key = personality_strategy.build_strategy(persona)
        except Exception as e:  # noqa: BLE001 - 人格识别失败不影响主策略
            logger.debug("人格倾向识别失败: %s", e)
            return [], [], ""
        if not key:
            return [], [], ""
        return list(recs), list(forbids), note

    # ------------------------------------------------------------------ #
    # 投入平衡 -> 引导双向奔赴策略（v3 新增）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _investment_balance_guidance(persona: dict) -> tuple[list[str], list[str], str]:
        """根据画像中的 investment_balance 调整策略：引导双向奔赴。

        核心原则：不是不给对方付出，而是不能单方面超额付出。
        最终目的是引导双向奔赴——创造机会让对方自然地表达关心和投入，
        形成互相滋养的健康关系，而不是单方面付出。

        三种状态：
          - 用户超额投入(overall>1.3)：推荐引导对方投入类技巧，禁忌继续单方面付出；
          - 对方超额投入(overall<0.7)：推荐适当接受+观察目的+对等回报；
          - 平衡(0.7-1.3)：推荐维持双向奔赴的健康节奏。
        """
        inv = (persona or {}).get("investment_balance")
        if not isinstance(inv, dict):
            return [], [], ""  # 画像未提供该维度，不介入（向后兼容）

        status = str(inv.get("status", "")).lower()
        try:
            overall = float(inv.get("overall_ratio", 1.0))
        except (TypeError, ValueError):
            overall = 1.0

        # 用户超额投入：需引导对方也参与投入
        if status == "user_overinvest" or overall > 1.3:
            recs = [
                "请对方帮你一个小忙",
                "引导对方主动发起一次约会",
                "适度保持自己的节奏，不过度投入",
                "减少主动联系频率，观察对方是否会主动找你",
            ]
            forbids = ["继续单方面超额付出", "无底线讨好"]
            note = (
                f"投入平衡：当前综合投入比{overall}（用户超额投入）。"
                "当前用户投入远大于对方，单方面付出会让关系失衡，"
                "建议创造机会让对方自然地表达关心和投入，形成互相滋养的双向奔赴。"
            )
            return recs, forbids, note

        # 对方超额投入：适当接受 + 观察目的 + 对等回报
        if status == "other_overinvest" or overall < 0.7:
            recs = [
                "适当接受对方的好意，但观察是否有后续索取目的",
                "回报对方的投入，但保持平衡",
                "如果对方投入后有索取，评估是否合理，不合理则温和拒绝",
            ]
            forbids: list[str] = []
            note = (
                f"投入平衡：当前综合投入比{overall}（对方投入较多）。"
                "对方投入较多时可以自然接受，同时留意是否存在单方面索取的隐性模式。"
                "健康的关系是双向奔赴，不是单方面接受。"
            )
            return recs, forbids, note

        # 平衡：维持健康节奏
        recs = ["维持双向奔赴的健康节奏"]
        forbids = []
        note = (
            f"投入平衡：当前综合投入比{overall}（双方投入平衡）。"
            "双方投入平衡，关系健康，继续维持当前节奏。"
        )
        return recs, forbids, note

    # ------------------------------------------------------------------ #
    # 无事献殷勤检测 -> 策略调整（v3 新增）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _unwarranted_guidance(persona: dict) -> tuple[list[str], list[str], str]:
        """根据画像中的 unwarranted_attention 调整策略。

        仅当检测到高风险(high/critical 或 score>=60)时介入；
        攻略导向：不是"对方在捞你"，而是"观察索取目的+引导双向奔赴"。
        """
        uw = (persona or {}).get("unwarranted_attention")
        if not isinstance(uw, dict):
            return [], [], ""
        if not uw.get("detected"):
            return [], [], ""

        level = str(uw.get("risk_level", "low"))
        score = uw.get("risk_score", 0)
        try:
            score_num = float(score)
        except (TypeError, ValueError):
            score_num = 0.0

        if level in ("high", "critical") or score_num >= 60:
            recs = ["观察索取目的+引导双向奔赴"]
            forbids = ["单方面接受大额好处"]
            note = (
                f"无事献殷勤检测：风险评分{score}（{level}）。"
                "检测到对方在关系早期有超额付出后索取的行为，"
                "建议观察是否有明确目的，引导双向奔赴而非单方面接受，"
                "重大利益前设定清晰底线。"
            )
            return recs, forbids, note
        return [], [], ""

    # ------------------------------------------------------------------ #
    # 已读不回/不回复场景：按 5 种原因叠加专属策略（v5 新增）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _apply_no_reply_strategy(persona: dict) -> tuple[list[str], list[str], str]:
        """检测到不回复场景时，按原因（5 种）叠加完全不同的应对策略。

        上游流水线（scheduler / persona 构建）会运行 NoReplyDetector，
        把结果写进 persona["no_reply"]，形如：
            {
              "is_no_reply": True,
              "severity": "中度",          # 轻度/中度/重度/极端
              "reason": "losing_interest", # true_busy/losing_interest/
                                          # punitive_silence/avoidant_space/npd_coldwar
              "reason_label": "没兴趣/在疏远",
              "waiting_hours": 48.0,
            }
        本方法据此调用 NoReplyDetector.get_strategy 取该原因的专属推荐/禁忌。

        设计原则（修复引导导向）：
          - 5 种原因策略两两不同，不能一概而论"别理她"或"继续发"；
          - 严重程度调节强度：轻度=观察 / 中度=行动 / 重度=设定边界 / 极端=评估关系；
          - 原因分类是行为模式推断，措辞用"疑似/倾向"，不贴标签。

        Returns:
            tuple: (追加推荐, 追加禁忌, reasoning 引用行)。
                   未检测到不回复场景时返回 ([], [], "")（向后兼容）。
        """
        nr = (persona or {}).get("no_reply")
        if not isinstance(nr, dict):
            return [], [], ""
        if not nr.get("is_no_reply", False):
            return [], [], ""

        reason = str(nr.get("reason", ""))
        severity = str(nr.get("severity", "中度"))
        if not reason:
            return [], [], ""

        try:
            from services.no_reply_detector import NoReplyDetector
            strat = NoReplyDetector().get_strategy(reason, severity, persona)
        except Exception as e:  # noqa: BLE001 - 不回复策略失败不影响主策略
            logger.debug("不回复策略生成失败: %s", e)
            return [], [], ""

        recs = list(strat.get("recommended", []))
        forbids = list(strat.get("forbidden", []))
        label = strat.get("reason_label", reason)
        waiting = nr.get("waiting_hours")
        wait_txt = f"已等待约{waiting:.0f}小时" if isinstance(waiting, (int, float)) else ""
        note = (
            f"不回复场景：当前{wait_txt}，原因判断为「{label}」（{severity}）。"
            f"{strat.get('explanation', '')}"
        )
        return recs, forbids, note


    # ------------------------------------------------------------------ #
    # 心理学定律：按画像自动匹配（v2.1 新增，数据驱动，非硬编码）
    # ------------------------------------------------------------------ #
    def _apply_psychology_laws(
        self, persona: dict, dims: dict, relation_stage: int
    ) -> tuple[list[str], list[str], str]:
        """根据画像+关系阶段，自动匹配恋爱/人际关系心理学定律。

        设计原则（修复引导导向）：
          - 定律用于"理解关系机制 + 引导健康互动"，不输出"操控对方"的策略；
          - 所有匹配数据驱动，见 services/psychology_laws.py 的 LAWS 注册表；
          - 对容易被误用为操控的定律（间歇性强化/稀缺/登门槛/吊桥...），
            推荐技巧一律是"识别对方机制 + 自己保持健康节奏"，禁忌列加入误用警告。

        Returns:
            tuple: (追加推荐技巧, 追加禁忌技巧, reasoning 引用行)。
                   未命中任何定律时返回 ([], [], "")（向后兼容）。
        """
        try:
            hits = psychology_laws.match_laws(persona, relation_stage, dims)
        except Exception as e:  # noqa: BLE001 - 定律层失败不影响主策略
            logger.warning("心理学定律匹配失败: %s", e)
            return [], [], ""
        if not hits:
            return [], [], ""

        rec_tips: list[str] = []
        forb_tips: list[str] = []
        for law in hits:
            for tip in law.get("rec_tips", []):
                if tip not in rec_tips:
                    rec_tips.append(tip)
            for tip in law.get("forb_tips", []):
                if tip not in forb_tips:
                    forb_tips.append(tip)

        note = psychology_laws.law_reasoning_lines(hits)
        return rec_tips, forb_tips, note

    # ------------------------------------------------------------------ #
    # 因时制宜：记录 / 历史 / 变更检测
    # ------------------------------------------------------------------ #
    def record_strategy(self, person_id: str, persona: dict, result: dict) -> None:
        """把一次策略评估写入 strategy_history（db 不可用时静默跳过）。

        Args:
            person_id: 人物 ID。
            persona: 本次画像 dict。
            result: analyze() 的返回 dict。
        """
        if self.db is None:
            return
        dims = result.get("_dims") or self._extract_dimensions(persona)
        try:
            self.db.execute(
                """
                INSERT INTO strategy_history
                    (person_id, attachment_type, conflict_style,
                     recommended_strategies_json, forbidden_strategies_json, reasoning)
                VALUES (:pid, :att, :cf, :rec, :forb, :reason)
                """,
                {
                    "pid": person_id,
                    "att": dims.get("attachment", ""),
                    "cf": dims.get("conflict", ""),
                    # 存储原始无注释技巧名，便于策略变更检测
                    "rec": json.dumps(result.get("recommended_raw", result.get("recommended", [])), ensure_ascii=False),
                    "forb": json.dumps(result.get("forbidden", []), ensure_ascii=False),
                    "reason": result.get("reasoning", ""),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("strategy_history 写入失败 person=%s: %s", person_id, e)

    # ------------------------------------------------------------------ #
    # 数据飞轮（P0）：接收用户对本轮推荐的“对/错/存疑”反馈
    # ------------------------------------------------------------------ #
    def submit_feedback(
        self,
        person_id: str,
        result: dict,
        feedback: str,
        note: str = "",
    ) -> dict:
        """输出侧暴露的“接收反馈”挂点。

        UI / 浮层 / CLI 在用户看完本轮策略推荐后点“对(right) / 错(wrong) /
        存疑(doubt)”时调用本方法；它从 ``analyze()`` 返回的 result 里取出
        本轮 ``judgment_id``，写入 feedback_records，供 persona/compass 校准消费。

        Args:
            person_id: 人物 ID。
            result: 本轮 ``analyze()`` 的返回 dict（需含 judgment_id/judgment_type）。
            feedback: ``right`` / ``wrong`` / ``doubt``。
            note: 可选备注（为什么觉得错/存疑）。

        Returns:
            dict: FeedbackService.record_feedback 的结果；非法 feedback 抛 ValueError。
        """
        result = result or {}
        judgment_id = result.get("judgment_id") or ""
        judgment_type = result.get("judgment_type") or "strategy"
        try:
            from services.feedback_service import get_feedback_service
            svc = get_feedback_service()
            return svc.record_feedback(
                person_id=person_id,
                judgment_type=judgment_type,
                judgment_id=judgment_id,
                feedback=feedback,
                note=note,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("策略反馈写入失败 person=%s: %s", person_id, e)
            return {"ok": False, "reason": str(e)}

    def get_strategy_history(self, person_id: str, limit: int = 10) -> list:
        """获取某人物的策略变更历史（最新在前）。

        Args:
            person_id: 人物 ID。
            limit: 最多返回条数。

        Returns:
            list[dict]: 历史记录列表；无 db 返回空列表。
        """
        if self.db is None:
            return []
        try:
            rows = self.db.fetch_all(
                "SELECT * FROM strategy_history WHERE person_id=:pid "
                "ORDER BY id DESC LIMIT :limit",
                {"pid": person_id, "limit": int(limit)},
            )
            return list(rows or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("strategy_history 查询失败 person=%s: %s", person_id, e)
            return []

    def detect_strategy_change(self, person_id: str, current_result: dict) -> dict:
        """对比最近一次历史，判断推荐策略是否随画像更新而变化。

        Args:
            person_id: 人物 ID。
            current_result: 本次 analyze() 的返回 dict。

        Returns:
            dict: {
                "changed": bool,
                "from": str,        # 旧策略描述（无历史时为"首次"）
                "to": str,          # 新策略描述
                "reason": str,      # 变化原因
            }
        """
        history = self.get_strategy_history(person_id, limit=1)
        # 用原始无注释技巧名做比较，避免注释格式变化误判为策略变更
        current_rec_raw = list(current_result.get("recommended_raw", current_result.get("recommended", [])))
        current_rec_display = list(current_result.get("recommended", []))
        current_att = (current_result.get("_dims") or {}).get("attachment", "")

        if not history:
            return {
                "changed": False,
                "from": "首次评估",
                "to": "、".join(current_rec_display[:5]),
                "reason": "无历史记录，视为首次建立策略基线",
            }

        last = history[0]
        try:
            old_rec = json.loads(last.get("recommended_strategies_json") or "[]")
        except (json.JSONDecodeError, TypeError, ValueError):
            old_rec = []
        old_att = last.get("attachment_type", "") or ""

        changed = (set(old_rec) != set(current_rec_raw)) or (old_att != current_att)
        if not changed:
            return {
                "changed": False,
                "from": "、".join(old_rec[:5]),
                "to": "、".join(current_rec_display[:5]),
                "reason": "推荐策略与上次一致",
            }

        return {
            "changed": True,
            "from": f"依恋={old_att}，推荐={'、'.join(old_rec[:5]) or '无'}",
            "to": f"依恋={current_att}，推荐={'、'.join(current_rec_display[:5]) or '无'}",
            "reason": f"对方画像/依恋类型由 {old_att or '未知'} 变为 {current_att or '未知'}，策略矩阵已重算",
        }


# ====================================================================== #
# 便捷：从磁盘读取某人物最近一次画像（供 task_engine / scheduler 调用）
# ====================================================================== #
def load_persona_from_disk(person_id: str, project_root: Optional[str] = None) -> Optional[dict]:
    """读取 PersonaAnalyzer 落盘的 persona_profile.json。

    Args:
        person_id: 人物 ID。
        project_root: 项目根目录；默认取本文件上两级。

    Returns:
        dict | None: 画像 dict；文件不存在或解析失败返回 None。
    """
    if not person_id:
        return None
    if project_root is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(project_root, "data", "persons", person_id, "persona_profile.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("读取画像失败 %s: %s", path, e)
        return None
