# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - R3.3-78 双轨情感指标（任务引擎侧）测试

反制线(counter)主指标=忠诚度(loyalty)；引导线(guide)主指标=好感度(affection)。
本文件只覆盖"任务引擎按路线区分任务推荐与奖励锚点"这部分：
  ① 路线生成：counter 任务带 route=="counter"、loyalty_reward 生效、affection 锚点下调；
     guide 路线行为与改动前一致。
  ② _complete 分发：counter 任务完成走 add_loyalty_bonus，guide/普通任务走 add_bonus。
  ③ extend_reward 接线：generate_recommended_tasks 的 expected_reward 按路线加权，
     含 loyalty 键（依赖并行模块 services.dual_track；缺失时自动 skip）。
  ④ 既有任务流程不回归（generate_main_tasks / check_completion）。

只读契约（并行子代理交付，本文件不重复实现）：
  AffectionEngine.get_route / add_loyalty_bonus / get_loyalty；
  services.dual_track.resolve_route / main_metric / extend_reward / ROUTE_WEIGHTS。
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
from services.task_engine import TaskEngine  # noqa: E402


class FakeAffection:
    """冻结契约的内存替身：实现 get_route/add_bonus/add_loyalty_bonus 与读取接口。"""

    def __init__(self, route: str = "guide") -> None:
        self.route = route
        self.bonuses: list = []       # 好感奖励 (person_id, bonus, reason)
        self.loyalty: list = []       # 忠诚奖励 (person_id, bonus, reason)
        self._scores: dict = {}

    # —— 冻结契约 —— #
    def get_route(self, person_id: str) -> str:
        return self.route

    def add_bonus(self, person_id, bonus, reason: str = "任务奖励") -> None:
        self.bonuses.append((person_id, float(bonus), reason))

    def add_loyalty_bonus(self, person_id, bonus, reason: str = "任务奖励") -> None:
        self.loyalty.append((person_id, float(bonus), reason))

    # —— 既有读取接口（_advance_loop / generate_recommended_tasks 用到） —— #
    def get_current(self, person_id):
        return self._scores.setdefault(
            person_id, {"a": 50.0, "b": 50.0, "c": 50.0, "d": 50.0, "e": 50.0})

    def get_total(self, scores):
        return sum(scores.values()) / max(1, len(scores))

    def get_total_with_bonus(self, person_id, scores):
        return self.get_total(scores)

    def get_stage(self, total):
        return 0 if total < 70 else 5


class TestDualTrackTaskEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_dualtrack_")
        self.db_path = os.path.join(self.tmpdir, "dualtrack.db")
        self.db = Database(self.db_path)
        self.db.init_tables()

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _engine(self, route: str = "guide") -> TaskEngine:
        return TaskEngine(self.db, FakeAffection(route=route), guide_engine=None)

    # ------------------------------------------------------------------ #
    # ① 路线生成 + 锚点分配
    # ------------------------------------------------------------------ #
    def test_anchor_for_guide_unchanged(self) -> None:
        """guide 路线：锚点与改动前一致（route=None，loyalty=0，好感=基准）。"""
        eng = self._engine("guide")
        self.assertEqual(eng._anchor_for("guide", "完善对方基础档案", 5.0),
                         (None, 5.0, 0.0))

    def test_anchor_for_counter_title_loyalty_anchor(self) -> None:
        """counter 路线 + 反制类标题：忠诚主锚点，好感锚点下调到 1.0。"""
        eng = self._engine("counter")
        route_tag, aff, loy = eng._anchor_for("counter", "记录煤气灯事实清单", 5.0)
        self.assertEqual(route_tag, "counter")
        self.assertEqual(loy, 5.0)
        self.assertEqual(aff, 1.0)  # affection 置小

    def test_anchor_for_counter_normal_title(self) -> None:
        """counter 路线普通任务：带 route 标签，保留好感 + 少量忠诚。"""
        eng = self._engine("counter")
        route_tag, aff, loy = eng._anchor_for("counter", "完善对方基础档案", 5.0)
        self.assertEqual(route_tag, "counter")
        self.assertEqual(aff, 5.0)
        self.assertEqual(loy, 2.0)

    def test_generate_main_tasks_guide_no_loyalty(self) -> None:
        """guide 路线生成的任务：route 为空、loyalty_reward=0、好感奖励=5.0（不回归）。"""
        eng = self._engine("guide")
        created = eng.generate_main_tasks("p1", stage=0)
        self.assertEqual(len(created), 3)
        rows = self.db.fetch_all(
            "SELECT route, loyalty_reward, affection_reward FROM tasks "
            "WHERE person_id=:pid", {"pid": "p1"})
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertIn(r["route"], (None, ""))
            self.assertEqual(float(r["loyalty_reward"] or 0), 0.0)
            self.assertEqual(float(r["affection_reward"]), 5.0)

    def test_generate_main_tasks_counter_tagged(self) -> None:
        """counter 路线生成的任务：带 route='counter' 且 loyalty_reward>0。"""
        eng = self._engine("counter")
        created = eng.generate_main_tasks("p1", stage=0)
        self.assertEqual(len(created), 3)
        rows = self.db.fetch_all(
            "SELECT route, loyalty_reward, affection_reward FROM tasks "
            "WHERE person_id=:pid", {"pid": "p1"})
        for r in rows:
            self.assertEqual(r["route"], "counter")
            self.assertGreater(float(r["loyalty_reward"] or 0), 0.0)

    # ------------------------------------------------------------------ #
    # ② _complete 奖励分发
    # ------------------------------------------------------------------ #
    def _row(self, tid: str) -> dict:
        return self.db.fetch_one("SELECT * FROM tasks WHERE id=:tid", {"tid": tid})

    def test_complete_counter_task_routes_to_loyalty(self) -> None:
        """counter 任务完成：add_loyalty_bonus 涨忠诚；好感只微变（affection=1.0）。"""
        eng = self._engine("counter")
        aff = eng.affection  # type: FakeAffection
        t = eng._create("p1", "记录煤气灯事实清单", "main",
                        affection_reward=1.0, route="counter", loyalty_reward=5.0)
        row = self._row(t["id"])
        eng._complete(row, "测试完成")

        # 忠诚奖励：+5.0
        self.assertEqual(len(aff.loyalty), 1)
        self.assertEqual(aff.loyalty[0][1], 5.0)
        self.assertEqual(aff.loyalty[0][0], "p1")
        # 好感奖励：仅 1.0（微变），且确实走了 add_bonus
        self.assertTrue(any(b[1] == 1.0 for b in aff.bonuses))

    def test_complete_guide_task_routes_to_affection(self) -> None:
        """guide/普通任务完成：只走 add_bonus，不调 add_loyalty_bonus。"""
        eng = self._engine("guide")
        aff = eng.affection  # type: FakeAffection
        t = eng._create("p1", "随便一个正向任务", "main", affection_reward=3.0)
        row = self._row(t["id"])
        eng._complete(row, "测试完成")

        self.assertEqual(aff.loyalty, [])           # 没有忠诚奖励
        self.assertTrue(any(b[1] == 3.0 for b in aff.bonuses))  # 好感 +3.0

    # ------------------------------------------------------------------ #
    # ③ extend_reward 接线（依赖并行模块 services.dual_track；缺失自动 skip）
    # ------------------------------------------------------------------ #
    def test_extend_reward_route_wired(self) -> None:
        """_extend_reward_route 在 dual_track 就绪时返回含 loyalty 键的七维向量。"""
        pytest = __import__("pytest")
        pytest.importorskip("services.dual_track")
        from services import dual_track  # noqa: F401

        eng = self._engine("counter")
        out = eng._extend_reward_route({"affection": 0.4, "trust": 0.1}, "counter")
        self.assertIn("loyalty", out)

    def test_generate_recommended_tasks_tags_route(self) -> None:
        """推荐结果无论是否走 planner，都带 route 标签；dual_track 就绪时 expected_reward 含 loyalty。"""
        pytest = __import__("pytest")
        eng = self._engine("counter")
        recs = eng.generate_recommended_tasks("p1", stage=2, top_n=3)
        self.assertGreater(len(recs), 0)
        for c in recs:
            self.assertEqual(c.get("route"), "counter")
        # dual_track 缺失时该断言自动跳过；就绪时验证七维向量
        try:
            from services import dual_track  # noqa: F401
        except Exception:
            self.skipTest("services.dual_track 并行模块尚未落地")
        for c in recs:
            self.assertIn("loyalty", c.get("expected_reward", {}))

    # ------------------------------------------------------------------ #
    # ④ 既有流程不回归
    # ------------------------------------------------------------------ #
    def test_generate_main_tasks_stage0_still_three(self) -> None:
        """既有：stage=0 仍生成 3 个主线任务。"""
        eng = self._engine("guide")
        tasks = eng.generate_main_tasks("p1", stage=0)
        self.assertEqual(len(tasks), 3)
        titles = [t["title"] for t in tasks]
        self.assertIn("完善对方基础档案", titles)

    def test_check_completion_smoke(self) -> None:
        """既有：限时任务触发 + check_completion 推进仍可用。"""
        eng = self._engine("guide")
        lim = eng.generate_limited_task("p1", "周末一起吃饭吗")
        self.assertIsNotNone(lim)
        tid = lim["id"]
        eng.check_completion("p1", "我对这家餐厅很感兴趣")
        prog = eng.get_task_progress(tid)
        self.assertEqual(prog["current_step"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
