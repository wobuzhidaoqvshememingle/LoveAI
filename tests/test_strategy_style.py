# -*- coding: utf-8 -*-
"""策略风格判断（刚猛 vs 温水煮青蛙）回归测试。"""
import os, sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.manipulation_coach import build_counter_manipulation

PERSONA = {"psychology": {
    "defensive_communication": {"煤气灯贬低": 5},
    "dark_triad_tendencies": {"操纵": 4, "辱骂贬低": 4},
}}


def test_gold_digger_default_warm_water():
    r = build_counter_manipulation(
        persona=PERSONA,
        finance_risk_flags=["对方多次提及转账/礼物"],
        stage=6, user_goal="引导修复", user_ability="一般",
    )
    assert r["triggered"] is True
    style = r["strategy_style"]
    # 捞女/操控 + 引导修复 + 一般执行力 → 默认温水煮青蛙
    assert style["style"] == "warm_water"
    assert style["reason"]
    # 反制是渐进式（含"渐进式减量"），不是"立刻停止加码"
    titles = " ".join(s["principle"] for s in r["counter_strategies"])
    assert "渐进式减量" in titles
    assert "立刻停止加码" not in titles


def test_immediate_stop_goal_prefers_direct():
    r = build_counter_manipulation(
        persona=PERSONA,
        finance_risk_flags=["对方多次提及转账/礼物"],
        stage=6, user_goal="立刻止损", user_ability="强",
    )
    assert r["strategy_style"]["style"] == "direct"


def test_no_signal_no_style():
    r = build_counter_manipulation(persona={})
    assert r["triggered"] is False
    assert r["strategy_style"] == {}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
