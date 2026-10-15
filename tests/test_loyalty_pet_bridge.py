# -*- coding: utf-8 -*-
"""R3.3-78 LoyaltyPetBridge 单元/集成测试（headless 全链路真接线）。

覆盖：
  - LOYALTY_CHANGED 经 BridgeBus 发布 → LoyaltyPetBridge → InMemoryPetSink 捕获
    emotion 事件，且携带 metric="loyalty" / feedback="顺从" / 顺从向情绪；
  - 缺 person_id 去噪不投；loyalty 钳制在 0~100；
  - 与好感类事件路径（AffectionPetBridge 投的 emotion 事件）载荷可区分，
    不共用同一套表达（双轨验收点）；
  - mount_loyalty_bridge(net) 挂载助手真接线。
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.bridges.bus import BridgeBus  # noqa: E402
from services.bridges.metrics import MetricsCollector  # noqa: E402
from services.bridges.arbiter import ConflictArbiter  # noqa: E402
from services.bridges.pet_sink import InMemoryPetSink  # noqa: E402
from services.bridges import events as ev  # noqa: E402
from services.bridges.affection_pet_bridge import AffectionPetBridge  # noqa: E402
from services.bridges.loyalty_pet_bridge import (  # noqa: E402
    LoyaltyPetBridge, LOYALTY_CHANGED, mount_loyalty_bridge)

PID = "t_loyalty_1"


class TestLoyaltyPetBridge(unittest.TestCase):
    def setUp(self):
        self.bus = BridgeBus()
        self.sink = InMemoryPetSink()
        self.bridge = LoyaltyPetBridge(self.bus, self.sink)
        self.bridge.setup()

    def test_loyalty_changed_emits_obedient_emotion(self):
        self.bus.publish(LOYALTY_CHANGED, {
            "person_id": PID, "loyalty": 72.0, "delta": 3.0,
            "reason": "反制话术生效，对方服从度上升"})
        emos = self.sink.by_event("emotion")
        self.assertEqual(len(emos), 1, "sink 未捕获 emotion 事件")
        d = emos[0]["data"]
        # 核心载荷：loyalty 值 + 反制线标记 + 顺从向反馈
        self.assertEqual(d["loyalty"], 72.0)
        self.assertEqual(d["metric"], "loyalty")
        self.assertEqual(d["feedback"], "顺从")
        self.assertIn(d["emotion"], ("obedient", "calm"))
        # 桥内部状态可见
        self.assertEqual(self.bridge.last_loyalty, 72.0)
        self.assertEqual(self.bridge.last_feedback, "顺从")

    def test_missing_person_id_is_noop(self):
        self.bus.publish(LOYALTY_CHANGED, {"loyalty": 60.0, "delta": 1.0})
        self.assertEqual(self.sink.events, [], "缺 person_id 应去噪不投")

    def test_missing_loyalty_is_noop(self):
        self.bus.publish(LOYALTY_CHANGED, {"person_id": PID, "delta": 1.0})
        self.assertEqual(self.sink.events, [])

    def test_loyalty_clamped_to_0_100(self):
        self.bus.publish(LOYALTY_CHANGED,
                         {"person_id": PID, "loyalty": 999.0, "delta": 9.0})
        d = self.sink.by_event("emotion")[0]["data"]
        self.assertEqual(d["loyalty"], 100.0)

        self.bus.reset_pass()
        self.sink.events.clear()
        self.bus.publish(LOYALTY_CHANGED,
                         {"person_id": PID, "loyalty": -50.0, "delta": -5.0})
        d = self.sink.by_event("emotion")[0]["data"]
        self.assertEqual(d["loyalty"], 0.0)

    def test_loyalty_vs_affection_payloads_distinguishable(self):
        """双轨验收：忠诚桥与好感桥投出的 emotion 事件载荷必须可区分。"""
        # 同向搭一座好感桥（direction A：affection_to_pet 不需要真实 affection 引擎）
        aff_bridge = AffectionPetBridge(self.bus, affection=None,
                                        pet_sink=self.sink)
        aff_bridge.setup()

        # 1) 反制线：忠诚上涨
        self.bus.publish(LOYALTY_CHANGED, {
            "person_id": PID, "loyalty": 80.0, "delta": 4.0, "reason": "服从强化"})
        self.bus.reset_pass()
        # 2) 引导线：好感上涨（既有 AFFECTION_LEVEL_UPDATED 事件）
        self.bus.publish(ev.AFFECTION_LEVEL_UPDATED, {
            "person_id": PID, "total": 80.0, "trend": "up"})

        emos = self.sink.by_event("emotion")
        self.assertEqual(len(emos), 2)
        loyalty_payload = emos[0]["data"]
        affection_payload = emos[1]["data"]

        # 忠诚侧：metric="loyalty" + 顺从 feedback + 顺从向情绪
        self.assertEqual(loyalty_payload["metric"], "loyalty")
        self.assertEqual(loyalty_payload["feedback"], "顺从")
        self.assertIn(loyalty_payload["emotion"], ("obedient", "calm"))
        self.assertIn("loyalty", loyalty_payload)

        # 好感侧：走既有风格（affection 总分 + happy/curious/neutral/sad 情绪），
        # 绝不能带 loyalty 标记 / 顺从 feedback——即不共用同一套表达
        self.assertNotIn("loyalty", affection_payload)
        self.assertNotIn("metric", affection_payload)
        self.assertNotEqual(affection_payload.get("feedback"), "顺从")
        self.assertIn(affection_payload["emotion"],
                      ("happy", "curious", "neutral", "sad"))
        self.assertIn("affection", affection_payload)

        # 两条载荷在"路线标记"字段上必须不一致
        self.assertNotEqual(loyalty_payload.get("metric"),
                            affection_payload.get("metric"))


class TestMountLoyaltyBridge(unittest.TestCase):
    def test_mount_helper_registers_and_routes(self):
        net = {
            "bus": BridgeBus(),
            "pet_sink": InMemoryPetSink(),
            "metrics": MetricsCollector(),
            "arbiter": ConflictArbiter(),
            "bridges": {},
        }
        net = mount_loyalty_bridge(net)
        self.assertIn("loyalty_pet", net["bridges"])
        self.assertIsInstance(net["bridges"]["loyalty_pet"], LoyaltyPetBridge)

        net["bus"].publish(LOYALTY_CHANGED, {
            "person_id": PID, "loyalty": 66.0, "delta": 2.0, "reason": "挂载后验证"})
        emos = net["pet_sink"].by_event("emotion")
        self.assertEqual(len(emos), 1)
        self.assertEqual(emos[0]["data"]["metric"], "loyalty")
        self.assertEqual(emos[0]["data"]["feedback"], "顺从")


if __name__ == "__main__":
    unittest.main()
