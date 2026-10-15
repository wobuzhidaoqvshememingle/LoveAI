# -*- coding: utf-8 -*-
"""v3.2 零成本中枢接线离线确定性测试（收口版架构）。

固化口径：
  · 本地扫描 local_scan：**默认开**（开箱即生效，与设置页初始勾选一致）；
  · 桌面 AI desktop_ai / 浏览器自动化 browser_ai：默认关；
  · 网页搜索 web_search：**默认关，且不在聊天模型兜底链**——只在 facade/consult
    层"知识不足"时经 augment_context_with_web_search 显式补全，绝不擅自联网。

只用 monkeypatch/假对象，不连真实浏览器、不依赖 Trae/WorkBuddy 登录态、不走真实网络。
逐文件隔离运行：
    .venv\\Scripts\\python.exe -m pytest tests\\test_zero_cost_wiring.py -q
"""
import os
import sys
import tempfile
import types
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.free_model_router import FreeModelRouter  # noqa: E402


# ---- 假对象：四能力的可注入替身 ---- #
class FakeLocalScanner:
    def __init__(self, result=None, exc=None):
        self.result = result or {"usable_models": []}
        self.exc = exc
        self.calls = 0

    def scan(self):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


class FakeDesktop:
    def __init__(self, descs=None, exc=None):
        self.descs = descs or []
        self.exc = exc
        self.calls = 0

    def discover_all(self):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.descs


