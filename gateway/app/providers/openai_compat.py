# -*- coding: utf-8 -*-
"""
通用 OpenAI 兼容协议适配器。

90% 的厂商 (OpenAI / DeepSeek / OpenRouter / Groq / Cerebras / NVIDIA NIM /
Mistral / 硅基流动 / 智谱 / 通义 Dashscope / 腾讯混元 / Moonshot / z.ai /
HuggingFace / SambaNova / GitHub Models / Together / Fireworks / Perplexity /
Hyperbolic / Novita / Lepton / Featherless / Chutes / Pollinations / Cohere compose /
Cloudflare Workers AI / Ollama / LM Studio ...) 都直接复用本类,
靠 ProviderConfig.base_url 区分。

约定: base_url 已经包含到 /v1 (或厂商对应的版本前缀), 本类只在后面拼
``/chat/completions`` 和 ``/models``。
  - Cloudflare Workers AI: base_url =
    ``https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1``
    (account_id 由调用方在配置 base_url 时拼好, 或放 extra.account_id 在
    build 时补全), 本类无需特殊处理。
  - Cohere 新版: base_url = ``https://api.cohere.ai/compose/v1``。
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

import httpx

from app.config import ProviderConfig
from app.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    HealthStatus,
    StreamChunk,
    StreamChoice,
    StreamChoiceDelta,
    Usage,
)
from app.providers.base import (
    AuthError,
    BaseProvider,
    ModelNotFoundError,
    ProviderError,
    RateLimitError,
    ServerError,
    TimeoutError,
)


def _map_http_error(status_code: int, body: str, provider: str) -> ProviderError:
    """把上游 HTTP 状态码映射到网关异常体系。"""
    msg = (body or "").strip()[:800] or f"http {status_code}"
    if status_code in (401, 403):
        return AuthError(msg, provider=provider)
    if status_code == 404:
        return ModelNotFoundError(msg, provider=provider)
    if status_code == 429:
        return RateLimitError(msg, provider=provider)
    if 500 <= status_code < 600:
        return ServerError(msg, provider=provider)
    return ServerError(f"unexpected status {status_code}: {msg}", provider=provider)


class OpenAICompatibleProvider(BaseProvider):
    """OpenAI /v1/chat/completions 协议通用实现。

    支持厂商见模块 docstring。只要厂商暴露 ``POST {base_url}/chat/completions``
    和 ``GET {base_url}/models`` 且请求/响应体与 OpenAI 对齐, 就能用本类。
    """

    provider_type = "openai_compatible"

    # -- 内部工具 ------------------------------------------------------------- #
    def _url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}{path}"

    def _headers(self) -> dict[str, str]:
        api_key = self.config.resolved_api_key()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # 空 key 场景 (零配置 provider / 本地推理):
        #  - 默认带 "Authorization: Bearer none", 满足要求 header 存在但不校验的端点
        #  - extra.no_auth_header=True 时完全不带 Authorization
        # 不要因为空 key 抛 AuthError。
        if self.config.extra.get("no_auth_header", False):
            pass
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers["Authorization"] = "Bearer none"
        # 部分厂商要求额外标识 (OpenRouter 建议带 HTTP-Referer / X-Title)
        extra_headers: dict[str, str] = self.config.extra.get("headers", {}) or {}
        headers.update(extra_headers)
        return headers

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(self.config.timeout_sec))

    # -- 非流式 --------------------------------------------------------------- #
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        url = self._url("/chat/completions")
        payload = req.to_openai_dict()
        payload["stream"] = False
        try:
            async with self._client() as client:
                resp = await client.post(url, json=payload, headers=self._headers())
        except httpx.TimeoutException as e:
            raise TimeoutError(f"upstream timeout: {e}", provider=self.name, cause=e) from e
        except httpx.HTTPError as e:
            raise ServerError(f"network error: {e}", provider=self.name, cause=e) from e

        if resp.status_code != 200:
            raise _map_http_error(resp.status_code, resp.text, self.name)

        data = resp.json()
        return self._parse_non_stream(data, model=req.model)

    def _parse_non_stream(self, data: dict[str, Any], *, model: str) -> ChatCompletionResponse:
        choices: list[Choice] = []
        for c in data.get("choices", []) or []:
            msg = c.get("message") or {}
            choices.append(
                Choice(
                    index=int(c.get("index", 0)),
                    message=ChatMessage(
                        role=msg.get("role", "assistant"),
                        content=msg.get("content", ""),
                        name=msg.get("name"),
                        tool_calls=msg.get("tool_calls"),
                    ),
                    finish_reason=c.get("finish_reason") or "stop",
                )
            )
        usage_raw = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
            total_tokens=int(
                usage_raw.get("total_tokens", 0)
                or (int(usage_raw.get("prompt_tokens", 0) or 0)
                    + int(usage_raw.get("completion_tokens", 0) or 0))
            ),
        )
        return ChatCompletionResponse(
            id=data.get("id") or f"chatcmpl-{int(time.time())}",
            created=int(data.get("created", int(time.time()))),
            model=data.get("model") or model,
            choices=choices,
            usage=usage,
            provider=self.name,
        )

    # -- 流式 ----------------------------------------------------------------- #
    async def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        url = self._url("/chat/completions")
        payload = req.to_openai_dict()
        payload["stream"] = True
        # 部分兼容端点要求 stream_options 才回 usage
        payload.setdefault("stream_options", {"include_usage": True})

        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", url, json=payload, headers=self._headers()
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise _map_http_error(
                            resp.status_code, body.decode("utf-8", "ignore"), self.name
                        )
                    async for line in resp.aiter_lines():
                        chunk = self._parse_sse_line(line, fallback_model=req.model)
                        if chunk is not None:
                            yield chunk
        except httpx.TimeoutException as e:
            raise TimeoutError(f"upstream stream timeout: {e}", provider=self.name, cause=e) from e
        except httpx.HTTPError as e:
            raise ServerError(f"stream network error: {e}", provider=self.name, cause=e) from e

    def _parse_sse_line(self, line: str, *, fallback_model: str) -> StreamChunk | None:
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None
        data_str = line[len("data:"):].strip()
        if not data_str or data_str == "[DONE]":
            return None
        try:
            obj = json.loads(data_str)
        except json.JSONDecodeError:
            return None

        choices: list[StreamChoice] = []
        for c in obj.get("choices", []) or []:
            delta = c.get("delta") or {}
            choices.append(
                StreamChoice(
                    index=int(c.get("index", 0)),
                    delta=StreamChoiceDelta(
                        role=delta.get("role"),
                        content=delta.get("content"),
                    ),
                    finish_reason=c.get("finish_reason"),
                )
            )
        usage_raw = obj.get("usage")
        usage: Usage | None = None
        if usage_raw:
            usage = Usage(
                prompt_tokens=int(usage_raw.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage_raw.get("completion_tokens", 0) or 0),
                total_tokens=int(usage_raw.get("total_tokens", 0) or 0),
            )
        return StreamChunk(
            id=obj.get("id") or f"chatcmpl-{int(time.time())}",
            created=int(obj.get("created", time.time())),
            model=obj.get("model") or fallback_model,
            choices=choices,
            usage=usage,
            provider=self.name,
        )

    # -- 健康检查 ------------------------------------------------------------- #
    async def health_check(self) -> HealthStatus:
        url = self._url("/models")
        t0 = time.perf_counter()
        try:
            async with self._client() as client:
                resp = await client.get(url, headers=self._headers())
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                return HealthStatus(provider=self.name, ok=True, latency_ms=latency_ms)
            return HealthStatus(
                provider=self.name,
                ok=False,
                latency_ms=latency_ms,
                error=f"GET /models -> {resp.status_code}",
            )
        except Exception as e:  # noqa: BLE001 - 健康检查不能抛
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return HealthStatus(
                provider=self.name, ok=False, latency_ms=latency_ms, error=str(e)
            )
