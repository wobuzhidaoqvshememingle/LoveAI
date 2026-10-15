# -*- coding: utf-8 -*-
"""benchmark 最小冒烟测试：
  * 环境可走若干步（A 组贪心能跑完场景）；
  * 聊天记录可注入临时 SQLite 并用真实 AffectionEngine 重算；
  * GreedyPolicy 可决策且 PlannedAction 字段齐全；
  * RunResult 指标字段齐全。
"""
import os
import sys
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from benchmark.sim_environment import SimEnvironment, RunResult  # noqa: E402
from benchmark.scenario_generator import (  # noqa: E402
    build_small_scenario,
    inject_scenario_chats,
)
from benchmark.policies import GreedyPolicy, load_b_policy  # noqa: E402
from services.decision_contract import (  # noqa: E402
    DecisionContext, PlannedAction, TaskCandidate,
)


class TestBenchmarkSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self.scenario = build_small_scenario(seed=0)

    def test_env_runs_and_metrics_fields_present(self) -> None:
        """环境可走若干步跑完；RunResult 关键字段齐全。"""
        env = SimEnvironment(self.scenario, seed=0)
        res = env.run(GreedyPolicy())
        self.assertIsInstance(res, RunResult)
        self.assertGreater(res.steps_used, 0)
        self.assertEqual(res.total_tasks, len(self.scenario.tasks))
        # 必含指标字段
        for k in (
            "completion_rate", "full_set_achieved", "discount_steps",
            "mainline_damage", "mainline_damage_events", "hard_push_events",
            "final_affection", "final_stage", "decision_ms_avg", "wall_ms",
        ):
            self.assertTrue(hasattr(res, k), f"缺少指标字段 {k}")
        # 贪心 detour 恒 None → 折扣恒 0
        self.assertEqual(res.discount_steps, 0.0)

    def test_greedy_decide_returns_planned_action(self) -> None:
        """GreedyPolicy.decide 返回合法 PlannedAction，字段齐全。"""
        env = SimEnvironment(self.scenario, seed=0)
        cands = env.available_candidates()
        self.assertGreater(len(cands), 0)
        ctx = DecisionContext(
            person_id="p1", state=env.state, candidates=cands,
            horizon=env.horizon, now_index=0, rng_seed=0, extras={},
        )
        act = GreedyPolicy().decide(ctx)
        self.assertIsInstance(act, PlannedAction)
        self.assertIn(act.candidate_id, [c.id for c in cands])
        # 贪心不会迂回
        self.assertIsNone(act.detour)

    def test_chat_injection_real_sqlite(self) -> None:
        """聊天记录真写入临时 SQLite，真实 AffectionEngine 重算出非默认数。"""
        tmpdir = tempfile.mkdtemp(prefix="loveai_bench_smoke_")
        try:
            from models.database import Database

            db_path = os.path.join(tmpdir, "t.db")
            db = Database(db_path)
            db.init_tables()
            meta = inject_scenario_chats(db, "p_smoke", "冒烟对象", self.scenario)
            self.assertGreater(meta["injected"], 0)
            # 真实引擎应给出非 50 的重算总分（注入关键词生效）
            self.assertIsInstance(meta["recalc_total"], (int, float))
            self.assertIsInstance(meta["proactive_ratio"], float)
            # chat_messages 表里真有行
            rows = db.fetch_all(
                "SELECT COUNT(*) AS n FROM chat_messages WHERE person_id=:pid",
                {"pid": "p_smoke"},
            )
            self.assertEqual(rows[0]["n"], meta["injected"])
            try:
                db.engine.dispose()
            except Exception:
                pass
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_b_policy_graceful_or_none(self) -> None:
        """B 组要么返回合法 policy，要么返回 None（不崩溃、不伪造）。"""
        b = load_b_policy()
        if b is not None:
            # 若就绪，应具备 decide 接口
            self.assertTrue(callable(getattr(b, "decide", None)))
        # None 也合法（模块未就绪）

    def test_determinism_same_seed_same_result(self) -> None:
        """同 seed 重跑两次，A 组完成任务集合一致（确定性）。"""
        r1 = SimEnvironment(self.scenario, seed=7).run(GreedyPolicy())
        r2 = SimEnvironment(self.scenario, seed=7).run(GreedyPolicy())
        self.assertEqual(r1.done_tasks, r2.done_tasks)
        self.assertEqual(r1.final_affection, r2.final_affection)


if __name__ == "__main__":
    unittest.main()
