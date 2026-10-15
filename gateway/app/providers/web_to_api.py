# -*- coding: utf-8 -*-
"""
网页转 API 适配器基类 (WebToApiProvider)。

适用场景
--------
有些免费服务只有网页端、没有公开 API (HuggingChat、aitoken.sbs、LibreClaude 等)。
本基类用 Playwright headless Chromium 模拟人工操作:
  打开网页 -> fill 输入框 -> 点发送 -> 等回复出现 -> 抓文本。

子类只需填 4 个选择器:
  url                  目标网页 URL
  input_selector       输入框 CSS selector
  send_button_selector 发送按钮 CSS selector (回车提交可留空)
  response_selector    回复区域 CSS selector (用于抓文本 + 流式增量)

可靠性
------
网页随时会改版, 选择器失效时抛 ProviderError("网页结构已变更"),
路由层 fallback 到下一个 provider, 不要 crash。

性能
----
比 API 直连慢 10~50 倍, 仅作零配置兜底。路由层应把 transport="web"
的 provider 排在 API 直连之后。

部署
----
self-hosted Windows 直接 ``playwright install chromium``。
Linux Docker 容器需要额外装系统依赖, 在 docker-compose.yml 里加注释:
  # web_to_api providers need: playwright install --with-deps chromium
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

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
from app.providers.base import BaseProvider, ProviderError, ServerError


class WebToApiProvider(BaseProvider):
    """网页转 API 基类。子类填选择器即可。"""

    provider_type = "web"

    # -- 子类必须覆盖 -------------------------------------------------------- #
    url: str = ""
    input_selector: str = ""
    send_button_selector: str = ""   # 留空 = 按 Enter 提交
    response_selector: str = ""
    # 等回复出现的最长时间 (秒)
    response_timeout_sec: float = 60.0
    # 流式增量观察: 多少毫秒没新文本就认为回复结束
    stream_idle_ms: int = 800

    # -- lazy playwright import --------------------------------------------- #
    @staticmethod
    def _require_playwright():
        try:
            from playwright.async_api import async_playwright  # noqa: WPS433
            return async_playwright
        except ImportError as e:
            raise ProviderError(
                "未安装 playwright; 请运行 `pip install playwright && playwright install chromium`",
                provider="web_to_api",
                cause=e,
            ) from e

    # -- 工具 ---------------------------------------------------------------- #
    def _last_user_text(self, req: ChatCompletionRequest) -> str:
        for m in reversed(req.messages):
            if m.role == "user":
                return m.content if isinstance(m.content, str) else str(m.content)
        return ""

    async def _run_web_turn(self, prompt: str, *, stream: bool) -> AsyncIterator[str]:
        """打开网页, 提交 prompt, yield 回复文本增量。失败抛 ProviderError。"""
        if not self.url or not self.input_selector or not self.response_selector:
            raise ProviderError(
                f"{self.name} 子类未填齐 url/input_selector/response_selector",
                provider=self.name,
            )

        async_playwright = self._require_playwright()
        try:
            pw = await async_playwright().start()
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"playwright 启动失败: {e}", provider=self.name, cause=e) from e

        browser = None
        try:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(self.url, wait_until="domcontentloaded", timeout=30000)

            # 填输入框
            try:
                await page.wait_for_selector(self.input_selector, timeout=15000)
                await page.fill(self.input_selector, prompt)
            except Exception as e:  # noqa: BLE001
                raise ProviderError(
                    f"网页结构已变更 (找不到输入框 {self.input_selector}): {e}",
                    provider=self.name, cause=e,
                ) from e

            # 提交: 点按钮 或 回车
            try:
                if self.send_button_selector:
                    await page.click(self.send_button_selector)
                else:
                    await page.press(self.input_selector, "Enter")
            except Exception as e:  # noqa: BLE001
                raise ProviderError(
                    f"网页结构已变更 (发送按钮不可点): {e}",
                    provider=self.name, cause=e,
                ) from e

            # 等回复区域出现
            try:
                await page.wait_for_selector(self.response_selector, timeout=self.response_timeout_sec * 1000)
            except Exception as e:  # noqa: BLE001
                raise ProviderError(
                    f"等待回复超时 ({self.response_timeout_sec}s): {e}",
                    provider=self.name, cause=e,
                ) from e

            # 流式: 轮询抓文本增量
            last_text = ""
            last_change = time.monotonic()
            idle_sec = self.stream_idle_ms / 1000.0
            while True:
                await asyncio.sleep(0.3)
                try:
                    current = await page.text_content(self.response_selector) or ""
                except Exception:  # noqa: BLE001
                    current = ""
                current = current.strip()
                if current and current != last_text:
                    delta = current[len(last_text):] if current.startswith(last_text) else current
                    if stream and delta:
                        yield delta
                    last_text = current
                    last_change = time.monotonic()
                else:
                    if time.monotonic() - last_change > idle_sec and last_text:
                        break
                    # 兜底: 防止页面死锁
                    if time.monotonic() - last_change > self.response_timeout_sec:
                        break

            if not stream:
                yield last_text
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ServerError(f"网页自动化异常: {e}", provider=self.name, cause=e) from e
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                await pw.stop()
            except Exception:  # noqa: BLE001
                pass

    # -- 主接口 --------------------------------------------------------------- #
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        prompt = self._last_user_text(req)
        if not prompt:
            raise ProviderError("网页转 API 需要至少一条 user 消息", provider=self.name)

        collected = ""
        async for delta in self._run_web_turn(prompt, stream=False):
            collected += delta

        if not collected:
            raise ProviderError("网页转 API 未拿到回复文本", provider=self.name)

        return ChatCompletionResponse(
            id=f"web-{int(time.time())}",
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
                prompt_tokens=len(prompt.split()),
                completion_tokens=len(collected.split()),
                total_tokens=len(prompt.split()) + len(collected.split()),
            ),
            provider=self.name,
        )

    async def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        prompt = self._last_user_text(req)
        if not prompt:
            raise ProviderError("网页转 API 需要至少一条 user 消息", provider=self.name)

        chunk_id = f"web-{int(time.time())}"
        created = int(time.time())
        received = False
        async for delta in self._run_web_turn(prompt, stream=True):
            received = True
            yield StreamChunk(
                id=chunk_id,
                created=created,
                model=req.model,
                choices=[
                    StreamChoice(
                        index=0,
                        delta=StreamChoiceDelta(role="assistant", content=delta),
                    )
                ],
                provider=self.name,
            )

        if not received:
            raise ProviderError("网页转 API 未拿到回复文本", provider=self.name)

        yield StreamChunk(
            id=chunk_id,
            created=created,
            model=req.model,
            choices=[StreamChoice(index=0, delta=StreamChoiceDelta(), finish_reason="stop")],
            provider=self.name,
        )

    # -- 健康检查 ------------------------------------------------------------- #
    async def health_check(self) -> HealthStatus:
        if not self.url:
            return HealthStatus(provider=self.name, ok=False, error="未配置 url")
        t0 = time.perf_counter()
        try:
            async_playwright = self._require_playwright()
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(self.url, wait_until="domcontentloaded", timeout=15000)
            has_input = await page.query_selector(self.input_selector) is not None
            await browser.close()
            await pw.stop()
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if has_input:
                return HealthStatus(provider=self.name, ok=True, latency_ms=latency_ms)
            return HealthStatus(
                provider=self.name, ok=False, latency_ms=latency_ms,
                error=f"页面打开但找不到输入框 {self.input_selector}",
            )
        except Exception as e:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return HealthStatus(
                provider=self.name, ok=False, latency_ms=latency_ms, error=str(e)
            )


# --------------------------------------------------------------------------- #
# 具体子类: HuggingChat 网页版 (Playwright 兜底)
# --------------------------------------------------------------------------- #
class HuggingChatWebProvider(WebToApiProvider):
    """用 Playwright 自动化 huggingface.co/chat 的网页端。

    当 HuggingChatProvider (web API) 被 Cloudflare 拦截时, 这个作为兜底。
    注意: 选择器随 HF 前端版本变化, 失效时会抛 "网页结构已变更"。
    """

    provider_type = "web_huggingchat"
    url = "https://huggingface.co/chat"
    input_selector = "textarea[data-testid='chat-input']"
    send_button_selector = "button[aria-label='Send message']"
    response_selector = ".prose.dark\\:prose-invert"
    response_timeout_sec = 90.0
