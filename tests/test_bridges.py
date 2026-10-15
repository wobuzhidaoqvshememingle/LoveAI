# -*- coding: utf-8 -*-
"""R3.3-77 算法桥接·网状联动 单元/集成测试（headless 真接线）。

覆盖：
  - BridgeBus 载荷 schema 校验（缺必填 key → 去噪丢弃）
  - 至少 3 组真双向桥：任务↔好感 / 好感↔桌宠 / 记忆↔任务 / 罗盘↔任务
  - 多跳事件驱动传播：任务完成 → 好感 → 桌宠事件流（真实模块状态可见）
  - 双向反馈：两端都真写
  - 冲突仲裁：两源同写同一软键，按优先级确定性裁决
  - 每桥指标可观测
  - 存量 EventBus 公开 API 不回归（subscribe/publish/去重/深度）
"""
import os
import sys
import json
import shutil
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402
from services.memory_service import MemoryService  # noqa: E402
from services.direction_compass import DirectionCompass  # noqa: E402
from services.relationship_graph import RelationshipGraph  # noqa: E402
from services.six_layer_persona import SixLayerProfile, Layer  # noqa: E402
from services import script_library  # noqa: E402
from services.event_bus import EventBus  # noqa: E402
from services.bridges import build_bridge_network, events as ev  # noqa: E402
from services.bridges.arbiter import (  # noqa: E402
    ConflictArbiter, PRI_AFFECTION, PRI_COMPASS, PRI_EXPRESSION)
from services.bridges.bus import BridgeBus  # noqa: E402
from services.bridges.metrics import MetricsCollector  # noqa: E402
from services.bridges.pet_sink import InMemoryPetSink  # noqa: E402

PID = "t_bridges_1"


