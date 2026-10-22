# -*- coding: utf-8 -*-
"""R3.7 价格门（fail-closed 硬闸）：默认拒绝 + 显式白名单放行。

任务书要求四组实证，全部落在本文件里（缺一不可）：

  ① **付费候选打不出去**：NVIDIA 显式配置分支、custom 自建端点分支各做一次，
     用假 HTTP + 假 openai client 计数，断言「付费候选被拒绝时出站调用次数 = 0」；
  ② **白名单正向对照**：同一候选登记进 ``llm.free.trusted_models`` 后**能被放行**，
     并且真的被调用（证明白名单不是死代码）；
  ③ **付费不可翻案**：被公开价格目录**正面证明**付费的候选，即使登记进白名单
     （连 ``provider:*`` 端点级白名单也一样）仍然被拒绝；
  ④ **不误杀对照**：本机 Ollama / 本机桥 / pollinations / llm7 / OpenRouter ``:free``
     仍然正常进池。

全部离线：``http_get`` 与 ``openai.OpenAI`` 都注入假实现，一个端口都不开、
一个真实请求都不发。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.free_model_router import (                       # noqa: E402
    BUILTIN_FREE_PROVIDERS,
    TRUSTED_MODELS_KEY,
    FreeModelRouter,
    _TRUSTED_MODEL_PROVIDERS,
    _TRUSTED_PARSE_WARNED,
    _parse_trusted_entries,
)
from services.free_key_channels import PROVIDER_IDS           # noqa: E402
import services.free_model_router as fmr                      # noqa: E402

# ---- 受控的假响应 ----
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"

# 全量目录：1 个真免费 + 1 个 :free 标记 + 1 个**正面证明付费** + 1 个聚合路由
OR_BODY = json.dumps({"data": [
    {"id": "vendor/zeroprice", "pricing": {"prompt": "0", "completion": "0.0"}},
    {"id": "vendor/new-free:free", "pricing": {}},
    {"id": "openai/gpt-4o",
     "pricing": {"prompt": "0.0000025", "completion": "0.00001"}},
    {"id": "openrouter/free", "pricing": {}},
]})
# NVIDIA /v1/models：**响应里根本没有 pricing 字段**（这就是残余 #1 的成因）
NVIDIA_BODY = json.dumps({"data": [{"id": "vendor/dyn-nim"}]})
BRIDGE_BODY = json.dumps({"data": [{"id": "workbuddy/glm-5.3"}]})


class FakeConfig:
    """点分 get/set（flat 键必须先经 from_flat 搭成嵌套，否则读不到）。"""

    def __init__(self, store=None):
        self.store = dict(store or {})

    @classmethod
    def from_flat(cls, mapping):
        cfg = cls()
        for k, v in dict(mapping or {}).items():
            cfg.set(k, v)
        return cfg

    def get(self, key, default=None):
        value = self.store
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def set(self, key, value):
        parts = key.split(".")
        node = self.store
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class FakeOpenAI:
    """假 openai client：只做两件事 —— **计数** + 按 model 返回预设响应。

    · ``calls``：``chat.completions.create`` 被调用的 model 序列（= 真正会出网、
      也真正会产生费用的那一步）；
    · ``built``：``OpenAI(...)`` 的构造次数（路由层只在真正要发请求前才构造客户端）。
    价格门拒绝候选时，这两个计数器都必须保持为空。
    """

    calls: list = []
    built: list = []
    behavior: dict = {}

    def __init__(self, *args, **kwargs):
        FakeOpenAI.built.append(kwargs.get("base_url", ""))
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        model = kw.get("model")
        FakeOpenAI.calls.append(model)
        handler = FakeOpenAI.behavior.get(model)
        if callable(handler):
            out = handler()
            if isinstance(out, Exception):
                raise out
            return out
        if isinstance(handler, Exception):
            raise handler
        return _Resp("ok")


def make_http(routes):
    """``routes``: {url: 响应体(str) 或 Exception}；未命中的 url 一律连接失败。

    返回 ``(http_get, calls)``；``calls`` 记录每一次出站 HTTP 尝试。
    """
    calls: list = []

    def _get(url, timeout=None, headers=None, **kw):
        calls.append(url)
        r = routes.get(url)
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise ConnectionError(f"未路由的假端点（等价于连不上）: {url}")
        return r

    return _get, calls


class _GateCase(unittest.TestCase):
    """公共夹具：临时 state_dir + 假 openai + 假本地采集（离线确定）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="loveai_price_gate_")
        # 用一个**文件**占住 state_dir 的父路径 → PersistentStateStore 打不开，
        # 自动退化为纯内存态（不落盘、不留 sqlite 句柄，Windows 下临时目录可删）
        self._block = os.path.join(self._tmp.name, "blocked")
        with open(self._block, "w", encoding="utf-8") as fh:
            fh.write("x")
        FakeOpenAI.calls = []
        FakeOpenAI.built = []
        FakeOpenAI.behavior = {}
        self._patch = mock.patch("openai.OpenAI", FakeOpenAI)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        for meth in ("_collect_local_scan_models", "_collect_desktop_models"):
            p = mock.patch.object(FreeModelRouter, meth, lambda self: [])
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    # ---- 隔离基线：除被测渠道外，一条出网路径都不开 ----
    def cfg(self, **extra):
        base = {
            "llm.free.state_dir": self._block,
            "llm.free.auto_catalog": "false",
            "llm.free.auto_discover_bridge": "false",
            "llm.free.include_ollama": "false",
            "llm.free.nvidia_catalog_md": "false",
            "llm.free.local_scan.enabled": "false",
            "llm.free.desktop_ai.enabled": "false",
            "llm.free.zero_config.enabled": "false",
        }
        base.update(extra)
        return FakeConfig.from_flat(base)

    def router(self, http=None, **extra):
        return FreeModelRouter(self.cfg(**extra), http_get=http)

    @staticmethod
    def models(router, provider=None):
        cs = router.candidates()
        if provider is None:
            return [c.model for c in cs]
        return [c.model for c in cs if c.provider == provider]

    # ---- 出站计数断言（两条路径的共用判据）----
    def assert_zero_outbound(self, http_calls):
        self.assertEqual(FakeOpenAI.built, [], "不该构造任何 openai 客户端")
        self.assertEqual(FakeOpenAI.calls, [], "不该发起任何 chat.completions 调用")
        self.assertEqual(list(http_calls), [], "不该发起任何 HTTP 请求")


