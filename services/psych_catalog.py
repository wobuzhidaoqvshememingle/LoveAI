# -*- coding: utf-8 -*-
"""LoveAI v2.0 - 心理学全维度参考体系筛选清单（结构化导出）。

用途：
  - 给"关于 / 理论依据"页展示 LoveAI 背后到底信哪些心理学理论；
  - 作为筛选决策的单一事实源（docs/心理学参考体系筛选清单-20261010.md 由本文件生成）；
  - 与 services/psychology_laws.py 里 ~90 条定律做映射，避免"理论名"和"代码 ID"两张皮。

筛选主线：恋爱 / 沟通 / 依恋 / 操纵 / 人格。
剔除/降级为"参考但不主用"：纯粹临床病理（精神分裂/双相细节）、发展心理学童年期理论、
工业组织心理学、教育心理学。

字段约定（每条）：
  id          : 稳定短 ID（snake_case）
  name        : 中文名
  origin      : 提出者 / 流派
  why_keep    : 为什么留在主用体系（一句话）
  loveai_module: 在 LoveAI 哪个模块用到
  code_file   : 对应代码文件（相对项目根）
  law_id      : 若已在 psychology_laws.py 注册，填其 id；否则空串
  tags        : 主线标签列表（love / communication / attachment / manipulation / personality）
"""
from __future__ import annotations

from typing import Any