class FakeBrowserHub:
    def __init__(self, resp=None, exc=None):
        self.resp = resp
        self.exc = exc
        self.calls = 0

    def chat(self, messages=None, query=None, model=None, timeout=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.resp


class FakeWebSearch:
    POLICY_INSUFFICIENT = "资料不足：本地知识库与联网补全都未返回可用内容，以下不作推断、不编造。"

    def __init__(self, out=None, exc=None):
        self.out = out
        self.exc = exc
        self.calls = 0

    def answer_with_sources(self, query, polisher=None, outcome=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.out


def _base_cfg():
    """关闭真实目录/桥探测/ollama/本地扫描，保证离线确定。

    local_scan 生产默认开，但单测默认显式关，避免捡到真机 Ollama；
    需要 local_scan 的用例再单独置 true。
    """
    return {
        "llm.free.auto_catalog": "false",
        "llm.free.auto_discover_bridge": "false",
        "llm.free.include_ollama": "false",
        "llm.free.nvidia_catalog_md": "false",
        "llm.free.local_scan.enabled": "false",
    }


def _desktop_desc(base_url, models, sid="trae"):
    return types.SimpleNamespace(openai_base_url=base_url, models=models,
                                 source_id=sid)


class TestSwitchDefaults(unittest.TestCase):
    """开关默认值：浏览器/桌面/网页搜索默认关；本地扫描默认开（单独用例验证）。"""

    def test_browser_ai_default_off_not_called(self):
        hub = FakeBrowserHub(resp={"choices": [{"message": {"content": "NEVER"}}]})
        web = FakeWebSearch(out={"answer": "NEVER", "used_web": False})
        cfg = _base_cfg()  # browser_ai 未设 -> 默认关
        r = FreeModelRouter(cfg, browser_hub=hub, web_search=web)
        with self.assertRaises(RuntimeError):
            r.chat("sys", "你好")
        self.assertEqual(hub.calls, 0, "浏览器自动化默认关，绝不应被自动路由调用")
        self.assertEqual(web.calls, 0, "网页搜索不在聊天兜底链，不应被调用")

    def test_web_search_default_off_not_in_chat_chain(self):
        web = FakeWebSearch(out={"answer": "一些检索资料", "used_web": True})
        r = FreeModelRouter(_base_cfg(), web_search=web)
        with self.assertRaises(RuntimeError):
            r.chat("sys", "hi")
        self.assertEqual(web.calls, 0, "网页搜索默认关，聊天兜底不得调用它")

    def test_web_search_augment_disabled_by_default(self):
        web = FakeWebSearch(out={"answer": "x"})
        r = FreeModelRouter(_base_cfg(), web_search=web)
        out = r.augment_context_with_web_search("今天天气")
        self.assertFalse(out["enabled"], "默认关时 augment 直接 disabled、不联网")
        self.assertEqual(web.calls, 0)

    def test_web_search_augment_returns_context_when_enabled(self):
        web = FakeWebSearch(out={"answer": "补全上下文正文", "used_web": True,
                                 "sources": ["http://s"]})
        cfg = _base_cfg()
        cfg["llm.free.web_search.enabled"] = "true"
        r = FreeModelRouter(cfg, web_search=web)
        out = r.augment_context_with_web_search("某时效性问题")
        self.assertTrue(out["enabled"])
        self.assertTrue(out["used_web"])
        self.assertEqual(out["context"], "补全上下文正文")
        self.assertEqual(web.calls, 1)
        # 即使显式开启，聊天链仍不调用 web_search
        with self.assertRaises(RuntimeError):
            r.chat("sys", "hi")
        self.assertEqual(web.calls, 1)

    def test_master_switch_disables_fallback(self):
        hub = FakeBrowserHub(resp={"choices": [{"message": {"content": "x"}}]})
        cfg = _base_cfg()
        cfg["llm.free.enabled"] = "false"
        cfg["browser_ai.enabled"] = "true"
        r = FreeModelRouter(cfg, browser_hub=hub)
        with self.assertRaises(RuntimeError):
            r.chat("sys", "hi")
        self.assertEqual(hub.calls, 0, "免费路由总闸关闭时兜底也不应跑")


class TestBrowserDesktopWiring(unittest.TestCase):
    """浏览器 AI 显式启用才调用；失败即跳过；网页搜索不在该链。"""

    def test_browser_explicit_on_called(self):
        hub = FakeBrowserHub(resp={
            "choices": [{"message": {"content": "密塔真回答"}}],
            "_meta": {"source": "密塔AI搜索"}})
        web = FakeWebSearch(out={"answer": "不该用到我"})
        cfg = _base_cfg()
        cfg["browser_ai.enabled"] = "true"
        r = FreeModelRouter(cfg, browser_hub=hub, web_search=web)
        out = r.chat("sys", "帮我写个开场")
        self.assertEqual(out["provider"], "browser_ai")
        self.assertTrue(out["model"].startswith("browser-ai:密塔"))
        self.assertEqual(web.calls, 0)

    def test_browser_failure_raises_no_web_chain(self):
        hub = FakeBrowserHub(exc=RuntimeError("反爬/超时"))
        web = FakeWebSearch(out={"answer": "网页兜底正文", "used_web": False})
        cfg = _base_cfg()
        cfg["browser_ai.enabled"] = "true"
        r = FreeModelRouter(cfg, browser_hub=hub, web_search=web)
        with self.assertRaises(RuntimeError) as ctx:
            r.chat("sys", "hi")
        msg = str(ctx.exception)
        self.assertIn("browser_ai:<hub>", msg)  # 浏览器确实被尝试
        self.assertNotIn("web_search", msg)     # 不再自动转网页搜索
        self.assertEqual(web.calls, 0, "浏览器失败后不得自动转网页搜索")

    def test_no_candidates_then_raise(self):
        r = FreeModelRouter(_base_cfg())
        with self.assertRaises(RuntimeError):
            r.chat("sys", "hi")


class TestLocalScanWiring(unittest.TestCase):
    def test_local_scan_default_on_out_of_box(self):
        """key 完全缺失时默认开：扫描被执行、候选被注册（开箱即生效）。"""
        scan = FakeLocalScanner(result={"usable_models": [
            {"base_url": "http://127.0.0.1:11434", "model": "qwen:7b",
             "key_present": False, "origin": "port_handshake:ollama"}]})
        cfg = _base_cfg()
        del cfg["llm.free.local_scan.enabled"]  # 模拟全新用户、无该键
        r = FreeModelRouter(cfg, local_scanner=scan)
        cands = [c for c in r.candidates() if c.provider == "local_scan"]
        self.assertEqual(len(cands), 1)
        self.assertGreaterEqual(scan.calls, 1)

    def test_local_scan_registered_when_enabled(self):
        scan = FakeLocalScanner(result={"usable_models": [
            {"base_url": "http://127.0.0.1:11434", "model": "qwen2.5:7b",
             "key_present": False, "origin": "port_handshake:ollama"}]})
        cfg = _base_cfg()
        cfg["llm.free.local_scan.enabled"] = "true"
        r = FreeModelRouter(cfg, local_scanner=scan)
        cands = [c for c in r.candidates() if c.provider == "local_scan"]
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].base_url, "http://127.0.0.1:11434/v1")
        self.assertEqual(r.ranked()[0].provider, "local_scan")

    def test_local_scan_disabled_not_called(self):
        scan = FakeLocalScanner(result={"usable_models": [
            {"base_url": "http://x", "model": "m"}]})
        r = FreeModelRouter(_base_cfg(), local_scanner=scan)  # base cfg 显式关
        self.assertEqual([c for c in r.candidates() if c.provider == "local_scan"], [])
        self.assertEqual(scan.calls, 0)

    def test_local_scan_exception_skipped(self):
        scan = FakeLocalScanner(exc=OSError("netstat 失败"))
        cfg = _base_cfg()
        cfg["llm.free.local_scan.enabled"] = "true"
        r = FreeModelRouter(cfg, local_scanner=scan)
        self.assertEqual([c for c in r.candidates() if c.provider == "local_scan"], [])
        st = r.catalog_status().get("local_scan", {})
        self.assertFalse(st.get("ok"))


