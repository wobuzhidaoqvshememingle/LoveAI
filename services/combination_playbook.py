# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 心理学效应「组合协同方案」（Combination Playbook）

设计动机：
  此前编排器只是把命中的定律「罗列」出来。真实的关系干预不是单效应发力，
  而是一组效应按「先后顺序 + 各自分工 + 协同逻辑」组合成一个方案——
  像病例处方：先做什么、后做什么、哪个打底、哪个收尾。

方法论来源（已确立的学术体系；运行时在线检索受限，采用教科书级共识）：
  1. 西奥迪尼影响力原则组合：互惠/承诺一致/社会认同/喜好/权威/稀缺 不是孤立
     使用，而是「先建立喜好与信任 → 再用承诺一致固化 → 辅以社会认同」的递进链；
  2. 个案概念化（case formulation）：先理解这个「具体的人」的维持机制，
     再据此挑选/排序干预，而不是套用通用技巧清单；
  3. 整合式治疗（integrative psychotherapy / technical eclecticism）：
     按来访者的实际问题从不同流派取技术，按「先建立关系/安全 → 再改变行为 →
     再巩固认知」的次序协同；
  4. Gottman 爱情大厦分层：地基（爱情地图/信任）→ 友谊 → 冲突管理 → 共享意义，
     层与层有先后，跳层无效。

本模块据此把 5 个高频场景预置为「组合方案」，每个方案明确：
  - effects：参与效应（id/分工 role/先后 order/时机 timing）；
  - synergy_logic：协同逻辑（一句话讲清为什么是这个顺序）；
  - combined_strategy：整体策略说明；
  - combined_tasks：按序组织的组合任务。

