# -*- coding: utf-8 -*-
"""
R3.3-41 4 个模型缺口诊断单测。

Mock 掉所有网络：
  - ollama /api/tags 不可达（本机未起）
  - free router.refresh_catalog 不可达
  - L3 provider 列表为空
验证：
  1. diagnose_model_gaps() 返回 4 个 layer
  2. 每个 gap 都有 fallback_to 路径（含规则模板）
  3. 各层在无网络/无 key 环境下，chat() 仍能返回非空字符串（规则兜底）
  4. L1 拒中文后确实落到 L2（链里含 l2）
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.llm_gateway import LLMGateway  # noqa: E402


class FakeConfig:
    def __init__(self) -> None:
        self.store: dict = {}

    def get(self, key: str, default=None):
        v = self.store
        for p in key.split("."):
            if isinstance(v, dict) and p in v:
                v = v[p]
            else:
                return default
        return v

    def set(self, key: str, value) -> None:
        parts = key.split(".")
        node = self.store
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value


class TestModelGaps(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_gaps_")
        self.db = Database(os.path.join(self.tmpdir, "t.db"))
        self.cfg = FakeConfig()
        self.gw = LLMGateway(self.cfg, signals=None, db=self.db)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- diagnose_model_gaps ---- #
    def test_diagnose_returns_four_layers(self) -> None:
        with mock.patch.object(self.gw, "_ollama_alive", return_value=False), \
             mock.patch("urllib.request.urlopen",
                         side_effect=OSError("no network")), \
             mock.patch.object(self.gw, "get_free_router",
                               return_value=mock.MagicMock(
                                   refresh_catalog=mock.MagicMock(return_value={}))):
            gaps = self.gw.diagnose_model_gaps()
        layers = [g["layer"] for g in gaps]
        self.assertIn("l1_zh", layers)
        self.assertIn("l2_ollama", layers)
        self.assertIn("l3_cloud", layers)
        self.assertIn("free_catalog", layers)

    def test_each_gap_has_fallback_chain(self) -> None:
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("no net")), \
             mock.patch.object(self.gw, "get_free_router",
                               return_value=mock.MagicMock(
                                   refresh_catalog=mock.MagicMock(return_value={}))):
            gaps = self.gw.diagnose_model_gaps()
        for g in gaps:
            self.assertIn("fallback_to", g)
            self.assertIsInstance(g["fallback_to"], list)
            self.assertGreaterEqual(len(g["fallback_to"]), 1)
            # 每个 gap 都必须能落到规则模板（最后兜底）
            self.assertIn("rules_template", g["fallback_to"])

    def test_l1_zh_gap_is_degraded_by_design(self) -> None:
        # 默认 allow_chinese_generation=False -> L1 中文必须被降级
        gaps = self.gw.diagnose_model_gaps()
        l1 = next(g for g in gaps if g["layer"] == "l1_zh")
        self.assertFalse(l1["available"])
        self.assertTrue(l1["degraded"])
        # fallback 链必须包含 l2_ollama
        self.assertIn("l2_ollama", l1["fallback_to"])

    def test_l3_no_key_is_explicit_not_silent(self) -> None:
        # 清空所有 key
        for k in ("llm.cloud.api_key", "llm.api_key",
                  "llm.l3.deepseek.api_key", "llm.deepseek.api_key",
                  "llm.l3.doubao.api_key", "llm.doubao.api_key"):
            self.cfg.set(k, "")
        gaps = self.gw.diagnose_model_gaps()
        l3 = next(g for g in gaps if g["layer"] == "l3_cloud")
        self.assertFalse(l3["available"])
        self.assertIn("api_key", l3["reason"])

    def test_l2_ollama_discovered_via_api_tags(self) -> None:
        # mock /api/tags 返回本机两个模型
        fake_tags = b'{"models":[{"name":"loveai-qwen05b:latest"},{"name":"loveai-qwen15b:latest"}]}'

        class FakeResp:
            def __init__(self, body): self._b = body
            def read(self): return self._b
            def __enter__(self): return self
            def __exit__(self, *a): return None

        with mock.patch("urllib.request.urlopen",
                        return_value=FakeResp(fake_tags)):
            gaps = self.gw.diagnose_model_gaps()
        l2 = next(g for g in gaps if g["layer"] == "l2_ollama")
        self.assertTrue(l2["available"])
        self.assertIn("loveai-qwen05b:latest", l2["models"])
        self.assertIn("loveai-qwen15b:latest", l2["models"])

    # ---- chat() 在全断网环境下仍返回非空 ---- #
    def test_chat_returns_nonempty_even_when_all_layers_down(self) -> None:
        # 把所有真实网络都打挂
        with mock.patch("urllib.request.urlopen",
                        side_effect=OSError("no net")), \
             mock.patch.object(self.gw, "_call_l3",
                               side_effect=RuntimeError("no key")), \
             mock.patch.object(self.gw, "_call_free",
                               side_effect=RuntimeError("no free")), \
             mock.patch.object(self.gw, "_call_l2",
                               side_effect=RuntimeError("no ollama")), \
             mock.patch.object(self.gw, "_call_l1",
                               side_effect=RuntimeError("l1 refuses zh")):
            out = self.gw.chat("sys", "你好", allow_rule_fallback=True)
        self.assertIsInstance(out, str)
        self.assertTrue(out.strip(), "全层降级后规则兜底必须非空")

    def test_l1_chain_includes_l2_for_chinese(self) -> None:
        # 上轮收口报告说 l1 链由 ["l1"] 改为 ["l1","l2"]——验证还在
        # 通过 _judge_complexity("在吗") = l1，链应为 ["l1","l2"]
        self.assertEqual(self.gw._judge_complexity("在吗"), "l1")
        # 直接读源码常量等价物：构造一个 dummy chat 调用观察 chain
        # 这里通过观察 chat() 在 l1 raise 后继续试 l2 的行为来验证
        calls = []

        def fake_l1(s, u, t, m):
            calls.append("l1")
            raise RuntimeError("l1 refuses zh")

        def fake_l2(s, u, t, m):
            calls.append("l2")
            return "l2-fake-reply"

        with mock.patch.object(self.gw, "_call_l1", side_effect=fake_l1), \
             mock.patch.object(self.gw, "_call_l2", side_effect=fake_l2):
            out = self.gw.chat("sys", "在吗")
        self.assertEqual(out, "l2-fake-reply")
        self.assertIn("l1", calls)
        self.assertIn("l2", calls)


if __name__ == "__main__":
    unittest.main()