# ===================================================================== #
# A. 判定函数本身（唯一判据 price_gate_verdict 的完整矩阵）
# ===================================================================== #
class TestPriceGateVerdict(_GateCase):

    def test_a_builtin_local_and_zero_config_sources_are_allowed(self):
        """(A) 内置本机/零配置直通源：地址确为本机/内网（或地址硬编码）→ builtin_free。"""
        r = self.router()
        # 地址硬编码的两个免 Key public 源：不传 base_url 也放行
        for prov in ("pollinations", "llm7"):
            ok, reason = r.price_gate_verdict(prov, "any-model-name")
            self.assertTrue(ok, prov)
            self.assertEqual(reason, "builtin_free", prov)
        # 地址可配置/运行时可变的四个源：给出本机地址即放行
        local_cases = (
            ("ollama", "loveai-qwen05b:latest", "http://localhost:11434/v1"),
            ("ollama", "x", "http://127.0.0.1:11434/v1"),
            ("local_bridge", "workbuddy/glm-5.3", "http://127.0.0.1:8787/v1"),
            ("local_scan", "qwen:7b", "http://127.0.0.1:11434/v1"),
            ("desktop", "trae/glm-5.3", "http://127.0.0.1:9/v1"),
            ("local_bridge", "m", "http://192.168.1.20:3000/v1"),
            ("local_bridge", "m", "http://10.0.0.5:8282/v1"),
            ("local_bridge", "m", "http://172.16.3.4:8282/v1"),
        )
        for prov, model, base in local_cases:
            self.assertEqual(r.price_gate_verdict(prov, model, base),
                             (True, "builtin_free"), f"{prov} {base}")

    def test_a_builtin_source_with_remote_base_is_not_builtin_free(self):
        """(A) 的地址可配置源：base_url 不在本机/内网 → 不算直通源（同类路径堵漏）。"""
        r = self.router()
        # 不传 base_url：无法证明是本机 → 不放行（fail-closed）
        for prov in ("ollama", "local_bridge", "local_scan", "desktop"):
            self.assertEqual(r.price_gate_verdict(prov, "some-model"),
                             (False, "endpoint_not_local"), prov)
        # 被指到公网付费端点 → 同样不放行
        for prov, base in (("ollama", "https://api.together.xyz/v1"),
                           ("local_bridge", "https://api.openai.com/v1"),
                           ("desktop", "https://api.moonshot.cn/v1"),
                           ("local_scan", "http://example.com/v1"),
                           ("ollama", "")):
            self.assertEqual(r.price_gate_verdict(prov, "gpt-4o", base),
                             (False, "endpoint_not_local"), f"{prov} {base}")

    def test_a_remote_based_builtin_source_can_be_rescued_by_whitelist(self):
        """名字是内置源但地址在公网：白名单仍然给用户一条显式放行通道。"""
        r = self.router(**{TRUSTED_MODELS_KEY: "ollama:some-model"})
        self.assertEqual(
            r.price_gate_verdict("ollama", "some-model", "https://api.together.xyz/v1"),
            (True, "trusted"))

    def test_b_free_suffix_is_proof_of_free(self):
        """(B) OpenRouter 自带的 ``:free`` 标记就是正面免费证据。"""
        r = self.router()
        self.assertEqual(r.price_gate_verdict("openrouter", "vendor/m:free"),
                         (True, "proven_free"))
        self.assertEqual(r.price_gate_verdict("nvidia", "vendor/m:free"),
                         (True, "proven_free"))

    def test_b_catalog_all_zero_pricing_is_proof_of_free(self):
        """(B) 目录五维全 0 → 进 free_ids → 放行。"""
        r = self.router()
        r._catalog["openrouter"] = {"models": ["vendor/zeroprice"], "ok": True, "ts": 0,
                                    "paid_ids": set(), "free_ids": {"vendor/zeroprice"}}
        self.assertEqual(r.price_gate_verdict("openrouter", "vendor/zeroprice"),
                         (True, "proven_free"))

    def test_c_unknown_openrouter_id_is_default_denied(self):
        """没有免费证据的公开模型：默认拒绝（这就是硬闸的方向）。"""
        r = self.router()
        self.assertEqual(r.price_gate_verdict("openrouter", "openai/gpt-4o"),
                         (False, "default_deny"))

    def test_c_nvidia_and_custom_are_never_builtin_allowed(self):
        """NVIDIA / custom 不在 (A) 表里 —— 它们永远拿不到『直接放行』。"""
        r = self.router()
        for prov in ("nvidia", "custom"):
            self.assertNotIn(prov, BUILTIN_FREE_PROVIDERS)
            self.assertEqual(r.price_gate_verdict(prov, "some-model"),
                             (False, "default_deny"))

    def test_c_whitelist_allows_exact_provider_and_id(self):
        r = self.router(**{TRUSTED_MODELS_KEY: "nvidia:meta/llama-3.3-70b-instruct"})
        self.assertEqual(r.price_gate_verdict("nvidia", "meta/llama-3.3-70b-instruct"),
                         (True, "trusted"))
        # 同一 provider 的别的 id 不受影响；同一 id 的别的 provider 也不受影响
        self.assertEqual(r.price_gate_verdict("nvidia", "other/model"),
                         (False, "default_deny"))
        self.assertEqual(r.price_gate_verdict("custom", "meta/llama-3.3-70b-instruct"),
                         (False, "default_deny"))

    def test_c_whitelist_provider_wildcard_covers_whole_endpoint(self):
        r = self.router(**{TRUSTED_MODELS_KEY: "custom:*"})
        for m in ("gpt-4o", "gpt-4o-mini", "workbuddy/glm-5.3"):
            self.assertEqual(r.price_gate_verdict("custom", m), (True, "trusted"))
        self.assertEqual(r.price_gate_verdict("nvidia", "gpt-4o"),
                         (False, "default_deny"))

    def test_c_whitelist_bare_id_matches_any_provider(self):
        r = self.router(**{TRUSTED_MODELS_KEY: "gpt-4o-mini"})
        self.assertEqual(r.price_gate_verdict("custom", "gpt-4o-mini"),
                         (True, "trusted"))
        self.assertEqual(r.price_gate_verdict("nvidia", "gpt-4o-mini"),
                         (True, "trusted"))

    def test_c_bare_star_does_not_open_the_floodgate(self):
        """裸 ``*`` 不生效（必须写成 provider:*）—— 防一个星号把全世界放进来。"""
        r = self.router(**{TRUSTED_MODELS_KEY: "*"})
        self.assertEqual(r.trusted_models(), ["*"])
        self.assertEqual(r.price_gate_verdict("openrouter", "openai/gpt-4o"),
                         (False, "default_deny"))
        self.assertEqual(r.price_gate_verdict("custom", "anything"),
                         (False, "default_deny"))

    def test_proven_paid_outranks_everything_including_wildcard(self):
        """**硬闸核心**：正面付费证据优先级最高，白名单（含端点级）也不得翻案。"""
        r = self.router(**{TRUSTED_MODELS_KEY: "openrouter:*,gpt-4o,openai/gpt-4o"})
        r._catalog["openrouter"] = {"models": [], "ok": True, "ts": 0,
                                    "paid_ids": {"openai/gpt-4o"}, "free_ids": set()}
        self.assertEqual(r.price_gate_verdict("openrouter", "openai/gpt-4o"),
                         (False, "proven_paid"))
        # 同一白名单里，没有付费证据的 id 仍然正常走 (C)
        self.assertEqual(r.price_gate_verdict("openrouter", "vendor/unlisted"),
                         (True, "trusted"))

    def test_trusted_entries_parsing_tolerates_colons_and_separators(self):
        """自带冒号的 id 不被截断；逗号/分号/中文逗号都能分隔。"""
        self.assertEqual(
            _parse_trusted_entries("openrouter:meta-llama/llama-3.3-70b-instruct:free"),
            [("openrouter", "meta-llama/llama-3.3-70b-instruct:free")])
        self.assertEqual(
            _parse_trusted_entries("nvidia:*; custom:gpt-4o，bare-id ，"),
            [("nvidia", "*"), ("custom", "gpt-4o"), ("", "bare-id")])
        # 非已知 provider 前缀 → 整条当裸 id（不会被当成 provider 解析）
        self.assertEqual(_parse_trusted_entries("openai:gpt-4o"),
                         [("", "openai:gpt-4o")])

    def test_trusted_models_list_is_trimmed_deduped_and_ordered(self):
        r = self.router(**{TRUSTED_MODELS_KEY: " b:1 , a:2 ,b:1,  ,c"})
        self.assertEqual(r.trusted_models(), ["b:1", "a:2", "c"])
        self.assertEqual(r.trusted_models_raw(), " b:1 , a:2 ,b:1,  ,c")

    def test_trusted_models_raw_survives_broken_config_source(self):
        """配置源读白名单时抛异常 → 当「没登记」处理（= 更严），绝不因此放开。"""
        block = self._block

        class _Boom:
            def get(self, key, default=None):
                if key == "llm.free.state_dir":     # 只为把状态库钉在临时目录
                    return block
                if key == TRUSTED_MODELS_KEY:
                    raise RuntimeError("配置源炸了")
                return default

        r = FreeModelRouter(_Boom())
        self.assertEqual(r.trusted_models(), [])
        self.assertEqual(r.price_gate_verdict("openrouter", "openai/gpt-4o"),
                         (False, "default_deny"))


