# -*- coding: utf-8 -*-
"""R3.3-77 桥接层事件类型 + 载荷 Schema 注册表。

设计原则：
- 优先复用 services.event_bus 已有的事件常量（AFFECTION_CHANGED / TASKS_COMPLETED /
  MEMORY_UPDATED / STRATEGY_CHANGED / PROFILE_UPDATED），这样 service_facade 里
  已经 publish 的事件可以直接被桥消费，无需改动存量。
- 桥接层新增的事件类型集中在此，避免拼写漂移；每个事件都登记一个载荷 schema
  （必填 key 集合 + 可选 key），由 BridgeBus 在 publish 时做校验/去噪。

服务层纯 Python：不 import PySide6。
"""
from __future__ import annotations

from services.event_bus import (  # noqa: F401  (re-export，桥与外部统一引用)
    AFFECTION_CHANGED,
    TASKS_COMPLETED,
    MEMORY_UPDATED,
    STRATEGY_CHANGED,
    PROFILE_UPDATED,
    EMOTION_UPDATED,
    STAGE_CHANGED,
)

# ---- 桥接层新增事件类型（网状多跳专用）---- #
# 任务完成（桥触发口径：任务引擎真实完成后，桥据此发联动）
TASK_COMPLETED_BRIDGE = "bridge_task_completed"
# 好感等级/总分变化（桥口径，比 AFFECTION_CHANGED 多带 level/trend）
AFFECTION_LEVEL_UPDATED = "bridge_affection_level_updated"
# 罗盘重校准完成（任务/好感/画像变化后，桥重跑 direction_compass）
COMPASS_RECALIBRATED = "bridge_compass_recalibrated"
# 桌宠情绪表现事件（好感/罗盘 → 桌宠 WS 表现）
PET_EMOTION_EVENT = "bridge_pet_emotion_event"
# 桌宠上行：用户与桌宠交互（点击/抚摸）→ 好感微反馈
PET_USER_INTERACTION = "bridge_pet_user_interaction"
# 记忆新增事实（记忆服务写入后 → 任务线索）
MEMORY_FACT_LEARNED = "bridge_memory_fact_learned"
# 侧写成熟度变化（六层画像证据补齐 → 话术倾向）
PORTRAIT_MATURED = "bridge_portrait_matured"
# NPC/关键人物影响变化（关系星图 → 任务/好感）
NPC_INFLUENCE_CHANGED = "bridge_npc_influence_changed"
# R3.3-79：NPC 对我方立场变化（任务完成 → 该 NPC 立场上修 → 任务/亲密度回写）
NPC_STANCE_CHANGED = "bridge_npc_stance_changed"
# R3.3-79：派系结构更新（立场变化后重算派系 → 下游可观测/再决策）
NPC_FACTION_UPDATED = "bridge_npc_faction_updated"
# R3.3-79：目标家庭上下文更新（父母关系状态/家庭结构 → 中性软提示任务）
FAMILY_CONTEXT_UPDATED = "bridge_family_context_updated"
# R3.3-80：关系复杂度画像更新（A/C/D/E/F 检测结果 → 家庭系统/里程碑/创伤桥消费）
COMPLEXITY_PROFILE_UPDATED = "bridge_complexity_profile_updated"
# R3.3-78：忠诚度（反制线主指标，语义=听话程度）变化 → 桌宠顺从向表现（与好感桥的亲昵载荷刻意区分）
LOYALTY_CHANGED = "bridge_loyalty_changed"


# ---- 载荷 Schema 注册表：event_type -> {required: [...], optional: [...]} ---- #
# 校验只做"必填 key 存在且非 None"的轻量契约，不做深类型校验（保持同步确定性、
# 不引入重依赖）；缺 key 的载荷会被 BridgeBus 拒绝并记一笔去噪指标。
PAYLOAD_SCHEMAS: dict[str, dict] = {
    # 复用存量事件：统一要求 person_id
    AFFECTION_CHANGED: {
        "required": ["person_id"],
        "optional": ["total", "scores", "delta", "reason", "changed_keys"],
    },
    TASKS_COMPLETED: {
        "required": ["person_id"],
        "optional": ["task_id", "title", "reward", "reason", "changed_keys"],
    },
    MEMORY_UPDATED: {
        "required": ["person_id"],
        "optional": ["memory_type", "content", "category", "changed_keys"],
    },
    STRATEGY_CHANGED: {
        "required": ["person_id"],
        "optional": ["strategy_text", "orientation", "changed_keys"],
    },
    PROFILE_UPDATED: {
        "required": ["person_id"],
        "optional": ["layer", "evidence_count", "changed_keys"],
    },
    # 桥接层新增事件
    TASK_COMPLETED_BRIDGE: {
        "required": ["person_id", "task_id"],
        "optional": ["title", "reward", "reason"],
    },
    AFFECTION_LEVEL_UPDATED: {
        "required": ["person_id", "total"],
        "optional": ["level", "stage", "trend", "delta", "reason"],
    },
    COMPASS_RECALIBRATED: {
        "required": ["person_id"],
        "optional": ["aligned", "drift", "gaps", "alignment_score", "trigger"],
    },
    PET_EMOTION_EVENT: {
        "required": ["emotion"],
        "optional": ["person_id", "intensity", "speech", "relationship_stage"],
    },
    PET_USER_INTERACTION: {
        "required": ["person_id", "kind"],
        "optional": ["intensity", "source_event"],
    },
    MEMORY_FACT_LEARNED: {
        "required": ["person_id", "content"],
        "optional": ["memory_type", "category", "importance"],
    },
    PORTRAIT_MATURED: {
        "required": ["person_id"],
        "optional": ["pending_layers", "maturity", "top_quotes"],
    },
    NPC_INFLUENCE_CHANGED: {
        "required": ["person_id"],
        "optional": ["influential", "alert_level", "affection_delta"],
    },
    NPC_STANCE_CHANGED: {
        "required": ["person_id", "npc_name"],
        "optional": ["stance_before", "stance_after", "delta"],
    },
    NPC_FACTION_UPDATED: {
        "required": ["person_id"],
        "optional": ["factions"],
    },
    FAMILY_CONTEXT_UPDATED: {
        "required": ["person_id"],
        "optional": ["family"],
    },
    COMPLEXITY_PROFILE_UPDATED: {
        "required": ["person_id"],
        "optional": ["profile", "family_system", "relationship_state",
                     "rebound_pattern", "condition_gap", "trauma_drivers"],
    },
    LOYALTY_CHANGED: {
        "required": ["person_id", "loyalty"],
        "optional": ["delta", "reason", "route"],
    },
}


def required_keys(event_type: str) -> list[str]:
    """返回某事件的必填载荷 key；未登记的事件返回 []（不强制，向后兼容）。"""
    return list((PAYLOAD_SCHEMAS.get(event_type) or {}).get("required") or [])


def known_events() -> list[str]:
    return sorted(PAYLOAD_SCHEMAS.keys())
