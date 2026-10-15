# -*- coding: utf-8 -*-
"""tests/test_confidence_cap.py — P-01 证据量→置信上限硬封顶 纯函数单测。

离线确定性：不连网、不加载模型、不碰 DB/chroma。
边界用例覆盖 n = 4 / 5 / 9 / 10 / 29 / 30 / 99 / 100。
"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.confidence_cap import (  # noqa: E402
    apply_evidence_cap, ceiling_for_neff, effective_n, time_decay_weight,
    band_of, downgrade_band,
)


class TestNeffDecay(unittest.TestCase):
    def test_fresh_evidence_weight_is_one(self):
        self.assertAlmostEqual(time_decay_weight(0), 1.0, places=6)

    def test_half_life_halves(self):
        # 过一个半衰期，权重应约为 0.5
        self.assertAlmostEqual(time_decay_weight(90, half_life_days=90), 0.5, places=3)

    def test_effective_n_sum(self):
        # 4 条全新证据 + 1 条已过一个半衰期 → n_eff ≈ 4.5
        n = effective_n([1.0, 1.0, 1.0, 1.0, 0.5])
        self.assertAlmostEqual(n, 4.5, places=3)

    def test_empty_weights_zero(self):
        self.assertEqual(effective_n([]), 0.0)


class TestCeilingBoundary(unittest.TestCase):
    """n = 4/5/9/10/29/30/99/100 边界。"""

    def test_n4_is_hypothesis(self):
        r = ceiling_for_neff(4)
        self.assertEqual(r["band"], "假设")
        self.assertFalse(r["drives_strategy"])
        self.assertFalse(r["show_probability"])
        self.assertEqual(r["ceiling"], 0.0)

    def test_n5_ceiling_45(self):
        self.assertEqual(ceiling_for_neff(5)["ceiling"], 0.45)

    def test_n9_ceiling_45(self):
        self.assertEqual(ceiling_for_neff(9)["ceiling"], 0.45)

    def test_n10_ceiling_60(self):
        self.assertEqual(ceiling_for_neff(10)["ceiling"], 0.60)

    def test_n29_ceiling_60(self):
        self.assertEqual(ceiling_for_neff(29)["ceiling"], 0.60)

    def test_n30_ceiling_80(self):
        self.assertEqual(ceiling_for_neff(30)["ceiling"], 0.80)

    def test_n99_ceiling_80(self):
        self.assertEqual(ceiling_for_neff(99)["ceiling"], 0.80)

    def test_n100_unlocked_only_when_calibrated(self):
        # 未校准达标：仍压在 0.80
        self.assertEqual(ceiling_for_neff(100, calibrated=False)["ceiling"], 0.80)
        # 校准达标：不封顶
        self.assertEqual(ceiling_for_neff(100, calibrated=True)["ceiling"], 1.0)


class TestApplyCap(unittest.TestCase):
    def test_raw_above_ceiling_is_clamped(self):
        # n=9 上限 0.45，原始 0.9 被压到 0.45
        out = apply_evidence_cap(0.9, 9)
        self.assertEqual(out["capped"], 0.45)
        self.assertTrue(out["drives_strategy"])

    def test_raw_below_ceiling_kept(self):
        # n=30 上限 0.80，原始 0.4 不超 → 保留
        out = apply_evidence_cap(0.4, 30)
        self.assertEqual(out["capped"], 0.4)

    def test_n_less_than5_forces_hypothesis(self):
        out = apply_evidence_cap(0.99, 4.9)
        self.assertEqual(out["capped"], 0.0)
        self.assertFalse(out["drives_strategy"])
        self.assertFalse(out["show_probability"])

    def test_downgrade_one_notch(self):
        # n=30 上限 0.80，raw=0.9 → 压到 0.80(较高)；降档 → 中等(0.60)
        out = apply_evidence_cap(0.9, 30, downgraded=True)
        self.assertEqual(out["band"], "中等")
        self.assertLessEqual(out["capped"], 0.60)
        self.assertTrue(out["downgraded"])

    def test_n100_calibrated_no_cap(self):
        out = apply_evidence_cap(0.95, 100, calibrated=True)
        self.assertEqual(out["capped"], 0.95)
        self.assertEqual(out["band"], "很高")

    def test_n100_not_calibrated_still_capped(self):
        out = apply_evidence_cap(0.95, 100, calibrated=False)
        self.assertEqual(out["capped"], 0.80)


class TestBandLadder(unittest.TestCase):
    def test_band_of(self):
        self.assertEqual(band_of(0.0), "假设")
        self.assertEqual(band_of(0.1), "很低")
        self.assertEqual(band_of(0.45), "偏低")
        self.assertEqual(band_of(0.60), "中等")
        self.assertEqual(band_of(0.80), "较高")
        self.assertEqual(band_of(0.99), "很高")

    def test_downgrade_floor(self):
        self.assertEqual(downgrade_band("很高"), "较高")
        self.assertEqual(downgrade_band("假设"), "假设")  # 最低不再降


if __name__ == "__main__":
    unittest.main()