# ------------------------------------------------------------------ #
# 主用理论（13 条核心 + 与之绑定的定律）
# ------------------------------------------------------------------ #
KEEP: list[dict[str, Any]] = [
    # ===== 依恋主线 =====
    {
        "id": "attachment_theory",
        "name": "依恋理论",
        "origin": "Bowlby / Ainsworth",
        "why_keep": "成人依恋类型（安全/焦虑/回避/恐惧）是 LoveAI 判断对方行为动机的第一把钥匙，决定策略是‘给安全感’还是‘给空间’。",
        "loveai_module": "persona_analyzer 依恋三维评分 + strategy_advisor 依恋矩阵",
        "code_file": "services/persona_analyzer.py",
        "law_id": "attachment_foundational",
        "tags": ["attachment", "love"],
    },
    # ===== 人格主线 =====
    {
        "id": "big_five",
        "name": "大五人格（NEO-PI-R）",
        "origin": "Costa & McCrae",
        "why_keep": "OCEAN 五维度是判断人格稳定性、宜人性、尽责性的学术底座；LoveAI 用神经质/宜人性做代理指标。",
        "loveai_module": "personality_strategy 14 人格签名 + persona_analyzer 情绪模式",
        "code_file": "services/personality_strategy.py",
        "law_id": "",
        "tags": ["personality"],
    },
    {
        "id": "dsm5_pd",
        "name": "DSM-5 人格障碍（NPD/BPD 为主）",
        "origin": "APA DSM-5",
        "why_keep": "识别自恋型（NPD）/边缘型（BPD）等病理性关系模式，是 LoveAI 反制路线与危机分级的依据。",
        "loveai_module": "personality_strategy npd/bpd + persona_analyzer 黑暗三联征 + digital_twin NPD prompt",
        "code_file": "services/personality_strategy.py",
        "law_id": "",
        "tags": ["personality", "manipulation"],
    },
    # ===== 婚姻/关系研究主线 =====
    {
        "id": "gottman_method",
        "name": "Gottman 婚姻研究体系",
        "origin": "John Gottman",
        "why_keep": "四骑士、5:1 正负比、情感投标、爱情地图、爱情大厦——是 LoveAI 冲突修复与长期满意度判断的金标准。",
        "loveai_module": "psychology_laws Gottman 四件套 + strategy_advisor 冲突矩阵",
        "code_file": "services/psychology_laws.py",
        "law_id": "gottman_four_horsemen",
        "tags": ["love", "communication"],
    },
    {
        "id": "sternberg_triangle",
        "name": "爱情三角理论",
        "origin": "Robert Sternberg",
        "why_keep": "亲密/激情/承诺三要素定位当前关系是喜欢/迷恋/空洞/浪漫/伴侣/圆满，决定阶段推进方向。",
        "loveai_module": "psychology_laws sternberg_triangle + persona_analyzer 关系阶段",
        "code_file": "services/psychology_laws.py",
        "law_id": "sternberg_triangle",
        "tags": ["love"],
    },
    {
        "id": "rusbult_investment",
        "name": "Rusbult 投资模型",
        "origin": "Caryl Rusbult",
        "why_keep": "满意度 + 替代质量 + 投入量共同决定承诺度，解释‘为什么不幸福也不分手’，是沉没成本/反制路线的理论依据。",
        "loveai_module": "persona_analyzer investment_balance + psychology_laws sunk_cost",
        "code_file": "services/persona_analyzer.py",
        "law_id": "sunk_cost",
        "tags": ["love"],
    },
    # ===== 认知/社会心理主线 =====
    {
        "id": "cognitive_dissonance",
        "name": "认知失调",
        "origin": "Leon Festinger",
        "why_keep": "解释人为何为自己投入过的人/事辩护，是‘越付出越爱’和反 PUA 时打破自我辩护的入口。",
        "loveai_module": "psychology_laws cognitive_dissonance",
        "code_file": "services/psychology_laws.py",
        "law_id": "cognitive_dissonance",
        "tags": ["love", "manipulation"],
    },
    {
        "id": "social_penetration",
        "name": "社会渗透理论（自我暴露）",
        "origin": "Altman & Taylor",
        "why_keep": "关系亲密度 = 自我暴露的广度+深度，是判断‘我们到底熟到哪一步’的核心指标。",
        "loveai_module": "psychology_laws self_disclosure_law + persona_analyzer 关系阶段",
        "code_file": "services/psychology_laws.py",
        "law_id": "self_disclosure_law",
        "tags": ["love", "communication"],
    },
    {
        "id": "interdependence",
        "name": "相互依赖理论",
        "origin": "Harold Kelley & John Thibaut",
        "why_keep": "关系是双方结果矩阵的博弈，对等投入/非零和是判断‘这段关系值不值得继续’的底层框架。",
        "loveai_module": "psychology_laws non_zero_sum + reciprocity_principle",
        "code_file": "services/psychology_laws.py",
        "law_id": "non_zero_sum",
        "tags": ["love"],
    },
    # ===== 沟通主线 =====
    {
        "id": "nvc",
        "name": "非暴力沟通",
        "origin": "Marshall Rosenberg",
        "why_keep": "观察-感受-需要-请求四步法是 LoveAI 温和档话术的风格底座，避免指责/冷战/翻旧账。",
        "loveai_module": "strategy_advisor recommended 话术 + tutorial_engine 分步教程",
        "code_file": "services/strategy_advisor.py",
        "law_id": "",
        "tags": ["communication"],
    },
    # ===== 操纵主线 =====
    {
        "id": "gaslighting",
        "name": "煤气灯效应",
        "origin": "Cale Hirsch / 电影《煤气灯下》",
        "why_keep": "系统性否定对方现实感是 PUA/NPD 的核心手段，LoveAI 用 _DEFENSIVE_SIGNALS 直接识别并出反制。",
        "loveai_module": "persona_analyzer _DEFENSIVE_SIGNALS + manipulation_coach + psychology_laws gaslighting_effect",
        "code_file": "services/manipulation_coach.py",
        "law_id": "gaslighting_effect",
        "tags": ["manipulation"],
    },
    {
        "id": "pua_schools",
        "name": "PUA 五大流派",
        "origin": "Pickup Artist 社群（迷男/杂耍/罗杰斯/大卫·迪安杰罗/猞猁）",
        "why_keep": "拆解打压、冷热推拉、爱轰炸、间歇性强化等手段，是 LoveAI 反识别与‘别变成那样’的反面教材。",
        "loveai_module": "persona_analyzer _DARK_TRIAD_SIGNALS + deception_detector EARLY_PROMISE/GIFT",
        "code_file": "services/persona_analyzer.py",
        "law_id": "intermittent_reinforcement",
        "tags": ["manipulation"],
    },
    # ===== 决策/行为惯性主线 =====
    {
        "id": "path_dependence",
        "name": "路径依赖",
        "origin": "David / Arthur（制度经济学）",
        "why_keep": "关系一旦形成固定互动模式（追逃/冷战/讨好），单靠‘想通了’改不动；必须靠新任务/新行为打断惯性。",
        "loveai_module": "task_engine 打断惯性任务 + psychology_laws loss_aversion/sunk_cost",
        "code_file": "services/task_engine.py",
        "law_id": "loss_aversion",
        "tags": ["love", "communication"],
    },
]

