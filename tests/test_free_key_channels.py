# -*- coding: utf-8 -*-
"""R3.8「需 Key 登记渠道」接入降级链 + 价格门双闸（fail-closed）。

被测对象
--------
``services/free_key_channels.py`` 描述的 10 条已登记渠道
（sambanova / zhipu / github_models / cloudflare_workers_ai / groq / cerebras /
 cohere / google_gemini / mistral / huggingface）已在 R3.8 接进
``FreeModelRouter.candidates()``（第 6 条来源），并照样要过出口价格门。

六组行为（任务书要求，缺一不可）
--------------------------------
  ① **无 key**                    → 该渠道不产候选；
  ② **有 key、无白名单**            → 候选为空 **且出站调用 0 次**（双闸第一道）；
  ③ **有 key + ``provider:*`` 白名单** → 候选出现且**真的被调用**（第二道闸打开）；
  ④ **缺必填配置**（cloudflare 缺 account_id）→ 整条跳过，base_url 不留未替换占位；
  ⑤ **``llm.free.keychannels.enabled=False``** → 该来源整体不产候选；
  ⑥ **内置源不受影响**（ollama / pollinations / llm7 / OpenRouter ``:free``）。

另含两组回归护栏（都是本轮实测发现的真缺陷，不锁住会静默复发）
--------------------------------------------------------------
  ⑦ ``_TRUSTED_MODEL_PROVIDERS`` 必须认这 10 个新 provider 名 —— 否则
     ``llm.free.trusted_models=groq:*`` 里的 "groq" 不被当 provider 前缀，会被
     ``_parse_trusted_entries`` 退化成**裸 id 模式 "groq:*"**，白名单写了却匹配不上
     任何模型、**静默失效**（无报错，极难排查）；
  ⑧ 白名单放行的新渠道必须能穿过 ``ranked()`` 的 remote bucket —— 否则候选在
     ``_sort_group`` 里被**静默丢弃**（原码注释原话），candidates() 里有、ranked() 里没有。

全部离线：``http_get`` 与 ``openai.OpenAI`` 都注入假实现，一个端口都不开、一个真实
请求都不发。
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

from services.free_key_channels import (                       # noqa: E402
    FREE_KEY_CHANNELS,
    PROVIDER_IDS,
    base_url_for,
    channels_with_keys,
    get_channel,
)
from services.free_model_router import (                       # noqa: E402
    BUILTIN_FREE_PROVIDERS,
    TRUSTED_MODELS_KEY,
    FreeModelRouter,
    _TRUSTED_MODEL_PROVIDERS,
    _parse_trusted_entries,
)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# 假 key（**不是真凭据**）：只用来证明「配了 key 会产候选」，不出网、不落盘。
FAKE_GROQ_KEY = "gsk_FAKEKEY_notreal_0000000000000000"
FAKE_CF_KEY = "cf_FAKEKEY_notreal"
FAKE_CF_ACCOUNT = "acct_fake_0001"


class FakeConfig:
    """点分 ``get``（``from_flat`` 会把扁平点分键搭成嵌套，与真实 ConfigService 同构）。"""

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
    """假 openai client：只**计数** + 返回预设响应（= 真正会出网、会产生费用的那一步）。"""

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
    """``routes``: {url: 响应体(str) 或 Exception}；未命中 url 一律连接失败。"""
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


class _KeyChanCase(unittest.TestCase):
    """公共夹具：临时 state_dir + 假 openai + 假本地采集 + 隔离基线（除被测渠道外不开出网路径）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="loveai_keychan_")
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
    def keychan_models(router):
        """当前候选里属于 10 条新渠道的 model 列表。"""
        return [c.model for c in router.candidates() if c.provider in PROVIDER_IDS]

    def assert_zero_outbound(self, http_calls):
        self.assertEqual(FakeOpenAI.built, [], "不该构造任何 openai 客户端")
        self.assertEqual(FakeOpenAI.calls, [], "不该发起任何 chat.completions 调用")
        self.assertEqual(list(http_calls), [], "不该发起任何 HTTP 请求")


