# -*- coding: utf-8 -*-
"""R3.3-31 启动全量探活测试：ProbeReport 结构 + 整体分级 + 降级路径。

全部离线：http_get / 文件 / import 全部可控，不发真实网络请求。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import services.startup_probe as sp


def fake_http_get_factory(ollama_body="", openrouter_ok=True):
    """返回一个可控 http_get：ollama /api/tags 给固定 body，其它按 openrouter_ok。"""
    def _get(url, timeout=2.0, headers=None):
        if url.endswith("/api/tags"):
            if ollama_body is None:
                raise ConnectionRefusedError("ollama down")
            return ollama_body
        if "openrouter" in url:
            if not openrouter_ok:
                raise TimeoutError("openrouter timeout")
            return '{"data": []}'
        raise RuntimeError(f"unexpected url: {url}")
    return _get


class TestProbeStructure(unittest.TestCase):
    """报告结构与单项降级路径。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "loveai.db")
        self.onnx_path = os.path.join(self.tmp, "model_q4.onnx")
        # 非确定性检查项钉死：voice/playwright/onnx 全部 ok
        self._patches = [
            mock.patch.object(sp, "_check_voice_imports",
                              lambda cfg: sp._item("voice_deps", "ok", "")),
            mock.patch.object(sp, "_check_playwright",
                              lambda cfg: sp._item("playwright", "ok", "")),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _base_config(self, **over):
        cfg = {
            "llm.cloud.api_key": "sk-test-l3",
            "db_path": self.db_path,
            "llm.local.l1.onnx_path": self.onnx_path,
        }
        cfg.update(over)
        return cfg

    def test_report_shape(self):
        """items/summary 结构完整；每项含 name/status/detail/fallback。"""
        open(self.onnx_path, "wb").write(b"x")
        body = json.dumps({"models": [{"name": "loveai-qwen05b:latest"}]})
        rep = sp.probe_all_services(
            self._base_config(),
            http_get=fake_http_get_factory(ollama_body=body))
        self.assertIn("items", rep)
        self.assertIn("summary", rep)
        names = {it["name"] for it in rep["items"]}
        self.assertEqual(names, {
            "ollama", "l3_api_key", "free_router_sources", "sqlite_db",
            "l1_onnx_model", "voice_deps", "playwright"})
        for it in rep["items"]:
            self.assertIn(it["status"], ("ok", "degraded", "fail"))
            for k in ("name", "status", "detail", "fallback"):
                self.assertIn(k, it)
        s = rep["summary"]
        self.assertIn(s["overall_status"], ("ok", "degraded", "fail"))

    def test_all_ok_overall_ok(self):
        open(self.onnx_path, "wb").write(b"x")
        body = json.dumps({"models": [{"name": "m1"}]})
        rep = sp.probe_all_services(
            self._base_config(),
            http_get=fake_http_get_factory(ollama_body=body))
        self.assertEqual(rep["summary"]["overall_status"], "ok")

    def test_core_fail_overall_fail(self):
        """L3 key 缺失（核心）→ overall=fail。"""
        open(self.onnx_path, "wb").write(b"x")
        body = json.dumps({"models": [{"name": "m1"}]})
        cfg = self._base_config()
        cfg.pop("llm.cloud.api_key")
        rep = sp.probe_all_services(
            cfg, http_get=fake_http_get_factory(ollama_body=body))
        self.assertEqual(rep["summary"]["overall_status"], "fail")
        self.assertIn("l3_api_key", rep["summary"]["core_fail_items"])

    def test_ollama_down_is_core_fail(self):
        open(self.onnx_path, "wb").write(b"x")
        rep = sp.probe_all_services(
            self._base_config(),
            http_get=fake_http_get_factory(ollama_body=None))  # ollama 拒连
        self.assertEqual(rep["summary"]["overall_status"], "fail")
        ollama = [it for it in rep["items"] if it["name"] == "ollama"][0]
        self.assertEqual(ollama["status"], "fail")

    def test_noncore_fail_overall_degraded(self):
        """核心全 ok，但免费源不可达（非阻塞）→ overall=degraded。"""
        open(self.onnx_path, "wb").write(b"x")
        body = json.dumps({"models": [{"name": "m1"}]})
        rep = sp.probe_all_services(
            self._base_config(),
            http_get=fake_http_get_factory(ollama_body=body,
                                           openrouter_ok=False))
        self.assertEqual(rep["summary"]["overall_status"], "degraded")
        fr = [it for it in rep["items"]
              if it["name"] == "free_router_sources"][0]
        self.assertEqual(fr["status"], "degraded")

    def test_each_check_isolated_on_exception(self):
        """某一项炸异常，其它项照常出结果（探活不被单点拖死）。"""
        open(self.onnx_path, "wb").write(b"x")
        body = json.dumps({"models": [{"name": "m1"}]})
        # sqlite 检查抛异常也不能让整次探活崩
        with mock.patch.object(sp, "_check_sqlite",
                              side_effect=RuntimeError("boom")):
            rep = sp.probe_all_services(
                self._base_config(),
                http_get=fake_http_get_factory(ollama_body=body))
        # 容错：probe_all_services 内部应把异常项兜成 fail/degraded
        self.assertIn("items", rep)


class TestSingleChecks(unittest.TestCase):
    """单项检查的降级路径。"""

    def test_l3_key_missing(self):
        it = sp._check_l3_key({})
        self.assertEqual(it["status"], "fail")

    def test_l3_key_present(self):
        it = sp._check_l3_key({"llm.cloud.api_key": "sk-x"})
        self.assertEqual(it["status"], "ok")

    def test_sqlite_writable(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "sub", "loveai.db")
        it = sp._check_sqlite({"db_path": path})
        self.assertEqual(it["status"], "ok")
        self.assertTrue(os.path.isfile(path))

    def test_sqlite_unwritable(self):
        # 指向一个目录当 db 文件 → sqlite 打不开 → fail
        it = sp._check_sqlite({"db_path": os.path.dirname(__file__)})
        self.assertEqual(it["status"], "fail")

    def test_onnx_missing_degraded(self):
        tmp = tempfile.mkdtemp()
        it = sp._check_l1_onnx({"llm.local.l1.onnx_path":
                                os.path.join(tmp, "nope.onnx")})
        self.assertEqual(it["status"], "degraded")

    def test_ollama_reachable_empty_models_degraded(self):
        it = sp._check_ollama({}, lambda *a, **k: '{"models": []}')
        self.assertEqual(it["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
