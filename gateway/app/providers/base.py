# -*- coding: utf-8 -*-
"""
Provider 抽象基类 + 异常体系。

新增厂商:
  1. 在 app/providers/ 下新建 <name>_provider.py
  2. 继承 BaseProvider, 实现 chat_completion / stream_chat_completion / health_check
  3. 在 providers/registry.py 的 BUILDERS 里登记 type -> 类
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from app.config import ProviderConfig
from app.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    HealthStatus,
    StreamChunk,
)


# --------------------------------------------------------------------------- #
# 异常体系 (路由层根据异常类型决定是否 fallback)
# --------------------------------------------------------------------------- #
class ProviderError(Exception):
    """所有 provider 异常基类。"""
    retryable: bool = True
    http_status: int = 502

    def __init__(self, message: str, *, provider: str = "", cause: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.cause = cause


class AuthError(ProviderError):
    """401/403 — key 错了, 不要重试这个 provider。"""
    retryable = False
    http_status = 401


class RateLimitError(ProviderError):
    """429 — 限流, 应切下一个 provider。"""
    retryable = True
    http_status = 429


class ModelNotFoundError(ProviderError):
    """404 / 模型不存在。"""
    retryable = False
    http_status = 404


class ServerError(ProviderError):
    """5xx。"""
    retryable = True
    http_status = 502


class TimeoutError(ProviderError):
    """上游超时。"""
    retryable = True
    http_status = 504


# --------------------------------------------------------------------------- #
# 抽象基类
# --------------------------------------------------------------------------- #
class BaseProvider(ABC):
    """所有厂商适配器的统一接口。"""

    provider_type: str = "base"

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.name = config.name

    # -- 属性 ---------------------------------------------------------------- #
    @property
    def supported_models(self) -> list[str]:
        return list(self.config.models)

    def supports(self, model: str) -> bool:
        return model in self.config.models

    # -- 主接口 --------------------------------------------------------------- #
    @abstractmethod
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        """非流式对话。"""

    @abstractmethod
    def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        """流式对话, 返回 async iterator。"""

    async def health_check(self) -> HealthStatus:
        """轻量健康检查, 默认返回 ok=True (子类可覆盖做真实探测)。"""
        return HealthStatus(provider=self.name, ok=True)

    # -- 成本估算 (路由层 cost_first / auto 用) -------------------------------- #
    def estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """粗估一次调用成本 (美元)。默认按 cost_tier 给个常数。"""
        tier = self.config.cost_tier
        if tier == "free":
            return 0.0
        if tier == "low":
            return (prompt_tokens + completion_tokens) * 0.0001 / 1000.0
        return (prompt_tokens + completion_tokens) * 0.001 / 1000.0
