# -*- coding: utf-8 -*-
"""
Key 自动识别 — 用户只贴一个 Key, 网关按前缀/格式猜出厂商。

设计:
  1. 前缀精确匹配 (最高优先级):
       Anthropic  sk-ant-...
       Gemini     AIza...
       Groq       gsk_...
       xAI        xai-...
       OpenRouter sk-or-v1-...
       智谱       <32 hex>.<8+ chars>  (JWT 段)
       DeepSeek   sk-... (模糊, 走探测)
       Moonshot   sk-... (模糊, 走探测)
  2. 模糊 sk- 开头: 同时试 OpenAI / DeepSeek / OpenRouter / 硅基流动 / 通义 /
     Moonshot / 火山方舟 等常见 base_url, 调 GET /models 看哪个返回 200,
     命中即定。结果由调用方缓存。
  3. 识别出厂商后, 自动构造 ProviderConfig (build_provider_config) 供
     engine.register_provider + cfg.providers.append 使用, 用户不用手填 yaml。

注意: 本模块只做"识别", 不落库 / 不发请求 (探测由 key_router.py /
admin.py 用 async httpx 完成)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.config import ProviderConfig


@dataclass
class KeyFingerprint:
    """一条 Key 指纹规则。"""
    provider_type: str          # 对应 ProviderConfig.type
    provider_name: str          # 默认 provider name
    pattern: re.Pattern
    default_base_url: str
    note: str = ""
    default_models: list[str] = field(default_factory=list)


# 已知厂商 Key 前缀 (按特异性排序, 先匹配先赢)
# base_url 统一带版本后缀, OpenAICompatibleProvider 只在后面拼 /chat/completions。
FINGERPRINTS: list[KeyFingerprint] = [
    KeyFingerprint(
        provider_type="anthropic",
        provider_name="anthropic",
        pattern=re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}"),
        default_base_url="https://api.anthropic.com",
        note="Anthropic Claude",
        default_models=["claude-sonnet-4-5", "claude-3-5-haiku-20241022"],
    ),
    KeyFingerprint(
        provider_type="gemini",
        provider_name="gemini",
        pattern=re.compile(r"^AIza[A-Za-z0-9_-]{30,}"),
        default_base_url="https://generativelanguage.googleapis.com",
        note="Google Gemini",
        default_models=["gemini-2.0-flash", "gemini-1.5-flash"],
    ),
    KeyFingerprint(
        provider_type="groq",
        provider_name="groq",
        pattern=re.compile(r"^gsk_[A-Za-z0-9]{20,}"),
        default_base_url="https://api.groq.com/openai/v1",
        note="Groq 极速推理",
        default_models=["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    ),
    KeyFingerprint(
        provider_type="xai",
        provider_name="xai",
        pattern=re.compile(r"^xai-[A-Za-z0-9_-]{20,}"),
        default_base_url="https://api.x.ai/v1",
        note="xAI Grok",
        default_models=["grok-2-latest"],
    ),
    KeyFingerprint(
        provider_type="openai_compatible",
        provider_name="openrouter",
        pattern=re.compile(r"^sk-or-v1-[A-Za-z0-9_-]{20,}"),
        default_base_url="https://openrouter.ai/api/v1",
        note="OpenRouter (聚合路由, :free 模型免费)",
        default_models=["deepseek/deepseek-chat-v3-0324:free",
                        "meta-llama/llama-3.3-70b-instruct:free"],
    ),
    KeyFingerprint(
        provider_type="openai_compatible",
        provider_name="zhipu",
        pattern=re.compile(r"^[a-f0-9]{32}\.[a-zA-Z0-9]{8,}$"),
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        note="智谱 GLM (JWT 段格式)",
        default_models=["glm-4-flash", "glm-4-air"],
    ),
    # Cohere trial key 一般是 40+ 字符的纯字母数字 (没有 sk- 前缀)
    KeyFingerprint(
        provider_type="openai_compatible",
        provider_name="cohere",
        pattern=re.compile(r"^[A-Za-z0-9]{40,}$"),
        default_base_url="https://api.cohere.ai/compose/v1",
        note="Cohere (40+ 字符 trial key)",
        default_models=["command-r-plus"],
    ),
]


# 模糊 sk- 开头的候选 base_url, 用于探测 (GET {base_url}/models, 200 即命中)
# 顺序: 最常见/最可能命中的排前面, as_completed 先到先得。
GENERIC_OPENAI_COMPATIBLE_CANDIDATES: list[tuple[str, str]] = [
    # (provider_name, base_url)
    ("openai",        "https://api.openai.com/v1"),
    ("deepseek",      "https://api.deepseek.com/v1"),
    ("openrouter",    "https://openrouter.ai/api/v1"),
    ("siliconflow",   "https://api.siliconflow.cn/v1"),
    ("dashscope",     "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("moonshot",      "https://api.moonshot.cn/v1"),
    ("zai",           "https://api.z.ai/api/paas/v4"),
    ("doubao",        "https://ark.cn-beijing.volces.com/api/v3"),
    ("groq",          "https://api.groq.com/openai/v1"),
    ("mistral",       "https://api.mistral.ai/v1"),
    ("cerebras",      "https://api.cerebras.ai/v1"),
    ("nvidia",        "https://integrate.api.nvidia.com/v1"),
    ("together",      "https://api.together.xyz/v1"),
    ("perplexity",    "https://api.perplexity.ai"),
    ("huggingface",   "https://router.huggingface.co/v1"),
]


def detect_by_prefix(api_key: str) -> Optional[KeyFingerprint]:
    """按前缀精确匹配。命中返回指纹, 否则 None。"""
    key = (api_key or "").strip()
    if not key:
        return None
    for fp in FINGERPRINTS:
        if fp.pattern.match(key):
            return fp
    return None


def looks_like_generic_sk(api_key: str) -> bool:
    """是否是模糊 sk- 开头 (需要探测)。"""
    return bool(re.match(r"^sk-[A-Za-z0-9]{20,}", (api_key or "").strip()))


def build_provider_config(
    *,
    provider_type: str,
    provider_name: str,
    base_url: str,
    api_key: str,
    models: Optional[list[str]] = None,
    priority: int = 50,
    cost_tier: str = "low",
) -> ProviderConfig:
    """把识别结果构造成 ProviderConfig, 供 engine.register_provider 使用。

    - provider_name 同时作为 config.name (唯一标识)
    - api_key 直接写进 config.api_key (本机 self-hosted, 不做 env 推导)
    - models 为空时用指纹默认模型; 探测到 /models 列表后由调用方覆盖
    """
    return ProviderConfig(
        name=provider_name,
        type=provider_type,
        base_url=base_url.rstrip("/"),
        api_key=api_key,
        models=list(models or []),
        priority=priority,
        cost_tier=cost_tier,
        enabled=True,
        extra={},
    )
