# -*- coding: utf-8 -*-
"""
免费模型路由层（FreeModelRouter）

对应总纲 B 模块「L2 免费模型路由」：
- 来源：OpenRouter 免费模型（:free 后缀）、NVIDIA NIM 免费端点、本地 Ollama；
- 同一轮按 可用(已配key/本地在线) → 历史成功率 → 首 token 延迟 排序；
- 限流(429)/超时/认证失败(401/403)/服务端错误(5xx) 时同轮自动切下一个，
  不绕过账号级免费配额，只在「有 key / 本地在跑」的渠道间切换；
- 全部走 OpenAI 兼容 Chat Completions 协议。

动态目录（"自动发现最新免费模型、轮换续命"）：
- OpenRouter：GET https://openrouter.ai/api/v1/models（公开免 key），按 pricing 全 0 /
  id 以 :free 结尾筛选；
- NVIDIA：配了 key 则 GET integrate.api.nvidia.com/v1/models；未配 key 则尽力解析公开
  目录 build.nvidia.com/models.md（best-effort）；
- 结果按 TTL 缓存（默认 24h，可定时刷新 / force）；任何网络异常都静默回退：
  显式 config 模型 > 动态目录 > 内置静态目录。

注意：免费 ≠ 无 key。OpenRouter / NVIDIA 需注册领取免费额度后填 key；
本地 Ollama 无需 key，但需本机已安装并 `ollama serve`。

---- R3.4 路由内核（services/free_router_kernel.py）----
本模块在既有韧性层（5xx 熔断 / 429 同模型退避 / 启动探活）之上，接上 7 条路由内核
（机制来自 freellmapi 的路由内核，用标准库等价实现）：

  1) 动态惩罚路由：实际排序 = 基础优先级 + 惩罚分（429 +3 上限 10 / 成功 -1 /
     每 2min 衰减 1）→ 被限流的通道自动下沉、冷却后自动回到原位，无需手工调顺序；
  2) 滑动窗口限流：60s 滑动窗口（不是「每分钟归零」的固定窗口），内存 + SQLite
     双写，重启不丢计数；
  3) 四维限流：RPM / RPD / TPM / TPD，任一维度触顶即跳过该 Provider
     （上限只来自 config；本项目不内置任何第三方额度数字）；
  4) 429 冷却升级：同一模型 24h 内第 N 次 429 → 2min / 10min / 1h / 24h（持久化）；
  5) Sticky Session：首条 user 消息的 SHA1 作键，**仅多轮生效**、30 分钟有效、
     命中只是「优先」不是「强制」；
  6) Fallback/重试：整链最多 MAX_ROUTE_ATTEMPTS(=20) 次尝试 +「模型+Key」跳过集合；
     **401/403 认证失败不进重试链**（换模型解决不了 Key 无效）；
  7) 可观测性：chat() 结果带 routed_via（provider:model），
     routing_snapshot() 出全量快照（供设置页只读展示）。

fail-closed 不变式（硬约束，任何新增逻辑都不得削弱）：
  · 只在本模块自己枚举出的**免费**候选里轮转，代码里没有任何一条通往付费 provider
    的路径（候选只可能来自：显式 config 免费项 / OpenRouter 免费目录 / NVIDIA NIM
    免费端点 / 本机 Ollama / 本机桥 / pollinations / llm7）；
  · 「不确定是否免费」= 不进入候选（OpenRouter 目录只收 pricing 五项
    [prompt/completion/request/input_cache_read/input_cache_write] 全 0 或
    :free 后缀；任一项非 0 或数值不可解析 → 拒绝，见 _strictly_free_pricing）；
  · R3.7 价格门（fail-closed 硬闸，唯一判据 = price_gate_verdict）：任何候选要进池
    必须满足下列 (A)(B)(C) 之一，**其余一律拒绝**，并打一条可读告警 + 白名单提示：
      (A) 内置本机/零配置直通源（BUILTIN_FREE_PROVIDERS：本机 Ollama / 本机桥
          127.0.0.1:8787·3000·8282 / 本机扫描 / 桌面客户端 shim / 内置免 Key 的
          pollinations·llm7）——它们不产生第三方计费，直接放行。注意 (A) 对
          **地址可配置**的那几个源还要求 base_url 确为本机/内网
          （_is_local_base_url）：把 llm.free.ollama_base 或
          llm.free.bridge_probe_urls 指到公网付费端点时，光有 provider 名不算 (A)，
          同样落到默认拒绝（原因码 endpoint_not_local）、要靠 (C) 才放行；
      (B) 正面证据证明免费：公开价格目录五维全 0（目录证据集 free_ids）或 id 以
          ``:free`` 结尾（OpenRouter 自带的免费标记）；
      (C) 用户显式白名单：把该模型 / 该端点登记进 ``llm.free.trusted_models``
          （用户自担风险的显式声明）。
    「拿不到证据就放行」的路径已**全部删除**：NVIDIA 显式配置分支与它的 /v1/models
    （响应不含 pricing 字段）、custom 自建端点（没有公开价目表）都必须靠 (C) 才放行；
    同理，(A) 里地址来自配置/运行时的四个源（ollama / local_bridge / local_scan /
    desktop）也不再只凭 provider 名放行，必须地址本身确为本机/内网；
  · R3.6 正面付费证据（_proven_paid_pricing / 目录证据集 paid_ids）仍是**最高**优先级的
    拒绝理由：pricing 任一项解析出非 0 即拒绝，**白名单也不得翻案**——用户声明不能
    覆盖客观价格事实；
  · 免费池耗尽 → chat() 抛 RuntimeError（空池 / 全失败），由上层继续降级，
    绝不改道付费模型，也绝不产生任何计费调用。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

from services.free_router_kernel import (
    Cooldown429,
    KNOWN_PROVIDERS,
    MAX_ROUTE_ATTEMPTS,
    PenaltyTable,
    PersistentStateStore,
    RateLimiter,
    RateLimitLedger,
    SkipSet,
    STICKY_TTL_SEC,
    StickySessionStore,
    base_priority_for,
    build_limit_specs,
    channel_tag,
    effective_score,
    session_key_for,
)

# R3.8 「需 Key 登记渠道」描述符（services/free_key_channels.py：纯数据 + 纯函数，
# 无网络、无副作用）。它提供 10 条已登记但此前未接入的需 key 免费渠道
# （sambanova/zhipu/github_models/cloudflare_workers_ai/groq/cerebras/cohere/
# google_gemini/mistral/huggingface）的 base_url / 鉴权头 / 配置键 / 默认模型。
from services.free_key_channels import PROVIDER_IDS, channels_with_keys  # noqa: E402

logger = logging.getLogger(__name__)

# ---- R3.3-30/32 韧性参数（熔断/退避/冷却）----
# 5xx 熔断：起步 30s，连续失败翻倍，封顶 5min
COOLDOWN_BASE_SEC = 30.0
COOLDOWN_MAX_SEC = 300.0
# 429 限流：同模型指数退避 1s -> 2s -> 4s，最多重试 3 次
BACKOFF_RETRY_DELAYS = (1.0, 2.0, 4.0)
BACKOFF_MAX_RETRIES = len(BACKOFF_RETRY_DELAYS)
# 启动探活
PROBE_MAX_TOKENS = 1
PROBE_TIMEOUT_SEC = 2.0
PROBE_TOTAL_BUDGET_SEC = 5.0

# OpenRouter 免费模型目录（:free 后缀，可能随时间上下架，运行时可被 config 覆盖）
_OPENROUTER_FREE = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "microsoft/wizardlm-2-8x22b-v1.0:free",
]

# NVIDIA NIM 免费端点（integrate.api.nvidia.com，OpenAI 兼容）
_NVIDIA_NIM_FREE = [
    "meta/llama-3.3-70b-instruct",
    "deepseek-ai/deepseek-r1",
    "microsoft/phi-3.5-mini-instruct",
]

# 本地 Ollama（OpenAI 兼容：http://host:11434/v1）
# 默认模型名对齐本机实际存在的 ollama 模型（小快/大稳）；真实列表优先由
# /api/tags 在线探测，这里的默认名仅在探测失败时兜底，避免 model not found(404)。
_OLLAMA_DEFAULT_MODELS = ["loveai-qwen05b:latest", "loveai-qwen15b:latest"]

# ---- 零配置免费 HTTP provider（免 Key、免注册，实测 chat 200 真出文本 2026-10-13）----
# 元数据来源: gateway/app/providers/registry.py FREE_PROVIDER_CATALOG（verified_ok）
#   pollinations: /models 505ms, chat 886ms 真出文本 "Hey there, friend."
#   llm7:         /models 787ms, chat 2292ms 真出文本 (DeepSeek-V4-Flash-0731)
# 这些 provider 无需 api_key；openai client 用占位 key 即可，端点忽略 Authorization。
# 默认开（llm.free.zero_config.enabled），真实请求经 candidates() 进入候选轮询。
_ZERO_CONFIG_API: list[dict] = [
    {
        "provider": "pollinations",
        "base_url": "https://text.pollinations.ai/openai",
        "models": ["openai-fast", "openai", "llama", "mistral"],
    },
    {
        "provider": "llm7",
        "base_url": "https://api.llm7.io/v1",
        "models": ["DeepSeek-V4-Flash-0731", "gpt-4o-mini",
                   "claude-3-5-sonnet", "llama-3.3-70b"],
    },
]

# ---- 动态目录端点 / 默认参数 ----
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
NVIDIA_CATALOG_MD_URL = "https://build.nvidia.com/models.md"
DEFAULT_HTTP_TIMEOUT = 8.0
DEFAULT_CATALOG_TTL_HOURS = 24.0

# 自动发现本机 OpenAI 兼容桥接（零第三方 key、复用本机已登录客户端的免费积分）
# - 8787：finch-workbuddy-bridge / workbuddy 桥（复用 WorkBuddy 桌面登录态）
# - 3000：one-api / New API 常见端口；8282：FastAPI 本地桥
DEFAULT_BRIDGE_PROBE_URLS = [
    "http://127.0.0.1:8787/v1",
    "http://127.0.0.1:3000/v1",
    "http://127.0.0.1:8282/v1",
]
DEFAULT_BRIDGE_PROBE_TIMEOUT = 1.5

# 形如 vendor/model 的 id，且被反引号或括号包裹（用于解析 build.nvidia.com/models.md）
_NV_ID_RE = re.compile(r"[`(]\s*([a-z0-9][a-z0-9_.\-]*/[a-z0-9][a-z0-9_.\-]*)\s*[`)]")


def _http_get(url: str, timeout: float = DEFAULT_HTTP_TIMEOUT,
              headers: Optional[dict] = None) -> str:
    """标准库 HTTP GET，返回响应体文本；任何异常向上抛，由调用方捕获并回退。"""
    from urllib import request as urlreq
    req = urlreq.Request(url, headers=headers or {})
    with urlreq.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


def _parse_nvidia_catalog_md(text: str) -> list[str]:
    """best-effort 解析 build.nvidia.com/models.md，提取 vendor/model 形式的模型 id。"""
    ids: set[str] = set()
    for m in _NV_ID_RE.finditer(text or ""):
        mid = m.group(1)
        if "http" not in mid and len(mid) < 80:
            ids.add(mid)
    return sorted(ids)


def _msg_field(msg, name: str) -> str:
    """从 OpenAI 风格消息（dict 或对象）里安全取字段，缺失返回空串。

    用于 Sticky Session 解析「首条 user 消息」与判断「是否含 assistant 历史」；
    两种形态都容错，绝不因为消息结构不规范而抛异常。
    """
    value = msg.get(name) if isinstance(msg, dict) else getattr(msg, name, None)
    return "" if value is None else str(value)


# 严格免费口径要看的五个计价维度（缺一项非 0 即不算免费）
STRICT_FREE_COST_KEYS = ("prompt", "completion", "request",
                         "input_cache_read", "input_cache_write")


def _strictly_free_pricing(pricing) -> bool:
    """严格免费判定：prompt/completion/request/input_cache_read/input_cache_write
    五项**必须全部为 0** 才算免费。

    · 字段**缺失** → 按 "0" 处理（= 该维度未计费），与改动前
      `str(p.get("prompt","0")) == "0"` 的口径完全一致；
    · 值为 None / 空串 / 解析不出来 → 一律不是免费
      （改动前 None/空串也会被 str() 判为不等于 "0" 而排除，故**只收窄不放宽**）；
    · 数值上等于 0 的等价写法（"0" / 0 / "0.0" / "0E0"）视为免费 —— 它们本来就
      是零价，不会引入任何计费；改动前的纯字符串比较反而会漏掉 "0.0" 这种零价项。

    fail-closed：本函数只可能把候选**排除**，绝不会把付费项放进免费池。
    """
    if not isinstance(pricing, dict):
        return False                    # 不是 dict = 无法确认 = 拒绝
    for key in STRICT_FREE_COST_KEYS:
        raw = pricing.get(key, "0")
        try:
            if float(raw) != 0.0:
                return False
        except (TypeError, ValueError):
            return False                # None / "" / 异常格式 → 不确定 → 拒绝
    return True


def _proven_paid_pricing(pricing) -> bool:
    """**正面证明**为付费：pricing 是 dict，且五个计价维度里至少一项解析出非 0。

    与 _strictly_free_pricing 的唯一区别在「证据不足」时的取向（这正是本函数存在的
    全部理由）：
      · 不是 dict / 字段缺失 / 值为 None·空串 / 数值解析不出来 → False（= 证据不足）；
      · 只有真的解析出非 0 的数 → True（= 有正面证据，确系付费）。

    R3.7 起本函数是价格门里**最高优先级**的拒绝理由（见 price_gate_verdict 第 1 条）：
    一旦解析出非 0，该 id 无论走哪条路（显式 config / 动态目录）都被拒绝，
    **白名单也不得翻案**。反之「证据不足」（不是 dict / 字段缺失 / 值 None·空串 /
    数值解析不出来）在这里返回 False —— 它只说明「没有付费证据」，**不再意味着放行**：
    R3.7 的默认取向是拒绝（fail-closed），无证据的候选要靠 (A) 内置直通源、
    (B) 免费证据 或 (C) 用户白名单才进得来。
    """
    if not isinstance(pricing, dict):
        return False
    for key in STRICT_FREE_COST_KEYS:
        raw = pricing.get(key)
        if raw is None:
            continue
        try:
            if float(raw) != 0.0:
                return True
        except (TypeError, ValueError):
            continue                # 解析不出来 = 证据不足，不算「付费证据」
    return False


# ---- R3.7 价格门（fail-closed 硬闸）：默认拒绝 + 显式白名单放行 ----
# 唯一判据 = FreeModelRouter.price_gate_verdict()；候选要进池必须满足 (A)(B)(C) 之一：
#   (A) 内置本机/零配置直通源（BUILTIN_FREE_PROVIDERS，不产生第三方计费）；
#   (B) 正面证据证明免费（目录证据集 free_ids 五维全 0，或 id 以 ":free" 结尾）；
#   (C) 用户显式白名单（TRUSTED_MODELS_KEY 里登记了该 id / 该端点，自担风险的声明）。
# 其余一律拒绝；且**正面付费证据（paid_ids）优先级最高，白名单也不得翻案**。
#
# 白名单配置键：用户可见、可查的普通配置项（不是内部集合），支持按 provider:id 精确登记。
TRUSTED_MODELS_KEY = "llm.free.trusted_models"

# (A) 内置直通源：本机四类（Ollama / 127.0.0.1:8787·3000·8282 自动发现桥 / 本机扫描 /
# 桌面客户端 shim）+ 内置零配置免 Key 表（pollinations / llm7）。它们的出站目标要么是
# 本机回环、要么是项目内置的公开免 Key 免费端点，不产生任何第三方计费，故直接放行。
# 注意：custom（自建/任意 OpenAI 兼容端点）与 nvidia（/v1/models 不返回 pricing）
# **不在**此表 —— 它们拿不到任何免费证据，必须由用户登记进白名单才放行。
BUILTIN_FREE_PROVIDERS = frozenset({
    "ollama", "local_bridge", "local_scan", "desktop",
    "pollinations", "llm7",
})

# (A) 里**地址由本模块硬编码**、用户改不了指向的两个免 Key 公共端点：按名字放行即可。
# 其余四个（ollama / local_bridge / local_scan / desktop）的 base_url 来自配置或
# 运行时发现（llm.free.ollama_base、llm.free.bridge_probe_urls、本机扫描结果、
# 桌面 shim），所以光看 provider 名**不足以**证明它不产生第三方计费 —— 必须再用
# 地址本身确认是「本机/内网」（见 _builtin_free_allowed）。这就是把
# 「把 ollama_base / bridge_probe_urls 指到公网付费端点」这条同类路径一并堵掉的地方。
_ENDPOINT_INDEPENDENT_BUILTIN = frozenset({"pollinations", "llm7"})

_PRIVATE_IPV4_RE = re.compile(
    r"^(?:10|127|169\.254|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.")
_LOOPBACK_V6_RE = re.compile(r"^(?:0*:)*0*1$")          # ::1
_LINKLOCAL_V6_RE = re.compile(r"^fe[89ab][0-9a-f]:", re.IGNORECASE)
_ULA_V6_RE = re.compile(r"^f[cd][0-9a-f]{2}:", re.IGNORECASE)


def _is_local_base_url(url: Optional[str]) -> bool:
    """``url`` 的 host 是否属于「本机/内网」——(A) 直通源的真实性校验。

    只认回环（localhost / 127.x / ::1 / 0.0.0.0）、RFC1918 私网、link-local、
    ULA、``*.local``。**拿不到 host（空/相对路径/解析异常）一律返回 False**
    （fail-closed：无法证明是本机，就不当 (A) 放行，要走白名单）。
    """
    try:
        host = urlsplit(str(url or "")).hostname or ""
    except Exception:  # noqa: BLE001 - 畸形 URL 一律按「不是本机」处理
        return False
    host = host.strip().strip("[]").lower()
    if not host:
        return False
    if host in ("localhost", "0.0.0.0") or host.endswith(".localhost"):
        return True
    if host.endswith(".local"):
        return True
    if ":" in host:                       # IPv6 字面量
        return bool(_LOOPBACK_V6_RE.match(host)
                    or _LINKLOCAL_V6_RE.match(host)
                    or _ULA_V6_RE.match(host))
    if host == "::1":
        return True
    if _PRIVATE_IPV4_RE.match(host):
        return True
    return False


def _builtin_free_allowed(provider: str, base_url: Optional[str]) -> bool:
    """(A) 判定：该 provider **并且**（地址硬编码 或 地址确为本机/内网）才算直通源。"""
    if provider not in BUILTIN_FREE_PROVIDERS:
        return False
    if provider in _ENDPOINT_INDEPENDENT_BUILTIN:
        return True
    return _is_local_base_url(base_url)


# 白名单条目里允许当 ``provider:`` 前缀用的名字（与路由已知 provider 同名）。
# R3.8：除内核已知 provider，还要认「需 Key 登记渠道」的 10 个 provider 名 —— 否则
# ``llm.free.trusted_models=groq:*`` 里的 "groq" 不被当 provider 前缀，会被
# ``_parse_trusted_entries`` 退化成**裸 id 模式 "groq:*"**，于是白名单写了却匹配不上任何
# 模型、**静默失效**（且没有任何报错，极难排查）。故这里并入 PROVIDER_IDS。
_TRUSTED_MODEL_PROVIDERS = frozenset(KNOWN_PROVIDERS) | frozenset(PROVIDER_IDS)

# 白名单里「长得像 provider 前缀、但 provider 名不认识」的条目 → 只告警一次（按条目原文去重）。
_TRUSTED_PARSE_WARNED: set = set()
_TRUSTED_PARSE_WARN_LOCK = threading.Lock()


def _warn_unknown_trusted_provider(token: str, head: str) -> None:
    """``llm.free.trusted_models`` 里写了 ``xxx:*`` / ``xxx:yyy``，但 ``xxx`` 不认识。

    D2（静默失效）：这类条目会被 ``_parse_trusted_entries`` 退化成**裸 id** 处理，
    于是 ``xxx:*`` 里的 ``"*"`` 不生效、谁也不匹配 —— 用户以为白名单已生效，实际
    什么都没放行，却**没有任何日志**（极难排查）。

    处理原则（只加告警，不改语义）：
      · 语义仍是「按裸 id 处理」——不抛异常、不打断启动、不改已有合法条目；
      · 必须留一条可自修复的 warning，同类条目（按原文）只打一次。
    不告警的情形：``head`` 空；以及 ``rest != "*"`` 且 ``head`` 含 ``/``（如
    ``meta-llama/llama-3.3-70b-instruct:free``）—— 那是显然的带冒号裸 id，避免刷屏。
    """
    token = str(token)
    with _TRUSTED_PARSE_WARN_LOCK:
        if token in _TRUSTED_PARSE_WARNED:
            return
        _TRUSTED_PARSE_WARNED.add(token)
    logger.warning(
        '白名单条目 "%s" 的 provider "%s" 不在已知 provider 集合内，已按「裸 id」处理、'
        '"*" 不会生效（若你本意是 provider 前缀，请核对 provider 名拼写）；'
        "已知 provider: %s",
        token, head, ", ".join(sorted(_TRUSTED_MODEL_PROVIDERS)))


def _parse_trusted_entries(raw: str) -> list[tuple[str, str]]:
    """把 ``llm.free.trusted_models`` 文本解析成 ``[(provider, pattern), ...]``。

    条目写法（逗号 / 分号 / 中文逗号分隔，容忍空白与空项）：
      · ``provider:id``  精确登记某渠道的某个模型（provider 见 KNOWN_PROVIDERS）；
      · ``provider:*``   登记整个端点（该 provider 下所有 id 一并视为用户已确认）；
      · ``id``           裸 id，任意 provider 均匹配。
    返回的 provider 为空串 = 裸 id；pattern 为 ``"*"`` = 整个端点。

    只有**已知 provider 名**才当 ``provider:`` 前缀解析，否则整条按裸 id 处理 ——
    这样自带冒号的 id（如 ``meta-llama/llama-3.3-70b-instruct:free``）不会被截断。
    裸 ``*``（不带 provider 前缀）**不生效**，避免一个星号把全世界放进来。

    D2：若条目形如 ``xxx:*`` / ``xxx:yyy`` 而 ``xxx`` 不在已知 provider 集合里，仍按
    裸 id 处理（语义不变），但会打一条 ``_warn_unknown_trusted_provider`` 告警
    （同类只打一次），避免用户写了 ``foobar:*`` 却**静默失效**、无人知道。
    """
    out: list[tuple[str, str]] = []
    for tok in str(raw or "").replace(";", ",").replace("，", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        head, sep, rest = tok.partition(":")
        rest = rest.strip()
        if sep and rest and head.strip().lower() in _TRUSTED_MODEL_PROVIDERS:
            out.append((head.strip().lower(), rest))
        else:
            # D2：形如 ``xxx:*`` / ``xxx:yyy`` 但 ``xxx`` 不是已知 provider → 仍按裸 id
            # 处理（语义不变），但必须告警，否则该条目会静默失效（``"*"`` 不生效）。
            _head = head.strip()
            if sep and rest and _head and (rest == "*" or "/" not in _head):
                _warn_unknown_trusted_provider(tok, _head)
            out.append(("", tok))
    return out


@dataclass
class Candidate:
    provider: str          # openrouter / nvidia / ollama
    base_url: str
    api_key: str
    model: str
    # 健康度（运行期累计，非持久化默认）
    ok: int = 0
    fail: int = 0
    latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        n = self.ok + self.fail
        return self.ok / n if n else 0.5  # 未知先验中性


class _DefaultZeroDict(dict):
    """兼容旧测试/旧调用：缺失 key 返回 0 而不是 KeyError。"""

    def __missing__(self, key):  # noqa: D401
        return 0


class CooldownTable:
    """R3.3-32 模型冷却表。

    内部表结构：model_name -> {"unlock_at": ts, "consecutive_failures": int,
                                "last_error": str}
    冷却时长：失败一次 = COOLDOWN_BASE_SEC，连续失败指数翻倍
    （base * 2^(n-1)），封顶 COOLDOWN_MAX_SEC；成功一次即清掉整条记录。

    注意：429 限流【不走冷却】，走 chat() 内 BACKOFF_RETRY_DELAYS 同轮重试。
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None,
                 base_sec: float = COOLDOWN_BASE_SEC,
                 max_sec: float = COOLDOWN_MAX_SEC) -> None:
        self.table: dict[str, dict] = {}
        self._clock = clock or time.time
        self._base = float(base_sec)
        self._max = float(max_sec)

    def record_failure(self, model: str, error: str = "") -> float:
        """记录一次失败：连续失败计数 +1，按指数退避写冷却截止时间。

        Returns: 本次写入的冷却时长（秒）。
        """
        entry = self.table.get(model) or {
            "unlock_at": 0.0, "consecutive_failures": 0, "last_error": ""}
        n = entry["consecutive_failures"] + 1
        cooldown_sec = min(self._base * (2 ** (n - 1)), self._max)
        entry["consecutive_failures"] = n
        entry["unlock_at"] = self._clock() + cooldown_sec
        entry["last_error"] = str(error)[:200]
        self.table[model] = entry
        return cooldown_sec

    def record_success(self, model: str) -> None:
        """一次成功：整条冷却记录清除（恢复可选）。"""
        self.table.pop(model, None)

    def is_cooled_down(self, model: str,
                       now: Optional[float] = None) -> bool:
        """该模型当前是否在冷却期内（未到 unlock_at）。"""
        entry = self.table.get(model)
        if not entry:
            return False
        t = self._clock() if now is None else now
        return t < entry["unlock_at"]

    def snapshot(self) -> list[dict]:
        """供 UI 显示：哪些模型在冷却、还剩多久、最近一次错误。"""
        now = self._clock()
        out: list[dict] = []
        for model, e in self.table.items():
            out.append({
                "model": model,
                "unlock_at": e["unlock_at"],
                "remaining_sec": max(0.0, round(e["unlock_at"] - now, 1)),
                "consecutive_failures": e["consecutive_failures"],
                "last_error": e["last_error"],
            })
        out.sort(key=lambda x: x["remaining_sec"], reverse=True)
        return out