服务层纯 Python，不依赖任何 GUI/Qt 框架；单方案异常隔离，失败不影响主流程。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ====================================================================== #
# 组合方案注册表
# 每条：name / scene_hint / 适用条件 cond / effects / synergy_logic / combined_strategy
# effects 中 id 必须与 psychology_laws.LAWS 的 id 对齐。
# ====================================================================== #
_PLAYBOOKS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------ #
    # 1. 暧昧推进组合（stage 2-5）
    # ------------------------------------------------------------------ #
    {
        "playbook_name": "暧昧推进组合",
        "scene": "暧昧推进",
        "effects": [
            {"id": "mere_exposure", "role": "增加熟悉感，打基础", "order": 1,
             "timing": "前期：低压力、可预测地自然出现"},
            {"id": "business_card_effect", "role": "展示真实相似点，建立认同", "order": 2,
             "timing": "熟悉后：在真实交集上主动共振"},
            {"id": "suspension_bridge", "role": "共同活动制造情绪唤醒，归因到彼此", "order": 3,
             "timing": "有一定熟悉后：安排轻度唤醒的共同活动"},
            {"id": "intermittent_reinforcement", "role": "健康版节奏：保留适度变化，避免机械秒回的腻烦", "order": 4,
             "timing": "升温期：稳定为主、偶有惊喜；若故意忽冷忽热，会触发间歇性强化机制、让关系变成赌局"},
            {"id": "franklin_effect", "role": "引导对方也投入，反向增加参与感", "order": 5,
             "timing": "熟悉后：请对方帮一个不涉及钱的小忙"},
        ],
        "synergy_logic": "先熟悉 → 再认同 → 再心动 → 再期待 → 再投入，层层递进；"
                         "前一层没打好就跳到心动/投入，会显得突兀或需求感过重。",
        "combined_strategy": "暧昧期不要急着表白，先用低压力熟悉感打底，"
                             "在真实相似点上共振，靠共同活动自然制造心跳，"
                             "节奏稳定中带一点变化，再引导对方也参与投入——让关系自己长出来。",
    },
    # ------------------------------------------------------------------ #
    # 2. 冲突修复组合
    # ------------------------------------------------------------------ #
    {
        "playbook_name": "冲突修复组合",
        "scene": "冲突修复",
        "effects": [
            {"id": "emotional_bids", "role": "先发出/响应连接信号，打破对立", "order": 1,
             "timing": "刚吵完：留意对方任何一个示弱/示好的小动作"},
            {"id": "south_wind_law", "role": "温和破冰，不施压不翻旧账", "order": 2,
             "timing": "信号出现后：把'你应该'换成'我希望/我需要'"},
            {"id": "aronson_effect", "role": "先理解后肯定，让对方愿意接受", "order": 3,
             "timing": "气氛缓和后：先肯定对方合理的部分，再谈分歧"},
            {"id": "self_disclosure_law", "role": "适度展示自己的感受与脆弱，拉近距离", "order": 4,
             "timing": "双方都平静后：用'我当时的感受是…'代替'你又…'"},
            {"id": "gottman_5to1_ratio", "role": "补足积极互动，把关系账户拉回健康线", "order": 5,
             "timing": "修复后：有意补 5 次真诚积极互动"},
        ],
        "synergy_logic": "先转向（发出连接）→ 再温和（不施压）→ 再被接受（先肯定）→ "
                         "再亲近（适度暴露）→ 最后补足比率；顺序反了（先讲道理/先翻账）会二次点燃冲突。",
        "combined_strategy": "冲突修复不是争输赢：先接住对方的连接信号，用温和替代强硬，"
                             "先理解再表达，适度暴露自己的感受，最后用多次小的积极互动把关系账户补回来。",
    },
    # ------------------------------------------------------------------ #
    # 3. 关系停滞重启组合
    # ------------------------------------------------------------------ #
    {
        "playbook_name": "停滞重启组合",
        "scene": "关系停滞",
        "effects": [
            {"id": "flywheel_effect", "role": "重新推动飞轮，前期费力也要先投入", "order": 1,
             "timing": "停滞期：先做一件高质量陪伴/记住小事的动作"},
            {"id": "self_disclosure_law", "role": "重新拉近距离，打破例行公事", "order": 2,
             "timing": "重启后：聊一个比'吃了吗'深一层的话题"},
            {"id": "matthew_effect", "role": "反向应用：打破单方面投入，引导对方也投入", "order": 3,
             "timing": "重新连接后：把投入降到对等，收回部分注意力"},
            {"id": "catfish_effect", "role": "【慎用】仅条件满足时，真实社交轻度激活", "order": 4,
             "timing": "最后才用：仅在安全/疏离型、非冲突期、非NPD急性期"},
        ],
        "synergy_logic": "先启动（费力推一下飞轮）→ 再亲近（重新敞露）→ 再平衡（双向投入）→ "
                         "最后才谨慎激活竞争；鲶鱼是最后手段，前置三步没做就上鲶鱼等于火上浇油。",
        "combined_strategy": "关系停滞先别想着用'危机感'刺激对方，而是先自己重新投入一点点、"
                             "重新敞露、把投入拉回对等；鲶鱼效应是严格门控下的最后选项，条件不齐不用。",
    },
    # ------------------------------------------------------------------ #
    # 4. 对方冷淡/退缩应对组合
    # ------------------------------------------------------------------ #
    {
        "playbook_name": "退缩应对组合",
        "scene": "对方冷淡退缩",
        "effects": [
            {"id": "hedgehog_dilemma", "role": "主动调整距离，不逼近", "order": 1,
             "timing": "退缩时：把联系频率降到你自己也不焦虑的程度"},
            {"id": "south_wind_law", "role": "温暖但不强压", "order": 2,
             "timing": "降频后：保持温和的低压关心，不质问"},
            {"id": "mere_exposure", "role": "低压保持存在感，不索取回应", "order": 3,
             "timing": "给空间期间：固定时间简短问候/共同场景偶遇"},
            {"id": "emotional_bids", "role": "等对方发出请求时，立刻转向响应", "order": 4,
             "timing": "对方一旦主动分享/求助：放下事，认真展开回应"},
        ],
        "synergy_logic": "先调距离（不追）→ 再温暖（低压关心）→ 再低压存在（不索取）→ "
                         "最后伺机响应（对方伸手时接住）；顺序反了（逼近/质问）会把对方推得更远。",
        "combined_strategy": "对方退缩时越追越远：先把距离调到双方都舒服，保持温和低压的存在感，"
                             "不索取回应，等对方主动发出连接信号时，立刻高质量接住。",
    },
    # ------------------------------------------------------------------ #
    # 5. 长期信任建设组合（stage 6+）
    # ------------------------------------------------------------------ #
    {
        "playbook_name": "长期信任组合",
        "scene": "长期信任",
        "effects": [
            {"id": "trust_integrity", "role": "诚信为基：小承诺要么不许，许了必兑", "order": 1,
             "timing": "贯穿始终：做不到的当场说，答应的就兑现"},
            {"id": "commitment_consistency", "role": "用小承诺的兑现积累可信", "order": 2,
             "timing": "日常：按自己节奏给承诺，给了就保持一致"},
            {"id": "emotional_bank_account", "role": "持续存款，取款后当天补", "order": 3,
             "timing": "日常：记住小事/主动关心，吵架后当天补一笔"},
            {"id": "love_maps", "role": "持续更新对对方内心世界的了解", "order": 4,
             "timing": "定期：主动问并记住TA最近的压力与期待"},
        ],
        "synergy_logic": "诚信为基 → 小承诺积累 → 持续存款 → 持续了解；信任是地基工程，"
                         "没有前面的一致性，后面的亲密与了解都立不住。",
        "combined_strategy": "长期关系靠的不是浪漫惊喜，而是可预期的一致：小承诺必兑现、"
                             "日常持续存款、不断更新你对TA的了解——信任是一点点存出来的。",
    },
]


# ====================================================================== #
# 方案适用条件（按 scene / stage / dims 数据驱动，不硬编码人设）
# ====================================================================== #
def _cond_flirting(stage: int, scene: str, dims: dict, persona: dict) -> bool:
    return 2 <= stage <= 5 or scene in ("暧昧推进", "邀约", "情感表白")


