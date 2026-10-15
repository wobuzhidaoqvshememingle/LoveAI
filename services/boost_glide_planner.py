# -*- coding: utf-8 -*-
"""助推-滑翔长期决策核心（B 组策略）。

严格数学语言：
  * SMDP（半马尔可夫决策过程）：候选即"选项(option)"，带 duration 时间尺度；
    Q(s,a) = E[g(a)|s] + gamma^{d_a} * V(T(s,a))，V 用有限视野 rollout 贝尔曼备份；
  * CMDP（约束 MDP）：主线硬闸 GateResult，mainline_impact<0 一律否决，Q 再高也不选；
  * MORL（多目标强化学习）：在 OBJECTIVE_KEYS 向量上做标量化 + 帕累托非支配前沿；
  * POMDP：BeliefState 分布 + 贝叶斯更新，试探动作降熵，confidence 诚实；
  * HTN：主线→中→小由 level 与 mainline_impact 体现。

"钱学森弹道/最小作用量/共振"仅为命名修辞，不进算法本体。
本模块只依赖 decision_contract（冻结契约），不依赖任何 UI / DB，benchmark 可直接喂合成数据。
"""
from __future__ import annotations

import math
from typing import Any, Optional

from services.decision_contract import (
    BUDGET_KEYS,
    DETOUR_BUFFER,
    DETOUR_EQUIVALENT,
    DETOUR_REFORM,
    DETOUR_STAGGER,
    LEVEL_MAIN,
    MODE_BOOST,
    MODE_DIVERGE,
    MODE_DIVE,
    MODE_GLIDE,
    MODES,
    OBJECTIVE_AFFECTION,
    OBJECTIVE_EMOTION_SAFETY,
    OBJECTIVE_GROWTH,
    OBJECTIVE_KEYS,
    OBJECTIVE_MAINLINE,
    OBJECTIVE_RECIPROCITY,
    OBJECTIVE_TRUST,
    BeliefState,
    DecisionContext,
    GateResult,
    PlannedAction,
    RelationState,
    TaskCandidate,
)

__all__ = ["BoostGlidePlanner", "build_decision_context", "raw_to_task_cand", "compute_pma"]


# ------------------------------------------------------------------ #
# 数值工具
# ------------------------------------------------------------------ #
def _clip01(x: float) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.5
    if math.isnan(x) or math.isinf(x):
        return 0.5
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _safe_norm_vec(d: dict[str, float], keys: tuple[str, ...]) -> dict[str, float]:
    out = {k: 0.0 for k in keys}
    for k in keys:
        v = d.get(k, 0.0) if d else 0.0
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            out[k] = 0.0
    return out