class FreeModelRouter:
    """免费/低成本模型自动路由。所有方法对「未配置 / 无网络」安全降级。"""

    def __init__(self,
                 config: Optional[dict] = None,
                 http_get: Optional[Callable[..., str]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 local_scanner=None,
                 desktop_connector=None,
                 browser_hub=None,
                 web_search=None,
                 routing_matrix=None,
                 auto_matrix: bool = True) -> None:
        self.config = config or {}
        # P0 卡2：可选注入的模型降级路由矩阵（L0-L3 × 绿黄红黑 + NYSE 熔断）。
        # - 传了 routing_matrix：持有注入实例（测试用 fake / 自定义 clock）；
        # - 没传但 auto_matrix=True（默认）：懒加载模块级默认实例，自动走
        #   data/.state/circuit_breaker.json 落盘，生产开箱即用；
        # - 没传且 auto_matrix=False：纯旧行为，不接矩阵（回归测试用）。
        self._routing_matrix = routing_matrix
        self._auto_matrix_enabled = auto_matrix
        # 运行期健康表：key=(provider,model) -> Candidate
        self._health: dict[tuple, Candidate] = {}
        # HTTP / 时钟可注入（离线测试用），默认走标准库 / 真实时间
        self._http = http_get or _http_get
        self._clock = clock or time.time
        # 退避 sleep 可注入（测试用 mock，不真睡）
        self._sleep: Callable[[float], None] = time.sleep
        # 动态目录缓存：source -> {"models":[], "ts":float, "ok":bool, "error":str}
        # （R3.6 起还带价格**证据**两个集合：
        #   · "paid_ids"：全量目录里被**正面证明**为付费的 id（price_gate_verdict 第 1 条）；
        #   · "free_ids"：全量目录里被**正面证明**五维全 0 的 id（第 3 条 (B)）；
        #   只有 openrouter 源会带非空证据——只有它的目录响应含 pricing 字段。）
        self._catalog: dict[str, dict] = {}
        # -------- R3.6/R3.7 fail-closed：价格证据通道 --------
        # 目录抓取器把本次全量响应里的付费 id 暂存到这里（**按线程**分槽：
        # thread_ident -> set，避免两个线程并发刷新时互相覆盖导致证据丢失），
        # refresh_catalog() 取走后写进 _catalog[source]["paid_ids"]；取不到（空）
        # = 没有付费证据（注意：**不再等于放行**，见 price_gate_verdict）。
        self._or_paid_ids_scratch: dict = {}
        # 价格门拒绝告警去重：同一 "provider:model" 只打一次，避免每次
        # candidates()/ranked() 都刷屏。_paid_reject_logged 是 R3.6 的名字，
        # 指向同一份集合（保持向后兼容，同一条 id 永远只告警一次）。
        self._price_gate_logged: set = set()
        self._paid_reject_logged: set = self._price_gate_logged
        # ---- R3.3-30/32 韧性状态 ----
        # 冷却表（5xx 熔断）：CooldownTable 是唯一真源；
        # 下面的 _cooldown / _consecutive_5xx 是兼容旧测试的只读视图（property）
        self._cd = CooldownTable(clock=self._clock)
        # 401/403 永久禁用（直到重启）的 model_name 集合
        self._disabled: set[str] = set()
        # R3.3-31 启动探活结果：model_name -> True(alive)/False(dead)；缺省 = unknown
        self._alive: dict[str, bool] = {}
        # ---- 零成本中枢四能力（v3.2 接线）：可注入假对象做离线确定性测试 ----
        # 为 None 时首次用到才懒构造（避免 import 副作用 / 拖慢启动）。
        self._local_scanner = local_scanner
        self._desktop_connector = desktop_connector
        self._browser_hub = browser_hub
        self._web_search = web_search
        # ---- R3.4 路由内核（动态惩罚 / 滑动窗口限流 / 429 冷却升级 / Sticky / 跳过集合）----
        # 线程安全：llm_gateway 是单例，可能被多线程调用；各内核组件自带锁，
        # 这里再加一把实例锁，护住「读快照 / 改状态」这几段复合读改写。
        self._lock = threading.RLock()
        # 只读快照模式开关：routing_snapshot() 期间置 True，禁止刷新目录 / 扫地 / 联网，
        # 保证设置页「只读展示、不联网、不改路由决策」的契约（见 _catalog_frozen）。
        self._catalog_frozen = False
        self._state_store = PersistentStateStore(self._state_db_path())
        self._penalty = PenaltyTable(clock=self._clock)
        self._ledger = RateLimitLedger(clock=self._clock, store=self._state_store)
        self._limits = RateLimiter(self._ledger,
                                   specs=build_limit_specs(self.config),
                                   clock=self._clock)
        self._cd429 = Cooldown429(clock=self._clock, store=self._state_store)
        self._sticky = StickySessionStore(clock=self._clock,
                                          store=self._state_store,
                                          ttl_sec=STICKY_TTL_SEC)
        # 最近一次实际命中的通道（provider:model），对齐 freellmapi 的 x-routed-via
        self._last_routed_via = ""

    # ------------------------------------------------------------------ #
    # 配置小工具
    # ------------------------------------------------------------------ #
    def _cfg(self, key: str, default: str = "") -> str:
        v = self.config.get(key, default)
        return v if isinstance(v, str) else str(v)

    def _flag(self, key: str, default: bool = True) -> bool:
        return str(self.config.get(key, "true" if default else "false")).strip().lower() not in (
            "false", "0", "no", "off")

    def _ttl_seconds(self) -> float:
        try:
            return float(self._cfg("llm.free.catalog_ttl_hours",
                                   str(DEFAULT_CATALOG_TTL_HOURS))) * 3600.0
        except ValueError:
            return DEFAULT_CATALOG_TTL_HOURS * 3600.0

    def _timeout(self) -> float:
        try:
            return float(self._cfg("llm.free.http_timeout", str(DEFAULT_HTTP_TIMEOUT)))
        except ValueError:
            return DEFAULT_HTTP_TIMEOUT

    # ------------------------------------------------------------------ #
    # 零成本中枢四能力（v3.2）：本地扫描 / 桌面AI / 浏览器AI / 网页搜索
    # ------------------------------------------------------------------ #
    def _get_local_scanner(self):
        if self._local_scanner is None:
            from services.local_ai_scanner import LocalAIScanner
            self._local_scanner = LocalAIScanner(self.config)
        return self._local_scanner

    def _get_desktop_connector(self):
        if self._desktop_connector is None:
            from services.desktop_ai_connector import DesktopAIConnector
            self._desktop_connector = DesktopAIConnector(self.config)
        return self._desktop_connector

    def _get_browser_hub(self):
        if self._browser_hub is None:
            from services.browser_ai_hub import BrowserAIHub
            self._browser_hub = BrowserAIHub(self.config)
        return self._browser_hub

    def _get_web_search(self):
        if self._web_search is None:
            from services.web_search_service import WebSearchService
            self._web_search = WebSearchService(config=self.config)
        return self._web_search

    def _collect_local_scan_models(self) -> list[Candidate]:
        """跑 LocalAIScanner.scan()，把可用的 OpenAI 兼容端点注册为零成本候选。
        单源失败即跳过（不影响其它渠道），结果按 TTL 缓存进 _catalog["local_scan"]。"""
        out: list[Candidate] = []
        # 扫描默认开（开箱即生效，与设置页「本地扫描」初始勾选一致）；
        # 用户可在设置页关闭（写 llm.free.local_scan.enabled=false）。
        cached = self._catalog.get("local_scan")
        if not (self._flag("llm.free.local_scan.enabled", True)):
            return out
        if self._catalog_frozen:
            # 只读快照模式：不跑扫描，只回已注册候选（不联网、不耗资源）
            return [c for c in self._health.values() if c.provider == "local_scan"]
        now = self._clock()
        ttl = self._ttl_seconds()
        if cached and cached.get("ok") and (now - cached.get("ts", 0) < ttl):
            for u in cached.get("usable", []):
                out.append(self._get("local_scan", u["base_url"],
                                     u.get("key", "sk-local"), u["model"]))
            return out
        try:
            result = self._get_local_scanner().scan() or {}
            usable = result.get("usable_models", []) or []
            norm = []
            for u in usable:
                base = (u.get("base_url") or "").rstrip("/")
                model = u.get("model")
                if not base or not model:
                    continue
                # base_url 可能不带 /v1；统一补全以贴合 OpenAI SDK
                if not base.endswith("/v1"):
                    base = base + "/v1"
                norm.append({"base_url": base, "model": model,
                             "key": "sk-local" if not u.get("key_present") else "sk-local-cloud"})
                out.append(self._get("local_scan", base,
                                     "sk-local" if not u.get("key_present") else "sk-local-cloud",
                                     model))
            self._catalog["local_scan"] = {
                "usable": norm, "models": [n["model"] for n in norm],
                "ts": now, "ok": bool(norm),
                "error": "" if norm else "扫描未发现可直接调用的本地模型"}
        except Exception as e:  # 扫描任何异常 → 跳过，不阻断
            logger.info("本地AI扫描跳过: %s", e)
            self._catalog["local_scan"] = {
                "usable": (cached or {}).get("usable", []),
                "models": (cached or {}).get("models", []),
                "ts": now, "ok": bool((cached or {}).get("usable")),
                "error": f"{type(e).__name__}: {e}"}
            for u in (cached or {}).get("usable", []):
                out.append(self._get("local_scan", u["base_url"], u.get("key", "sk-local"), u["model"]))
        return out

    def _collect_desktop_models(self) -> list[Candidate]:
        """桌面 AI 连接器（Trae/WorkBuddy 登录态）。
        只有发现到已安装 App 且其 shim 暴露了 OpenAI 兼容 base_url 时才注册；
        本机未装这些程序 → discover_all() 返回空 → 0 候选（如实 503，不假绿）。"""
        out: list[Candidate] = []
        # 引擎层缺省关；设置页「桌面AI连接器」开关默认勾选，保存后写 config 生效。
        if not self._flag("llm.free.desktop_ai.enabled", False):
            return out
        if self._catalog_frozen:
            # 只读快照模式：不发现/不探测，只回已注册候选
            return [c for c in self._health.values() if c.provider == "desktop"]
        try:
            descs = self._get_desktop_connector().discover_all() or []
        except Exception as e:
            logger.info("桌面AI连接器发现跳过: %s", e)
            self._catalog["desktop_ai"] = {"models": [], "ts": self._clock(),
                                           "ok": False,
                                           "error": f"{type(e).__name__}: {e}"}
            return out
        registered = 0
        for d in descs:
            base = getattr(d, "openai_base_url", "") or ""
            models = getattr(d, "models", []) or []
            if not base or not models:
                # 发现到 App 但 shim 未起 / 无 openai_base_url → 不注册（数字诚实）
                continue
            for m in models:
                out.append(self._get("desktop", base, "sk-desktop", m))
                registered += 1
        self._catalog["desktop_ai"] = {
            "models": [getattr(d, "source_id", "?") for d in descs],
            "ts": self._clock(), "ok": bool(registered),
            "error": "" if registered else
                     ("已安装桌面AI但shim未提供OpenAI端点" if descs
                      else "未发现 Trae/WorkBuddy 等桌面AI客户端（503 不可用）")}
        return out

    # ------------------------------------------------------------------ #
    # 本机桥接自动发现
    # ------------------------------------------------------------------ #
    def _bridge_probe_urls(self) -> list[str]:
        raw = self._cfg("llm.free.bridge_probe_urls")
        if raw:
            return [x.strip() for x in raw.split(",") if x.strip()]
        return list(DEFAULT_BRIDGE_PROBE_URLS)

    def _bridge_timeout(self) -> float:
        try:
            return float(self._cfg("llm.free.bridge_probe_timeout",
                                   str(DEFAULT_BRIDGE_PROBE_TIMEOUT)))
        except ValueError:
            return DEFAULT_BRIDGE_PROBE_TIMEOUT

    def _probe_endpoint(self, base: str) -> list[str]:
        """GET {base}/models，返回模型 id 列表；端点不在线/非兼容则抛异常。"""
        body = self._http(base.rstrip("/") + "/models",
                          timeout=self._bridge_timeout(),
                          headers={"Accept": "application/json"})
        data = json.loads(body).get("data", [])
        return sorted({str(m.get("id")) for m in data if m.get("id")})

    def _fetch_local_bridges(self) -> list[dict]:
        """逐候选端口探测；单端口失败静默跳过，返回在线端点 [{base, models}]。"""
        endpoints: list[dict] = []
        for base in self._bridge_probe_urls():
            try:
                models = self._probe_endpoint(base)
            except Exception as e:  # 端口未监听/非兼容：跳过，不影响其它端口
                logger.info("本地桥探测跳过 %s: %s", base, e)
                continue
            if models:
                endpoints.append({"base": base, "models": models})
        return endpoints

    def _ollama_api_base(self) -> str:
        """从 ollama OpenAI base(http://host:11434/v1) 推出原生 /api 基址(http://host:11434)。"""
        base = self._cfg("llm.free.ollama_base", "http://localhost:11434/v1")
        return base.rstrip("/").removesuffix("/v1")

    def _discover_local_ollama_models(self) -> list[str]:
        """best-effort GET {base}/api/tags，返回本机实际已安装的 ollama 模型名列表。

        任何异常（ollama 未起/超时/非兼容）都静默返回 []，由调用方回退默认名。
        这样以后模型名变了也不会再出现「默认名对不上 -> 404 model not found」。"""
        try:
            url = self._ollama_api_base() + "/api/tags"
            body = self._http(url, timeout=1.5,
                              headers={"Accept": "application/json"})
            data = json.loads(body)
            return sorted({str(m.get("name")) for m in data.get("models", [])
                           if m.get("name")})
        except Exception as e:  # noqa: BLE001
            logger.info("ollama /api/tags 探测失败，回退默认模型名: %s", e)
            return []

    def _ollama_model_list(self) -> list[str]:
        """免费渠道的 ollama 候选模型顺序：显式 config > 本机实探(小快优先) > 默认名。"""
        explicit = self._cfg("llm.free.ollama_models")
        if explicit:
            return [x.strip() for x in explicit.split(",") if x.strip()]
        discovered = self._discover_local_ollama_models()
        if discovered:
            # 已知顺序：小快(05b)优先、大稳(15b)其次；其它本机模型追加在后
            preferred = ("loveai-qwen05b:latest", "loveai-qwen15b:latest")
            ordered = [m for m in preferred if m in discovered]
            ordered += [m for m in discovered if m not in ordered]
            return ordered
        return list(_OLLAMA_DEFAULT_MODELS)

    # ------------------------------------------------------------------ #
    # 动态目录拉取（每源返回模型 id 列表；失败抛异常，由 refresh 捕获）
    # ------------------------------------------------------------------ #
    def _fetch_openrouter_index(self) -> tuple:
        """拉 OpenRouter /models **全量**响应 → (严格免费 id 列表, 正面证明付费的 id 集合)。

        免责列表口径与改动前逐字节一致（`:free` 后缀 或 五项计价全 0）；
        「付费集合」是 R3.6 新增的**正面证据**，R3.7 起是价格门里**最高优先级**的拒绝
        理由（见 _proven_paid_pricing / price_gate_verdict 第 1 条）：pricing 缺失 /
        不可解析的一律不进集合 = 该维度「没有付费证据」；注意 R3.7 下「没有付费证据」
        **不等于放行**（默认拒绝），无证据的候选要靠 (A)(B)(C) 之一才进得来。
        """
        body = self._http(OPENROUTER_MODELS_URL, timeout=self._timeout(),
                          headers={"Accept": "application/json"})
        data = json.loads(body).get("data", [])
        free: set[str] = set()
        paid: set[str] = set()
        for m in data:
            mid = str(m.get("id", ""))
            if not mid or mid == "openrouter/free":
                continue  # 跳过聚合路由本身，避免重复
            p = m.get("pricing", {}) or {}
            # 严格免费口径（fail-closed 硬约束）：prompt / completion / request /
            # input_cache_read / input_cache_write **五项全为 0** 才算免费，任一项
            # 非 0 即不算。字段缺失按未计费处理（默认 "0"）；数值解析不出来 = 不
            # 确定 = 拒绝（不收进候选）。:free 后缀是 OpenRouter 自带的免费标记，
            # 沿用项目既有口径作为等价豁免。此判断只会**收窄**候选集，绝不会放宽。
            if _proven_paid_pricing(p):
                paid.add(mid)           # R3.6：价格门第 1 条「正面付费证据」
            if mid.endswith(":free") or _strictly_free_pricing(p):
                free.add(mid)
        return sorted(free), paid

    def _fetch_openrouter_free(self) -> list[str]:
        """兼容包装：只返回严格免费的 id 列表（既有调用方/测试直接调它）。

        返回值同时是 R3.7 价格门的 **(B) 正面免费证据**：refresh_catalog() 会把它原样
        存进 ``_catalog["openrouter"]["free_ids"]``（只有 openrouter 的抓取器做了 pricing
        解析，所以只有它的免费证据非空）。

        副作用：把本次的「正面付费证据」暂存到**本线程**的槽位，由 refresh_catalog()
        取走写入目录缓存。被测试打桩替换时不会产生证据 = 该维度「无证据」——
        R3.7 下「无证据」不再等于放行（默认拒绝），要靠白名单或 :free 后缀进来。
        """
        ids, paid = self._fetch_openrouter_index()
        with self._lock:
            self._or_paid_ids_scratch[threading.get_ident()] = set(paid)
        return ids

    def _fetch_nvidia(self) -> list[str]:
        key = self._cfg("llm.free.nvidia_key")
        if key:
            body = self._http(NVIDIA_MODELS_URL, timeout=self._timeout(),
                              headers={"Authorization": f"Bearer {key}",
                                       "Accept": "application/json"})
            data = json.loads(body).get("data", [])
            return sorted({str(m.get("id")) for m in data if m.get("id")})
        if self._flag("llm.free.nvidia_catalog_md", True):
            body = self._http(NVIDIA_CATALOG_MD_URL, timeout=self._timeout())
            return _parse_nvidia_catalog_md(body)
        return []

    def refresh_catalog(self, force: bool = False) -> dict:
        """拉取并缓存动态目录，按 TTL 决定是否复用；单源失败不影响其它源与回退。

        Returns 每源状态快照（ok / count / ts / error）。
        """
        if not self._flag("llm.free.auto_catalog", True):
            return self.catalog_status()
        if self._catalog_frozen:
            # 只读快照模式（routing_snapshot 期间）：只用现有缓存，绝不联网刷新
            return self.catalog_status()
        now = self._clock()
        ttl = self._ttl_seconds()
        sources = {
            "openrouter": self._fetch_openrouter_free,
            "nvidia": self._fetch_nvidia,
        }
        for name, fetcher in sources.items():
            cached = self._catalog.get(name)
            if not force and cached and cached.get("ok") and (now - cached.get("ts", 0) < ttl):
                continue  # 缓存仍新鲜
            try:
                with self._lock:
                    # R3.6：清掉本线程的槽位，抓取器会填「正面付费证据」
                    self._or_paid_ids_scratch.pop(threading.get_ident(), None)
                models = fetcher()
                with self._lock:
                    paid_ids = set(self._or_paid_ids_scratch.pop(
                        threading.get_ident(), None) or ())
                # R3.7：目录返回的「严格免费 id 列表」本身就是**正面免费证据**
                # （free_ids）。只有 openrouter 的抓取器会做 pricing 解析，所以
                # 也只有它的 free_ids 非空；nvidia 的 /v1/models 不含 pricing →
                # free_ids 恒为空 → 它的候选只能靠白名单放行（这正是本次修复点）。
                free_ids = set(models) if name == "openrouter" else set()
                if models:
                    self._catalog[name] = {"models": models, "ts": now,
                                           "ok": True, "error": "",
                                           "paid_ids": paid_ids,
                                           "free_ids": free_ids}
                else:
                    # 拉取成功但目录为空：保留旧缓存，标记本次为空
                    self._catalog[name] = {
                        "models": (cached or {}).get("models", []),
                        "ts": (cached or {}).get("ts", now),
                        "ok": bool((cached or {}).get("models")),
                        "error": "目录为空",
                        "paid_ids": (cached or {}).get("paid_ids", set()),
                        "free_ids": (cached or {}).get("free_ids", set())}
            except Exception as e:  # 网络/解析失败：保留旧缓存，标记失败
                logger.info("动态目录 %s 拉取失败，将回退: %s", name, e)
                self._catalog[name] = {
                    "models": (cached or {}).get("models", []),
                    "ts": (cached or {}).get("ts", now),
                    "ok": bool((cached or {}).get("models")),
                    "error": f"{type(e).__name__}: {e}",
                    # 拉取失败时**沿用**已取得的证据（它们来自一次成功的全量响应，
                    # 属既有证据而非本次新增）：付费证据沿用 → 断网也不会让已证明
                    # 付费的 id 复活；免费证据沿用 → 断网也不会把已证明免费的 id
                    # 误杀成「无证据」（R3.7 默认取向是拒绝，证据越少拦得越狠）。
                    "paid_ids": (cached or {}).get("paid_ids", set()),
                    "free_ids": (cached or {}).get("free_ids", set())}

        # 本机桥接（结果结构含 endpoints；单端口探测失败已在内部吞掉）
        if self._flag("llm.free.auto_discover_bridge", True):
            lb_cached = self._catalog.get("local_bridge")
            lb_fresh = (not force and lb_cached and lb_cached.get("ok")
                        and (now - lb_cached.get("ts", 0) < ttl))
            if not lb_fresh:
                try:
                    eps = self._fetch_local_bridges()
                    flat = sorted({m for e in eps for m in e["models"]})
                    self._catalog["local_bridge"] = {
                        "endpoints": eps, "models": flat, "ts": now,
                        "ok": bool(eps),
                        "error": "" if eps else "未发现本机 OpenAI 兼容桥接"}
                except Exception as e:
                    self._catalog["local_bridge"] = {
                        "endpoints": (lb_cached or {}).get("endpoints", []),
                        "models": (lb_cached or {}).get("models", []),
                        "ts": (lb_cached or {}).get("ts", now),
                        "ok": bool((lb_cached or {}).get("endpoints")),
                        "error": f"{type(e).__name__}: {e}"}
        return self.catalog_status()

    def catalog_status(self) -> dict:
        return {name: {"ok": bool(c.get("ok")),
                        "count": len(c.get("models", [])),
                        "ts": c.get("ts"),
                        "error": c.get("error", "")}
                for name, c in self._catalog.items()}

    # ------------------------------------------------------------------ #
    # R3.7 价格门（fail-closed 硬闸）：默认拒绝 + 显式白名单放行
    # ------------------------------------------------------------------ #
    def trusted_models_raw(self) -> str:
        """白名单配置项 ``llm.free.trusted_models`` 的原始文本（取不到 = 空串）。"""
        try:
            raw = self.config.get(TRUSTED_MODELS_KEY, "")
        except Exception:  # noqa: BLE001 - 配置源异常按「没登记」处理（= 更严）
            raw = ""
        return raw if isinstance(raw, str) else str(raw or "")

    def trusted_models(self) -> list[str]:
        """白名单清单（去空白、去重、保序）——供 routing_snapshot / 设置页 / 诊断展示。"""
        seen: set[str] = set()
        out: list[str] = []
        for tok in (self.trusted_models_raw()
                    .replace(";", ",").replace("，", ",").split(",")):
            tok = tok.strip()
            if tok and tok not in seen:
                seen.add(tok)
                out.append(tok)
        return out

    def _trusted_pattern_matches(self, provider: str, model: str) -> bool:
        """该 ``provider:model`` 是否命中用户白名单（(C) 显式登记）。"""
        prov = (provider or "").strip().lower()
        for want_provider, pattern in _parse_trusted_entries(self.trusted_models_raw()):
            if want_provider and want_provider != prov:
                continue
            if pattern == "*":
                # 只有 provider:* 才生效；裸 * 不放行任何东西（防一票全放）
                if want_provider:
                    return True
                continue
            if pattern == model:
                return True
        return False

    def _evidence_ids(self, provider: str, field: str) -> set:
        """目录价格**证据**集（field: ``"paid_ids"`` / ``"free_ids"``）。

        取不到 = 空集 = 该维度**没有证据**。只读用途，直接返回底层 set（调用方只做
        ``in`` 判断），避免每次判定都拷贝一遍几百条 id 的目录集合。
        """
        try:
            with self._lock:
                entry = self._catalog.get(provider) or {}
                ids = entry.get(field)
        except Exception:  # noqa: BLE001 - 证据读不到 = 没证据（fail-closed 方向）
            return set()
        return ids if isinstance(ids, set) else set(ids or ())

    def _proven_paid_ids(self, provider: str) -> set:
        """被公开价格目录**正面证明**为付费的 id 集合（pricing 任一项非 0）。"""
        return self._evidence_ids(provider, "paid_ids")

    def _proven_free_ids(self, provider: str) -> set:
        """被公开价格目录**正面证明**为免费的 id 集合（五维全 0）。"""
        return self._evidence_ids(provider, "free_ids")

    def price_gate_verdict(self, provider: str, model: str,
                           base_url: Optional[str] = None) -> tuple:
        """价格门判定：返回 ``(是否放行, 原因码)`` —— 本模块**唯一**的放行/拒绝判据。

        ``base_url`` 是可选追加参数（不传则 (A) 里的「本机」类 provider 因无法确认
        地址而**不放行**，fail-closed）。

        判定顺序（不可颠倒，正面付费证据永远最高优先级）：
          1. ``proven_paid``  —— 公开价格目录正面证明该 id 收费（五维任一项非 0）
                                 → 拒绝；**白名单也不得翻案**（用户声明不能覆盖价格事实）；
          2. ``builtin_free`` —— (A) 内置本机/零配置直通源，**且**地址确为本机/内网
                                 （或地址由本模块硬编码）→ 放行（不产生第三方计费）；
          3. ``proven_free``  —— (B) 目录五维全 0（free_ids）或 id 以 ``:free`` 结尾 → 放行；
          4. ``trusted``      —— (C) 命中 ``llm.free.trusted_models`` 白名单 → 放行
                                 （用户自担风险的显式声明）；
          5. ``endpoint_not_local`` / ``default_deny`` —— 其余**一律拒绝**（fail-closed：
             拿不到免费证据就不放行）。``endpoint_not_local`` 专指「provider 名是内置
             本机源，但 base_url 不在本机/内网」——典型是把 ``llm.free.ollama_base`` /
             ``llm.free.bridge_probe_urls`` 指到了公网付费端点；这种也必须白名单才放行。
        """
        prov = (provider or "").strip().lower()
        mid = str(model or "")
        if mid and mid in self._proven_paid_ids(prov):
            return False, "proven_paid"
        if _builtin_free_allowed(prov, base_url):
            return True, "builtin_free"
        if mid and (mid.endswith(":free") or mid in self._proven_free_ids(prov)):
            return True, "proven_free"
        if mid and self._trusted_pattern_matches(prov, mid):
            return True, "trusted"
        if prov in BUILTIN_FREE_PROVIDERS:
            # 名字是内置本机源，但地址不是本机 → 不能当 (A)；落到默认拒绝（可白名单）
            return False, "endpoint_not_local"
        return False, "default_deny"

    def _price_gate_allows(self, provider: str, model: str,
                           base_url: Optional[str] = None) -> bool:
        """``price_gate_verdict`` 的布尔包装（放行 = True）。"""
        return self.price_gate_verdict(provider, model, base_url)[0]

    def _log_price_gate_deny(self, provider: str, model: str, reason: str) -> None:
        """价格门拒绝告警：同一 ``provider:model`` 只打一次（避免每次 candidates() 刷屏）。

        告警必须**可读、可执行**：说清是 fail-closed 默认拒绝、原因码是什么、
        以及「如确认其免费，就把 id 加进哪个白名单配置项、怎么写」。
        """
        tag = f"{provider}:{model}"
        with self._lock:
            if tag in self._price_gate_logged:
                return
            self._price_gate_logged.add(tag)
        if reason == "proven_paid":
            hint = ("公开价格目录已证明它收费（pricing 任一项非 0），白名单也不得翻案；")
        elif reason == "endpoint_not_local":
            hint = ("provider 名虽是内置本机源，但它的 base_url 不在本机/内网"
                    "（例如 llm.free.ollama_base / llm.free.bridge_probe_urls 被指到了"
                    "公网端点），无法证明不产生第三方计费；")
        else:
            hint = ("没有拿到任何免费证据（公开目录未覆盖 / 目录拉取失败 / "
                    "该端点不公开价目表）；")
        logger.warning(
            "免费通道 %s 已拒绝（fail-closed 价格门 %s，默认拒绝）：%s如确认其确为免费，"
            "请把 id 加入白名单配置项 %s 后重试（写法：provider:id / provider:* / 裸 id）",
            tag, reason, hint, TRUSTED_MODELS_KEY)

    def _reject_proven_paid(self, source: str, models: list[str]) -> list[str]:
        """R3.6 兼容包装：剔除「被正面证明为付费」的配置项。

        R3.7 起**唯一的权威判据**是 price_gate_verdict()（见 candidates() 末尾的
        「价格门最终拦网」）；这里保留是因为显式 config 那一级提前剔除可以少建
        Candidate 对象，而且告警与价格门共用同一份去重表（同一条 id 永远只告警一次）。
        """
        if not models:
            return models
        paid = self._proven_paid_ids(source)
        if not paid:
            return models
        kept: list[str] = []
        for m in models:
            if m in paid:
                self._log_price_gate_deny(source, m, "proven_paid")
                continue
            kept.append(m)
        return kept

    def _models_for(self, source: str, cfg_key: str,
                    static: list[str]) -> list[str]:
        """三级回退：显式 config 模型 > 动态目录 > 内置静态目录。

        R3.6：显式 config 这一级先过「正面付费证据」闸（_reject_proven_paid，少建对象）。
        R3.7：三条回退**都**不再自带放行语义 —— 真正决定放行/拒绝的是
        candidates() 末尾的价格门最终拦网（price_gate_verdict）：动态目录那一级
        也要过门（NVIDIA 的 /v1/models 不返回 pricing → 无免费证据 → 默认拒绝），
        内置静态目录同理（`_OPENROUTER_FREE` 靠 ``:free`` 后缀走 (B) 放行）。
        """
        explicit = self._cfg(cfg_key)
        if explicit:
            models = [x.strip() for x in explicit.split(",") if x.strip()]
            return self._reject_proven_paid(source, models)
        dynamic = self._catalog.get(source, {})
        if dynamic.get("ok") and dynamic.get("models"):
            return list(dynamic["models"])
        return list(static)

    # ------------------------------------------------------------------ #
    # 候选构造
    # ------------------------------------------------------------------ #
    def candidates(self) -> list[Candidate]:
        """按配置/动态目录生成可用候选；未配 key / 未开启的渠道不入选。

        R3.7：出口处统一过**价格门最终拦网**（fail-closed 硬闸，见 price_gate_verdict）
        —— 拿不到免费证据的候选一律不出这道门，因此 candidates() 的返回值就是
        「本模块允许出站的全部通道」，ranked()/chat()/routing_snapshot() 都只看到它。
        """
        out: list[Candidate] = []
        enabled = self._flag("llm.free.enabled", True)
        if not enabled:
            return out

        # 惰性刷新动态目录（受 TTL；失败静默回退）
        self.refresh_catalog()

        # 1) OpenRouter（需 key；模型来自 动态/config/静态）
        or_key = self._cfg("llm.free.openrouter_key")
        if or_key:
            for m in self._models_for("openrouter",
                                      "llm.free.openrouter_models",
                                      _OPENROUTER_FREE):
                out.append(self._get("openrouter", "https://openrouter.ai/api/v1",
                                     or_key, m))

        # 2) NVIDIA NIM（需 key；模型来自 动态/config/静态）
        nv_key = self._cfg("llm.free.nvidia_key")
        if nv_key:
            for m in self._models_for("nvidia",
                                      "llm.free.nvidia_models",
                                      _NVIDIA_NIM_FREE):
                out.append(self._get("nvidia", "https://integrate.api.nvidia.com/v1",
                                     nv_key, m))

        # 3) 本地 Ollama（无需 key；候选模型取「显式config > 本机/api/tags实探 > 默认名」，
        #    走 OpenAI 兼容 /v1/chat/completions（自动套 qwen chat template），不走裸 prompt）
        ollama_base = self._cfg("llm.free.ollama_base", "http://localhost:11434/v1")
        if self._flag("llm.free.include_ollama", True):
            for m in self._ollama_model_list():
                out.append(self._get("ollama", ollama_base, "ollama", m))

        # 3.5) 自动发现的本机 OpenAI 兼容桥（WorkBuddy 积分桥 / one-api 等，零第三方 key）
        for ep in self._catalog.get("local_bridge", {}).get("endpoints", []):
            for m in ep.get("models", []):
                out.append(self._get("local_bridge", ep["base"],
                                     self._cfg("llm.free.bridge_key", "sk-local"), m))

        # 3.6) 本机已装 AI 扫描结果（LocalAIScanner：Ollama/新点/Cherry 等 OpenAI 端点）
        out.extend(self._collect_local_scan_models())

        # 3.7) 桌面 AI 客户端（Trae/WorkBuddy 登录态 shim；未安装→0候选/503）
        out.extend(self._collect_desktop_models())

        # 4) 自定义本机/自建 OpenAI 兼容端点（DSH loopback 端口、one-api、New API、
        #    或桥接了 WorkBuddy/Trae 积分的本地代理）——填地址即用，无需逆向桌面 app
        custom_base = self._cfg("llm.free.custom_base")
        if custom_base:
            custom_models = self._cfg("llm.free.custom_models") or self._cfg("llm.free.custom_model")
            if custom_models:
                for m in [x.strip() for x in custom_models.split(",") if x.strip()]:
                    out.append(self._get("custom", custom_base,
                                         self._cfg("llm.free.custom_key", "sk-local"), m))

        # 5) 零配置免费 HTTP provider（pollinations/llm7，免 Key 实测 chat 200 真出文本）
        #    与 openrouter/nvidia 不同：无需 key 开箱即用；默认开，可用
        #    llm.free.zero_config.enabled=false 关闭。模型名可用
        #    llm.free.pollinations_models / llm.free.llm7_models 逗号分隔覆盖。
        if self._flag("llm.free.zero_config.enabled", True):
            for entry in _ZERO_CONFIG_API:
                override = self._cfg(f"llm.free.{entry['provider']}_models")
                models = ([x.strip() for x in override.split(",") if x.strip()]
                          if override else list(entry["models"]))
                for m in models:
                    out.append(self._get(entry["provider"], entry["base_url"],
                                         "zero-config", m))

        # 6) 「需 Key」登记渠道（services/free_key_channels.py 描述符）——填了 key 才产候选。
        #    模型三级回退与 openrouter/nvidia 同构，**必须借道 _models_for**，这样它的
        #    产出照样要过下面第 7 步的出口价格门（本模块唯一放行/拒绝判据）。
        #    缺必填配置（如 cloudflare 缺 account_id）或 base_url 仍含未替换占位 ``{..}``
        #    的整条跳过 —— 绝不把坏地址塞进候选。
        #    ⚠️ 这 10 条**都没有目录抓取器** → 价格证据集（free_ids / paid_ids）恒空 →
        #    它们只会落到 default_deny：即「填了 key 但没进白名单」= 0 候选、0 出站
        #    （刻意的双闸，不是 bug）；要真正进池还须把 id 登记进
        #    llm.free.trusted_models（例：groq:*）。
        if self._flag("llm.free.keychannels.enabled", True):
            for channel in channels_with_keys(self.config):
                if channel.get("missing_config"):
                    continue
                base = channel.get("base_url_resolved") or ""
                if not base or "{" in base:
                    continue
                provider = channel["provider"]
                for m in self._models_for(provider,
                                          channel["models_config"],
                                          channel["default_models"]):
                    out.append(self._get(provider, base, channel["api_key"], m))

        # 7) R3.7 价格门**最终拦网**（fail-closed 硬闸）——唯一放行/拒绝判据
        #
        #    无论上面哪一条来源把候选放进 out，都必须在这里过 price_gate_verdict：
        #    只有 (A) 内置本机/零配置直通源、(B) 正面免费证据（五维全 0 / :free 后缀）、
        #    (C) 用户显式白名单 三者之一才留得下来；**其余一律剔除**并打一条可读告警
        #    （含原因码 + 白名单配置项名）。
        #
        #    放在最后而不是分散到各来源，是为了让「放行/拒绝」只有一个可审计的判据：
        #    以后无论谁再加渠道，都只能从这道门出去。被剔除的候选既不进 ranked()/
        #    chat()，也不产生任何出站调用（出站调用是 chat() 里按候选发起的）。
        admitted: list[Candidate] = []
        for c in out:
            ok, reason = self.price_gate_verdict(c.provider, c.model, c.base_url)
            if ok:
                admitted.append(c)
            else:
                self._log_price_gate_deny(c.provider, c.model, reason)
        return admitted

    def _get(self, provider: str, base_url: str, key: str, model: str) -> Candidate:
        k = (provider, model)
        if k not in self._health:
            self._health[k] = Candidate(provider, base_url, key, model)
        return self._health[k]

    def ranked(self, task_color=None) -> list[Candidate]:
        """排序：可用候选按「实际排序分 → 成功率降序 → 延迟升序」；未知渠道排在
        已知失败之后、已知成功之前。Ollama 本地默认最优先（零成本、无配额）。

        R3.3-30/31/32：
        - dead（探活失败）/ permanently disabled（401/403）/ cooldown 未到期 → 不入选；
        - alive=True 的候选整体排在 unknown 之前（unknown 兜底）；
        - 未调用过 probe_all() 时 _alive 为空，全部按 unknown 处理，行为与旧版一致。

        R3.4：第一排序键换成「实际排序分 = 基础优先级(base_priority) + 惩罚分」，
        由 _score() 给出 —— 被 429 打压过的通道自动下沉、惩罚衰减/成功后自动回到
        原位，所以**不需要手工调各 Provider 顺序**。基础优先级见
        free_router_kernel.PROVIDER_BASE_PRIORITY（可用
        ``llm.free.priority.<provider>`` 覆盖，数字越小越优先）。

        P0 卡2：task_color（可选，TaskColor 或其字符串值）传入时，按
        ModelRoutingMatrix 的 L0-L3 × 四色表过滤候选：
          - 不在允许 tier 集合里的 provider 被剔除；
          - 被矩阵判定为 BLOCKED（24h 拉黑）的 provider 被剔除。
        传 None（默认）时行为与旧版完全一致。
        """
        cands = [c for c in self.candidates() if self._eligible(c)]

        # P0 卡2：按 task_color 过滤
        if task_color is not None:
            matrix = self._get_routing_matrix()
            if matrix is not None:
                cands = self._filter_by_task_color(cands, task_color)

        alive = [c for c in cands if self._alive.get(c.model) is True]
        unknown = [c for c in cands if self._alive.get(c.model) is not True]

        def _sort_group(group: list[Candidate]) -> list[Candidate]:
            local = [c for c in group if c.provider in (
                "ollama", "custom", "local_bridge", "local_scan", "desktop")]
            # 远端免费渠道：openrouter/nvidia(需 key) 与 pollinations/llm7(零配置免 key)
            # 统一按成功率降序、延迟升序；漏进这个 bucket 的 provider 会被静默丢弃。
            remote = [c for c in group if c.provider in (
                "openrouter", "nvidia", "pollinations", "llm7",
                # R3.8：「需 Key 登记渠道」的 provider 名（services/free_key_channels.py
                # ::PROVIDER_IDS）。不加进这个 bucket 的 provider 会被**静默丢弃**。
                *PROVIDER_IDS)]
            # R3.4：两个 bucket 内部统一按「实际排序分 → 成功率降序 → 延迟升序」。
            # 实际排序分 = 基础优先级 + 惩罚分（见 _score）：被 429 打压的通道自动
            # 下沉、惩罚衰减/成功后自动回到原位。惩罚分全 0 时排序退化为
            # 「基础优先级 → 成功率 → 延迟」；基础优先级的默认值与改动前远端桶在
            # 成功率并列时的既有次序一致（见 free_router_kernel.PROVIDER_BASE_PRIORITY
            # 的注释），且可被 llm.free.priority.<provider> 覆盖。
            score = self._score
            local.sort(key=lambda c: (score(c), -c.success_rate, c.latency_ms))
            remote.sort(key=lambda c: (score(c), -c.success_rate, c.latency_ms))
            return local + remote

        return _sort_group(alive) + _sort_group(unknown)

    # ------------------------------------------------------------------ #
    # P0 卡2：task_color 过滤
    # ------------------------------------------------------------------ #
    def _get_routing_matrix(self):
        """获取 routing matrix：注入的优先；否则懒加载默认实例（auto_matrix=True 时）。"""
        if self._routing_matrix is not None:
            return self._routing_matrix
        if not self._auto_matrix_enabled:
            return None
        # 懒加载：避免 import 副作用 + 拖慢启动
        try:
            from services.model_routing_matrix import ModelRoutingMatrix
            self._routing_matrix = ModelRoutingMatrix(clock=self._clock)
            return self._routing_matrix
        except Exception as e:  # noqa: BLE001
            logger.warning("懒加载 routing_matrix 失败: %s", e)
            self._auto_matrix_enabled = False  # 失败一次就不再试
            return None

    def _filter_by_task_color(self, cands: list, task_color) -> list:
        """按 ModelRoutingMatrix 的四色 × L0-L3 表过滤候选。

        - task_color 可能是 TaskColor 枚举或其字符串值（"GREEN"/"YELLOW"/...）；
        - 不在允许 tier 集合的 provider 被剔除；
        - 被矩阵判定为 BLOCKED 的 provider 被剔除；
        - 任何异常都静默回退到「不过滤」，绝不炸主流程。
        """
        try:
            from services.model_routing_matrix import (
                TaskColor, COLOR_TIER_MAP, TIER_PROVIDERS, ProviderState,
            )
            # 归一化 task_color
            if isinstance(task_color, str):
                tc = TaskColor(task_color)
            else:
                tc = task_color
            allowed_tiers = set(COLOR_TIER_MAP[tc])
            # provider -> tier 反查表
            provider_to_tier = {}
            for tier, ps in TIER_PROVIDERS.items():
                for p in ps:
                    if p is not None:
                        provider_to_tier[p] = tier

            out = []
            matrix = self._get_routing_matrix()
            for c in cands:
                tier = provider_to_tier.get(c.provider)
                if tier is None:
                    # 未知 provider（如自定义渠道）不在矩阵表里，保守放行
                    out.append(c)
                    continue
                if tier not in allowed_tiers:
                    continue
                # 查熔断状态：用 matrix 自己的时钟（避免 FreeModelRouter 真实时钟
                # 与注入 FakeClock 错位导致 BLOCKED 误判为已过期）
                cb = matrix._breakers.get(c.provider) if matrix is not None else None
                state = cb.state() if cb is not None else ProviderState.OK
                if state is ProviderState.BLOCKED:
                    continue
                out.append(c)
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning("task_color 过滤异常，回退不过滤: %s", e)
            return cands

    # ------------------------------------------------------------------ #
    # R3.3-30/31/32 韧性：错误分级 / 熔断 / 冷却 / 探活
    # ------------------------------------------------------------------ #
    @staticmethod
    def _classify_exc(exc: Exception) -> str:
        """按 HTTP status 把异常分成 5xx / 429 / 401_403 / network。

        openai v3 的 APIStatusError 带 .status_code；其它库/网络异常无 status → network。
        """
        status = getattr(exc, "status_code", None)
        if status is None:
            resp = getattr(exc, "response", None)
            if resp is not None:
                status = getattr(resp, "status_code", None)
        if status is None:
            return "network"
        try:
            code = int(status)
        except (TypeError, ValueError):
            return "network"
        if 500 <= code < 600:
            return "5xx"
        if code == 429:
            return "429"
        if code in (401, 403):
            return "401_403"
        return "network"

    # ------------------------------------------------------------------ #
    # 兼容旧测试/旧调用的只读视图：model -> unlock_at / 连续失败次数（缺省 0）
    # ------------------------------------------------------------------ #
    @property
    def _cooldown(self) -> dict:
        """model_name -> 冷却截止时间戳（实时从 CooldownTable 派生）。"""
        return {m: e["unlock_at"] for m, e in self._cd.table.items()}

    @property
    def _consecutive_5xx(self) -> dict:
        """model_name -> 连续 5xx 失败次数（缺省 0）。"""
        out = _DefaultZeroDict()
        out.update({m: e["consecutive_failures"]
                    for m, e in self._cd.table.items()})
        return out

    # ------------------------------------------------------------------ #
    # R3.4 路由内核：状态落点 / 通道标识 / 排序分 / Session 解析
    # ------------------------------------------------------------------ #
    def _state_db_path(self) -> str:
        """路由状态库落点（滑动窗口计数 / 429 冷却等级 / Sticky 映射）。

        优先级：显式 config ``llm.free.state_dir`` >
        ``services.runtime_paths.writable_data_dir()``（项目统一可写数据根：
        开发态 = 仓库根/data、冻结态 = 安装目录/data；既不写只读的 _internal，
        也不碰任何系统目录）> 仓库根 data/ 兜底。库文件名固定 router_state.db。
        打不开时由 PersistentStateStore 静默降级为纯内存，不影响路由。
        """
        explicit = self._cfg("llm.free.state_dir")
        if explicit:
            base = str(explicit)
            if not os.path.isabs(base):
                base = os.path.join(self._project_root(), base)
        else:
            base = ""
            try:
                from services.runtime_paths import writable_data_dir
                base = writable_data_dir()
            except Exception as e:  # noqa: BLE001
                logger.debug("runtime_paths 不可用，回退仓库根 data/: %s", e)
            if not base:
                base = os.path.join(self._project_root(), "data")
        return os.path.join(base, "free_router", "router_state.db")

    @staticmethod
    def _project_root() -> str:
        return os.path.abspath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

    @staticmethod
    def _channel_key(c: Candidate) -> str:
        """通道标识 ``provider:model``（= routed_via 的形态）。

        惩罚分 / 429 分级冷却 / 跳过集合 / Sticky 映射统一用它——比裸 model 名更
        精确，避免跨 Provider 同名模型互相污染；5xx 熔断 CooldownTable 仍按裸
        model 名，保持既有行为不变。
        """
        return channel_tag(c.provider, c.model)

    def _priority(self, c: Candidate) -> int:
        """基础优先级（越小越优先）；可被 ``llm.free.priority.<provider>`` 覆盖。"""
        raw = None
        try:
            raw = self.config.get(f"llm.free.priority.{c.provider}")
        except Exception:  # noqa: BLE001 - 配置源异常按「未覆盖」处理
            raw = None
        return base_priority_for(c.provider,
                                 {c.provider: raw} if raw is not None else None)

    def _score(self, c: Candidate) -> float:
        """实际排序分 = 基础优先级 + 惩罚分（越小越优先）。"""
        return effective_score(self._priority(c),
                               self._penalty.value(self._channel_key(c)))

    @staticmethod
    def _estimate_tokens(system_prompt, user_prompt, text, usage=None) -> int:
        """本次调用 token 用量：优先取响应 usage，缺省按字符数/4 估算。

        只喂给 TPM/TPD 限流计数（不是计费口径），拿不到 usage 时估算足够。
        """
        if usage is not None:
            for attr in ("total_tokens", "prompt_tokens"):
                value = (usage.get(attr) if isinstance(usage, dict)
                         else getattr(usage, attr, None))
                if isinstance(value, (int, float)) and value > 0:
                    return int(value)
        approx = (len(system_prompt or "") + len(user_prompt or "")
                  + len(text or "")) / 4.0
        return int(approx)

    def _resolve_session_key(self, messages, user_prompt, multi_turn,
                             session_key) -> str:
        """解析 Sticky SessionKey（空串 = 不建不命中）。

        - 显式 ``session_key`` 优先（调用方已自己算好）；
        - 给了 ``messages``（OpenAI 风格消息列表）时：取「第一条 user 消息」的
          SHA1，多轮判定默认 = 列表里出现过 assistant 历史；
        - 旧调用方只给两条字符串时：多轮判定需显式 ``multi_turn=True``，否则返回
          空串 —— **单轮对话不建 Sticky**，避免把一次性提问绑死到某个模型。
        """
        derived_multi = multi_turn
        first_user = ""
        if messages:
            if derived_multi is None:
                derived_multi = any(
                    _msg_field(m, "role").strip().lower() == "assistant"
                    for m in messages)
            for m in messages:
                if _msg_field(m, "role").strip().lower() == "user":
                    first_user = _msg_field(m, "content")
                    break
        if derived_multi is None:
            derived_multi = False
        if session_key:
            return str(session_key) if derived_multi else ""
        return session_key_for(first_user or user_prompt, bool(derived_multi))

    def _eligible(self, c: Candidate) -> bool:
        """该候选当前是否可被选中。

        既有判据：未永久禁用、未探活 dead、不在 5xx 熔断冷却期。
        R3.4 追加两条硬闸（任一不过即跳过本通道，**不影响其它免费通道**）：
        - 429 分级冷却（Cooldown429）：同一模型 24h 内第 N 次限流后的
          2min / 10min / 1h / 24h；
        - 四维限流（RateLimiter）：RPM / RPD / TPM / TPD 任一维度触顶。
        """
        key = c.model
        if key in self._disabled:
            return False
        if self._alive.get(key) is False:
            return False
        now = self._clock()
        if self._cd.is_cooled_down(key, now):
            return False
        if self._cd429.is_cooling(self._channel_key(c), now):
            return False
        if not self._limits.allow(c.provider):
            return False
        return True

    def _record_success(self, c: Candidate) -> None:
        """一次成功：清掉该模型整条 5xx 冷却记录、标记 alive、惩罚分 -1。

        惩罚分 -1（下限 0）= 回到基础优先级「原位」；429 冷却等级**不因成功重置**，
        严格保持「同一模型 24h 内第 N 次 429」的退避阶梯语义。
        """
        self._cd.record_success(c.model)
        self._alive[c.model] = True
        self._penalty.on_success(self._channel_key(c))
        # P0 卡2：同步回写熔断矩阵（懒加载默认实例 / 注入实例均可）
        matrix = self._get_routing_matrix()
        if matrix is not None:
            try:
                matrix.record(c.provider, success=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("routing_matrix.record(success) 失败: %s", e)

    def _record_429(self, c: Candidate) -> dict:
        """429 重试用尽仍被限流：惩罚分 +3 并升级 429 冷却（2min/10min/1h/24h）。

        单次 429 不立刻冷却 —— 先走 chat() 内的同模型指数退避
        （BACKOFF_RETRY_DELAYS 1/2/4s，最多 BACKOFF_MAX_RETRIES 次）；退避全部
        失败才升级为冷却，避免一次抖动就把通道踢出一整轮。冷却状态持久化。
        """
        self._penalty.on_429(self._channel_key(c))
        return self._cd429.record_429(self._channel_key(c))

    def _record_5xx(self, c: Candidate, error: Exception = None) -> None:
        """一次 5xx：CooldownTable 记录失败（30s 起步翻倍、封顶 5min）。"""
        self._cd.record_failure(c.model,
                                f"5xx: {error}" if error else "5xx")
        # P0 卡2：同步回写熔断矩阵（懒加载默认实例 / 注入实例均可）
        matrix = self._get_routing_matrix()
        if matrix is not None:
            try:
                matrix.record(c.provider, success=False)
            except Exception as e:  # noqa: BLE001
                logger.warning("routing_matrix.record(failure) 失败: %s", e)

    def _record_auth_fail(self, c: Candidate) -> None:
        """401/403：永久标记该模型/账号不可用（直到重启），不再重试。"""
        key = c.model
        self._disabled.add(key)
        self._alive[key] = False

    def cooldown_snapshot(self) -> list[dict]:
        """供 UI 显示：哪些模型在冷却、何时恢复（R3.3-32）。"""
        return self._cd.snapshot()

    def next_available_candidate(self) -> Optional[Candidate]:
        """选模型入口：返回当前第一个可用候选；
        若所有模型都在冷却/禁用/dead → 返回 None，由上层走兜底链。"""
        ranked = self.ranked()
        return ranked[0] if ranked else None

    def get_cooldown_status(self) -> dict:
        """供前端/设置页查看：每个模型的冷却截止、剩余秒数、连续失败、是否永久禁用。

        R3.4：额外并入 429 分级冷却（键为 ``provider:model``，带 ``cooldown_level``
        与 ``reason``），以及四维限流的触顶维度 ``limit_blocked``。5xx 熔断那部分
        的键与字段保持不变（向后兼容）。
        """
        out: dict[str, dict] = {}
        for row in self._cd.snapshot():
            out[row["model"]] = {
                "cooldown_until": row["unlock_at"],
                "remaining_sec": row["remaining_sec"],
                "consecutive_5xx": row["consecutive_failures"],
            }
        for row in self._cd429.snapshot():
            out.setdefault(row["key"], {}).update({
                "cooldown_until": row["unlock_at"],
                "remaining_sec": row["remaining_sec"],
                "cooldown_level": row["level"],
                "reason": "429",
            })
        for provider in set(self._limits.configured()):
            dim = self._limits.blocked_dim(provider)
            if dim:
                out.setdefault(f"{provider}:*", {})["limit_blocked"] = dim
        for key in self._disabled:
            out.setdefault(key, {})["permanently_disabled"] = True
        return out

    def _probe_one(self, c: Candidate, timeout: float = PROBE_TIMEOUT_SEC) -> bool:
        """对单个候选发 max_tokens=1 的 'hi' 探活；任何异常 → False（dead）。"""
        try:
            import openai
            client = openai.OpenAI(api_key=c.api_key, base_url=c.base_url,
                                   timeout=timeout)
            resp = client.chat.completions.create(
                model=c.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=PROBE_MAX_TOKENS,
            )
            return bool(resp.choices and resp.choices[0].message is not None)
        except Exception as e:  # 探活失败即 dead，不抛
            logger.info("探活 %s:%s 失败: %s", c.provider, c.model, e)
            return False

    def probe_all(self, budget_sec: float = PROBE_TOTAL_BUDGET_SEC) -> dict:
        """启动时对每个候选发极短请求，标记 alive/dead。

        - 总耗时不超过 budget_sec（默认 5s）；
        - 每个探活超时算 dead；预算用完还没探到的候选保持 unknown（不阻塞启动）；
        - 结果写入 self._alive，供 ranked() 优先/兜底/排除使用。
        """
        deadline = self._clock() + budget_sec
        for c in self.candidates():
            remaining = deadline - self._clock()
            if remaining <= 0:
                break  # 预算用完，剩余保持 unknown
            timeout = min(PROBE_TIMEOUT_SEC, remaining)
            self._alive[c.model] = self._probe_one(c, timeout=timeout)
        return dict(self._alive)

    # ------------------------------------------------------------------ #
    # 调用
    # ------------------------------------------------------------------ #
    def chat(self, system_prompt: str, user_prompt: str,
             temperature: float = 0.7, max_tokens: int = 1500,
             timeout: float = 30.0,
             messages: Optional[list] = None,
             multi_turn: Optional[bool] = None,
             session_key: Optional[str] = None) -> dict:
        """逐候选尝试。返回 {"text","provider","model","tried","routed_via"}。

        顺序：① OpenAI 兼容候选（本地扫描/桌面shim/桥/Ollama/OpenRouter/NVIDIA/
        零配置 pollinations·llm7）② 非 OpenAI 兜底（仅显式启用的浏览器AI）。
        浏览器自动化默认关（browser_ai.enabled 缺省 false），网页搜索不在聊天兜底链
        （默认关，仅 consult 层知识不足时显式补全），绝不擅自联网。

        R3.4 内核接线（新增参数全部可选，对既有调用方**完全向后兼容**）：
        - **重试预算**：整条链路最多 MAX_ROUTE_ATTEMPTS(=20) 次出站尝试；
        - **跳过集合**：失败过的「模型 + Key」组合进 SkipSet，同一轮不再重试；
        - **重试触发**：429 / 超时 / 连接拒绝 / 5xx 都换下一个候选继续；
        - **401/403 不进重试链**：认证失败是 Key 无效/无权限，换模型、换参数都解决
          不了，所以不做任何退避重试，只永久禁用该模型（见下方显式注释）；
        - **Sticky Session**：仅多轮对话（``messages`` 含 assistant 历史，或显式
          ``multi_turn=True``）才建立/命中，30 分钟有效，且只是「优先」不是「强制」；
        - **可观测性**：命中的通道以 ``routed_via``（``provider:model``）随结果返回，
          对齐 freellmapi 的响应头 x-routed-via。
        免费池全空 / 全部不可用 → raise RuntimeError，**绝不改道付费模型**。
        """
        tried: list[str] = []
        last_err: Optional[Exception] = None
        skip = SkipSet()
        attempts = 0

        # Sticky：解析 SessionKey（只对多轮生效；单轮恒为空 → 不建不命中）
        sticky_key = self._resolve_session_key(
            messages=messages, user_prompt=user_prompt,
            multi_turn=multi_turn, session_key=session_key)
        sticky_hit = self._sticky.get(sticky_key) if sticky_key else ""

        # ① OpenAI 兼容候选（ranked() 已过滤 dead/disabled/两种冷却/限流触顶）
        while attempts < MAX_ROUTE_ATTEMPTS:
            pool = [c for c in self.ranked() if not skip.has_candidate(c)]
            if not pool:
                break
            # Sticky「优先不强制」：上次成功的通道若仍可用就放队首；它若已冷却/限流，
            # 根本不会出现在 pool 里 → 正常 Fallback（这就是「优先」的实现方式）。
            if sticky_hit:
                head = [c for c in pool if self._channel_key(c) == sticky_hit]
                if head:
                    pool = head + [c for c in pool if c not in head]
            c = pool[0]
            tag = self._channel_key(c)
            tried.append(tag)

            # 同模型 429 指数退避重试循环：最多 BACKOFF_MAX_RETRIES 次
            attempt = 0
            while True:
                if attempts >= MAX_ROUTE_ATTEMPTS:
                    break
                attempts += 1
                t0 = time.time()
                # 滑动窗口记账（内存 + SQLite 双写）：本次出站请求先计入四维用量
                event = self._limits.note_request(c.provider)
                try:
                    import openai
                    client = openai.OpenAI(api_key=c.api_key, base_url=c.base_url,
                                           timeout=timeout)
                    resp = client.chat.completions.create(
                        model=c.model,
                        messages=[{"role": "system", "content": system_prompt},
                                  {"role": "user", "content": user_prompt}],
                        temperature=temperature, max_tokens=max_tokens,
                    )
                    text = (resp.choices[0].message.content or "").strip()
                    c.ok += 1
                    c.latency_ms = (time.time() - t0) * 1000
                    self._limits.note_tokens(
                        event,
                        self._estimate_tokens(system_prompt, user_prompt, text,
                                              getattr(resp, "usage", None)))
                    if text:
                        self._record_success(c)
                        with self._lock:
                            self._last_routed_via = tag
                        if sticky_key:
                            self._sticky.set(sticky_key, tag)
                        logger.info("免费模型命中 %s（第%d次尝试）", tag, len(tried))
                        return {"text": text, "provider": c.provider,
                                "model": c.model, "tried": tried,
                                "routed_via": tag}
                    # 空回复：HTTP 成功但无内容，不标记 alive/不清冷却，切下一个
                    skip.add_candidate(c)
                    break
                except Exception as e:  # 限流/超时/认证/5xx/离线
                    c.fail += 1
                    last_err = e
                    kind = self._classify_exc(e)
                    if kind == "429":
                        # 429：惩罚分 +3（累计上限 10）→ 该模型下一轮排序自动下沉
                        self._penalty.on_429(tag)
                        if (attempt < BACKOFF_MAX_RETRIES
                                and attempts < MAX_ROUTE_ATTEMPTS):
                            delay = BACKOFF_RETRY_DELAYS[
                                min(attempt, len(BACKOFF_RETRY_DELAYS) - 1)]
                            logger.warning("免费渠道 %s 429 限流，%.0fs 后同模型重试 (%d/%d)",
                                           tag, delay, attempt + 1, BACKOFF_MAX_RETRIES)
                            self._sleep(delay)
                            attempt += 1
                            continue  # 同模型退避后重试
                        # 退避用尽仍被限流 → 升级 429 冷却（2min/10min/1h/24h）后跳过
                        info = self._record_429(c)
                        logger.warning(
                            "免费渠道 %s 429 退避用尽 → 冷却升级第%d档 %.0fs，切下一个",
                            tag, info["level"], info["cooldown_sec"])
                        skip.add_candidate(c)
                        break
                    if kind == "5xx":
                        self._record_5xx(c, e)
                        remaining = (self._cd.table.get(c.model, {}).get(
                            "unlock_at", 0.0) - self._clock())
                        logger.warning("免费渠道 %s 5xx 熔断冷却 %.0fs，切下一个: %s",
                                       tag, remaining, e)
                        skip.add_candidate(c)
                        break
                    if kind == "401_403":
                        # 【认证失败不进重试链】401/403 = Key 无效 / 无权限：换模型、
                        # 换参数、退避重试都解决不了，因此这里不做任何重试，
                        # 直接永久禁用该模型（直到重启）并跳过。
                        self._record_auth_fail(c)
                        skip.add_candidate(c)
                        logger.warning(
                            "免费渠道 %s 认证失败(401/403)，永久禁用且不进重试链: %s",
                            tag, e)
                        break
                    # network/超时/连接拒绝/其它：换下一个候选重试（跳过集合兜底）
                    skip.add_candidate(c)
                    logger.warning("免费渠道 %s 失败，切换下一个: %s", tag, e)
                    break

        # ② 非 OpenAI 兼容兜底（浏览器AI 显式启用 / 网页搜索）
        fb = self._fallback_fchat(system_prompt, user_prompt, timeout, tried)
        if fb is not None:
            return fb

        if not tried:
            # 免费池为空：fail-closed —— 只报空池，绝不改道付费模型
            raise RuntimeError(
                "免费模型池为空（fail-closed：不路由到付费模型）：已配置的免费渠道"
                "全部不可用（未配置 / 探活失败 / 冷却中 / 限流触顶）")
        raise RuntimeError(
            f"全部免费渠道失败，已尝试 {tried}: {last_err}") from last_err

    # ------------------------------------------------------------------ #
    # 非 OpenAI 兼容兜底：仅显式启用的浏览器 AI（网页搜索不在此链）
    # ------------------------------------------------------------------ #
    def _fallback_fchat(self, system_prompt: str, user_prompt: str,
                        timeout: float, tried: list) -> Optional[dict]:
        """慢/不稳源逐次尝试，单源失败即跳过。返回归一化 dict 或 None。

        注意：网页搜索（web_search）已移出聊天模型兜底链——它只在 facade/consult
        层做"知识不足上下文补全"（见 augment_context_with_web_search），且默认关，
        绝不在用户未开关/不知情时驱动联网。
        """
        # 与 candidates() 同一总闸：免费路由整体关闭时，兜底也不启用
        if not self._flag("llm.free.enabled", True):
            return None
        # 浏览器自动化 AI：仅在显式启用时调用（默认关，不进自动路由）
        if self._flag("browser_ai.enabled", False):
            tried.append("browser_ai:<hub>")
            try:
                hub = self._get_browser_hub()
                resp = hub.chat(query=user_prompt, timeout=timeout)
                text = ((resp.get("choices", [{}])[0].get("message", {})
                         or {}).get("content") or "").strip()
                src_name = ((resp.get("_meta") or {}).get("source")
                            or "browser-ai")
                if text:
                    # R3.4 可观测性：兜底命中同样留痕（routed_via + last_routed_via）
                    routed = f"browser_ai:{src_name}"
                    with self._lock:
                        self._last_routed_via = routed
                    logger.info("免费兜底命中 browser_ai:%s", src_name)
                    return {"text": text, "provider": "browser_ai",
                            "model": f"browser-ai:{src_name}", "tried": tried,
                            "routed_via": routed}
            except Exception as e:  # BrowserUnavailable / 全源失败 / 反爬
                logger.info("浏览器AI兜底跳过: %s", e)
                self._catalog["browser_ai"] = {
                    "models": [], "ts": self._clock(), "ok": False,
                    "error": f"{type(e).__name__}: {e}"}
        return None

    # ------------------------------------------------------------------ #
    # 网页搜索：仅供 facade/consult 层在"知识不足"时显式做上下文补全（默认关）
    # ------------------------------------------------------------------ #
    def augment_context_with_web_search(self, query: str,
                                        timeout: float = 20.0) -> dict:
        """知识不足时由 consult 层显式调用，返回补全上下文（不直接充当聊天回复）。

        - 默认关：llm.free.web_search.enabled 缺省 false，未开启时返回 disabled，
          不发起任何网络请求；
        - 返回 {"enabled", "used_web", "context", "sources"}。
        """
        if not self._flag("llm.free.web_search.enabled", False):
            return {"enabled": False, "used_web": False, "context": "",
                    "sources": []}
        try:
            svc = self._get_web_search()
            out = svc.answer_with_sources(query) or {}
            return {"enabled": True,
                    "used_web": bool(out.get("used_web", False)),
                    "context": out.get("answer", "") or "",
                    "sources": out.get("sources", []) or []}
        except Exception as e:  # noqa: BLE001
            logger.info("网页搜索上下文补全不可用: %s", e)
            return {"enabled": True, "used_web": False, "context": "",
                    "sources": [], "error": str(e)}

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def routing_snapshot(self) -> dict:
        """冻结接口：路由内核全量快照（JSON 可序列化，供设置页只读展示）。

        结构（另一个并行子代理按此写界面，字段名与含义不可改）::

            {
              "generated_at": float,          # 快照时间戳
              "last_routed_via": str,         # "provider:model"；本进程尚未命中过 = ""
              "sticky_sessions": int,         # 存活（未过期）的 Sticky 会话数
              "channels": [ {
                    "key": "provider:model",
                    "provider": str, "model": str,
                    "base_priority": int,     # 基础优先级，越小越优先
                    "penalty": float,         # 动态惩罚分（0~10）
                    "effective_score": float, # = base_priority + penalty，越小越优先
                    "eligible": bool,         # 与 ranked()/_eligible() 同一判据
                    "cooldown_until": float,  # 5xx 熔断与 429 分级冷却的较晚者
                    "remaining_sec": float,
                    "cooldown_level": int,    # 429 冷却档位（0 = 未升级过）
                    "limits": {"rpm","rpd","tpm","tpd",            # null = 未配上限
                               "rpm_used","rpd_used","tpm_used","tpd_used"},
                    "ok": int, "fail": int,
                    "latency_ms": float,
              }, ... ],
              "mode": {"free_only": True, "fail_closed": True,
                       # R3.7 追加（原有 free_only / fail_closed 两个键含义不变）：
                       "price_gate": "fail_closed_whitelist",  # 价格门模式（默认拒绝）
                       "trusted_models": [str, ...],   # 白名单清单（llm.free.trusted_models 原文条目）
                       "trusted_models_count": int},   # 白名单条目数
              # 「至少含」之外的附加字段，便于排查：
              "limits_configured": {...}, "persistence": {...}, "penalty": {...},
            }

        返回的 channels 只含**已通过价格门**的候选（candidates() 就是那道门），
        因此「快照里的通道数」= 当前真正允许出站的通道数。

        只读保证：本方法**不联网、不跑扫描、不改路由决策** —— 期间目录刷新与本地
        扫描被冻结（只用已缓存/已注册的候选），因此可在设置页后台线程安全调用。
        所有值都是 JSON 原生类型（null/str/int/float/bool），不含 NaN/Inf。
        """
        now = float(self._clock())
        with self._lock:
            prev_frozen = self._catalog_frozen
            self._catalog_frozen = True
        try:
            cands = self.candidates()
        except Exception as e:  # noqa: BLE001 - 目录异常也不能把界面打崩
            logger.info("routing_snapshot 取候选失败（按空处理）: %s", e)
            cands = []
        finally:
            with self._lock:
                self._catalog_frozen = prev_frozen
        channels: list[dict] = []
        for c in cands:
            try:
                ck = self._channel_key(c)
                cd5 = float(self._cd.table.get(c.model, {}).get("unlock_at", 0.0))
                cd4 = self._cd429.unlock_at(ck)
                until = max(cd5, cd4)
                channels.append({
                    "key": ck,
                    "provider": c.provider,
                    "model": c.model,
                    "base_priority": int(self._priority(c)),
                    "penalty": float(self._penalty.value(ck)),
                    "effective_score": float(self._score(c)),
                    "eligible": bool(self._eligible(c)),
                    "cooldown_until": float(until),
                    "remaining_sec": max(0.0, round(until - now, 1)),
                    "cooldown_level": int(self._cd429.level(ck)),
                    "limits": self._limits.report(c.provider),
                    "ok": int(c.ok),
                    "fail": int(c.fail),
                    "latency_ms": round(float(c.latency_ms), 1),
                })
            except Exception as e:  # noqa: BLE001 - 单通道异常不影响整表
                logger.info("routing_snapshot 单通道跳过: %s", e)
        channels.sort(key=lambda r: (r["effective_score"], r["provider"], r["model"]))
        trusted = self.trusted_models()
        return {
            "generated_at": now,
            "last_routed_via": str(self._last_routed_via or ""),
            "sticky_sessions": int(self._sticky.count()),
            "channels": channels,
            "mode": {"free_only": True, "fail_closed": True,
                     # R3.7 价格门：默认拒绝 + 显式白名单放行。白名单清单/条目数
                     # 放进快照，设置页与诊断脚本（读 routing_snapshot）即可看到
                     # 「现在到底放行了哪些原本没有免费证据的通道」。
                     "price_gate": "fail_closed_whitelist",
                     "trusted_models": list(trusted),
                     "trusted_models_count": len(trusted)},
            "limits_configured": self._limits.configured(),
            "persistence": self._state_store.status(),
            "penalty": self._penalty.snapshot(),
        }

    def health_report(self) -> list[dict]:
        out = []
        for c in self.candidates():
            out.append({"provider": c.provider, "model": c.model,
                        "success_rate": round(c.success_rate, 2),
                        "ok": c.ok, "fail": c.fail,
                        "latency_ms": round(c.latency_ms, 1)})
        return out

    def catalog_report(self) -> list[dict]:
        """动态目录透明报告：每源是否成功、模型数、时间与错误（供 UI/日志展示）。"""
        rep = []
        for name, st in self.catalog_status().items():
            rep.append({"source": name, "ok": st["ok"],
                        "count": st["count"], "ts": st["ts"],
                        "error": st["error"]})
        return rep
