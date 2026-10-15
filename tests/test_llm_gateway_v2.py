# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - LLMGateway L1/L2/L3 三级路由 单元测试

覆盖：
  - 复杂度自动判断（简单->L1 / 日常->L2 / 复杂->L3）
  - 降级链：L3 失败 -> L2 -> L1 -> 规则兜底（mock 各层）
  - LoRA 列表 / 选择 / 当前适配器（临时目录）
  - 训练数据：标记 / 统计 / 导出 JSONL（临时数据库）
  - get_status 字段齐全
  - L1 文件缺失时优雅降级
"""
import os
import sys
import json
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.llm_gateway import LLMGateway  # noqa: E402


class FakeConfig:
    """最小 ConfigService 替身：点分 get/set，内存字典。"""

    def __init__(self) -> None:
        self.store: dict = {}

    def get(self, key: str, default=None):
        value = self.store
        for part in key.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return default
        return value

    def set(self, key: str, value) -> None:
        parts = key.split(".")
        node = self.store
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value


class TestLLMGatewayV2(unittest.TestCase):
    """L1/L2/L3 三级路由网关测试。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_llm_test_")
        self.db = Database(os.path.join(self.tmpdir, "test.db"))
        self.config = FakeConfig()
        self.gw = LLMGateway(self.config, signals=None, db=self.db)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---------------- 复杂度判断 ---------------- #
    def test_judge_greeting_is_l1(self) -> None:
        self.assertEqual(self.gw._judge_complexity("你好呀"), "l1")

    def test_judge_short_is_l1(self) -> None:
        self.assertEqual(self.gw._judge_complexity("在吗"), "l1")

    def test_judge_daily_is_l2(self) -> None:
        text = "今天上班好累，地铁又挤又闷，回家只想躺着什么都不想动"
        self.assertEqual(self.gw._judge_complexity(text), "l2")

    def test_judge_complex_keyword_is_l3(self) -> None:
        text = "帮我分析一下她这句话到底是什么意思，我总觉得有潜台词"
        self.assertEqual(self.gw._judge_complexity(text), "l3")

    def test_judge_long_text_is_l3(self) -> None:
        text = "她" * 150
        self.assertEqual(self.gw._judge_complexity(text), "l3")

    # ---------------- 降级链 ---------------- #
    def test_chat_l3_fail_degrades_to_l2(self) -> None:
        self.gw._call_l3 = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key"))
        self.gw._call_free = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no free"))
        self.gw._call_l2 = lambda *a, **k: "L2回复"
        out = self.gw.chat("sys", "今天上班好累啊地铁又挤又闷真的烦", complexity="l3")
        self.assertEqual(out, "L2回复")
        self.assertEqual(self.gw.last_layer, "l2")

    def test_chat_l3_l2_fail_degrades_to_l1(self) -> None:
        self.gw._call_l3 = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no key"))
        self.gw._call_free = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no free"))
        self.gw._call_l2 = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no model"))
        self.gw._call_l1 = lambda *a, **k: "L1回复"
        out = self.gw.chat("sys", "今天上班好累啊地铁又挤又闷真的烦", complexity="l3")
        self.assertEqual(out, "L1回复")
        self.assertEqual(self.gw.last_layer, "l1")

    def test_chat_all_fail_uses_stat_fallback(self) -> None:
        """所有生成层失败 -> P-10 统计反应分布兜底（标注无生成模型，不硬失败）。"""
        def _boom(*a, **k):
            raise RuntimeError("boom")
        self.gw._call_l3 = _boom
        self.gw._call_l2 = _boom
        self.gw._call_l1 = _boom
        out = self.gw.chat("sys", "你好呀在吗", complexity="auto")
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertIn("统计预测", out)
        self.assertEqual(self.gw.last_layer, "stat")

    def test_chat_all_fail_stat_empty_uses_rule(self) -> None:
        """统计兜底也失效时，最终落规则句。"""
        def _boom(*a, **k):
            raise RuntimeError("boom")
        self.gw._call_l3 = _boom
        self.gw._call_l2 = _boom
        self.gw._call_l1 = _boom
        self.gw._stat_fallback_text = lambda prompt: ""
        out = self.gw.chat("sys", "你好呀在吗", complexity="auto")
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertEqual(self.gw.last_layer, "rules")

    def test_legacy_simple_maps_to_l1(self) -> None:
        """旧调用方传 complexity='simple' 应映射到 L1 链。"""
        self.gw._call_l1 = lambda *a, **k: "L1回复"
        out = self.gw.chat("sys", "在吗", complexity="simple")
        self.assertEqual(out, "L1回复")
        self.assertEqual(self.gw.last_layer, "l1")

    # ---------------- LoRA 适配器管理 ---------------- #
    def test_lora_list_from_tempdir(self) -> None:
        lora_dir = os.path.join(self.tmpdir, "lora")
        for name in ("alpha", "beta", "empty"):
            os.makedirs(os.path.join(lora_dir, name), exist_ok=True)
        # 只有含 adapter_config.json 的才算适配器
        for name in ("alpha", "beta"):
            with open(os.path.join(lora_dir, name, "adapter_config.json"), "w") as f:
                f.write("{}")
        self.config.set("llm.local.l2.lora_dir", lora_dir)
        self.assertEqual(self.gw.list_lora_adapters(), ["alpha", "beta"])

    def test_lora_select_writes_config(self) -> None:
        lora_dir = os.path.join(self.tmpdir, "lora")
        os.makedirs(os.path.join(lora_dir, "my-adapter"), exist_ok=True)
        with open(os.path.join(lora_dir, "my-adapter", "adapter_config.json"), "w") as f:
            f.write("{}")
        self.config.set("llm.local.l2.lora_dir", lora_dir)
        self.gw.select_lora_adapter("my-adapter")
        self.assertEqual(self.gw.get_current_lora(), "my-adapter")
        self.assertEqual(self.config.get("llm.local.l2.lora_adapter"), "my-adapter")

    def test_lora_select_invalid_raises(self) -> None:
        lora_dir = os.path.join(self.tmpdir, "lora")
        os.makedirs(os.path.join(lora_dir, "real"), exist_ok=True)
        with open(os.path.join(lora_dir, "real", "adapter_config.json"), "w") as f:
            f.write("{}")
        self.config.set("llm.local.l2.lora_dir", lora_dir)
        with self.assertRaises(ValueError):
            self.gw.select_lora_adapter("not-exist")

    # ---------------- 训练数据收集 ---------------- #
    def test_mark_good_reply_and_stats(self) -> None:
        self.gw.last_layer = "l2"
        rid1 = self.gw.mark_good_reply("回复A", context="ctx1", person_id="p1")
        rid2 = self.gw.mark_good_reply("回复B", context="ctx2", person_id="p1")
        self.gw.last_layer = "l1"
        rid3 = self.gw.mark_good_reply("回复C", person_id="p2")
        self.assertGreater(rid1, 0)
        self.assertGreater(rid2, rid1)
        stats = self.gw.get_training_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["by_person"]["p1"], 2)
        self.assertEqual(stats["by_person"]["p2"], 1)
        self.assertEqual(stats["by_model"]["l2"], 2)
        self.assertEqual(stats["by_model"]["l1"], 1)

    def test_export_training_data_jsonl(self) -> None:
        self.gw.last_layer = "l3"
        self.gw.mark_good_reply("你好呀，今天过得怎么样？")
        self.gw.mark_good_reply("我觉得你可以主动一点")
        out_path = os.path.join(self.tmpdir, "train.jsonl")
        n = self.gw.export_training_data(out_path)
        self.assertEqual(n, 2)
        with open(out_path, "r", encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        self.assertEqual(len(lines), 2)
        row = json.loads(lines[0])
        self.assertIn("text", row)
        self.assertTrue(row["text"])

    # ---------------- 状态与降级 ---------------- #
    def test_get_status_fields_complete(self) -> None:
        st = self.gw.get_status()
        for key in ("l1_available", "l2_available", "l3_available",
                    "current_lora", "l1_loaded", "l2_loaded", "local_model_path"):
            self.assertIn(key, st)

    def test_l1_missing_file_degrades_gracefully(self) -> None:
        """L1 模型文件缺失时 _call_l1 抛错，chat() 自动落到 P-10 统计兜底，不崩溃。"""
        self.config.set("llm.local.l1.onnx_path", os.path.join(self.tmpdir, "nope.onnx"))
        self.config.set("llm.local.l1.tokenizer_path", os.path.join(self.tmpdir, "nope.json"))
        # 其他两层也 mock 成失败
        def _boom(*a, **k):
            raise RuntimeError("boom")
        self.gw._call_l3 = _boom
        self.gw._call_l2 = _boom
        out = self.gw.chat("sys", "你好", complexity="l1")
        self.assertIsInstance(out, str)
        self.assertTrue(out)
        self.assertEqual(self.gw.last_layer, "stat")


if __name__ == "__main__":
    unittest.main(verbosity=2)
