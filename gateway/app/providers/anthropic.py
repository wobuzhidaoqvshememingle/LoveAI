# -*- coding: utf-8 -*-
"""
Anthropic Claude 适配器。

协议差异 (不能直接复用 OpenAICompatibleProvider):
  - 端点: ``POST {base_url}/v1/messages`` (不是 /chat/completions)
  - 鉴权头: ``x-api-key: <key>`` + ``anthropic-version: 2023-06-01``
  - system prompt 是请求体顶层字段, 不是 messages 里的一条
  - 响应: ``content: [{type:text, text:...}]`` 数组
  - usage 字段名: input_tokens / output_tokens

支持模型: claude-opus-4 / claude-sonnet-4 / claude-3-5-sonnet / claude-3-5-haiku 等。
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

ANTHROPIC_VERSION = "2023-06-01"

# Anthropic stop_reason -> OpenAI finish_reason
_STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
}


def _map_http_error(status_code: int, body: str, provider: str) -> ProviderError:
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


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API 适配器。"""

    provider_type = "anthropic"

    def _url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/v1/messages"

    def _headers(self) -> dict[str, str]:
        api_key = self.config.resolved_api_key()
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(self.config.timeout_sec))

    # -- 请求体转换 ----------------------------------------------------------- #
    def _build_payload(self, req: ChatCompletionRequest, *, stream: bool) -> dict[str, Any]:
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for m in req.messages:
            if m.role == "system":
                if isinstance(m.content, str):
                    system_parts.append(m.content)
                elif isinstance(m.content, list):
                    # 多模态 system: 只取 text 段
                    for part in m.content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            system_parts.append(part.get("text", ""))
                continue
            role = "assistant" if m.role == "assistant" else "user"
            content = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            messages.append({"role": role, "content": content})

        payload: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens or req.max_completion_tokens or 1024,
            "messages": messages,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n\n".join(p for p in system_parts if p)
        if req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.top_p is not None:
            payload["top_p"] = req.top_p
        if req.stop:
            payload["stop_sequences"] = req.stop if isinstance(req.stop, list) else [req.stop]
        return payload

    # -- 非流式 --------------------------------------------------------------- #
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        payload = self._build_payload(req, stream=False)
        try:
            async with self._client() as client:
                resp = await client.post(self._url(), json=payload, headers=self._headers())
        except httpx.TimeoutException as e:
            raise TimeoutError(f"upstream timeout: {e}", provider=self.name, cause=e) from e
        except httpx.HTTPError as e:
            raise ServerError(f"network error: {e}", provider=self.name, cause=e) from e

        if resp.status_code != 200:
            raise _map_http_error(resp.status_code, resp.text, self.name)

        data = resp.json()
        text = "".join(
            blk.get("text", "") for blk in data.get("content", []) if blk.get("type") == "text"
        )
        usage_raw = data.get("usage", {}) or {}
        input_tokens = int(usage_raw.get("input_tokens", 0) or 0)
        output_tokens = int(usage_raw.get("output_tokens", 0) or 0)
        finish = _STOP_REASON_MAP.get(data.get("stop_reason", ""), "stop")
        return ChatCompletionResponse(
            id=data.get("id") or f"msg_{int(time.time())}",
            created=int(time.time()),
            model=data.get("model") or req.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=text),
                    finish_reason=finish,
                )
            ],
            usage=Usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
            provider=self.name,
        )

    # -- 流式 ----------------------------------------------------------------- #
    async def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(req, stream=True)
        chunk_id = f"msg_{int(time.time())}"
        created = int(time.time())
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None

        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", self._url(), json=payload, headers=self._headers()
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise _map_http_error(
                            resp.status_code, body.decode("utf-8", "ignore"), self.name
                        )
                    async for line in resp.aiter_lines():
                        event = self._parse_sse_line(
                            line,
                            chunk_id=chunk_id,
                            created=created,
                            model=req.model,
                        )
                        if event is None:
                            continue
                        kind, chunk, meta = event
                        if kind == "usage_in":
                            input_tokens = meta
                        elif kind == "usage_out":
                            output_tokens = meta
                        elif kind == "stop":
                            finish_reason = meta
                        elif kind == "chunk":
                            yield chunk
        except httpx.TimeoutException as e:
            raise TimeoutError(f"upstream stream timeout: {e}", provider=self.name, cause=e) from e
        except httpx.HTTPError as e:
            raise ServerError(f"stream network error: {e}", provider=self.name, cause=e) from e

        # 收尾: 把 finish_reason 和累计 usage 作为最后一块发出去
        yield StreamChunk(
            id=chunk_id,
            created=created,
            model=req.model,
            choices=[
                StreamChoice(
                    index=0,
                    delta=StreamChoiceDelta(),
                    finish_reason=finish_reason or "stop",
                )
            ],
            usage=Usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
            provider=self.name,
        )

    def _parse_sse_line(
        self, line: str, *, chunk_id: str, created: int, model: str
    ) -> tuple[str, StreamChunk | None, Any] | None:
        """返回 (kind, chunk_or_None, meta)。

        kind: "chunk" | "usage_in" | "usage_out" | "stop"
        """
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None
        data_str = line[len("data:"):].strip()
        if not data_str:
            return None
        try:
            obj = json.loads(data_str)
        except json.JSONDecodeError:
            return None

        etype = obj.get("type")
        if etype == "message_start":
            usage = (obj.get("message") or {}).get("usage") or {}
            return ("usage_in", None, int(usage.get("input_tokens", 0) or 0))
        if etype == "content_block_delta":
            delta = obj.get("delta") or {}
            if delta.get("type") == "text_delta":
                text = delta.get("text", "")
                return (
                    "chunk",
                    StreamChunk(
                        id=chunk_id,
                        created=created,
                        model=model,
                        choices=[
                            StreamChoice(
                                index=0,
                                delta=StreamChoiceDelta(role="assistant", content=text),
                            )
                        ],
                        provider=self.name,
                    ),
                    None,
                )
            return None
        if etype == "message_delta":
            delta = obj.get("delta") or {}
            usage = obj.get("usage") or {}
            stop = delta.get("stop_reason")
            out_tokens = int(usage.get("output_tokens", 0) or 0)
            if out_tokens:
                return ("usage_out", None, out_tokens)
            if stop:
                return ("stop", None, _STOP_REASON_MAP.get(stop, "stop"))
            return None
        if etype == "message_stop":
            return ("stop", None, "stop")
        return None

    # -- 健康检查 ------------------------------------------------------------- #
    async def health_check(self) -> HealthStatus:
        url = f"{self.config.base_url.rstrip('/')}/v1/models"
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
                error=f"GET /v1/models -> {resp.status_code}",
            )
        except Exception as e:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return HealthStatus(
                provider=self.name, ok=False, latency_ms=latency_ms, error=str(e)
            )