# ===================================================================== #
# B. 实证①a / ②a：NVIDIA 显式配置分支
# ===================================================================== #
class TestNvidiaBranch(_GateCase):

    NV = {"llm.free.nvidia_key": "nvapi-test",
          "llm.free.nvidia_models": "openai/gpt-4o"}

    def test_paid_candidate_is_blocked_with_zero_outbound_calls(self):
        """**实证①a**：把付费模型写进 llm.free.nvidia_models，发不出去，出站 = 0。"""
        http, http_calls = make_http({})
        r = self.router(http=http, **self.NV)

        self.assertEqual(r.candidates(), [], "NVIDIA 分支的付费候选不该进池")
        self.assertEqual(self.models(r), [])
        self.assertEqual(r.ranked(), [])

        with self.assertRaises(RuntimeError) as cm:
            r.chat("你是助手", "你好")
        self.assertIn("免费模型池为空", str(cm.exception))
        # 付费候选被拒绝时，出站调用次数为 0（客户端都没构造）
        self.assert_zero_outbound(http_calls)

    def test_dynamic_nvidia_catalog_has_no_pricing_so_also_blocked(self):
        """残余 #1 的真实成因：NVIDIA /v1/models 不返回 pricing → 动态目录同样默认拒绝。"""
        http, http_calls = make_http({NVIDIA_MODELS_URL: NVIDIA_BODY,
                                      OPENROUTER_MODELS_URL: ConnectionError()})
        r = self.router(http=http,
                        **{"llm.free.nvidia_key": "nvapi-test",
                           "llm.free.auto_catalog": "true"})
        self.assertEqual(self.models(r, "nvidia"), [])                # 进不了池
        self.assertEqual(r.catalog_status()["nvidia"]["count"], 1)    # 目录确实拉到了
        with self.assertRaises(RuntimeError):
            r.chat("sys", "hi")
        self.assertEqual(FakeOpenAI.calls, [])
        self.assertEqual(FakeOpenAI.built, [])
        self.assertIn(NVIDIA_MODELS_URL, http_calls)                 # 只拉了目录，没调模型

    def test_whitelist_admits_the_same_candidate_and_it_is_really_called(self):
        """**实证②a**：同一个候选登记进白名单后能被放行，且真的被调用（非死代码）。"""
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **dict(self.NV,
                               **{TRUSTED_MODELS_KEY: "nvidia:openai/gpt-4o"}))

        self.assertIn("openai/gpt-4o", self.models(r, "nvidia"))
        self.assertEqual(r.routing_snapshot()["mode"]["trusted_models_count"], 1)

        out = r.chat("你是助手", "你好")
        self.assertEqual(out["routed_via"], "nvidia:openai/gpt-4o")
        self.assertEqual(FakeOpenAI.calls, ["openai/gpt-4o"])         # 真的发出去了
        self.assertEqual(http_calls, [])
        self.assertEqual(FakeOpenAI.built,
                         ["https://integrate.api.nvidia.com/v1"])

    def test_whitelist_survives_repeated_denials_dedup_log(self):
        """同一 id 的拒绝只告警一次（不刷屏），且告警里能读到白名单键名。"""
        http, _ = make_http({})
        r = self.router(http=http, **self.NV)
        with self.assertLogs("services.free_model_router", level="WARNING") as cap:
            r.candidates()
            r.candidates()
        hits = [m for m in cap.output
                if "openai/gpt-4o" in m and "已拒绝" in m and "fail-closed" in m]
        self.assertEqual(len(hits), 1, cap.output)
        self.assertIn(TRUSTED_MODELS_KEY, hits[0])
        self.assertIn("default_deny", hits[0])


