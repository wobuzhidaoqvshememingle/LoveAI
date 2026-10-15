# -*- coding: utf-8 -*-
"""
HuggingChat (huggingface.co/chat) 网页端匿名会话适配器 (best-effort)。

不依赖 API Key, 直接打 HuggingChat 的 web 内部 API:
  1. POST {base_url}/chat/conversation          -> 创建会话, 返回 conversationId
  2. POST {base_url}/chat/conversation/{id}     -> 发消息, SSE 流式返回

**重要**: 这是网页内部 API, 非官方公开接口, HF 随时可能改字段/加 Cloudflare 校验。
失败时抛 ProviderError, 路由层应 fallback 到下一个 provider。
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
from app.providers.base import ProviderError, ServerError, TimeoutError, BaseProvider


class HuggingChatProvider(BaseProvider):
    """HuggingChat 匿名网页会话适配器。"""

    provider_type = "huggingchat"

    def _base(self) -> str:
        return (self.config.base_url or "https://huggingface.co").rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout_sec),
            headers={
                "User-Agent": "LoveAI-Gateway/1.0 (huggingchat best-effort)",
                "Accept": "text/event-stream, application/json",
            },
        )

    @staticmethod
    def _history(req: ChatCompletionRequest) -> list[dict[str, str]]:
        history = []
        for m in req.messages:
            if m.role == "system":
                continue
            if not isinstance(m.content, str):
                continue
            history.append({"role": m.role, "content": m.content})
        return history

    # -- 非流式 --------------------------------------------------------------- #
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        collected = ""
        async for chunk in self.stream_chat_completion(req):
            if chunk.choices and chunk.choices[0].delta.content:
                collected += chunk.choices[0].delta.content
        if not collected:
            raise ProviderError(
                "HuggingChat 匿名会话未返回内容, 可能被 Cloudflare 拦截, 请 fallback",
                provider=self.name,
            )
        return ChatCompletionResponse(
            id=f"hugchat-{int(time.time())}",
            created=int(time.time()),
            model=req.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=collected),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=sum(len(m.content.split()) for m in req.messages if isinstance(m.content, str)),
                completion_tokens=len(collected.split()),
                total_tokens=0,
            ),
            provider=self.name,
        )

    # -- 流式 ----------------------------------------------------------------- #
    async def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        base = self._base()
        model = req.model or "meta-llama/Llama-3.3-70B-Instruct"

        try:
            async with self._client() as client:
                # 1. 创建会话
                conv_resp = await client.post(
                    f"{base}/chat/conversation",
                    json={"model": model},
                )
                if conv_resp.status_code not in (200, 201):
                    raise ProviderError(
                        f"HuggingChat 创建会话失败: HTTP {conv_resp.status_code}",
                        provider=self.name,
                    )
                conv_id = (conv_resp.json() or {}).get("conversationId")
                if not conv_id:
                    raise ProviderError("HuggingChat 响应里没有 conversationId", provider=self.name)

                # 2. 发消息, SSE
                payload = {
                    "model": model,
                    "inputs": req.messages[-1].content if req.messages else "",
                    "history": self._history(req)[:-1],
                    "stream": True,
                }
                chunk_id = f"hugchat-{int(time.time())}"
                created = int(time.time())
                async with client.stream(
                    "POST",
                    f"{base}/chat/conversation/{conv_id}",
                    json=payload,
                ) as resp:
                    if resp.status_code != 200:
                        raise ProviderError(
                            f"HuggingChat 发消息失败: HTTP {resp.status_code}",
                            provider=self.name,
                        )
                    async for line in resp.aiter_lines():
                        text = self._parse_sse_line(line)
                        if text:
                            yield StreamChunk(
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
                            )
        except httpx.TimeoutError as e:
            raise TimeoutError(f"HuggingChat 超时: {e}", provider=self.name, cause=e) from e
        except ProviderError:
            raise
        except httpx.HTTPError as e:
            raise ServerError(f"HuggingChat 网络错误: {e}", provider=self.name, cause=e) from e

    @staticmethod
    def _parse_sse_line(line: str) -> str:
        """HuggingChat SSE: data: {token: "..."} 或 data: {generated_text: "..."}。"""
        line = line.strip()
        if not line or not line.startswith("data:"):
            return ""
        data_str = line[len("data:"):].strip()
        if not data_str or data_str == "[DONE]":
            return ""
        try:
            obj = json.loads(data_str)
        except json.JSONDecodeError:
            return ""
        if isinstance(obj, dict):
            if obj.get("type") == "final":
                return ""
            return obj.get("token") or obj.get("generated_text") or ""
        return ""

    # -- 健康检查 ------------------------------------------------------------- #
    async def health_check(self) -> HealthStatus:
        t0 = time.perf_counter()
        try:
            async with self._client() as client:
                resp = await client.get(f"{self._base()}/chat")
            latency_ms = (time.perf_counter() - t0) * 1000.0
            ok = resp.status_code == 200
            return HealthStatus(
                provider=self.name,
                ok=ok,
                latency_ms=latency_ms,
                error=None if ok else f"GET /chat -> {resp.status_code}",
            )
        except Exception as e:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return HealthStatus(
                provider=self.name, ok=False, latency_ms=latency_ms, error=str(e)
            )
