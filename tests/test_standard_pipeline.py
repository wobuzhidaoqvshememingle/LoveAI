# -*- coding: utf-8 -*-
"""R3.3-57 standard_pipeline 全链路测试（mock 所有层）。"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest
# 【废弃 · 2026-10-13】依赖的 services.data_pool.local_persona/.model_adapter/.pipeline/.adapters
# 子模块已不存在（DAL 重构为单文件 services/data_pool.py，原 services/data_pool/ 包被移除）。
# 为避免凭空重建已下线子系统，本文件整体 skip；备份见 *.bak_skip_20261013_145803。
pytest.skip(
    "services.data_pool.local_persona/.model_adapter/.pipeline/.adapters 子模块已随包移除而不存在，"
    "本 standard_pipeline 测试整体失效（DAL 现为单文件 services/data_pool.py）。",
    allow_module_level=True,
)

from services.data_pool.local_persona import LocalPersonaPool
from services.data_pool.model_adapter import ModelAdapterPool
from services.data_pool.pipeline import standard_pipeline
from services.data_pool.adapters import OpenAICompatAdapter, OllamaAdapter


class TestStandardPipeline(unittest.TestCase):

    def _make_pools(self):
        lp = LocalPersonaPool(
            style_learner=type("SL", (), {
                "build_style_context": lambda self: "短句~"}),
            persona_analyzer=type("PA", (), {
                "analyze": lambda self, pid, info=None: {
                    "person_id": pid, "attachment": "anxious"}}),
            memory_service=type("MS", (), {
                "search_memories": lambda self, pid, q, k=10: [
                    {"content": "她不吃辣", "confidence": 0.9}]}),
            strategy_advisor=type("SA", (), {
                "analyze": lambda self, persona, stage=5: {
                    "recommended": ["主动报备", "多夸奖", "慢推进"],
                    "forbidden": "冷暴力",
                    "reasoning": "焦虑型要确定性",
                    "confidence": 0.8}}),
            digital_twin_factory=lambda pid: type("T", (), {
                "predict_reaction": lambda self, **kw: {
                    "possible_replies": ["好呀", "你几点来"],
                    "emotion": "开心"}})(),
        )
        mp = ModelAdapterPool(adapters={
            "openai_compat.free": OpenAICompatAdapter(
                chat_fn=lambda msgs: "周末一起吃饭吧~"),
        })
        return lp, mp

    def test_pipeline_returns_all_keys(self):
        lp, mp = self._make_pools()
        out = standard_pipeline(
            {"person_id": "p1", "user_text": "今晚约她",
             "recent_chats": [{"role": "user", "content": "在吗"}],
             "affection": {"score": 60}, "relation_stage": 5},
            local_persona=lp, model_pool=mp, tier="draft",
        )
        for key in ("person_id", "persona", "style", "memory",
                    "strategy", "strategy_detail", "candidates",
                    "prediction", "prediction_detail",
                    "model_used", "adapter", "latency_ms",
                    "draft_text", "ok"):
            self.assertIn(key, out, f"missing key: {key}")

    def test_pipeline_uses_real_adapter_output(self):
        lp, mp = self._make_pools()
        out = standard_pipeline(
            {"person_id": "p1", "user_text": "hi"},
            local_persona=lp, model_pool=mp, tier="draft",
        )
        self.assertEqual(out["draft_text"], "周末一起吃饭吧~")
        self.assertTrue(out["ok"])
        # 策略候选应该来自 strategy_advisor
        self.assertEqual(out["candidates"][0], "主动报备")

    def test_pipeline_empty_services_no_crash(self):
        # 全空服务 + 全空模型池 -> 走 rule_fallback，不抛
        out = standard_pipeline({"person_id": "p1", "user_text": "hi"})
        self.assertEqual(out["person_id"], "p1")
        self.assertIn(out["adapter"], ("rule_fallback", ""))
        # 仍然有所有 key
        self.assertIn("candidates", out)
        self.assertIn("prediction", out)

    def test_pipeline_no_user_text_still_works(self):
        lp, mp = self._make_pools()
        out = standard_pipeline(
            {"person_id": "p2"},
            local_persona=lp, model_pool=mp, tier="judgment",
        )
        self.assertEqual(out["person_id"], "p2")


if __name__ == "__main__":
    unittest.main()
