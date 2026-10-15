# -*- coding: utf-8 -*-
"""R3.3-57 contracts dataclass 序列化 / 反序列化测试。

====================================================================
【废弃说明 · 2026-10-13】本文件已整体 skip，不再运行，原因如下：
  - 本文件原本 import ``services.data_pool.contracts``（Message/ModelResponse/
    Evidence/StrategyOutput/TaskOutput 等 dataclass），期望存在一个
    ``services/data_pool/`` 包结构；
  - 现状：DAL 已重构为单文件 ``services/data_pool.py``（write/read/SCHEMA/
    审计/双写告警），``services.data_pool`` 不再是包，``.contracts`` 子模块
    不存在；这些 dataclass 的语义也未并入单文件 DAL；
  - 因此原 import 必然 ``ModuleNotFoundError: 'services.data_pool' is not a
    package``，该测试永远跑不过。
  - 处置选择（三选一中的②）：DAL 定位即单文件，无法/不应为旧测试重建整个
    contracts 子系统（否则是凭空捏造）。改为 module-level skip，保留用例历史
    与备份，不遗留「永远 collect 失败」的文件。
  - 真实 DAL 行为由 ``tests/test_data_pool.py``（17 例）覆盖；架构一致性由
    ``tests/test_architecture_consistency.py``（7 例）覆盖。
  - 备份：tests/test_data_pool_contracts.py.bak_skip_20261013_145422
====================================================================
"""
import os
import sys
import unittest

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 模块级 skip：依赖的 services.data_pool.contracts 包已不存在，见文件头废弃说明。
pytest.skip(
    "services.data_pool.contracts 包已不存在（DAL 改为单文件 services/data_pool.py），"
    "本 contracts dataclass 测试永久失效；真实 DAL 测试见 tests/test_data_pool.py。",
    allow_module_level=True,
)

# --- 以下为历史用例源码（已被上方 skip 整体跳过，不执行），保留作留痕 ---------------- #
from services.data_pool.contracts import (  # noqa: E402
    Message, ModelResponse, Evidence, StrategyOutput, TaskOutput,
    make_message, make_ok_response, make_fallback_response,
)


class TestContracts(unittest.TestCase):

    def test_message_defaults_safe(self):
        m = Message()
        self.assertEqual(m.role, "user")
        self.assertEqual(m.content, "")
        self.assertIsInstance(m.metadata, dict)
        self.assertGreater(m.timestamp, 0)

    def test_message_roundtrip(self):
        m = Message(role="system", content="你好", metadata={"k": 1})
        d = m.to_dict()
        m2 = Message.from_dict(d)
        self.assertEqual(m2.role, "system")
        self.assertEqual(m2.content, "你好")
        self.assertEqual(m2.metadata, {"k": 1})

    def test_message_from_dict_bad_input(self):
        # 非 dict / 缺字段 -> 不抛
        m = Message.from_dict(None)  # type: ignore[arg-type]
        self.assertEqual(m.role, "user")
        m2 = Message.from_dict({})
        self.assertEqual(m2.content, "")

    def test_model_response_ok(self):
        r = make_ok_response("hi", model="deepseek", adapter="openai_compat.free")
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "hi")
        d = r.to_dict()
        self.assertIn("text", d)
        self.assertIn("adapter", d)

    def test_model_response_fallback(self):
        r = make_fallback_response("boom")
        self.assertFalse(r.ok)
        self.assertEqual(r.model_name, "rule_fallback")

    def test_evidence_roundtrip(self):
        e = Evidence(text="记得她不吃辣", source="memory", confidence=0.8)
        e2 = Evidence.from_dict(e.to_dict())
        self.assertEqual(e2.text, "记得她不吃辣")
        self.assertEqual(e2.source, "memory")
        self.assertAlmostEqual(e2.confidence, 0.8)

    def test_strategy_output_defaults(self):
        s = StrategyOutput()
        self.assertEqual(s.candidates, [])
        self.assertEqual(s.confidence, 0.5)
        d = s.to_dict()
        self.assertEqual(d["candidates"], [])

    def test_strategy_output_from_dict_normalizes_str_candidates(self):
        s = StrategyOutput.from_dict({"candidates": "only one"})
        self.assertEqual(s.candidates, ["only one"])

    def test_task_output(self):
        t = TaskOutput(task_title="约晚饭", due="今晚", steps=["订位", "发消息"])
        d = t.to_dict()
        t2 = TaskOutput.from_dict(d)
        self.assertEqual(t2.task_title, "约晚饭")
        self.assertEqual(len(t2.steps), 2)

    def test_make_message_helper(self):
        m = make_message("user", "在吗", tag="test")
        self.assertEqual(m.metadata, {"tag": "test"})


if __name__ == "__main__":
    unittest.main()