# ------------------------------------------------------------------ #
# 策略主体
# ------------------------------------------------------------------ #
class BoostGlidePlanner:
    """长期优化"助推-滑翔"规划器，实现 Policy 协议。

    配置项（config dict，全部有默认值，缺字段走稳健默认）：
      gamma         折扣因子（SMDP 时间折扣），默认 0.92
      p_gate_neg    伤主线否决阈值（mainline_impact < 该值 → 否决），默认 0.0
      boost_p       P 低于此值进入"助推蓄势"，默认 0.50
      glide_m       M 高于此且 P 达标 → "滑翔顺势"，默认 0.40
      dive_p / dive_m 俯冲窗口阈值，默认 0.60 / 0.50
      max_cands     候选上限（性能红线），默认 24
      rollout_cap   单次 rollout 最大步数，默认 horizon
    """

    name = "boost_glide"

    def __init__(self, config: Optional[dict] = None) -> None:
        cfg = config or {}
        self.gamma = float(cfg.get("gamma", 0.94))
        self.p_gate_neg = float(cfg.get("p_gate_neg", 0.0))
        self.boost_p = float(cfg.get("boost_p", 0.48))
        self.glide_m = float(cfg.get("glide_m", 0.38))
        self.dive_p = float(cfg.get("dive_p", 0.58))
        self.dive_m = float(cfg.get("dive_m", 0.48))
        self.max_cands = int(cfg.get("max_cands", 24))
        self.rollout_cap = int(cfg.get("rollout_cap", 14))
        # 迂回时间成本（benchmark 按类型计折扣）：等价同槽=0、错峰=1、缓冲=2、重构=1
        self.detour_cost = {"equivalent": 0.0, "stagger": 1.0,
                            "buffer": 2.0, "reform": 1.0}
        self.detour_cost_lambda = float(cfg.get("detour_cost_lambda", 0.15))
        # ALG-36 延迟信用分配：动作 trace（实例级，按决策序列累积）
        self._credit_trace: list[dict[str, Any]] = []

    # ---- ALG-36：延迟信用分配（可运行骨架） ---- #
    def recent_trace(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(self._credit_trace[-limit:])

    def assign_credit(self, delayed_event: dict[str, Any]) -> list[dict[str, float]]:
        """对延迟出现的结果做归因标签：回看动作 trace，按奖励向量相关性与
        deps/主线影响，给早先动作打信用权重（和为 1）。

        delayed_event: {"target": 任务id, "delta": 收益变化, "mainline": bool}
        返回: [{"step": int, "candidate_id": str, "credit": float}, ...]
        """
        target = str(delayed_event.get("target", ""))
        is_main = bool(delayed_event.get("mainline", False))
        weights: list[dict[str, float]] = []
        for rec in self._credit_trace:
            w = 0.0
            if target and target in (rec.get("unblocks") or []):
                w += 0.6                      # 直接解锁目标任务的动作
            if is_main and rec.get("mainline_impact", 0.0) > 0.1:
                w += 0.3                      # 对主线有正贡献的动作
            if rec.get("candidate_id") == target:
                w += 0.8                      # 目标任务自身
            if w > 0:
                weights.append({"step": float(rec.get("step", -1)),
                                "candidate_id": str(rec.get("candidate_id", "")),
                                "credit": w})
        tot = sum(x["credit"] for x in weights) or 1.0
        for x in weights:
            x["credit"] = round(x["credit"] / tot, 3)
        return weights

    # ============================================================== #
    # Policy 协议
    # ============================================================== #
    def decide(self, ctx: DecisionContext) -> PlannedAction:
        ranked = self.recommend(ctx, top_n=1)
        if not ranked:
            # 全部被硬闸否决：返回一个中性 dive? 不——返回带 gate 信息的占位动作，
            # 但绝不选中任何伤主线候选。调用方（task_engine）应回退旧路径。
            cands = ctx.candidates or []
            fallback = cands[0] if cands else TaskCandidate(id="none", title="（无可选任务）")
            pa = PlannedAction(
                candidate_id=fallback.id, title=fallback.title,
                mode=MODE_BOOST, q=0.0, v_after=self._terminal_value(ctx.state),
                expected_reward=dict(fallback.reward),
                gate=GateResult(passed=False, mainline_impact=fallback.mainline_impact,
                                reasons=["全部候选未通过主线硬闸"]),
                rationale="无可通过主线硬闸的候选", candidate=fallback,
            )
            return pa
        return ranked[0]

    def recommend(self, ctx: DecisionContext, top_n: int = 3) -> list[PlannedAction]:
        state = ctx.state
        feats = self._score_all(ctx)
        # 只保留通过硬闸的候选进入"可选集"（CMDP：伤主线一律否决）
        feasible = [f for f in feats if f["gate"].passed]
        # 长期 Q 降序
        feasible.sort(key=lambda f: f["q"], reverse=True)
        out: list[PlannedAction] = []
        n = min(top_n, len(feasible))
        alternatives = [f["cand"].id for f in feasible]
        for i, f in enumerate(feasible[:n]):
            cand = f["cand"]
            pa = PlannedAction(
                candidate_id=cand.id,
                title=cand.title,
                mode=f["mode"],
                q=round(f["q"], 4),
                v_after=round(f["v_after"], 4),
                expected_reward={k: round(v, 4) for k, v in cand.reward.items()},
                success_prob=round(f["p_eff"], 3),
                cvar=round(f["cvar"], 4),
                gate=f["gate"],
                detour=f["detour"],
                pareto=f["pareto"],
                belief_after=f["belief_after"],
                alternatives=[a for j, a in enumerate(alternatives)
                              if j != feasible.index(f)][:3] if feasible else [],
                rationale=f["rationale"],
                candidate=cand,
            )
            out.append(pa)
        return out

    # ============================================================== #
    # 核心：逐候选 Q/V（SMDP 有限视野贝尔曼备份）
    # ============================================================== #
    def _read_profile(self, ctx: DecisionContext) -> dict[str, Any]:
        """从 extras 读双主体画像（ALG-22）；缺失走中性默认，benchmark 不喂也稳健。"""
        ex = ctx.extras or {}
        self_p = ex.get("self_profile") or {}
        other_p = ex.get("other_profile") or {}
        return {
            "self_efficacy": float(self_p.get("efficacy", 0.6) or 0.6),
            "self_locus": str(self_p.get("locus", "internal")),
            "self_cognitive": str(self_p.get("cognitive_style", "balanced")),
            "other_attachment": str(other_p.get("attachment", "secure")),
            "other_persona": str(other_p.get("personality", "")),
            "other_orientation": str(other_p.get("orientation", "")),
        }

    def _reaction_factor(self, state: RelationState, cand: TaskCandidate,
                         profile: dict[str, Any]) -> float:
        """ALG-25 对手建模 + ALG-22 心理适配：预测对方"读到我方动作后"的反应，
        以及我方效能对执行成功率的调节。返回 0..1 乘子。
        """
        f = 1.0
        # 对方依恋反应：回避型对"冒进低信任"动作（高affection/低trust）反应消极
        attach = profile.get("other_attachment", "secure")
        pushy = max(0.0, cand.reward.get(OBJECTIVE_AFFECTION, 0.0)) \
            - max(0.0, cand.reward.get(OBJECTIVE_TRUST, 0.0))
        if attach in ("avoidant", "fearful", "dismissing") and pushy > 0.15:
            f *= max(0.55, 1.0 - 0.35 * pushy)
        elif attach in ("secure",):
            f *= 1.05
        elif attach in ("anxious", "preoccupied") and pushy > 0.15:
            f *= 0.9   # 焦虑型对突进攻略易过度解读
        # 我方效能：低效能用户先小步赋能（ALG-23）——长/难动作成功率打折
        eff = _clip01(profile.get("self_efficacy", 0.6))
        d = max(1, int(cand.duration or 1))
        if eff < 0.5 and d >= 3:
            f *= max(0.6, 0.5 + eff)
        return _clip01(f)

    def _htn_shaping(self, cand: TaskCandidate, cands: list[TaskCandidate]) -> tuple[float, bool, list[str]]:
        """HTN 依赖感知：返回 (shaping, ready, unmet_deps)。
        ready = 无未满足前置；未就绪动作会被重度惩罚（现在做必失败/白做）。
        """
        remaining_ids = {c.id for c in cands}
        unmet = [d for d in (cand.deps or []) if d in remaining_ids]
        ready = len(unmet) == 0
        unlocks = sum(1 for o in cands if cand.id in (o.deps or []))
        chain = (1.4 if cand.level == LEVEL_MAIN
                 else 0.7 if cand.level == "mid" else 0.25)
        shaping = 0.0 if not ready else (0.8 * chain + 0.4 * unlocks)
        return shaping, ready, unmet

    def _score_all(self, ctx: DecisionContext) -> list[dict[str, Any]]:
        state = ctx.state
        cands = list(ctx.candidates or [])[: self.max_cands]
        profile = self._read_profile(ctx)
        # 视野自适应：基础 horizon + 任务链总时长（迂回吃时间后仍够解锁主链）
        chain_dur = sum(max(1, int(c.duration or 1)) for c in cands)
        horizon = max(int(ctx.horizon or 12), min(chain_dur, 40))
        weights = self._adaptive_weights(state)
        # 帕累托前沿（在可行候选的奖励向量上）
        pareto_map = self._pareto(cands, horizon)

        feats: list[dict[str, Any]] = []
        for cand in cands:
            gate = self._gate(cand, state)
            react = self._reaction_factor(state, cand, profile)
            p_eff = self._success_prob(state, cand) * react
            imm = p_eff * self._scalarize(cand.reward, weights)
            # 后继状态转移（近似模型）
            s_next = self._transition(state, cand, p_eff)
            remain = max(0, horizon - max(1, int(cand.duration or 1)))
            # 长期值：rollout 贝尔曼备份（沿 HTN 链推进，含完成奖励塑形）
            v_after = self._rollout_value(s_next, remain, horizon, cands, weights)
            q = imm + (self.gamma ** max(1, int(cand.duration or 1))) * v_after
            # HTN 依赖就绪塑形：未就绪(前置未完成)重度惩罚；就绪且解锁主链加分
            shaping, ready, unmet = self._htn_shaping(cand, cands)
            if not ready:
                q -= 1.5   # 前置未满足，现在做必失败/白做
            else:
                q += shaping
            # 风险敏感 CVaR（简化）：下行 = (1-p_eff) * 负奖励幅度
            neg = -min(0.0, self._scalarize(cand.reward, weights))
            cvar = (1.0 - p_eff) * neg

            mode = self._mode_for(state, cand, gate, cands)
            detour = self._detour_for(state, cand, cands, mode)
            # 迂回成本惩罚：主动选省时路径（等价0/错峰1/缓冲2/重构1）
            detour_cost = self.detour_cost.get(detour["type"], 0.0) if detour else 0.0
            q -= self.detour_cost_lambda * detour_cost
            belief_after = self._update_belief(state, cand, p_eff, gate)

            # ALG-36 trace：记录该动作的解锁关系，供延迟信用归因
            self._credit_trace.append({
                "step": ctx.now_index, "candidate_id": cand.id,
                "unblocks": [o.id for o in cands if cand.id in (o.deps or [])],
                "mainline_impact": float(cand.mainline_impact),
                "expected": round(imm, 4),
            })

            rationale = self._rationale(state, cand, p_eff, q, gate, mode, ready, unmet)
            feats.append({
                "cand": cand, "gate": gate, "p_eff": p_eff,
                "v_after": v_after, "q": q, "cvar": cvar,
                "mode": mode, "detour": detour,
                "belief_after": belief_after,
                "pareto": pareto_map.get(cand.id, {"is_dominated": False,
                                                   "front_size": 0,
                                                   "weights": {k: round(v, 3) for k, v in weights.items()}}),
                "rationale": rationale, "ready": ready,
            })
        return feats

    # ---- 近似转移模型（SMDP option 落地） ---- #
    def _transition(self, state: RelationState, cand: TaskCandidate,
                    p_eff: float) -> RelationState:
        r = cand.reward
        dP = 0.60 * p_eff * r.get(OBJECTIVE_AFFECTION, 0.0) \
            + 0.40 * p_eff * r.get(OBJECTIVE_TRUST, 0.0)
        # 势头 = 近期斜率的代理：正收益抬升动量，失败(1-p_eff)拖累
        dM = (0.50 * p_eff * r.get(OBJECTIVE_AFFECTION, 0.0)
              + 0.30 * p_eff * r.get(OBJECTIVE_TRUST, 0.0)
              - 0.15 * (1.0 - p_eff))
        dA = 0.10 * p_eff * r.get(OBJECTIVE_MAINLINE, 0.0) \
            + 0.05 * p_eff * cand.mainline_impact
        return RelationState(
            stage=state.stage,
            P=_clip01(state.P + dP),
            M=_clip01(state.M + dM),
            A=_clip01(state.A + dA),
            affection_total=max(0.0, min(100.0,
                               state.affection_total + 100.0 * p_eff * r.get(OBJECTIVE_AFFECTION, 0.0))),
            belief=state.belief, npcs=state.npcs,
            active_conflicts=state.active_conflicts, scene=state.scene,
            budget_remaining=state.budget_remaining,
            history_totals=state.history_totals,
        )

    def _success_prob(self, state: RelationState, cand: TaskCandidate) -> float:
        """结合 P 的有效成功概率：高 P 释放高价值动作，这是长期规划的引擎。"""
        base = _clip01(cand.success_prob)
        # 高风险高回报动作（affection 增益大）更依赖 P；维稳类动作几乎不依赖 P
        risk = max(0.0, cand.reward.get(OBJECTIVE_AFFECTION, 0.0))
        dep = 0.45 * min(1.0, risk)  # 0..0.45
        factor = (1.0 - dep) + dep * (0.30 + 0.70 * _clip01(state.P))
        return _clip01(base * factor)

    def _terminal_value(self, state: RelationState) -> float:
        """视野末端的关系价值（结局指标代理）：P/M/A 加权，扣负向 NPC 阻力。"""
        v = (0.45 * _clip01(state.P)
             + 0.35 * _clip01(state.M)
             + 0.20 * _clip01(state.A))
        v -= self._npc_drag(state)[0]
        return v

    def _npc_drag(self, state: RelationState) -> tuple[float, bool]:
        """ALG-24 NPC 联动：负向 NPC（情敌/反对家人，stance<0 且影响力高）
        对势头/对齐产生阻力；返回 (drag, has_opposition)。
        """
        drag = 0.0
        opposition = False
        for n in state.npcs or []:
            try:
                influence = _clip01(n.influence)
                stance = float(n.stance)
            except (TypeError, ValueError):
                continue
            if stance < -0.2 and influence >= 0.4:
                drag += influence * abs(stance) * 0.15
                opposition = True
        return min(0.4, drag), opposition

    def _rollout_value(self, state: RelationState, remaining: int, horizon: int,
                      cands: list[TaskCandidate], weights: dict[str, float]) -> float:
        """有限视野 rollout（HTN 基线策略）：维护 done-set，反复选"就绪且在主链上"的
        动作推进，完成一个就从池中移除、重算依赖就绪度；累积折扣奖励 + 末端完成价值。
        这是真实的贝尔曼备份，不是把即时分当长期值；沿链推进保证主链被解锁。
        """
        feasible = [c for c in cands if self._gate(c, state).passed]
        if not feasible or remaining <= 0:
            return self._terminal_value(state)
        # 预计算静态量（每候选标量化奖励只算一次，热循环里不再重复）
        static = [(c, self._scalarize(c.reward, weights),
                   1.4 if c.level == LEVEL_MAIN else 0.7 if c.level == "mid" else 0.25,
                   set(c.deps or [])) for c in feasible]
        total_main = sum(1 for c in feasible if c.mainline_impact > 0.1)
        V = 0.0
        s = state
        elapsed = 0
        steps = 0
        cap = min(self.rollout_cap, max(1, remaining))
        pool = static
        while remaining > 0 and pool and steps < cap:
            remaining_ids = {c[0].id for c in pool}
            best_a, best_g = None, -1e18
            for c, scalar, chain, depset in pool:
                p = self._success_prob(s, c)
                g = p * scalar
                # HTN 就绪塑形：未满足前置的动作现在做必失败，重度惩罚
                if depset & remaining_ids:
                    g -= 1.5
                else:
                    unlocks = sum(1 for o in pool if c.id in o[3])
                    g += 0.8 * chain + 0.4 * unlocks
                # 迂回成本：选省时路径（等价0/错峰1/缓冲2/重构1）
                if c.conflict_tags:
                    g -= self.detour_cost_lambda * self.detour_cost.get(
                        "reform" if c.mainline_impact > 0.2 else "stagger", 1.0)
                # 信念不确定时，高 info_gain 试探优先（提速降熵）
                if getattr(s.belief, "entropy", 0.0) > 0.5 and c.info_gain > 0:
                    g += 0.1
                if g > best_g:
                    best_g, best_a, best_c = g, c, (c, scalar, chain, depset)
            if best_a is None:
                break
            d = max(1, int(best_a.duration or 1))
            V += (self.gamma ** elapsed) * best_g
            s = self._transition(s, best_a, self._success_prob(s, best_a))
            pool = [x for x in pool if x[0].id != best_a.id]  # 完成即移出池
            elapsed += d
            remaining -= d
            steps += 1
        # 末端价值：P/M/A 关系值 + 主线任务完成度奖励（整集达成的塑形）
        left_main = sum(1 for c in pool if c[0].mainline_impact > 0.1)
        comp_bonus = 0.6 * (1.0 - left_main / max(1, total_main))
        V += (self.gamma ** elapsed) * (self._terminal_value(s) + comp_bonus)
        return V

    # ---- 多目标：自适应权重 + 帕累托 ---- #
    def _adaptive_weights(self, state: RelationState) -> dict[str, float]:
        w = {
            OBJECTIVE_AFFECTION: 0.20,
            OBJECTIVE_MAINLINE: 0.25,
            OBJECTIVE_TRUST: 0.18,
            OBJECTIVE_RECIPROCITY: 0.12,
            OBJECTIVE_EMOTION_SAFETY: 0.10,
            OBJECTIVE_GROWTH: 0.15,
        }
        if state.P < self.boost_p:           # 蓄势期：重吸引/信任，轻冒进主线
            w[OBJECTIVE_AFFECTION] += 0.10
            w[OBJECTIVE_TRUST] += 0.08
            w[OBJECTIVE_MAINLINE] -= 0.10
            w[OBJECTIVE_GROWTH] += 0.04     # ALG-23：培育能动性/成长
        if state.M < self.glide_m:           # 势头弱：重情绪安全/信任，修复优先
            w[OBJECTIVE_EMOTION_SAFETY] += 0.08
            w[OBJECTIVE_TRUST] += 0.05
            w[OBJECTIVE_MAINLINE] -= 0.08
        if state.A < 0.40:                    # 对齐差：重主线回正
            w[OBJECTIVE_MAINLINE] += 0.10
        tot = sum(max(0.0, x) for x in w.values()) or 1.0
        return {k: max(0.0, v) / tot for k, v in w.items()}

    def _scalarize(self, reward: dict[str, float], weights: dict[str, float]) -> float:
        r = _safe_norm_vec(reward, OBJECTIVE_KEYS)
        return sum(weights.get(k, 0.0) * r[k] for k in OBJECTIVE_KEYS)

    def _pareto(self, cands: list[TaskCandidate], horizon: int) -> dict[str, dict[str, Any]]:
        """在可行候选的奖励向量上求非支配前沿（最大化所有目标）。"""
        feas = [c for c in cands if c.mainline_impact >= self.p_gate_neg]
        recs = {c.id: _safe_norm_vec(c.reward, OBJECTIVE_KEYS) for c in feas}

        def dominates(u: dict[str, float], v: dict[str, float]) -> bool:
            no_worse = all(u[k] >= v[k] - 1e-9 for k in OBJECTIVE_KEYS)
            strictly = any(u[k] > v[k] + 1e-9 for k in OBJECTIVE_KEYS)
            return no_worse and strictly

        front: list[str] = []
        info: dict[str, dict[str, Any]] = {}
        for c in feas:
            u = recs[c.id]
            dom = False
            for o in feas:
                if o.id == c.id:
                    continue
                if dominates(recs[o.id], u):
                    dom = True
                    break
            if not dom:
                front.append(c.id)
        for c in feas:
            info[c.id] = {
                "is_dominated": c.id not in front,
                "front_size": len(front),
                "weights": {},
            }
        return info

    # ---- CMDP 主线硬闸 ---- #
    def _gate(self, cand: TaskCandidate, state: RelationState) -> GateResult:
        reasons: list[str] = []
        passed = True
        if cand.mainline_impact < self.p_gate_neg:
            passed = False
            reasons.append(f"伤主线(mainline_impact={cand.mainline_impact:.2f}<0)")
        # 预算硬约束（ALG-40）：消耗超过剩余预算 → 否决
        for bk in BUDGET_KEYS:
            cost = float(cand.cost.get(bk, 0.0) or 0.0)
            remain = float(state.budget_remaining.get(bk, 1.0) or 1.0)
            if cost > remain + 1e-6 and remain > 0:
                passed = False
                reasons.append(f"超{bk}预算(cost={cost:.2f}>remain={remain:.2f})")
        return GateResult(passed=passed, mainline_impact=cand.mainline_impact, reasons=reasons)

    # ---- 四模式状态机 ---- #
    def _mode_for(self, state: RelationState, cand: TaskCandidate,
                  gate: GateResult, cands: Optional[list[TaskCandidate]] = None) -> str:
        p, m, a = _clip01(state.P), _clip01(state.M), _clip01(state.A)
        # diverge：仅真实冲突（活跃冲突/负向 NPC/标签互斥）；有等价池但无冲突不算
        _drag, opposition = self._npc_drag(state)
        real_tag_conflict = False
        if cands is not None:
            my_tags = set(cand.conflict_tags or [])
            real_tag_conflict = bool(my_tags) and any(
                o.id != cand.id and my_tags & set(o.conflict_tags or []) for o in cands)
        if state.active_conflicts or opposition or real_tag_conflict:
            return MODE_DIVERGE
        # dive：窗口成熟（P/M 双高）且候选是高确定性主线收口动作
        if (p >= self.dive_p and m >= self.dive_m
                and cand.mainline_impact > 0.2 and cand.success_prob >= 0.6):
            return MODE_DIVE
        # boost：P 不足，先建吸引/信任/信息
        if p < self.boost_p:
            return MODE_BOOST
        # glide：P 足、M 足、航向对齐，借势浅弧度推进
        if m >= self.glide_m and a >= 0.40:
            return MODE_GLIDE
        return MODE_BOOST

    def _detour_for(self, state: RelationState, cand: TaskCandidate,
                    cands: list[TaskCandidate], mode: str) -> Optional[dict[str, Any]]:
        """冲突消解：按迂回成本全局最小化选方案，绝不做'按优先级取舍'。

        成本升序（heading/momentum 均保持）：
          equivalent(0) > stagger(1) ≈ reform(1) > buffer(2, 最后手段)
        buffer 仅当 equivalent/stagger/reform 都无法化解时才用。
        一个冲突只消解一次：完成后池内无冲突对手，后续步骤自然恢复 glide/dive。
        """
        if mode != MODE_DIVERGE:
            return None
        my_tags = set(cand.conflict_tags or [])
        has_tag_conflict = bool(my_tags) and any(
            o.id != cand.id and my_tags & set(o.conflict_tags or []) for o in cands)
        active = bool(state.active_conflicts)
        if not (has_tag_conflict or active or cand.equivalents):
            return None
        both_main = (cand.mainline_impact > 0.2 and any(
            o.id != cand.id and my_tags & set(o.conflict_tags or [])
            and o.mainline_impact > 0.2 for o in cands))
        # —— 成本最小化选择：从最低成本往上取第一个适用的 —— #
        if cand.equivalents:
            # 0 成本：占用同一时段的等价动作直接绕开轻度干扰
            dtype = DETOUR_EQUIVALENT
            path = list(cand.equivalents)
        elif has_tag_conflict:
            # 时间互斥：错峰(1)；双方都主线都得做 → 重构(1)
            dtype = DETOUR_REFORM if both_main else DETOUR_STAGGER
            path = [cand.id]
        elif active:
            # 活跃冲突且无更便宜方案 → buffer(2) 仅作最后手段
            dtype = DETOUR_BUFFER
            path = [cand.id]
        else:
            return None
        return {"type": dtype, "path": path,
                "cost": self.detour_cost.get(dtype, 1.0),
                "heading_kept": True, "momentum_kept": True}

    # ---- POMDP 信念更新（第二阶段骨架，真实贝叶斯） ---- #
    def _update_belief(self, state: RelationState, cand: TaskCandidate,
                       p_eff: float, gate: GateResult) -> BeliefState:
        b = state.belief
        if not b.distribution:
            # 无先验：诚实给低置信，info_gain 高的试探动作可抬 confidence
            conf = 0.5 + 0.3 * _clip01(cand.info_gain)
            return BeliefState(distribution={}, entropy=1.0, confidence=_clip01(conf))
        new_dist: dict[str, float] = {}
        for k, prob in b.distribution.items():
            k_low = k.lower()
            accept = any(t in k_low for t in ("accept", "yes", "high", "同意", "接", "高"))
            like = p_eff if accept else (1.0 - p_eff) * 0.6 + 0.1
            new_dist[k] = max(1e-6, float(prob)) * max(1e-6, like)
        tot = sum(new_dist.values()) or 1.0
        new_dist = {k: v / tot for k, v in new_dist.items()}
        entropy = -sum(p * math.log(p + 1e-9) for p in new_dist.values() if p > 0)
        conf = 1.0 - min(1.0, entropy / 2.0)
        return BeliefState(distribution=new_dist, entropy=round(entropy, 4),
                           confidence=round(_clip01(conf), 4))

    # ---- 可解释依据 ---- #
    def _rationale(self, state: RelationState, cand: TaskCandidate,
                   p_eff: float, q: float, gate: GateResult, mode: str,
                   ready: bool = True, unmet: Optional[list[str]] = None) -> str:
        bits = [f"P={state.P:.2f}/M={state.M:.2f}/A={state.A:.2f}",
                f"mode={mode}", f"Q={q:.3f}", f"p={p_eff:.2f}"]
        if cand.mainline_impact:
            bits.append(f"主线影响={cand.mainline_impact:+.2f}")
        if cand.info_gain:
            bits.append(f"信息增益={cand.info_gain:.2f}")
        if not ready:
            bits.append(f"前置未完成:{','.join(unmet or [])}")
        if gate.reasons:
            bits.append("硬闸:" + "/".join(gate.reasons))
        return "；".join(bits)


# ------------------------------------------------------------------ #
# task_engine 桥接：原始任务字典 → DecisionContext（P/M/A 真实计算）
# ------------------------------------------------------------------ #
# 类别 → MORL 奖励向量（数据驱动：按 category 映射，不硬编码标题/id）
_CATEGORY_REWARD: dict[str, dict[str, float]] = {
    "邀约推进": {OBJECTIVE_AFFECTION: 0.35, OBJECTIVE_MAINLINE: 0.30,
                OBJECTIVE_TRUST: -0.05, OBJECTIVE_RECIPROCITY: 0.10,
                OBJECTIVE_GROWTH: 0.15},
    "升温试探": {OBJECTIVE_AFFECTION: 0.40, OBJECTIVE_TRUST: -0.15,
                OBJECTIVE_EMOTION_SAFETY: -0.05, OBJECTIVE_GROWTH: 0.10},
    "关系维护": {OBJECTIVE_AFFECTION: 0.10, OBJECTIVE_TRUST: 0.25,
                OBJECTIVE_EMOTION_SAFETY: 0.20, OBJECTIVE_RECIPROCITY: 0.10},
    "冲突修复": {OBJECTIVE_TRUST: 0.35, OBJECTIVE_EMOTION_SAFETY: 0.30,
                OBJECTIVE_MAINLINE: 0.15, OBJECTIVE_AFFECTION: -0.05},
    "后撤观察": {OBJECTIVE_EMOTION_SAFETY: 0.20, OBJECTIVE_RECIPROCITY: 0.25,
                OBJECTIVE_AFFECTION: -0.10, OBJECTIVE_TRUST: 0.10},
    "主线目标": {OBJECTIVE_MAINLINE: 0.45, OBJECTIVE_AFFECTION: 0.15,
                OBJECTIVE_GROWTH: 0.20},
}
_DIFFICULTY_P = {"简单": 0.90, "普通": 0.75, "困难": 0.55, "地狱": 0.40}
_DIFFICULTY_DUR = {"简单": 1, "普通": 2, "困难": 3, "地狱": 4}


def raw_to_task_cand(raw: dict[str, Any], idx: int = 0,
                     direction: str = "维稳") -> TaskCandidate:
    """task_engine 原始任务字典 → TaskCandidate（缺字段走契约默认值，稳健）。"""
    title = str(raw.get("title", f"任务{idx}"))
    category = str(raw.get("category", ""))
    reward = dict(_CATEGORY_REWARD.get(category, {OBJECTIVE_AFFECTION: 0.10,
                                                  OBJECTIVE_TRUST: 0.10}))
    # 归一化到 OBJECTIVE_KEYS
    reward = {k: float(reward.get(k, 0.0)) for k in OBJECTIVE_KEYS}
    difficulty = str(raw.get("difficulty", "普通"))
    success_prob = _DIFFICULTY_P.get(difficulty, 0.70)
    duration = _DIFFICULTY_DUR.get(difficulty, 2)
    # base 微调（旧字段，弱信号）
    base = float(raw.get("base", 1.0) or 1.0)
    success_prob = _clip01(success_prob * (0.9 + 0.1 * base))
    # 主线影响：主线目标/邀约推进正向；后撤/升温试探近零偏负
    if category == "主线目标":
        mainline_impact = 0.30
    elif category == "邀约推进":
        mainline_impact = 0.15
    elif category in ("后撤观察",):
        mainline_impact = -0.05
    else:
        mainline_impact = 0.0
    # info_gain：低信息阶段的试探类动作
    info_gain = 0.3 if category in ("升温试探", "邀约推进") else 0.05
    return TaskCandidate(
        id=str(raw.get("id", f"cand_{idx}_{abs(hash(title)) % 100000}")),
        title=title,
        level=LEVEL_MAIN if category == "主线目标" else "small",
        category=category,
        direction=str(raw.get("direction", direction)),
        difficulty=difficulty,
        reward=reward,
        cost={},
        duration=duration,
        mainline_impact=mainline_impact,
        info_gain=info_gain,
        success_prob=success_prob,
        raw=dict(raw),
    )


def compute_pma(person_id: str,
                affection: Any = None,
                compass_result: Optional[dict] = None,
                stage: Optional[int] = None) -> tuple[float, float, float, float, list[float]]:
    """从真实引擎计算 P/M/A 代理。任何异常都降级为中性默认，绝不抛。"""
    P = M = A = 0.5
    affection_total = 50.0
    history_totals: list[float] = []
    try:
        if affection is not None:
            scores = affection.get_current(person_id)
            try:
                affection_total = float(affection.get_total_with_bonus(person_id, scores))
            except Exception:  # noqa: BLE001
                affection_total = float(affection.get_total(scores))
            # P = 好感总分归一化(60%) + 信息完备度(40%)
            hist = affection.get_history(person_id, days=30) or []
            history_totals = [float(h.get("total_score", affection_total)) for h in reversed(hist)]
            info_completeness = _clip01(len(history_totals) / 7.0)
            P = _clip01(0.60 * (affection_total / 100.0) + 0.40 * info_completeness)
            # M = 近期斜率(50%) + 对方主动率(50%)
            slope = 0.0
            if len(history_totals) >= 2:
                span = max(1, len(history_totals) - 1)
                slope = _clip01(0.5 + (history_totals[-1] - history_totals[0]) / (span * 12.0))
            try:
                proactive = float(affection.get_proactive_ratio(person_id))
            except Exception:  # noqa: BLE001
                proactive = 0.3
            M = _clip01(0.5 * slope + 0.5 * proactive)
    except Exception:  # noqa: BLE001
        P, M, affection_total = 0.5, 0.5, 50.0
    # A = 方向罗盘 alignment_score（缺失用 gates 兜底，再缺失中性 0.5）
    try:
        if compass_result is not None:
            a = compass_result.get("alignment_score", None)
            if a is None:
                gates = compass_result.get("gates", {}) or {}
                passes = [g.get("pass", True) for g in gates.values() if isinstance(g, dict)]
                a = 0.5 + 0.2 * (sum(1 for p in passes if p) - len(passes)) / max(1, len(passes))
            A = _clip01(float(a))
    except Exception:  # noqa: BLE001
        A = 0.5
    return P, M, A, affection_total, history_totals


def _persona_to_profiles(persona: Optional[dict]) -> dict[str, dict[str, Any]]:
    """把 task_engine 加载的画像 dict 映射为规划器双主体画像（ALG-22）。

    规则稳健：字段缺失/结构异常一律回退中性默认，绝不抛、绝不编造。
      other_profile.attachment: 依恋类型 → avoidant/anxious/secure
      self_profile.efficacy:  用户效能（画像无此数据时给中性 0.5）
    """
    other: dict[str, Any] = {"attachment": "secure", "personality": "", "orientation": ""}
    selfp: dict[str, Any] = {"efficacy": 0.5, "locus": "internal",
                             "cognitive_style": "balanced"}
    if not persona or not isinstance(persona, dict):
        return {"self_profile": selfp, "other_profile": other}
    try:
        att = persona.get("attachment") or {}
        if isinstance(att, dict):
            raw = str(att.get("primary_type") or att.get("type") or "")
        else:
            raw = str(att)
        rl = raw.lower()
        if any(k in raw for k in ("回避", "恐惧")) or any(k in rl for k in ("avoid", "dismiss", "fearful")):
            other["attachment"] = "avoidant"
        elif any(k in raw for k in ("焦虑",)) or any(k in rl for k in ("anxious", "preoccupied")):
            other["attachment"] = "anxious"
        else:
            other["attachment"] = "secure"
        # 人格/定向：有就透传，没有留空（中性）
        pers = persona.get("personality") or persona.get("persona_type") or ""
        other["personality"] = str(pers) if isinstance(pers, str) else ""
        ori = persona.get("orientation") or ""
        other["orientation"] = str(ori) if isinstance(ori, str) else ""
        # 用户效能：画像通常不含，取 user_profile 里的 efficacy；缺省 0.5
        up = persona.get("user_profile") or persona.get("self_profile") or {}
        if isinstance(up, dict):
            eff = up.get("efficacy")
            try:
                selfp["efficacy"] = _clip01(float(eff))
            except (TypeError, ValueError):
                selfp["efficacy"] = 0.5
    except Exception:  # noqa: BLE001 - 画像异常不影响推荐
        pass
    return {"self_profile": selfp, "other_profile": other}


def build_decision_context(person_id: str,
                           raw_candidates: list[dict[str, Any]],
                           *,
                           affection: Any = None,
                           compass_result: Optional[dict] = None,
                           stage: Optional[int] = None,
                           direction: str = "维稳",
                           horizon: int = 12,
                           now_index: int = 0,
                           persona: Optional[dict] = None) -> DecisionContext:
    """task_engine 调用：原始任务 + 引擎数据 → DecisionContext。

    persona（ALG-22 心理适配）：映射为 ctx.extras 的 self_profile/other_profile；
    缺失或异常时回退中性默认，不影响推荐主流程。
    """
    P, M, A, aff_total, hist = compute_pma(person_id, affection, compass_result, stage)
    cands = [raw_to_task_cand(raw, i, direction) for i, raw in enumerate(raw_candidates or [])]
    state = RelationState(
        stage=int(stage) if stage is not None else 2,
        P=P, M=M, A=A, affection_total=aff_total,
        history_totals=hist,
    )
    profiles = _persona_to_profiles(persona)
    return DecisionContext(person_id=person_id, state=state, candidates=cands,
                           horizon=horizon, now_index=now_index, extras=profiles)
