# -*- coding: utf-8 -*-
"""
R3.3-81 指标函数化改造：分析→delta 推理层单元测试。

覆盖：
  - 推理层产出"内心状态维度进度报告"（18维 delta + loyalty + 证据链）
  - 反机械性：同一关键词在不同情绪/语境下 delta 不同（甚至相反）
  - 顺从/违抗姿态 → loyalty（不污染好感）
  - 敷衍/中性寒暄 → 无真实进展时 delta≈0
  - 证据链格式："她说X → 推断她内心Y → Z维度Δ"
  - 任务推进门禁：命中关键词但分析判定无进展 → 不推进
  - 与 AffectionEngine 集成：update_from_message 返回 reasoning_report
  - 18维+loyalty 存储结构不破坏
"""
import os
import sys
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from services.affection_reasoning import AffectionReasoner  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402


def _make_person(db, pid):
    db.execute("INSERT INTO persons(id, nickname) VALUES(:p, :n)", {"p": pid, "n": pid})


class TestReasonerCore(unittest.TestCase):
    def setUp(self):
        self.r = AffectionReasoner()

    def test_report_structure(self):
        rep = self.r.build_report(
            [{"content": "想你了宝贝，今天给你点了外卖", "direction": "received"}],
            {"label": "love", "intensity": 0.9}, {})
        self.assertIn("dimensions", rep)
        self.assertIn("loyalty_delta", rep)
        self.assertIn("evidence", rep)
        self.assertIn("conclusions", rep)
        # 报告应带证据链
        self.assertGreater(len(rep["evidence"]), 0)
        self.assertIsInstance(rep["evidence"][0], dict)

    def test_warm_message_raises_affection(self):
        rep = self.r.build_report(
            [{"content": "想你了宝贝，今天给你点了外卖", "direction": "received"}],
            {"label": "love", "intensity": 0.9}, {})
        # 亲密/信任/情绪价值应正向
        self.assertGreater(rep["dimensions"]["intimacy"], 0)
        self.assertGreater(rep["dimensions"]["trust"], 0)
        self.assertGreater(rep["dimensions"]["emotional_value"], 0)

    def test_negative_message_lowers(self):
        rep = self.r.build_report(
            [{"content": "别烦我，关你什么事", "direction": "received"}],
            {"label": "angry", "intensity": 0.9}, {})
        self.assertLess(rep["dimensions"]["trust"], 0)
        self.assertLess(rep["dimensions"]["security"], 0)

    def test_same_keyword_different_context(self):
        """核心反机械性：同一句含'想你'，love 语境 vs cold 反话语境 delta 相反。"""
        warm = self.r.build_report(
            [{"content": "想你了宝贝", "direction": "received"}],
            {"label": "love", "intensity": 0.9}, {})
        cold = self.r.build_report(
            [{"content": "想你？呵，你少来这套", "direction": "received"}],
            {"label": "cold", "intensity": 0.8}, {})
        # 温暖语境：亲密正向
        self.assertGreater(warm["dimensions"].get("intimacy", 0), 0)
        # 反话语境：信任不涨反降（亲密词在负情绪下不算数）
        self.assertLess(cold["dimensions"].get("trust", 0), 0)
        # 关键：两者 delta 方向不同（不是固定关键词分）
        self.assertNotEqual(
            warm["dimensions"].get("intimacy", 0),
            cold["dimensions"].get("intimacy", 0),
        )

    def test_compliance_drives_loyalty_not_affection(self):
        rep = self.r.build_report(
            [{"content": "好好好，我都听你的，这次是我错了", "direction": "received"}],
            {"label": "neutral", "intensity": 0.2}, {})
        self.assertGreater(rep["loyalty_delta"], 0)
        # 顺从话术不推好感维
        self.assertEqual(rep["dimensions"], {})

    def test_defiance_lowers_loyalty(self):
        rep = self.r.build_report(
            [{"content": "凭什么要我改？你管不着", "direction": "received"}], {}, {})
        self.assertLess(rep["loyalty_delta"], 0)

    def test_perfunctory_no_mechanical_gain(self):
        rep = self.r.build_report(
            [{"content": "嗯", "direction": "received"}],
            {"label": "neutral", "intensity": 0.1}, {})
        # 敷衍/中性寒暄：无真实进展，总加权增量很小（不再固定 -0.5 机械扣分）
        total_abs = sum(abs(v) for v in rep["dimensions"].values())
        self.assertLess(total_abs, 0.5)

    def test_evidence_chain_format(self):
        rep = self.r.build_report(
            [{"content": "今天给你点了外卖", "direction": "received"}],
            {"label": "love", "intensity": 0.8}, {})
        joined = " ".join(e["text"] for e in rep["evidence"])
        # 证据链三段式：她说X → 推断内心Y → Z维度Δ
        self.assertIn("她说", joined)
        self.assertIn("→", joined)


