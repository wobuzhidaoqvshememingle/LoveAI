# -*- coding: utf-8 -*-
"""P-10 离线确定性单测：local_statistical_fallback。

不联网、不加载真实模型、不碰 chroma/真实DB。
纯函数 + 注入式 retriever。
"""
from __future__ import annotations

import math

import pytest

from services.local_statistical_fallback import (
    DEFAULT_POPULATION_PRIOR,
    LocalStatisticalFallback,
    NO_GENERATIVE_ANNOTATION,
    REACTION_CATEGORIES,
    align_to_categories,
    normalize_probs,
    shrink_counts,
)


class TestNormalizeProbs:
    def test_sums_to_one(self):
        d = {"Y1": 0.5, "Y2": 1.0, "Y3": 1.5}
        out = normalize_probs(d)
        assert abs(sum(out.values()) - 1.0) < 1e-9

    def test_all_zero_returns_uniform(self):
        d = {"Y1": 0.0, "Y2": 0.0, "Y3": 0.0}
        out = normalize_probs(d)
        assert abs(sum(out.values()) - 1.0) < 1e-9
        assert all(abs(v - 1.0 / 3) < 1e-9 for v in out.values())

    def test_negative_clamped(self):
        d = {"Y1": -1.0, "Y2": 2.0}
        out = normalize_probs(d)
        assert out["Y1"] == 0.0
        assert abs(out["Y2"] - 1.0) < 1e-9


class TestShrinkCounts:
    def test_empty_history_falls_back_to_prior(self):
        out = shrink_counts({})
        # 空历史 -> 收缩后应非常接近先验
        for k, v in DEFAULT_POPULATION_PRIOR.items():
            assert abs(out[k] - v) < 1e-6

    def test_shrunk_moves_toward_prior(self):
        # 历史极度偏向 Y1（1000 次 Y1），但收缩强度 20
        out = shrink_counts({"Y1": 1000.0}, strength=20.0)
        # Y1 概率应该 < 1.0（被先验拉下来一点，即便 1000 很大）
        assert out["Y1"] < 1.0
        # 其他类别不应为 0（被先验填充）
        for c in REACTION_CATEGORIES:
            assert out[c] > 0.0
        # 用更极端的对比：少量历史时 Y1 应明显 < 0.95
        out_small = shrink_counts({"Y1": 5.0}, strength=20.0)
        assert out_small["Y1"] < 0.5

    def test_more_history_less_shrinkage(self):
        # 少量历史
        small = shrink_counts({"Y1": 2.0}, strength=20.0)
        # 大量历史
        large = shrink_counts({"Y1": 2000.0}, strength=20.0)
        # 大量历史下 Y1 应更极端（更远离先验的 Y1=0.08）
        assert large["Y1"] > small["Y1"]

    def test_sums_to_one(self):
        out = shrink_counts({"Y1": 5, "Y4": 10})
        assert abs(sum(out.values()) - 1.0) < 1e-9


class TestAlignToCategories:
    def test_missing_categories_filled(self):
        out = align_to_categories({"Y1": 1.0})
        assert set(out.keys()) == set(REACTION_CATEGORIES)
        assert abs(sum(out.values()) - 1.0) < 1e-9

    def test_extra_categories_dropped(self):
        out = align_to_categories({"Y1": 1.0, "UNKNOWN": 5.0})
        assert "UNKNOWN" not in out


class TestLocalStatisticalFallback:
    def test_predict_never_raises(self):
        fb = LocalStatisticalFallback()
        out = fb.predict({})
        assert out is not None

    def test_distribution_sums_to_one(self):
        fb = LocalStatisticalFallback()
        out = fb.predict({"scene": "conflict"})
        assert abs(sum(out["distribution"].values()) - 1.0) < 1e-9

    def test_annotation_field_exists(self):
        fb = LocalStatisticalFallback()
        out = fb.predict({})
        assert "annotation" in out
        assert out["annotation"] == NO_GENERATIVE_ANNOTATION

    def test_empty_history_uses_prior_and_downgrades(self):
        fb = LocalStatisticalFallback(history_counts={})
        out = fb.predict({})
        assert out["evidence_insufficient"] is True
        assert out["level"] == "极低"
        # 分布应接近先验
        for k, v in DEFAULT_POPULATION_PRIOR.items():
            assert abs(out["distribution"][k] - v) < 0.05

    def test_with_history_not_insufficient(self):
        fb = LocalStatisticalFallback(
            history_counts={c: 5 for c in REACTION_CATEGORIES},  # 总 35 >= 10
        )
        out = fb.predict({})
        assert out["evidence_insufficient"] is False
        assert out["level"] in ("低", "中", "极低")

    def test_retriever_injected_and_used(self):
        def fake_retriever(ctx):
            # 情境是 conflict -> 偏向 Y5/Y6
            if ctx.get("scene") == "conflict":
                return [("Y5", 3.0), ("Y6", 2.0)]
            return []

        fb = LocalStatisticalFallback(retriever=fake_retriever)
        out = fb.predict({"scene": "conflict"})
        assert out["n_retrieved"] == 2
        # Y5 概率应高于先验的 0.10
        assert out["distribution"]["Y5"] > DEFAULT_POPULATION_PRIOR["Y5"]

    def test_retriever_exception_does_not_crash(self):
        def bad_retriever(ctx):
            raise RuntimeError("boom")

        fb = LocalStatisticalFallback(retriever=bad_retriever)
        out = fb.predict({"scene": "anything"})
        # 检索失败 -> 回退到纯历史/先验，不抛异常
        assert out["n_retrieved"] == 0
        assert out["annotation"] == NO_GENERATIVE_ANNOTATION

    def test_top_reaction_most_probable(self):
        fb = LocalStatisticalFallback()
        out = fb.predict({})
        top = out["top_reaction"]
        assert out["distribution"][top] == max(out["distribution"].values())

    def test_predict_top_text_contains_annotation(self):
        fb = LocalStatisticalFallback()
        txt = fb.predict_top_text({"scene": "daily"})
        assert NO_GENERATIVE_ANNOTATION in txt

    def test_custom_prior_respected(self):
        custom = {"Y1": 0.5, "Y2": 0.5}  # 其余为 0
        fb = LocalStatisticalFallback(population_prior=custom, prior_strength=50.0)
        out = fb.predict({})
        # 强先验下，Y1+Y2 应占大头
        assert out["distribution"]["Y1"] + out["distribution"]["Y2"] > 0.8
