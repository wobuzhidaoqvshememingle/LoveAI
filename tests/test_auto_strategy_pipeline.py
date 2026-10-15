# -*- coding: utf-8 -*-
"""R3.3-47 自动策略链路 单测。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services import auto_strategy_pipeline as asp  # noqa: E402
from services.direction_compass import DirectionCompass  # noqa: E402


class TestAutoStrategyPipeline(unittest.TestCase):

    def test_run_returns_required_keys_nonempty(self):
        """mock 掉各服务，验证 run() 返回含 strategy/tasks/candidates/prediction。"""
        fake_persona = {"attachment": {"primary_type": "anxious"}}

        fake_advisor = MagicMock()
        fake_advisor.analyze.return_value = {
            "recommended": ["稳定回应", "给安全感"],
            "forbidden": ["冷战"],
            "reasoning": "焦虑型需要确定感",
        }

        fake_te = MagicMock()
        fake_te.generate_main_tasks.return_value = [
            {"title": "今天主动报备", "deadline": "今天", "done_when": "对方回复"},
            {"title": "周日语音一次", "deadline": "周日", "done_when": "通话 20 分钟"},
        ]

        fake_tute = MagicMock()
        fake_tute.generate_guide.return_value = {"id": "g1", "steps": ["step1", "step2"]}

        fake_dt = MagicMock()
        fake_dt.predict_reaction.return_value = {
            "emotion": "安心",
            "inner_thought": "他今天很稳",
            "possible_replies": ["好呀", "你今天挺主动"],
            "action": "继续聊",
            "confidence": 0.7,
        }

        with patch.object(asp, "_get_persona_analyzer", return_value=None), \
             patch.object(asp, "_get_strategy_advisor", return_value=fake_advisor), \
             patch.object(asp, "_get_task_engine", return_value=fake_te), \
             patch.object(asp, "_get_tutorial_engine", return_value=fake_tute), \
             patch.object(asp, "_get_digital_twin", return_value=fake_dt):
            out = asp.run({
                "person_id": "u1",
                "persona": fake_persona,
                "recent_chats": [{"role": "her", "text": "你今天怎么没找我"}],
                "relation_stage": 5,
                "affection": {"score": 60},
            })

        # 必须有的 key
        for k in ("persona", "strategy", "tasks", "tutorials",
                  "candidates", "prediction", "confidence"):
            self.assertIn(k, out, f"缺 key {k}")

        # 非空
        self.assertTrue(out["strategy"])
        self.assertIsInstance(out["tasks"], list)
        self.assertGreaterEqual(len(out["tasks"]), 1)
        self.assertIsInstance(out["candidates"], list)
        self.assertGreaterEqual(len(out["candidates"]), 3)
        self.assertTrue(out["prediction"])
        self.assertGreater(out["confidence"], 0)
        # mock 全成功 → 不该有兜底
        self.assertEqual(out["fallbacks_used"], [])

    def test_run_fallback_when_all_services_down(self):
        """所有服务都不可用时，run() 仍返回完整 dict，走兜底模板。"""
        with patch.object(asp, "_get_persona_analyzer", return_value=None), \
             patch.object(asp, "_get_strategy_advisor", return_value=None), \
             patch.object(asp, "_get_task_engine", return_value=None), \
             patch.object(asp, "_get_tutorial_engine", return_value=None), \
             patch.object(asp, "_get_digital_twin", return_value=None):
            out = asp.run({"person_id": "x", "recent_chats": []})

        self.assertTrue(out["strategy"]["recommended"])
        self.assertGreaterEqual(len(out["tasks"]), 1)
        self.assertGreaterEqual(len(out["candidates"]), 3)
        self.assertTrue(out["prediction"]["possible_replies"])
        # 走了多个兜底
        self.assertIn("strategy", out["fallbacks_used"])
        self.assertIn("tasks", out["fallbacks_used"])
        self.assertIn("prediction", out["fallbacks_used"])

    def test_run_survives_service_exception(self):
        """服务抛栈时不崩，降级到兜底。"""
        bad_advisor = MagicMock()
        bad_advisor.analyze.side_effect = RuntimeError("boom")
        with patch.object(asp, "_get_persona_analyzer", return_value=None), \
             patch.object(asp, "_get_strategy_advisor", return_value=bad_advisor), \
             patch.object(asp, "_get_task_engine", return_value=None), \
             patch.object(asp, "_get_tutorial_engine", return_value=None), \
             patch.object(asp, "_get_digital_twin", return_value=None):
            out = asp.run({})
        # 不抛栈，strategy 走兜底
        self.assertIn("strategy", out["fallbacks_used"])
        self.assertTrue(out["strategy"]["recommended"])

    def test_direction_compass_has_auto_strategy_entry(self):
        """懒接线：DirectionCompass 上有 auto_strategy 方法。"""
        self.assertTrue(hasattr(DirectionCompass, "auto_strategy"))
        dc = DirectionCompass()
        # 调用时所有服务都不可用，走兜底也能返回
        with patch.object(asp, "_get_persona_analyzer", return_value=None), \
             patch.object(asp, "_get_strategy_advisor", return_value=None), \
             patch.object(asp, "_get_task_engine", return_value=None), \
             patch.object(asp, "_get_tutorial_engine", return_value=None), \
             patch.object(asp, "_get_digital_twin", return_value=None):
            out = dc.auto_strategy({"person_id": "x"})
        self.assertIn("tasks", out)
        self.assertIn("candidates", out)
        self.assertIn("prediction", out)


if __name__ == "__main__":
    unittest.main()