class BridgesHarness:
    """搭一套真实模块 + 桥网络，供多个用例复用。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="loveai_bridges_test_")
        self.db = Database(os.path.join(self.tmp, "t.db"))
        self.db.init_tables()
        self.db.execute("INSERT INTO persons(id, nickname) VALUES(:p,:n)",
                        {"p": PID, "n": "测试对象"})
        self.affection = AffectionEngine(self.db)
        self.tasks = TaskEngine(self.db, self.affection, guide_engine=None, signals=None)
        self.memory = MemoryService(self.db)
        self.compass = DirectionCompass(db=None)
        self.portrait = SixLayerProfile(person_id=PID)
        self.graph = RelationshipGraph(center_id="me")
        self.pet_sink = InMemoryPetSink()
        low = {d: 25.0 for d in self.affection.WEIGHTS}
        self.db.execute(
            "INSERT OR REPLACE INTO current_affection(person_id, scores_json) "
            "VALUES(:p,:s)", {"p": PID, "s": json.dumps(low)})
        self.net = build_bridge_network(
            affection=self.affection, task_engine=self.tasks,
            compass=self.compass, memory=self.memory, portrait=self.portrait,
            script_lib=script_library, graph=self.graph, pet_sink=self.pet_sink)
        self.bus = self.net["bus"]
        self.sink = self.net["pet_sink"]

    def cleanup(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestBridgeBusSchema(unittest.TestCase):
    def test_missing_required_key_is_denoised(self):
        mc = MetricsCollector()
        bus = BridgeBus(metrics=mc)
        got = []
        bus.subscribe(ev.AFFECTION_CHANGED, lambda d: got.append(d))
        # 缺 person_id → 应被去噪，不触发
        r = bus.publish(ev.AFFECTION_CHANGED, {"total": 60})
        self.assertTrue(r.get("denoised"))
        self.assertEqual(len(got), 0)
        self.assertEqual(mc.denoised_events, 1)
        # 带全必填 → 正常触发
        bus.publish(ev.AFFECTION_CHANGED, {"person_id": PID, "total": 60})
        self.assertEqual(len(got), 1)

    def test_underlying_eventbus_semantics_preserved(self):
        # 存量 API 不回归：深度限制 + 单轮去重
        eb = EventBus(max_depth=2)
        hits = []
        eb.subscribe("x", lambda d: hits.append(1))
        eb.publish("x", {})
        self.assertEqual(len(hits), 1)
        eb.reset_pass()
        # 同轮重复发布被去重
        eb.publish("x", {})
        eb.publish("x", {})
        self.assertEqual(len(hits), 2)


class TestBridgesWired(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = BridgesHarness()

    @classmethod
    def tearDownClass(cls):
        cls.h.cleanup()

    def test_01_task_to_affection_real(self):
        h = self.h
        before = h.affection.get_current(PID)[next(iter(h.affection.WEIGHTS))]
        h.bus.reset_pass()
        h.bus.publish(ev.TASKS_COMPLETED, {
            "person_id": PID, "task_id": "t1", "title": "破冰", "reward": 5.0})
        after = h.affection.get_current(PID)[next(iter(h.affection.WEIGHTS))]
        self.assertGreater(after, before)

    def test_02_affection_to_pet_event_stream(self):
        # 多跳：好感变化后，桌宠 sink 应捕获 emotion 事件
        h = self.h
        h.bus.reset_pass()
        h.bus.publish(ev.AFFECTION_LEVEL_UPDATED,
                      {"person_id": PID, "total": 80.0, "trend": "up"})
        emo = h.sink.by_event("emotion")
        self.assertTrue(len(emo) >= 1)
        self.assertEqual(emo[-1]["data"]["emotion"], "happy")

    def test_03_pet_to_affection_bidirectional(self):
        h = self.h
        dim = next(iter(h.affection.WEIGHTS))
        before = h.affection.get_current(PID)[dim]
        h.bus.reset_pass()
        h.bus.publish(ev.PET_USER_INTERACTION,
                      {"person_id": PID, "kind": "pat", "intensity": 1.0})
        after = h.affection.get_current(PID)[dim]
        self.assertGreater(after, before)

    def test_04_memory_task_bidirectional(self):
        h = self.h
        h.bus.reset_pass()
        h.bus.publish(ev.MEMORY_FACT_LEARNED,
                      {"person_id": PID, "content": "喜欢猫"})
        # 记忆→任务：派生跟进任务（真实入 tasks 表）
        tasks = h.tasks.get_active_tasks(PID)
        self.assertTrue(any("猫" in (t.get("title") or "") for t in tasks),
                        f"未生成记忆跟进任务: {tasks}")
        # 任务→记忆：完成后沉淀结论
        h.bus.reset_pass()
        h.bus.publish(ev.TASKS_COMPLETED,
                      {"person_id": PID, "task_id": "tx", "title": "用猫话题破冰"})
        mems = h.memory.get_memories(PID, memory_type="conclusion")
        self.assertTrue(len(mems) >= 1)

    def test_05_compass_recalibrates_on_task(self):
        h = self.h
        h.bus.reset_pass()
        h.bus.publish(ev.TASKS_COMPLETED,
                      {"person_id": PID, "task_id": "tz", "title": "修复", "reward": 3.0})
        tc = h.net["bridges"]["task_compass"]
        self.assertIsNotNone(tc.last_verdict)
        self.assertIn("aligned", tc.last_verdict)

    def test_06_multi_hop_chain_visible(self):
        # 任务完成 → 好感 → 桌宠情绪，同一条链在 >=2 个真实模块状态上可见
        h = self.h
        pet_before = len(h.sink.by_event("emotion"))
        h.bus.reset_pass()
        h.bus.publish(ev.TASKS_COMPLETED,
                      {"person_id": PID, "task_id": "tmh", "title": "升温", "reward": 4.0})
        pet_after = len(h.sink.by_event("emotion"))
        self.assertGreater(pet_after, pet_before, "多跳未传播到桌宠事件流")

    def test_07_each_bridge_has_metrics(self):
        h = self.h
        s = h.net["metrics"].summary()
        self.assertGreaterEqual(s["total_bridges"], 4)
        for name, rep in s["bridges"].items():
            self.assertIn("conversions", rep)
            self.assertIn("failure_rate", rep)
            self.assertIn("avg_latency_ms", rep)

    def test_08_npc_to_task_injects(self):
        h = self.h
        before = len(h.tasks.get_active_tasks(PID))
        h.bus.reset_pass()
        h.bus.publish(ev.NPC_INFLUENCE_CHANGED, {
            "person_id": PID,
            "influential": [
                {"name": "前任", "relation_type": "前任", "affection_delta": -5},
                {"name": "闺蜜", "relation_type": "朋友", "affection_delta": 2}]})
        after = len(h.tasks.get_active_tasks(PID))
        self.assertGreater(after, before, "NPC 影响未注入任务")

    def test_09_task_to_graph_writes_back(self):
        h = self.h
        h.bus.reset_pass()
        h.bus.publish(ev.TASKS_COMPLETED,
                      {"person_id": PID, "task_id": "tnpc", "title": "助攻"})
        node = [n for n in h.graph.nodes.values() if n.name == PID]
        self.assertEqual(len(node), 1)
        self.assertGreater(node[0].intimacy, 40.0)


class TestArbiter(unittest.TestCase):
    def test_priority_resolution_deterministic(self):
        mc = MetricsCollector()
        arb = ConflictArbiter(mc)
        arb.claim("task_engine", "rhythm", "推进", PRI_AFFECTION, "task_affection")
        d = arb.claim("task_engine", "rhythm", "给空间", PRI_COMPASS, "task_compass")
        self.assertEqual(d["winner_bridge"], "task_affection")
        self.assertEqual(d["value"], "推进")
        self.assertFalse(d["is_winner"])  # compass 这一方是败者
        self.assertGreaterEqual(mc.arbitrated_writes, 1)

    def test_same_priority_later_wins(self):
        arb = ConflictArbiter()
        arb.claim("pet", "emotion", "neutral", PRI_EXPRESSION, "a")
        d = arb.claim("pet", "emotion", "happy", PRI_EXPRESSION, "b")
        self.assertEqual(d["winner_bridge"], "b")
        self.assertEqual(d["value"], "happy")


if __name__ == "__main__":
    unittest.main()
