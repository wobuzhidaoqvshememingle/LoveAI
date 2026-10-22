# -*- coding: utf-8 -*-
"""FreeModelRouter 免费模型路由单元测试（全部离线、不发真实网络请求）。"""
import json, os, sys, unittest
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.free_model_router import (
    FreeModelRouter, OPENROUTER_MODELS_URL, NVIDIA_MODELS_URL,
    NVIDIA_CATALOG_MD_URL, _parse_nvidia_catalog_md)

# 本文件只验证免费路由的静态/动态目录逻辑：在各测试类 setUp 中用 mock 临时禁用
# 「本地扫描/桌面 AI」收集（自动还原），避免 local_scan 默认开时捡到真机 Ollama 端点。
from unittest import mock


def _patch_local_collectors(testcase):
    for meth in ("_collect_local_scan_models", "_collect_desktop_models"):
        p = mock.patch.object(FreeModelRouter, meth, lambda self: [])
        p.start()
        testcase.addCleanup(p.stop)

# ---- 受控的假目录响应 ----
OR_BODY = json.dumps({"data": [
    {"id": "vendor/new-free:free", "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "vendor/zeroprice", "pricing": {"prompt": "0", "completion": "0"}},
    {"id": "vendor/paid", "pricing": {"prompt": "0.0001", "completion": "0.0002"}},
    {"id": "openrouter/free", "pricing": {"prompt": "0", "completion": "0"}},
]})
NVIDIA_BODY = json.dumps({"data": [{"id": "vendor/dyn-nim"}]})
NVIDIA_MD = "intro [`vendor/md-model`] and (vendor/another-id) in catalog"


def make_http(routes):
    """routes: {url: body(str) 或 Exception}。返回 (http_get, calls)。"""
    calls = []

    def _get(url, timeout=None, headers=None):
        calls.append(url)
        r = routes.get(url)
        if isinstance(r, Exception):
            raise r
        return r
    return _get, calls


class TestFreeRouter(unittest.TestCase):
    """原有行为：关闭自动目录，纯走静态/config，保证离线确定。"""

    def setUp(self):
        _patch_local_collectors(self)

    def test_disabled(self):
        r = FreeModelRouter({"llm.free.enabled": "false"})
        self.assertEqual(r.candidates(), [])

    def test_no_key_no_ollama(self):
        # 无 key / 无 ollama / 无动态目录 / 关闭零配置 provider -> 空候选
        r = FreeModelRouter({"llm.free.include_ollama": "false",
                             "llm.free.auto_catalog": "false",
                             "llm.free.zero_config.enabled": "false"})
        self.assertEqual(r.candidates(), [])

    def test_openrouter_candidates(self):
        r = FreeModelRouter({"llm.free.openrouter_key": "sk-or-x",
                             "llm.free.auto_catalog": "false"})
        provs = {(c.provider, c.model) for c in r.candidates()}
        self.assertTrue(any(p == "openrouter" for p, _ in provs))
        self.assertTrue(any(m.endswith(":free") for _, m in provs))

    def test_nvidia_candidates(self):
        # R3.7 行为变更：NVIDIA /v1/models 不返回 pricing → 拿不到任何免费证据 →
        # **默认拒绝**（改前是「证据不足则放行」）。登记白名单后同一份配置即可放行，
        # 证明渠道接线本身没变，变的只是价格门口径。
        blocked = FreeModelRouter({"llm.free.nvidia_key": "nvapi-x",
                                   "llm.free.include_ollama": "false",
                                   "llm.free.auto_catalog": "false",
                                   "llm.free.zero_config.enabled": "false",
                                   "llm.free.auto_discover_bridge": "false",
                                   "llm.free.local_scan.enabled": "false",
                                   "llm.free.desktop_ai.enabled": "false"})
        self.assertFalse(any(c.provider == "nvidia" for c in blocked.candidates()))

        r = FreeModelRouter({"llm.free.nvidia_key": "nvapi-x",
                             "llm.free.include_ollama": "false",
                             "llm.free.auto_catalog": "false",
                             "llm.free.trusted_models": "nvidia:*"})
        self.assertTrue(any(c.provider == "nvidia" for c in r.candidates()))

    def test_ollama_default_first(self):
        r = FreeModelRouter({"llm.free.openrouter_key": "sk-or-x",
                             "llm.free.auto_catalog": "false"})
        self.assertEqual(r.ranked()[0].provider, "ollama")

    def test_rank_by_success_rate(self):
        r = FreeModelRouter({"llm.free.openrouter_key": "sk-or-x",
                             "llm.free.include_ollama": "false",
                             "llm.free.auto_catalog": "false"})
        cands = r.candidates()
        cands[0].ok, cands[0].fail = 9, 1
        cands[-1].ok, cands[-1].fail = 1, 9
        order = r.ranked()
        self.assertGreater(order[0].success_rate, order[-1].success_rate)

    def test_chat_no_candidates_raises(self):
        r = FreeModelRouter({"llm.free.enabled": "false"})
        with self.assertRaises(RuntimeError):
            r.chat("sys", "hi")

    def test_health_report(self):
        r = FreeModelRouter({"llm.free.openrouter_key": "sk-or-x",
                             "llm.free.auto_catalog": "false"})
        r.candidates()[0].ok = 3
        rep = r.health_report()
        self.assertGreater(len(rep), 0)
        self.assertIn("success_rate", rep[0])

    def test_custom_loopback_endpoint(self):
        # R3.7 行为变更：任意 OpenAI 兼容端点（哪怕地址是回环）没有公开价目表 →
        # 拿不到免费证据 → **默认拒绝**；登记白名单后才进池，且优先级仍最高。
        blocked = FreeModelRouter({"llm.free.custom_base": "http://127.0.0.1:8767/v1",
                                   "llm.free.custom_model": "workbuddy/glm-5.3",
                                   "llm.free.include_ollama": "false",
                                   "llm.free.auto_catalog": "false",
                                   "llm.free.zero_config.enabled": "false"})
        self.assertEqual(blocked.candidates(), [])

        r = FreeModelRouter({"llm.free.custom_base": "http://127.0.0.1:8767/v1",
                             "llm.free.custom_model": "workbuddy/glm-5.3",
                             "llm.free.include_ollama": "false",
                             "llm.free.auto_catalog": "false",
                             "llm.free.trusted_models": "custom:workbuddy/glm-5.3"})
        cands = r.candidates()
        self.assertTrue(any(c.provider == "custom" for c in cands))
        self.assertEqual(r.ranked()[0].provider, "custom")


class TestDynamicCatalog(unittest.TestCase):
    """动态目录：自动发现、三级回退、TTL 定时刷新。"""

    def setUp(self):
        _patch_local_collectors(self)

    OR_ONLY = {"llm.free.openrouter_key": "k",
               "llm.free.include_ollama": "false",
               "llm.free.nvidia_catalog_md": "false"}

    def test_openrouter_filter_free(self):
        http, calls = make_http({OPENROUTER_MODELS_URL: OR_BODY})
        r = FreeModelRouter(self.OR_ONLY, http_get=http)
        models = {c.model for c in r.candidates() if c.provider == "openrouter"}
        self.assertIn("vendor/new-free:free", models)
        self.assertIn("vendor/zeroprice", models)
        self.assertNotIn("vendor/paid", models)          # 付费排除
        self.assertNotIn("openrouter/free", models)      # 聚合路由排除

    def test_dynamic_over_static(self):
        http, _ = make_http({OPENROUTER_MODELS_URL: OR_BODY})
        r = FreeModelRouter(self.OR_ONLY, http_get=http)
        models = {c.model for c in r.candidates() if c.provider == "openrouter"}
        self.assertIn("vendor/new-free:free", models)
        # 动态成功后不再使用内置静态目录
        self.assertNotIn("meta-llama/llama-3.3-70b-instruct:free", models)

    def test_config_models_override_dynamic(self):
        http, _ = make_http({OPENROUTER_MODELS_URL: OR_BODY})
        cfg = dict(self.OR_ONLY)
        cfg["llm.free.openrouter_models"] = "explicit/m:free"
        r = FreeModelRouter(cfg, http_get=http)
        models = [c.model for c in r.candidates() if c.provider == "openrouter"]
        self.assertEqual(models, ["explicit/m:free"])

    def test_http_error_fallback_static(self):
        http, _ = make_http({OPENROUTER_MODELS_URL: TimeoutError()})
        r = FreeModelRouter(self.OR_ONLY, http_get=http)
        models = {c.model for c in r.candidates() if c.provider == "openrouter"}
        self.assertIn("meta-llama/llama-3.3-70b-instruct:free", models)
        self.assertFalse(r.catalog_status()["openrouter"]["ok"])

    def test_auto_catalog_disabled_no_network(self):
        http, calls = make_http({OPENROUTER_MODELS_URL: OR_BODY})
        cfg = dict(self.OR_ONLY)
        cfg["llm.free.auto_catalog"] = "false"
        r = FreeModelRouter(cfg, http_get=http)
        r.candidates()
        self.assertEqual(calls, [])

    def test_catalog_ttl_and_force(self):
        t = {"v": 1000.0}
        clock = lambda: t["v"]
        http, calls = make_http({OPENROUTER_MODELS_URL: OR_BODY})
        r = FreeModelRouter(self.OR_ONLY, http_get=http, clock=clock)

        r.candidates()
        self.assertEqual(calls.count(OPENROUTER_MODELS_URL), 1)
        r.candidates()  # TTL 内，不重拉
        self.assertEqual(calls.count(OPENROUTER_MODELS_URL), 1)
        t["v"] += 25 * 3600  # 超过默认 24h TTL
        r.candidates()
        self.assertEqual(calls.count(OPENROUTER_MODELS_URL), 2)
        r.refresh_catalog(force=True)
        self.assertEqual(calls.count(OPENROUTER_MODELS_URL), 3)

    def test_nvidia_key_dynamic(self):
        # R3.7 行为变更：NIM 动态目录照旧拉到（catalog 可见），但它的响应不含 pricing
        # → 拿不到免费证据 → 默认拒绝；把该 id 登记进白名单后才进池。
        http, calls = make_http({NVIDIA_MODELS_URL: NVIDIA_BODY,
                                 OPENROUTER_MODELS_URL: TimeoutError()})
        cfg = {"llm.free.nvidia_key": "nvapi-x",
               "llm.free.include_ollama": "false",
               "llm.free.nvidia_catalog_md": "false"}
        r = FreeModelRouter(cfg, http_get=http)
        self.assertEqual([c.model for c in r.candidates()
                          if c.provider == "nvidia"], [])
        self.assertEqual(r.catalog_status()["nvidia"]["count"], 1)
        self.assertIn(NVIDIA_MODELS_URL, calls)

        cfg2 = dict(cfg)
        cfg2["llm.free.trusted_models"] = "nvidia:vendor/dyn-nim"
        r2 = FreeModelRouter(cfg2, http_get=http)
        self.assertEqual([c.model for c in r2.candidates()
                          if c.provider == "nvidia"], ["vendor/dyn-nim"])

    def test_nvidia_no_key_catalog_md(self):
        http, calls = make_http({NVIDIA_CATALOG_MD_URL: NVIDIA_MD,
                                 OPENROUTER_MODELS_URL: TimeoutError()})
        cfg = {"llm.free.include_ollama": "false"}
        r = FreeModelRouter(cfg, http_get=http)
        r.refresh_catalog()
        nv = r.catalog_status()["nvidia"]
        self.assertTrue(nv["ok"])
        self.assertEqual(nv["count"], 2)
        self.assertIn(NVIDIA_CATALOG_MD_URL, calls)

    def test_parse_nvidia_catalog_md(self):
        ids = _parse_nvidia_catalog_md(NVIDIA_MD)
        self.assertEqual(ids, ["vendor/another-id", "vendor/md-model"])

    def test_catalog_report(self):
        http, _ = make_http({OPENROUTER_MODELS_URL: OR_BODY})
        r = FreeModelRouter(self.OR_ONLY, http_get=http)
        r.refresh_catalog()
        rep = r.catalog_report()
        orow = [x for x in rep if x["source"] == "openrouter"][0]
        self.assertTrue(orow["ok"])
        self.assertGreaterEqual(orow["count"], 2)

    def test_auto_discover_local_bridge(self):
        bridge = json.dumps({"data": [{"id": "workbuddy/glm-5.3"},
                                      {"id": "workbuddy/kimi-k3"}]})
        routes = {OPENROUTER_MODELS_URL: TimeoutError(),
                  "http://127.0.0.1:8787/v1/models": bridge,
                  "http://127.0.0.1:3000/v1/models": ConnectionError(),
                  "http://127.0.0.1:8282/v1/models": ConnectionError()}
        http, calls = make_http(routes)
        cfg = {"llm.free.include_ollama": "false",
               "llm.free.nvidia_catalog_md": "false"}
        r = FreeModelRouter(cfg, http_get=http)
        r.refresh_catalog()
        lb = r.catalog_status()["local_bridge"]
        self.assertTrue(lb["ok"])
        self.assertEqual(lb["count"], 2)
        cands = [c for c in r.candidates() if c.provider == "local_bridge"]
        self.assertEqual({c.model for c in cands},
                         {"workbuddy/glm-5.3", "workbuddy/kimi-k3"})
        self.assertTrue(all(c.base_url == "http://127.0.0.1:8787/v1" for c in cands))
        self.assertEqual(r.ranked()[0].provider, "local_bridge")

    def test_no_bridge_quiet(self):
        routes = {OPENROUTER_MODELS_URL: TimeoutError(),
                  "http://127.0.0.1:8787/v1/models": ConnectionError(),
                  "http://127.0.0.1:3000/v1/models": ConnectionError(),
                  "http://127.0.0.1:8282/v1/models": ConnectionError()}
        http, _ = make_http(routes)
        r = FreeModelRouter({"llm.free.include_ollama": "false",
                             "llm.free.nvidia_catalog_md": "false"}, http_get=http)
        r.refresh_catalog()
        self.assertFalse(r.catalog_status()["local_bridge"]["ok"])
        self.assertEqual([c for c in r.candidates() if c.provider == "local_bridge"], [])

    def test_disable_bridge_discovery(self):
        http, calls = make_http({OPENROUTER_MODELS_URL: OR_BODY})
        cfg = dict(self.OR_ONLY)
        cfg["llm.free.auto_discover_bridge"] = "false"
        r = FreeModelRouter(cfg, http_get=http)
        r.refresh_catalog()
        self.assertNotIn("local_bridge", r.catalog_status())
        self.assertFalse(any("8787" in u for u in calls))

    def test_custom_probe_urls(self):
        body = json.dumps({"data": [{"id": "x/m"}]})
        routes = {"http://127.0.0.1:9999/v1/models": body,
                  OPENROUTER_MODELS_URL: TimeoutError()}
        http, calls = make_http(routes)
        cfg = {"llm.free.include_ollama": "false",
               "llm.free.nvidia_catalog_md": "false",
               "llm.free.bridge_probe_urls": "http://127.0.0.1:9999/v1"}
        r = FreeModelRouter(cfg, http_get=http)
        r.refresh_catalog()
        self.assertTrue(r.catalog_status()["local_bridge"]["ok"])
        self.assertIn("http://127.0.0.1:9999/v1/models", calls)


class TestZeroConfigApiProviders(unittest.TestCase):
    """零配置免费 HTTP provider（pollinations/llm7）接入路由验证（离线、无真实请求）。

    背景：这两家在 registry 里标注 verified_ok（实测 chat 200 真出文本），
    但此前 FreeModelRouter.candidates() 从不发射它们 -> 真实请求走 free 层永远命中不了。
    这里验证它们真正进入候选与排序。
    """

    def setUp(self):
        _patch_local_collectors(self)

    def _base_cfg(self):
        # 隔离其它源：无 key / 无 ollama / 无动态目录 / 无桥 / 无本地扫描
        return {"llm.free.include_ollama": "false",
                "llm.free.auto_catalog": "false",
                "llm.free.nvidia_catalog_md": "false"}

    def test_pollinations_emitted_as_candidate(self):
        r = FreeModelRouter(self._base_cfg())
        cands = [c for c in r.candidates() if c.provider == "pollinations"]
        self.assertTrue(cands, "pollinations 应进入候选")
        bases = {c.base_url for c in cands}
        self.assertEqual(bases, {"https://text.pollinations.ai/openai"})
        models = {c.model for c in cands}
        self.assertIn("openai-fast", models)
        # 零配置：api_key 为占位串，不需要真 key
        self.assertTrue(all(c.api_key for c in cands))

    def test_llm7_emitted_as_candidate(self):
        r = FreeModelRouter(self._base_cfg())
        cands = [c for c in r.candidates() if c.provider == "llm7"]
        self.assertTrue(cands, "llm7 应进入候选")
        bases = {c.base_url for c in cands}
        self.assertEqual(bases, {"https://api.llm7.io/v1"})
        models = {c.model for c in cands}
        self.assertIn("DeepSeek-V4-Flash-0731", models)

    def test_zero_config_enabled_by_default(self):
        # 不传任何开关 -> pollinations/llm7 默认进入候选（免注册开箱即用）
        r = FreeModelRouter(self._base_cfg())
        provs = {c.provider for c in r.candidates()}
        self.assertIn("pollinations", provs)
        self.assertIn("llm7", provs)

    def test_zero_config_can_be_disabled(self):
        cfg = dict(self._base_cfg())
        cfg["llm.free.zero_config.enabled"] = "false"
        r = FreeModelRouter(cfg)
        provs = {c.provider for c in r.candidates()}
        self.assertNotIn("pollinations", provs)
        self.assertNotIn("llm7", provs)

    def test_zero_config_models_override(self):
        cfg = dict(self._base_cfg())
        cfg["llm.free.pollinations_models"] = "poll-fast,poll-pro"
        r = FreeModelRouter(cfg)
        models = {c.model for c in r.candidates() if c.provider == "pollinations"}
        self.assertEqual(models, {"poll-fast", "poll-pro"})

    def test_zero_config_survives_ranked_sort(self):
        # 回归：_sort_group 远端桶必须含 pollinations/llm7，否则会被静默丢弃
        r = FreeModelRouter(self._base_cfg())
        ranked = r.ranked()
        provs = {c.provider for c in ranked}
        self.assertIn("pollinations", provs)
        self.assertIn("llm7", provs)

    def test_zero_config_chat_uses_pollinations(self):
        """mock openai：ollama/key 源全关，pollinations 应成为首个可用候选并真的被调到。"""
        from unittest import mock as _mock

        class _Msg:
            def __init__(self, c): self.content = c
        class _Choice:
            def __init__(self, c): self.message = _Msg(c)
        class _Resp:
            def __init__(self, c): self.choices = [_Choice(c)]

        calls = []

        class _Completions:
            def create(self, **kw):
                calls.append(kw["model"])
                return _Resp("hello from pollinations")
        class _Chat:
            completions = _Completions()
        class _Client:
            chat = _Chat()

        r = FreeModelRouter(self._base_cfg())
        with _mock.patch("openai.OpenAI", lambda *a, **k: _Client()):
            out = r.chat("sys", "hi")
        self.assertEqual(out["text"], "hello from pollinations")
        self.assertEqual(out["provider"], "pollinations")
        self.assertTrue(calls, "应至少调用一次 chat.completions")


if __name__ == "__main__":
    unittest.main()
