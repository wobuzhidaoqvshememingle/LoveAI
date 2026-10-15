# -*- coding: utf-8 -*-
"""twin_sim_pipeline.py — 拟真增强 E9-E13：事件驱动连续模拟流水线。

四阶段状态机：life_progression(生活推进) -> perception(是否看见) ->
decision(结构化决定) -> render(渲染/沉默/延迟)。状态会话内保持、可持久化续跑。

修复版要点（验收 P1-11）：
- 反应分布 / decision reason 随【我方具体那句话】变化（意图+话题兴趣+输入哈希抖动），
  不再是"场景查表常量"。
- 渲染真正回应消息意图：忙->报状态+何时回；道歉->接台阶务实恢复；礼物->对礼物反应。
- 表层指纹从本人真实回复学习（句长/口癖/语气词/标点）：短直人格不加"呀/呢/啦"filler。
- 示例池扩量并跨轮去重；叙事不逐字重复；deflect 渲染是真跳脱/反问，不黏人错位。
- 离线确定性兜底：三人设即使全离线也区分鲜明。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, Optional, List

# ===================================================================== #
# 配置默认先验（E12 / E13；冷启动先验，预留 T 校准）
# ===================================================================== #
DEFAULT_CONFIG: Dict[str, Any] = {
    "baseThreshold": 10, "densityFactor": 0.3,
    "sameDirectionBoost": 0.05, "oppositeDecay": 0.15,
    "minWeight": 0.2, "maxIntensity": 2.0,
    "willingnessThreshold": 0.5, "maxProactivePerDay": 3,
    "scheduleLoadLimit": 0.6, "privacyNeedLimit": 0.6,
}
CALIBRATION_HOOK: Dict[str, Any] = {"calibrated": False}

EVENT_USER_MESSAGE = "user_message"
EVENT_FOLLOW_UP = "follow_up"
EVENT_INTENT_EXPIRY = "intent_expiry"
EVENT_LIFE_TICK = "life_tick"
MODES = ("reply", "silent", "delay", "deflect", "proactive", "idle")

Y_KEYS = ("Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7")
Y_LABELS = {"Y1": "积极推进", "Y2": "正向接纳", "Y3": "中性", "Y4": "敷衍回避",
            "Y5": "防御冲突", "Y6": "已读不回", "Y7": "未读/无回应"}
DELAY_BUCKETS = ("<=5min", "5-30min", "30min-2h", "2h-12h", "12h-48h", ">48h")

_SCENE_WORDS = {
    "invite": ["吃饭", "见面", "周末", "有空", "约", "出来", "一起", "电影", "逛", "看你", "看纪录", "散步", "想有"],
    "emotion": ["累", "难过", "不开心", "烦", "委屈", "伤心", "压力", "心情不好", "难受", "想哭"],
    "conflict": ["生气", "分手", "到底", "你又", "错", "过分", "失望", "怪", "吵架", "不理我", "不把我放心上"],
    "praise": ["厉害", "棒", "好看", "优秀", "帅", "美", "想你", "喜欢你", "爱你"],
    "question": ["怎么", "什么", "哪", "谁", "是不是", "吗", "？", "?"],
    "daily": [],
}

# 我方消息的细粒度意图（比 scene 更贴具体话术）
_RE_BUSY = re.compile(r"(在忙吗|忙吗|在吗|干嘛呢|在干嘛|忙不忙|还在忙)")
_RE_APOLOGY = re.compile(r"(对不起|抱歉|是我(语气|不对|错)|我错了|我的问题|怪我|我急了)")
_RE_GIFT = re.compile(r"(给你带|送你|给你买|爱喝的|爱吃的|礼物|带了|买了)")


def _intent_of(text: str) -> Optional[str]:
    t = text or ""
    if _RE_BUSY.search(t):
        return "busy_probe"
    if _RE_APOLOGY.search(t):
        return "apology"
    if _RE_GIFT.search(t):
        return "gift"
    return None


# ===================================================================== #
# 人设规格 + 表层指纹学习
# ===================================================================== #
def learn_verbosity(own_messages: List[str]) -> dict:
    """从本人真实回复学习表层指纹：句长 / 语气词 / 标点倾向。"""
    msgs = [m.strip() for m in (own_messages or []) if m and m.strip()]
    if not msgs:
        return {"verbosity": "normal", "mean_len": None, "used_fillers": set()}
    lens = [len(m) for m in msgs]
    mean_len = sum(lens) / len(lens)
    particles = ["呀", "呢", "啦", "哦", "哈", "嘛", "哇", "呗", "哟"]
    joined = "".join(msgs)
    used = {p for p in particles if p in joined}
    density = sum(joined.count(p) for p in particles) / max(1, len(joined))
    if mean_len <= 12 and density < 0.02:
        verbosity = "terse"          # 短直：不加语气词
    elif density > 0.05:
        verbosity = "warm"
    else:
        verbosity = "normal"
    return {"verbosity": verbosity, "mean_len": round(mean_len, 1),
            "used_fillers": used, "filler_density": round(density, 4)}


def derive_persona_spec(profile: Optional[dict], own_messages: Optional[List[str]] = None) -> dict:
    profile = profile or {}
    att = profile.get("attachment", {}) or {}
    atype = att.get("type") or att.get("primary_type") or ""
    avoid = float(att.get("avoidance_score", 50) or 50)
    anx = float(att.get("anxiety_score", 50) or 50)
    psych = (profile.get("psychology", {}) or {}).get("overall_health", "") or ""
    dark = (profile.get("psychology", {}) or {}).get("dark_triad_tendencies", {}) or {}
    npd_like = ("自恋" in dark) or (avoid >= 60 and anx < 40)

    if "安全" in atype:
        style = "secure"
    elif ("回避" in atype) or ("疏离" in atype) or avoid >= 60:
        style = "avoid"
    elif "焦虑" in atype or anx >= 60:
        style = "anxious"
    else:
        style = "avoid" if (avoid >= 60 and anx < 40) else ("anxious" if anx >= 60 else "secure")

    comm = profile.get("communication", {}) or {}
    conflict = profile.get("conflict_style", {}) or {}
    verb = learn_verbosity(own_messages or [])
    return {
        "person_id": profile.get("person_id", "对方"),
        "style": style, "npd_like": bool(npd_like),
        "avoid": avoid, "anx": anx,
        "communication": comm.get("pattern", ""),
        "conflict": conflict.get("primary", ""), "psych_health": psych,
        "verbosity": verb["verbosity"], "mean_len": verb["mean_len"],
        "used_fillers": verb["used_fillers"],
    }


# ===================================================================== #
# 确定性语料库（人设一致 + 意图感知 + 短直变体）
# ===================================================================== #
# key = 意图/场景；每条为高概率真实风格回复
_REPLY_BANK: Dict[str, Dict[str, list]] = {
    "secure": {
        "invite": ["可以啊，你定时间，我都行。", "周末我没安排，你想去哪？", "行啊，几点？",
                   "那定吧，提前跟我说一声。", "好啊，听你安排。"],
        "emotion": ["怎么啦？跟我说说，我听着呢。", "别一个人扛着，慢慢说。"],
        "conflict": ["先别急，把事情说清楚就好。", "我觉得先各自冷静下，再聊。"],
        "praise": ["哈哈，被你这么一说还挺开心。", "谢谢，你也不错。"],
        "question": ["我想想，我觉得可以。", "嗯，我是这么看的。"],
        "daily": ["刚忙完，正想找你。", "在呢，怎么了？"],
        "deflect": ["哈哈辛苦啦，先吃饭，吃完再说。", "你先忙你的，我这边也行。"],
        "busy_probe": ["在呢，刚开完个短会，十分钟后跟你说。", "在改东西，稍晚回你。"],
        "apology": ["没事，说开了就好，晚上再聊。", "嗯，过去就翻篇了。"],
        "gift": ["还记着这个，谢啦。下次别破费。", "哟，有心了，谢了。"],
    },
    "avoid": {
        "invite": ["再说吧，最近挺忙的。", "看情况，到时候再说。"],
        "emotion": ["哦，注意休息。", "嗯，知道了。"],
        "conflict": ["随你怎么想。", "我懒得解释。"],
        "praise": ["哦，谢谢。", "嗯。"],
        "question": ["我哪知道，你自己看着办。", "大概吧。"],
        "daily": ["嗯。", "在忙。"],
        "deflect": ["哦，忙着呢，先不说了。", "回头再说。"],
        "busy_probe": ["在忙，晚点说。", "手上有事，先这样。"],
        "apology": ["过去了，别提了。", "嗯，知道了。"],
        "gift": ["哦，谢了。", "嗯，费心了。"],
    },
    "anxious": {
        "invite": ["真的吗？你不会放我鸽子吧？", "好啊好啊！几点来接我？"],
        "emotion": ["你怎么了？别吓我啊！", "是不是我哪里没做好？"],
        "conflict": ["你是不是想跟我分手？你说话啊！", "你别不理我。"],
        "praise": ["真的吗？你不是哄我开心吧？", "那你以后只能对我这样说。"],
        "question": ["你说呀，我在听呢！", "为什么？到底怎么回事？"],
        "daily": ["你在干嘛呀？怎么才回我？", "我等你半天了！"],
        # 真·跳脱/反问：把话题抛回，而不是黏人
        "deflect": ["你今天话好少，是不是有心事？", "哎，先不说我了，你那边怎样？"],
        "busy_probe": ["在！怎么了？是不是找我有事？", "在的在的，你说。"],
        "apology": ["你知道就好，我刚才真有点委屈。", "算了，下次别这样了。"],
        "gift": ["真的给我带的？你怎么突然对我这么好。", "哇，谢谢！你怎么知道我喜欢这个。"],
    },
}

# 短直（terse）变体：句短、无语气词，贴合 p_slowwarm 这类务实短直人格
_TERSE_BANK: Dict[str, list] = {
    "invite": ["可以，你定时间。", "行，几点？"],
    "daily": ["在忙，晚点说。", "刚忙完。"],
    "busy_probe": ["在改bug，刚开完会，晚点细说。", "在忙，手头走不开。"],
    "apology": ["嗯，没事。说开了就行。", "过去了，别想了。"],
    "gift": ["还记着这个，谢了。下次别买了。", "有心了，谢。"],
    "emotion": ["怎么了，说。", "别硬扛。"],
    "conflict": ["先冷静，回头说。", "说开了就行。"],
    "deflect": ["你先忙，回头聊。", "嗯，知道了。"],
}

_LIFE_ACTIVITY = {
    "secure": ["刚下班到家，正在煮一碗面，顺手刷了下手机。",
               "在公司赶完一个方案，伸了个懒腰，端起水杯。",
               "周末下午，在阳台收拾东西，手机放在桌上。",
               "刚跑完步，坐在江边喝水，风挺大。",
               "午休吃完饭，在楼下走了两圈。",
               "晚上窝在沙发上看剧，手机搁腿上。",
               "周末早上醒得早，在厨房冲了杯咖啡。",
               "刚从超市拎着菜回来，把袋子搁桌上。"],
    "avoid": ["在工作室对着电脑，手头一摊事，微信搁一边没顾上看。",
              "刚开完个长会，耳机还没摘，懒得切到聊天界面。",
              "在外面办事，手上拎着东西，没腾出手机。",
              "下午在健身房练器械，手机锁柜子里。",
              "开车堵在路上，手机架着没看消息。"],
    "anxious": ["手机攥在手里，隔两分钟就点开对话框看你有没有回。",
                "坐立不安地刷着手机，屏幕一亮就赶紧抓起来。",
                "一边做晚饭一边瞟手机，就等你那条消息。",
                "躺在床上翻来覆去，把对话框开了又关。",
                "吃饭也没胃口，筷子搁着一直看屏幕。"],
}
_MOOD_TINT = {
    "secure": ["心里挺平稳的", "状态放松", "心情不错", "节奏正常", "有点小期待", "慢悠悠的"],
    "avoid": ["淡淡的，没什么波澜", "有点烦，想自己待着", "提不起劲", "懒得理人"],
    "anxious": ["心里七上八下", "隐隐有点不安", "盯着手机等你", "患得患失"],
}
_ATMO_SERIOUS = ["气氛有些沉，空气里绷着一根弦。", "话题的分量变重了，两人都收着。"]
_ATMO_RELAXED = ["气氛松快下来，闲聊变得随意。", "像是卸下点什么，语气轻了。"]

_BASE_DIST = {
    "secure":  {"Y1": 0.22, "Y2": 0.34, "Y3": 0.22, "Y4": 0.09, "Y5": 0.05, "Y6": 0.05, "Y7": 0.03},
    "avoid":   {"Y1": 0.04, "Y2": 0.10, "Y3": 0.16, "Y4": 0.30, "Y5": 0.06, "Y6": 0.22, "Y7": 0.12},
    "anxious": {"Y1": 0.34, "Y2": 0.20, "Y3": 0.14, "Y4": 0.05, "Y5": 0.16, "Y6": 0.09, "Y7": 0.02},
}
_BASE_DELAY = {
    "secure":  [0.45, 0.35, 0.12, 0.06, 0.02, 0.00],
    "avoid":   [0.10, 0.20, 0.30, 0.25, 0.10, 0.05],
    "anxious": [0.55, 0.25, 0.12, 0.05, 0.02, 0.01],
}


def _detect_scene(text: str) -> str:
    t = text or ""
    for scene in ("invite", "emotion", "conflict", "praise", "question"):
        if any(w in t for w in _SCENE_WORDS[scene]):
            return scene
    return "daily"


def _hash01(seed_str: str) -> float:
    return int(hashlib.md5(seed_str.encode("utf-8")).hexdigest(), 16) % 1000 / 1000.0


def _seeded_pick(seed_str: str, items: list) -> Any:
    """确定性选取：按 seed 哈希，离线可复现。"""
    if not items:
        return ""
    h = int(hashlib.md5(seed_str.encode("utf-8")).hexdigest(), 16)
    return items[h % len(items)]


def _norm_dist(d: Dict[str, float]) -> Dict[str, float]:
    total = sum(max(0.0, float(v)) for v in d.values())
    if total <= 0:
        n = 1.0 / len(Y_KEYS)
        return {k: n for k in Y_KEYS}
    return {k: max(0.0, float(d.get(k, 0.0))) / total for k in Y_KEYS}


def _strip_unused_fillers(text: str, used_fillers: set) -> str:
    """短直人格：把他从不用的语气词删掉，不凭空加口癖。"""
    if not used_fillers:
        for p in ["呀", "呢", "啦", "哈", "嘛", "哇", "呗"]:
            text = text.replace(p, "")
    return text


# ===================================================================== #
class TwinSimPipeline:
    def __init__(self, person_id: str, persona_profile: Optional[dict] = None,
                 data_dir: Optional[str] = None, llm: Any = None,
                 config: Optional[dict] = None, own_messages: Optional[List[str]] = None) -> None:
        self.person_id = person_id
        self.llm = llm
        self.cfg = dict(DEFAULT_CONFIG)
        if config:
            self.cfg.update(config)
        self.profile = persona_profile
        self.own_messages = own_messages or []
        if self.profile is None and data_dir:
            self.profile = self._load_profile(data_dir, person_id)
        self.spec = derive_persona_spec(self.profile, self.own_messages)

    @staticmethod
    def _load_profile(data_dir: str, person_id: str) -> Optional[dict]:
        for p in (os.path.join(data_dir, "persons", person_id, "persona_profile.json"),
                  os.path.join(data_dir, person_id, "persona_profile.json")):
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception:  # noqa: BLE001
                    return None
        return None

    def new_state(self) -> dict:
        return {
            "person_id": self.person_id, "clock_minutes": 12 * 60,
            "last_interaction_minutes": 12 * 60, "turn_index": 0,
            "life_phase": "", "mood_valence": 50.0,
            "alter": {"accumulator": 0, "atmosphere": []},
            "proactive": {"reasons": [], "sent_count": 0, "pending_checks": []},
            "used_renders": [], "used_activities": [], "history": [],
        }

    # ------------------------------------------------------------------ #
    def run_turn(self, person_id: Optional[str] = None, user_message: Optional[str] = None,
                 event: Optional[dict] = None, state: Optional[dict] = None) -> dict:
        pid = person_id or self.person_id
        event = dict(event or {})
        etype = event.get("type", EVENT_USER_MESSAGE if user_message else EVENT_LIFE_TICK)
        state = self._clone_state(state or self.new_state())
        state["turn_index"] = int(state.get("turn_index", 0)) + 1
        elapsed = float(event.get("elapsed_minutes", 0) or 0)
        state["clock_minutes"] = float(state.get("clock_minutes", 0)) + elapsed

        narrative = self._narrate(state, etype, user_message, event)
        seen, delay_bucket = self._perceive(state, etype, user_message, event)
        decision = self._decide(state, etype, user_message, seen, delay_bucket, event)
        alter_out = self._apply_alter(state, etype, decision, event)
        agency = self._evaluate_agency(state, event)
        dist = self._reaction_distribution(state, decision, seen, delay_bucket, user_message, event)
        rendered = self._render(state, decision, narrative, alter_out, user_message, event)

        state["history"].append({"turn": state["turn_index"], "etype": etype,
                                  "mode": decision["mode"], "rendered": rendered.get("message", "")})
        if etype in (EVENT_USER_MESSAGE, EVENT_FOLLOW_UP):
            state["last_interaction_minutes"] = state["clock_minutes"]
        return {
            "person_id": pid, "event_type": etype,
            "clock_minutes": state["clock_minutes"], "elapsed_minutes": elapsed,
            "stage": "rendered", "narrative": narrative, "decision": decision,
            "render": rendered, "reaction_distribution": dist,
            "detach": decision.get("detach", {}), "alter": alter_out,
            "agency": agency, "state": state,
        }

    # stage1 + E9：第三方叙事（活动轮播，不逐字重复）
    def _narrate(self, state, etype, user_message, event) -> dict:
        elapsed = int(max(0, state["clock_minutes"] - state.get("last_interaction_minutes",
                                                                 state["clock_minutes"])))
        style = self.spec["style"]
        pool = _LIFE_ACTIVITY[style]
        used = state.setdefault("used_activities", [])
        uh = _hash01(f"narr|{self.person_id}|{state['turn_index']}|{user_message or ''}")
        # 全程去重：优先选从未用过的，池子用尽再轮转（不逐字重复）
        fresh = [a for a in pool if a not in used]
        candidates = fresh or list(pool)
        activity = event.get("life_activity") or _seeded_pick(
            f"{self.person_id}|{state['turn_index']}|life|{uh:.3f}|{len(candidates)}", candidates)
        used.append(activity)
        used_tint = state.setdefault("used_tint", [])
        tint_pool = [t for t in _MOOD_TINT[style] if t not in used_tint] or list(_MOOD_TINT[style])
        tint = _seeded_pick(f"{self.person_id}|{state['turn_index']}|tint|{uh:.3f}", tint_pool)
        used_tint.append(tint)
        state["life_phase"] = activity
        intrusion = (f"这时，{self._who()}的手机震了一下——你的消息闯了进来。"
                     if etype in (EVENT_USER_MESSAGE, EVENT_FOLLOW_UP) and user_message
                     else "没有外部消息打扰，他继续过着自己的生活。")
        return {"view": "third_person_narrator", "elapsed_minutes": elapsed,
                "doing": activity, "mood_tint": tint, "intrusion": intrusion,
                "text": f"【叙事】距上次互动约 {elapsed} 分钟。此刻{self._who()}{activity}；{tint}。{intrusion}"}

    def _who(self) -> str:
        return "他"

    # stage2
    def _perceive(self, state, etype, user_message, event):
        if etype not in (EVENT_USER_MESSAGE, EVENT_FOLLOW_UP):
            return None, None
        device = bool(event.get("device_available", True))
        busy = float(event.get("schedule_load", self._default_load()))
        seen = device and not (busy > 0.8 and self.spec["style"] == "avoid")
        return seen, self._pick_delay_bucket(state, seen, busy, event)

    def _default_load(self) -> float:
        return {"secure": 0.35, "avoid": 0.55, "anxious": 0.25}[self.spec["style"]]

    def _pick_delay_bucket(self, state, seen, busy, event) -> str:
        dist = list(_BASE_DELAY[self.spec["style"]])
        if not seen:
            dist = [0.0, 0.0, 0.1, 0.2, 0.3, 0.4]
        elif busy > 0.6:
            dist = [0.05, 0.15, 0.30, 0.30, 0.15, 0.05]
        r = _hash01(f"{self.person_id}|{state['turn_index']}|delay")
        acc = 0.0
        for i, p in enumerate(dist):
            acc += p
            if r <= acc:
                return DELAY_BUCKETS[i]
        return DELAY_BUCKETS[-1]

    # stage3：结构化决定
    def _decide(self, state, etype, user_message, seen, delay_bucket, event) -> dict:
        style = self.spec["style"]
        text = (user_message or "").strip()
        scene = _detect_scene(text)
        intent = _intent_of(text)

        if etype not in (EVENT_USER_MESSAGE, EVENT_FOLLOW_UP):
            return {
                "seen": None, "mode": "proactive" if event.get("proactive_reasons") else "idle",
                "delay_bucket": None,
                "emotion_params": {"valence": self._emotion_mean(state, "daily", style),
                                   "arousal": 0.3, "scene": "idle"},
                "tool_calls": [{"tool": "evaluate_agency_window",
                                "args": {"reasons": event.get("proactive_reasons", [])}, "status": "declared"}],
                "scene": "idle", "intent": None,
                "reason": "独立生活推进：不回应某条消息，仅评估是否主动联系(E13)。",
                "detach": {"topic_interest": 1.0, "detached": False, "manner": "idle",
                           "note": "无外部消息，跳脱不适用"},
            }

        tool_calls = [{"tool": "retrieve_history_reply", "args": {"scene": scene, "text": text}, "status": "declared"},
                      {"tool": "read_persona_profile", "args": {}, "status": "declared"}]

        interest = self._topic_interest(text, scene, event)
        detached = interest < 0.35
        if detached:
            manner = {"avoid": "dismiss_redirect", "anxious": "ask_back"}.get(style, "polite_lighter")
            mode = "deflect" if style in ("avoid", "anxious") else "reply"
        else:
            if seen is False:
                mode = "silent"
            elif delay_bucket in ("2h-12h", "12h-48h", ">48h"):
                mode = "delay"
            else:
                mode = "reply"

        reason = self._decision_reason(style, scene, intent, interest, seen, event, text)
        return {
            "seen": bool(seen), "mode": mode, "delay_bucket": delay_bucket,
            "emotion_params": {"valence": self._emotion_mean(state, scene, style),
                               "arousal": {"secure": 0.5, "avoid": 0.25, "anxious": 0.85}[style],
                               "scene": scene, "intent": intent},
            "tool_calls": tool_calls, "scene": scene, "intent": intent, "reason": reason,
            "detach": {"topic_interest": round(interest, 2), "detached": bool(detached),
                       "manner": manner if detached else "answer",
                       "note": "按人格/话题权重决定是否正面作答" if detached else "正面作答"},
        }

    def _topic_interest(self, text, scene, event) -> float:
        if "topic_interest" in event:
            return float(event["topic_interest"])
        if _intent_of(text) in ("apology", "gift"):
            return 0.75
        if scene in ("invite", "praise", "emotion"):
            return 0.8
        if scene == "conflict":
            return 0.7
        if scene == "question" or _RE_BUSY.search(text or ""):
            return 0.55
        if "你" in (text or ""):
            return 0.5
        return 0.15

    def _decision_reason(self, style, scene, intent, interest, seen, event, text="") -> str:
        # 意图优先，理由贴具体话术；人格底色收尾；并随具体话术(hash)取不同措辞变体
        tone = {"secure": "他态度平和、愿意接话",
                "avoid": "他本就抽离，低卷入只想快点结束",
                "anxious": "他容易焦虑黏人，会把话题拉回关系"}[style]
        h = _hash01(f"{style}|{scene}|{text or ''}")
        if seen is False:
            return "消息在他忙碌/没看手机时到达，这一轮先不处理（已读不回/未读）。"
        if intent == "apology":
            v = ["你在为语气道歉；{t}，不翻旧账，按场景正常恢复。",
                 "你在主动递台阶；{t}，接过去就往下走，不追究谁对谁错。"][int(h * 2) % 2]
            return v.format(t=tone)
        if intent == "gift":
            v = ["你带了他爱喝的东西；{t}，先对这份心意做出回应。",
                 "这是一份定向示好；{t}，先谢这份心，再看要不要回礼。"][int(h * 2) % 2]
            return v.format(t=tone)
        if intent == "busy_probe":
            return "你在试探他是否在忙；他此刻手头有事，先报清状态再约时间，不绕弯。"
        if scene == "conflict":
            return f"这是冲突对话；{tone}，按他的冲突模式(冷战/修复)回应。"
        if scene == "invite":
            v = ["你在约{topic}；话题兴趣{it:.2f}，{tone}，按他的节奏接或拖。",
                 "这是一次关于{topic}的推进邀请；{it:.2f} 卷入度，{tone}，判断接下还是改期。",
                 "你想拉他一起{topic}；{it:.2f} 的兴趣，{tone}，看是当场应下还是另约时间。"][int(h * 3) % 3]
            return v.format(topic=self._topic_cue(text), it=interest, tone=tone)
        if interest < 0.35:
            return f"这条消息卷入度低({interest:.2f})；{tone}，选择跳脱/反问而非有问必答。"
        return f"日常对话，话题兴趣{interest:.2f}；{tone}，按表层指纹回应。"

    @staticmethod
    def _topic_cue(text: str) -> str:
        """从我方消息抽一个短话题词，让理由贴具体话术（离线，无分词）。"""
        cues = ["纪录片", "电影", "爬山", "散步", "吃饭", "跑步", "逛街", "看展", "喝咖啡", "旅行"]
        for c in cues:
            if c in (text or ""):
                return c
        # 退而求其次：取消息里"周末/一起"之后的内容
        return "这个安排"

    # E12（保留已通过逻辑）
    def _scene_alter(self, scene: str, style: str) -> int:
        base = {"conflict": 4, "invite": -2, "praise": -2, "emotion": 1, "question": 0, "daily": 0}.get(scene, 0)
        if style == "anxious":
            base += 1
        elif style == "avoid":
            base -= 1
        return max(-5, min(5, base))

    def _apply_alter(self, state, etype, decision, event) -> dict:
        a = state.setdefault("alter", {"accumulator": 0, "atmosphere": []})
        new_alter = int(max(-5, min(5, event["alter"]))) if "alter" in event \
            else self._scene_alter(decision.get("scene", "daily"), self.spec["style"])
        density = min(1.0, state.get("turn_index", 1) / 6.0)
        threshold = self.cfg["baseThreshold"] * (1 - self.cfg["densityFactor"] * density)
        direction = 1 if new_alter >= 0 else -1
        kept = []
        for e in a.get("atmosphere", []):
            e["weight"] += self.cfg["sameDirectionBoost"] if e["direction"] == direction else -self.cfg["oppositeDecay"]
            if e["weight"] >= self.cfg["minWeight"]:
                kept.append(e)
        a["atmosphere"] = kept
        accum = int(a.get("accumulator", 0)) + new_alter
        triggered = False
        if abs(accum) >= threshold:
            triggered = True
            texts = _ATMO_SERIOUS if accum > 0 else _ATMO_RELAXED
            same = next((e for e in a["atmosphere"] if e["direction"] == direction), None)
            if same:
                same["text"] = "；".join(texts[:2])
                same["intensity"] = min(self.cfg["maxIntensity"], same["intensity"] + abs(accum) / max(1.0, threshold))
                same["weight"] += self.cfg["sameDirectionBoost"]
            else:
                a["atmosphere"].append({"text": "；".join(texts[:2]), "direction": direction,
                                        "weight": 1.0, "intensity": round(min(self.cfg["maxIntensity"], abs(accum) / max(1.0, threshold)), 3)})
            accum = 0
        a["accumulator"] = accum
        for e in a["atmosphere"]:
            e["intensity"] = min(self.cfg["maxIntensity"], e["intensity"])
        a["atmosphere"] = [e for e in a["atmosphere"] if e["weight"] >= self.cfg["minWeight"]]
        return {"new_alter": new_alter, "accumulator": accum, "density": round(density, 3),
                "threshold": round(threshold, 3), "triggered": triggered,
                "atmosphere": a["atmosphere"], "injection_text": [e["text"] for e in a["atmosphere"]],
                "note": "正=更严肃紧张，负=更轻松活跃；参数层"}

    # E13（保留已通过逻辑）
    def _evaluate_agency(self, state, event) -> dict:
        pro = state.setdefault("proactive", {"reasons": [], "sent_count": 0, "pending_checks": []})
        reasons = list(event.get("proactive_reasons", pro.get("reasons", [])))
        pro["reasons"] = reasons
        load = float(event.get("schedule_load", self._default_load()))
        privacy = float(event.get("privacy_need", {"secure": 0.3, "avoid": 0.65, "anxious": 0.25}[self.spec["style"]]))
        device = bool(event.get("device_available", True))
        result = {"action": "drop", "willingness": 0.0, "reason": "", "draft": None,
                  "predicted_user_initiative": 0.0, "auto_send": False,
                  "conditions": {"schedule_load": load, "privacy_need": privacy, "device_available": device}}
        if not reasons:
            result["reason"] = "无真实生活理由（承诺/日程/关系余波），不主动"
            return result
        if int(pro.get("sent_count", 0)) >= self.cfg["maxProactivePerDay"]:
            result["reason"] = f"今日主动次数已达上限 {self.cfg['maxProactivePerDay']}"
            return result
        top = max(reasons, key=lambda r: float(r.get("willingness", 0.0)))
        will = float(top.get("willingness", 0.0))
        result["willingness"] = will
        result["reason"] = top.get("text", "")
        result["predicted_user_initiative"] = round(min(1.0, will * 0.7), 3)
        if will < self.cfg["willingnessThreshold"]:
            result["action"] = "proactive-check"; result["reason"] += "（意愿低于阈值，稍后重查）"; return result
        if load <= self.cfg["scheduleLoadLimit"] and privacy <= self.cfg["privacyNeedLimit"] and device:
            result["action"] = "immediate"; result["draft"] = self._proactive_draft(top)
        else:
            result["action"] = "proactive-check"
            blocked = [n for n, ok in (("日程负荷高", load <= self.cfg["scheduleLoadLimit"]),
                                       ("需要隐私空间", privacy <= self.cfg["privacyNeedLimit"]),
                                       ("设备不可用", device)) if not ok]
            result["reason"] += "（被条件阻断：" + "、".join(blocked) + "，稍后重查）"
        return result

    def _proactive_draft(self, reason) -> str:
        tmpl = {"secure": ["在忙吗？想起昨天说的事，跟你确认下。", "周末有空的话，一起吃个饭？"],
                "avoid": ["嗯，路过想起之前答应你的事，说一声。", "最近怎么样。"],
                "anxious": ["你今天怎么没怎么理我？是不是我哪里惹你了？", "在干嘛呀？我好想你。"]}[self.spec["style"]]
        return _seeded_pick(f"{self.person_id}|{reason.get('text','')}", tmpl)

    # 反应分布：随具体话术变化（意图偏移 + 输入哈希抖动）
    def _emotion_mean(self, state, scene, style) -> float:
        base = {"secure": 68, "avoid": 42, "anxious": 55}[style]
        shift = {"conflict": -18, "invite": 12, "praise": 15, "emotion": 5}.get(scene, 0)
        return round(max(0.0, min(100.0, base + shift)), 1)

    def _reaction_distribution(self, state, decision, seen, delay_bucket, user_message, event) -> dict:
        style = self.spec["style"]
        dist = dict(_BASE_DIST[style])
        scene = decision.get("scene", "daily")
        intent = decision.get("intent")
        mode = decision.get("mode", "reply")
        h = _hash01(f"{scene}|{user_message or ''}|{self.person_id}")  # 同场景不同话术 -> 不同抖动
        if seen is False:
            dist["Y7"] += 0.30; dist["Y6"] += 0.10; dist["Y1"] = max(0.01, dist["Y1"] - 0.2)
        if mode == "silent":
            dist["Y6"] += 0.25
        if mode == "deflect":
            dist["Y4"] += 0.20; dist["Y1"] = max(0.01, dist["Y1"] - 0.1)
        if mode == "delay":
            dist["Y6"] += 0.12; dist["Y7"] += 0.08
        if scene == "conflict":
            dist["Y5"] += 0.20; dist["Y2"] = max(0.01, dist["Y2"] - 0.1)
        if mode == "reply" and scene in ("invite", "praise"):
            dist["Y1"] += 0.15; dist["Y2"] += 0.10
        # 意图偏移：道歉->朝修复(Y2/Y3)；礼物->接纳(Y2)；忙试探->中性偏拖(Y6)
        if intent == "apology":
            dist["Y2"] += 0.12; dist["Y3"] += 0.08; dist["Y5"] = max(0.01, dist["Y5"] - 0.1)
        elif intent == "gift":
            dist["Y2"] += 0.15; dist["Y1"] += 0.05
        elif intent == "busy_probe":
            dist["Y3"] += 0.08; dist["Y6"] += 0.05
        # 输入抖动：在 Y1/Y4 之间挪动约 0.06，使同场景不同话术分布不同
        shift = (h - 0.5) * 0.12
        dist["Y1"] = max(0.01, dist["Y1"] + shift)
        dist["Y4"] = max(0.01, dist["Y4"] - shift)
        dist = _norm_dist(dist)
        P = {k: round(dist[k], 4) for k in Y_KEYS}
        no_reply = round(P["Y4"] + P["Y6"] + P["Y7"], 4)
        emo = self._emotion_mean(state, scene, style)
        rng = {"secure": (max(0, emo - 10), min(100, emo + 10)),
               "avoid": (max(0, emo - 15), min(100, emo + 15)),
               "anxious": (max(0, emo - 25), min(100, emo + 25))}[style]
        key = intent or scene
        bank = _REPLY_BANK[style].get(key, _REPLY_BANK[style].get(scene, _REPLY_BANK[style]["daily"]))
        # 示例池：按输入哈希旋转顺序 + 全程去重，跨场景/跨轮不吐同一组
        used_ex = state.setdefault("used_examples", [])
        rot = int(h * len(bank))
        rotated = bank[rot:] + bank[:rot]
        ex = [e for e in rotated if e not in used_ex] or rotated
        examples = ex[:3]
        used_ex.extend(examples)
        return {
            "P": P, "sum": round(sum(P.values()), 4), "labels": Y_LABELS,
            "no_reply_avoid": no_reply,
            "emotion": {"mean": emo, "range": [round(rng[0], 1), round(rng[1], 1)]},
            "delay_buckets": {DELAY_BUCKETS[i]: round(_BASE_DELAY[style][i], 4) for i in range(len(DELAY_BUCKETS))},
            "examples": list(examples), "calibration": "先验·未校准",
            "note": "概率为冷启动先验+具体话术抖动，未接 T 校准；不得当作命中率",
        }

    # stage4：意图感知渲染 + 表层指纹 + 去重
    def _render(self, state, decision, narrative, alter_out, user_message, event) -> dict:
        style = self.spec["style"]
        mode = decision["mode"]
        scene = decision["scene"]
        intent = decision.get("intent")
        if mode == "silent" or decision.get("seen") is False or decision.get("seen") is None:
            return {"mode": mode, "message": "", "silent": True,
                    "delay_bucket": decision.get("delay_bucket"), "verbosity": self.spec["verbosity"]}
        key = "deflect" if decision.get("detach", {}).get("detached") else (intent or scene)
        # 短直人格优先用 terse 池
        if self.spec["verbosity"] == "terse" and key in _TERSE_BANK:
            pool = list(_TERSE_BANK[key])
        else:
            pool = list(_REPLY_BANK[style].get(key, _REPLY_BANK[style].get(scene, _REPLY_BANK[style]["daily"])))
        used = state.setdefault("used_renders", [])
        fresh = [m for m in pool if m not in used[-4:]] or pool
        base = _seeded_pick(f"{self.person_id}|{state['turn_index']}|{key}", fresh)
        base = _strip_unused_fillers(base, self.spec["used_fillers"])
        used.append(base)
        return {"mode": mode, "message": base, "silent": False,
                "delay_bucket": decision.get("delay_bucket"), "verbosity": self.spec["verbosity"],
                "atmosphere_injected": alter_out.get("injection_text", [])}

    @staticmethod
    def _clone_state(state: dict) -> dict:
        return json.loads(json.dumps(state, default=str))

    def to_json(self, result: dict) -> str:
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
