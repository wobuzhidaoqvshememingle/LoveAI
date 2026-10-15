# -*- coding: utf-8 -*-
"""断路器三态测试。"""
from __future__ import annotations

import time

from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry


def test_closed_allows_requests():
    cb = CircuitBreaker("p", failure_threshold=3, open_sec=300)
    assert cb.allow() is True
    assert cb.state == "closed"


def test_opens_after_threshold_failures():
    cb = CircuitBreaker("p", failure_threshold=3, open_sec=300)
    for _ in range(3):
        cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False


def test_open_reopens_after_open_sec(monkeypatch):
    cb = CircuitBreaker("p", failure_threshold=1, open_sec=1)
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False
    # 时间快进
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 2.0)
    assert cb.allow() is True
    assert cb.state == "half_open"


def test_half_open_success_closes():
    cb = CircuitBreaker("p", failure_threshold=1, open_sec=1, half_open_trial=1)
    cb.record_failure()
    # 进入 half_open
    cb._state = CircuitBreaker.STATE_HALF_OPEN
    cb._trials_used = 0
    # 放行试探
    assert cb.allow() is True
    cb.record_success()
    assert cb.state == "closed"
    assert cb.allow() is True


def test_half_open_failure_reopens():
    cb = CircuitBreaker("p", failure_threshold=1, open_sec=10, half_open_trial=1)
    cb.record_failure()
    cb._state = CircuitBreaker.STATE_HALF_OPEN
    cb._trials_used = 0
    assert cb.allow() is True
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow() is False


def test_half_open_limits_trials():
    cb = CircuitBreaker("p", failure_threshold=1, open_sec=10, half_open_trial=1)
    cb._state = CircuitBreaker.STATE_HALF_OPEN
    cb._trials_used = 0
    assert cb.allow() is True   # 第 1 个试探
    assert cb.allow() is False  # 超额


def test_registry_gets_or_creates():
    reg = CircuitBreakerRegistry(failure_threshold=3, open_sec=300)
    a = reg.get("alpha")
    b = reg.get("alpha")
    assert a is b
    assert reg.get("beta") is not a


def test_success_resets_failure_count():
    cb = CircuitBreaker("p", failure_threshold=3, open_sec=300)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    # 仍未到阈值 3
    assert cb.state == "closed"
