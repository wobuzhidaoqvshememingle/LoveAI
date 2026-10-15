# -*- coding: utf-8 -*-
"""
Google Gemini (Generative Language API) 适配器。

协议差异 (不能复用 OpenAICompatibleProvider):
  - 端点: ``POST {base_url}/v1beta/models/{model}:generateContent?key={api_key}``
  - 流式: ``...:streamGenerateContent?alt=sse&key={api_key}``
  - 请求体: ``contents: [{role: user|model, parts:[{text}]}]`` +
    顶层 ``system_instruction: {parts:[{text}]}``
  - 响应: ``candidates[0].content.parts[0].text``,
    usage 在 ``usageMetadata.promptTokenCount / candidatesTokenCount``

支持模型: gemini-2.0-flash / gemini-2.5-pro / gemini-1.5-pro / gemini-1.5-flash 等。
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

# Gemini finishReason -> OpenAI finish_reason
_FINISH_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "OTHER": "stop",
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


class GeminiProvider(BaseProvider):
    """Google Generative Language API 适配器。"""

    provider_type = "gemini"

    def _base(self) -> str:
        return self.config.base_url.rstrip("/")

    def _api_key(self) -> str:
        return self.config.resolved_api_key() or ""

    def _url(self, model: str, *, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        return (
            f"{self._base()}/v1beta/models/{model}:{action}"
            f"?key={self._api_key()}"
        )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(self.config.timeout_sec))

    # -- 请求体转换 ----------------------------------------------------------- #
    def _build_payload(self, req: ChatCompletionRequest) -> dict[str, Any]:
        system_parts: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []
        for m in req.messages:
            if m.role == "system":
                text = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
                system_parts.append({"text": text})
                continue
            role = "model" if m.role == "assistant" else "user"
            if isinstance(m.content, str):
                parts = [{"text": m.content}]
            elif isinstance(m.content, list):
                parts = []
                for part in m.content:
                    if isinstance(part, dict):
                        if "text" in part:
                            parts.append({"text": part["text"]})
                        elif part.get("type") == "text":
                            parts.append({"text": part.get("text", "")})
            else:
                parts = [{"text": str(m.content)}]
            contents.append({"role": role, "parts": parts})

        payload: dict[str, Any] = {"contents": contents}
        if system_parts:
            payload["system_instruction"] = {"parts": system_parts}
        generation_config: dict[str, Any] = {}
        if req.temperature is not None:
            generation_config["temperature"] = req.temperature
        if req.top_p is not None:
            generation_config["topP"] = req.top_p
        if req.max_tokens or req.max_completion_tokens:
            generation_config["maxOutputTokens"] = req.max_tokens or req.max_completion_tokens
        if req.stop:
            generation_config["stopSequences"] = req.stop if isinstance(req.stop, list) else [req.stop]
        if generation_config:
            payload["generationConfig"] = generation_config
        return payload

    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))

    @staticmethod
    def _extract_finish(data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return "stop"
        reason = candidates[0].get("finishReason", "STOP")
        return _FINISH_MAP.get(reason, "stop")

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> Usage:
        um = data.get("usageMetadata") or {}
        prompt_tokens = int(um.get("promptTokenCount", 0) or 0)
        completion_tokens = int(um.get("candidatesTokenCount", 0) or 0)
        total_tokens = int(um.get("totalTokenCount", 0) or (prompt_tokens + completion_tokens))
        return Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    # -- 非流式 --------------------------------------------------------------- #
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        payload = self._build_payload(req)
        try:
            async with self._client() as client:
                resp = await client.post(
                    self._url(req.model, stream=False),
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.TimeoutException as e:
            raise TimeoutError(f"upstream timeout: {e}", provider=self.name, cause=e) from e
        except httpx.HTTPError as e:
            raise ServerError(f"network error: {e}", provider=self.name, cause=e) from e

        if resp.status_code != 200:
            raise _map_http_error(resp.status_code, resp.text, self.name)

        data = resp.json()
        text = self._extract_text(data)
        return ChatCompletionResponse(
            id=f"gemini-{int(time.time())}",
            created=int(time.time()),
            model=req.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=text),
                    finish_reason=self._extract_finish(data),
                )
            ],
            usage=self._extract_usage(data),
            provider=self.name,
        )

    # -- 流式 ----------------------------------------------------------------- #
    async def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(req)
        chunk_id = f"gemini-{int(time.time())}"
        created = int(time.time())
        usage = Usage()
        finish_reason = "stop"

        try:
            async with self._client() as client:
                async with client.stream(
                    "POST",
                    self._url(req.model, stream=True),
                    json=payload,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise _map_http_error(
                            resp.status_code, body.decode("utf-8", "ignore"), self.name
                        )
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if not data_str or data_str == "[DONE]":
                            continue
                        try:
                            obj = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        text = self._extract_text(obj)
                        if text:
                            yield StreamChunk(
                                id=chunk_id,
                                created=created,
                                model=req.model,
                                choices=[
                                    StreamChoice(
                                        index=0,
                                        delta=StreamChoiceDelta(role="assistant", content=text),
                                    )
                                ],
                                provider=self.name,
                            )
                        if obj.get("candidates"):
                            finish_reason = self._extract_finish(obj)
                        u = self._extract_usage(obj)
                        if u.prompt_tokens or u.completion_tokens:
                            usage = u
        except httpx.TimeoutException as e:
            raise TimeoutError(f"upstream stream timeout: {e}", provider=self.name, cause=e) from e
        except httpx.HTTPError as e:
            raise ServerError(f"stream network error: {e}", provider=self.name, cause=e) from e

        yield StreamChunk(
            id=chunk_id,
            created=created,
            model=req.model,
            choices=[
                StreamChoice(
                    index=0,
                    delta=StreamChoiceDelta(),
                    finish_reason=finish_reason,
                )
            ],
            usage=usage,
            provider=self.name,
        )

    # -- 健康检查 ------------------------------------------------------------- #
    async def health_check(self) -> HealthStatus:
        url = f"{self._base()}/v1beta/models?key={self._api_key()}"
        t0 = time.perf_counter()
        try:
            async with self._client() as client:
                resp = await client.get(url)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                return HealthStatus(provider=self.name, ok=True, latency_ms=latency_ms)
            return HealthStatus(
                provider=self.name,
                ok=False,
                latency_ms=latency_ms,
                error=f"GET /v1beta/models -> {resp.status_code}",
            )
        except Exception as e:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return HealthStatus(
                provider=self.name, ok=False, latency_ms=latency_ms, error=str(e)
            )
