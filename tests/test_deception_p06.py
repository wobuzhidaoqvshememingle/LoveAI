# -*- coding: utf-8 -*-
"""P-06 伪装识别 与 侧写慢更新 离线单测。

覆盖口径（《LoveAI-统一实施总纲 v3.1 加固补丁》P-06）：
  - 证据独立性：混杂簇（周末+深夜+同一话题）去重；≥3 个不相关情境方向才放行；
  - 个体内 z 分数（不用人群规范）；
  - CUSUM 过程控制（单步更新 + 上/下报警），而非单一阈值；
  - 露馅信号经"自然解释清单"排除；排除不掉且重复 ≥3 才入"待验证假设池"，
    单次暴露永不输出；
  - 强制反向陈述："此判断可能不成立，因为…"；
  - 更新步长受限：单次最多移动 k=1 个后验标准差；重大跨档改判需审批；
  - 高功能伪装下只报告异常模式，不下人格判决。

本文件纯离线：不联网、不加载真实模型、不碰 chroma/真实 DB。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.deception_detector import (  # noqa: E402
    individual_zscore,
    cusum_update,
    cusum_alarm,
    dedupe_confounded_signals,
    evidence_independence_gate,
    natural_excludes,
    HypothesisPool,
    limit_posterior_step,
    cross_tier_requires_approval,
    reverse_statement,
    report_anomaly_only,
    POSTERIOR_STEP_K,
    HYPOTHESIS_MIN_REPEAT,
    MIN_INDEPENDENT_SITUATIONS,
)


class TestIndividualZscore(unittest.TestCase):
    def test_uses_personal_baseline(self):
        # 个体内 z：相对本人基线，不是人群均值
        self.assertAlmostEqual(individual_zscore(120.0, 60.0, 30.0), 2.0, places=6)

    def test_zero_std_returns_zero(self):
        self.assertEqual(individual_zscore(999.0, 1.0, 0.0), 0.0)
        self.assertEqual(individual_zscore(999.0, 1.0, None), 0.0)


class TestCUSUM(unittest.TestCase):
    def test_no_alarm_in_normal_region(self):
        s_hi = s_lo = 0.0
        for v in [1.0, 0.5, -0.5, 0.2]:
            s_hi, s_lo = cusum_update(s_hi, s_lo, v, target=0.0)
        self.assertIsNone(cusum_alarm(s_hi, s_lo))

    def test_up_alarm_on_sustained_deviation(self):
        s_hi = s_lo = 0.0
        # 持续正向偏离，漂移 0.5、上阈值 5.0
        for v in [3.0, 3.0, 3.0, 3.0, 3.0]:
            s_hi, s_lo = cusum_update(s_hi, s_lo, v, target=0.0)
        self.assertEqual(cusum_alarm(s_hi, s_lo), "up")

    def test_down_alarm(self):
        s_hi = s_lo = 0.0
        for v in [-3.0, -3.0, -3.0, -3.0, -3.0]:
            s_hi, s_lo = cusum_update(s_hi, s_lo, v, target=0.0)
        self.assertEqual(cusum_alarm(s_hi, s_lo), "down")


class TestEvidenceIndependence(unittest.TestCase):
    def test_confounded_cluster_counted_once(self):
        # 同一混杂簇：周末+深夜+同一话题"借钱"，来 3 条 → 只计 1
        signals = [
            {"is_weekend": True, "is_late_night": True, "topic": "借钱", "direction": "延迟变长"},
            {"is_weekend": True, "is_late_night": True, "topic": "借钱", "direction": "情感降温"},
            {"is_weekend": True, "is_late_night": True, "topic": "借钱", "direction": "回避"},
        ]
        deduped = dedupe_confounded_signals(signals)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["cluster_hits"], 3)

    def test_distinct_clusters_not_merged(self):
        signals = [
            {"is_weekend": True, "is_late_night": True, "topic": "借钱", "direction": "a"},
            {"is_weekend": False, "is_late_night": False, "topic": "约会", "direction": "b"},
        ]
        self.assertEqual(len(dedupe_confounded_signals(signals)), 2)

    def test_gate_needs_three_unrelated_directions(self):
        # 只有 2 个方向 → 拦截
        few = [
            {"is_weekend": True, "is_late_night": True, "topic": "t1", "direction": "延迟变长"},
            {"is_weekend": False, "is_late_night": False, "topic": "t2", "direction": "情感降温"},
        ]
        r_few = evidence_independence_gate(few)
        self.assertTrue(r_few["blocked"])
        self.assertLess(r_few["distinct_dirs"], MIN_INDEPENDENT_SITUATIONS)

        # 3 个不相关方向 → 放行
        enough = few + [{"is_weekend": True, "is_late_night": False,
                         "topic": "t3", "direction": "承诺模糊"}]
        r_enough = evidence_independence_gate(enough)
        self.assertTrue(r_enough["passes"])


class TestNaturalExclusionAndPool(unittest.TestCase):
    def test_natural_exclusion_covers_window(self):
        ext = [{"type": "work_cycle", "start": 100.0, "end": 200.0}]
        self.assertTrue(natural_excludes({"ts": 150.0}, ext))
        self.assertFalse(natural_excludes({"ts": 500.0}, ext))

    def test_non_natural_event_type_not_excluded(self):
        ext = [{"type": "personality_verdict_claim", "start": 0.0, "end": 9999.0}]
        self.assertFalse(natural_excludes({"ts": 500.0}, ext))

    def test_single_exposure_never_output(self):
        pool = HypothesisPool()
        r = pool.observe("late_night_slip", explained_by_natural=False)
        self.assertFalse(r["entered_pool"])
        self.assertEqual(pool.pending(), [])

    def test_repeat_three_times_after_exclusion_enters_pool(self):
        pool = HypothesisPool()
        # 前两次排除不掉但不足 3 次 → 不输出
        pool.observe("late_night_slip", explained_by_natural=False)
        pool.observe("late_night_slip", explained_by_natural=False)
        self.assertEqual(pool.pending(), [])
        # 第三次 → 入池
        r = pool.observe("late_night_slip", explained_by_natural=False)
        self.assertTrue(r["entered_pool"])
        self.assertEqual(len(pool.pending()), 1)

    def test_natural_explained_observations_do_not_count(self):
        pool = HypothesisPool()
        # 三次都被自然解释 → 永不入池
        for _ in range(5):
            pool.observe("cold_shift", explained_by_natural=True)
        self.assertEqual(pool.pending(), [])


class TestSlowUpdateStep(unittest.TestCase):
    def test_step_clamped_to_one_sigma(self):
        # current=0, baseline_std=10, proposed 要移动 +50（=5σ）→ 截到 k=1σ=10
        r = limit_posterior_step(current=0.0, proposed=50.0, baseline_std=10.0)
        self.assertTrue(r["clamped"])
        self.assertAlmostEqual(r["applied"], 10.0, places=6)
        self.assertAlmostEqual(r["moved_sigma"], POSTERIOR_STEP_K, places=4)

    def test_small_move_not_clamped(self):
        r = limit_posterior_step(current=0.0, proposed=5.0, baseline_std=10.0)
        self.assertFalse(r["clamped"])
        self.assertAlmostEqual(r["applied"], 5.0, places=6)

    def test_no_scale_holds_current(self):
        r = limit_posterior_step(current=3.0, proposed=99.0, baseline_std=0.0)
        self.assertAlmostEqual(r["applied"], 3.0, places=6)


class TestReverseStatementAndTiers(unittest.TestCase):
    def test_reverse_statement_present(self):
        s = reverse_statement("对方近期回复明显变慢", ["可能只是项目冲刺", "样本仍少"])
        self.assertIn("此判断可能不成立", s)

    def test_cross_tier_requires_approval(self):
        r = cross_tier_requires_approval("低", "高")
        self.assertTrue(r["needs_approval"])

    def test_same_tier_no_approval(self):
        r = cross_tier_requires_approval("中", "中")
        self.assertFalse(r["needs_approval"])


class TestAnomalyOnlyNoVerdict(unittest.TestCase):
    def test_personality_verdict_is_none(self):
        out = report_anomaly_only([{"type": "sudden_cold", "note": "延迟突变"}])
        self.assertIsNone(out["personality_verdict"])
        self.assertEqual(len(out["anomaly_patterns"]), 1)
        self.assertIn("不替代临床诊断", out["clinical_note"])


if __name__ == "__main__":
    unittest.main()