class TestDesktopWiring(unittest.TestCase):
    def test_desktop_registered_with_shim_base_url(self):
        desk = FakeDesktop(descs=[_desktop_desc("http://127.0.0.1:9/v1",
                                               ["trae/glm-5.3"], "trae")])
        cfg = _base_cfg()
        cfg["llm.free.desktop_ai.enabled"] = "true"
        r = FreeModelRouter(cfg, desktop_connector=desk)
        cands = [c for c in r.candidates() if c.provider == "desktop"]
        self.assertEqual({c.model for c in cands}, {"trae/glm-5.3"})

    def test_desktop_503_when_no_app(self):
        desk = FakeDesktop(descs=[])  # 本机未装 Trae/WorkBuddy
        cfg = _base_cfg()
        cfg["llm.free.desktop_ai.enabled"] = "true"
        r = FreeModelRouter(cfg, desktop_connector=desk)
        self.assertEqual([c for c in r.candidates() if c.provider == "desktop"], [])
        st = r.catalog_status()["desktop_ai"]
        self.assertFalse(st["ok"])
        self.assertIn("503", st["error"])

    def test_desktop_no_base_url_not_registered(self):
        desk = FakeDesktop(descs=[_desktop_desc("", ["x"], "trae")])
        cfg = _base_cfg()
        cfg["llm.free.desktop_ai.enabled"] = "true"
        r = FreeModelRouter(cfg, desktop_connector=desk)
        self.assertEqual([c for c in r.candidates() if c.provider == "desktop"], [])


class TestSettingsPersistence(unittest.TestCase):
    """设置页写入的开关，经 ConfigService 落盘后能被 FreeModelRouter 读回。"""

    def test_roundtrip_via_configservice(self):
        from services.config_service import ConfigService
        tmp = tempfile.mktemp(suffix=".json")
        try:
            cs = ConfigService(tmp)
            cs.set("llm.free.local_scan.enabled", True)
            cs.set("llm.free.desktop_ai.enabled", True)
            cs.set("llm.free.web_search.enabled", True)
            cs.set("web.autofill.enabled", False)
            cs.set("browser_ai.enabled", False)

            cs2 = ConfigService(tmp)
            self.assertTrue(cs2.get("llm.free.local_scan.enabled"))
            self.assertFalse(cs2.get("browser_ai.enabled"))
            self.assertFalse(cs2.get("web.autofill.enabled"))

            r = FreeModelRouter(cs2)
            self.assertTrue(r._flag("llm.free.local_scan.enabled", False))
            self.assertFalse(r._flag("browser_ai.enabled", False))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


if __name__ == "__main__":
    unittest.main()