# ===================================================================== #
# C. 实证①b / ②b：custom 自建端点分支
# ===================================================================== #
class TestCustomBranch(_GateCase):

    CUSTOM = {"llm.free.custom_base": "http://10.0.0.5:9999/v1",
              "llm.free.custom_models": "gpt-4o"}

    def test_paid_candidate_is_blocked_with_zero_outbound_calls(self):
        """**实证①b**：custom 端点没有公开价目表 → 无免费证据 → 默认拒绝，出站 = 0。"""
        http, http_calls = make_http({})
        r = self.router(http=http, **self.CUSTOM)

        self.assertEqual(r.candidates(), [])
        with self.assertRaises(RuntimeError) as cm:
            r.chat("你是助手", "你好")
        self.assertIn("免费模型池为空", str(cm.exception))
        self.assert_zero_outbound(http_calls)

    def test_loopback_custom_endpoint_is_also_blocked(self):
        """复查出的同类路径：回环地址的 custom 端点也照样要白名单（本地代理可能
        转发到付费上游）——不因为『地址看着像本机』就放行。"""
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **{"llm.free.custom_base": "http://127.0.0.1:8767/v1",
                           "llm.free.custom_model": "workbuddy/glm-5.3"})
        self.assertEqual(r.candidates(), [])
        with self.assertRaises(RuntimeError):
            r.chat("sys", "hi")
        self.assert_zero_outbound(http_calls)

    def test_whitelist_admits_the_same_candidate_and_it_is_really_called(self):
        """**实证②b**：登记 provider:id 后放行，并真的被调用。"""
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **dict(self.CUSTOM, **{TRUSTED_MODELS_KEY: "custom:gpt-4o"}))
        self.assertEqual(self.models(r, "custom"), ["gpt-4o"])
        out = r.chat("你是助手", "你好")
        self.assertEqual(out["routed_via"], "custom:gpt-4o")
        self.assertEqual(FakeOpenAI.calls, ["gpt-4o"])
        self.assertEqual(FakeOpenAI.built, ["http://10.0.0.5:9999/v1"])
        self.assertEqual(http_calls, [])

    def test_endpoint_wildcard_admits_all_models_of_that_endpoint(self):
        http, _ = make_http({})
        r = self.router(http=http,
                        **{"llm.free.custom_base": "http://10.0.0.5:9999/v1",
                           "llm.free.custom_models": "gpt-4o, gpt-4o-mini, glm-5",
                           TRUSTED_MODELS_KEY: "custom:*"})
        self.assertEqual(sorted(self.models(r, "custom")),
                         ["glm-5", "gpt-4o", "gpt-4o-mini"])


