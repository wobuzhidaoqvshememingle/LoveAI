# -*- coding: utf-8 -*-
"""共享 fixtures: 临时 DB / GatewayConfig / 假 provider。"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, List

import pytest

# 让 tests 可以直接 import app.*
GATEWAY_ROOT = Path(__file__).resolve().parent.parent
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))

from app.config import GatewayConfig, ProviderConfig  # noqa: E402
from app.db import Database  # noqa: E402
from app.models import (  # noqa: E402
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    StreamChunk,
    Usage,
)
from app.providers.base import (  # noqa: E402
    AuthError,
    BaseProvider,
    ModelNotFoundError,
    ProviderError,
    RateLimitError,
    ServerError,
)


# --------------------------------------------------------------------------- #
# 假 provider
# --------------------------------------------------------------------------- #
class FakeProvider(BaseProvider):
    """可脚本化行为的测试 provider。"""

    def __init__(
        self,
        config: ProviderConfig,
        behavior: str = "ok",
        latency_ms: int = 0,
    ) -> None:
        super().__init__(config)
        self.behavior = behavior
        self.latency_ms = latency_ms
        self.calls: List[str] = []

    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        self.calls.append(req.model)
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000.0)
        if self.behavior == "always_fail":
            raise ServerError("boom", provider=self.name)
        if self.behavior == "fail_once":
            if len(self.calls) == 1:
                raise ServerError("first call fails", provider=self.name)
        if self.behavior == "auth":
            raise AuthError("bad key", provider=self.name)
        if self.behavior == "model_not_found":
            raise ModelNotFoundError("no such model", provider=self.name)
        return ChatCompletionResponse(
            model=req.model,
            choices=[],
            usage=Usage(prompt_tokens=5, completion_tokens=7, total_tokens=12),
        )

    async def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        self.calls.append(req.model)
        if self.behavior == "always_fail":
            raise ServerError("boom", provider=self.name)
        if self.behavior == "auth":
            raise AuthError("bad key", provider=self.name)
        if self.behavior == "fail_once":
            raise ServerError("stream first call fails", provider=self.name)
        for text in ["Hello", " ", "world"]:
            chunk = StreamChunk(model=req.model)
            chunk.choices = [type("C", (), {
                "index": 0,
                "delta": type("D", (), {"content": text, "role": None})(),
                "finish_reason": None,
            })()]
            yield chunk


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def tmp_db():
    d = tempfile.mkdtemp(prefix="loveai_gw_test_")
    db = Database(Path(d) / "test.db")
    yield db


@pytest.fixture
def basic_cfg() -> GatewayConfig:
    cfg = GatewayConfig(
        default_strategy="priority",
        fallback_max_tries=3,
        circuit_breaker_failure_threshold=3,
        circuit_breaker_open_sec=300,
        circuit_breaker_half_open_trial=1,
        data_dir=tempfile.mkdtemp(prefix="loveai_gw_data_"),
    )
    cfg.providers = [
        ProviderConfig(
            name="primary", type="openai_compatible",
            base_url="https://primary.example.com",
            models=["gpt-4o", "gpt-4o-mini"],
            priority=10, cost_tier="free",
        ),
        ProviderConfig(
            name="secondary", type="openai_compatible",
            base_url="https://secondary.example.com",
            models=["gpt-4o", "gpt-4o-mini", "claude-3-sonnet"],
            priority=50, cost_tier="low",
        ),
        ProviderConfig(
            name="premium", type="openai_compatible",
            base_url="https://premium.example.com",
            models=["claude-3-opus"],
            priority=100, cost_tier="high",
        ),
    ]
    return cfg


def make_req(model: str = "gpt-4o", **gw_kwargs) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="hi")],
        **gw_kwargs,
    )
