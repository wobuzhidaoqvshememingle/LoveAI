# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 六维战略评分测试

覆盖：
    a. 单边剃头（用户发 20 条，对方回 2 条「哦」）互惠分 <= 3
    b. 危险财务需求（5000 元买包）现实可行分 <= 2
    c. 双方投入对等（各 10 条）互惠分 >= 6
    d. 空数据（无聊天记录）六维都应给中性 5，不崩
"""
from __future__ import annotations

import unittest

from services.six_dim_strategy import SixDimensionStrategyScorer


class FakeDB:
    """最小内存 db，模拟 fetch_all."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []

    def fetch_all(self, sql: str, params: dict | None = None) -> list[dict]:
        pid = (params or {}).get("pid")
        return [r for r in self.rows if r.get("person_id") == pid]

    def fetch_one(self, sql: str, params: dict | None = None):
        return None


class FakeFinancial:
    """模拟 financial_analyzer.check_demand_feasibility."""

    def __init__(self, result: dict) -> None:
        self.result = result

    def check_demand_feasibility(self, person_id: str, item_value: float, item_name: str) -> dict:
        return self.result


def _msg(direction: str, content: str, pid: str = "p1") -> dict:
    return {"person_id": pid, "direction": direction, "content": content}


class TestSixDimStrategy(unittest.TestCase):

    # ------------------------------------------------------------------ #
    # a. 单边剃头
    # ------------------------------------------------------------------ #
    def test_one_sided_reciprocal_low(self) -> None:
        rows = [_msg("sent", "在吗？") for _ in range(20)]
        rows += [_msg("received", "哦") for _ in range(2)]
        db = FakeDB(rows)
        scorer = SixDimensionStrategyScorer(db=db)
        out = scorer.score("p1", "今晚约她吃饭")
        self.assertLessEqual(out["reciprocal"], 3.0,
                             f"单边剃头互惠分应<=3，实际={out['reciprocal']}")

    # ------------------------------------------------------------------ #
    # b. 危险财务需求
    # ------------------------------------------------------------------ #
    def test_dangerous_finance_feasibility_low(self) -> None:
        fake_fin = FakeFinancial({
            "feasible": False,
            "risk_level": "dangerous",
            "ratio": 1.5,
            "suggestion": "严重超出承受能力",
            "warning": "安全警告",
        })
        scorer = SixDimensionStrategyScorer(db=FakeDB([]), financial_analyzer=fake_fin)
        out = scorer.score("p1", "给她买个5000元的包")
        self.assertLessEqual(out["feasibility"], 2.0,
                             f"危险财务需求可行性应<=2，实际={out['feasibility']}")

    # ------------------------------------------------------------------ #
    # c. 双方投入对等
    # ------------------------------------------------------------------ #
    def test_balanced_reciprocal_high(self) -> None:
        rows = [_msg("sent", "在吗？") for _ in range(10)]
        rows += [_msg("received", "在的呀") for _ in range(10)]
        db = FakeDB(rows)
        scorer = SixDimensionStrategyScorer(db=db)
        out = scorer.score("p1", "周末一起看电影")
        self.assertGreaterEqual(out["reciprocal"], 6.0,
                                f"对等投入互惠分应>=6，实际={out['reciprocal']}")

    # ------------------------------------------------------------------ #
    # d. 空数据
    # ------------------------------------------------------------------ #
    def test_empty_data_neutral(self) -> None:
        scorer = SixDimensionStrategyScorer(db=FakeDB([]))
        out = scorer.score("nobody", "随便说点啥")
        for dim in ("reciprocal", "reliable", "attraction",
                    "values", "feasibility", "opportunity_cost"):
            self.assertEqual(out[dim], 5.0,
                             f"空数据下 {dim} 应为 5，实际={out[dim]}")
        self.assertEqual(out["total"], 30.0)
        self.assertIn("reasons", out)

    # ------------------------------------------------------------------ #
    # 额外：combine 规则
    # ------------------------------------------------------------------ #
    def test_combine_rules(self) -> None:
        # sim>=60: 0.6*sim + 0.4*(six/60*100)
        c1 = SixDimensionStrategyScorer.combine(80, 45)  # 45/60*100=75
        self.assertAlmostEqual(c1, 0.6 * 80 + 0.4 * 75, places=1)
        # sim<60: 0.3*sim + 0.7*(six/60*100)
        c2 = SixDimensionStrategyScorer.combine(30, 30)  # 30/60*100=50
        self.assertAlmostEqual(c2, 0.3 * 30 + 0.7 * 50, places=1)


if __name__ == "__main__":
    unittest.main()
