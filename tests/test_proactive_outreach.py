# -*- coding: utf-8 -*-
"""主动发起建议线门控单测。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.proactive_outreach import should_recommend_outreach  # noqa: E402

HIGH = {"information_value": "high", "baseline_type": "秒回型"}
LOW = {"information_value": "low", "baseline_type": "沉默型"}
NODE = {"long_silence": True, "emotional_low": False, "escalation_node": False}


class TestGates:
    def test_silent_hours_never_fire(self):
        r = should_recommend_outreach(now_hour=3, hours_since_last_outreach=100,
                                      surprise=HIGH, north_star=NODE, rng=0.9)
        assert r["fire"] is False and "静默" in r["reason"]

    def test_cooldown_blocks(self):
        r = should_recommend_outreach(now_hour=14, hours_since_last_outreach=2,
                                      surprise=HIGH, north_star={}, rng=0.9)
        assert r["fire"] is False and "冷却" in r["reason"]

    def test_high_surprise_triggers(self):
        # 秒回基线->高意外，冷却已过，概率门通过(rng<prob)
        r = should_recommend_outreach(now_hour=14, hours_since_last_outreach=100,
                                      surprise=HIGH, north_star={}, rng=0.1)
        assert r["fire"] is True

    def test_low_surprise_no_node_does_not_trigger(self):
        # 沉默型基线->低意外，且无节点信号：不触发（不是绝对时长判断）
        r = should_recommend_outreach(now_hour=14, hours_since_last_outreach=100,
                                      surprise=LOW, north_star={}, rng=0.1)
        assert r["fire"] is False

    def test_node_trigger_even_low_surprise(self):
        r = should_recommend_outreach(now_hour=14, hours_since_last_outreach=100,
                                      surprise=LOW,
                                      north_star={"long_silence": True}, rng=0.1)
        assert r["fire"] is True

    def test_probability_gate_can_block(self):
        # 命中触发源但 rng>=prob -> 概率门拦截（不固定频率）
        r = should_recommend_outreach(now_hour=14, hours_since_last_outreach=100,
                                      surprise=HIGH, north_star={}, rng=0.9)
        assert r["fire"] is False and "概率门" in r["reason"]

    def test_avoidant_not_pushed_unless_long_silence(self):
        # 回避型+情绪低点但非长期沉默 -> 降频不施压
        r = should_recommend_outreach(now_hour=14, hours_since_last_outreach=100,
                                      surprise=HIGH,
                                      north_star={"emotional_low": True},
                                      attachment="回避型依恋", rng=0.1)
        assert r["fire"] is False and r["do_not_push"] is True

    def test_avoidant_long_silence_can_trigger(self):
        r = should_recommend_outreach(now_hour=14, hours_since_last_outreach=100,
                                      surprise=LOW,
                                      north_star={"long_silence": True},
                                      attachment="回避型依恋", rng=0.1)
        assert r["fire"] is True


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
