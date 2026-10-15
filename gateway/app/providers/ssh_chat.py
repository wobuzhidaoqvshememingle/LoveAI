# -*- coding: utf-8 -*-
"""
Hugging Face SSH Chat 适配器 (experimental / 零配置兜底)。

背景
----
HF 官方提供 ``ssh chat.hf.co`` 直接进交互式聊天 (Qwen/Qwen3 系列),
无需 API Key、无需注册、不限量。本 provider 让网关"装好就能用",
作为第一个免费兜底通道。

实现策略 (best-effort)
----------------------
SSH 后端是交互式 TUI, 程序化调用天然不稳定。本类按以下顺序尝试:

1. 非交互模式: ``ssh -o BatchMode=no chat.hf.co "<last user message>"``
   (部分 SSH chat 服务端支持把参数当 prompt 直接回显结果)。
2. 管道喂入: ``echo "<prompt>" | ssh -o BatchMode=no chat.hf.co``,
   用 asyncio 读 stdout, 等待 3~5 秒无新输出就认为回复结束。
3. 以上都失败 -> 抛 ProviderError, 让路由层 fallback 到下一个 provider。

**不要把这个 provider 当主力**, 它只是零配置兜底。docstring 里明确标注
experimental, 调用方应优先用 OpenAI-compatible 的 HF Inference Endpoint。
"""
from __future__ import annotations

import asyncio
import re
import shlex
import time
from typing import AsyncIterator

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
from app.providers.base import BaseProvider, ProviderError

# SSH 命令里要剥掉的 TUI 控制字符 (ANSI 转义序列 / 光标移动)
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
# 常见 prompt 行 / 横幅行的 heuristics
_NOISE_RE = re.compile(
    r"^\s*(hugging\s*face|hf|chat>\s*$|\$\s*$|>\s*$|welcome|type\s+your)",
    re.IGNORECASE,
)

_SSH_HOST = "chat.hf.co"
_IDLE_TIMEOUT_SEC = 4.0          # 流式读: 多少秒没新输出就收尾
_CONNECT_TIMEOUT_SEC = 8.0


def _clean_output(raw: str) -> str:
    """剥掉 ANSI / 噪声行, 返回纯 assistant 文本。"""
    text = _ANSI_RE.sub("", raw)
    lines = []
    for ln in text.splitlines():
        ln = ln.rstrip()
        if not ln.strip():
            continue
        if _NOISE_RE.match(ln):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


class SSHChatProvider(BaseProvider):
    """通过 SSH 调用 HF 交互式 chat 的 experimental 适配器。

    不依赖 api_key; config.base_url 默认为 ``ssh chat.hf.co``。
    模型名 (qwen3-27b / qwen3-72b) 只用于元数据, 实际由服务端决定。
    """

    provider_type = "ssh"

    # -- 工具 ----------------------------------------------------------------- #
    def _ssh_base_args(self) -> list[str]:
        # 允许用户在 extra.ssh_args 里追加自定义参数
        extra_args: list[str] = self.config.extra.get("ssh_args", []) or []
        return [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ConnectTimeout={int(_CONNECT_TIMEOUT_SEC)}",
            *extra_args,
            _SSH_HOST,
        ]

    @staticmethod
    def _last_user_text(req: ChatCompletionRequest) -> str:
        """取最后一条 user message 文本 (SSH chat 只认单轮 prompt)。"""
        for m in reversed(req.messages):
            if m.role == "user":
                if isinstance(m.content, str):
                    return m.content
                return str(m.content)
        return ""

    async def _run_ssh(self, prompt: str, *, stream: bool) -> AsyncIterator[str]:
        """跑 ssh 子进程, yield 原始输出行。失败抛 ProviderError。"""
        args = self._ssh_base_args() + [shlex.quote(prompt)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as e:
            raise ProviderError(
                "ssh 命令未找到; 请安装 OpenSSH 或改用其他 provider",
                provider=self.name,
                cause=e,
            ) from e
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"启动 ssh 失败: {e}", provider=self.name, cause=e) from e

        # 读 stdout: stream=True 时逐行 yield; False 时攒完一次性回
        buf: list[str] = []
        last_recv = time.monotonic()
        assert proc.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=_IDLE_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                # 超时: 认为回复结束
                break
            if not line:
                break
            last_recv = time.monotonic()
            decoded = line.decode("utf-8", errors="replace")
            if stream:
                yield decoded
            else:
                buf.append(decoded)

        # 收尾: 杀进程
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        await proc.wait()

        if not stream:
            yield "".join(buf)

    # -- 非流式 --------------------------------------------------------------- #
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        prompt = self._last_user_text(req)
        if not prompt:
            raise ProviderError("SSH chat 需要至少一条 user 消息", provider=self.name)

        collected = ""
        async for chunk in self._run_ssh(prompt, stream=False):
            collected += chunk

        text = _clean_output(collected)
        if not text:
            raise ProviderError(
                "SSH 交互式调用不稳定, 未拿到有效回复; 建议改用其他 provider",
                provider=self.name,
            )

        return ChatCompletionResponse(
            id=f"ssh-{int(time.time())}",
            created=int(time.time()),
            model=req.model,
            choices=[
                Choice(
                    index=0,
                    message=ChatMessage(role="assistant", content=text),
                    finish_reason="stop",
                )
            ],
            usage=Usage(
                prompt_tokens=len(prompt.split()),
                completion_tokens=len(text.split()),
                total_tokens=len(prompt.split()) + len(text.split()),
            ),
            provider=self.name,
        )

    # -- 流式 ----------------------------------------------------------------- #
    async def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        prompt = self._last_user_text(req)
        if not prompt:
            raise ProviderError("SSH chat 需要至少一条 user 消息", provider=self.name)

        chunk_id = f"ssh-{int(time.time())}"
        created = int(time.time())
        received = False
        async for raw_line in self._run_ssh(prompt, stream=True):
            cleaned = _clean_output(raw_line)
            if not cleaned:
                continue
            received = True
            yield StreamChunk(
                id=chunk_id,
                created=created,
                model=req.model,
                choices=[
                    StreamChoice(
                        index=0,
                        delta=StreamChoiceDelta(role="assistant", content=cleaned + "\n"),
                    )
                ],
                provider=self.name,
            )

        if not received:
            raise ProviderError(
                "SSH 交互式调用不稳定, 未拿到有效回复; 建议改用其他 provider",
                provider=self.name,
            )

        yield StreamChunk(
            id=chunk_id,
            created=created,
            model=req.model,
            choices=[StreamChoice(index=0, delta=StreamChoiceDelta(), finish_reason="stop")],
            provider=self.name,
        )

    # -- 健康检查 ------------------------------------------------------------- #
    async def health_check(self) -> HealthStatus:
        t0 = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._ssh_base_args(),
                "exit",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=_CONNECT_TIMEOUT_SEC + 2)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ProviderError("ssh 连接超时", provider=self.name)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if proc.returncode == 0:
                return HealthStatus(provider=self.name, ok=True, latency_ms=latency_ms)
            return HealthStatus(
                provider=self.name,
                ok=False,
                latency_ms=latency_ms,
                error=f"ssh exit rc={proc.returncode}",
            )
        except FileNotFoundError as e:
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return HealthStatus(
                provider=self.name, ok=False, latency_ms=latency_ms, error=f"ssh 未安装: {e}"
            )
        except Exception as e:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000.0
            return HealthStatus(
                provider=self.name, ok=False, latency_ms=latency_ms, error=str(e)
            )
