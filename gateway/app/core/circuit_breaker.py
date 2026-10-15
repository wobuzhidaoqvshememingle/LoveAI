# -*- coding: utf-8 -*-
"""
断路器 (Circuit Breaker)。

三态:
  closed     正常放行; 连续失败 N 次 -> open
  open       拒绝请求 open_sec 秒; 时间到 -> half_open
  half_open  放行 half_open_trial 个试探; 任意成功 -> closed, 任意失败 -> open

只统计 retryable=True 的失败 (AuthError/ModelNotFound 这类配置错误不跳闸)。
纯内存, 线程安全 (锁粒度小)。
"""
from __future__ import annotations

import threading
import time
from typing import Dict


class CircuitBreaker:
    """单个 provider 的断路器。"""

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        open_sec: int = 300,
        half_open_trial: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, int(failure_threshold))
        self.open_sec = float(open_sec)
        self.half_open_trial = max(1, int(half_open_trial))

        self._lock = threading.Lock()
        self._state = self.STATE_CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trials_used = 0

    # ------------------------------------------------------------------ #
    # 只读
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_transition_locked()
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures

    def _maybe_transition_locked(self) -> None:
        """open -> half_open (时间到)。调用方必须持锁。"""
        if self._state == self.STATE_OPEN and (time.time() - self._opened_at) >= self.open_sec:
            self._state = self.STATE_HALF_OPEN
            self._trials_used = 0
            self._failures = 0

    # ------------------------------------------------------------------ #
    # 决策
    # ------------------------------------------------------------------ #
    def allow(self, now: float | None = None) -> bool:
        """是否放行本次请求。"""
        with self._lock:
            self._maybe_transition_locked()
            if self._state == self.STATE_CLOSED:
                return True
            if self._state == self.STATE_OPEN:
                return False
            # half_open: 放行有限个试探
            if self._trials_used < self.half_open_trial:
                self._trials_used += 1
                return True
            return False

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = self.STATE_CLOSED
            self._trials_used = 0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == self.STATE_HALF_OPEN:
                # 半开放行的试探失败 -> 重新 open
                self._state = self.STATE_OPEN
                self._opened_at = time.time()
                self._trials_used = 0
                return
            if self._failures >= self.failure_threshold:
                self._state = self.STATE_OPEN
                self._opened_at = time.time()

    # ------------------------------------------------------------------ #
    # 测试辅助
    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        with self._lock:
            self._state = self.STATE_CLOSED
            self._failures = 0
            self._opened_at = 0.0
            self._trials_used = 0


class CircuitBreakerRegistry:
    """按 provider name 持有一组断路器。"""

    def __init__(
        self,
        failure_threshold: int = 3,
        open_sec: int = 300,
        half_open_trial: int = 1,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.open_sec = open_sec
        self.half_open_trial = half_open_trial
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> CircuitBreaker:
        with self._lock:
            cb = self._breakers.get(name)
            if cb is None:
                cb = CircuitBreaker(
                    name=name,
                    failure_threshold=self.failure_threshold,
                    open_sec=self.open_sec,
                    half_open_trial=self.half_open_trial,
                )
                self._breakers[name] = cb
            return cb

    def is_open(self, name: str) -> bool:
        return self.get(name).state == CircuitBreaker.STATE_OPEN

    def snapshot(self) -> dict:
        """仪表盘用。"""
        out = {}
        with self._lock:
            for name, cb in self._breakers.items():
                out[name] = {
                    "state": cb.state,
                    "failures": cb.failure_count,
                }
        return out
