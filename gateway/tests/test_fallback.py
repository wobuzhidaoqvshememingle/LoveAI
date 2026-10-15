# -*- coding: utf-8 -*-
"""Fallback 链: 失败切换 / 退避 / 非流式 + 流式。"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core.engine import Engine
from app.providers.base import AuthError, ProviderError, ServerError
from tests.conftest import FakeProvider, make_req


def _make_engine(basic_cfg, db, providers: dict) -> Engine:
    eng = Engine(basic_cfg, db)
    for name, p in providers.items():
        eng.register_provider(name, p)
    return eng


@pytest.mark.asyncio
async def test_fallback_to_next_provider_on_retryable_error(basic_cfg, tmp_db, monkeypatch):
    """首选 provider 抛 ServerError -> 切到下一个同模型 provider 成功。"""
    import app.core.engine as eng_mod
    monkeypatch.setattr(eng_mod, "_BACKOFF_BASE", [0.0, 0.0, 0.0])

    primary = FakeProvider(basic_cfg.providers[0], behavior="always_fail")
    secondary = FakeProvider(basic_cfg.providers[1], behavior="ok")
    eng = _make_engine(basic_cfg, tmp_db, {"primary": primary, "secondary": secondary})

    req = make_req("gpt-4o", gw_strategy="priority")
    resp = await eng.complete(req)
    assert resp.provider == "secondary"
    assert resp.model == "gpt-4o"
    assert "gpt-4o" in primary.calls
    assert "gpt-4o" in secondary.calls


@pytest.mark.asyncio
async def test_non_retryable_auth_raises_immediately(basic_cfg, tmp_db, monkeypatch):
    """AuthError (不可重试) -> 立即抛, 不切下一个。"""
    import app.core.engine as eng_mod
    monkeypatch.setattr(eng_mod, "_BACKOFF_BASE", [0.0, 0.0, 0.0])

    primary = FakeProvider(basic_cfg.providers[0], behavior="auth")
    secondary = FakeProvider(basic_cfg.providers[1], behavior="ok")
    eng = _make_engine(basic_cfg, tmp_db, {"primary": primary, "secondary": secondary})

    req = make_req("gpt-4o")
    with pytest.raises(AuthError):
        await eng.complete(req)
    assert secondary.calls == []


@pytest.mark.asyncio
async def test_all_candidates_fail_raises_last_exception(basic_cfg, tmp_db, monkeypatch):
    """所有候选都失败 -> 抛最后一个异常。"""
    import app.core.engine as eng_mod
    monkeypatch.setattr(eng_mod, "_BACKOFF_BASE", [0.0, 0.0, 0.0])

    primary = FakeProvider(basic_cfg.providers[0], behavior="always_fail")
    eng = _make_engine(basic_cfg, tmp_db, {"primary": primary})

    req = make_req("gpt-4o")
    with pytest.raises(ProviderError):
        await eng.complete(req)


@pytest.mark.asyncio
async def test_backoff_sleeps_between_tries(basic_cfg, tmp_db, monkeypatch):
    """退避序列: 第 2 个候选开始 sleep, 且 sleep 时长来自 _BACKOFF_BASE。"""
    import app.core.engine as eng_mod
    monkeypatch.setattr(eng_mod, "_BACKOFF_BASE", [0.0, 2.0, 5.0])

    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    # 让 premium 也支持 gpt-4o, 这样候选列表是 primary/secondary/premium 三个同模型
    basic_cfg.providers[2].models = ["gpt-4o", "claude-3-opus"]
    basic_cfg.fallback_max_tries = 3
    primary = FakeProvider(basic_cfg.providers[0], behavior="always_fail")
    secondary = FakeProvider(basic_cfg.providers[1], behavior="always_fail")
    premium = FakeProvider(basic_cfg.providers[2], behavior="ok")
    eng = _make_engine(basic_cfg, tmp_db, {
        "primary": primary, "secondary": secondary, "premium": premium,
    })

    req = make_req("gpt-4o")
    resp = await eng.complete(req)
    # primary 失败 -> sleep(2±1) -> secondary 失败 -> sleep(5±2.5) -> premium 成功
    assert len(sleeps) == 2
    # 第一次 sleep 在 1~3s 之间
    assert 1.0 <= sleeps[0] <= 3.0
    # 第二次 sleep 在 2.5~7.5s 之间
    assert 2.5 <= sleeps[1] <= 7.5
    assert resp.provider == "premium"


@pytest.mark.asyncio
async def test_stream_fallback_before_first_chunk(basic_cfg, tmp_db, monkeypatch):
    """流式: 首选 provider 在吐 chunk 之前抛错 -> 切下一个。"""
    import app.core.engine as eng_mod
    monkeypatch.setattr(eng_mod, "_BACKOFF_BASE", [0.0, 0.0, 0.0])

    primary = FakeProvider(basic_cfg.providers[0], behavior="fail_once")
    secondary = FakeProvider(basic_cfg.providers[1], behavior="ok")
    eng = _make_engine(basic_cfg, tmp_db, {"primary": primary, "secondary": secondary})

    req = make_req("gpt-4o")
    chunks = []
    async for c in eng.stream(req):
        chunks.append(c)
    assert len(chunks) >= 1
    assert len(secondary.calls) >= 1


@pytest.mark.asyncio
async def test_circuit_open_skips_provider(basic_cfg, tmp_db, monkeypatch):
    """断路器 open 后, 该 provider 直接被跳过 (不调用)。"""
    import app.core.engine as eng_mod
    monkeypatch.setattr(eng_mod, "_BACKOFF_BASE", [0.0, 0.0, 0.0])

    primary = FakeProvider(basic_cfg.providers[0], behavior="ok")
    secondary = FakeProvider(basic_cfg.providers[1], behavior="ok")
    eng = _make_engine(basic_cfg, tmp_db, {"primary": primary, "secondary": secondary})

    # 手动把 primary 断路器打开, opened_at=now (让它保持 open)
    cb = eng.circuit.get("primary")
    cb._state = cb.STATE_OPEN
    cb._opened_at = time.time()

    req = make_req("gpt-4o")
    resp = await eng.complete(req)
    assert resp.provider == "secondary"
    assert primary.calls == []
