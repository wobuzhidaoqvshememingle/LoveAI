# -*- coding: utf-8 -*-
"""基线意外度（香农信息量）单测。

核心对照：同一绝对等待时长，对"秒回型基线"是高意外（高信息量），
对"沉默/从不秒回型基线"是低意外（本就如此）——验证差分加权正确。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.baseline_surprise import (  # noqa: E402
    classify_baseline_type, surprise_z, evaluate_reply_surprise,
    extract_reply_lags,
)


class TestBaselineType:
    def test_classify(self):
        assert classify_baseline_type(2) == "秒回型"
        assert classify_baseline_type(30) == "正常型"
        assert classify_baseline_type(120) == "慢回型"
        assert classify_baseline_type(400) == "沉默型"


class TestSurpriseDifferential:
    def test_same_waiting_high_surprise_for_fast_baseline(self):
        # 秒回型基线：均值2分钟，σ=1
        fast = [1, 2, 2, 3, 1, 2, 2, 1]
        r = evaluate_reply_surprise(waiting_min=120, reply_lags_min=fast)
        assert r["available"] is True
        assert r["baseline_type"] == "秒回型"
        assert r["is_high_surprise"] is True
        assert r["information_value"] == "high"

    def test_same_waiting_low_surprise_for_silent_baseline(self):
        # 沉默型基线：均值300分钟(5小时)，σ=60
        silent = [240, 300, 360, 300, 280, 320, 300, 260]
        r = evaluate_reply_surprise(waiting_min=120, reply_lags_min=silent)
        assert r["available"] is True
        assert r["baseline_type"] == "沉默型"
        # 等待120分钟 比 她平时平均300分钟 还短 -> 不是高意外
        assert r["is_high_surprise"] is False
        assert r["information_value"] == "low"

    def test_fast_baseline_surprise_exceeds_silent(self):
        fast = [1, 2, 2, 3, 1, 2, 2, 1]
        silent = [240, 300, 360, 300, 280, 320, 300, 260]
        z_fast = evaluate_reply_surprise(120, fast)["surprise_z"]
        z_silent = evaluate_reply_surprise(120, silent)["surprise_z"]
        # 同一等待120分钟，秒回型的意外度必须远大于沉默型
        assert z_fast > z_silent


class TestDataAdequacy:
    def test_insufficient_samples(self):
        r = evaluate_reply_surprise(60, [1, 2])  # 样本<5
        assert r["available"] is False

    def test_empty(self):
        r = evaluate_reply_surprise(60, [])
        assert r["available"] is False


class TestExtractLags:
    def test_extract(self):
        msgs = [
            {"direction": "sent", "created_at": "2026-06-01 10:00:00"},
            {"direction": "received", "created_at": "2026-06-01 10:02:00"},
            {"direction": "received", "created_at": "2026-06-01 10:03:00"},
            {"direction": "sent", "created_at": "2026-06-01 11:00:00"},
            {"direction": "received", "created_at": "2026-06-01 12:00:00"},
        ]
        lags = extract_reply_lags(msgs)
        assert lags == [2.0, 60.0]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
