# -*- coding: utf-8 -*-
"""
从 Cherry Studio 提取的 provider 配置加载器。
自动加载 61 家厂商的 base_url, 用户只需填 API Key。

修复记录 (2026-10-10):
  - 原 `from app.core.logger import logger` 导入了不存在的模块, 改为标准 logging。
  - 新增 build_provider_configs(): 把 cherry JSON 转成 ProviderConfig 列表,
    默认 enabled=False (需要用户填 Key 才启用), 注册进 engine + cfg.providers。
  - adapter_family -> ProviderConfig.type 映射, anthropic/google 走专用适配器。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

from app.config import ProviderConfig

logger = logging.getLogger(__name__)

# 配置文件路径
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "cherry_providers.json"
)

_providers_cache: Optional[List[dict]] = None


# adapter_family -> ProviderConfig.type
# 绝大多数厂商是 OpenAI 兼容协议, 统一走 openai_compatible;
# 只有 anthropic / google 有专用适配器。
_ADAPTER_FAMILY_TO_TYPE: Dict[str, str] = {
    "anthropic": "anthropic",
    "google": "gemini",
    "openai": "openai_compatible",
    "openai-compatible": "openai_compatible",
    "newapi": "openai_compatible",
    "aihubmix": "openai_compatible",
    "dmxapi": "openai_compatible",
    "gateway": "openai_compatible",
    "perplexity": "openai_compatible",
    "togetherai": "openai_compatible",
    "groq": "openai_compatible",
    "cerebras": "openai_compatible",
    "mistral": "openai_compatible",
    "xai": "openai_compatible",
    "cherryin": "openai_compatible",
    "github-copilot-openai-compatible": "openai_compatible",
    "voyage": "openai_compatible",  # embedding 专用, 注册但默认不用
    # comfyui 是图像生成, 非 chat, 不注册到路由表
}

# 这些 id 不是 chat 推理端点, 不注册到运行时路由表
_SKIP_IDS = {"comfyui", "voyageai"}


def load_cherry_providers() -> List[dict]:
    """加载 Cherry Studio 提取的 provider 配置 (带缓存)。"""
    global _providers_cache
    if _providers_cache is not None:
        return _providers_cache

    if not os.path.exists(_CONFIG_PATH):
        logger.warning("Cherry providers config not found: %s", _CONFIG_PATH)
        _providers_cache = []
        return []

    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _providers_cache = json.load(f)
        logger.info("Loaded %d cherry studio providers", len(_providers_cache))
        return _providers_cache
    except Exception as e:
        logger.error("Failed to load cherry providers: %s", e)
        _providers_cache = []
        return []


def detect_provider_by_key(api_key: str) -> Optional[dict]:
    """根据 API Key 前缀粗略匹配 (仅做参考, 精确识别走 key_detect.py)。"""
    providers = load_cherry_providers()
    key_prefixes = {
        "sk-ant-": "anthropic",
        "sk-or-v1-": "openrouter",
        "gsk_": "groq",
        "AIza": "gemini",
        "xai-": "xai",
    }
    for prefix, provider_id in key_prefixes.items():
        if api_key.startswith(prefix):
            for p in providers:
                if p["id"] == provider_id:
                    return p
    return None


def get_provider_by_id(provider_id: str) -> Optional[dict]:
    """根据 id 获取 provider 配置。"""
    providers = load_cherry_providers()
    for p in providers:
        if p["id"] == provider_id:
            return p
    return None


def list_all_providers() -> List[dict]:
    """列出所有可用的 provider。"""
    return load_cherry_providers()


def _normalize_base_url(entry: dict) -> str:
    """base_url 归一化: 去掉尾部斜杠; 裸 API host 补 /v1 (仅当缺版本段时)。

    Cherry JSON 里有的是 https://api.openai.com (缺 /v1), 有的已经是完整路径。
    只对"看起来是裸 host 且没有路径段"的补 /v1, 避免破坏已有路径。
    """
    url = (entry.get("base_url") or "").strip().rstrip("/")
    if not url:
        return ""
    # 本地服务 / 已有路径段 (含 /) 不动
    if url.startswith("http://") and "localhost" in url:
        return url
    # 形如 https://host (没有额外 path) 的补 /v1
    # 解析 path: 去掉 scheme 后看第一个 /
    rest = url.split("://", 1)[-1]
    if "/" not in rest:
        return url + "/v1"
    return url


def _entry_to_type(entry: dict) -> str:
    family = entry.get("adapter_family") or ""
    if family in _ADAPTER_FAMILY_TO_TYPE:
        return _ADAPTER_FAMILY_TO_TYPE[family]
    # 没有 adapter_family 的默认按 OpenAI 兼容处理
    return "openai_compatible"


def build_provider_configs() -> List[ProviderConfig]:
    """把 61 个 cherry provider 转成 ProviderConfig 列表。

    - 默认 enabled=False: 用户还没填 Key, 不参与路由, 但已注册进运行时表
      (出现在 /admin/catalog, 用户贴 Key 后自动 enable 对应条目)。
    - 跳过非 chat 端点 (comfyui / voyageai)。
    - type 按 adapter_family 映射, anthropic/google 走专用适配器。
    """
    out: List[ProviderConfig] = []
    for entry in load_cherry_providers():
        pid = entry.get("id", "")
        if pid in _SKIP_IDS:
            continue
        base_url = _normalize_base_url(entry)
        if not base_url:
            continue
        ptype = _entry_to_type(entry)
        out.append(ProviderConfig(
            name=f"cherry_{pid}",
            type=ptype,
            base_url=base_url,
            api_key="",            # 等用户贴 Key
            models=[],             # 启用时再探测 /models
            priority=80,           # 比内置零配置/注册即送低, 兜底
            cost_tier="low",
            enabled=False,         # 关键: 没 Key 不启用
            extra={"cherry_id": pid, "display_name": entry.get("name", pid)},
        ))
    return out


def match_cherry_for_key(provider_name: str, base_url: str) -> Optional[ProviderConfig]:
    """用户贴 Key 识别出厂商后, 找到对应的 cherry ProviderConfig (如果有)。

    用于启用已注册但 disabled 的 cherry 条目, 而不是新建重复条目。
    """
    name_norm = (provider_name or "").lower()
    url_norm = (base_url or "").rstrip("/").lower()
    for pcfg in build_provider_configs():
        cherry_id = (pcfg.extra or {}).get("cherry_id", "").lower()
        if cherry_id and cherry_id == name_norm:
            return pcfg
        if pcfg.base_url.rstrip("/").lower() == url_norm:
            return pcfg
    return None