# ===================================================================== #
# D. 复查中发现的**同类路径**：内置本机源的「地址」来自配置时，也不能只凭名字放行
# ===================================================================== #
class TestConfigurableLocalEndpointLeak(_GateCase):
    """``llm.free.ollama_base`` / ``llm.free.bridge_probe_urls`` 都可以被指到公网付费
    端点 —— 那样「provider 名是本机源」就不成立，必须走白名单。"""

    def test_ollama_base_pointed_at_a_paid_endpoint_is_blocked(self):
        http, http_calls = make_http({})
        r = self.router(http=http, **{
            "llm.free.include_ollama": "true",
            "llm.free.ollama_base": "https://api.together.xyz/v1",
            "llm.free.ollama_models": "meta-llama/Llama-3-70b"})
        self.assertEqual(r.candidates(), [], "地址在公网时不应再当 (A) 直通源")
        with self.assertRaises(RuntimeError) as cm:
            r.chat("你是助手", "你好")
        self.assertIn("免费模型池为空", str(cm.exception))
        self.assert_zero_outbound(http_calls)

    def test_bridge_probe_url_pointed_at_a_paid_endpoint_is_blocked(self):
        remote_body = json.dumps({"data": [{"id": "gpt-4o"}]})
        http, http_calls = make_http({"https://api.paid.example/v1/models": remote_body,
                                      OPENROUTER_MODELS_URL: ConnectionError()})
        r = self.router(http=http, **{
            "llm.free.auto_catalog": "true",
            "llm.free.auto_discover_bridge": "true",
            "llm.free.bridge_probe_urls": "https://api.paid.example/v1"})
        self.assertEqual(r.candidates(), [])
        with self.assertRaises(RuntimeError):
            r.chat("你是助手", "你好")
        self.assertEqual(FakeOpenAI.calls, [])
        self.assertEqual(FakeOpenAI.built, [])
        self.assertIn("https://api.paid.example/v1/models", http_calls)  # 只探了目录

    def test_whitelist_admits_a_remote_based_builtin_source(self):
        http, http_calls = make_http({})
        r = self.router(http=http, **{
            "llm.free.include_ollama": "true",
            "llm.free.ollama_base": "https://api.together.xyz/v1",
            "llm.free.ollama_models": "meta-llama/Llama-3-70b",
            TRUSTED_MODELS_KEY: "ollama:meta-llama/Llama-3-70b"})
        self.assertEqual(self.models(r, "ollama"), ["meta-llama/Llama-3-70b"])
        out = r.chat("你是助手", "你好")
        self.assertEqual(out["routed_via"], "ollama:meta-llama/Llama-3-70b")
        self.assertEqual(FakeOpenAI.calls, ["meta-llama/Llama-3-70b"])
        self.assertEqual(FakeOpenAI.built, ["https://api.together.xyz/v1"])
        self.assertEqual(http_calls, [])


