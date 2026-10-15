# -*- coding: utf-8 -*-
"""R3.3-06 PsyDI 渐进式人格探测 单测（services/progressive_probe.py）。

覆盖：①轮次进度增长 ②低→高敏分阶（不到熟不越级探高敏）③证据累积复用心理学账本
④next_probe 引导与 done ⑤读边 next_probe(profile)。纯本地、无 LLM、无 DB。
"""
from __future__ import annotations

from services import progressive_probe as pp


def test_initial_state_starts_low_sensitivity_tier():
    """首轮无状态：从低敏 tier1（日常偏好）起步，不越级。"""
    st = pp.update(None, "今天天气不错", {}, {}, turn=1)
    assert st["turns"] == 1
    # 首轮无任何证据 → current_tier 仍为 1（下层未探明前不解锁高敏）
    assert st["current_tier"] == 1
    np_ = st["next_probe"]
    assert np_["done"] is False
    # 起步必在 tier1 维度（values_money / values_commitment）
    assert np_["target_dimension"] in ("values_money", "values_commitment")


def test_confidence_grows_with_signals():
    """同一维度信号多轮累积，置信度单调增长且封顶 1.0。"""
    psy = {"dark_triad_tendencies": {"自恋": True}}
    st = None
    for t in range(1, 12):
        st = pp.update(st, "我就是这样", psy, {}, turn=t)
    conf = st["dimensions"]["dark_narcissism"]["confidence"]
    # 多次 bump 后应明显 >0 且 <=1
    assert 0.0 < conf <= 1.0
    # signals 计数应随调用递增
    assert st["dimensions"]["dark_narcissism"]["signals"] >= 5


def test_tier_ladder_unlocks_only_when_lower_covered():
    """tier1 未探明前，next_probe 不会越级到 tier3 高敏（依恋/黑暗）。"""
    # 无任何证据 → 只探 tier1
    st = pp.update(None, "随便聊聊", {}, {}, turn=1)
    assert st["current_tier"] == 1
    assert st["next_probe"]["current_tier"] == 1
    # 低敏维度不可能在一次调用内就解锁 tier3
    assert st["current_tier"] < 3


def test_values_signal_advances_tier1():
    """命中 values 原文信号 → values 维度置信度上升，进入 probed 后解锁 tier2。"""
    st = None
    # 连续多次命中 values_money / values_commitment，把 tier1 两维顶到 _COVERED
    money_txt = "约会开销AA轮流请各付各"
    commit_txt = "结婚见家长名分一辈子谈多久"
    for t in range(1, 12):
        st = pp.update(st, money_txt if t % 2 else commit_txt, {}, {}, turn=t)
    dm = st["dimensions"]["values_money"]["confidence"]
    dc = st["dimensions"]["values_commitment"]["confidence"]
    assert dm >= pp._COVERED
    assert dc >= pp._COVERED
    # tier1 两维都探明 → 至少解锁 tier2
    assert st["current_tier"] >= 2
    assert "values_money" in st["probed"]
    assert "values_commitment" in st["probed"]


def test_reuse_existing_psychology_evidence_not_fabricated():
    """证据来自已有 psychology 账本（dark_triad/cbt/defensive），无信号时不打标。"""
    # 无证据输入
    st = pp.update(None, "嗯", {}, {}, turn=1)
    assert st["dimensions"]["cognitive_bias"]["confidence"] == 0.0
    assert st["dimensions"]["dark_narcissism"]["confidence"] == 0.0
    # 有 cbt_distortions 账本 → cognitive_bias 升
    st2 = pp.update(None, "嗯", {"cbt_distortions": {"非黑即白": True}}, {}, turn=1)
    assert st2["dimensions"]["cognitive_bias"]["confidence"] > 0.0


def test_next_probe_done_when_all_covered():
    """全部维度探明 → done=True，不再追问（避免过度追问引发防备）。"""
    # 手工构造一个全高置信度状态
    full = {"turns": 5, "dimensions": {k: {"confidence": 0.9, "signals": 3, "last_turn": 1}
                                      for k in pp.DIMENSION_REGISTRY}, "probed": list(pp.DIMENSION_REGISTRY)}
    nxt = pp._next_probe(full)
    assert nxt["done"] is True
    assert nxt["target_dimension"] is None


def test_read_edge_accepts_full_profile():
    """读边 next_probe(profile) 能从 profile.psychology.progressive_probe 取状态。"""
    st = pp.update(None, "约会AA", {}, {}, turn=1)
    profile = {"psychology": {"progressive_probe": st}}
    nxt = pp.next_probe(profile)
    assert "target_dimension" in nxt
    # 空 profile → 给初始低敏建议
    nxt0 = pp.next_probe({})
    assert nxt0["target_dimension"] in ("values_money", "values_commitment")
