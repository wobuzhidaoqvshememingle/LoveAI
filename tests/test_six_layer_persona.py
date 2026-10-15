# -*- coding: utf-8 -*-
"""R3.3-45 六层人格画像 单测。"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.six_layer_persona import (  # noqa: E402
    Layer, SixLayerProfile, EVIDENCE_THRESHOLD,
    WORLD_INFO_LAYERS, VECTOR_LAYERS, to_six_layer,
)


class TestSixLayerProfile(unittest.TestCase):

    def _fill(self, prof: SixLayerProfile, per_layer: int) -> None:
        for layer in Layer:
            for i in range(per_layer):
                prof.add_evidence(layer, {"quote": f"{layer.value}-{i}",
                                          "source": "test"})

    def test_add_evidence_and_count(self):
        p = SixLayerProfile(person_id="p1")
        p.add_evidence(Layer.L1_SURFACE, {"quote": "爱发哈哈"})
        p.add_evidence(Layer.L1_SURFACE, {"quote": "回复快"})
        self.assertEqual(len(p.evidence[Layer.L1_SURFACE]), 2)

    def test_confidence_scaling(self):
        p = SixLayerProfile()
        # 0 条 → 0.0
        self.assertEqual(p.confidence_per_layer()[Layer.L1_SURFACE.value], 0.0)
        # 2 条 → 2/3 ≈ 0.667
        for i in range(2):
            p.add_evidence(Layer.L1_SURFACE, {"quote": f"x{i}"})
        self.assertAlmostEqual(
            p.confidence_per_layer()[Layer.L1_SURFACE.value],
            2 / EVIDENCE_THRESHOLD, places=2)
        # 5 条 → 1.0（封顶）
        for i in range(3):
            p.add_evidence(Layer.L2_BEHAVIOR, {"quote": f"y{i}"})
        self.assertEqual(p.confidence_per_layer()[Layer.L2_BEHAVIOR.value], 1.0)

    def test_pending_layers(self):
        p = SixLayerProfile()
        # 全空 → 六层都待补
        pending = p.pending_layers()
        self.assertEqual(len(pending), len(list(Layer)))
        # 填满 L1 → 只剩五层待补
        for i in range(EVIDENCE_THRESHOLD):
            p.add_evidence(Layer.L1_SURFACE, {"quote": f"x{i}"})
        pending = p.pending_layers()
        self.assertNotIn(Layer.L1_SURFACE.value, pending)
        self.assertIn(Layer.L2_BEHAVIOR.value, pending)

    def test_summary_structure(self):
        p = SixLayerProfile(person_id="u1")
        self._fill(p, per_layer=5)  # 每层都够 3 条
        s = p.summary()
        self.assertEqual(s["person_id"], "u1")
        self.assertIn("per_layer", s)
        self.assertEqual(len(s["per_layer"]), len(list(Layer)))
        self.assertEqual(s["pending_layers"], [])
        self.assertGreaterEqual(s["maturity"], 0.9)
        self.assertEqual(s["maturity_label"], "成熟画像")
        for layer, info in s["per_layer"].items():
            self.assertEqual(info["status"], "ok")
            self.assertEqual(info["evidence_count"], 5)

    def test_writable_to_memory_split(self):
        p = SixLayerProfile()
        # L1/L2/L3 → vector；L4/L5/L6 → world_info
        for layer in VECTOR_LAYERS:
            p.add_evidence(layer, {"quote": f"v-{layer.value}"})
        for layer in WORLD_INFO_LAYERS:
            p.add_evidence(layer, {"quote": f"w-{layer.value}"})
        out = p.writable_to_memory()
        self.assertEqual(len(out["vector"]), 3)
        self.assertEqual(len(out["world_info"]), 3)
        # 内容归属正确
        vec_quotes = " ".join(e["quote"] for e in out["vector"])
        world_quotes = " ".join(e["quote"] for e in out["world_info"])
        for layer in VECTOR_LAYERS:
            self.assertIn(layer.value, vec_quotes)
            self.assertNotIn(layer.value, world_quotes)
        for layer in WORLD_INFO_LAYERS:
            self.assertIn(layer.value, world_quotes)
            self.assertNotIn(layer.value, vec_quotes)

    def test_bad_layer_raises(self):
        p = SixLayerProfile()
        with self.assertRaises(TypeError):
            p.add_evidence("L1_SURFACE", {"quote": "x"})  # type: ignore[arg-type]

    def test_to_six_layer_maps_fields(self):
        fake_persona = {
            "person_id": "p9",
            "relationship_stage": "暧昧期",
            "communication_style": {"tone": "温和", "reply_speed": "快"},
            "behavior": {"initiation_rate": 0.6, "cancel_rate": 0.1,
                         "fulfillment_rate": 0.8},
            "emotion_pattern": {"volatility": "中", "empathy": "高"},
            "attachment": {"primary_type": "anxious",
                           "avoidance_score": 25, "anxiety_score": 70},
            "values": {"money_view": "AA", "loyalty_view": "忠诚"},
            "psychology": {"dark_triad_tendencies": {"自恋": 1}},
            "core_self": {"family_origin": "严父", "self_worth": "低"},
            "primary_personality": "anxious_preoccupied",
        }
        prof = to_six_layer(fake_persona, person_id="p9")
        self.assertEqual(prof.person_id, "p9")
        # 每层都至少有证据
        for layer in Layer:
            self.assertGreaterEqual(len(prof.evidence[layer]), 1,
                                    f"{layer.value} 没拿到证据")
        # L4 依恋应该有 primary_type
        l4_quotes = " ".join(e["quote"] for e in prof.evidence[Layer.L4_ATTACHMENT])
        self.assertIn("anxious", l4_quotes)
        # L6 核心自我应该有 primary_personality
        l6_quotes = " ".join(e["quote"] for e in prof.evidence[Layer.L6_CORE])
        self.assertIn("anxious_preoccupied", l6_quotes)

    def test_to_six_layer_handles_empty(self):
        # 空 dict 不抛栈
        prof = to_six_layer({})
        self.assertEqual(prof.person_id, "")
        self.assertEqual(sum(len(v) for v in prof.evidence.values()), 0)


if __name__ == "__main__":
    unittest.main()
