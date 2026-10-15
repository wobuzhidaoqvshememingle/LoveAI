# -*- coding: utf-8 -*-
"""R3.3-57 LocalPersonaPool 门面测试：mock 各服务，验证返回结构 + 缺服务不抛。"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest
# 【废弃 · 2026-10-13】依赖的 services.data_pool.local_persona / .contracts 子模块
# 已不存在（DAL 重构为单文件 services/data_pool.py，原 services/data_pool/ 包被移除）。
# 为避免凭空重建已下线子系统，本文件整体 skip；备份见 *.bak_skip_20261013_145803。
pytest.skip(
    "services.data_pool.local_persona/.contracts 子模块已随 data_pool 包移除而不存在，"
    "本 LocalPersonaPool 测试整体失效（DAL 现为单文件 services/data_pool.py）。",
    allow_module_level=True,
)

from services.data_pool.local_persona import LocalPersonaPool
from services.data_pool.contracts import Evidence


class _FakeStyleLearner:
    def build_style_context(self):
        return "短句，爱用 emoji，句尾加~"


class _FakePersonaAnalyzer:
    def analyze(self, person_id, person_info=None):
        return {"person_id": person_id, "attachment": "anxious", "score": 7}


class _FakeMemory:
    def search_memories(self, person_id, query, top_k=10):
        return [
            {"content": "她不吃辣", "source": "memory", "confidence": 0.9},
            {"content": "上次约会她迟到了 20 分钟", "confidence": 0.7},
        ]


class _FakeStrategyAdvisor:
    def analyze(self, persona, relation_stage=5):
        return {
            "recommended": ["主动报备", "多夸奖", "慢推身体接触"],
            "forbidden": "冷暴力",
            "reasoning": "焦虑型需要确定性",
            "confidence": 0.8,
        }


class _FakeTwin:
    def __init__(self, person_id):
        self.person_id = person_id

    def predict_reaction(self, user_action, recent_chats, affection, **kw):
        return {
            "emotion": "开心",
            "inner_thought": "他终于主动了",
            "possible_replies": ["好呀", "你几点来接我"],
            "confidence": 0.7,
        }


class TestLocalPersonaPool(unittest.TestCase):

    def setUp(self):
        self.pool = LocalPersonaPool(
            style_learner=_FakeStyleLearner(),
            persona_analyzer=_FakePersonaAnalyzer(),
            memory_service=_FakeMemory(),
            strategy_advisor=_FakeStrategyAdvisor(),
            digital_twin_factory=_FakeTwin,
        )

    def test_get_style(self):
        s = self.pool.get_style("p1")
        self.assertIn("style_context", s)
        self.assertIn("emoji", s["style_context"])

    def test_get_persona(self):
        p = self.pool.get_persona("p1")
        self.assertEqual(p["attachment"], "anxious")

    def test_get_memory_returns_evidence_list(self):
        ev = self.pool.get_memory("p1", "吃什么")
        self.assertIsInstance(ev, list)
        self.assertTrue(all(isinstance(e, Evidence) for e in ev))
        self.assertEqual(ev[0].text, "她不吃辣")
        self.assertAlmostEqual(ev[0].confidence, 0.9)

    def test_get_strategy_output(self):
        so = self.pool.get_strategy("p1", {"relation_stage": 5})
        self.assertEqual(so.strategy, "主动报备")
        self.assertEqual(len(so.candidates), 3)
        self.assertEqual(so.risk, "冷暴力")

    def test_get_digital_twin(self):
        out = self.pool.get_digital_twin("p1", "今晚约饭", [], {})
        self.assertIn("possible_replies", out)
        self.assertEqual(len(out["possible_replies"]), 2)

    def test_empty_pool_does_not_raise(self):
        # 全空服务 -> 都返回空默认值，不抛
        empty = LocalPersonaPool()
        self.assertEqual(empty.get_style("x"), {})
        self.assertEqual(empty.get_persona("x"), {"person_id": "x"})
        self.assertEqual(empty.get_memory("x", "q"), [])
        so = empty.get_strategy("x")
        self.assertEqual(so.strategy, "")
        self.assertEqual(empty.get_digital_twin("x", "hi"), {})

    def test_failing_service_swallowed(self):
        class _Boom:
            def build_style_context(self):
                raise RuntimeError("db down")
        p = LocalPersonaPool(style_learner=_Boom())
        self.assertEqual(p.get_style("x"), {})


if __name__ == "__main__":
    unittest.main()