# ===================================================================== #
# E. 实证③：正面付费证据不可翻案
# ===================================================================== #
class TestProvenPaidCannotBeOverridden(_GateCase):

    CATALOG = {OPENROUTER_MODELS_URL: OR_BODY}

    def _router(self, trusted, models="openai/gpt-4o"):
        http, http_calls = make_http(dict(self.CATALOG))
        r = self.router(http=http,
                        **{"llm.free.openrouter_key": "sk-or-x",
                           "llm.free.auto_catalog": "true",
                           "llm.free.openrouter_models": models,
                           TRUSTED_MODELS_KEY: trusted})
        return r, http_calls

    def test_exact_whitelist_entry_cannot_revive_a_proven_paid_id(self):
        r, _ = self._router("openrouter:openai/gpt-4o")
        self.assertEqual(r.candidates(), [], "已证明付费的 id 不得因白名单复活")

    def test_endpoint_wildcard_cannot_revive_a_proven_paid_id(self):
        """连 provider:* 端点级白名单也不行 —— 付费证据优先级最高。"""
        r, _ = self._router("openrouter:*")
        self.assertEqual(r.candidates(), [])

    def test_bare_id_whitelist_cannot_revive_a_proven_paid_id(self):
        r, _ = self._router("openai/gpt-4o")
        self.assertEqual(r.candidates(), [])

    def test_denied_proven_paid_produces_zero_outbound_calls(self):
        """端到端：付费候选 + 白名单 → chat() 抛空池，出站调用 0 次。"""
        r, http_calls = self._router("openrouter:*")
        with self.assertRaises(RuntimeError) as cm:
            r.chat("你是助手", "你好")
        self.assertIn("免费模型池为空", str(cm.exception))
        self.assertEqual(FakeOpenAI.built, [])
        self.assertEqual(FakeOpenAI.calls, [])
        self.assertEqual(http_calls, [OPENROUTER_MODELS_URL])   # 只拉了目录

    def test_warning_says_whitelist_cannot_override(self):
        r, _ = self._router("openrouter:*")
        with self.assertLogs("services.free_model_router", level="WARNING") as cap:
            r.candidates()
        hits = [m for m in cap.output
                if "openai/gpt-4o" in m and "已拒绝" in m and "fail-closed" in m]
        self.assertEqual(len(hits), 1, cap.output)
        self.assertIn("白名单也不得翻案", hits[0])


# ===================================================================== #
# E. 实证④：不误杀（本机/零配置/：free 仍正常进池）
# ===================================================================== #
class TestNoCollateralDamage(_GateCase):

    def test_ollama_still_in_pool(self):
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **{"llm.free.include_ollama": "true",
                           "llm.free.ollama_models": "loveai-qwen05b:latest"})
        self.assertEqual(self.models(r, "ollama"), ["loveai-qwen05b:latest"])
        self.assertEqual(r.ranked()[0].provider, "ollama")
        self.assertEqual(http_calls, [])

    def test_local_bridge_still_in_pool(self):
        """本机桥发现走动态目录总闸，所以 auto_catalog 要开；8787 在线即应进池。"""
        http, _ = make_http({"http://127.0.0.1:8787/v1/models": BRIDGE_BODY,
                             "http://127.0.0.1:3000/v1/models": ConnectionError(),
                             "http://127.0.0.1:8282/v1/models": ConnectionError(),
                             OPENROUTER_MODELS_URL: ConnectionError()})
        r = self.router(http=http, **{"llm.free.auto_discover_bridge": "true",
                                      "llm.free.auto_catalog": "true"})
        self.assertEqual(self.models(r, "local_bridge"), ["workbuddy/glm-5.3"])

    def test_zero_config_providers_still_in_pool(self):
        http, http_calls = make_http({})
        r = self.router(http=http, **{"llm.free.zero_config.enabled": "true"})
        provs = {c.provider for c in r.candidates()}
        self.assertEqual(provs, {"pollinations", "llm7"})
        self.assertEqual(http_calls, [])

    def test_openrouter_static_free_suffix_still_in_pool(self):
        http, http_calls = make_http({})
        r = self.router(http=http, **{"llm.free.openrouter_key": "sk-or-x"})
        models = self.models(r, "openrouter")
        self.assertTrue(models)
        self.assertTrue(all(m.endswith(":free") for m in models), models)
        self.assertEqual(http_calls, [])

    def test_openrouter_dynamic_free_list_still_in_pool_and_paid_stays_out(self):
        http, _ = make_http({OPENROUTER_MODELS_URL: OR_BODY})
        r = self.router(http=http,
                        **{"llm.free.openrouter_key": "sk-or-x",
                           "llm.free.auto_catalog": "true"})
        models = self.models(r, "openrouter")
        self.assertIn("vendor/zeroprice", models)        # 五维全 0 → (B)
        self.assertIn("vendor/new-free:free", models)    # :free 标记 → (B)
        self.assertNotIn("openai/gpt-4o", models)        # 正面证明付费 → 拒绝
        self.assertNotIn("openrouter/free", models)

    def test_free_and_whitelisted_channels_coexist(self):
        """同一轮里：(A) 本机 + (B) :free + (C) 白名单的 nvidia 三者可以共存。"""
        http, _ = make_http({})
        r = self.router(http=http, **{
            "llm.free.include_ollama": "true",
            "llm.free.ollama_models": "loveai-qwen05b:latest",
            "llm.free.zero_config.enabled": "true",
            "llm.free.openrouter_key": "sk-or-x",
            "llm.free.nvidia_key": "nvapi-test",
            "llm.free.nvidia_models": "meta/llama-3.3-70b-instruct",
            TRUSTED_MODELS_KEY: "nvidia:meta/llama-3.3-70b-instruct",
        })
        provs = {c.provider for c in r.candidates()}
        self.assertEqual(provs, {"ollama", "pollinations", "llm7",
                                 "openrouter", "nvidia"})


