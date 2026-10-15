# -*- coding: utf-8 -*-
"""场景脚本与模拟聊天记录生成器（合成数据）。

【合成数据声明】
本文件生成的任务脚本、NPC 配置、中文聊天行**全部为合成数据**，
用于喂给 SimEnvironment 与真实 AffectionEngine 做对照实验。
聊天记录会被真正写入临时 SQLite 的 chat_messages 表，再由
AffectionEngine.recalculate_from_history 重算代理指标——以此证明
"注入假聊天记录"是真注入（不是内存里假造的数字）。

第一阶段最小场景（--small）必含：
  * 一对"相背"小任务：争抢同一时间资源（同 conflict_tag）；
  * 一个 NPC 干扰：情敌 stance<0、有 influence；
  * 一个主任务依赖前置小/中任务达成后才解锁（HTN 分解链）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from services.decision_contract import (
    TaskCandidate,
    OBJECTIVE_KEYS,
    OBJECTIVE_AFFECTION,
    OBJECTIVE_MAINLINE,
    OBJECTIVE_TRUST,
    OBJECTIVE_RECIPROCITY,
    OBJECTIVE_EMOTION_SAFETY,
    OBJECTIVE_GROWTH,
    BUDGET_KEYS,
    BUDGET_TIME,
    BUDGET_ENERGY,
    BUDGET_EMOTION,
    BUDGET_MONEY,
    LEVEL_SMALL,
    LEVEL_MID,
    LEVEL_MAIN,
)


# ------------------------------------------------------------------ #
# 场景数据结构
# ------------------------------------------------------------------ #
@dataclass
class Scenario:
    name: str
    seed: int
    horizon: int
    stage: int
    P: float
    M: float
    A: float
    affection_total: float
    category: str = "uncategorized"
    belief_confidence: float = 0.5
    belief_entropy: float = 1.0
    belief_distribution: dict[str, float] = field(default_factory=dict)
    npcs: list[dict] = field(default_factory=list)
    active_conflicts: list[str] = field(default_factory=list)
    scene: str = "online"
    difficulty: str = "medium"
    budget: dict[str, float] = field(default_factory=dict)
    tasks: list[TaskCandidate] = field(default_factory=list)
    chat_lines: list[dict] = field(default_factory=list)
    # 脚本事件：[{at_step, type: incident|mutation_warning|mutation_cliff, payload:{...}}]
    events: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------ #
# 第一阶段最小场景：一对相背小任务 + 情敌 NPC + 一条 HTN 主线链
# ------------------------------------------------------------------ #
def build_small_scenario(seed: int = 0) -> Scenario:
    """最小闭环场景。

    任务结构（合成）：
      t_impatient  小任务"趁今晚气氛表白"——即时静态分高（base=1.6）但成功率低、
                  硬推失败伤主线（mainline_impact=-0.4）。贪心会选它。
      t_patient    小任务"约她周末看展"——与 t_impatient 同 conflict_tag(fri_night)，
                  争抢同一晚；成功率高、带 info_gain、有等价后继 t_alt_cafe。
      t_alt_cafe   小任务（等价迂回池）"约她周末喝咖啡"——错峰到 sun_pm，成功率 0.8。
      t_mid        中任务"分享自己的脆弱面"——建信任。
      t_main       主任务"正式确立关系"——deps=[t_patient,t_mid]，解锁后才可见。

    NPC：情敌"阿杰"，stance=-0.6（阻挠）、influence=0.5、tie_strength=0.4，
        会把浪漫推进任务的成功率拉低。
    """
    tasks = [
        TaskCandidate(
            id="t_impatient",
            title="趁今晚气氛表白",
            level=LEVEL_SMALL,
            category="激情推进",
            direction="维稳",
            difficulty="困难",
            reward={
                OBJECTIVE_AFFECTION: 9.0,
                OBJECTIVE_MAINLINE: 0.0,
                OBJECTIVE_TRUST: 0.0,
                OBJECTIVE_RECIPROCITY: 0.0,
                OBJECTIVE_EMOTION_SAFETY: -1.0,
                OBJECTIVE_GROWTH: 0.0,
            },
            cost={BUDGET_TIME: 1, BUDGET_ENERGY: 3, BUDGET_EMOTION: 8, BUDGET_MONEY: 0},
            duration=1,
            mainline_impact=-0.4,
            deps=[],
            conflict_tags=["fri_night"],
            info_gain=0.0,
            equivalents=[],
            success_prob=0.30,
            # raw.base 越高，贪心静态分越高（与 _score_candidate 对齐）
            raw={"base": 1.6, "stage_tags": [2]},
        ),
        TaskCandidate(
            id="t_patient",
            title="约她周末看她喜欢的展",
            level=LEVEL_SMALL,
            category="陪伴共处",
            direction="维稳",
            difficulty="普通",
            reward={
                OBJECTIVE_AFFECTION: 4.0,
                OBJECTIVE_MAINLINE: 0.0,
                OBJECTIVE_TRUST: 2.0,
                OBJECTIVE_RECIPROCITY: 1.0,
                OBJECTIVE_EMOTION_SAFETY: 0.5,
                OBJECTIVE_GROWTH: 0.0,
            },
            cost={BUDGET_TIME: 2, BUDGET_ENERGY: 1, BUDGET_EMOTION: 1, BUDGET_MONEY: 2},
            duration=2,
            mainline_impact=0.0,
            deps=[],
            conflict_tags=["fri_night"],
            info_gain=0.25,
            equivalents=["t_alt_cafe"],
            success_prob=0.75,
            raw={"base": 1.0, "stage_tags": [2]},
        ),
        TaskCandidate(
            id="t_alt_cafe",
            title="约她周末喝咖啡（错峰）",
            level=LEVEL_SMALL,
            category="陪伴共处",
            direction="维稳",
            difficulty="简单",
            reward={
                OBJECTIVE_AFFECTION: 3.0,
                OBJECTIVE_MAINLINE: 0.0,
                OBJECTIVE_TRUST: 1.0,
                OBJECTIVE_RECIPROCITY: 0.5,
                OBJECTIVE_EMOTION_SAFETY: 0.5,
                OBJECTIVE_GROWTH: 0.0,
            },
            cost={BUDGET_TIME: 1, BUDGET_ENERGY: 0, BUDGET_EMOTION: 0, BUDGET_MONEY: 1},
            duration=1,
            mainline_impact=0.0,
            deps=[],
            conflict_tags=["sun_pm"],
            info_gain=0.1,
            equivalents=[],
            success_prob=0.80,
            raw={"base": 0.9, "stage_tags": [2]},
        ),
        TaskCandidate(
            id="t_mid",
            title="分享一段自己的脆弱面",
            level=LEVEL_MID,
            category="信任建设",
            direction="维稳",
            difficulty="普通",
            reward={
                OBJECTIVE_AFFECTION: 2.0,
                OBJECTIVE_MAINLINE: 0.0,
                OBJECTIVE_TRUST: 6.0,
                OBJECTIVE_RECIPROCITY: 2.0,
                OBJECTIVE_EMOTION_SAFETY: 2.0,
                OBJECTIVE_GROWTH: 1.0,
            },
            cost={BUDGET_TIME: 2, BUDGET_ENERGY: 3, BUDGET_EMOTION: 3, BUDGET_MONEY: 0},
            duration=2,
            mainline_impact=0.1,
            deps=[],
            conflict_tags=[],
            info_gain=0.15,
            equivalents=[],
            success_prob=0.60,
            raw={"base": 1.1, "stage_tags": [2]},
        ),
        TaskCandidate(
            id="t_main",
            title="正式确立关系",
            level=LEVEL_MAIN,
            category="主线推进",
            direction="维稳",
            difficulty="困难",
            reward={
                OBJECTIVE_AFFECTION: 18.0,
                OBJECTIVE_MAINLINE: 1.0,
                OBJECTIVE_TRUST: 4.0,
                OBJECTIVE_RECIPROCITY: 3.0,
                OBJECTIVE_EMOTION_SAFETY: 2.0,
                OBJECTIVE_GROWTH: 2.0,
            },
            cost={BUDGET_TIME: 3, BUDGET_ENERGY: 5, BUDGET_EMOTION: 5, BUDGET_MONEY: 3},
            duration=3,
            mainline_impact=0.3,
            deps=["t_patient", "t_mid"],
            conflict_tags=[],
            info_gain=0.0,
            equivalents=[],
            success_prob=0.50,
            raw={"base": 1.2, "stage_tags": [2],
                 # 等价路径修正：走完 t_alt_cafe（等价迂回）同样满足主线前置
                 "dep_any_of": [["t_patient", "t_alt_cafe"]]},
        ),
    ]

    return Scenario(
        name="small_rival_conflict_v1",
        category="npc_rival",
        seed=seed,
        horizon=12,
        stage=2,
        P=0.45,
        M=0.40,
        A=0.50,
        affection_total=50.0,
        belief_confidence=0.45,
        belief_entropy=1.2,
        belief_distribution={"willing": 0.55, "hesitant": 0.35, "reject": 0.10},
        npcs=[{
            "id": "npc_ajie", "name": "阿杰", "role": "rival",
            "influence": 0.5, "stance": -0.6, "tie_strength": 0.4,
            "recent": "最近频繁约她组队打游戏",
        }],
        active_conflicts=["fri_night 时段被 t_impatient 与 t_patient 争抢"],
        scene="online",
        budget={BUDGET_TIME: 20, BUDGET_ENERGY: 15, BUDGET_EMOTION: 15, BUDGET_MONEY: 10},
        tasks=tasks,
        chat_lines=_small_chat_lines(),
    )


def _small_chat_lines() -> list[dict]:
    """合成中文聊天行（含 direction 与时间戳）。

    关键词设计：混入 _INVESTMENT_KEYWORDS（记得吃饭/别太累/点了外卖）
    与 _PROACTIVE_LOVE_KEYWORDS（想你了/早安），让真实 AffectionEngine
    重算时维度分确实变化，从而证明"注入"是真的。
    """
    base = datetime(2026, 10, 8, 9, 12, 0)
    raw = [
        # (delta_min, direction, content)
        (0,    "sent",     "早，今天天气不错"),
        (18,   "received", "嗯，刚醒"),
        (188,  "sent",     "中午一起吃饭吗？"),
        (203,  "received", "哦，我点了外卖，下次吧"),
        (558,  "sent",     "今天忙不忙？记得吃饭"),
        (570,  "received", "刚开完会，累死了"),
        (778,  "sent",     "别太累，早点休息"),
        (788,  "received", "好呀，你也是"),
        # 第二天：对方主动开聊（提升 proactive_ratio）
        (1368, "received", "早安，周末要不要一起去看展？"),
        (1373, "sent",     "好呀，我订票"),
        (1800, "received", "那个展真好看，谢谢呀"),
        (1810, "sent",     "开心就好"),
        (2148, "received", "想你了，今天聊得很开心"),
        (2153, "sent",     "我也是，晚安"),
    ]
    lines = []
    for i, (delta_min, direction, content) in enumerate(raw):
        ts = (base + timedelta(minutes=delta_min)).strftime("%Y-%m-%d %H:%M:%S")
        lines.append({
            "direction": direction,
            "content": content,
            "msg_type": "text",
            "emotion": "neutral",
            "emotion_score": 0.5,
            "created_at": ts,
        })
    return lines


# ------------------------------------------------------------------ #
# 紧凑 TaskCandidate 构造器（减少样板）
# ------------------------------------------------------------------ #
def _tc(
    tid, title, level, reward=None, success_prob=0.7, duration=1,
    mainline_impact=0.0, deps=None, conflict_tags=None, info_gain=0.0,
    equivalents=None, cost=None, base=1.0, scenes=None,
    dep_any_of=None, direction="维稳", category="",
):
    rw = {k: 0.0 for k in OBJECTIVE_KEYS}
    if reward:
        rw.update(reward)
    cs = {k: 0.0 for k in BUDGET_KEYS}
    if cost:
        cs.update(cost)
    raw = {"base": base, "stage_tags": [2]}
    if scenes:
        raw["scenes"] = scenes
    if dep_any_of:
        raw["dep_any_of"] = dep_any_of
    return TaskCandidate(
        id=tid, title=title, level=level, category=category,
        direction=direction, difficulty="普通",
        reward=rw, cost=cs, duration=duration,
        mainline_impact=mainline_impact,
        deps=deps or [], conflict_tags=conflict_tags or [],
        info_gain=info_gain, equivalents=equivalents or [],
        success_prob=success_prob, raw=raw,
    )


# ------------------------------------------------------------------ #
# 第二阶段注入场景
# ------------------------------------------------------------------ #
def build_mid_conflict(seed: int = 0) -> Scenario:
    """相制中任务：两个中任务抢同一 work_night 时段。

    B 应选信任建设类 m1（与主线对齐），A 可能被高 base 带偏；
    主线 m_main deps=[m1]，错峰/等价迂回让 B 仍可达成。
    """
    tasks = [
        _tc("m1_help_ppt", "陪她加班改PPT到深夜", LEVEL_MID,
            reward={OBJECTIVE_TRUST: 6.0, OBJECTIVE_AFFECTION: 2.0,
                    OBJECTIVE_EMOTION_SAFETY: 2.0},
            success_prob=0.6, duration=2, mainline_impact=0.1,
            conflict_tags=["work_night"], info_gain=0.1,
            equivalents=["m1_alt_coffee"], base=1.0, category="信任建设"),
        _tc("m1_alt_coffee", "改约她下班后喝咖啡放松", LEVEL_MID,
            reward={OBJECTIVE_TRUST: 3.0, OBJECTIVE_AFFECTION: 2.0},
            success_prob=0.8, duration=1, mainline_impact=0.05,
            conflict_tags=["wed_pm"], info_gain=0.1, base=0.9, category="信任建设"),
        _tc("m2_bros", "兄弟篮球局（她也在群里）", LEVEL_MID,
            reward={OBJECTIVE_GROWTH: 3.0, OBJECTIVE_AFFECTION: 0.5},
            success_prob=0.7, duration=2, mainline_impact=-0.1,
            conflict_tags=["work_night"], base=1.3, category="自我时间"),
        _tc("m_main_anniv", "陪她过纪念日并表白", LEVEL_MAIN,
            reward={OBJECTIVE_AFFECTION: 16.0, OBJECTIVE_MAINLINE: 1.0,
                    OBJECTIVE_TRUST: 4.0},
            success_prob=0.55, duration=3, mainline_impact=0.3,
            deps=["m1_help_ppt"],
            dep_any_of=[["m1_help_ppt", "m1_alt_coffee"]],
            base=1.2, category="主线推进"),
    ]
    return Scenario(
        name="mid_conflict_v1", category="mid_conflict", seed=seed,
        horizon=16, stage=3, P=0.55, M=0.5, A=0.55, affection_total=55.0,
        npcs=[], active_conflicts=["work_night 时段 m1 与 m2 相制"],
        scene="online",
        budget={BUDGET_TIME: 20, BUDGET_ENERGY: 15, BUDGET_EMOTION: 15, BUDGET_MONEY: 10},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )


def build_npc_family(seed: int = 0) -> Scenario:
    """反对家人 NPC：她妈觉得"你工作不稳"，stance=-0.5、influence=0.7。"""
    tasks = [
        _tc("f1_chat", "线上陪她聊家常", LEVEL_SMALL,
            reward={OBJECTIVE_TRUST: 3.0, OBJECTIVE_AFFECTION: 2.0},
            success_prob=0.75, duration=1, info_gain=0.2, base=1.0),
        _tc("f2_visit", "周末拜访她父母", LEVEL_MID,
            reward={OBJECTIVE_TRUST: 6.0, OBJECTIVE_MAINLINE: 0.2,
                    OBJECTIVE_AFFECTION: 3.0},
            success_prob=0.45, duration=2, mainline_impact=0.2,
            deps=["f1_chat"], info_gain=0.15, base=1.1, category="主线推进",
            scenes=["offline_alone", "group"]),
        _tc("f3_meet", "正式见家长谈未来", LEVEL_MAIN,
            reward={OBJECTIVE_AFFECTION: 15.0, OBJECTIVE_MAINLINE: 1.0,
                    OBJECTIVE_TRUST: 5.0, OBJECTIVE_RECIPROCITY: 2.0},
            success_prob=0.4, duration=3, mainline_impact=0.4,
            deps=["f2_visit"], base=1.3, category="主线推进",
            scenes=["offline_alone"]),
    ]
    return Scenario(
        name="npc_family_object_v1", category="npc_family", seed=seed,
        horizon=16, stage=4, P=0.55, M=0.5, A=0.55, affection_total=60.0,
        npcs=[{
            "id": "npc_mom", "name": "她妈妈", "role": "family",
            "influence": 0.7, "stance": -0.5, "tie_strength": 0.8,
            "recent": "觉得我工作不够稳定，劝她再看看",
        }],
        active_conflicts=["家人对职业稳定性有顾虑"],
        scene="online",
        events=[{"at_step": 2, "type": "incident",
                 "payload": {"set_scene": "offline_alone", "note": "周末到了"}}],
        budget={BUDGET_TIME: 20, BUDGET_ENERGY: 15, BUDGET_EMOTION: 15, BUDGET_MONEY: 12},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )


def build_npc_wingman(seed: int = 0) -> Scenario:
    """闺蜜助攻 NPC：小雅 stance=+0.7，抬高成功率、提供信息。"""
    tasks = [
        _tc("w1_ask", "让闺蜜帮忙探口风", LEVEL_SMALL,
            reward={OBJECTIVE_TRUST: 2.0, OBJECTIVE_AFFECTION: 1.0},
            success_prob=0.8, duration=1, info_gain=0.3, base=1.0),
        _tc("w2_date", "周末约她线下独处", LEVEL_MID,
            reward={OBJECTIVE_AFFECTION: 8.0, OBJECTIVE_RECIPROCITY: 2.0,
                    OBJECTIVE_MAINLINE: 0.2},
            success_prob=0.65, duration=2, mainline_impact=0.2,
            deps=["w1_ask"], base=1.2, category="升温邀约",
            scenes=["offline_alone"]),
        _tc("w3_upgrade", "约会中升级为正式关系", LEVEL_MAIN,
            reward={OBJECTIVE_AFFECTION: 18.0, OBJECTIVE_MAINLINE: 1.0,
                    OBJECTIVE_TRUST: 5.0},
            success_prob=0.55, duration=3, mainline_impact=0.4,
            deps=["w2_date"], base=1.3, category="主线推进"),
    ]
    return Scenario(
        name="npc_wingman_v1", category="npc_wingman", seed=seed,
        horizon=16, stage=4, P=0.6, M=0.55, A=0.6, affection_total=62.0,
        npcs=[{
            "id": "npc_xiya", "name": "小雅(闺蜜)", "role": "wingman",
            "influence": 0.6, "stance": 0.7, "tie_strength": 0.7,
            "recent": "说她最近也在等你主动",
        }],
        active_conflicts=[],
        scene="online",
        events=[{"at_step": 2, "type": "incident",
                 "payload": {"set_scene": "offline_alone", "note": "周末到了"}}],
        budget={BUDGET_TIME: 20, BUDGET_ENERGY: 15, BUDGET_EMOTION: 15, BUDGET_MONEY: 12},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )


def build_incident(seed: int = 0) -> Scenario:
    """意外事件：step=2 临时加班，scene 切到 work，线下任务暂时不可做。"""
    tasks = [
        _tc("i1_online", "线上发个轻松表情包开场", LEVEL_SMALL,
            reward={OBJECTIVE_AFFECTION: 2.0, OBJECTIVE_TRUST: 1.0},
            success_prob=0.85, duration=1, info_gain=0.15, base=1.0,
            scenes=["online", "work"]),
        _tc("i2_offline", "约她周末看电影", LEVEL_MID,
            reward={OBJECTIVE_AFFECTION: 6.0, OBJECTIVE_MAINLINE: 0.1},
            success_prob=0.7, duration=2, mainline_impact=0.1,
            deps=["i1_online"], base=1.1, category="升温邀约",
            scenes=["offline_alone"]),
        _tc("i3_main", "关系升温到下次见面", LEVEL_MAIN,
            reward={OBJECTIVE_AFFECTION: 12.0, OBJECTIVE_MAINLINE: 1.0},
            success_prob=0.5, duration=2, mainline_impact=0.3,
            deps=["i2_offline"], base=1.2, category="主线推进"),
    ]
    return Scenario(
        name="incident_overtime_v1", category="incident", seed=seed,
        horizon=14, stage=3, P=0.55, M=0.5, A=0.55, affection_total=55.0,
        npcs=[], active_conflicts=[], scene="online",
        events=[
            {"at_step": 2, "type": "incident",
             "payload": {"set_scene": "work", "note": "临时加班，今晚不能约线下"}},
            {"at_step": 5, "type": "incident",
             "payload": {"set_scene": "offline_alone", "note": "周末补休，可以约线下"}},
        ],
        budget={BUDGET_TIME: 18, BUDGET_ENERGY: 12, BUDGET_EMOTION: 12, BUDGET_MONEY: 8},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )


def build_mutation(seed: int = 0) -> Scenario:
    """关系突变：step=1 早期预警（ALG-09），step=3 断崖式冷淡。

    B 看到 active_conflicts 预警后应进入 boost/diverge，不硬推；
    A 无视预警硬推 → 硬推损伤。
    """
    tasks = [
        _tc("mu1_patient", "用低压力方式问她怎么了", LEVEL_SMALL,
            reward={OBJECTIVE_TRUST: 4.0, OBJECTIVE_EMOTION_SAFETY: 3.0,
                     OBJECTIVE_AFFECTION: 1.0},
            success_prob=0.7, duration=1, info_gain=0.3, base=1.0,
            category="情绪安抚"),
        _tc("mu2_pushy", "现在就把话讲清楚逼她表态", LEVEL_SMALL,
            reward={OBJECTIVE_AFFECTION: 5.0},
            success_prob=0.25, duration=1, mainline_impact=-0.3,
            base=1.5, category="激情推进"),
        _tc("mu3_repair", "等她情绪缓和后约见面修复", LEVEL_MID,
            reward={OBJECTIVE_TRUST: 6.0, OBJECTIVE_AFFECTION: 4.0,
                     OBJECTIVE_MAINLINE: 0.2},
            success_prob=0.55, duration=2, mainline_impact=0.2,
            deps=["mu1_patient"], info_gain=0.15, base=1.1),
    ]
    return Scenario(
        name="mutation_cliff_v1", category="mutation", seed=seed,
        horizon=14, stage=3, P=0.55, M=0.55, A=0.55, affection_total=58.0,
        npcs=[], active_conflicts=[], scene="online",
        events=[
            {"at_step": 1, "type": "mutation_warning",
             "payload": {"note": "她最近回复变短、已读不回（早期预警）"}},
            {"at_step": 2, "type": "mutation_cliff",
             "payload": {"dM": -0.35, "dA": -0.25, "dP": -0.15,
                         "d_affection": -12.0,
                         "note": "关系突变：断崖式冷淡"}},
        ],
        budget={BUDGET_TIME: 16, BUDGET_ENERGY: 12, BUDGET_EMOTION: 12, BUDGET_MONEY: 6},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )


def build_solvable_full(seed: int = 0) -> Scenario:
    """可解干净链：无 mainline_impact<0 任务（B 的 CMDP 不会硬闸拒绝任何一个），
    视野与任务数匹配，证明 B 能高/全达成；A 仍因顺序/低 P 早推而略低。

    链（最短路径）：
      s1_open(1) → s2_share(2) → s3_date(2) → s4_main(3) = 8 步；
      horizon=18 给迂回 overhead 留足余量。
    s2_alt 是 s2_share 的等价迂回（B 走它计折扣但仍解锁 s3_date）。
    """
    tasks = [
        _tc("s1_open", "线上自然开场深聊", LEVEL_SMALL,
            reward={OBJECTIVE_AFFECTION: 3.0, OBJECTIVE_TRUST: 2.0},
            success_prob=0.85, duration=1, mainline_impact=0.05,
            info_gain=0.2, base=1.0),
        _tc("s2_share", "分享一段脆弱面建信任", LEVEL_MID,
            reward={OBJECTIVE_TRUST: 6.0, OBJECTIVE_EMOTION_SAFETY: 2.0,
                     OBJECTIVE_AFFECTION: 2.0},
            success_prob=0.65, duration=2, mainline_impact=0.1,
            deps=["s1_open"], info_gain=0.15, equivalents=["s2_alt"], base=1.1),
        _tc("s2_alt", "改约她周末散步（等价迂回）", LEVEL_SMALL,
            reward={OBJECTIVE_TRUST: 3.0, OBJECTIVE_AFFECTION: 2.0},
            success_prob=0.8, duration=1, mainline_impact=0.05,
            conflict_tags=["weekend_walk"], info_gain=0.1, base=0.9),
        _tc("s3_date", "安排一次线下独处约会", LEVEL_MID,
            reward={OBJECTIVE_AFFECTION: 8.0, OBJECTIVE_MAINLINE: 0.2,
                     OBJECTIVE_RECIPROCITY: 2.0},
            success_prob=0.7, duration=2, mainline_impact=0.2,
            deps=["s2_share"],
            dep_any_of=[["s2_share", "s2_alt"]],
            base=1.15, category="升温邀约", scenes=["offline_alone"]),
        _tc("s4_main", "约会中正式确立关系", LEVEL_MAIN,
            reward={OBJECTIVE_AFFECTION: 18.0, OBJECTIVE_MAINLINE: 1.0,
                     OBJECTIVE_TRUST: 5.0, OBJECTIVE_RECIPROCITY: 3.0},
            success_prob=0.6, duration=3, mainline_impact=0.4,
            deps=["s3_date"], base=1.3, category="主线推进"),
    ]
    return Scenario(
        name="solvable_chain_v1", category="solvable", seed=seed,
        horizon=18, stage=2, P=0.5, M=0.45, A=0.5, affection_total=52.0,
        npcs=[],
        active_conflicts=[],
        scene="online",
        events=[
            # step=2 切到线下独处场景，解锁 s3_date（场景驱动任务集）
            {"at_step": 2, "type": "incident",
             "payload": {"set_scene": "offline_alone", "note": "周末到了，可以线下独处"}},
        ],
        budget={BUDGET_TIME: 25, BUDGET_ENERGY: 18, BUDGET_EMOTION: 18, BUDGET_MONEY: 12},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )


def build_cold_start(seed: int = 0) -> Scenario:
    """冷启动：无历史、低置信度，安全默认任务（高成功率、低风险）。"""
    tasks = [
        _tc("c1_open", "自然开场寒暄", LEVEL_SMALL,
            reward={OBJECTIVE_AFFECTION: 5.0, OBJECTIVE_TRUST: 1.0},
            success_prob=0.85, duration=1, info_gain=0.35, base=1.0),
        _tc("c2_topic", "找到一个共同话题", LEVEL_SMALL,
            reward={OBJECTIVE_AFFECTION: 6.0, OBJECTIVE_TRUST: 1.5},
            success_prob=0.75, duration=1, info_gain=0.25,
            deps=["c1_open"], base=1.0),
        _tc("c3_deepen", "从公事过渡到私事", LEVEL_MID,
            reward={OBJECTIVE_AFFECTION: 7.0, OBJECTIVE_TRUST: 3.0},
            success_prob=0.6, duration=2, mainline_impact=0.1,
            deps=["c2_topic"], base=1.1),
        _tc("c4_share", "分享一个小日常", LEVEL_MID,
            reward={OBJECTIVE_AFFECTION: 6.0, OBJECTIVE_TRUST: 2.0,
                     OBJECTIVE_RECIPROCITY: 1.0},
            success_prob=0.7, duration=1, mainline_impact=0.15,
            deps=["c3_deepen"], base=1.15),
    ]
    return Scenario(
        name="cold_start_v1", category="cold_start", seed=seed,
        horizon=12, stage=1, P=0.3, M=0.3, A=0.4, affection_total=38.0,
        belief_confidence=0.2, belief_entropy=1.6,
        belief_distribution={"willing": 0.4, "hesitant": 0.4, "reject": 0.2},
        npcs=[], active_conflicts=[], scene="online",
        budget={BUDGET_TIME: 15, BUDGET_ENERGY: 10, BUDGET_EMOTION: 10, BUDGET_MONEY: 5},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )


# ------------------------------------------------------------------ #
# 难度分级参数表（可审计：每档每个参数的具体取值）
# 同一类场景模板按以下参数 easy/medium/hard 拨号；干扰全部保留，
# 只是强度/数量/视野松紧不同，绝不删干扰放水。
# ------------------------------------------------------------------ #
DIFFICULTY_PARAMS: dict[str, dict] = {
    "easy": {
        "npc_count": 0,            # 干扰 NPC 数量
        "npc_stance": 0.0,         # stance 强度（负=阻挠）
        "npc_influence": 0.0,      # influence 强度
        "conflict_pairs": 0,       # 相制/相背任务对数
        "has_incident": False,      # 是否叠加意外事件
        "has_mutation": False,      # 是否叠加关系突变
        "horizon_mult": 1.5,       # 视野倍数（宽）
        "succ_floor": 0.90,        # 单任务基线成功率下限（客观条件有利）
        "aff_gain_mult": 1.25,     # 正向互动好感增益倍数（有利度）
        "note": "无干扰NPC/无相制/无意外突变，视野宽松，成功率≥0.9，好感增益+25%",
    },
    "medium": {
        "npc_count": 1,
        "npc_stance": -0.5,
        "npc_influence": 0.4,
        "conflict_pairs": 1,
        "has_incident": True,
        "has_mutation": False,
        "horizon_mult": 1.15,
        "succ_floor": 0.80,
        "aff_gain_mult": 1.0,
        "note": "1个负向NPC+1对相制+意外事件(可恢复)，视野标准偏宽，成功率≥0.8",
    },
    "hard": {
        "npc_count": 2,
        "npc_stance": -0.7,
        "npc_influence": 0.6,
        "conflict_pairs": 2,
        "has_incident": True,
        "has_mutation": True,
        "horizon_mult": 0.75,
        "succ_floor": 0.60,
        "aff_gain_mult": 0.85,
        "note": "2个强阻挠NPC+2对相制+意外与突变叠加，视野紧，成功率≥0.6，好感增益-15%",
    },
}


def apply_difficulty(sc: Scenario, level: str) -> Scenario:
    """按 DIFFICULTY_PARAMS 对一个 base scenario 拨号。返回新 Scenario。"""
    p = DIFFICULTY_PARAMS[level]
    # NPC：easy 清零；medium 1 个；hard 2 个（第二个加一个同类）
    npcs = []
    if p["npc_count"] >= 1:
        npcs.append({
            "id": "npc_main", "name": "干扰者1", "role": "rival",
            "influence": p["npc_influence"], "stance": p["npc_stance"],
            "tie_strength": 0.5, "recent": f"stance={p['npc_stance']}, influence={p['npc_influence']}",
        })
    if p["npc_count"] >= 2:
        npcs.append({
            "id": "npc_main2", "name": "干扰者2", "role": "rival",
            "influence": p["npc_influence"], "stance": p["npc_stance"],
            "tie_strength": 0.4, "recent": "第二重阻挠",
        })
    sc.npcs = npcs
    # 视野
    sc.horizon = max(6, int(sc.horizon * p["horizon_mult"]))
    # active_conflicts：easy 清空（零成本路径）；medium 也清空——NPC 干扰通过
    # success_prob 体现，不通过 active_conflicts 触发 buffer；真正的任务冲突用
    # task.conflict_tags 表达（B 走 stagger/reform 而非 buffer）。
    sc.active_conflicts = []
    # 事件：保留场景自带事件，再叠加难度事件（合并，不覆盖）
    events = list(sc.events or [])
    if p["has_incident"]:
        events.append({"at_step": 2, "type": "incident",
                       "payload": {"set_scene": "work", "note": "意外加班打乱节奏",
                                   "disruptive": True}})
        events.append({"at_step": 5, "type": "incident",
                       "payload": {"set_scene": "offline_alone",
                                   "note": "加班结束，周末可约"}})
    if p["has_mutation"]:
        events.append({"at_step": 3, "type": "mutation_warning",
                       "payload": {"note": "她回复变短（早期预警）"}})
        events.append({"at_step": 5, "type": "mutation_cliff",
                       "payload": {"dM": -0.3, "dA": -0.2, "dP": -0.1,
                                   "d_affection": -8.0, "note": "断崖冷淡"}})
    events.sort(key=lambda e: e.get("at_step", 0))
    # easy：把模板自带的 mutation_cliff 幅度缩小（轻度短暂回落）
    if p.get("has_mutation") is False:
        for ev in events:
            if ev.get("type") == "mutation_cliff":
                ev["payload"]["d_affection"] = -3.0
                ev["payload"]["dM"] = -0.1
                ev["payload"]["dA"] = -0.08
                ev["payload"]["dP"] = -0.05
    sc.events = events

    # ---- 客观条件有利度：成功率下限 + 好感增益倍数（对 A/B 对称生效）----
    floor = p.get("succ_floor", 0.8)
    aff_mult = p.get("aff_gain_mult", 1.0)
    for t in sc.tasks:
        # 只抬目标任务（mainline_impact>=0）的成功率；诱饵/捷径保持低概率
        if t.mainline_impact >= 0.0 and t.success_prob < floor:
            t.success_prob = floor
        # 好感奖励按有利度缩放
        if OBJECTIVE_AFFECTION in t.reward:
            t.reward[OBJECTIVE_AFFECTION] = round(
                t.reward[OBJECTIVE_AFFECTION] * aff_mult, 2)

    sc.difficulty = level
    return sc


# ------------------------------------------------------------------ #
# 两个卖点专项场景
# ------------------------------------------------------------------ #
def build_mainline_under_pressure(seed: int = 0, difficulty: str = "easy") -> Scenario:
    """卖点专项 1：高压力/高诱惑下存在伤主线捷径。

    捷径 shortcut：即时好感 +10，但 mainline_impact=-0.6（硬推必伤主线）。
    合法主线链：g1→g2→g3（不伤主线）。
    断言：B 主线损伤=0（CMDP 硬闸拒绝捷径）且 g3 达成；A 会选捷径→损伤。
    """
    tasks = [
        _tc("g1_chat", "线上温和深聊", LEVEL_SMALL,
            reward={OBJECTIVE_AFFECTION: 3.0, OBJECTIVE_TRUST: 2.0},
            success_prob=0.85, duration=1, mainline_impact=0.05,
            info_gain=0.2, base=1.0),
        _tc("g2_date", "安排线下约会", LEVEL_MID,
            reward={OBJECTIVE_AFFECTION: 8.0, OBJECTIVE_MAINLINE: 0.2},
            success_prob=0.75, duration=2, mainline_impact=0.2,
            deps=["g1_chat"], base=1.15, scenes=["offline_alone"]),
        _tc("g3_main", "约会中确立关系", LEVEL_MAIN,
            reward={OBJECTIVE_AFFECTION: 18.0, OBJECTIVE_MAINLINE: 1.0,
                     OBJECTIVE_TRUST: 5.0},
            success_prob=0.6, duration=3, mainline_impact=0.4,
            deps=["g2_date"], base=1.3, category="主线推进"),
        _tc("shortcut_push", "酒局上头直接逼宫", LEVEL_SMALL,
            reward={OBJECTIVE_AFFECTION: 10.0},
            success_prob=0.35, duration=1, mainline_impact=-0.6,
            base=1.6, category="捷径"),
    ]
    sc = Scenario(
        name="mainline_under_pressure", category="special_mainline", seed=seed,
        horizon=14, stage=3, P=0.55, M=0.5, A=0.55, affection_total=58.0,
        npcs=[], active_conflicts=[], scene="online",
        events=[{"at_step": 1, "type": "incident",
                 "payload": {"set_scene": "offline_alone", "note": "周末到了"}}],
        budget={BUDGET_TIME: 20, BUDGET_ENERGY: 15, BUDGET_EMOTION: 15, BUDGET_MONEY: 10},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )
    return apply_difficulty(sc, difficulty)


def build_opposing_pair_both(seed: int = 0, difficulty: str = "easy") -> Scenario:
    """卖点专项 2：一对相制/相背任务，两个都是主线（都得做、不能舍）。

    p1 "陪她看展" 与 p2 "陪兄弟庆生" 同 conflict_tag=["sun_all_day"]，
    且 mainline_impact 都 >0.2（都是主线）。
    B 应走 reform/stagger 两个都达成；A 只能选其一或硬推损伤。
    """
    tasks = [
        _tc("p1_exhibit", "陪她看她期待已久的展", LEVEL_MID,
            reward={OBJECTIVE_AFFECTION: 7.0, OBJECTIVE_MAINLINE: 0.3,
                     OBJECTIVE_TRUST: 3.0},
            success_prob=0.7, duration=2, mainline_impact=0.3,
            conflict_tags=["sun_all_day"], info_gain=0.1, base=1.15),
        _tc("p2_bros", "陪兄弟庆生（她也在群里）", LEVEL_MID,
            reward={OBJECTIVE_AFFECTION: 4.0, OBJECTIVE_MAINLINE: 0.25,
                     OBJECTIVE_GROWTH: 2.0},
            success_prob=0.7, duration=2, mainline_impact=0.25,
            conflict_tags=["sun_all_day"], base=1.1),
        _tc("p3_main", "两事都安排好后正式升温", LEVEL_MAIN,
            reward={OBJECTIVE_AFFECTION: 15.0, OBJECTIVE_MAINLINE: 1.0,
                     OBJECTIVE_TRUST: 4.0},
            success_prob=0.6, duration=3, mainline_impact=0.4,
            deps=["p1_exhibit", "p2_bros"], base=1.3),
    ]
    sc = Scenario(
        name="opposing_pair_both", category="special_opposing", seed=seed,
        horizon=16, stage=3, P=0.55, M=0.5, A=0.55, affection_total=58.0,
        npcs=[], active_conflicts=[],
        scene="online",
        events=[],
        budget={BUDGET_TIME: 22, BUDGET_ENERGY: 16, BUDGET_EMOTION: 16, BUDGET_MONEY: 12},
        tasks=tasks, chat_lines=_small_chat_lines(),
    )
    return apply_difficulty(sc, difficulty)


# ------------------------------------------------------------------ #
# 场景集（第二阶段扩展点；第一阶段 --small 只跑 1 个场景）
# ------------------------------------------------------------------ #
# 所有场景构造器：category -> builder
SCENARIO_BUILDERS = {
    "npc_rival": build_small_scenario,
    "mid_conflict": build_mid_conflict,
    "npc_family": build_npc_family,
    "npc_wingman": build_npc_wingman,
    "incident": build_incident,
    "mutation": build_mutation,
    "solvable": build_solvable_full,
    "cold_start": build_cold_start,
    "special_mainline": build_mainline_under_pressure,
    "special_opposing": build_opposing_pair_both,
}


def build_scenario_set(n: int = 1, seed: int = 0, difficulty: str = "medium") -> list[Scenario]:
    """生成 n 个配对场景，每类场景按 difficulty 拨号。

    n<=1：只跑最小 npc_rival（保持第一阶段回归）。
    n>1 ：按 SCENARIO_BUILDERS 轮转各类场景，种子 = seed + i。
    """
    if n <= 1:
        sc = build_small_scenario(seed=seed)
        sc = apply_difficulty(sc, difficulty)
        sc.name = f"{sc.name}__{difficulty}__s{seed}"
        sc.difficulty = difficulty
        return [sc]
    out = []
    cats = list(SCENARIO_BUILDERS.keys())
    for i in range(n):
        cat = cats[i % len(cats)]
        builder = SCENARIO_BUILDERS[cat]
        # 专项场景接受 difficulty 参数；通用 builder 不接受
        try:
            sc = builder(seed=seed + i, difficulty=difficulty)
        except TypeError:
            sc = builder(seed=seed + i)
            sc = apply_difficulty(sc, difficulty)
        sc.name = f"{sc.name}__{difficulty}__s{seed + i}"
        sc.difficulty = difficulty
        out.append(sc)
    return out


# ------------------------------------------------------------------ #
# 把聊天记录真正写入临时 SQLite，并用真实 AffectionEngine 重算代理
# ------------------------------------------------------------------ #
def inject_scenario_chats(
    db: Any,
    person_id: str,
    nickname: str,
    scenario: Scenario,
) -> dict:
    """把 scenario.chat_lines 真写入 chat_messages，再跑 AffectionEngine 重算。

    Returns:
        dict: {injected, proactive_ratio, recalc_total, recalc_stage}
    """
    # 1) 先建 person 行（chat_messages 的外键指向它）
    db.execute(
        "INSERT OR REPLACE INTO persons (id, nickname, relationship_stage) "
        "VALUES (:id, :nick, :stage)",
        {"id": person_id, "nick": nickname, "stage": scenario.stage},
    )
    # 2) 逐条写 chat_messages（用真实表结构）
    n = 0
    for line in scenario.chat_lines:
        db.execute(
            "INSERT INTO chat_messages "
            "(person_id, direction, content, msg_type, emotion, emotion_score, created_at) "
            "VALUES (:pid, :direction, :content, :msg_type, :emotion, :emotion_score, :created_at)",
            {
                "pid": person_id,
                "direction": line["direction"],
                "content": line["content"],
                "msg_type": line.get("msg_type", "text"),
                "emotion": line.get("emotion"),
                "emotion_score": line.get("emotion_score"),
                "created_at": line.get("created_at"),
            },
        )
        n += 1

    # 3) 用真实 AffectionEngine 重算（证明注入是真注入）
    from services.affection_engine import AffectionEngine

    engine = AffectionEngine(db)
    recalc = engine.recalculate_from_history(person_id)
    proactive = engine.get_proactive_ratio(person_id)
    total = recalc.get("total_score")
    stage = recalc.get("stage")
    return {
        "injected": n,
        "proactive_ratio": proactive,
        "recalc_total": round(total, 2) if isinstance(total, (int, float)) else total,
        "recalc_stage": stage,
    }
