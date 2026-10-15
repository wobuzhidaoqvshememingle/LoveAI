# -*- coding: utf-8 -*-
"""
网关配置加载。

优先级: 环境变量 > config.yaml > 默认值。
敏感字段 (api_key) 不写进 yaml, 通过环境变量注入:
  环境变量名 = GATEWAY_PROVIDER_<NAME>_API_KEY (大写, 非字母数字转下划线)
例如 provider name=groq -> GATEWAY_PROVIDER_GROQ_API_KEY
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


# --------------------------------------------------------------------------- #
# 数据类
# --------------------------------------------------------------------------- #
@dataclass
class ProviderConfig:
    """单个厂商配置。"""
    name: str                                   # 唯一标识, 如 "openai", "groq"
    type: str                                   # openai_compatible | anthropic | gemini | cloudflare | cohere | mistral | ollama
    base_url: str = ""
    api_key_env: str = ""                       # 环境变量名, 从这里读 key
    api_key: str = ""                           # 直接写死 (dev 用, 生产建议走 env)
    models: list[str] = field(default_factory=list)   # 该厂商支持的模型名列表
    priority: int = 100                         # 数字越小优先级越高
    cost_tier: str = "low"                      # free | low | high
    rpm_limit: int = 0                          # 0 = 不限
    rpd_limit: int = 0
    tpm_limit: int = 0
    tpd_limit: int = 0
    timeout_sec: float = 60.0
    enabled: bool = True
    extra: dict = field(default_factory=dict)   # 厂商特定参数 (如 cloudflare account_id)

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        # 自动推导: GATEWAY_PROVIDER_<NAME>_API_KEY
        derived = "GATEWAY_PROVIDER_" + "".join(
            c.upper() if c.isalnum() else "_" for c in self.name
        ) + "_API_KEY"
        return os.environ.get(derived, "")


@dataclass
class CompressionConfig:
    enabled: bool = True
    min_messages_to_compress: int = 8           # 对话超过 N 条才压缩
    retain_last_n_messages: int = 4             # 保留最近 N 条原文
    summarize_system_prompt: bool = True
    skip_compression_models: list[str] = field(default_factory=lambda: [
        # 敏感/代码任务不压缩, 按模型名匹配子串
    ])


@dataclass
class SecurityConfig:
    gateway_api_key: str = ""                   # 调用方访问网关需要的 key; 空 = 不鉴权 (仅本机)
    encrypt_keys_at_rest: bool = True
    master_key_env: str = "GATEWAY_MASTER_KEY"  # AES-256-GCM 主密钥来源
    log_payloads: bool = False                  # 是否记录请求/响应正文 (默认只记元数据)


@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    default_strategy: str = "auto"              # priority | cost_first | latency_first | quality_first | load_balance | auto
    fallback_max_tries: int = 3
    circuit_breaker_failure_threshold: int = 3  # 连续失败 N 次 -> open
    circuit_breaker_open_sec: int = 300         # open 持续秒数
    circuit_breaker_half_open_trial: int = 1    # 半开放行几个试探
    data_dir: str = "./data"
    providers: list[ProviderConfig] = field(default_factory=list)
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)

    def provider_by_name(self, name: str) -> Optional[ProviderConfig]:
        for p in self.providers:
            if p.name == name:
                return p
        return None

    def models_index(self) -> dict[str, list[ProviderConfig]]:
        """构建 model_name -> [providers] 反向索引。"""
        idx: dict[str, list[ProviderConfig]] = {}
        for p in self.providers:
            if not p.enabled:
                continue
            for m in p.models:
                idx.setdefault(m, []).append(p)
        return idx


# --------------------------------------------------------------------------- #
# 加载
# --------------------------------------------------------------------------- #
def _parse_provider(d: dict) -> ProviderConfig:
    return ProviderConfig(
        name=d["name"],
        type=d.get("type", "openai_compatible"),
        base_url=d.get("base_url", ""),
        api_key_env=d.get("api_key_env", ""),
        api_key=d.get("api_key", ""),
        models=list(d.get("models", [])),
        priority=int(d.get("priority", 100)),
        cost_tier=d.get("cost_tier", "low"),
        rpm_limit=int(d.get("rpm_limit", 0)),
        rpd_limit=int(d.get("rpd_limit", 0)),
        tpm_limit=int(d.get("tpm_limit", 0)),
        tpd_limit=int(d.get("tpd_limit", 0)),
        timeout_sec=float(d.get("timeout_sec", 60.0)),
        enabled=bool(d.get("enabled", True)),
        extra=dict(d.get("extra", {})),
    )


def load_config(path: str | Path = "config.yaml") -> GatewayConfig:
    """从 yaml 加载配置; 文件不存在时返回空配置 (仅本机无鉴权)。"""
    path = Path(path)
    cfg = GatewayConfig()
    if not path.exists():
        return cfg
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.host = raw.get("host", cfg.host)
    cfg.port = int(raw.get("port", cfg.port))
    cfg.default_strategy = raw.get("default_strategy", cfg.default_strategy)
    cfg.fallback_max_tries = int(raw.get("fallback_max_tries", cfg.fallback_max_tries))
    cfg.circuit_breaker_failure_threshold = int(
        raw.get("circuit_breaker_failure_threshold", cfg.circuit_breaker_failure_threshold)
    )
    cfg.circuit_breaker_open_sec = int(raw.get("circuit_breaker_open_sec", cfg.circuit_breaker_open_sec))
    cfg.data_dir = raw.get("data_dir", cfg.data_dir)

    if "compression" in raw:
        c = raw["compression"]
        cfg.compression = CompressionConfig(
            enabled=bool(c.get("enabled", True)),
            min_messages_to_compress=int(c.get("min_messages_to_compress", 8)),
            retain_last_n_messages=int(c.get("retain_last_n_messages", 4)),
            summarize_system_prompt=bool(c.get("summarize_system_prompt", True)),
            skip_compression_models=list(c.get("skip_compression_models", [])),
        )
    if "security" in raw:
        s = raw["security"]
        cfg.security = SecurityConfig(
            gateway_api_key=s.get("gateway_api_key", ""),
            encrypt_keys_at_rest=bool(s.get("encrypt_keys_at_rest", True)),
            master_key_env=s.get("master_key_env", "GATEWAY_MASTER_KEY"),
            log_payloads=bool(s.get("log_payloads", False)),
        )
    cfg.providers = [_parse_provider(p) for p in raw.get("providers", [])]
    return cfg
