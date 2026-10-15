# -*- coding: utf-8 -*-
"""
LoveAI v3.1 - TaskEngine 新增能力 + 多源联动单元测试

覆盖：
  - adjust_difficulty_by_mood：四种状态难度调整
  - _apply_repeat_penalty：重复话术/地点惩罚
  - check_missed_milestones：空库优雅返回
  - direction_from_compass：罗盘→四方向映射
  - generate_tasks_by_direction：四方向任务池
  - strategy_from_advisor：策略顾问→五策略映射
  - generate_recommended_tasks：多源加权聚合 top3
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402


class TestTaskEngineV31(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_task_v31_")
        self.db = Database(os.path.join(self.tmpdir, "t.db"))
        self.db.init_tables()
        self.affection = AffectionEngine(self.db)
        self.engine = TaskEngine(self.db, self.affection, guide_engine=None,
                                 twin=None, signals=None)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # ① 状态自适应难度
    # ------------------------------------------------------------------ #
    def test_mood_happy_lowers_difficulty(self):
        task = {"difficulty": "普通"}
        out = self.engine.adjust_difficulty_by_mood(task, {"proactivity": "热情"})
        self.assertEqual(out["difficulty"], "简单")
        self.assertIn("mood_adjustment", out)

    def test_mood_cold_raises_difficulty(self):
        task = {"difficulty": "普通"}
        out = self.engine.adjust_difficulty_by_mood(task, {"proactivity": "冷淡"})
        self.assertEqual(out["difficulty"], "困难")

    def test_mood_bad_raises_two_levels(self):
        task = {"difficulty": "简单"}
        out = self.engine.adjust_difficulty_by_mood(task, {"proactivity": "情绪差"})
        self.assertEqual(out["difficulty"], "困难")
        self.assertIn("安慰", out["mood_adjustment"]["note"])

    def test_mood_normal_unchanged(self):
        task = {"difficulty": "普通"}
        out = self.engine.adjust_difficulty_by_mood(task, {"proactivity": "正常"})
        self.assertEqual(out["difficulty"], "普通")

    # ------------------------------------------------------------------ #
    # ② 重复惩罚
    # ------------------------------------------------------------------ #
    def test_repeat_template_second_time(self):
        task = {"title": "邀约吃饭", "template": "邀约吃饭"}
        history = [{"title": "邀约吃饭", "template": "邀约吃饭"}]
        out = self.engine._apply_repeat_penalty(task, history)
        self.assertEqual(out["effectiveness"], 0.7)

    def test_repeat_template_third_time(self):
        task = {"title": "邀约吃饭", "template": "邀约吃饭"}
        history = [{"title": "邀约吃饭", "template": "邀约吃饭"},
                   {"title": "邀约吃饭", "template": "邀约吃饭"}]
        out = self.engine._apply_repeat_penalty(task, history)
        self.assertEqual(out["effectiveness"], 0.4)

    def test_repeat_location_second_time(self):
        task = {"title": "约会", "location": "星巴克"}
        history = [{"location": "星巴克"}]
        out = self.engine._apply_repeat_penalty(task, history)
        self.assertEqual(out["effectiveness"], 0.8)

    def test_no_repeat_full_effect(self):
        task = {"title": "新任务", "template": "新任务"}
        out = self.engine._apply_repeat_penalty(task, [])
        self.assertEqual(out["effectiveness"], 1.0)

    # ------------------------------------------------------------------ #
    # ③ 错过里程碑
    # ------------------------------------------------------------------ #
    def test_missed_milestones_empty_db(self):
        """空库不应抛异常，返回空列表。"""
        missed = self.engine.check_missed_milestones("nobody")
        self.assertIsInstance(missed, list)
        self.assertEqual(len(missed), 0)

    # ------------------------------------------------------------------ #
    # 联动①：方向罗盘
    # ------------------------------------------------------------------ #
    def test_direction_from_compass_withdraw(self):
        compass = {"gates": {"direction": {"reason": "含撤离导向：分手"}},
                   "drift": {"drift": False, "level": "none", "reasons": []},
                   "aligned": False, "alignment_score": 0.3}
        self.assertEqual(TaskEngine.direction_from_compass(compass), "后撤")

    def test_direction_from_compass_repair(self):
        compass = {"gates": {"direction": {"reason": "方向朝好结局"}},
                   "drift": {"drift": True, "level": "mid", "reasons": ["好感趋势下滑"]},
                   "aligned": False, "alignment_score": 0.4}
        self.assertEqual(TaskEngine.direction_from_compass(compass), "修复")

    def test_direction_from_compass_warmup(self):
        compass = {"gates": {"direction": {"reason": "方向朝好结局"}},
                   "drift": {"drift": False, "level": "none", "reasons": []},
                   "aligned": True, "alignment_score": 0.7}
        self.assertEqual(TaskEngine.direction_from_compass(compass), "升温")

    def test_direction_from_compass_maintain_default(self):
        compass = {"gates": {"direction": {"reason": "缺少推进"}},
                   "drift": {"drift": False, "level": "none", "reasons": []},
                   "aligned": False, "alignment_score": 0.5}
        self.assertEqual(TaskEngine.direction_from_compass(compass), "维稳")

    def test_generate_tasks_by_direction(self):
        for d in ("升温", "维稳", "修复", "后撤"):
            tasks = self.engine.generate_tasks_by_direction("p1", d)
            self.assertGreater(len(tasks), 0)
            self.assertTrue(all(t["direction"] == d for t in tasks))

    # ------------------------------------------------------------------ #
    # 联动②：策略顾问
    # ------------------------------------------------------------------ #
    def test_strategy_from_advisor_backoff(self):
        persona = {"risk_warning": {"overall_level": "高"}, "deception_risk": "高"}
        self.assertEqual(TaskEngine.strategy_from_advisor(persona, {}), "后撤")

    def test_strategy_from_advisor_repair(self):
        persona = {"emotion_pattern": {"pattern": "消极倾向"},
                   "attachment": {"primary_type": "安全型"}}
        self.assertEqual(TaskEngine.strategy_from_advisor(persona, {}), "修复")

    def test_strategy_from_advisor_pull(self):
        persona = {"attachment": {"primary_type": "恐惧型"}}
        self.assertEqual(TaskEngine.strategy_from_advisor(persona, {}), "拉扯")

    def test_strategy_from_advisor_warmup(self):
        persona = {"attachment": {"primary_type": "安全型"},
                   "relationship_stage": {"stage": "暧昧期"}}
        self.assertEqual(TaskEngine.strategy_from_advisor(persona, {}), "升温")

    def test_strategy_from_advisor_slow_default(self):
        persona = {"attachment": {"primary_type": "疏离型回避"}}
        self.assertEqual(TaskEngine.strategy_from_advisor(persona, {}), "慢热")

    # ------------------------------------------------------------------ #
    # 联动③：多源加权聚合
    # ------------------------------------------------------------------ #
    def test_generate_recommended_tasks_returns_top3(self):
        """无 strategy_advisor 时也能跑通，返回 top3。"""
        recs = self.engine.generate_recommended_tasks("p1", stage=2, top_n=3)
        self.assertEqual(len(recs), 3)
        for r in recs:
            self.assertIn("score", r)
            self.assertIn("matched_direction", r)
            self.assertIn("matched_strategy", r)
        # 按分数降序
        scores = [r["score"] for r in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_recommended_tasks_with_other_state(self):
        recs = self.engine.generate_recommended_tasks(
            "p1", stage=5, other_state={"proactivity": "冷淡"}, top_n=2)
        self.assertGreaterEqual(len(recs), 1)
        # 冷淡状态应触发难度上调说明
        self.assertIn("mood_adjustment", recs[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
