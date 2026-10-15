# -*- coding: utf-8 -*-
"""
OpenAI 兼容的请求/响应 Pydantic 模型。
尽量与 openai>=1.0 SDK 的字段对齐, 这样 LoveAI 主程序改 base_url 就能直接用。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 消息
# --------------------------------------------------------------------------- #
class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool", "developer"]
    content: Any = ""          # str 或 list[dict] (多模态)
    name: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None


# --------------------------------------------------------------------------- #
# 请求
# --------------------------------------------------------------------------- #
class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    stop: Optional[Any] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[Any] = None
    response_format: Optional[dict] = None
    user: Optional[str] = None
    # 网关私有参数 (下划线前缀, 转发时剥离)
    gw_preferred_provider: Optional[str] = Field(default=None, exclude=True)
    gw_strategy: Optional[str] = Field(default=None, exclude=True)
    gw_allow_fallback: Optional[bool] = Field(default=True, exclude=True)

    def to_openai_dict(self) -> dict:
        """转成发给上游的 OpenAI 兼容 dict (剥离 gw_* 私有字段)。"""
        d = self.model_dump(exclude_none=True)
        for k in list(d.keys()):
            if k.startswith("gw_"):
                d.pop(k)
        return d


# --------------------------------------------------------------------------- #
# 响应
# --------------------------------------------------------------------------- #
class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: Optional[str] = "stop"


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:24]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[Choice] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    # 网关元信息 (OpenAI SDK 会忽略额外字段)
    provider: Optional[str] = None
    strategy: Optional[str] = None


class StreamChoiceDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class StreamChoice(BaseModel):
    index: int = 0
    delta: StreamChoiceDelta = Field(default_factory=StreamChoiceDelta)
    finish_reason: Optional[str] = None


class StreamChunk(BaseModel):
    """SSE 单块。转成 OpenAI 的 chunk JSON 发出去。"""
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:24]}")
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[StreamChoice] = Field(default_factory=list)
    usage: Optional[Usage] = None
    provider: Optional[str] = None

    def to_sse_json(self) -> dict:
        return self.model_dump(exclude_none=True)


# --------------------------------------------------------------------------- #
# 健康/状态
# --------------------------------------------------------------------------- #
class HealthStatus(BaseModel):
    provider: str
    ok: bool
    latency_ms: float = 0.0
    error: Optional[str] = None


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "loveai-gateway"


# --------------------------------------------------------------------------- #
# 错误
# --------------------------------------------------------------------------- #
class GatewayError(BaseModel):
    message: str
    type: str = "gateway_error"
    code: Optional[str] = None
    tried_providers: list[dict] = Field(default_factory=list)
