# -*- coding: utf-8 -*-
"""损失厌恶（相对代价比例版）回归测试。"""
import os, sys
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.loss_aversion import analyze_loss_aversion
from services.psychology_laws import match_laws


def test_scenario_A_commoner_beats_rich():
    """月入5000花500(10%) > 月入5万花2000(4%)。"""
    commoner = analyze_loss_aversion(
        received_text="", your_text="", their_monthly_income=5000,
        their_spent_yuan=500)
    rich = analyze_loss_aversion(
        received_text="", your_text="", their_monthly_income=50000,
        their_spent_yuan=2000)
    money_c = commoner["dimensions"]["金钱"]
    money_r = rich["dimensions"]["金钱"]
    # 普通人代价比例10%、评分2；富人4%、评分1
    assert money_c["cost_ratio"] == 0.1
    assert money_c["score"] == 2
    assert money_r["cost_ratio"] == 0.04
    assert money_r["score"] == 1
    assert money_c["score"] > money_r["score"]


def test_scenario_B_busy_beats_free():
    """忙碌者挤出时间 vs 空闲者长陪，相对代价比例更高。"""
    busy = analyze_loss_aversion(received_text="我加班忙 在干嘛 晚安 陪你")
    free = analyze_loss_aversion(received_text="好呀")
    busy_time = busy["dimensions"]["时间"]
    free_time = free["dimensions"]["时间"]
    assert busy_time["capacity"] == 2.0  # 识别为忙碌
    assert busy_time["cost_ratio"] > free_time["cost_ratio"]


def test_scenario_C_unemployment_raises_cost():
    """对方失业后可承受能力下降，同样花200块代价比例更高。"""
    employed = analyze_loss_aversion(
        received_text="", their_monthly_income=8000, their_spent_yuan=200)
    unemployed = analyze_loss_aversion(
        received_text="", their_monthly_income=2000, their_spent_yuan=200)
    assert unemployed["dimensions"]["金钱"]["cost_ratio"] > \
        employed["dimensions"]["金钱"]["cost_ratio"]


def test_low_loss_one_sided():
    """单方面付出→低损失感、高风险、引导投入。"""
    r = analyze_loss_aversion(
        received_text="随便，你定吧",
        your_text="我想你了 帮你搬家 请客",
        your_financial_count=3, their_financial_count=0)
    assert "高风险" in r["risk"]


def test_social_hierarchy_triggers():
    hits = match_laws(persona={}, stage=5, dims={})
    assert "social_hierarchy_comm" in [h["id"] for h in hits]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
