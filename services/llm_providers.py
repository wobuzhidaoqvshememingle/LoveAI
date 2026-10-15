# -*- coding: utf-8 -*-
"""
LoveAI-v2 对话 LLM Provider 抽象
=================================
把「真 LLM」从桥接脚本里解耦，按优先级自动选一条能用的路：

优先级（可被环境变量覆盖）：
  1. local_llama  —— llama-cpp-python 直接加载本地 GGUF（完全离线，不依赖云端）
  2. ollama       —— 本机 Ollama 服务（http://127.0.0.1:11434/v1，OpenAI 兼容）
  3. cloud        —— OPENAI_API_KEY 配置的任意 OpenAI 兼容云接口
  4. template     —— 以上都不可用时的兜底模板回复

为什么不硬接 LoveAI 主程序的策略引擎：桥接是独立进程，主策略引擎跑在 Qt 里、
重依赖难无头拉起；这里用同一套 OpenAI 兼容消息协议（messages 数组），
日后要把 provider 换成主策略引擎，只要新写一个实现 chat() 的类即可。

环境变量：
- LOVEAI_LLM_BACKEND   auto|local_llama|ollama|cloud|template
- LOVEAI_GGUF_PATH     默认 models_local/qwen2.5-1.5b-instruct-q4_k_m.gguf
- OLLAMA_HOST          默认 http://127.0.0.1:11434
- OLLAMA_MODEL         默认 loveai-qwen （由桥接用 Modelfile 从 GGUF 建）
- OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL  云接口
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("LoveAI.llm_provider")

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_GGUF = ROOT / "models_local" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"


class LLMProvider:
    """统一对话接口。chat(messages) 异步返回纯文本回复。"""

    name = "base"

    async def chat(self, messages: list[dict]) -> str:  # pragma: no cover
        raise NotImplementedError

    def available(self) -> bool:
        return True


# --------------------------------------------------------------------------- #
# 1) llama-cpp-python 本地 GGUF（纯离线）
# --------------------------------------------------------------------------- #
class LocalLlamaProvider(LLMProvider):
    name = "local_llama"

    def __init__(self, gguf_path: Optional[str] = None) -> None:
        self.gguf_path = Path(gguf_path or os.environ.get("LOVEAI_GGUF_PATH", str(DEFAULT_GGUF)))
        self._llm = None
        self._tried = False
        self._err: Optional[Exception] = None

    def available(self) -> bool:
        if not self.gguf_path.exists():
            logger.info("local_llama: GGUF 不存在 %s", self.gguf_path)
            return False
        try:
            import llama_cpp  # noqa: F401
            return True
        except Exception as e:  # noqa: BLE001
            logger.info("local_llama: llama-cpp-python 未安装: %s", e)
            return False

    def _ensure(self):
        if self._llm is not None:
            return self._llm
        if self._tried:
            return None
        self._tried = True
        try:
            from llama_cpp import Llama
            logger.info("加载本地 GGUF: %s", self.gguf_path)
            self._llm = Llama(
                model_path=str(self.gguf_path),
                n_ctx=2048,
                n_threads=max(2, (os.cpu_count() or 4) // 2),
                verbose=False,
            )
            logger.info("本地 GGUF 加载完成")
        except Exception as e:  # noqa: BLE001
            self._err = e
            logger.warning("本地 GGUF 加载失败: %s", e)
            self._llm = None
        return self._llm

    def _chat_sync(self, messages: list[dict]) -> str:
        llm = self._ensure()
        if llm is None:
            return ""
        resp = llm.create_chat_completion(
            messages=messages,
            max_tokens=120,
            temperature=0.8,
        )
        return (resp["choices"][0]["message"].get("content") or "").strip()

    async def chat(self, messages: list[dict]) -> str:
        return await asyncio.to_thread(self._chat_sync, messages)


# --------------------------------------------------------------------------- #
# 2) Ollama 本地服务（OpenAI 兼容）
# --------------------------------------------------------------------------- #
class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, host: Optional[str] = None, model: Optional[str] = None) -> None:
        self.host = host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        self.model = model or os.environ.get("OLLAMA_MODEL", "loveai-qwen")
        self._client = None

    def available(self) -> bool:
        import urllib.request
        try:
            urllib.request.urlopen(self.host + "/api/tags", timeout=1.5)
            return True
        except Exception:
            return False

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from openai import OpenAI
        self._client = OpenAI(base_url=self.host + "/v1", api_key="ollama")
        return self._client

    async def chat(self, messages: list[dict]) -> str:
        def _call() -> str:
            client = self._ensure_client()
            resp = client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=120, temperature=0.8,
            )
            return (resp.choices[0].message.content or "").strip()
        return await asyncio.to_thread(_call, messages)


# --------------------------------------------------------------------------- #
# 3) 云 OpenAI 兼容
# --------------------------------------------------------------------------- #
class CloudOpenAIProvider(LLMProvider):
    name = "cloud"

    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
        self._client = None

    def available(self) -> bool:
        return bool(self.api_key)

    async def chat(self, messages: list[dict]) -> str:
        from openai import AsyncOpenAI
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
        resp = await self._client.chat.completions.create(
            model=self.model, messages=messages, max_tokens=120, temperature=0.8,
        )
        return (resp.choices[0].message.content or "").strip()


# --------------------------------------------------------------------------- #
# 4) 模板兜底
# --------------------------------------------------------------------------- #
class TemplateProvider(LLMProvider):
    name = "template"

    async def chat(self, messages: list[dict]) -> str:
        user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user = m.get("content", ""); break
        return f"老公，我听到你说「{user}」啦～今天也要抱抱哦～"


# --------------------------------------------------------------------------- #
# 工厂：按优先级挑一个能用的
# --------------------------------------------------------------------------- #
def build_provider(prefer: Optional[str] = None) -> LLMProvider:
    prefer = (prefer or os.environ.get("LOVEAI_LLM_BACKEND", "auto")).strip().lower()
    candidates: list[LLMProvider]
    if prefer == "local_llama":
        candidates = [LocalLlamaProvider(), TemplateProvider()]
    elif prefer == "ollama":
        candidates = [OllamaProvider(), TemplateProvider()]
    elif prefer == "cloud":
        candidates = [CloudOpenAIProvider(), TemplateProvider()]
    elif prefer == "template":
        candidates = [TemplateProvider()]
    else:  # auto
        candidates = [
            LocalLlamaProvider(),
            OllamaProvider(),
            CloudOpenAIProvider(),
            TemplateProvider(),
        ]
    for p in candidates:
        try:
            if p.available():
                logger.info("选中 LLM provider: %s", p.name)
                return p
        except Exception as e:  # noqa: BLE001
            logger.warning("provider %s 探测异常: %s", p.name, e)
    return TemplateProvider()