# ------------------------------------------------------------------ #
# 参考但不主用（保留在知识库里，不进主策略）
# ------------------------------------------------------------------ #
REFERENCE: list[dict[str, Any]] = [
    {
        "id": "schizophrenia_bipolar",
        "name": "精神分裂 / 双相情感障碍细节",
        "origin": "DSM-5 临床精神病学",
        "why_defer": "LoveAI 不做临床诊断；只在 detect_self_harm_risk 等安全门上做风险提示，不替代就医。",
        "code_file": "services/psychology_laws.py",
        "law_id": "werther_effect",
        "tags": ["clinical"],
    },
    {
        "id": "developmental_childhood",
        "name": "发展心理学童年期理论（皮亚杰/埃里克森早期阶段）",
        "origin": "Piaget / Erikson",
        "why_defer": "成人依恋已覆盖童年经历的成年后果；童年期细节不直接服务于‘这段关系下一步怎么走’。",
        "code_file": "",
        "law_id": "",
        "tags": ["developmental"],
    },
    {
        "id": "io_psych",
        "name": "工业组织心理学（领导力/员工动机）",
        "origin": "Industrial-Organizational Psychology",
        "why_defer": "社交层级沟通法则、名片效应等带 I-O 色彩的条目，与亲密关系场景弱相关，仅作话术修辞参考。",
        "code_file": "services/psychology_laws.py",
        "law_id": "social_hierarchy_comm",
        "tags": ["io"],
    },
    {
        "id": "educational_psych",
        "name": "教育心理学（皮格马利翁/过度理由）",
        "origin": "Rosenthal / Deci",
        "why_defer": "教师对学生的期待效应和外部动机挤出效应，在恋爱场景只做弱映射，不做主用理论。",
        "code_file": "services/psychology_laws.py",
        "law_id": "pygmalion_effect",
        "tags": ["educational"],
    },
    {
        "id": "neuroscience_long_term_love",
        "name": "长久爱情神经科学（VTA/尾状核/多巴胺）",
        "origin": "Fischer / Brown",
        "why_defer": "神经科学结论用来做科普解释很好，但 LoveAI 本地规则拿不到脑电数据，只能作为‘为什么长期关系会变淡’的注释。",
        "code_file": "services/psychology_laws.py",
        "law_id": "long_term_love_neuroscience",
        "tags": ["neuro"],
    },
    {
        "id": "folk_economy_laws",
        "name": "民间/管理类定律（墨菲/蝴蝶/木桶/二八/手表/鸟笼/飞轮/鲶鱼）",
        "origin": "民间智慧 + 管理学说",
        "why_defer": "修辞价值大于预测价值，不在主策略命中表；仅在话术里做比喻，不参与 dims 匹配。",
        "code_file": "services/psychology_laws.py",
        "law_id": "murphy_law",
        "tags": ["folk"],
    },
]


# ------------------------------------------------------------------ #
# 结构化导出
# ------------------------------------------------------------------ #
def all_keep() -> list[dict[str, Any]]:
    """返回主用理论列表（副本）。"""
    return [dict(x) for x in KEEP]


def all_reference() -> list[dict[str, Any]]:
    """返回参考但不主用的理论列表（副本）。"""
    return [dict(x) for x in REFERENCE]


def catalog() -> dict[str, Any]:
    """完整目录：供‘关于/理论依据’页一次性渲染。"""
    return {
        "version": "2026-10-10",
        "mainline": ["恋爱", "沟通", "依恋", "操纵", "人格"],
        "keep": KEEP,
        "reference": REFERENCE,
        "stats": {
            "keep_count": len(KEEP),
            "reference_count": len(REFERENCE),
        },
    }


if __name__ == "__main__":  # pragma: no cover
    import json
    print(json.dumps(catalog(), ensure_ascii=False, indent=2))
