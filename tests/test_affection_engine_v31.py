# -*- coding: utf-8 -*-
"""
LoveAI v3.1 - AffectionEngine 新增能力单元测试

覆盖：
  - summarize_to_three_axes：吸引力/信任/舒适度三大类汇总
  - energy 能量维度：深度话题扣能量 / 浅层回能量 / 透支建议
  - explain_change：变化原因人性化
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


class TestThreeAxes(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_aff_v31_")
        self.db = Database(os.path.join(self.tmpdir, "a.db"))
        self.db.init_tables()
        self.engine = AffectionEngine(self.db)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _scores(self, **overrides):
        s = {dim: 50.0 for dim in self.engine.WEIGHTS}
        s.update(overrides)
        return s

    def test_three_axes_structure(self):
        out = self.engine.summarize_to_three_axes(self._scores())
        self.assertIn("attraction", out)
        self.assertIn("trust", out)
        self.assertIn("comfort", out)
        self.assertIn("overall", out)

    def test_attraction_reflects_dimensions(self):
        s = self._scores(physical_attraction=80, humor=80, romance=80, chat_resonance=80)
        out = self.engine.summarize_to_three_axes(s)
        self.assertEqual(out["attraction"]["score"], 80.0)

    def test_trust_reflects_dimensions(self):
        s = self._scores(trust=90, security=90, thoughtfulness=90, respect=90)
        out = self.engine.summarize_to_three_axes(s)
        self.assertEqual(out["trust"]["score"], 90.0)

    def test_comfort_reflects_dimensions(self):
        s = self._scores(emotional_stability=40, emotional_value=40,
                         intimacy=40, independence=40)
        out = self.engine.summarize_to_three_axes(s)
        self.assertEqual(out["comfort"]["score"], 40.0)

    def test_overall_is_average(self):
        s = self._scores(physical_attraction=70, humor=70, romance=70, chat_resonance=70,
                         trust=50, security=50, thoughtfulness=50, respect=50,
                         emotional_stability=30, emotional_value=30,
                         intimacy=30, independence=30)
        out = self.engine.summarize_to_three_axes(s)
        expected = round((70.0 + 50.0 + 30.0) / 3, 1)
        self.assertEqual(out["overall"], expected)

    def test_axis_level_label(self):
        s = self._scores(physical_attraction=90, humor=90, romance=90, chat_resonance=90)
        out = self.engine.summarize_to_three_axes(s)
        self.assertEqual(out["attraction"]["level"], "高")


class TestEnergy(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_energy_")
        self.db = Database(os.path.join(self.tmpdir, "e.db"))
        self.db.init_tables()
        self.engine = AffectionEngine(self.db)
        # 每个用例用唯一 person_id，避免 energy 持久化跨用例串状态
        import uuid
        self.pid = "energy_" + uuid.uuid4().hex[:8]

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_default_energy(self):
        self.assertEqual(self.engine.get_energy(self.pid), 80.0)

    def test_deep_topic_drains_energy(self):
        r = self.engine.apply_energy_from_message(
            self.pid, "我小时候原生家庭不太好，想聊聊未来规划")
        self.assertEqual(r["delta"], -15.0)
        self.assertEqual(r["energy"], 65.0)

    def test_shallow_topic_restores_energy(self):
        r = self.engine.apply_energy_from_message(self.pid, "晚安啦~")
        self.assertEqual(r["delta"], 3.0)
        self.assertEqual(r["energy"], 83.0)

    def test_other_initiated_boosts_energy(self):
        r = self.engine.apply_energy_from_message(
            self.pid, "哈哈在干嘛", other_initiated=True)
        self.assertEqual(r["delta"], 13.0)  # +3 shallow +10 proactive

    def test_low_energy_suggests_rest(self):
        # 连续深度话题把能量打到透支
        self.engine.apply_energy_from_message(self.pid, "聊我的童年创伤和未来规划")
        self.engine.apply_energy_from_message(self.pid, "再聊聊原生家庭")
        self.engine.apply_energy_from_message(self.pid, "继续深入谈人生目标")
        self.engine.apply_energy_from_message(self.pid, "又谈了过去的事")
        e = self.engine.get_energy(self.pid)
        r = self.engine.apply_energy_from_message(self.pid, "继续聊聊我的人生规划")
        self.assertLessEqual(e, 20.0)
        self.assertIn("休息", r["suggestion"])


class TestExplainChange(unittest.TestCase):
    def test_explain_with_dimension(self):
        s = AffectionEngine.explain_change(3.0, "other_initiated_date", "亲密感")
        self.assertIn("对方主动约你", s)
        self.assertIn("亲密感", s)

    def test_explain_negative(self):
        s = AffectionEngine.explain_change(-5.0, "quarrel", "信任")
        self.assertIn("吵了一架", s)

    def test_explain_replies_short(self):
        s = AffectionEngine.explain_change(-1.0, "other_replies_short", "情绪价值")
        self.assertIn("回复很短", s)

    def test_explain_fallback(self):
        s = AffectionEngine.explain_change(1.0, "unknown_event")
        self.assertIn("小变化", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
