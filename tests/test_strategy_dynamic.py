# -*- coding: utf-8 -*-
"""策略实时动态调整：current_state / 反馈分支 / 情绪适配 回归测试。"""
import os, sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.manipulation_coach import (
    build_counter_manipulation, assess_current_state,
)

PERSONA = {"psychology": {
    "defensive_communication": {"煤气灯贬低": 5},
    "dark_triad_tendencies": {"操纵": 4, "辱骂贬低": 4},
}}


def test_current_state_extracted_from_recent_msgs():
    # 对方此刻愤怒+辱骂
    s = assess_current_state(["你就是废物", "死哪儿去了", "你穷得理直气壮"])
    assert s["emotion"] == "愤怒"
    assert s["trend"]  # 趋势被判断

    # 对方此刻回避激活
    s2 = assess_current_state(["忙", "算了", "没必要"])
    assert "回避" in s2["attachment_activation"]

    # 对方此刻焦虑追问
    s3 = assess_current_state(["你在哪", "为什么不回我", "是不是不爱我"])
    assert "焦虑" in s3["attachment_activation"]


def test_gold_digger_output_has_dynamic_fields():
    r = build_counter_manipulation(
        persona=PERSONA,
        finance_risk_flags=["对方多次提及转账/礼物"],
        recent_messages=["你穷得理直气壮", "是你不够努力", "发工资了转账吧"],
    )
    assert r["triggered"] is True
    # 必须包含动态输出字段
    assert r["current_state"]["emotion"]
    assert r["feedback_branches"], "应有反馈分支"
    assert r["expected_reaction"]
    assert r["validity_window"]
    assert r["strategy_style"]["style"] == "warm_water"
    # 反馈分支至少4种反应-动作映射
    assert len(r["feedback_branches"]) >= 3
    for b in r["feedback_branches"]:
        assert b["when"] and b["then"]


def test_state_warmup_switches_tone():
    # 对方由冷转热 → 趋势识别为转热
    r = build_counter_manipulation(
        persona=PERSONA,
        finance_risk_flags=["对方多次提及转账/礼物"],
        recent_messages=["忙", "算了", "好呀", "亲爱的我们见一面吧"],
    )
    assert "转热" in r["current_state"]["trend"] or r["current_state"]["emotion"] == "开心/热情"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
