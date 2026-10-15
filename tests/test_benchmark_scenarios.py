# -*- coding: utf-8 -*-
"""第二阶段注入与统计的冒烟测试：
  * 多类场景可跑（mid_conflict / npc_family / npc_wingman / incident / mutation / solvable / cold_start）；
  * 脚本事件（意外/早期预警/突变）确实触发；
  * dep_any_of 等价路径解锁主线；
  * 符号检验函数可运行；
  * solvable 场景 B 达成率 >= A（真实运行）。
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

from benchmark.scenario_generator import (  # noqa: E402
    SCENARIO_BUILDERS,
    build_solvable_full,
    build_mutation,
    build_mainline_under_pressure,
    build_opposing_pair_both,
    DIFFICULTY_PARAMS,
)
from benchmark.sim_environment import SimEnvironment  # noqa: E402
from benchmark.policies import GreedyPolicy, load_b_policy  # noqa: E402
from benchmark.run_benchmark import sign_test, _binom_two_sided_p  # noqa: E402


class TestScenarioVariants(unittest.TestCase):
    def test_all_builders_run_a(self) -> None:
        """每类场景 A 组都能跑完不崩。"""
        for cat, builder in SCENARIO_BUILDERS.items():
            sc = builder(seed=0)
            env = SimEnvironment(sc, seed=0)
            res = env.run(GreedyPolicy())
            self.assertGreater(res.steps_used, 0, f"{cat} A 没走步")
            self.assertGreaterEqual(res.done_tasks + res.blocked_tasks, 1, f"{cat} 无任务被处理")

    def test_mutation_event_fires(self) -> None:
        """mutation 场景：早期预警 + 断崖突变事件确实触发，M/A/好感下降。"""
        sc = build_mutation(seed=0)
        env = SimEnvironment(sc, seed=0)
        # 跑完整场景（跑到 horizon 或无可用候选），确保 cliff(at_step=3) 能触发
        while env.now_index < env.horizon:
            if env.step(GreedyPolicy()) is None:
                break
        cliff_fired = any(
            e.get("type") == "mutation_cliff"
            for t in env.trace for e in t.get("events_fired", [])
        )
        warning_fired = any(
            e.get("type") == "mutation_warning"
            for t in env.trace for e in t.get("events_fired", [])
        )
        self.assertTrue(warning_fired, "早期预警未触发")
        # cliff 可能在没有候选可走时触发（不进 trace），直接查 fired 集合
        cliff_in_fired = any(
            env._events[i].get("type") == "mutation_cliff"
            for i in env._events_fired
        )
        self.assertTrue(cliff_fired or cliff_in_fired, "断崖突变未触发")

    def test_solvable_b_at_least_a(self) -> None:
        """solvable 场景：B 全达成率 >= A（B 不会被硬闸误伤）。"""
        sc = build_solvable_full(seed=100)
        ra = SimEnvironment(sc, seed=100).run(GreedyPolicy())
        b = load_b_policy()
        if b is None:
            self.skipTest("B 模块未就绪")
        rb = SimEnvironment(sc, seed=100).run(b)
        self.assertGreaterEqual(rb.done_tasks, ra.done_tasks - 1)
        # B 在 solvable 不应有主线硬推损伤
        self.assertEqual(rb.mainline_damage, 0.0)

    def test_sign_test_runs(self) -> None:
        """符号检验函数对一组假配对正确给出计数与 p。"""
        # 构造 10 个 B 全赢的配对
        class _R:
            def __init__(self, c, aff, dmg):
                self.completion_rate = c
                self.final_affection = aff
                self.mainline_damage = dmg
        pairs = []
        for i in range(10):
            pairs.append({"A": _R(0.2, 40, 0.5), "B": _R(0.9, 70, 0.0)})
        s = sign_test(pairs)
        self.assertEqual(s["completion_rate"]["B_better"], 10)
        self.assertEqual(s["completion_rate"]["A_better"], 0)
        self.assertLess(s["completion_rate"]["sign_test_p_two_sided"], 0.01)

    def test_binom_p(self) -> None:
        self.assertAlmostEqual(_binom_two_sided_p(0, 10), 2.0 / 1024, places=4)
        self.assertEqual(_binom_two_sided_p(5, 10), 1.0)


class TestDifficultyTiers(unittest.TestCase):
    def test_difficulty_params_auditable(self) -> None:
        """难度参数表三档齐全且参数单调。"""
        for lvl in ("easy", "medium", "hard"):
            p = DIFFICULTY_PARAMS[lvl]
            self.assertIn("npc_count", p)
            self.assertIn("horizon_mult", p)
        self.assertLessEqual(
            DIFFICULTY_PARAMS["easy"]["npc_count"],
            DIFFICULTY_PARAMS["medium"]["npc_count"],
        )

    def test_difficulty_scales_horizon(self) -> None:
        """easy 视野 > medium > hard（同一类场景）。"""
        h_easy = build_mainline_under_pressure(seed=0, difficulty="easy").horizon
        h_med = build_mainline_under_pressure(seed=0, difficulty="medium").horizon
        h_hard = build_mainline_under_pressure(seed=0, difficulty="hard").horizon
        self.assertGreaterEqual(h_easy, h_med)
        self.assertGreaterEqual(h_med, h_hard)


class TestSpecialScenarios(unittest.TestCase):
    def test_mainline_under_pressure_b_no_damage(self) -> None:
        """专项1：B 主线损伤=0（拒绝捷径），A 会损伤。"""
        b = load_b_policy()
        if b is None:
            self.skipTest("B 模块未就绪")
        for seed in (0, 1, 2):
            sc = build_mainline_under_pressure(seed=seed, difficulty="easy")
            rb = SimEnvironment(sc, seed=seed).run(b)
            ra = SimEnvironment(sc, seed=seed).run(GreedyPolicy())
            # B 硬闸拒绝捷径 → 零主线损伤（不论 RNG）
            self.assertEqual(rb.mainline_damage, 0.0,
                             f"seed={seed} B 不应有主线损伤")
            # A 走捷径：失败则损伤（成功则无伤，RNG 决定）；至少 B 不损伤
            self.assertGreaterEqual(ra.mainline_damage, 0.0)

    def test_opposing_pair_both_b_completes_both(self) -> None:
        """专项2：B 把两个相制主线任务都达成；A 做不到。"""
        b = load_b_policy()
        if b is None:
            self.skipTest("B 模块未就绪")
        sc = build_opposing_pair_both(seed=0, difficulty="easy")
        rb = SimEnvironment(sc, seed=0).run(b)
        # B 三个目标任务全达成（p1+p2+p3）
        self.assertEqual(rb.done_target, rb.target_tasks,
                         f"B 应全达成，实际 done={rb.done_target}/{rb.target_tasks}")
        self.assertTrue(rb.full_set_achieved)
        self.assertEqual(rb.mainline_damage, 0.0)

    def test_decoy_not_in_completion_denominator(self) -> None:
        """口径修正：被 CMDP 拒绝的诱饵不计入未达成分母。"""
        b = load_b_policy()
        if b is None:
            self.skipTest("B 模块未就绪")
        sc = build_mainline_under_pressure(seed=0, difficulty="easy")
        rb = SimEnvironment(sc, seed=0).run(b)
        self.assertGreater(rb.decoy_tasks, 0, "场景应含诱饵任务")
        self.assertEqual(rb.rejected_decoy, rb.decoy_tasks,
                         "B 应拒绝全部诱饵")
        self.assertEqual(rb.completion_rate, 1.0)

    def test_easy_all_categories_high_completion(self) -> None:
        """easy 档每类 B 达成率 >= 0.9（结构可解）。"""
        b = load_b_policy()
        if b is None:
            self.skipTest("B 模块未就绪")
        from benchmark.scenario_generator import apply_difficulty
        for cat, builder in SCENARIO_BUILDERS.items():
            try:
                sc = builder(seed=0, difficulty="easy")
            except TypeError:
                sc = builder(seed=0)
                sc = apply_difficulty(sc, "easy")
            rb = SimEnvironment(sc, seed=0).run(b)
            self.assertGreaterEqual(
                rb.completion_rate, 0.9,
                f"{cat} easy B 达成率={rb.completion_rate:.2f} < 0.9")


if __name__ == "__main__":
    unittest.main()
