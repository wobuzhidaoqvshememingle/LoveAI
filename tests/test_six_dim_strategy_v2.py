# -*- coding: utf-8 -*-
"""六维策略 v2 新功能单测：阶段化策略 / 行为影响因子 / 下一步引导。"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.six_dim_strategy import (  # noqa: E402
    BEHAVIOR_IMPACT, STAGE_STRATEGIES, RELATIONSHIP_DIMS,
    strategy_by_stage, next_steps_after_analysis,
)


class TestSixDimStrategyV2(unittest.TestCase):

    # ------------------------------------------------------------------ #
    # BEHAVIOR_IMPACT
    # ------------------------------------------------------------------ #
    def test_behavior_impact_required_keys(self):
        for k in ("主动分享秘密", "赞美对方", "记住对方说过的话",
                  "吵架后主动给台阶", "忽冷忽热"):
            self.assertIn(k, BEHAVIOR_IMPACT, f"缺行为 {k}")

    def test_behavior_impact_values_are_ints(self):
        for behavior, dims in BEHAVIOR_IMPACT.items():
            for dim, v in dims.items():
                self.assertIsInstance(v, int, f"{behavior}.{dim} 不是 int")

    def test_behavior_cold_hot_has_negative(self):
        # 忽冷忽热：吸引力+2，但信任-3，舒适度-2
        b = BEHAVIOR_IMPACT["忽冷忽热"]
        self.assertEqual(b["吸引力"], 2)
        self.assertEqual(b["信任"], -3)
        self.assertEqual(b["舒适度"], -2)

    # ------------------------------------------------------------------ #
    # strategy_by_stage
    # ------------------------------------------------------------------ #
    def test_stage_strategies_four_stages(self):
        for stage in ("Meet", "Date", "Deepen", "Resolve"):
            self.assertIn(stage, STAGE_STRATEGIES)

    def test_strategy_by_stage_returns_list_of_dicts(self):
        out = strategy_by_stage("Meet", "信任")
        self.assertIsInstance(out, list)
        self.assertGreater(len(out), 0)
        for item in out:
            self.assertIn("action", item)
            self.assertIn("effect", item)
            self.assertIn("when", item)

    def test_strategy_meet_trust_has_secret_sharing(self):
        out = strategy_by_stage("Meet", "信任")
        joined = " ".join(i["action"] for i in out)
        self.assertIn("小秘密", joined)

    def test_strategy_deepen_trust_has_cooperation(self):
        out = strategy_by_stage("Deepen", "信任")
        joined = " ".join(i["action"] for i in out)
        self.assertIn("合作", joined)

    def test_strategy_unknown_stage_returns_empty(self):
        self.assertEqual(strategy_by_stage("不存在", "信任"), [])

    def test_strategy_unknown_dim_returns_empty(self):
        self.assertEqual(strategy_by_stage("Meet", "不存在维度"), [])

    def test_all_stages_all_dims_have_content(self):
        for stage in ("Meet", "Date", "Deepen", "Resolve"):
            for dim in RELATIONSHIP_DIMS:
                out = strategy_by_stage(stage, dim)
                self.assertGreater(
                    len(out), 0,
                    f"{stage}/{dim} 策略为空",
                )

    # ------------------------------------------------------------------ #
    # 下一步引导
    # ------------------------------------------------------------------ #
    def test_next_steps_nonempty(self):
        steps = next_steps_after_analysis()
        self.assertIsInstance(steps, list)
        self.assertGreater(len(steps), 0)
        for s in steps:
            self.assertTrue(s.strip())


if __name__ == "__main__":
    unittest.main()