# ===================================================================== #
# 0. 描述符与白名单前缀：两个「静默失效」缺陷的回归护栏
# ===================================================================== #
class TestDescriptorsAndTrustedPrefixes(_KeyChanCase):

    def test_ten_channels_are_registered(self):
        self.assertEqual(len(FREE_KEY_CHANNELS), 10)
        self.assertEqual(len(PROVIDER_IDS), 10)
        self.assertEqual(set(PROVIDER_IDS), {ch["id"] for ch in FREE_KEY_CHANNELS})

    def test_channel_ids_are_not_in_builtin_free_providers(self):
        """这 10 条**不是** (A) 内置直通源 —— 它们拿不到「直接放行」。"""
        for pid in PROVIDER_IDS:
            self.assertNotIn(pid, BUILTIN_FREE_PROVIDERS, pid)

    def test_trusted_model_providers_recognise_the_ten_new_providers(self):
        """护栏⑦：不认这 10 个名字 → ``groq:*`` 会退化成裸 id 而静默失效。"""
        for pid in PROVIDER_IDS:
            self.assertIn(pid, _TRUSTED_MODEL_PROVIDERS, pid)

    def test_provider_wildcard_parses_as_provider_prefix_not_bare_id(self):
        """护栏⑦的直接证据：``groq:*`` 必须解析成 ``("groq","*")``。"""
        self.assertEqual(_parse_trusted_entries("groq:*"), [("groq", "*")])
        self.assertEqual(_parse_trusted_entries("cloudflare_workers_ai:*"),
                         [("cloudflare_workers_ai", "*")])
        # 分隔符与大小写：分号/中文逗号/大写都能分隔与归一
        self.assertEqual(_parse_trusted_entries("GROQ:*; zhipu:glm-4-flash，mistral:*"),
                         [("groq", "*"), ("zhipu", "glm-4-flash"), ("mistral", "*")])

    def test_unknown_provider_prefix_still_treated_as_bare_id(self):
        """旧行为不回归：非已知 provider 前缀仍整条当裸 id（如 ``openai:gpt-4o``）。"""
        self.assertNotIn("openai", _TRUSTED_MODEL_PROVIDERS)
        self.assertEqual(_parse_trusted_entries("openai:gpt-4o"),
                         [("", "openai:gpt-4o")])

    def test_channels_with_keys_is_pure_and_masks_nothing_here(self):
        """纯函数层面：空 config → 无渠道；配了 key → 命中该渠道。"""
        self.assertEqual(channels_with_keys(self.cfg()), [])
        rows = channels_with_keys(self.cfg(**{"llm.free.groq_key": FAKE_GROQ_KEY}))
        self.assertEqual([r["id"] for r in rows], ["groq"])
        self.assertEqual(rows[0]["base_url_resolved"], "https://api.groq.com/openai/v1")
        self.assertEqual(rows[0]["missing_config"], [])


# ===================================================================== #
# ① 无 key → 该渠道不产候选
# ===================================================================== #
class TestNoKeyProducesNoCandidate(_KeyChanCase):

    def test_no_key_no_candidate_and_no_outbound(self):
        http, http_calls = make_http({})
        r = self.router(http=http)                     # 一条 key 都没配
        self.assertEqual(r.candidates(), [])
        self.assertEqual(self.keychan_models(r), [])
        for pid in PROVIDER_IDS:
            self.assertEqual([c.model for c in r.candidates() if c.provider == pid],
                             [], pid)
        self.assert_zero_outbound(http_calls)

    def test_key_of_other_channel_does_not_leak_in(self):
        """只配 groq 的 key → 另外 9 条一条都不产候选。"""
        http, _ = make_http({})
        r = self.router(http=http, **{"llm.free.groq_key": FAKE_GROQ_KEY})
        others = {c.provider for c in r.candidates()} - {"groq"}
        self.assertEqual(others & set(PROVIDER_IDS), set())


