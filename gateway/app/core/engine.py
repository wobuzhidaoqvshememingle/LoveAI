# -*- coding: utf-8 -*-
"""
引擎门面 — HTTP 层只跟这个类打交道。

职责:
  - 接收 ChatCompletionRequest
  - 用 Router 选出候选 provider 列表
  - 用 FallbackChain 依次尝试 (含断路器、用量窗口、重试退避)
  - 写 SQLite 日志
  - 返回 ChatCompletionResponse 或 async iterator[StreamChunk]
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, AsyncIterator, Dict, List, Optional

from app.config import GatewayConfig
from app.db import Database
from app.models import ChatCompletionRequest, ChatCompletionResponse, StreamChunk

from app.core.circuit_breaker import CircuitBreakerRegistry
from app.core.compression import MessageCompressor
from app.core.router import Router
from app.core.usage import UsageTracker
from app.providers.base import ProviderError

logger = logging.getLogger(__name__)


# 退避序列: 第 1 个候选 0s, 第 2 个 2s, 第 3 个 5s; ±50% 抖动
_BACKOFF_BASE = [0.0, 2.0, 5.0]


def _backoff_sleep_seconds(attempt_idx: int) -> float:
    base = _BACKOFF_BASE[min(attempt_idx, len(_BACKOFF_BASE) - 1)]
    if base <= 0:
        return 0.0
    jitter = base * 0.5
    return base + random.uniform(-jitter, jitter)


class Engine:
    """网关主引擎单例。"""

    def __init__(self, cfg: GatewayConfig, db: Database) -> None:
        self.cfg = cfg
        self.db = db
        self.router = Router(cfg, db)
        self.circuit = CircuitBreakerRegistry(
            failure_threshold=cfg.circuit_breaker_failure_threshold,
            open_sec=cfg.circuit_breaker_open_sec,
            half_open_trial=cfg.circuit_breaker_half_open_trial,
        )
        self.usage = UsageTracker(db, cfg)
        self.compressor = MessageCompressor(cfg.compression)
        self._providers: Dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # provider 注册
    # ------------------------------------------------------------------ #
    def register_provider(self, name: str, provider) -> None:
        self._providers[name] = provider

    def get_provider(self, name: str):
        return self._providers.get(name)

    def all_providers(self) -> dict:
        return dict(self._providers)

    def _registered_names(self) -> set:
        return set(self._providers.keys())

    # ------------------------------------------------------------------ #
    # 内部: 候选过滤
    # ------------------------------------------------------------------ #
    def _eligible_candidates(self, req: ChatCompletionRequest) -> list:
        candidates = self.router.route(req, registered=self._registered_names())
        out = []
        for c in candidates:
            if c.provider not in self._providers:
                continue
            cb = self.circuit.get(c.provider)
            if not cb.allow():
                continue
            if not self.usage.is_within_limits(c.provider):
                continue
            out.append(c)
            if len(out) >= self.cfg.fallback_max_tries:
                break
        return out

    # ------------------------------------------------------------------ #
    # 非流式
    # ------------------------------------------------------------------ #
    async def complete(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        req = self.compressor.compress(req)
        candidates = self._eligible_candidates(req)
        if not candidates:
            raise ProviderError(
                "no eligible provider (all open / quota-exceeded / unregistered)",
                provider="",
            )

        strategy = req.gw_strategy or self.cfg.default_strategy
        last_exc: Optional[ProviderError] = None

        for attempt, cand in enumerate(candidates):
            if attempt > 0:
                sleep_s = _backoff_sleep_seconds(attempt)
                if sleep_s > 0:
                    await asyncio.sleep(sleep_s)

            provider = self._providers[cand.provider]
            req2 = req.model_copy(update={"model": cand.model})
            start = time.time()
            try:
                resp = await provider.chat_completion(req2)
                latency = int((time.time() - start) * 1000)
                self.circuit.get(cand.provider).record_success()
                try:
                    self.db.log_request(
                        provider=cand.provider,
                        model=cand.model,
                        prompt_tokens=resp.usage.prompt_tokens,
                        completion_tokens=resp.usage.completion_tokens,
                        latency_ms=latency,
                        status="success",
                        strategy=strategy,
                    )
                except Exception as log_e:
                    logger.warning("log_request failed: %s", log_e)
                resp.provider = cand.provider
                resp.strategy = strategy
                resp.model = cand.model
                return resp
            except ProviderError as e:
                latency = int((time.time() - start) * 1000)
                # retryable 失败 -> 跳闸计数
                if e.retryable:
                    self.circuit.get(cand.provider).record_failure()
                try:
                    self.db.log_request(
                        provider=cand.provider,
                        model=cand.model,
                        prompt_tokens=0,
                        completion_tokens=0,
                        latency_ms=latency,
                        status="error",
                        error_type=type(e).__name__,
                        strategy=strategy,
                    )
                except Exception as log_e:
                    logger.warning("log_request failed: %s", log_e)
                last_exc = e
                # 非可重试错误 (Auth/ModelNotFound) -> 不换 provider, 直接抛
                if not e.retryable:
                    raise
                continue
            except Exception as e:
                # 未知异常 -> 包装成 ServerError, 可重试
                pe = ProviderError(f"unexpected: {e}", provider=cand.provider, cause=e)
                self.circuit.get(cand.provider).record_failure()
                last_exc = pe
                continue

        if last_exc is not None:
            raise last_exc
        raise ProviderError("all candidates failed without exception", provider="")

    # ------------------------------------------------------------------ #
    # 流式: 只在吐出第一个 chunk 之前 fallback
    # ------------------------------------------------------------------ #
    async def stream(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        req = self.compressor.compress(req)
        candidates = self._eligible_candidates(req)
        if not candidates:
            raise ProviderError("no eligible provider for streaming", provider="")

        strategy = req.gw_strategy or self.cfg.default_strategy
        last_exc: Optional[ProviderError] = None

        for attempt, cand in enumerate(candidates):
            if attempt > 0:
                sleep_s = _backoff_sleep_seconds(attempt)
                if sleep_s > 0:
                    await asyncio.sleep(sleep_s)

            provider = self._providers[cand.provider]
            req2 = req.model_copy(update={"model": cand.model})
            start = time.time()
            started = False
            try:
                agen = provider.stream_chat_completion(req2)
                async for chunk in agen:
                    if not started:
                        started = True
                    chunk.provider = cand.provider
                    chunk.model = cand.model
                    yield chunk
                # 正常结束
                latency = int((time.time() - start) * 1000)
                self.circuit.get(cand.provider).record_success()
                try:
                    self.db.log_request(
                        provider=cand.provider,
                        model=cand.model,
                        prompt_tokens=0,
                        completion_tokens=0,
                        latency_ms=latency,
                        status="success",
                        strategy=strategy,
                    )
                except Exception:
                    pass
                return
            except ProviderError as e:
                latency = int((time.time() - start) * 1000)
                if e.retryable and not started:
                    self.circuit.get(cand.provider).record_failure()
                try:
                    self.db.log_request(
                        provider=cand.provider,
                        model=cand.model,
                        prompt_tokens=0,
                        completion_tokens=0,
                        latency_ms=latency,
                        status="error",
                        error_type=type(e).__name__,
                        strategy=strategy,
                    )
                except Exception:
                    pass
                if started:
                    # 已经吐过 chunk 了, 不能换 provider
                    raise
                last_exc = e
                if not e.retryable:
                    raise
                continue
            except Exception as e:
                pe = ProviderError(f"unexpected stream error: {e}", provider=cand.provider, cause=e)
                if not started:
                    self.circuit.get(cand.provider).record_failure()
                    last_exc = pe
                    continue
                raise pe

        if last_exc is not None:
            raise last_exc
        raise ProviderError("stream: no candidate succeeded", provider="")

    # ------------------------------------------------------------------ #
    # 仪表盘
    # ------------------------------------------------------------------ #
    async def health_report(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for name, provider in self._providers.items():
            snap = self.db.provider_health_snapshot(name, window_sec=300)
            cb_state = self.circuit.get(name).state
            try:
                hs = await provider.health_check()
                out.append({
                    "provider": name,
                    "ok": hs.ok,
                    "latency_ms": hs.latency_ms,
                    "error": hs.error,
                    "circuit": cb_state,
                    "success_rate": snap["success_rate"],
                    "avg_latency_ms": snap["avg_latency_ms"],
                    "total_5m": snap["total"],
                })
            except Exception as e:
                out.append({
                    "provider": name,
                    "ok": False,
                    "latency_ms": 0.0,
                    "error": str(e),
                    "circuit": cb_state,
                    "success_rate": snap["success_rate"],
                    "avg_latency_ms": snap["avg_latency_ms"],
                    "total_5m": snap["total"],
                })
        return out

    async def usage_report(self) -> Dict[str, Any]:
        providers: Dict[str, Dict[str, Any]] = {}
        for p in self.cfg.providers:
            ok, usage = self.usage.check(p.name)
            providers[p.name] = {
                "within_limits": ok,
                "usage": usage,
            }
        return {
            "providers": providers,
            "circuit": self.circuit.snapshot(),
        }
