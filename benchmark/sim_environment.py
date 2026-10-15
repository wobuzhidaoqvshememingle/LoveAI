# -*- coding: utf-8 -*-
"""确定性关系演化环境（合成数据 benchmark）。

【合成假设声明】
本环境的转移模型——成功概率抽样、P/M/A 演化、硬推损伤、迂回折扣、NPC 干涉——
全部为**显式的合成假设（synthetic assumptions）**，仅用于 A/B 算法对照实验，
**不是真实用户行为数据**。任何"成功概率""好感变化"数字都由这里的规则产生，
benchmark 报告必须连同这些假设口径一起阅读。

环境与策略的唯一边界：
  * 环境只构造 DecisionContext（RelationState + 候选 TaskCandidate 列表）交给策略；
  * 策略只回 PlannedAction；环境绝不向策略暴露内部任务表 / RNG / 任务是否被 blocked。
  * 策略若要"看到上一步成败"，只能通过 ctx.extras["last_outcome"]（环境显式广播的反馈）。

A/B 配对语义：
  * 同一 Scenario + 同一 seed → 两个 SimEnvironment 的初始状态、RNG 第一抽完全一致；
  * 之后因策略选择不同而分叉，这正是配对对照想要的结果。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from services.decision_contract import (
    TaskCandidate,
    RelationState,
    BeliefState,
    NpcState,
    DecisionContext,
    PlannedAction,
    GateResult,
    OBJECTIVE_AFFECTION,
    OBJECTIVE_TRUST,
    OBJECTIVE_MAINLINE,
    OBJECTIVE_RECIPROCITY,
    OBJECTIVE_EMOTION_SAFETY,
    OBJECTIVE_GROWTH,
    BUDGET_TIME,
    BUDGET_ENERGY,
    BUDGET_EMOTION,
    BUDGET_MONEY,
    MODE_GLIDE,
)

# ------------------------------------------------------------------ #
# 任务在环境内部的状态（不透传给策略）
# ------------------------------------------------------------------ #
_TASK_PENDING = "pending"
_TASK_DONE = "done"
_TASK_BLOCKED = "blocked"

# 忠实折扣模型：按 detour.type 计额外时间步（合成假设口径，对称）
_DETOUR_OVERHEAD = {
    "equivalent": 0.0,   # 等价动作替换同一时段，不新增
    "stagger":    1.0,   # 改期错峰
    "buffer":     2.0,   # 额外插入缓冲动作
    "reform":     1.0,   # 重构结构撑大可行域（未新增动作）
}


@dataclass
class _TaskRecord:
    candidate: TaskCandidate
    status: str = _TASK_PENDING
    attempts: int = 0


@dataclass
class RunResult:
    """单次策略跑完一个场景后的全部指标（真实运行产出，无手填）。"""

    policy_name: str = ""
    scenario: str = ""
    seed: int = 0
    horizon: int = 0
    steps_used: int = 0
    residual_steps: int = 0

    total_tasks: int = 0
    done_tasks: int = 0
    blocked_tasks: int = 0
    pending_left_tasks: int = 0
    completion_rate: float = 0.0          # 完成目标任务数 / 目标任务总数（诱饵不计入分母）
    full_set_achieved: bool = False        # 目标任务 100% 达成
    target_tasks: int = 0                  # 应达成的目标任务数（mainline_impact>=0）
    done_target: int = 0                   # 完成的目标任务数
    decoy_tasks: int = 0                   # 诱饵/捷径任务数（mainline_impact<0）
    rejected_decoy: int = 0                # 被 CMDP 正确拒绝的诱饵数

    discount_steps: float = 0.0            # 迂回额外时间步（零折扣=0）
    mainline_damage: float = 0.0           # 累积 |负 mainline_impact|
    mainline_damage_events: int = 0        # 伤主线的硬推次数
    hard_push_events: int = 0              # 硬推（失败损伤）总次数

    final_affection: float = 0.0
    final_stage: int = 0
    final_P: float = 0.0
    final_M: float = 0.0
    final_A: float = 0.0

    decision_ms_avg: float = 0.0
    decision_ms_total: float = 0.0
    wall_ms: float = 0.0

    # 聊天注入代理（证明"注入假聊天"是真注入）
    chat_injected: int = 0
    chat_proactive_ratio: Optional[float] = None
    chat_recalc_total: Optional[float] = None

    per_task_trace: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "scenario": self.scenario,
            "seed": self.seed,
            "horizon": self.horizon,
            "steps_used": self.steps_used,
            "residual_steps": self.residual_steps,
            "total_tasks": self.total_tasks,
            "done_tasks": self.done_tasks,
            "blocked_tasks": self.blocked_tasks,
            "pending_left_tasks": self.pending_left_tasks,
            "completion_rate": round(self.completion_rate, 4),
            "full_set_achieved": self.full_set_achieved,
            "discount_steps": round(self.discount_steps, 3),
            "mainline_damage": round(self.mainline_damage, 4),
            "mainline_damage_events": self.mainline_damage_events,
            "hard_push_events": self.hard_push_events,
            "final_affection": round(self.final_affection, 2),
            "final_stage": self.final_stage,
            "final_P": round(self.final_P, 3),
            "final_M": round(self.final_M, 3),
            "final_A": round(self.final_A, 3),
            "decision_ms_avg": round(self.decision_ms_avg, 3),
            "decision_ms_total": round(self.decision_ms_total, 3),
            "wall_ms": round(self.wall_ms, 2),
            "chat_injected": self.chat_injected,
            "chat_proactive_ratio": self.chat_proactive_ratio,
            "chat_recalc_total": self.chat_recalc_total,
        }


class SimEnvironment:
    """通用、确定性（可设 seed）的关系演化环境。

    用法：
        env = SimEnvironment(scenario, seed=123)
        result = env.run(policy)
    """

    def __init__(self, scenario: "Scenario", seed: int = 0, person_id: str = "bench_p1"):
        # 延迟 import 避免循环依赖；scenario 只用到 dataclass 字段
        from benchmark.scenario_generator import Scenario

        self.scenario: Scenario = scenario
        self.rng = random.Random(seed)
        self.seed = seed
        self.person_id = person_id

        # ---- 初始关系状态（合成假设：来自 scenario 配置）----
        self.state = RelationState(
            stage=scenario.stage,
            P=scenario.P,
            M=scenario.M,
            A=scenario.A,
            affection_total=scenario.affection_total,
            belief=BeliefState(
                confidence=scenario.belief_confidence,
                entropy=scenario.belief_entropy,
                distribution=dict(scenario.belief_distribution),
            ),
            npcs=[NpcState(**n) for n in scenario.npcs],
            active_conflicts=list(scenario.active_conflicts),
            scene=scenario.scene,
            budget_remaining=dict(scenario.budget),
            history_totals=[scenario.affection_total],
        )

        # 任务表（深拷贝候选，保证 A/B 两个 env 不共享可变对象）
        self.tasks: dict[str, _TaskRecord] = {
            t.id: _TaskRecord(candidate=_clone_candidate(t)) for t in scenario.tasks
        }

        self.now_index = 0
        self.horizon = scenario.horizon
        self.last_outcome: Optional[dict] = None
        # 脚本事件（意外/突变/早期预警），按 fired 索引消费
        self._events: list[dict] = sorted(
            [dict(e) for e in getattr(scenario, "events", [])],
            key=lambda e: e.get("at_step", 0),
        )
        self._events_fired: set[int] = set()
        self.early_warning_active: bool = False

        # 指标累加器
        self.discount_steps = 0.0
        self.mainline_damage = 0.0
        self.mainline_damage_events = 0
        self.hard_push_events = 0
        self.decision_ms: list[float] = []
        self.trace: list[dict] = []

    # ------------------------------------------------------------------ #
    # 候选可见性（deps 满足才算可用；blocked 不透传给策略，由策略自己从
    # extras["blocked_tasks"] 得知——贪心据此"跳过→任务未达成"）
    # ------------------------------------------------------------------ #
    def _deps_satisfied(self, rec: _TaskRecord) -> bool:
        """HTN 前置依赖。

        deps: list[str]——全部必须完成（硬前置）。
        raw["dep_any_of"]: list[list[str]]——每组至少完成一个即视为满足
            （等价后继路径：走 t_alt_cafe 也能解锁主线）。
        """
        for d in rec.candidate.deps:
            if self.tasks[d].status != _TASK_DONE:
                return False
        for group in rec.candidate.raw.get("dep_any_of", []) or []:
            if not any(self.tasks[g].status == _TASK_DONE for g in group):
                return False
        return True

    def _task_scene_ok(self, rec: _TaskRecord) -> bool:
        """场景过滤：任务在 raw["scenes"] 里声明了可做场景集时，
        当前 scene 不在集合内则不可做（线上/线下独处/群体/工作/异地）。"""
        allowed = rec.candidate.raw.get("scenes")
        if not allowed:
            return True
        return self.state.scene in allowed

    def available_candidates(self) -> list[TaskCandidate]:
        out: list[TaskCandidate] = []
        for rec in self.tasks.values():
            if rec.status == _TASK_PENDING and self._deps_satisfied(rec) and self._task_scene_ok(rec):
                out.append(rec.candidate)
        return out

    # ------------------------------------------------------------------ #
    # 脚本事件（意外 / 早期预警 / 关系突变）——合成假设
    # ------------------------------------------------------------------ #
    def _fire_due_events(self) -> list[dict]:
        """消费 at_step <= now_index 且未触发的事件，返回本轮触发列表。"""
        fired = []
        for i, ev in enumerate(self._events):
            if i in self._events_fired:
                continue
            if ev.get("at_step", 0) > self.now_index:
                continue
            self._events_fired.add(i)
            kind = ev.get("type")
            payload = ev.get("payload", {}) or {}
            if kind == "mutation_warning":
                # ALG-09 早期预警：突变前 1-2 步出现可识别信号
                # （仅设 early_warning 标志，不加 active_conflicts——它是信号不是任务冲突）
                self.early_warning_active = True
            elif kind == "mutation_cliff":
                # 断崖式冷淡：M/A/好感骤降
                self.state.M = max(0.0, self.state.M + float(payload.get("dM", -0.3)))
                self.state.A = max(0.0, self.state.A + float(payload.get("dA", -0.2)))
                self.state.P = max(0.0, self.state.P + float(payload.get("dP", -0.1)))
                self.state.affection_total = max(
                    0.0, self.state.affection_total + float(payload.get("d_affection", -10.0)))
                self.early_warning_active = False
            elif kind == "incident":
                # 意外事件：临时切换 scene。不加入 active_conflicts——
                # scene 切换不是任务间冲突，否则 B 误走 buffer。
                new_scene = payload.get("set_scene")
                if new_scene:
                    self.state.scene = new_scene
            fired.append({"at_step": self.now_index, "type": kind, "note": payload.get("note", kind)})
        return fired

    # ------------------------------------------------------------------ #
    # 转移模型（全部为合成假设）
    # ------------------------------------------------------------------ #
    def _adjusted_success_prob(self, cand: TaskCandidate, planned: PlannedAction) -> float:
        """合成假设：基线 success_prob 受 P/M/A、NPC stance/influence、冲突、信念、迂回加成。"""
        p = cand.success_prob
        p += 0.20 * (self.state.P - 0.5)   # 势能高 → 更顺
        p += 0.12 * (self.state.M - 0.5)   # 势头好 → 更顺
        # NPC 干涉（合成假设）：负 stance 拉低成功率，正 stance 助攻
        for npc in self.state.npcs:
            if npc.stance < 0:
                p -= npc.influence * abs(npc.stance) * npc.tie_strength
            else:
                p += npc.influence * npc.stance * npc.tie_strength * 0.5
        # 相制惩罚：还有别的 pending 任务占用同一 conflict_tag → 现实阻碍
        my_tags = set(cand.conflict_tags)
        for other in self.tasks.values():
            if other.candidate.id == cand.id or other.status != _TASK_PENDING:
                continue
            if my_tags & set(other.candidate.conflict_tags):
                p -= 0.15
        # 信念置信度微调（POMDP：置信高略增成功率）
        p += 0.05 * (self.state.belief.confidence - 0.5)
        # 策略选了迂回/等价路径 → 现实中这是更安全的走法
        if planned.detour is not None:
            p += 0.15
        return max(0.05, min(0.95, p))

    def _apply_success(self, cand: TaskCandidate) -> None:
        """合成假设：成功 → 把 reward 多目标向量折算进 P/M/A/affection_total。"""
        rw = cand.reward
        aff = rw.get(OBJECTIVE_AFFECTION, 0.0)
        trust = rw.get(OBJECTIVE_TRUST, 0.0)
        main = rw.get(OBJECTIVE_MAINLINE, 0.0)

        self.state.affection_total = max(
            0.0, min(100.0, self.state.affection_total + aff * 0.8)
        )
        self.state.P = max(0.0, min(1.0, self.state.P + 0.10 * aff + 0.05 * trust))
        self.state.M = max(0.0, min(1.0, self.state.M + 0.06 * aff))
        self.state.A = max(0.0, min(1.0, self.state.A + 0.05 * main + 0.05 * cand.mainline_impact))
        # 关系阶段随好感总分爬（合成假设：每 10 分一档，0..9）
        self.state.stage = int(max(0.0, min(9.0, self.state.affection_total / 10.0)))

    def _apply_hard_push_damage(self, cand: TaskCandidate) -> float:
        """合成假设：硬推失败 → 情绪/主线损伤 + M 下滑。返回扣掉的好感分。"""
        dmg = 6.0 + 4.0 * abs(cand.mainline_impact)
        self.state.affection_total = max(0.0, self.state.affection_total - dmg)
        self.state.M = max(0.0, self.state.M - 0.18)
        self.state.P = max(0.0, self.state.P - 0.08)
        if cand.mainline_impact < 0:
            self.mainline_damage += abs(cand.mainline_impact)
            self.mainline_damage_events += 1
        self.state.stage = int(max(0.0, min(9.0, self.state.affection_total / 10.0)))
        return dmg

    # ------------------------------------------------------------------ #
    # 一步决策 + 转移
    # ------------------------------------------------------------------ #
    def step(self, policy: Any) -> Optional[PlannedAction]:
        # 先消费脚本事件（意外/早期预警/突变），让策略看到最新状态
        fired = self._fire_due_events()
        cands = self.available_candidates()
        if not cands:
            # 无可用候选：推进 1 步让脚本事件（切场景/突变）有机会触发，
            # 不直接终止（否则 scene 永不切换的场景会死循环式早停）。
            self.now_index += 1
            self.trace.append({
                "now_index": self.now_index, "candidate_id": "(none)",
                "title": "等待事件/场景切换", "adjusted_success_prob": 0.0,
                "success": False, "detour": False, "events_fired": fired,
                "affection_after": round(self.state.affection_total, 2),
                "P": round(self.state.P, 3), "M": round(self.state.M, 3),
                "A": round(self.state.A, 3),
            })
            return None

        blocked_ids = [
            rec.candidate.id for rec in self.tasks.values()
            if rec.status == _TASK_BLOCKED
        ]
        ctx = DecisionContext(
            person_id=self.person_id,
            state=self.state,
            candidates=cands,
            horizon=self.horizon,
            now_index=self.now_index,
            rng_seed=self.seed,
            extras={
                "last_outcome": self.last_outcome,
                "blocked_tasks": blocked_ids,
                "early_warning": self.early_warning_active,
                "events_fired": fired,
            },
        )

        t0 = time.perf_counter()
        planned = policy.decide(ctx)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        self.decision_ms.append(dt_ms)

        # 兜底：策略若返回不在候选里的 id，退到第一个候选（绝不读内部表）
        cand = next((c for c in cands if c.id == planned.candidate_id), cands[0])
        rec = self.tasks[cand.id]

        # CMDP 硬闸尊重：B 明确返回 gate.passed=False（拒绝所有伤主线/超预算动作）
        # → 环境不执行、不损伤、不扣预算，仅推进 0 步（策略本轮选择"不动"）。
        if planned.gate is not None and not planned.gate.passed:
            self.last_outcome = {"candidate_id": cand.id, "success": False,
                                  "skipped_by_gate": True}
            self.trace.append({
                "now_index": self.now_index, "candidate_id": cand.id,
                "title": cand.title, "adjusted_success_prob": 0.0,
                "success": False, "detour": False, "events_fired": fired,
                "skipped_by_gate": True,
                "affection_after": round(self.state.affection_total, 2),
                "P": round(self.state.P, 3), "M": round(self.state.M, 3),
                "A": round(self.state.A, 3),
            })
            # 硬闸拒绝时不能无限空转：若所有候选都被拒，直接结束
            self.now_index += 1  # 占 1 步表示"犹豫/空转"
            return planned

        rec.attempts += 1

        succ_p = self._adjusted_success_prob(cand, planned)
        success = self.rng.random() < succ_p
        is_detour = planned.detour is not None

        overhead = 0.0
        # ---- 忠实折扣模型（按 detour.type 计额外时间步）----
        # 修正说明：旧版每 detour 一律 +2 过粗；现按现实时间成本对称计：
        #   equivalent 等价动作占用同一时段（替换而非新增）→ 0 额外步
        #   stagger    改期错峰                       → +1
        #   buffer     额外插入缓冲动作               → +2
        #   reform     重构结构撑大可行域（未新增动作）→ +1
        # A 组 detour 恒 None，故 A 折扣恒 0，不受此修正影响。
        if success:
            self._apply_success(cand)
            rec.status = _TASK_DONE
            outcome = {"candidate_id": cand.id, "success": True, "detour": is_detour}
            if is_detour:
                dtype = (planned.detour or {}).get("type", "equivalent")
                overhead = _DETOUR_OVERHEAD.get(dtype, 0.0)
                self.discount_steps += overhead
        else:
            if is_detour:
                dtype = (planned.detour or {}).get("type", "equivalent")
                overhead = _DETOUR_OVERHEAD.get(dtype, 0.0)
                self.discount_steps += overhead
                outcome = {
                    "candidate_id": cand.id, "success": False,
                    "detour": True, "no_damage": True, "detour_type": dtype,
                }
            else:
                # 硬推失败 → 损伤
                self.hard_push_events += 1
                dmg = self._apply_hard_push_damage(cand)
                # 口径修正：普通任务(mainline_impact>=0)失败可重试，不永久 block；
                # 只有诱饵/捷径(mainline_impact<0)硬推失败才 block（不该做）。
                if cand.mainline_impact < 0.0:
                    rec.status = _TASK_BLOCKED
                outcome = {
                    "candidate_id": cand.id, "success": False,
                    "detour": False, "hard_push_damage": dmg,
                }

        # 信念更新（合成假设：info_gain>0 → 置信上升、熵下降）
        if cand.info_gain > 0:
            b = self.state.belief
            b.confidence = min(1.0, b.confidence + cand.info_gain)
            b.entropy = max(0.0, b.entropy - cand.info_gain)

        # 扣预算
        for k, v in cand.cost.items():
            self.state.budget_remaining[k] = max(
                0.0, self.state.budget_remaining.get(k, 0.0) - v
            )

        # 时间推进：名义 duration + 迂回 overhead
        self.now_index += cand.duration + int(overhead)
        self.state.history_totals.append(self.state.affection_total)
        self.last_outcome = outcome

        self.trace.append({
            "now_index": self.now_index,
            "candidate_id": cand.id,
            "title": cand.title,
            "adjusted_success_prob": round(succ_p, 3),
            "success": success,
            "detour": is_detour,
            "detour_type": (planned.detour or {}).get("type", "") if is_detour else "",
            "events_fired": fired,
            "affection_after": round(self.state.affection_total, 2),
            "P": round(self.state.P, 3),
            "M": round(self.state.M, 3),
            "A": round(self.state.A, 3),
        })
        return planned

    # ------------------------------------------------------------------ #
    # 跑完整场景
    # ------------------------------------------------------------------ #
    def run(self, policy: Any) -> RunResult:
        wall0 = time.perf_counter()
        steps = 0
        while self.now_index < self.horizon:
            planned = self.step(policy)
            steps += 1
            # step() 返回 None 表示"本步无候选、空转 1 步等事件"，不 break。
            pending_left = [
                r for r in self.tasks.values()
                if r.status == _TASK_PENDING and self._deps_satisfied(r)
            ]
            if not pending_left and self.now_index >= self.horizon:
                break
            # 连续空转保护：无 pending 目标任务且无事件待触发则停
            has_events_pending = any(i not in self._events_fired
                                     for i in range(len(self._events)))
            if not pending_left and not has_events_pending:
                break
        wall_ms = (time.perf_counter() - wall0) * 1000.0

        total = len(self.tasks)
        done = sum(1 for r in self.tasks.values() if r.status == _TASK_DONE)
        blocked = sum(1 for r in self.tasks.values() if r.status == _TASK_BLOCKED)
        pending_left = total - done - blocked

        # 口径修正：诱饵/捷径（mainline_impact<0）不是目标任务，不计入分母。
        # 被 CMDP 正确拒绝的诱饵单独记 rejected_decoy；A 选诱饵则记损伤。
        target_ids = [tid for tid, r in self.tasks.items()
                      if r.candidate.mainline_impact >= 0.0]
        decoy_ids = [tid for tid, r in self.tasks.items()
                     if r.candidate.mainline_impact < 0.0]
        done_target = sum(1 for tid in target_ids
                          if self.tasks[tid].status == _TASK_DONE)
        rejected_decoy = sum(1 for tid in decoy_ids
                             if self.tasks[tid].status != _TASK_DONE)

        return RunResult(
            policy_name=getattr(policy, "name", "unknown"),
            scenario=self.scenario.name,
            seed=self.seed,
            horizon=self.horizon,
            steps_used=steps,
            residual_steps=max(0, self.horizon - self.now_index),
            total_tasks=total,
            done_tasks=done,
            blocked_tasks=blocked,
            pending_left_tasks=pending_left,
            completion_rate=(done_target / len(target_ids)) if target_ids else 1.0,
            full_set_achieved=(done_target == len(target_ids)),
            target_tasks=len(target_ids),
            done_target=done_target,
            decoy_tasks=len(decoy_ids),
            rejected_decoy=rejected_decoy,
            discount_steps=self.discount_steps,
            mainline_damage=self.mainline_damage,
            mainline_damage_events=self.mainline_damage_events,
            hard_push_events=self.hard_push_events,
            final_affection=self.state.affection_total,
            final_stage=self.state.stage,
            final_P=self.state.P,
            final_M=self.state.M,
            final_A=self.state.A,
            decision_ms_avg=(sum(self.decision_ms) / len(self.decision_ms)) if self.decision_ms else 0.0,
            decision_ms_total=sum(self.decision_ms),
            wall_ms=wall_ms,
            per_task_trace=self.trace,
        )


def _clone_candidate(c: TaskCandidate) -> TaskCandidate:
    """深拷贝候选，保证 A/B 两个 env 不共享可变 dict/list。"""
    return TaskCandidate(
        id=c.id, title=c.title, level=c.level, category=c.category,
        direction=c.direction, difficulty=c.difficulty,
        reward=dict(c.reward), cost=dict(c.cost), duration=c.duration,
        mainline_impact=c.mainline_impact, deps=list(c.deps),
        conflict_tags=list(c.conflict_tags), info_gain=c.info_gain,
        equivalents=list(c.equivalents), success_prob=c.success_prob,
        raw=dict(c.raw),
    )