# ===================================================================== #
# ② 有 key、无白名单 → 候选为空 + 出站 0 次（双闸第一道）
# ===================================================================== #
class TestKeyWithoutWhitelistIsStillShut(_KeyChanCase):

    def test_key_without_whitelist_yields_empty_pool(self):
        http, http_calls = make_http({})
        r = self.router(http=http, **{"llm.free.groq_key": FAKE_GROQ_KEY})
        self.assertEqual(r.candidates(), [], "填了 key 但没进白名单 → 依然是 0 候选")
        self.assertEqual(r.ranked(), [])
        with self.assertRaises(RuntimeError) as cm:
            r.chat("你是助手", "你好")
        self.assertIn("免费模型池为空", str(cm.exception))
        self.assert_zero_outbound(http_calls)

    def test_each_channel_alone_is_shut_without_whitelist(self):
        """10 条逐个单独验证：有 key、无白名单 → 都不产候选（这就是 default_deny）。"""
        for ch in FREE_KEY_CHANNELS:
            with self.subTest(channel=ch["id"]):
                http, http_calls = make_http({})
                extra = {ch["key_config"]: "FAKEKEY-notreal-000000"}
                for placeholder, cfg_key in (ch.get("required_config") or {}).items():
                    extra[cfg_key] = f"fake_{placeholder}"     # 必填字段也补齐
                r = self.router(http=http, **extra)
                self.assertEqual(r.candidates(), [], ch["id"])
                self.assert_zero_outbound(http_calls)

    def test_verdict_is_default_deny_with_reason(self):
        """归宿可复现：这 10 条没有目录抓取器 → 证据集恒空 → ``default_deny``。"""
        r = self.router()
        r._catalog["groq"] = {"models": [], "ok": True, "ts": 0,
                              "paid_ids": set(), "free_ids": set()}
        self.assertEqual(r.price_gate_verdict("groq", "llama-3.1-8b-instant",
                                              "https://api.groq.com/openai/v1"),
                         (False, "default_deny"))

    def test_deny_warning_points_at_the_whitelist_config_key(self):
        r = self.router(**{"llm.free.groq_key": FAKE_GROQ_KEY})
        with self.assertLogs("services.free_model_router", level="WARNING") as cap:
            r.candidates()
        hits = [m for m in cap.output
                if "groq" in m and "llama-3.1-8b-instant" in m and "已拒绝" in m]
        self.assertTrue(hits, cap.output)
        self.assertIn(TRUSTED_MODELS_KEY, hits[0])
        self.assertIn("default_deny", hits[0])


