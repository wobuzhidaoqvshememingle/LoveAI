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
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

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
                 web_search=None) -> None:
        self.config = config or {}
        # 运行期健康表：key=(provider,model) -> Candidate
        self._health: dict[tuple, Candidate] = {}
        # HTTP / 时钟可注入（离线测试用），默认走标准库 / 真实时间
        self._http = http_get or _http_get
        self._clock = clock or time.time
        # 退避 sleep 可注入（测试用 mock，不真睡）
        self._sleep: Callable[[float], None] = time.sleep
        # 动态目录缓存：source -> {"models":[], "ts":float, "ok":bool, "error":str}
        self._catalog: dict[str, dict] = {}
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
    def _fetch_openrouter_free(self) -> list[str]:
        body = self._http(OPENROUTER_MODELS_URL, timeout=self._timeout(),
                          headers={"Accept": "application/json"})
        data = json.loads(body).get("data", [])
        out: set[str] = set()
        for m in data:
            mid = str(m.get("id", ""))
            if not mid or mid == "openrouter/free":
                continue  # 跳过聚合路由本身，避免重复
            p = m.get("pricing", {}) or {}
            is_free = mid.endswith(":free") or (
                str(p.get("prompt", "0")) == "0"
                and str(p.get("completion", "0")) == "0")
            if is_free:
                out.add(mid)
        return sorted(out)

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
                models = fetcher()
                if models:
                    self._catalog[name] = {"models": models, "ts": now,
                                           "ok": True, "error": ""}
                else:
                    # 拉取成功但目录为空：保留旧缓存，标记本次为空
                    self._catalog[name] = {
                        "models": (cached or {}).get("models", []),
                        "ts": (cached or {}).get("ts", now),
                        "ok": bool((cached or {}).get("models")),
                        "error": "目录为空"}
            except Exception as e:  # 网络/解析失败：保留旧缓存，标记失败
                logger.info("动态目录 %s 拉取失败，将回退: %s", name, e)
                self._catalog[name] = {
                    "models": (cached or {}).get("models", []),
                    "ts": (cached or {}).get("ts", now),
                    "ok": bool((cached or {}).get("models")),
                    "error": f"{type(e).__name__}: {e}"}

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

    def _models_for(self, source: str, cfg_key: str,
                    static: list[str]) -> list[str]:
        """三级回退：显式 config 模型 > 动态目录 > 内置静态目录。"""
        explicit = self._cfg(cfg_key)
        if explicit:
            return [x.strip() for x in explicit.split(",") if x.strip()]
        dynamic = self._catalog.get(source, {})
        if dynamic.get("ok") and dynamic.get("models"):
            return list(dynamic["models"])
        return list(static)

    # ------------------------------------------------------------------ #
    # 候选构造
    # ------------------------------------------------------------------ #
    def candidates(self) -> list[Candidate]:
        """按配置/动态目录生成可用候选；未配 key / 未开启的渠道不入选。"""
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

        return out

    def _get(self, provider: str, base_url: str, key: str, model: str) -> Candidate:
        k = (provider, model)
        if k not in self._health:
            self._health[k] = Candidate(provider, base_url, key, model)
        return self._health[k]

    def ranked(self) -> list[Candidate]:
        """排序：可用候选按 成功率降序 → 延迟升序；未知渠道排在已知失败之后、
        已知成功之前。Ollama 本地默认最优先（零成本、无配额）。

        R3.3-30/31/32：
        - dead（探活失败）/ permanently disabled（401/403）/ cooldown 未到期 → 不入选；
        - alive=True 的候选整体排在 unknown 之前（unknown 兜底）；
        - 未调用过 probe_all() 时 _alive 为空，全部按 unknown 处理，行为与旧版一致。
        """
        cands = [c for c in self.candidates() if self._eligible(c)]
        alive = [c for c in cands if self._alive.get(c.model) is True]
        unknown = [c for c in cands if self._alive.get(c.model) is not True]

        def _sort_group(group: list[Candidate]) -> list[Candidate]:
            local = [c for c in group if c.provider in (
                "ollama", "custom", "local_bridge", "local_scan", "desktop")]
            # 远端免费渠道：openrouter/nvidia(需 key) 与 pollinations/llm7(零配置免 key)
            # 统一按成功率降序、延迟升序；漏进这个 bucket 的 provider 会被静默丢弃。
            remote = [c for c in group if c.provider in (
                "openrouter", "nvidia", "pollinations", "llm7")]
            remote.sort(key=lambda c: (-c.success_rate, c.latency_ms))
            return local + remote

        return _sort_group(alive) + _sort_group(unknown)

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

    def _eligible(self, c: Candidate) -> bool:
        """该候选当前是否可被选中：未永久禁用、未探活 dead、不在冷却期。"""
        key = c.model
        if key in self._disabled:
            return False
        if self._alive.get(key) is False:
            return False
        if self._cd.is_cooled_down(key, self._clock()):
            return False
        return True

    def _record_success(self, c: Candidate) -> None:
        """一次成功：清掉该模型整条冷却记录、标记 alive。"""
        self._cd.record_success(c.model)
        self._alive[c.model] = True

    def _record_5xx(self, c: Candidate, error: Exception = None) -> None:
        """一次 5xx：CooldownTable 记录失败（30s 起步翻倍、封顶 5min）。"""
        self._cd.record_failure(c.model,
                                f"5xx: {error}" if error else "5xx")

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
        """供前端/设置页查看：每个模型的冷却截止、剩余秒数、连续失败、是否永久禁用。"""
        out: dict[str, dict] = {}
        for row in self._cd.snapshot():
            out[row["model"]] = {
                "cooldown_until": row["unlock_at"],
                "remaining_sec": row["remaining_sec"],
                "consecutive_5xx": row["consecutive_failures"],
            }
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
             timeout: float = 30.0) -> dict:
        """逐候选尝试。返回 {"text","provider","model","tried"}。

        顺序：① OpenAI 兼容候选（本地扫描/桌面shim/桥/Ollama/OpenRouter/NVIDIA）
        ② 非 OpenAI 兜底（仅显式启用的浏览器AI）。
        浏览器自动化默认关（browser_ai.enabled 缺省 false），网页搜索不在聊天兜底链
        （默认关，仅 consult 层知识不足时显式补全），绝不擅自联网。
        全部失败 raise 最后一个异常。"""
        tried: list[str] = []
        last_err: Optional[Exception] = None

        # ① OpenAI 兼容候选（ranked() 已过滤 dead/disabled/cooldown）
        for c in self.ranked():
            tag = f"{c.provider}:{c.model}"
            tried.append(tag)

            # 同模型 429 指数退避重试循环：最多 BACKOFF_MAX_RETRIES 次
            attempt = 0
            while True:
                t0 = time.time()
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
                    if text:
                        self._record_success(c)
                        logger.info("免费模型命中 %s（第%d次尝试）", tag, len(tried))
                        return {"text": text, "provider": c.provider,
                                "model": c.model, "tried": tried}
                    # 空回复：HTTP 成功但无内容，不标记 alive/不清冷却，切下一个
                    break
                except Exception as e:  # 限流/超时/认证/5xx/离线
                    c.fail += 1
                    last_err = e
                    kind = self._classify_exc(e)
                    if kind == "429" and attempt < BACKOFF_MAX_RETRIES:
                        delay = BACKOFF_RETRY_DELAYS[min(attempt, len(BACKOFF_RETRY_DELAYS) - 1)]
                        logger.warning("免费渠道 %s 429 限流，%.0fs 后同模型重试 (%d/%d)",
                                       tag, delay, attempt + 1, BACKOFF_MAX_RETRIES)
                        self._sleep(delay)
                        attempt += 1
                        continue  # 同模型退避后重试
                    if kind == "5xx":
                        self._record_5xx(c, e)
                        remaining = (self._cd.table.get(c.model, {}).get(
                            "unlock_at", 0.0) - self._clock())
                        logger.warning("免费渠道 %s 5xx 熔断冷却 %.0fs，切下一个: %s",
                                       tag, remaining, e)
                        break
                    if kind == "401_403":
                        self._record_auth_fail(c)
                        logger.warning("免费渠道 %s 认证失败(401/403)，永久禁用: %s", tag, e)
                        break
                    # network / 其它：保持现状，切下一个
                    logger.warning("免费渠道 %s 失败，切换下一个: %s", tag, e)
                    break

        # ② 非 OpenAI 兼容兜底（浏览器AI 显式启用 / 网页搜索）
        fb = self._fallback_fchat(system_prompt, user_prompt, timeout, tried)
        if fb is not None:
            return fb

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
                    logger.info("免费兜底命中 browser_ai:%s", src_name)
                    return {"text": text, "provider": "browser_ai",
                            "model": f"browser-ai:{src_name}", "tried": tried}
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
