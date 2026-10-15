# -*- coding: utf-8 -*-
"""
FeedbackService 单测（离线、临时库，不碰正式 loveai.db）。

覆盖：
- 建表 schema 与任务书逐字段一致；
- record_feedback 正常写入 + 严格枚举校验（非法 feedback / 空字段抛 ValueError）；
- list_feedback 倒序、limit；
- get_stats 计数 / 比例 / accuracy / needs_recalibration；
- get_stats_by_type 分类型命中率；
- generate_judgment_id 格式与唯一性；
- 输出侧接入：StrategyAdvisor.analyze() 产出 judgment_id；
  submit_feedback() 写回 feedback_records（单例被 monkeypatch 到临时库）。
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from services.feedback_service import (
    FeedbackService,
    VALID_FEEDBACKS,
    FB_RIGHT,
    FB_WRONG,
    FB_DOUBT,
)
import services.feedback_service as fs_module


@pytest.fixture()
def svc(tmp_path):
    db = tmp_path / "feedback_test.db"
    return FeedbackService(str(db))


# ------------------------------------------------------------------ #
# 建表
# ------------------------------------------------------------------ #
def test_table_schema_matches_spec(svc, tmp_path):
    conn = sqlite3.connect(svc.db_path)
    cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(feedback_records)")}
    pk = [r[1] for r in conn.execute("PRAGMA table_info(feedback_records)") if r[5]]
    conn.close()
    # 任务书固定字段
    for f in ("id", "person_id", "judgment_type", "judgment_id", "feedback", "note", "created_at"):
        assert f in cols, f"缺少字段 {f}"
    assert "id" in pk and pk == ["id"]


def test_table_is_idempotent(tmp_path):
    db = str(tmp_path / "x.db")
    a = FeedbackService(db)
    b = FeedbackService(db)  # 二次实例化不应报错
    assert os.path.exists(db)


# ------------------------------------------------------------------ #
# 写入 + 枚举校验
# ------------------------------------------------------------------ #
def test_record_feedback_ok(svc):
    res = svc.record_feedback("demo0001", "compass", "compass_20261013T_x1", "right", "靠谱")
    assert res["ok"] is True
    assert res["id"] >= 1
    assert res["feedback"] == "right"
    assert res["note"] == "靠谱"


def test_record_feedback_strict_enum(svc):
    with pytest.raises(ValueError):
        svc.record_feedback("p1", "compass", "j1", "maybe")  # 非法枚举


def test_record_feedback_lowercases(svc):
    res = svc.record_feedback("p1", "compass", "j1", "WRONG")
    assert res["ok"] is True and res["feedback"] == "wrong"


def test_record_feedback_missing_fields(svc):
    with pytest.raises(ValueError):
        svc.record_feedback("", "compass", "j1", "right")
    with pytest.raises(ValueError):
        svc.record_feedback("p1", "compass", "", "right")
    with pytest.raises(ValueError):
        svc.record_feedback("p1", "", "j1", "right")


# ------------------------------------------------------------------ #
# 读取
# ------------------------------------------------------------------ #
def test_list_feedback_order_and_limit(svc):
    for i in range(3):
        svc.record_feedback("p1", "task", f"task_j{i}", "right")
    rows = svc.list_feedback("p1", limit=2)
    assert len(rows) == 2
    # 倒序：最新的在前
    assert rows[0]["judgment_id"] == "task_j2"
    assert rows[-1]["judgment_id"] == "task_j1"


def test_list_feedback_filter_person(svc):
    svc.record_feedback("p1", "compass", "j1", "right")
    svc.record_feedback("p2", "compass", "j2", "wrong")
    rows = svc.list_feedback("p1")
    assert len(rows) == 1 and rows[0]["judgment_id"] == "j1"


# ------------------------------------------------------------------ #
# 统计
# ------------------------------------------------------------------ #
def test_get_stats_counts_and_ratios(svc):
    svc.record_feedback("p1", "compass", "j1", "right")
    svc.record_feedback("p1", "compass", "j2", "right")
    svc.record_feedback("p1", "compass", "j3", "wrong")
    svc.record_feedback("p1", "compass", "j4", "doubt")
    st = svc.get_stats("p1")
    assert st["total"] == 4
    assert st["right"] == 2 and st["wrong"] == 1 and st["doubt"] == 1
    assert st["right_ratio"] == 0.5
    assert st["accuracy"] == 0.5
    # wrong_ratio=0.25 < 0.3，不触发重校准
    assert st["needs_recalibration"] is False


def test_get_stats_needs_recalibration(svc):
    svc.record_feedback("p1", "compass", "j1", "wrong")
    svc.record_feedback("p1", "compass", "j2", "wrong")
    svc.record_feedback("p1", "compass", "j3", "right")
    st = svc.get_stats("p1")
    # 服务端比例 round 到 4 位小数（2/3 -> 0.6667），容差对齐该精度
    assert st["wrong_ratio"] == pytest.approx(round(2 / 3, 4), abs=1e-3)
    assert st["needs_recalibration"] is True


def test_get_stats_empty_person(svc):
    st = svc.get_stats("nobody")
    assert st["total"] == 0 and st["accuracy"] == 0.0
    assert st["needs_recalibration"] is False


def test_get_stats_by_type(svc):
    svc.record_feedback("p1", "compass", "c1", "wrong")
    svc.record_feedback("p1", "strategy", "s1", "right")
    svc.record_feedback("p1", "strategy", "s2", "right")
    by = svc.get_stats_by_type("p1")
    assert set(by.keys()) == {"compass", "strategy"}
    assert by["compass"]["accuracy"] == 0.0
    assert by["strategy"]["accuracy"] == 1.0


# ------------------------------------------------------------------ #
# judgment_id 生成
# ------------------------------------------------------------------ #
def test_generate_judgment_id_format_and_unique():
    a = FeedbackService.generate_judgment_id("strategy")
    b = FeedbackService.generate_judgment_id("strategy")
    assert a.startswith("strategy_")
    assert a != b  # 含随机后缀，同秒内也应唯一


def test_valid_feedback_enum_is_three():
    assert VALID_FEEDBACKS == (FB_RIGHT, FB_WRONG, FB_DOUBT)


# ------------------------------------------------------------------ #
# 输出侧接入：StrategyAdvisor
# ------------------------------------------------------------------ #
def test_strategy_advisor_emits_judgment_id():
    from services.strategy_advisor import StrategyAdvisor
    adv = StrategyAdvisor(db=None)
    res = adv.analyze({"attachment": "avoidant", "emotion_stability": "low"})
    assert res["judgment_type"] == "strategy"
    assert res["judgment_id"].startswith("strategy_")


def test_strategy_advisor_submit_feedback_writes_records(svc, monkeypatch):
    # 把生产单例替换成临时库 service，避免碰正式 loveai.db
    monkeypatch.setattr(fs_module, "_default_service", svc)
    from services.strategy_advisor import StrategyAdvisor
    adv = StrategyAdvisor(db=None)
    res = adv.analyze({"attachment": "secure"})
    out = adv.submit_feedback("p1", res, "wrong", "推荐的技巧不适用")
    assert out["ok"] is True
    rows = svc.list_feedback("p1")
    assert len(rows) == 1
    assert rows[0]["judgment_id"] == res["judgment_id"]
    assert rows[0]["feedback"] == "wrong"


def test_strategy_advisor_submit_feedback_bad_enum(svc, monkeypatch):
    monkeypatch.setattr(fs_module, "_default_service", svc)
    from services.strategy_advisor import StrategyAdvisor
    adv = StrategyAdvisor(db=None)
    res = adv.analyze({"attachment": "secure"})
    out = adv.submit_feedback("p1", res, "maybe")  # 非法枚举 -> 返回 ok=False
    assert out["ok"] is False