# ===================================================================== #
# ③ 有 key + provider:* 白名单 → 进池且真的被调用（双闸第二道）
# ===================================================================== #
class TestKeyWithWhitelistEntersPool(_KeyChanCase):

    def test_groq_wildcard_admits_whole_endpoint(self):
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **{"llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "groq:*"})
        models = [c.model for c in r.candidates() if c.provider == "groq"]
        self.assertTrue(models, "白名单放行后必须产候选")
        self.assertEqual(models, ["llama-3.1-8b-instant", "llama-3.3-70b-versatile",
                                  "gpt-oss-120b"])
        self.assertEqual(http_calls, [], "产候选阶段不该发任何 HTTP 请求")

    def test_whitelisted_candidate_is_really_called(self):
        """**不是死代码**：白名单放行后 chat() 真的把请求发到该渠道官方端点。"""
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **{"llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "groq:*"})
        out = r.chat("你是助手", "你好")
        self.assertEqual(out["routed_via"], "groq:llama-3.1-8b-instant")
        self.assertEqual(FakeOpenAI.calls, ["llama-3.1-8b-instant"])
        self.assertEqual(FakeOpenAI.built, ["https://api.groq.com/openai/v1"])
        self.assertEqual(http_calls, [])

    def test_exact_whitelist_entry_also_works(self):
        """``provider:id`` 精确登记同样有效（只放行登记的那一个）。"""
        http, _ = make_http({})
        r = self.router(http=http,
                        **{"llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "groq:gpt-oss-120b"})
        models = [c.model for c in r.candidates() if c.provider == "groq"]
        self.assertEqual(models, ["gpt-oss-120b"])

    def test_whitelist_of_other_provider_does_not_open_this_one(self):
        http, _ = make_http({})
        r = self.router(http=http,
                        **{"llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "zhipu:*"})
        self.assertEqual([c.model for c in r.candidates() if c.provider == "groq"], [])

    def test_config_models_override_is_honoured_through_models_for(self):
        """三级回退借道 ``_models_for``：显式 config 覆盖优先于默认模型。"""
        http, _ = make_http({})
        r = self.router(http=http,
                        **{"llm.free.groq_key": FAKE_GROQ_KEY,
                           "llm.free.groq_models": "llama-3.1-8b-instant",
                           TRUSTED_MODELS_KEY: "groq:*"})
        models = [c.model for c in r.candidates() if c.provider == "groq"]
        self.assertEqual(models, ["llama-3.1-8b-instant"])

    def test_whitelisted_channel_survives_ranked_remote_bucket(self):
        """护栏⑧：放行后必须穿过 ``ranked()`` 的 remote bucket（否则被静默丢弃）。"""
        r = self.router(**{"llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "groq:*"})
        ranked = r.ranked()
        self.assertEqual({c.provider for c in ranked}, {"groq"})
        self.assertEqual([c.model for c in ranked],
                         ["llama-3.1-8b-instant", "llama-3.3-70b-versatile",
                          "gpt-oss-120b"])
        snap = r.routing_snapshot()
        self.assertEqual({c["provider"] for c in snap["channels"]}, {"groq"})
        self.assertEqual(snap["mode"]["price_gate"], "fail_closed_whitelist")
        self.assertEqual(snap["mode"]["trusted_models_count"], 1)

    def test_snapshot_is_json_serialisable(self):
        r = self.router(**{"llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "groq:*"})
        json.dumps(r.routing_snapshot())          # 不得抛


# ===================================================================== #
# ④ 缺必填配置（cloudflare 缺 account_id）→ 整条跳过，不留未替换占位
# ===================================================================== #
class TestMissingRequiredConfigSkipsChannel(_KeyChanCase):

    CF = {"llm.free.cloudflare_key": FAKE_CF_KEY}

    def test_cloudflare_without_account_id_is_skipped(self):
        http, http_calls = make_http({})
        r = self.router(http=http, **self.CF)
        self.assertEqual([c.model for c in r.candidates()
                          if c.provider == "cloudflare_workers_ai"], [])
        self.assert_zero_outbound(http_calls)

    def test_no_candidate_ever_carries_an_unreplaced_placeholder(self):
        """全池扫描：任何候选的 base_url 都不许含 ``{占位}``。"""
        http, _ = make_http({})
        r = self.router(http=http, **dict(self.CF, **{TRUSTED_MODELS_KEY: "cloudflare_workers_ai:*"}))
        for c in r.candidates():
            self.assertNotIn("{", c.base_url, c.base_url)
            self.assertNotIn("}", c.base_url, c.base_url)

    def test_descriptor_never_hands_out_a_placeholder_url(self):
        """D3 契约：缺 account_id 时 ``base_url_resolved`` **置空串**（不交出坏地址），
        ``missing_config`` 仍如实报出缺失字段；底层 ``base_url_for()`` 仍保留占位。"""
        rows = channels_with_keys(self.cfg(**self.CF))
        self.assertEqual([r["id"] for r in rows], ["cloudflare_workers_ai"])
        row = rows[0]
        self.assertEqual(row["missing_config"], ["account_id"])
        # 契约：必填配置不齐 → 不交地址
        self.assertEqual(row["base_url_resolved"], "")
        # 契约：非空 ⇔ 不含 ``{``（对同一 config 下所有返回行都成立）
        for r_ in channels_with_keys(self.cfg(**self.CF)):
            resolved = r_["base_url_resolved"]
            if resolved:
                self.assertNotIn("{", resolved, r_["id"])
                self.assertNotIn("}", resolved, r_["id"])
            else:
                self.assertTrue(r_["missing_config"], r_["id"])
        # 底层函数仍保留占位（那是它的既定语义，供诊断展示「地址长什么样」）
        channel = get_channel("cloudflare_workers_ai")
        self.assertIn("{account_id}", base_url_for(channel, self.cfg(**self.CF)))

    def test_with_account_id_configured_the_channel_becomes_usable(self):
        """补齐 account_id 后：占位被替换，且配合白名单能真正产候选。"""
        http, _ = make_http({})
        r = self.router(http=http,
                        **dict(self.CF,
                               **{"llm.free.cloudflare_account_id": FAKE_CF_ACCOUNT,
                                  TRUSTED_MODELS_KEY: "cloudflare_workers_ai:*"}))
        cs = [c for c in r.candidates() if c.provider == "cloudflare_workers_ai"]
        self.assertTrue(cs)
        for c in cs:
            self.assertNotIn("{", c.base_url)
            self.assertIn(FAKE_CF_ACCOUNT, c.base_url)
            self.assertTrue(c.base_url.startswith(
                "https://api.cloudflare.com/client/v4/accounts/"), c.base_url)

    def test_account_id_without_key_still_produces_nothing(self):
        http, _ = make_http({})
        r = self.router(http=http,
                        **{"llm.free.cloudflare_account_id": FAKE_CF_ACCOUNT})
        self.assertEqual([c for c in r.candidates()
                          if c.provider == "cloudflare_workers_ai"], [])

    def test_contract_holds_for_every_row_when_all_keys_are_set(self):
        """**全渠道不变量**（D3 契约锁）：10 条都配了（假）key 时，
        每一行都要满足 —— ``base_url_resolved`` 非空 ⇒ 不含 ``{``／``}``；
        且**当且仅当** ``missing_config`` 为空时才非空。
        预期只有 cloudflare 因缺 ``account_id`` 落空串。"""
        all_keys = {ch["key_config"]: "FAKE_KEY_notreal_0000" for ch in FREE_KEY_CHANNELS}
        rows = channels_with_keys(self.cfg(**all_keys))
        self.assertEqual(len(rows), 10, "10 条都配了 key，应返回 10 行")
        empty_ids = []
        for row in rows:
            resolved = row["base_url_resolved"]
            if resolved:
                self.assertNotIn("{", resolved, row["id"])
                self.assertNotIn("}", resolved, row["id"])
                self.assertEqual(row["missing_config"], [], row["id"])
            else:
                empty_ids.append(row["id"])
                self.assertTrue(row["missing_config"], row["id"])
        self.assertEqual(empty_ids, ["cloudflare_workers_ai"])


# ===================================================================== #
# ⑤ keychannels.enabled=False → 该来源整体不产候选
# ===================================================================== #
class TestSwitchOff(_KeyChanCase):

    def test_disabled_switch_produces_no_candidate_even_with_whitelist(self):
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **{"llm.free.keychannels.enabled": "false",
                           "llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "groq:*"})
        self.assertEqual(r.candidates(), [])
        with self.assertRaises(RuntimeError):
            r.chat("你是助手", "你好")
        self.assert_zero_outbound(http_calls)

    def test_switch_defaults_to_on(self):
        r = self.router(**{"llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "groq:*"})
        self.assertTrue([c for c in r.candidates() if c.provider == "groq"])

    def test_switch_off_does_not_affect_builtin_sources(self):
        """关掉新来源，不能顺手把内置源也关掉。"""
        http, _ = make_http({})
        r = self.router(http=http,
                        **{"llm.free.keychannels.enabled": "false",
                           "llm.free.zero_config.enabled": "true"})
        self.assertEqual({c.provider for c in r.candidates()},
                         {"pollinations", "llm7"})


# ===================================================================== #
# ⑥ 内置源不受影响（不误杀对照）
# ===================================================================== #
class TestNoCollateralDamage(_KeyChanCase):

    def test_ollama_still_in_pool(self):
        http, http_calls = make_http({})
        r = self.router(http=http,
                        **{"llm.free.include_ollama": "true",
                           "llm.free.ollama_models": "loveai-qwen05b:latest"})
        self.assertEqual([c.model for c in r.candidates() if c.provider == "ollama"],
                         ["loveai-qwen05b:latest"])
        self.assertEqual(http_calls, [])

    def test_zero_config_providers_still_in_pool(self):
        http, http_calls = make_http({})
        r = self.router(http=http, **{"llm.free.zero_config.enabled": "true"})
        self.assertEqual({c.provider for c in r.candidates()}, {"pollinations", "llm7"})
        self.assertEqual(http_calls, [])

    def test_openrouter_free_suffix_still_in_pool(self):
        http, http_calls = make_http({})
        r = self.router(http=http, **{"llm.free.openrouter_key": "sk-or-x"})
        models = [c.model for c in r.candidates() if c.provider == "openrouter"]
        self.assertTrue(models)
        self.assertTrue(all(m.endswith(":free") for m in models), models)
        self.assertEqual(http_calls, [])

    def test_builtin_pool_is_unchanged_by_an_unwhitelisted_new_channel(self):
        """同一轮：内置源该进池的都进池；新渠道没白名单则一条都不混进来。"""
        http, _ = make_http({})
        r = self.router(http=http,
                        **{"llm.free.include_ollama": "true",
                           "llm.free.ollama_models": "loveai-qwen05b:latest",
                           "llm.free.zero_config.enabled": "true",
                           "llm.free.openrouter_key": "sk-or-x",
                           "llm.free.groq_key": FAKE_GROQ_KEY})   # 有 key，但无白名单
        provs = {c.provider for c in r.candidates()}
        self.assertEqual(provs, {"ollama", "pollinations", "llm7", "openrouter"})
        self.assertEqual(provs & set(PROVIDER_IDS), set())

    def test_builtin_and_whitelisted_channel_coexist(self):
        """(A) 内置 + (B) :free + (C) 白名单新渠道 三者可共存，且排序正常。"""
        http, _ = make_http({})
        r = self.router(http=http,
                        **{"llm.free.include_ollama": "true",
                           "llm.free.ollama_models": "loveai-qwen05b:latest",
                           "llm.free.zero_config.enabled": "true",
                           "llm.free.openrouter_key": "sk-or-x",
                           "llm.free.groq_key": FAKE_GROQ_KEY,
                           TRUSTED_MODELS_KEY: "groq:*"})
        provs = {c.provider for c in r.candidates()}
        self.assertEqual(provs,
                         {"ollama", "pollinations", "llm7", "openrouter", "groq"})
        ranked = r.ranked()
        self.assertEqual(ranked[0].provider, "ollama")      # 本机零成本仍最优先
        self.assertIn("groq", {c.provider for c in ranked})


if __name__ == "__main__":
    unittest.main(verbosity=2)
