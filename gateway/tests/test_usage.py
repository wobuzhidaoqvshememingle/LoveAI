# -*- coding: utf-8 -*-
"""用量窗口: rpm/tpm/rpd/tpd 超限自动跳过。"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.config import ProviderConfig
from app.core.engine import Engine
from app.core.usage import UsageTracker
from tests.conftest import FakeProvider, make_req


def test_usage_tracker_allows_when_under_limit(basic_cfg, tmp_db):
    basic_cfg.providers[0].rpm_limit = 10
    tr = UsageTracker(tmp_db, basic_cfg)
    ok, usage = tr.check("primary")
    assert ok is True
    assert usage["rpm"] == 0


def test_usage_tracker_blocks_when_rpm_exceeded(basic_cfg, tmp_db):
    basic_cfg.providers[0].rpm_limit = 2
    # 写 2 条最近 60s 的日志
    for _ in range(2):
        tmp_db.log_request(
            provider="primary", model="gpt-4o",
            prompt_tokens=1, completion_tokens=1,
            latency_ms=1, status="success",
        )
    tr = UsageTracker(tmp_db, basic_cfg)
    ok, usage = tr.check("primary")
    assert ok is False
    assert any("rpm" in s for s in usage["_over"])


def test_usage_tracker_blocks_when_tpd_exceeded(basic_cfg, tmp_db):
    basic_cfg.providers[0].tpd_limit = 100
    tmp_db.log_request(
        provider="primary", model="gpt-4o",
        prompt_tokens=60, completion_tokens=50,  # 110 > 100
        latency_ms=1, status="success",
    )
    tr = UsageTracker(tmp_db, basic_cfg)
    ok, usage = tr.check("primary")
    assert ok is False
    assert any("tpd" in s for s in usage["_over"])


def test_usage_tracker_zero_means_unlimited(basic_cfg, tmp_db):
    # 0 = 不限
    basic_cfg.providers[0].rpm_limit = 0
    for _ in range(100):
        tmp_db.log_request(
            provider="primary", model="gpt-4o",
            prompt_tokens=1, completion_tokens=1,
            latency_ms=1, status="success",
        )
    tr = UsageTracker(tmp_db, basic_cfg)
    ok, _ = tr.check("primary")
    assert ok is True


@pytest.mark.asyncio
async def test_engine_skips_provider_over_quota(basic_cfg, tmp_db, monkeypatch):
    """engine 在选路时跳过超配额的 provider。"""
    import app.core.engine as eng_mod
    monkeypatch.setattr(eng_mod, "_BACKOFF_BASE", [0.0, 0.0, 0.0])

    # primary 配额写满
    basic_cfg.providers[0].rpm_limit = 5
    for _ in range(5):
        tmp_db.log_request(
            provider="primary", model="gpt-4o",
            prompt_tokens=1, completion_tokens=1,
            latency_ms=1, status="success",
        )

    primary = FakeProvider(basic_cfg.providers[0], behavior="ok")
    secondary = FakeProvider(basic_cfg.providers[1], behavior="ok")
    eng = Engine(basic_cfg, tmp_db)
    eng.register_provider("primary", primary)
    eng.register_provider("secondary", secondary)

    req = make_req("gpt-4o")
    resp = await eng.complete(req)
    # primary 超配额被跳过, 直接走 secondary
    assert resp.provider == "secondary"
    assert primary.calls == []
