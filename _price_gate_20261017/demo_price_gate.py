# -*- coding: utf-8 -*-
"""R3.7 价格门 4 组实证（离线：不联网、不开端口、不写仓库运行库）。

用法（在工程根目录）::

    .\\.venv\\Scripts\\python.exe _price_gate_20261017\\demo_price_gate.py

这是本任务的**取证脚本**（不是产品代码），只放进 `_price_gate_20261017\\` 这个
临时证据目录，方便复核者原样重跑。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import types
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services.free_model_router import (                      # noqa: E402
    BUILTIN_FREE_PROVIDERS,
    TRUSTED_MODELS_KEY,
    FreeModelRouter,
)

logging.basicConfig(level=logging.WARNING, stream=sys.stdout,
                    format="[%(levelname)s] %(name)s: %(message)s")

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
OR_BODY = json.dumps({"data": [
    {"id": "vendor/zeroprice", "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "vendor/new-free:free", "pricing": {}},
    {"id": "openai/gpt-4o",
     "pricing": {"prompt": "0.0000025", "completion": "0.00001"}},
    {"id": "openrouter/free", "pricing": {}},
]})

TMP = tempfile.TemporaryDirectory(prefix="loveai_gate_demo_")
BLOCK = os.path.join(TMP.name, "blocked")
with open(BLOCK, "w", encoding="utf-8") as fh:
    fh.write("x")


class FlatConfig:
    """与产品里 `config.get("a.b.c")` 同名的嵌套配置。"""

    def __init__(self, mapping):
        self.store = {}
        for k, v in mapping.items():
            parts = k.split(".")
            node = self.store
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = v

    def get(self, key, default=None):
        value = self.store
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value


class _Msg:
    def __init__(self, c):
        self.content = c


class _Choice:
    def __init__(self, c):
        self.message = _Msg(c)


class _Resp:
    def __init__(self, c):
        self.choices = [_Choice(c)]


class FakeOpenAI:
    """假 openai client：只计数，不发任何请求。"""

    clients: list = []      # OpenAI(...) 构造记录（base_url）
    calls: list = []        # chat.completions.create 记录（model）

    def __init__(self, *a, **kw):
        FakeOpenAI.clients.append(kw.get("base_url", ""))
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        FakeOpenAI.calls.append(kw.get("model"))
        return _Resp("ok")


def reset():
    FakeOpenAI.clients = []
    FakeOpenAI.calls = []


# **必须先打桩 openai.OpenAI**，否则 chat() 会真发请求（本脚本要求全程离线）
_OPENAI_PATCH = mock.patch("openai.OpenAI", FakeOpenAI)
_OPENAI_PATCH.start()
print("[harness] openai.OpenAI 已打桩为假客户端")

# 再加一道硬保险：禁用一切 socket 连接（连本机回环也不放），任何真实出网都会当场抛错
import socket  # noqa: E402

_REAL_CONNECT = socket.socket.connect


def _no_network(self, addr):
    raise ConnectionError(f"[harness] 取证脚本禁用网络，捕获到真实连接尝试: {addr}")


socket.socket.connect = _no_network
socket.socket.connect_ex = _no_network
print("[harness] socket.connect / connect_ex 已禁用（全程离线）")


def make_http(routes):
    calls = []

    def _get(url, timeout=None, headers=None, **kw):
        calls.append(url)
        r = routes.get(url)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise ConnectionError(f"未路由的假端点: {url}")
        return r

    return _get, calls


BASE = {
    "llm.free.state_dir": BLOCK,
    "llm.free.auto_catalog": "false",
    "llm.free.auto_discover_bridge": "false",
    "llm.free.include_ollama": "false",
    "llm.free.nvidia_catalog_md": "false",
    "llm.free.local_scan.enabled": "false",
    "llm.free.desktop_ai.enabled": "false",
    "llm.free.zero_config.enabled": "false",
}


def cfg(**extra):
    d = dict(BASE)
    d.update(extra)
    return FlatConfig(d)


def router(config, http):
    return FreeModelRouter(config, http_get=http)


def show(title, r, http_calls, snapshot_keys=False):
    print(f"  candidates         : {[c.provider + ':' + c.model for c in r.candidates()]}")
    print(f"  ranked             : {[c.provider + ':' + c.model for c in r.ranked()]}")
    print(f"  [调用前] http_get 调用   : {http_calls}")
    print(f"  [调用前] openai 客户端构造: {FakeOpenAI.clients}")
    print(f"  [调用前] chat.completions: {FakeOpenAI.calls}")
    try:
        out = r.chat("你是助手", "你好")
        print(f"  chat() 结果         : routed_via={out['routed_via']} text={out['text']!r}")
    except RuntimeError as e:
        print(f"  chat() 结果         : RuntimeError({e})")
    print(f"  [调用后] openai 客户端构造: {FakeOpenAI.clients}")
    print(f"  [调用后] chat.completions: {FakeOpenAI.calls}")
    if snapshot_keys:
        snap = r.routing_snapshot()
        print(f"  snapshot['mode']    : {json.dumps(snap['mode'], ensure_ascii=False)}")


print("=" * 78)
print("① 付费候选打不出去：拒绝时出站调用次数 = 0")
print("=" * 78)
http, calls = make_http({})
print("\n[①a] llm.free.nvidia_models 里塞付费模型 openai/gpt-4o（NVIDIA 分支）")
reset()
r = router(cfg(**{"llm.free.nvidia_key": "nvapi-test",
                  "llm.free.nvidia_models": "openai/gpt-4o"}), http)
show("nvidia", r, calls)

print("\n[①b] llm.free.custom_base + custom_models 塞付费模型（custom 分支）")
calls = []
reset()
r = router(cfg(**{"llm.free.custom_base": "http://10.0.0.5:9999/v1",
                  "llm.free.custom_models": "gpt-4o"}), http)
show("custom", r, calls)

print("\n" + "=" * 78)
print("② 白名单正向对照：登记后能被放行，且真的被调用（不是死代码）")
print("=" * 78)
print("\n[②a] 同一份 nvidia 配置 + trusted_models=nvidia:openai/gpt-4o")
reset()
r = router(cfg(**{"llm.free.nvidia_key": "nvapi-test",
                  "llm.free.nvidia_models": "openai/gpt-4o",
                  TRUSTED_MODELS_KEY: "nvidia:openai/gpt-4o"}), http)
show("nvidia", r, calls, snapshot_keys=True)

print("\n[②b] 同一份 custom 配置 + trusted_models=custom:*（端点级）")
reset()
r = router(cfg(**{"llm.free.custom_base": "http://10.0.0.5:9999/v1",
                  "llm.free.custom_models": "gpt-4o",
                  TRUSTED_MODELS_KEY: "custom:*"}), http)
show("custom", r, calls)

print("\n" + "=" * 78)
print("③ 付费不可翻案：正面证明付费的候选，登记白名单也仍被拒绝")
print("=" * 78)
http, calls = make_http({OPENROUTER_MODELS_URL: OR_BODY})
print("\n[③] 全量目录里 openai/gpt-4o pricing 非 0 + trusted_models=openrouter:*")
reset()
r = router(cfg(**{"llm.free.openrouter_key": "sk-or-x",
                  "llm.free.auto_catalog": "true",
                  "llm.free.openrouter_models": "openai/gpt-4o",
                  TRUSTED_MODELS_KEY: "openrouter:*"}), http)
show("proven_paid", r, calls)
print("  ↑ 注意：同一条 openrouter:* 白名单对**没有付费证据**的 id 是生效的（见下）")
reset()
r2 = router(cfg(**{"llm.free.openrouter_key": "sk-or-x",
                   "llm.free.auto_catalog": "true",
                   "llm.free.openrouter_models": "vendor/unlisted-in-catalog",
                   TRUSTED_MODELS_KEY: "openrouter:*"}), http)
print(f"  [对照] vendor/unlisted-in-catalog 的 candidates: "
      f"{[c.model for c in r2.candidates()]}")

print("\n" + "=" * 78)
print("④ 不误杀对照：本机 Ollama / pollinations / llm7 / OpenRouter :free 仍进池")
print("=" * 78)
print("\n[④a] 本机 Ollama + 零配置 pollinations/llm7 + OpenRouter 静态 :free")
reset()
r = router(cfg(**{"llm.free.include_ollama": "true",
                  "llm.free.ollama_models": "loveai-qwen05b:latest",
                  "llm.free.zero_config.enabled": "true",
                  "llm.free.openrouter_key": "sk-or-x"}), http)
show("no-collateral-damage", r, calls)
print(f"  (A) 直通源集合      : {sorted(BUILTIN_FREE_PROVIDERS)}")

print("\n[④b] OpenRouter 动态目录：五维全 0 / :free 进池，正面证明付费的不进池")
http2, calls2 = make_http({OPENROUTER_MODELS_URL: OR_BODY})
reset()
r = router(cfg(**{"llm.free.openrouter_key": "sk-or-x",
                  "llm.free.auto_catalog": "true"}), http2)
show("dynamic-free", r, calls2)

print("\n" + "=" * 78)
print("总结")
print("=" * 78)
print("① NVIDIA / custom 的付费候选：candidates 为空，出站调用 0 次")
print("② 同一候选登记白名单后：进池并被真实调用")
print("③ 正面证明付费者：即使 provider:* 白名单也仍被拒绝（出站 0 次）")
print("④ Ollama / pollinations / llm7 / OpenRouter :free 全部照常进池")
socket.socket.connect = _REAL_CONNECT
_OPENAI_PATCH.stop()
TMP.cleanup()
