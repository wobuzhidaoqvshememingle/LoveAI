# -*- coding: utf-8 -*-
"""benchmark 用策略组。

A 组 GreedyPolicy：忠实移植 services/task_engine.py:_score_candidate 的现有
静态/短视打分——仅用当前信息取即时标量 argmax；不做长期前瞻、不维护 POMDP
信念、遇阻不会迂回（PlannedAction.detour 恒为 None）。失败后由环境把任务
置 blocked，贪心在下一步跳过（=任务未达成）。

B 组：经 services.decision_contract.load_planner() 加载 BoostGlidePlanner。
模块未就绪时捕获 ImportError，返回 None——harness 不崩溃，但**绝不伪造 B 数字**。
"""
from __future__ import annotations

from typing import Any, Optional

from services.decision_contract import (
    DecisionContext,
    PlannedAction,
    MODE_GLIDE,
)


class GreedyPolicy:
    """A 组贪心策略（name='A_greedy'）。

    与 services/task_engine.py:_score_candidate 的对应关系（逐行对齐）：
        base    = float(cand.raw.get("base", 1.0))
                  ↔ _score_candidate: base = float(cand.get("base", 1.0))
        dir_w   = 1.3 if cand.direction == self.current_direction else 0.7
                  ↔ _score_candidate: dir_w = 1.3 if cand.direction==direction else 0.7
        strat_w = self._STRATEGY_WEIGHTS.get(strategy,{}).get(cand.category, 1.0)
                  ↔ _score_candidate: strat_w = self._STRATEGY_CATEGORY_WEIGHT.get(...)
        stage_w = 1.2 if (not tags or stage in tags) else 0.85
                  ↔ _score_candidate: stage_w = 1.2 if (not tags or stage in tags) else 0.85
        score   = round(base*dir_w*strat_w*stage_w, 3)
                  ↔ _score_candidate return 同式

    贪心的"现有"局限（benchmark 要证明它不如长期规划）：
      * 不看 horizon / Q(s,a)：不知道"今晚表白"长期会伤主线；
      * 不维护 POMDP 信念：info_gain 动作对它无吸引力；
      * 不会迂回：detour 恒 None，遇阻要么硬推（损伤）要么跳过（未达成）。
    """

    name = "A_greedy"

    # 与 task_engine._STRATEGY_CATEGORY_WEIGHT 同形；第一阶段全部类别默认 1.0，
    # 即不引入额外策略偏置，让"静态打分 vs 长期规划"的对照干净。
    _STRATEGY_WEIGHTS: dict[str, dict[str, float]] = {
        "慢热": {},
    }

    def __init__(self, strategy: str = "慢热", current_direction: str = "维稳"):
        self.strategy = strategy
        self.current_direction = current_direction

    # -- 与 _score_candidate 同形的静态打分 -------------------------------- #
    def _score(self, cand: Any, stage: int) -> float:
        base = float(cand.raw.get("base", 1.0))
        dir_w = 1.3 if cand.direction == self.current_direction else 0.7
        cat = cand.category or ""
        strat_w = self._STRATEGY_WEIGHTS.get(self.strategy, {}).get(cat, 1.0)
        tags = cand.raw.get("stage_tags", []) or []
        stage_w = 1.2 if (not tags or stage in tags) else 0.85
        return round(base * dir_w * strat_w * stage_w, 3)

    # -- Policy 协议 ------------------------------------------------------ #
    def decide(self, ctx: DecisionContext) -> PlannedAction:
        # 环境广播的 blocked_tasks：贪心"跳过→任务未达成"，不再选
        blocked = set(ctx.extras.get("blocked_tasks", []) or [])
        avail = [c for c in ctx.candidates if c.id not in blocked]
        if not avail:
            avail = list(ctx.candidates)

        best = max(avail, key=lambda c: self._score(c, ctx.state.stage))
        score = self._score(best, ctx.state.stage)
        return PlannedAction(
            candidate_id=best.id,
            title=best.title,
            mode=MODE_GLIDE,           # 贪心不懂四模式，一律标 glide
            q=score,                    # 即时静态分冒充 Q
            v_after=score,
            expected_reward=dict(best.reward),
            success_prob=best.success_prob,
            detour=None,                # 贪心不会迂回
            rationale=(
                f"greedy argmax base*dir*strat*stage={score} "
                f"(base={best.raw.get('base',1.0)}, dir_w="
                f"{1.3 if best.direction==self.current_direction else 0.7})"
            ),
            candidate=best,
        )

    def recommend(self, ctx: DecisionContext, top_n: int = 3) -> list[PlannedAction]:
        blocked = set(ctx.extras.get("blocked_tasks", []) or [])
        avail = [c for c in ctx.candidates if c.id not in blocked]
        ranked = sorted(avail, key=lambda c: -self._score(c, ctx.state.stage))
        return [
            PlannedAction(
                candidate_id=c.id, title=c.title, mode=MODE_GLIDE,
                q=self._score(c, ctx.state.stage), candidate=c,
            )
            for c in ranked[:top_n]
        ]


def load_b_policy() -> Optional[Any]:
    """B 组：经契约 load_planner() 加载 BoostGlidePlanner（name='B_boost_glide'）。

    模块未就绪（ImportError）时返回 None——harness 不崩溃、不伪造 B 数字。
    B 模块就绪后无需改这里，自动接入。
    """
    try:
        from services.decision_contract import load_planner

        planner = load_planner()
        # 契约未强制 name 字段，这里兜底
        if not getattr(planner, "name", None):
            planner.name = "B_boost_glide"
        return planner
    except ImportError as e:
        # 明确标注：B 待算法模块就绪后接入
        return None
    except Exception as e:  # noqa: BLE001
        # 其他导入期错误（依赖缺失等）同样降级，不污染 A 组数字
        return None