# ===================================================================== #
# F. 设置页 / 诊断脚本可见性：白名单必须在 routing_snapshot() 里
# ===================================================================== #
class TestSnapshotExposesWhitelist(_GateCase):

    def test_mode_carries_whitelist_list_and_count(self):
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **{TRUSTED_MODELS_KEY: "nvidia:meta/llama-3.3-70b-instruct, custom:*"})
        snap = r.routing_snapshot()
        self.assertEqual(snap["mode"]["trusted_models"],
                         ["nvidia:meta/llama-3.3-70b-instruct", "custom:*"])
        self.assertEqual(snap["mode"]["trusted_models_count"], 2)
        self.assertEqual(snap["mode"]["price_gate"], "fail_closed_whitelist")
        # 原有两个键含义不变（追加式改动）
        self.assertTrue(snap["mode"]["free_only"])
        self.assertTrue(snap["mode"]["fail_closed"])
        json.dumps(snap, ensure_ascii=False)             # 必须可序列化
        self.assertEqual(http_calls, [], "快照不该联网")

    def test_mode_whitelist_reflects_a_single_trusted_channel_in_channels(self):
        http, _ = make_http({})
        r = self.router(http=http, **{
            "llm.free.nvidia_key": "nvapi-test",
            "llm.free.nvidia_models": "meta/llama-3.3-70b-instruct",
            TRUSTED_MODELS_KEY: "nvidia:meta/llama-3.3-70b-instruct"})
        snap = r.routing_snapshot()
        keys = [ch["key"] for ch in snap["channels"]]
        self.assertEqual(keys, ["nvidia:meta/llama-3.3-70b-instruct"])
        self.assertEqual(snap["mode"]["trusted_models_count"], 1)

    def test_default_snapshot_reports_empty_whitelist(self):
        http, _ = make_http({})
        r = self.router(http=http)
        snap = r.routing_snapshot()
        self.assertEqual(snap["mode"]["trusted_models"], [])
        self.assertEqual(snap["mode"]["trusted_models_count"], 0)
        self.assertEqual(snap["channels"], [])


