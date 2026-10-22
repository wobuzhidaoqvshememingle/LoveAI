# -*- coding: utf-8 -*-
"""LoveAI v2.0 - 识别后自动出策略+任务+话术完整链路（R3.3-47）。

主函数 `run(chat_snapshot)` 串起：
  1. persona_analyzer / six_layer_persona 识别人格/依恋/操纵
  2. strategy_advisor 出策略（不是只出分析）
  3. task_engine 自动派 1-3 个具体任务（带截止时间、完成条件）
  4. tutorial_engine 给每个任务生成分步教程
  5. strategy_advisor 出 3 条候选话术
  6. digital_twin.predict_reaction 预测对方反应

每个步骤 guarded import：缺服务/抛栈时降级到规则模板，绝不抛栈给上游。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Guarded imports：服务不可用时降级，不抛栈
# ------------------------------------------------------------------ #
def _safe_import(module_path: str, attr: str = "") -> Any:
    try:
        import importlib
        m = importlib.import_module(module_path)
        return getattr(m, attr) if attr else m
    except Exception as e:  # pragma: no cover - 运行期保护
        logger.warning("auto_strategy_pipeline: import %s.%s 失败: %s",
                       module_path, attr, e)
        return None


# 延迟解析，避免 import 期就崩
def _get_persona_analyzer():
    cls = _safe_import("services.persona_analyzer", "PersonaAnalyzer")
    if cls is None:
        return None
    try:
        return cls()
    except Exception as e:
        logger.warning("PersonaAnalyzer 实例化失败: %s", e)
        return None


def _get_strategy_advisor():
    cls = _safe_import("services.strategy_advisor", "StrategyAdvisor")
    if cls is None:
        return None
    try:
        return cls()
    except Exception as e:
        logger.warning("StrategyAdvisor 实例化失败: %s", e)
        return None


def _get_task_engine():
    cls = _safe_import("services.task_engine", "TaskEngine")
    if cls is None:
        return None
    try:
        return cls()
    except Exception as e:
        logger.warning("TaskEngine 实例化失败: %s", e)
        return None


def _get_tutorial_engine():
    cls = _safe_import("services.tutorial_engine", "TutorialEngine")
    if cls is None:
        return None
    try:
        return cls()
    except Exception as e:
        logger.warning("TutorialEngine 实例化失败: %s", e)
        return None


def _get_digital_twin(person_id: str = ""):
    cls = _safe_import("services.digital_twin", "DigitalTwin")
    if cls is None:
        return None
    try:
        return cls(person_id=person_id)
    except Exception:
        try:
            return cls()
        except Exception as e:
            logger.warning("DigitalTwin 实例化失败: %s", e)
            return None


# ------------------------------------------------------------------ #
# 规则兜底（服务全挂时也能出东西）
# ------------------------------------------------------------------ #
_FALLBACK_STRATEGY = {
    "recommended": ["稳定回应", "先听再说", "不追不逼"],
    "forbidden": ["冷战", "翻旧账", "人身攻击"],
    "reasoning": "服务降级：基于通用关系健康原则给出基础建议。",
}

_FALLBACK_TASKS = [
    {"title": "今天主动发一条轻松日常", "deadline": "今天 22:00 前",
     "done_when": "对方至少回复一句（哪怕一个表情）"},
    {"title": "这周约一次线下或语音", "deadline": "本周日 20:00 前",
     "done_when": "敲定具体时间地点"},
    {"title": "记录对方 3 句话并标注情绪", "deadline": "本周六",
     "done_when": "3 条记录入库"},
]

_FALLBACK_CANDIDATES = [
    "今天忙不忙？我刚路过一家店想到你了。",
    "你今天说话比平时短，是不是累了？",
    "有空聊聊吗？不急，你方便的时候。",
]

_FALLBACK_PREDICTION = {
    "emotion": "中性",
    "inner_thought": "他今天没什么压力，正常聊就行。",
    "possible_replies": ["嗯，还好。", "刚忙完，怎么了？"],
    "action": "继续正常对话",
    "confidence": 0.4,
}


# ------------------------------------------------------------------ #
# 主流程
# ------------------------------------------------------------------ #
def run(chat_snapshot: dict) -> dict:
    """识别后自动出完整策略链路。

    chat_snapshot 字段（均可缺）：
      person_id       : str
      recent_chats    : list[dict]，每条至少 {role, text}
      persona         : dict，已有画像（缺则现场跑 persona_analyzer）
      relation_stage  : int 0-10
      affection       : dict
      user_intent     : str，用户当前意图（升温/修复/约见面...）

    Returns:
      {
        persona: dict,
        six_layer: dict,        # 六层画像摘要
        strategy: dict,         # 推荐/禁忌/理由
        tasks: [dict, ...],    # 1-3 个具体任务
        tutorials: {task_title: dict, ...},
        candidates: [str, ...], # 3 条候选话术
        prediction: dict,       # digital_twin 预测
        confidence: float,      # 整体置信度 0-1
        fallbacks_used: [str, ...], # 哪些步骤走了兜底
      }
    """
    chat_snapshot = chat_snapshot or {}
    person_id = str(chat_snapshot.get("person_id", "") or "")
    recent_chats = chat_snapshot.get("recent_chats") or []
    persona_in = chat_snapshot.get("persona") or {}
    relation_stage = chat_snapshot.get("relation_stage", 5)
    affection = chat_snapshot.get("affection") or {}
    fallbacks: list[str] = []

    # ---- 1. 画像：用已有 persona，没有就现场跑 ----
    persona = dict(persona_in) if isinstance(persona_in, dict) else {}
    if not persona:
        analyzer = _get_persona_analyzer()
        if analyzer is not None:
            try:
                persona = analyzer.analyze(person_id,
                                           person_info={"recent_chats": recent_chats}) or {}
            except Exception as e:
                logger.warning("persona_analyzer.analyze 失败: %s", e)
                fallbacks.append("persona")
                persona = {}
        else:
            fallbacks.append("persona")

    # 1.5 六层画像（v3.2.0：走测写凭证链，不再各自直接 to_six_layer().summary()）
    six_layer: dict[str, Any] = {}
    try:
        from services.profiling_chain import run_profiling_chain
        chain_msgs = [
            {"content": str(c.get("text") or c.get("content") or ""),
             "direction": "sent" if c.get("role") == "me" else "received"}
            for c in recent_chats if isinstance(c, dict)
        ]
        chain_res = run_profiling_chain(person_id, chain_msgs,
                                       persona_result=persona or None)
        if chain_res.get("ok"):
            six_layer = chain_res.get("summary") or {}
            six_layer["unmet_layers"] = chain_res.get("unmet_layers", [])
        else:
            logger.error("六层测写链断裂 @ %s: %s",
                         chain_res.get("broken_at"), chain_res.get("reason"))
            six_layer = {"chain_broken": True,
                         "broken_at": chain_res.get("broken_at"),
                         "reason": chain_res.get("reason")}
            fallbacks.append("six_layer_chain_broken")
    except Exception as e:  # 链路径异常 ERROR 上抛，不再静默 warning 吞
        logger.error("六层测写链未预期异常: %s", e)
        six_layer = {"chain_error": True, "error": str(e)}
        fallbacks.append("six_layer")

    # 1.6 NPD/黑暗三联征加权（C12 修复）：
    #     persona_analyzer 容易把 NPD 的「贬低/煤气灯」误判成「抑郁倾向」。
    #     这里对最近聊天做一次 NPD 信号扫描，命中则把 dark_triad 权重抬高，
    #     确保 NPD 不被抑郁倾向顶掉。
    persona = _boost_dark_triad(persona, recent_chats)
    # R3.3-47: 如果 persona_analyzer 直接 error（无聊天记录/分析失败），
    # 但 NPD 信号命中 >=2，直接合成一份最小可用 persona，保证下游能识别 NPD。
    if persona.get("error") and persona.get("_npd_signal_boost"):
        persona = _synthesize_npd_persona(persona, recent_chats)

    # ---- 2. 策略 ----
    advisor = _get_strategy_advisor()
    strategy: dict
    if advisor is not None:
        try:
            strategy = advisor.analyze(persona, relation_stage=relation_stage) or {}
        except Exception as e:
            logger.warning("strategy_advisor.analyze 失败: %s", e)
            fallbacks.append("strategy")
            strategy = dict(_FALLBACK_STRATEGY)
    else:
        fallbacks.append("strategy")
        strategy = dict(_FALLBACK_STRATEGY)

    # R3.3-47: NPD 信号命中时，强制把 manipulation_counter（反制操纵）挂进策略。
    if persona.get("_npd_signal_boost"):
        _inject_manipulation_counter(strategy, persona)

    # ---- 3. 任务（必须出 2-3 条，C12 修复：不能只出 1 条）----
    tasks: list[dict]
    te = _get_task_engine()
    if te is not None:
        try:
            raw_tasks = te.generate_main_tasks(person_id, stage=relation_stage) or []
            tasks = [_normalize_task(t) for t in raw_tasks[:3]]
            if not tasks:
                raise ValueError("empty")
        except Exception as e:
            logger.warning("task_engine 失败: %s", e)
            fallbacks.append("tasks")
            tasks = [dict(t) for t in _FALLBACK_TASKS]
    else:
        fallbacks.append("tasks")
        tasks = [dict(t) for t in _FALLBACK_TASKS]
    # 不足 2 条时用兜底任务补齐到 2-3 条（验收硬要求：不能只出 1 条）
    if len(tasks) < 2:
        have = {t.get("title") for t in tasks}
        for fb in _FALLBACK_TASKS:
            if len(tasks) >= 3:
                break
            if fb["title"] not in have:
                tasks.append(dict(fb))
    if len(tasks) < 2:
        tasks = [dict(t) for t in _FALLBACK_TASKS[:2]]

    # ---- 4. 教程 ----
    tutorials: dict[str, dict] = {}
    tute = _get_tutorial_engine()
    if tute is not None:
        for t in tasks:
            title = t.get("title", "")
            try:
                g = tute.generate_guide(
                    question=f"如何完成：{title}",
                    mode="target",
                    person_id=person_id,
                )
                if g:
                    tutorials[title] = g
            except Exception as e:
                logger.warning("tutorial_engine 任务[%s] 失败: %s", title, e)
                tutorials[title] = {"fallback": True,
                                    "steps": [t.get("done_when", "见任务")]}
    else:
        fallbacks.append("tutorials")
        for t in tasks:
            tutorials[t.get("title", "")] = {
                "fallback": True,
                "steps": [
                    "第一步：别慌，先看任务目标。",
                    f"第二步：照做——{t.get('title','')}",
                    f"第三步：完成标准：{t.get('done_when','')}",
                ],
            }

    # ---- 5. 候选话术 3 条 ----
    if persona.get("_npd_signal_boost"):
        candidates = _build_npd_candidates(persona, recent_chats)
    else:
        candidates = _build_candidates(strategy, chat_snapshot)

    # ---- 6. 预测对方反应 ----
    dt = _get_digital_twin(person_id=person_id)
    prediction: dict
    primary_action = candidates[0] if candidates else "随便聊聊"
    if dt is not None:
        try:
            prediction = dt.predict_reaction(
                user_action=primary_action,
                recent_chats=recent_chats,
                affection=affection,
                context_hint=strategy.get("reasoning", ""),
            ) or {}
            if not prediction:
                raise ValueError("empty")
        except Exception as e:
            logger.warning("digital_twin.predict_reaction 失败: %s", e)
            fallbacks.append("prediction")
            prediction = dict(_FALLBACK_PREDICTION)
    else:
        fallbacks.append("prediction")
        prediction = dict(_FALLBACK_PREDICTION)

    # ---- 置信度：按兜底步骤数衰减 ----
    base_conf = 0.85
    base_conf -= 0.1 * len(fallbacks)
    # 六层画像成熟度也参与
    sl_maturity = six_layer.get("maturity")
    if isinstance(sl_maturity, (int, float)):
        base_conf = (base_conf + float(sl_maturity)) / 2
    confidence = round(max(0.1, min(1.0, base_conf)), 3)

    return {
        "persona": persona,
        "six_layer": six_layer,
        "strategy": strategy,
        "tasks": tasks,
        "tutorials": tutorials,
        "candidates": candidates,
        "prediction": prediction,
        "confidence": confidence,
        "fallbacks_used": fallbacks,
    }




# ------------------------------------------------------------------ #
# R3.3-47 NPD 专用合成：persona_analyzer 失败时直接造一份可用 persona
# ------------------------------------------------------------------ #
def _synthesize_npd_persona(persona: dict, recent_chats: list) -> dict:
    """persona_analyzer 返回 error 但 NPD 信号命中时，现场造一份最小 persona。

    让 dark_triad 分数、primary_key、操控标签都真有值，不依赖 17 维分析。
    """
    hits = persona.get("_npd_signal_hits") or []
    n = len(hits)
    # 命中越多分数越高，封顶 0.95
    score = round(min(0.95, 0.45 + n * 0.06), 3)
    synth = {
        "person_id": persona.get("person_id", ""),
        "primary_key": "npd",
        "primary_personality": "自恋型(NPD)",
        "primary_label": "自恋型人格倾向（贬低/煤气灯/合理化）",
        "primary_score": score,
        "psychology": {
            "dark_triad_tendencies": {
                "narcissism": score,
                "machiavellianism": round(max(0.3, score - 0.1), 3),
                "psychopathy": round(max(0.2, score - 0.15), 3),
                # 中文键：让下游 manipulation_coach._detect_patterns 真的命中
                # （它查的是「自恋/操纵/辱骂贬低」等中文键，之前只有英文键，
                #  导致 build_counter_manipulation 永不触发、manipulation=False）。
                "自恋": score,
                "操纵": round(max(0.3, score - 0.1), 3),
                "辱骂贬低": bool(hits),
                "煤气灯贬低": any(
                    h in ("我从没说过", "你想多了", "你太敏感", "你有病") for h in hits
                ),
            },
            "defensive_communication": {
                "煤气灯贬低": any(
                    h in ("我从没说过", "你想多了", "你太敏感") for h in hits
                ),
                "贬低感受": any(
                    h in ("都是你的错", "你不配", "你太敏感", "你有病") for h in hits
                ),
            },
        },
        "manipulation_risk": True,
        "manipulation_signals": hits,
        "_npd_signal_boost": True,
        "_npd_signal_hits": hits,
        "_synthesize": True,
    }
    # 保留原有的风险警告
    for k in ("risk_warnings",):
        if persona.get(k):
            synth[k] = persona[k]
    return synth


def _inject_manipulation_counter(strategy: dict, persona: dict) -> None:
    """NPD 命中时，把反制操纵策略挂进 strategy['manipulation_counter']。"""
    if not isinstance(strategy, dict):
        return
    hits = persona.get("_npd_signal_hits") or []
    counter = {
        "triggered": True,
        "detected_signals": hits,
        "principles": [
            "不接贬低：对方说'你太敏感/都是你的错'时，不辩解、不道歉，用'这是你的看法，我有我的感受'划界。",
            "识别煤气灯：对'我从没说过'类否认，不翻旧账纠缠，直接引用原话截图/记录。",
            "合理化拆穿：对方说'我这是为你好'时，区分动机与效果——效果是让你内耗，就不是为你好。",
            "收回情绪供应：NPD 靠贬低后再捧来控制，停止主动求和/解释，给关系降频。",
        ],
        "do_not": [
            "不要在被贬低后立刻道歉/解释/自证",
            "不要独自承担'都是你的错'",
            "不要相信'只有我能忍你/离开我没人要'类孤立话术",
        ],
    }
    strategy["manipulation_counter"] = counter
    # 把反制原则也追加到 recommended，让上层 UI 能看到
    rec = strategy.get("recommended")
    if isinstance(rec, list):
        for tip in [
            "反制煤气灯：保留原话记录，被否认时直接引用，不情绪化争辩。",
            "划界：被贬低时用'我不接受这种说法'终止话题，不解释自证。",
            "降频：减少主动联系次数，观察对方是否调整，再决定下一步。",
        ]:
            if tip not in rec:
                rec.append(tip)


def _build_npd_candidates(persona: dict, recent_chats: list) -> list:
    """NPD 场景下出 3 条具体可复制的反制话术（不是原则性的'你应该…'）。

    每条都是能直接发给对方的成句，覆盖：划界 / 不接贬低 / 引用原话。
    """
    return [
        "你刚才说「都是我的错」，我不这么认为。这件事我们各有各的责任，我不想一个人背。",
        "我有我的感受，你可以不同意，但我不接受「你太敏感」这种说法。我们先停一下，改天再聊。",
        "你上周明明说过……（引用对方原话）。我不是翻旧账，只是不希望我们说完就当没发生过。",
    ]


def _normalize_task(t: dict) -> dict:
    """把 task_engine 输出统一成 {title, deadline, done_when}。"""
    if not isinstance(t, dict):
        return {"title": str(t), "deadline": "", "done_when": ""}
    return {
        "title": t.get("title") or t.get("name") or "未命名任务",
        "deadline": t.get("deadline") or t.get("due") or t.get("time") or "",
        "done_when": t.get("done_when") or t.get("completion")
        or t.get("success_criteria") or "",
        "raw": t,
    }


def _build_candidates(strategy: dict, snapshot: dict) -> list[str]:
    """出 3 条候选话术（C12 修复：从话术库匹配 + refine，不再大杂烩拼接）。

    流程：
      1. 把 user_intent / 场景关键词映射到 script_library 的场景；
      2. pick_scripts 挑 3 条；
      3. refine_script 按 context（称呼/上次话题/对方情绪）真微调；
      4. 迷信命中仍叠加 playalong 话术（保留）。
    """
    intent = snapshot.get("user_intent", "") or ""
    # 意图关键词 → 话术库场景映射
    scene = _intent_to_scene(intent, strategy)
    cands: list[str] = []
    try:
        from services.script_library import pick_scripts, refine_script
        ctx = _build_refine_context(snapshot)
        picked = pick_scripts(scene, k=3)
        for tpl in picked:
            cands.append(refine_script(tpl, ctx))
    except Exception as e:  # noqa: BLE001
        logger.warning("script_library 话术选取失败，回退兜底: %s", e)

    # 迷信借力打力（如果对方最近提到迷信，仍叠加一条）
    try:
        sp = _safe_import("services.superstition_playalong")
        if sp is not None:
            for msg in (snapshot.get("recent_chats") or [])[-5:]:
                txt = msg.get("text") if isinstance(msg, dict) else str(msg)
                hit = sp.maybe_playalong(txt or "", user_intent=intent)
                if hit:
                    for c in (hit.get("candidates") or [])[:1]:
                        if c and c not in cands:
                            cands.append(c)
                    break
    except Exception as e:
        logger.warning("superstition_playalong 接入失败: %s", e)

    # 补齐到 3 条
    for fb in _FALLBACK_CANDIDATES:
        if len(cands) >= 3:
            break
        if fb not in cands:
            cands.append(fb)
    return cands[:3]


# 意图/策略 → 话术库场景
_INTENT_SCENE_MAP = [
    (("升温", "推进", "暧昧", "拉近"), "升温"),
    (("约", "见面", "吃饭", "看电影", "邀约"), "邀约"),
    (("复合", "和好", "修复", "吵架", "挽回"), "修复"),
    (("表白", "确认关系", "在一起", "名分"), "确认关系"),
    (("分手", "边缘", "想分"), "分手边缘"),
    (("拒绝", "不行", "不答应"), "拒绝"),
    (("爽约", "放鸽子", "解释"), "爽约"),
    (("关心", "累", "心情"), "关心"),
    (("幽默", "逗", "轻松"), "幽默"),
    (("早安", "早上"), "早安"),
    (("晚安", "睡"), "晚安"),
]


def _intent_to_scene(intent: str, strategy: dict) -> str:
    """把用户意图/策略方向映射到话术库场景；默认'升温'。"""
    text = (intent or "") + " " + str(strategy.get("recommended", ""))
    for keys, scene in _INTENT_SCENE_MAP:
        if any(k in text for k in keys):
            return scene
    return "升温"


def _build_refine_context(snapshot: dict) -> dict:
    """从 chat_snapshot 提取 refine_script 需要的 context。"""
    chats = snapshot.get("recent_chats") or []
    # 最近一条对方消息作为 last_topic 线索
    last_topic = ""
    for m in reversed(chats[-5:]):
        if isinstance(m, dict) and m.get("role") in ("other", "对方", "she", "her"):
            last_topic = (m.get("text") or "")[:20]
            break
    if not last_topic and chats:
        last = chats[-1]
        last_topic = (last.get("text") or "")[:20] if isinstance(last, dict) else str(last)[:20]
    return {
        "nickname": snapshot.get("nickname", ""),
        "pet_name": snapshot.get("pet_name", ""),
        "last_topic": last_topic,
        "mood": snapshot.get("mood", ""),
    }


# NPD / 黑暗三联征关键词（命中即抬高权重，防止被抑郁倾向顶掉）
_NPD_SIGNALS = (
    "你太敏感", "都是你的错", "我从没说过", "你不配", "你想多了",
    "不是我", "你有病", "你是不是有问题", "我这是为你好", "你离不开我",
    "你朋友都不了解你", "只有我能忍你", "你要是离开我就没人要了",
    "贬低", "煤气灯", "操控", "PUA",
)


def _boost_dark_triad(persona: dict, recent_chats: list) -> dict:
    """扫描最近聊天的 NPD/煤气灯信号，命中则抬高 dark_triad 权重。

    不重写 persona_analyzer，只在结果上叠加：
      - 命中≥2 个信号 → persona['_npd_signal_boost'] = True；
      - 把 dark_triad_tendencies 的自恋/权术分 ×1.5；
      - 防止 primary 被抑郁倾向顶掉：若已判为抑郁倾向但 NPD 信号强，
        加一条 warning 提示「可能是 NPD 贬低伪装成的抑郁」。
    """
    if not isinstance(persona, dict):
        return persona
    joined = "\n".join(
        (m.get("text") if isinstance(m, dict) else str(m)) or ""
        for m in (recent_chats or [])[-30:]
    )
    hits = [kw for kw in _NPD_SIGNALS if kw in joined]
    if len(hits) < 2:
        return persona

    persona["_npd_signal_boost"] = True
    persona["_npd_signal_hits"] = hits
    # 抬高黑暗三联征分数
    psy = persona.get("psychology")
    if isinstance(psy, dict):
        dt = psy.get("dark_triad_tendencies")
        if isinstance(dt, dict):
            for k in ("自恋", "权术", "心理变态", "narcissism", "machiavellianism"):
                if k in dt:
                    try:
                        dt[k] = round(float(dt[k]) * 1.5, 3)
                    except (TypeError, ValueError):
                        pass
    # 若 primary 已被判成抑郁倾向，加防顶掉提示
    primary = str(persona.get("primary_key", "") or persona.get("primary_personality", ""))
    if "depress" in primary.lower() or "抑郁" in primary:
        persona.setdefault("risk_warnings", []).append(
            f"⚠ NPD 信号扫描命中 {len(hits)} 个（{hits[:3]}）："
            f"当前判为「抑郁倾向」可能是 NPD 煤气灯/贬低造成的继发性低落，"
            f"请勿只按抑郁处理，优先核对操控特征。"
        )
        persona["_npd_overrides_depression"] = True
    return persona


# ------------------------------------------------------------------ #
# 懒接线到 direction_compass
# ------------------------------------------------------------------ #
def attach_to_direction_compass() -> None:
    """把 auto_strategy 入口挂到 DirectionCompass 上（懒接线，可重复调用）。"""
    try:
        from services import direction_compass as dc
    except Exception as e:  # pragma: no cover
        logger.warning("attach_to_direction_compass: import direction_compass 失败: %s", e)
        return
    if hasattr(dc.DirectionCompass, "auto_strategy"):
        return

    def auto_strategy(self, chat_snapshot: dict) -> dict:
        return run(chat_snapshot or {})

    dc.DirectionCompass.auto_strategy = auto_strategy  # type: ignore[attr-defined]


# import 期自动挂一次
attach_to_direction_compass()


__all__ = ["run", "attach_to_direction_compass"]
