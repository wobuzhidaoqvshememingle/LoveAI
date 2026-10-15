# -*- coding: utf-8 -*-
"""R3.3-80 桥接层真接线测试（headless，真实 TaskEngine + 临时 sqlite + build_bridge_network）。

参照 tests/test_bridges.py 的 BridgesHarness：真实模块实例，不打桩。
覆盖：
  A 类 family_system_bridge：发布含祖辈/继子女/负债画像 → 任务表真实出现对应任务（查库）；
  B 类 milestone_task_bridge：STAGE_CHANGED → 落该阶段里程碑任务（查库）；
  D 类 milestone_task_bridge：rebound 循环/纠缠/新欢 → 落止损/冷静期干预任务；
  F 类 trauma_portrait_bridge：讨好型/被抛弃 → 落边界/锚点任务 + strategy claim；
  星图接线：graph 传入时节点数真实增加；graph=None 时仍落任务不炸；
  每桥 metrics 记录到转换；同画像重复发布幂等去重；未知阶段不炸。
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
from services.six_layer_persona import SixLayerProfile  # noqa: E402
from services import script_library  # noqa: E402
from services.bridges import build_bridge_network, events as ev  # noqa: E402
from services.bridges.pet_sink import InMemoryPetSink  # noqa: E402
from services.bridges.complexity_publisher import publish_complexity_profile  # noqa: E402

PID = "t_r3380_1"


class Harness:
    """真实模块 + 桥网络；per-test 新建，保证星图节点/任务计数隔离。"""

    def __init__(self, with_graph: bool = True):
        self.tmp = tempfile.mkdtemp(prefix="loveai_r3380_")
        self.db = Database(os.path.join(self.tmp, "t.db"))
        self.db.init_tables()
        self.db.execute("INSERT INTO persons(id, nickname) VALUES(:p,:n)",
                        {"p": PID, "n": "测试对象"})
        self.affection = AffectionEngine(self.db)
        self.tasks = TaskEngine(self.db, self.affection,
                                guide_engine=None, signals=None)
        self.memory = MemoryService(self.db)
        self.compass = DirectionCompass(db=None)
        self.portrait = SixLayerProfile(person_id=PID)
        self.graph = RelationshipGraph(center_id="me") if with_graph else None
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

    def titles(self) -> list[str]:
        return [t.get("title") or "" for t in self.tasks.get_active_tasks(PID)]

    def cleanup(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 复用的画像片段（符合 r3380_spec.md 冻结 schema）
# --------------------------------------------------------------------------- #
def _family_payload(who=None, step=True, debt=True):
    who = who or ["奶奶"]
    return {
        "person_id": PID,
        "family_system": {
            "grandparent_influence": {
                "detected": True, "who": who, "stance": "观望",
                "confidence": 0.8, "evidence": ["奶奶说了算"], "adaptation": "见家长先经营祖辈"},
            "stepchildren": {
                "detected": step, "acceptance_level": "低", "confidence": 0.7,
                "evidence": ["她女儿见都不想见"], "adaptation": "先做平行朋友"},
            "family_debt": {
                "detected": debt, "level": "中", "evidence": ["家里还在还房贷"],
                "adaptation": "金钱节奏放慢"},
            "caregiving_pressure": {
                "detected": False, "level": "未知", "evidence": [], "adaptation": ""},
            "remarried_family": {"detected": True, "confidence": 0.6,
                                  "evidence": ["离异带娃"], "adaptation": ""},
        },
        "rebound_pattern": {"cycle_count": 0, "entangled": False,
                            "ex_new_partner": False, "escalation_level": "无",
                            "intervention_tasks": []},
        "trauma_drivers": {"traumas": [], "attachment_link": "", "strategy_adaptations": []},
    }


def _rebound_payload():
    return {
        "person_id": PID,
        "family_system": {"grandparent_influence": {"detected": False},
                         "stepchildren": {"detected": False}},
        "rebound_pattern": {
            "cycle_count": 2, "cycle_evidence": ["分手 → 和好"],
            "entangled": True, "ex_new_partner": True,
            "escalation_level": "高", "intervention_tasks": ["循环≥2轮"]},
        "trauma_drivers": {"traumas": [], "strategy_adaptations": []},
    }


def _trauma_payload():
    return {
        "person_id": PID,
        "family_system": {"grandparent_influence": {"detected": False},
                         "stepchildren": {"detected": False}},
        "rebound_pattern": {"cycle_count": 0, "entangled": False, "ex_new_partner": False},
        "trauma_drivers": {
            "traumas": [
                {"type": "讨好型损耗", "evidence": ["一直在付出"],
                 "behavior_link": "长期讨好→需求后置"},
                {"type": "被抛弃恐惧", "evidence": ["一不回消息就慌"],
                 "behavior_link": "被抛弃史→频繁确认"}],
            "attachment_link": "焦虑型",
            "strategy_adaptations": [
                "引导对方表达真实偏好，任务设计避免单向付出",
                "主动给稳定的可预期回应，减少突然失联"]},
    }


class TestR3380Bridges(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.cleanup()

    def test_01_family_tasks_really_in_db(self):
        """A 类：发布含祖辈+继子女+负债画像 → 任务表查库出现三类任务。"""
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, _family_payload())
        titles = self.h.titles()
        self.assertTrue(any("见家长支线" in t and "祖辈" in t for t in titles),
                        f"未见祖辈见家长任务: {titles}")
        self.assertTrue(any("继子女接受度" in t for t in titles),
                        f"未见继子女任务: {titles}")
        self.assertTrue(any("关系节奏调整提示" in t and "负债" in t for t in titles),
                        f"未见负债节奏提示任务: {titles}")

    def test_02_milestone_stage_changed(self):
        """B 类：STAGE_CHANGED → 落该阶段可解锁里程碑任务。"""
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.STAGE_CHANGED, {"person_id": PID, "stage": "未婚夫妻"})
        titles = self.h.titles()
        self.assertTrue(any(t.startswith("[里程碑]") for t in titles),
                        f"未见里程碑任务: {titles}")
        self.assertTrue(any("[里程碑]求婚" in t for t in titles),
                        f"未婚夫妻阶段应解锁求婚: {titles}")

    def test_03_breakup_cycle_intervention(self):
        """D 类：循环≥2+纠缠+新欢 → 落止损/冷静期干预任务（查库）。"""
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, _rebound_payload())
        titles = self.h.titles()
        self.assertTrue(any("冷静期" in t for t in titles),
                        f"未见冷静期干预任务: {titles}")
        self.assertTrue(any("纠缠" in t or "视奸" in t for t in titles),
                        f"未见纠缠/新欢叠加任务: {titles}")

    def test_04_trauma_tasks_and_claim(self):
        """F 类：讨好型/被抛弃 → 落边界/锚点任务，且 strategy_adaptations 被 claim。"""
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, _trauma_payload())
        titles = self.h.titles()
        self.assertTrue(any("边界练习" in t for t in titles), f"未见边界练习任务: {titles}")
        self.assertTrue(any("稳定锚点" in t for t in titles), f"未见稳定锚点任务: {titles}")
        tp = self.h.net["bridges"]["trauma_portrait"]
        summ = tp.network_summary()
        self.assertGreaterEqual(len(summ["claimed_adaptations"]), 2,
                                f"strategy_adaptations 未 claim: {summ}")

    def test_05_graph_nodes_increased(self):
        """星图接线：graph 传入时，祖辈节点真实写入星图，节点数增加。"""
        before = len(self.h.graph.nodes)
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED,
                           _family_payload(who=["奶奶"], step=False, debt=False))
        after = len(self.h.graph.nodes)
        self.assertGreater(after, before,
                           f"星图节点未增加: before={before} after={after}")
        names = [getattr(n, "name", "") for n in self.h.graph.nodes.values()]
        self.assertIn("奶奶", names, f"星图未见祖辈节点: {names}")

    def test_06_metrics_recorded_per_bridge(self):
        """发布后 bus/metrics 记录到三座新桥的转换。"""
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, _family_payload())
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, _trauma_payload())
        # 补一份有循环信号的画像，确保 milestone_task(D类) 产生转换
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, _rebound_payload())
        s = self.h.net["metrics"].summary()["bridges"]
        for name in ("family_system", "milestone_task", "trauma_portrait"):
            self.assertIn(name, s, f"metrics 缺桥 {name}: {list(s)}")
            self.assertGreaterEqual(s[name]["conversions"], 1,
                                    f"桥 {name} 无转换记录: {s[name]}")

    def test_07_no_graph_still_tasks_no_crash(self):
        """graph=None 时 family_system 桥不接星图但仍落任务、不炸。"""
        h = Harness(with_graph=False)
        try:
            h.bus.reset_pass()
            h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED,
                          _family_payload(who=["姥姥"], step=False, debt=False))
            titles = h.titles()
            self.assertTrue(any("见家长支线" in t for t in titles),
                            f"无 graph 时未落祖辈任务: {titles}")
        finally:
            h.cleanup()

    def test_08_duplicate_publish_idempotent(self):
        """同画像重复发布：任务按标题去重，不重复落库。"""
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, _family_payload())
        n1 = len([t for t in self.h.titles() if "见家长支线" in t])
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.COMPLEXITY_PROFILE_UPDATED, _family_payload())
        n2 = len([t for t in self.h.titles() if "见家长支线" in t])
        self.assertEqual(n1, n2, f"重复发布产生重复任务: n1={n1} n2={n2}")

    def test_09_unknown_stage_no_crash_no_tasks(self):
        """未知阶段名：STAGE_CHANGED 不炸、不落里程碑任务。"""
        self.h.bus.reset_pass()
        self.h.bus.publish(ev.STAGE_CHANGED, {"person_id": PID, "stage": "火星阶段"})
        titles = self.h.titles()
        self.assertFalse(any(t.startswith("[里程碑]") for t in titles),
                         f"未知阶段不应落里程碑任务: {titles}")

    def test_10_publisher_end_to_end_real_messages(self):
        """全链路：publish_complexity_profile 跑真实中文消息 → 画像返回且桥消费落任务。"""
        messages = [
            {"content": "我奶奶在家里说了算，她不同意的事我对象很难进门"},
            {"content": "她是离异带个娃过来的，那个孩子是她和前任的孩子"},
            {"content": "那个孩子现在见都不想见我，挺抵触"},
            {"content": "家里还有房贷要还，老人养老压力也大"},
            {"content": "上次她突然说分手吧，别联系了"},
            {"content": "结果没几天又加回来，说我们和好吧"},
            {"content": "后来又吵一架，她说散了吧结束吧"},
            {"content": "没过多久我们又在一起了，反复断联复联拉扯"},
            {"content": "我都听说她现在有新男友了，我还在反复"},
            {"content": "我总是一直在付出、委屈自己，一不回消息我就慌"},
        ]
        profile = publish_complexity_profile(
            self.h.bus, PID, messages, persona_result=None)
        self.assertTrue(profile["family_system"]["grandparent_influence"]["detected"])
        self.assertTrue(profile["rebound_pattern"]["cycle_count"] >= 1)
        self.assertTrue(profile["trauma_drivers"]["traumas"])
        titles = self.h.titles()
        # 祖辈 + 继子女 + 负债 + 干预/边界任务至少各有一类落库
        self.assertTrue(any("见家长支线" in t for t in titles), f"未落祖辈任务: {titles}")
        self.assertTrue(any("边界练习" in t or "稳定锚点" in t or "冷静期" in t
                            for t in titles), f"未落干预/边界任务: {titles}")


if __name__ == "__main__":
    unittest.main()