# ===================================================================== #
# G. D2（对抗验证方）：白名单里未知 provider 名的**静默失效** → 语义不变 + 必须有告警
#
#    改前：``llm.free.trusted_models=foobar:*`` 被退化成裸 id ``"foobar:*"``，
#    ``"*"`` 不生效、谁也不匹配，用户以为白名单生效 —— **无任何日志**，极难排查。
#    改后：解析结果**一字不变**（仍按裸 id、不抛异常、不打断启动），但打一条
#    可自修复的 warning（同类条目只打一次）。
# ===================================================================== #
class TestUnknownProviderEntryWarns(_GateCase):

    def setUp(self):
        super().setUp()
        _TRUSTED_PARSE_WARNED.clear()      # 去重集合是模块级状态：逐测试清空，避免相互影响
        self.addCleanup(_TRUSTED_PARSE_WARNED.clear)

    @staticmethod
    def _capture_warnings():
        return mock.patch.object(fmr.logger, "warning")

    def test_unknown_provider_wildcard_degrades_to_bare_id_and_warns(self):
        """D2 主用例：``foobar:*`` → 落在裸 id 模式**且产生 warning**。"""
        with self._capture_warnings() as w:
            got = _parse_trusted_entries("foobar:*")
        self.assertEqual(got, [("", "foobar:*")], "语义不变：仍按裸 id 处理")
        self.assertEqual(w.call_count, 1, "必须恰好告警一次")
        fmt, token, head, known = w.call_args[0]
        self.assertIn("不在已知 provider 集合内", fmt)
        self.assertIn("裸 id", fmt)
        self.assertEqual(token, "foobar:*")
        self.assertEqual(head, "foobar")
        self.assertIn("groq", known)                 # 提示里给出已知 provider，可自修复
        self.assertIn("nvidia", known)

    def test_unknown_provider_exact_form_also_warns(self):
        """``xxx:yyy`` 形态同样告警（不只看 ``:*``）。"""
        with self._capture_warnings() as w:
            got = _parse_trusted_entries("foobar:gpt-4o")
        self.assertEqual(got, [("", "foobar:gpt-4o")])
        self.assertEqual(w.call_count, 1)

    def test_same_entry_warns_only_once(self):
        """同类条目只打一次（不刷屏；白名单每次读配置都会被解析）。"""
        with self._capture_warnings() as w:
            _parse_trusted_entries("zenith:*")
            _parse_trusted_entries("zenith:*")
            _parse_trusted_entries("zenith:*")
        self.assertEqual(w.call_count, 1)

    def test_colon_bearing_bare_id_stays_silent(self):
        """``vendor/model:free`` 是显然的带冒号裸 id → 不告警（防对合法配置刷屏）。"""
        with self._capture_warnings() as w:
            got = _parse_trusted_entries(
                "meta-llama/llama-3.3-70b-instruct:free")
        self.assertEqual(got, [("", "meta-llama/llama-3.3-70b-instruct:free")])
        self.assertEqual(w.call_count, 0)

    def test_regression_four_writing_styles_unchanged(self):
        """回归护栏：``provider:*`` / ``provider:id`` / 裸 ``id`` / 裸 ``*`` 逐条一致。"""
        with self._capture_warnings() as w:
            self.assertEqual(_parse_trusted_entries("groq:*"), [("groq", "*")])
            self.assertEqual(_parse_trusted_entries("groq:llama-3.1-8b"),
                             [("groq", "llama-3.1-8b")])
            self.assertEqual(_parse_trusted_entries("bare-id"), [("", "bare-id")])
            self.assertEqual(_parse_trusted_entries("*"), [("", "*")])
        self.assertEqual(w.call_count, 0, "已知前缀/裸 id/裸 * 都不该告警")
        # 端到端：裸 * 依旧不生效
        r = self.router(**{TRUSTED_MODELS_KEY: "*"})
        self.assertEqual(r.trusted_models(), ["*"])
        self.assertEqual(r.price_gate_verdict("openrouter", "openai/gpt-4o"),
                         (False, "default_deny"))
        self.assertEqual(r.price_gate_verdict("custom", "anything"),
                         (False, "default_deny"))

    def test_known_entries_unaffected_by_unknown_neighbours(self):
        """混入未知 provider 时，已知条目的匹配**不受影响**。"""
        raw = "foobar:*, groq:*, openrouter:vendor/unlisted, bare-id, *"
        with self._capture_warnings() as w:
            entries = _parse_trusted_entries(raw)
        self.assertEqual(entries, [
            ("", "foobar:*"),
            ("groq", "*"),
            ("openrouter", "vendor/unlisted"),
            ("", "bare-id"),
            ("", "*"),
        ])
        self.assertEqual(w.call_count, 1, "只有 foobar:* 这一条告警")
        r = self.router(**{TRUSTED_MODELS_KEY: raw})
        self.assertEqual(r.price_gate_verdict("groq", "llama-3.1-8b"),
                         (True, "trusted"))
        self.assertEqual(r.price_gate_verdict("openrouter", "vendor/unlisted"),
                         (True, "trusted"))
        # 未知条目的 "*" 不生效：只有 id **字面等于** "foobar:*" 才命中（裸 id 是字面匹配）
        self.assertEqual(r.price_gate_verdict("nvidia", "openai/gpt-4o"),
                         (False, "default_deny"))
        self.assertEqual(r.price_gate_verdict("nvidia", "foobar:*"),
                         (True, "trusted"))

    def test_provider_ids_all_known_to_the_whitelist_parser(self):
        """D2 顺带确认：10 个「需 Key 渠道」provider 名全在已知集合里（差集为空）。"""
        self.assertEqual(len(PROVIDER_IDS), 10)
        self.assertEqual(set(PROVIDER_IDS) - _TRUSTED_MODEL_PROVIDERS, set())
        self.assertTrue(set(PROVIDER_IDS) <= set(_TRUSTED_MODEL_PROVIDERS))


if __name__ == "__main__":
    unittest.main()
