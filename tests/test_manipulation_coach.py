# -*- coding: utf-8 -*-
"""manipulation_coach 回归测试：识别操控模式 + 非对抗反制 + 手段中立。"""
import os, sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.manipulation_coach import build_counter_manipulation


def test_no_signal_no_trigger():
    r = build_counter_manipulation(persona={"psychology": {}})
    assert r["triggered"] is False
    assert r["counter_strategies"] == []


def test_gaslight_and_money_triggers():
    persona = {"psychology": {
        "defensive_communication": {"煤气灯贬低": 5},
        "dark_triad_tendencies": {"操纵": 4, "辱骂贬低": 4},
    }}
    r = build_counter_manipulation(
        persona=persona,
        finance_risk_flags=["对方多次提及转账/礼物"],
    )
    assert r["triggered"] is True
    keys = {p["key"] for p in r["manipulation_patterns"]}
    assert "gaslighting" in keys
    assert "money_control" in keys
    assert "verbal_degradation" in keys
    # 反制步骤非空，且每步含效果与对方可能反应/跑掉说明（渐进式）
    assert len(r["counter_strategies"]) >= 3
    for s in r["counter_strategies"]:
        assert s["effect"]
        assert s.get("likely_reaction") or s.get("if_runs_or_escalates")
    # 引导路线存在
    assert len(r["coaching"]) >= 2
    # 手段中立：不含道德禁令
    note = r["neutral_note"]
    for bad in ("赶紧分手", "禁止继续", "绝对不要"):
        assert bad not in note


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