class TestTaskProgressGate(unittest.TestCase):
    """验收点3：任务推进由分析结论门禁。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_r3381_gate_")
        self.db = Database(os.path.join(self.tmpdir, "g.db"))
        self.db.init_tables()
        self.reasoner = AffectionReasoner()
        self.engine = TaskEngine(self.db, FakeAffection(), guide_engine=None,
                                 reasoner=self.reasoner)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _limited(self):
        return self.engine.generate_limited_task("p1", "周末一起吃饭吗")

    def test_genuine_progress_advances(self):
        lim = self._limited()
        self.assertIsNotNone(lim)
        tid = lim["id"]
        self.engine.check_completion("p1", "我对这家餐厅很感兴趣",
                                     emotion={"label": "happy", "intensity": 0.6})
        prog = self.engine.get_task_progress(tid)
        self.assertEqual(prog["current_step"], 1)

    def test_cold_keyword_hit_does_not_advance(self):
        """命中完成关键词'兴趣'，但分析判定对方敷衍/抵触 → 不推进。"""
        lim = self._limited()
        tid = lim["id"]
        # 含"兴趣"完成关键词，但情绪冷淡/抵触
        self.engine.check_completion("p1", "兴趣？随便吧，无所谓",
                                     emotion={"label": "cold", "intensity": 0.8})
        prog = self.engine.get_task_progress(tid)
        self.assertEqual(prog["current_step"], 0,
                         "命中关键词但无真实进展不应推进任务")


class FakeAffection:
    def get_route(self, person_id): return "guide"
    def add_bonus(self, person_id, bonus, reason=""): pass
    def add_loyalty_bonus(self, person_id, bonus, reason=""): pass
    def get_current(self, person_id): return {}
    def get_total(self, scores): return 50.0
    def get_total_with_bonus(self, p, s): return 50.0
    def get_stage(self, t): return 0


class TestEngineIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_r3381_eng_")
        self.db = Database(os.path.join(self.tmpdir, "e.db"))
        self.db.init_tables()
        self.engine = AffectionEngine(self.db)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_update_returns_reasoning_report(self):
        _make_person(self.db, "p_int")
        r = self.engine.update_from_message(
            "p_int",
            {"content": "想你了宝贝，今天给你点了外卖", "direction": "received"},
            emotion={"label": "love", "intensity": 0.9},
        )
        self.assertIn("reasoning_report", r)
        rep = r["reasoning_report"]
        self.assertIn("dimensions", rep)
        self.assertGreater(r["total_score"], 50.0)
        # reasons 来自证据链
        self.assertTrue(any("她说" in x or "姿态" in x for x in r["reasons"]))

    def test_storage_structure_intact(self):
        """18维 + loyalty 独立存储不破坏。"""
        _make_person(self.db, "p_str")
        self.engine.update_from_message(
            "p_str", {"content": "别烦我", "direction": "received"},
            emotion={"label": "angry", "intensity": 0.8})
        cur = self.engine.get_current("p_str")
        self.assertEqual(len([k for k in cur if k != "loyalty"]), 18)
        self.assertIn("loyalty", cur)
        # loyalty 不进加权总分
        self.assertAlmostEqual(self.engine.get_total(
            {d: 50.0 for d in AffectionEngine.WEIGHTS}), 50.0)

    def test_llm_hints_still_respected(self):
        """analysis.affection_hints 叠加路径保留（可选 LLM 增强）。"""
        _make_person(self.db, "p_hint")
        r = self.engine.update_from_message(
            "p_hint", {"content": "随便聊聊", "direction": "received"},
            emotion={"label": "neutral", "intensity": 0.1},
            analysis={"affection_hints": {"emotional_value": 100.0}},
        )
        # 单日上限 10 封顶
        self.assertLessEqual(abs(r["applied"]["emotional_value"]), 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
