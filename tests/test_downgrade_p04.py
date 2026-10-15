# -*- coding: utf-8 -*-
"""tests/test_downgrade_p04.py — P-04 高压强制降档 / 先验切换 / 披露措辞 纯策略单测。

离线确定性：不连网、不加载模型、不碰 DB/chroma。
"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.confidence_cap import (  # noqa: E402
    detect_downgrade, disclosure_wording, contains_deterministic_language,
    atypical_adjustment, apply_evidence_cap, P4_TRIGGERS,
)


class TestDetectDowngrade(unittest.TestCase):
    def test_no_trigger_no_downgrade(self):
        r = detect_downgrade({})
        self.assertFalse(r["downgrade"])
        self.assertEqual(r["triggers"], [])

    def test_trigger_forces_downgrade(self):
        r = detect_downgrade({"conflict_escalation": True})
        self.assertTrue(r["downgrade"])
        self.assertIn("conflict_escalation", r["triggers"])

    def test_all_triggers(self):
        flags = {t: True for t in P4_TRIGGERS}
        r = detect_downgrade(flags)
        self.assertTrue(r["downgrade"])
        self.assertEqual(len(r["triggers"]), len(P4_TRIGGERS))


class TestTurmoilSwitch(unittest.TestCase):
    def test_recent_conflict_switches_to_prior(self):
        r = detect_downgrade({"recent_severe_conflict": True})
        self.assertTrue(r["turmoil"])
        self.assertTrue(r["switch_to_prior"])
        self.assertTrue(r["downgrade"])  # 剧变期也强降档

    def test_normal_no_switch(self):
        r = detect_downgrade({})
        self.assertFalse(r["switch_to_prior"])


class TestDisclosureWording(unittest.TestCase):
    def test_wording_template(self):
        w = disclosure_wording(0.30, "正向接纳", n_eff=8, downgraded=True)
        self.assertIn("约 30%", w)
        self.assertIn("正向接纳", w)
        self.assertIn("仅供参考", w)

    def test_wording_has_no_deterministic(self):
        w = disclosure_wording(0.5, "敷衍回避", n_eff=20)
        self.assertFalse(contains_deterministic_language(w))

    def test_detects_forbidden_words(self):
        self.assertTrue(contains_deterministic_language("他一定不会回你"))
        self.assertTrue(contains_deterministic_language("肯定是这样"))
        self.assertTrue(contains_deterministic_language("他不会变"))


class TestAtypicalIndividual(unittest.TestCase):
    def test_not_enough_early_messages(self):
        r = atypical_adjustment(0.9, n_early=20)
        self.assertFalse(r["atypical"])
        self.assertFalse(r["downgrade"])

    def test_large_deviation_relaxes_kappa_and_downgrades(self):
        r = atypical_adjustment(0.6, n_early=50, base_kappa=10.0)
        self.assertTrue(r["atypical"])
        self.assertTrue(r["downgrade"])
        self.assertLess(r["kappa"], 10.0)   # 放宽收缩 κ

    def test_small_deviation_standard(self):
        r = atypical_adjustment(0.1, n_early=50, base_kappa=10.0)
        self.assertFalse(r["atypical"])
        self.assertEqual(r["kappa"], 10.0)


class TestDowngradeInteractsWithCap(unittest.TestCase):
    def test_downgrade_flag_lowers_cap(self):
        plain = apply_evidence_cap(0.9, 30)
        lowered = apply_evidence_cap(0.9, 30, downgraded=True)
        self.assertLess(lowered["capped"], plain["capped"])


if __name__ == "__main__":
    unittest.main()
