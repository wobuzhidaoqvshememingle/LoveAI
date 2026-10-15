# -*- coding: utf-8 -*-
"""决策契约（frozen contract）——算法核心层 与 合成数据 benchmark 共享的数据结构与接口。

本文件只定义"双方约定的边界"，不包含任何算法实现：
  * 任务候选 / 关系状态 / 信念状态 / NPC 状态 / 决策上下文 / 规划动作 的结构；
  * 策略（Policy）协议：benchmark 用同一接口分别驱动 A=贪心静态打分、B=长期优化助推-滑翔；
  * 具体规划器的固定落点（类名 / 模块路径 / 构造与方法签名），供 task_engine 与 benchmark 共同引用。

严格数学语言对应（算法本体在具体规划器中实现）：
  SMDP（多时间尺度小/中/主）、CMDP（主线不损硬约束）、MORL/MO-MDP（帕累托）、
  HTN（主线→中→小分解）、POMDP（信念状态）。
钱学森弹道 / 最小作用量 / 共振等仅为命名修辞，不进算法本体、也不在本契约中出现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

# ------------------------------------------------------------------ #
# 常量 / 枚举（用字符串字面量，避免额外依赖）
# ------------------------------------------------------------------ #

# 三级任务粒度（SMDP 的多时间尺度；HTN 分解层级）
LEVEL_SMALL = "small"   # 小任务
LEVEL_MID = "mid"       # 中任务
LEVEL_MAIN = "main"     # 主线任务
TASK_LEVELS = (LEVEL_SMALL, LEVEL_MID, LEVEL_MAIN)

# 助推-滑翔四模式（状态机输出标签）
MODE_BOOST = "boost"      # 助推·蓄势：P 不足，先建吸引/信任/信息
MODE_GLIDE = "glide"      # 滑翔·顺势：P 足 M 足 无障碍，借势浅弧度推进
MODE_DIVERGE = "diverge"  # 变轨·迂回：遇冲突/拒绝，等价后继池绕行，航向不变、不掉势
MODE_DIVE = "dive"        # 俯冲·收口：窗口成熟，高确定性任务一击达标
MODES = (MODE_BOOST, MODE_GLIDE, MODE_DIVERGE, MODE_DIVE)

# MORL 多目标维度（每个候选在这些维度上标注增量；帕累托前沿在该向量空间中计算）
OBJECTIVE_AFFECTION = "affection"            # 好感/吸引（势能 P 的收益面）
OBJECTIVE_MAINLINE = "mainline"              # 主线推进（硬约束相关目标）
OBJECTIVE_TRUST = "trust"                    # 信任/安全感
OBJECTIVE_RECIPROCITY = "reciprocity"        # 对等投入（对方投入占比）
OBJECTIVE_EMOTION_SAFETY = "emotion_safety"  # 情绪安全（下行风险最小化）
OBJECTIVE_GROWTH = "growth"                  # 能动性/关系成长
OBJECTIVE_KEYS = (
    OBJECTIVE_AFFECTION,
    OBJECTIVE_MAINLINE,
    OBJECTIVE_TRUST,
    OBJECTIVE_RECIPROCITY,
    OBJECTIVE_EMOTION_SAFETY,
    OBJECTIVE_GROWTH,
)

# 资源预算维度（ALG-40：不超时间/精力/情绪/经济预算）
BUDGET_TIME = "time"
BUDGET_ENERGY = "energy"
BUDGET_EMOTION = "emotion"
BUDGET_MONEY = "money"
BUDGET_KEYS = (BUDGET_TIME, BUDGET_ENERGY, BUDGET_EMOTION, BUDGET_MONEY)

# 迂回（变轨）路径类型（ALG-27：等价/缓冲/错峰/重构；明确不做"按优先级取舍"）
DETOUR_EQUIVALENT = "equivalent"  # 等价后继：换一个不冲突的等价动作
DETOUR_BUFFER = "buffer"          # 缓冲：先插入缓冲动作，撑大可行域
DETOUR_STAGGER = "stagger"        # 错峰：把相制任务在时间轴错开
DETOUR_REFORM = "reform"          # 重构：改变任务结构/约束本身，使二者可兼得
DETOUR_TYPES = (DETOUR_EQUIVALENT, DETOUR_BUFFER, DETOUR_STAGGER, DETOUR_REFORM)


def _default_reward() -> dict[str, float]:
    return {k: 0.0 for k in OBJECTIVE_KEYS}


def _default_budget() -> dict[str, float]:
    return {k: 0.0 for k in BUDGET_KEYS}


# ------------------------------------------------------------------ #
# 数据结构
# ------------------------------------------------------------------ #
@dataclass
class BeliefState:
    """POMDP 信念状态：对方内心部分可观测，维护假设上的概率分布。

    distribution: 假设键（如对方接纳度/真实意愿的离散状态）-> 概率（和为 1）。
    entropy: 分布熵（越大越不确定）；试探动作应使熵下降。
    confidence: 0..1，对当前主判断的置信度（诚实标注，永不假定为 1）。
    """

    distribution: dict[str, float] = field(default_factory=dict)
    entropy: float = 0.0
    confidence: float = 0.5

    def argmax(self) -> Optional[str]:
        if not self.distribution:
            return None
        return max(self.distribution.items(), key=lambda kv: kv[1])[0]


@dataclass
class NpcState:
    """关系网络节点（活数据，ALG-05）。

    role: rival=情敌 / family=反对或支持的家人 / wingman=闺蜜兄弟助攻 / other。
    influence: 0..1 对目标对象的影响力。
    stance: -1..1 对"我方与目标在一起"的立场（负=阻挠，正=助攻）。
    tie_strength: 0..1 与目标的关系强度。
    recent: 最近行为的文字描述（活数据）。
    """

    id: str
    name: str
    role: str = "other"
    influence: float = 0.3
    stance: float = 0.0
    tie_strength: float = 0.3
    recent: str = ""
    # R3.3-79：可选扩展字段（frozen contract 红线——仅追加带默认值的新可选字段）
    faction: str = ""
    family_context: dict = field(default_factory=dict)


@dataclass
class TaskCandidate:
    """任务候选（任务建模底座 ALG-12~16）。

    level: small/mid/main。
    reward: 该动作在各 MORL 目标维度上的期望增量（0..1 量级，可含负值表示损伤）。
    cost: 各资源预算的消耗。
    duration: 预计占用的时间尺度（SMDP 的选项持续时间，整数时间步）。
    mainline_impact: -1..1，对主线的影响；负值=伤主线（CMDP 硬闸据此否决）。
    deps: 前置/part-of 任务 id（HTN 分解链）。
    conflict_tags: 该任务占用的互斥资源/时段标签；与其他任务同标签即"相制"。
    info_gain: 试探动作可降低的信念不确定度（POMDP）。
    equivalents: 等价后继候选 id（变轨绕行池）。
    success_prob: 不考虑信念时的基线成功概率 0..1。
    raw: task_engine 原始任务字典（透传，便于回写与兼容）。
    """

    id: str
    title: str
    level: str = LEVEL_SMALL
    category: str = ""
    direction: str = "维稳"
    difficulty: str = "普通"
    reward: dict[str, float] = field(default_factory=_default_reward)
    cost: dict[str, float] = field(default_factory=_default_budget)
    duration: int = 1
    mainline_impact: float = 0.0
    deps: list[str] = field(default_factory=list)
    conflict_tags: list[str] = field(default_factory=list)
    info_gain: float = 0.0
    equivalents: list[str] = field(default_factory=list)
    success_prob: float = 0.7
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RelationState:
    """关系状态（感知输入层的结构化结果）。

    P/M/A 三状态量（ALG-01），均为 0..1 可测代理：
      P 势能：好感/吸引/信息充分度；
      M 势头：近期好感斜率、对方主动投入占比；
      A 对齐：航向与主线吻合度（来自方向罗盘）。
    affection_total: 0..100 好感总分（结局指标的原始量）。
    active_conflicts: 当前未消解的冲突/相制描述。
    scene: 场景标签（线上/线下独处/群体/工作/异地，ALG-10）。
    budget_remaining: 各资源预算剩余。
    history_totals: 近期好感总分序列（用于斜率/势头，旧→新）。
    """

    stage: int = 2
    P: float = 0.5
    M: float = 0.5
    A: float = 0.5
    affection_total: float = 50.0
    belief: BeliefState = field(default_factory=BeliefState)
    npcs: list[NpcState] = field(default_factory=list)
    active_conflicts: list[str] = field(default_factory=list)
    scene: str = "online"
    budget_remaining: dict[str, float] = field(default_factory=_default_budget)
    history_totals: list[float] = field(default_factory=list)


@dataclass
class DecisionContext:
    """一次决策的完整输入（SMDP/CMDP/POMDP 在该上下文上求解）。

    horizon: 长期规划视野（时间步），用于 Q/V 长期值函数，根治逐任务贪心。
    now_index: 当前时间步（场景脚本与错峰据此推进）。
    """

    person_id: str
    state: RelationState
    candidates: list[TaskCandidate] = field(default_factory=list)
    horizon: int = 12
    now_index: int = 0
    rng_seed: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResult:
    """CMDP 主线硬闸结果（ALG-47：伤主线一律否决）。"""

    passed: bool = True
    mainline_impact: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class PlannedAction:
    """规划器输出（一个可执行动作及其长期决策依据）。

    mode: 四模式之一。
    q: 长期动作值 Q(s,a)（含折扣的未来累积收益），替代即时静态分。
    v_after: 执行后预计状态值 V(s')。
    expected_reward: 单步多目标期望向量。
    success_prob: 结合信念后的成功概率。
    cvar: 风险敏感下行指标（CVaR，越大表示尾部风险越大，ALG-26）。
    gate: 主线硬闸结果。
    detour: 若为变轨迂回，记录 {type, path:[候选id], heading_kept:bool, momentum_kept:bool}。
    pareto: 帕累托信息 {is_dominated:bool, front_size:int, weights:dict}。
    belief_after: 执行（含试探）后预计信念状态。
    alternatives: 其余 2-3 个尊重自主的候选 id（ALG-29）。
    rationale: 可解释依据（说明用了哪些状态量，ALG-31）。
    """

    candidate_id: str
    title: str
    mode: str = MODE_GLIDE
    q: float = 0.0
    v_after: float = 0.0
    expected_reward: dict[str, float] = field(default_factory=_default_reward)
    success_prob: float = 0.7
    cvar: float = 0.0
    gate: GateResult = field(default_factory=GateResult)
    detour: Optional[dict[str, Any]] = None
    pareto: dict[str, Any] = field(default_factory=dict)
    belief_after: Optional[BeliefState] = None
    alternatives: list[str] = field(default_factory=list)
    rationale: str = ""
    candidate: Optional[TaskCandidate] = None


# ------------------------------------------------------------------ #
# 策略协议：benchmark 用同一接口驱动 A/B 两组策略
# ------------------------------------------------------------------ #
@runtime_checkable
class Policy(Protocol):
    """决策策略协议。A=贪心静态打分、B=长期优化助推-滑翔都实现该接口。"""

    name: str

    def decide(self, ctx: DecisionContext) -> PlannedAction:
        """返回单个规划动作（benchmark 每一步调用一次）。"""
        ...

    def recommend(self, ctx: DecisionContext, top_n: int = 3) -> list[PlannedAction]:
        """返回 top_n 个可选动作（task_engine 多候选推荐用）。"""
        ...


# ------------------------------------------------------------------ #
# 具体规划器固定落点（算法核心子代理实现；task_engine 与 benchmark 按此导入）
# ------------------------------------------------------------------ #
PLANNER_MODULE = "services.boost_glide_planner"
PLANNER_CLASS = "BoostGlidePlanner"


def load_planner(config: Optional[dict] = None) -> "Policy":
    """按固定落点加载长期规划器（B 组）。导入失败由调用方降级处理。"""
    import importlib

    mod = importlib.import_module(PLANNER_MODULE)
    cls = getattr(mod, PLANNER_CLASS)
    return cls(config=config) if config is not None else cls()
