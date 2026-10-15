# -*- coding: utf-8 -*-
"""
R3.3-47 行动面板：把识别→策略→任务→话术→风险汇总成一个用户可直接看的输出。
输入：orchestrator.orchestrate() 的返回结果
输出：{strategy, tasks[3], scripts[3], risk, summary}
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)


# 按依恋/人格类型给的3条基础话术模板
_SCRIPT_TEMPLATES = {
    "avoidant": [
        "（轻松分享）刚路过一家很有意思的店，拍给你看看",
        "（低压力）不着急回，你忙完再说",
        "（给空间）今天天气不错，出去走了走，想起上次你说的那家咖啡馆",
    ],
    "anxious": [
        "（主动表达）今天发生了一件事，第一时间就想告诉你",
        "（确定感）我在呢，有什么事直接说",
        "（持续确认）周末想见你，你时间方便吗",
    ],
    "fearful": [
        "（慢节奏）不急，我们慢慢来，你觉得舒服最重要",
        "（一致性）上次说的那家店，我查好了地址，这周末有空就去",
        "（不逼问）你不想说的事没关系，等你愿意说的时候我在听",
    ],
    "npd": [
        "（不接招）你说的有道理，不过我想先了解一下你的想法",
        "（设边界）我理解你的感受，但这件事我需要按自己的节奏来",
        "（不证明爱）感情不是靠转账证明的，我更看重平时怎么相处",
    ],
    "bpd": [
        "（稳定回应）我在，没有不理你",
        "（安抚）刚才那件事我不是那个意思，我说清楚",
        "（不被拉扯）我们先冷静一下，半小时后再聊",
    ],
    "flirting": [
        "（轻松）刚看到一个东西特别像你",
        "（推拉）你今天那句话我想了一整天",
        "（邀约）周末有个展，要不要一起去看",
    ],
    "money_danger": [
        "（不接招）这个金额我现在确实有困难",
        "（设边界）我希望我们的关系不是建立在钱上的",
        "（转方向）不过我知道一家性价比很高的店，下次可以一起去",
    ],
    "default": [
        "（轻松开场）今天怎么样",
        "（关心）忙不忙，记得吃饭",
        "（分享）刚看到个有意思的，分享给你",
    ],
}


def build_action_panel(orch_result: dict, persona: dict | None = None) -> dict:
    """从 orchestrator 结果聚合行动面板。

    Args:
        orch_result: AnalysisOrchestrator.orchestrate() 返回的完整dict
        persona: 可选，persona_analyzer 的画像结果

    Returns:
        {
            "summary": 一句话总结,
            "identification": 识别结论（场景+人格倾向）,
            "strategy": {"recommended": [...], "forbidden": [...]},
            "tasks": [3条],
            "scripts": [3条具体话术],
            "risk_warning": "1条风险提示" 或 "",
        }
    """
    persona = persona or {}
    if not isinstance(orch_result, dict):
        return {"summary": "分析结果为空", "identification": {}, "strategy": {}, "tasks": [], "scripts": _SCRIPT_TEMPLATES["default"], "risk_warning": ""}

    # ---- 识别结论 ----
    scene = orch_result.get("scene", {})
    scene_name = scene.get("name", "")
    scene_conf = scene.get("confidence", 0)

    personality = orch_result.get("personality", {})
    primary_tendency = personality.get("primary") if isinstance(personality, dict) else None
    tendency_label = ""
    if primary_tendency:
        tendency_label = primary_tendency.get("label", "") if isinstance(primary_tendency, dict) else str(primary_tendency)

    identification_parts = []
    if scene_name:
        identification_parts.append(f"场景：{scene_name}（置信度{scene_conf:.0%}）")
    if tendency_label:
        identification_parts.append(f"倾向：{tendency_label}")

    # ---- 策略 ----
    strategy = orch_result.get("strategy", {}) or {}
    recs = strategy.get("recommended", []) or []
    forbids = strategy.get("forbidden", []) or []

    # ---- 任务（取前3）----
    raw_tasks = orch_result.get("tasks", []) or []
    tasks: list[dict] = []
    for t in raw_tasks[:3]:
        if isinstance(t, dict):
            tasks.append({
                "title": t.get("title", t.get("action", "")),
                "source": t.get("source", "analysis"),
            })
        elif isinstance(t, str):
            tasks.append({"title": t, "source": "analysis"})
    # 如果orchestrator任务为空，用personality_strategy的fallback任务补
    if not tasks and persona:
        try:
            from services.personality_strategy import build_tasks
            _, ptasks = build_tasks(persona)
            for t in (ptasks or [])[:3]:
                if isinstance(t, str):
                    tasks.append({"title": t, "source": "personality"})
                elif isinstance(t, dict):
                    tasks.append({"title": t.get("title", str(t)), "source": "personality"})
        except Exception:
            pass

    # ---- 话术（3条）----
    # 根据场景/倾向选模板
    script_key = "default"
    if "金钱" in scene_name or "money" in scene_name.lower():
        script_key = "money_danger"
    elif tendency_label:
        tl = tendency_label.lower()
        if "自恋" in tendency_label or "npd" in tl:
            script_key = "npd"
        elif "边缘" in tendency_label or "bpd" in tl:
            script_key = "bpd"
        elif "回避" in tendency_label:
            script_key = "avoidant"
        elif "焦虑" in tendency_label:
            script_key = "anxious"
        elif "恐惧" in tendency_label:
            script_key = "fearful"
    elif "暧昧" in scene_name:
        script_key = "flirting"

    scripts = list(_SCRIPT_TEMPLATES.get(script_key, _SCRIPT_TEMPLATES["default"]))

    # ---- 风险提示 ----
    risk_warnings = orch_result.get("risk_warnings", []) or []
    risk_text = ""
    if risk_warnings:
        risk_text = risk_warnings[0] if isinstance(risk_warnings[0], str) else str(risk_warnings[0])

    # 检查 financial_analyzer 是否标记了危险
    manipulation = orch_result.get("manipulation", {})
    if isinstance(manipulation, dict) and manipulation.get("counter_strategy"):
        risk_text = (risk_text + " " if risk_text else "") + "已识别操纵模式，建议按反制策略应对"

    # ---- 一句话总结 ----
    summary_parts = []
    if scene_name:
        summary_parts.append(f"当前判断为「{scene_name}」")
    if tendency_label:
        summary_parts.append(f"对方有{tendency_label}倾向")
    if recs:
        summary_parts.append(f"建议：{recs[0]}")
    if risk_text:
        summary_parts.append(f"⚠️{risk_text[:50]}")

    return {
        "summary": "，".join(summary_parts) if summary_parts else "分析完成",
        "identification": {
            "scene": scene_name,
            "scene_confidence": scene_conf,
            "personality_tendency": tendency_label,
        },
        "strategy": {
            "recommended": recs[:5],
            "forbidden": forbids[:5],
        },
        "tasks": tasks[:3],
        "scripts": scripts[:3],
        "risk_warning": risk_text,
    }
