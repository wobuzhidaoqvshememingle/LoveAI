# -*- coding: utf-8 -*-
"""6 种路由策略的选 provider 正确性。"""
from __future__ import annotations

from app.core.router import Router
from tests.conftest import make_req


def _router(basic_cfg, tmp_db) -> Router:
    return Router(basic_cfg, tmp_db)


def test_priority_strategy_orders_by_priority(basic_cfg, tmp_db):
    r = _router(basic_cfg, tmp_db)
    req = make_req("gpt-4o", gw_strategy="priority")
    cands = r.route(req, registered={"primary", "secondary", "premium"})
    same = [c for c in cands if c.layer in (1, 2)]
    assert same[0].provider == "primary"   # priority=10
    assert same[1].provider == "secondary"  # priority=50


def test_cost_first_prefers_free_tier(basic_cfg, tmp_db):
    r = _router(basic_cfg, tmp_db)
    req = make_req("gpt-4o", gw_strategy="cost_first")
    cands = r.route(req, registered={"primary", "secondary"})
    assert cands[0].provider == "primary"   # free
    assert cands[1].provider == "secondary"  # low


def test_quality_first_prefers_high_tier(basic_cfg, tmp_db):
    # 让 premium 也支持 gpt-4o, 并且是 high tier
    basic_cfg.providers[2].models = ["gpt-4o", "claude-3-opus"]
    r = _router(basic_cfg, tmp_db)
    req = make_req("gpt-4o", gw_strategy="quality_first")
    cands = r.route(req, registered={"primary", "secondary", "premium"})
    # 同模型: primary(free), secondary(low), premium(high)
    # quality_first 应把 premium(high) 排第一
    same = [c for c in cands if c.layer in (1, 2)]
    assert same[0].provider == "premium"
    assert same[-1].provider == "primary"


def test_latency_first_uses_db_health(basic_cfg, tmp_db):
    tmp_db.log_request(provider="primary", model="gpt-4o", prompt_tokens=1, completion_tokens=1,
                       latency_ms=200, status="success")
    tmp_db.log_request(provider="secondary", model="gpt-4o", prompt_tokens=1, completion_tokens=1,
                       latency_ms=50, status="success")
    r = _router(basic_cfg, tmp_db)
    req = make_req("gpt-4o", gw_strategy="latency_first")
    cands = r.route(req, registered={"primary", "secondary"})
    assert cands[0].provider == "secondary"   # 50ms < 200ms
    assert cands[1].provider == "primary"


def test_load_balance_round_robin(basic_cfg, tmp_db):
    r = _router(basic_cfg, tmp_db)
    req = make_req("gpt-4o", gw_strategy="load_balance")
    c1 = r.route(req, registered={"primary", "secondary"})
    c2 = r.route(req, registered={"primary", "secondary"})
    assert [c.provider for c in c1] != [c.provider for c in c2]


def test_auto_prefers_healthy_free(basic_cfg, tmp_db):
    r = _router(basic_cfg, tmp_db)
    req = make_req("gpt-4o", gw_strategy="auto")
    cands = r.route(req, registered={"primary", "secondary"})
    assert cands[0].provider == "primary"


def test_preferred_provider_moves_to_layer1(basic_cfg, tmp_db):
    r = _router(basic_cfg, tmp_db)
    req = make_req("gpt-4o", gw_preferred_provider="secondary")
    cands = r.route(req, registered={"primary", "secondary"})
    assert cands[0].provider == "secondary"
    assert cands[0].layer == 1
    assert cands[1].provider == "primary"
    assert cands[1].layer == 2


def test_fallback_layers_present_for_unknown_model(basic_cfg, tmp_db):
    r = _router(basic_cfg, tmp_db)
    req = make_req("no-such-model-xyz")
    cands = r.route(req, registered={"primary", "secondary", "premium"})
    layers = {c.layer for c in cands}
    assert 3 in layers or 4 in layers
    assert len(cands) > 0