def _cond_repair(stage: int, scene: str, dims: dict, persona: dict) -> bool:
    if scene in ("冲突争吵", "分手危机"):
        return True
    return dims.get("conflict") in ("competing", "avoiding")


def _cond_stagnation(stage: int, scene: str, dims: dict, persona: dict) -> bool:
    rd = (persona or {}).get("relation_status")
    return (str(rd).lower() == "stagnant") or bool((persona or {}).get("_orchestrator_stagnation"))


def _cond_withdraw(stage: int, scene: str, dims: dict, persona: dict) -> bool:
    if scene in ("对方冷淡", "已读不回"):
        return True
    nr = (persona or {}).get("no_reply") or {}
    return bool(nr.get("is_no_reply"))


def _cond_longterm(stage: int, scene: str, dims: dict, persona: dict) -> bool:
    return stage >= 6


_CONDITIONS: dict[str, Callable[[int, str, dict, dict], bool]] = {
    "暧昧推进组合": _cond_flirting,
    "冲突修复组合": _cond_repair,
    "停滞重启组合": _cond_stagnation,
    "退缩应对组合": _cond_withdraw,
    "长期信任组合": _cond_longterm,
}


def _law_name_by_id(law_id: str, law_index: dict) -> str:
    law = law_index.get(law_id)
    return law.get("name", law_id) if law else law_id


def build_playbook(playbook: dict, law_index: dict, hit_ids: set) -> dict:
    """把一个方案模板实例化：标注每个效应当前是否命中(active)，生成组合任务。"""
    effects_out: list[dict] = []
    combined_tasks: list[str] = []
    for ef in playbook["effects"]:
        lid = ef["id"]
        law = law_index.get(lid, {})
        active = lid in hit_ids
        effects_out.append({
            "id": lid,
            "name": _law_name_by_id(lid, law_index),
            "role": ef["role"],
            "order": ef["order"],
            "timing": ef["timing"],
            "active": active,
        })
        # 组合任务：优先取该效应的首条任务（按 order 排序，最多取前 5 条防爆炸）
        if active and law.get("tasks") and len(combined_tasks) < 5:
            combined_tasks.append(law["tasks"][0])

    effects_out.sort(key=lambda e: e["order"])
    active_count = sum(1 for e in effects_out if e["active"])
    return {
        "playbook_name": playbook["playbook_name"],
        "scene": playbook["scene"],
        "effects": effects_out,
        "synergy_logic": playbook["synergy_logic"],
        "combined_strategy": playbook["combined_strategy"],
        "combined_tasks": combined_tasks,
        "active_effects_count": active_count,
    }


def match_playbooks(scene: str, stage: int, dims: dict, persona: dict,
                    hits: list[dict]) -> list[dict]:
    """根据场景/阶段/画像/命中效应，选出并实例化适用的组合方案。

    Args:
        scene: 编排器识别的场景名（如"冲突争吵"）。
        stage: 关系阶段 0-10。
        dims: psychology_laws 提取的维度 dict。
        persona: 完整画像 dict。
        hits: 当前命中的定律完整 dict 列表（match_laws 的返回）。

    Returns:
        list[dict]: 实例化后的组合方案；按 active_effects_count 降序。
    """
    try:
        law_index = {law["id"]: law for law in _all_laws()}
    except Exception as e:  # noqa: BLE001
        logger.warning("组合方案：加载定律索引失败: %s", e)
        law_index = {}
    hit_ids = {h.get("id") for h in hits}

    selected: list[dict] = []
    for pb in _PLAYBOOKS:
        cond = _CONDITIONS.get(pb["playbook_name"])
        if cond is None:
            continue
        try:
            if not cond(stage, scene or "", dims or {}, persona or {}):
                continue
        except Exception as e:  # noqa: BLE001 - 单方案条件失败不影响其他
            logger.debug("组合方案 %s 条件判断失败: %s", pb["playbook_name"], e)
            continue
        try:
            selected.append(build_playbook(pb, law_index, hit_ids))
        except Exception as e:  # noqa: BLE001
            logger.warning("组合方案 %s 实例化失败: %s", pb["playbook_name"], e)

    # 命中效应越多的方案排越前（最贴合当前局面的组合）
    selected.sort(key=lambda p: p.get("active_effects_count", 0), reverse=True)
    return selected


def _all_laws() -> list[dict]:
    """延迟取 LAWS（避免与 psychology_laws 顶层循环导入）。"""
    from services import psychology_laws as _pl
    return _pl.LAWS


def primary_playbooks_summary(playbooks: list[dict]) -> str:
    """把组合方案压成一行 reasoning 摘要（供策略引用）。"""
    if not playbooks:
        return ""
    parts = []
    for pb in playbooks[:2]:
        chain = " → ".join(e["name"] for e in pb.get("effects", []))
        parts.append(f"【{pb['playbook_name']}】{chain}；协同：{pb.get('synergy_logic','')}")
    return "心理学效应组合方案：" + "；".join(parts)
