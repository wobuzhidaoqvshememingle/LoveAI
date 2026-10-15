# -*- coding: utf-8 -*-
"""R3.3-80 B 类：里程碑事件链 单元/集成测试（headless，真实 sqlite + TaskEngine）。

覆盖：
  - MILESTONE_TASKS 模板齐全（>=4 类核心里程碑，含 difficulty/steps/reward/stage_gate）
  - MilestoneChain.tasks_for_stage / next_milestones 查询正确
  - create_milestone_tasks 真实落库（查 tasks 表能看到）
  - 同标题重复调用不重复创建（add_custom_task 自带去重）
  - plan_breakup_cycle_intervention：cycle_count>=2 返回干预任务；<2 返回空
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
from services.task_engine import TaskEngine, TASK_DIFFICULTY_XP  # noqa: E402
from services.milestone_task_templates import (  # noqa: E402
    MILESTONE_TASKS,
    MilestoneChain,
    STAGE_ORDER,
)

PID = "t_milestone_1"

CORE_MILESTONES = ["求婚", "订婚", "领证", "婚礼", "买房", "备孕"]
COMM_MILESTONES = ["彩礼沟通", "嫁妆沟通", "见家长流程"]


class MilestoneHarness:
    """搭一套真实 TaskEngine + 临时 sqlite，供用例复用。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="loveai_milestone_test_")
        self.db = Database(os.path.join(self.tmp, "t.db"))
        self.db.init_tables()
        self.db.execute("INSERT INTO persons(id, nickname) VALUES(:p,:n)",
                        {"p": PID, "n": "测试对象"})
        self.affection = AffectionEngine(self.db)
        self.tasks = TaskEngine(self.db, self.affection,
                                guide_engine=None, signals=None)
        low = {d: 25.0 for d in self.affection.WEIGHTS}
        self.db.execute(
            "INSERT OR REPLACE INTO current_affection(person_id, scores_json) "
            "VALUES(:p,:s)", {"p": PID, "s": json.dumps(low)})

    def active_titles(self) -> list[str]:
        rows = self.db.fetch_all(
            "SELECT title FROM tasks WHERE person_id=:p AND "
            "status IN ('pending','in_progress')", {"p": PID})
        return [r["title"] for r in rows]

    def cleanup(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestMilestoneTemplateSchema(unittest.TestCase):
    """模板表结构与字段齐全性。"""

    def test_all_core_milestones_present(self):
        for name in CORE_MILESTONES:
            self.assertIn(name, MILESTONE_TASKS, f"缺少核心里程碑: {name}")

    def test_all_comm_milestones_present(self):
        for name in COMM_MILESTONES:
            self.assertIn(name, MILESTONE_TASKS, f"缺少沟通里程碑: {name}")

    def test_each_template_has_required_fields(self):
        for name, tpl in MILESTONE_TASKS.items():
            self.assertIn("stage_gate", tpl, f"{name} 缺 stage_gate")
            self.assertIsInstance(tpl["stage_gate"], int)
            self.assertIn(tpl["difficulty"], TASK_DIFFICULTY_XP,
                          f"{name} difficulty 非法: {tpl['difficulty']}")
            self.assertGreaterEqual(len(tpl["steps"]), 3,
                                    f"{name} steps 应 >=3 步")
            self.assertLessEqual(len(tpl["steps"]), 5,
                                 f"{name} steps 应 <=5 步")
            self.assertIsInstance(tpl["reward"], (int, float))
            self.assertGreater(tpl["reward"], 0)
            self.assertTrue(tpl["script"], f"{name} script 为空")

    def test_stage_order_matches_11_levels(self):
        self.assertEqual(len(STAGE_ORDER), 11)
        self.assertEqual(STAGE_ORDER[0], "相识")
        self.assertEqual(STAGE_ORDER[-1], "终身伴侣")


class TestMilestoneChainQuery(unittest.TestCase):
    """tasks_for_stage / next_milestones 查询逻辑。"""

    @classmethod
    def setUpClass(cls):
        cls.chain = MilestoneChain()

    def test_tasks_for_stage_early_stage_returns_empty(self):
        # 相识阶段不应解锁任何里程碑
        self.assertEqual(self.chain.tasks_for_stage("相识"), [])

    def test_tasks_for_stage_object_stage_unlocks_communication(self):
        out = self.chain.tasks_for_stage("对象")
        names = [t["name"] for t in out]
        # 对象阶段应解锁 3 个沟通类
        for n in COMM_MILESTONES:
            self.assertIn(n, names, f"对象阶段未解锁沟通里程碑: {n}")
        # 但不应解锁 备孕 / 婚礼 这种后期
        self.assertNotIn("备孕", names)

    def test_next_milestones_from_object(self):
        nxt = self.chain.next_milestones("对象")
        # 对象(idx=5) 的下一级是 未婚夫妻(idx=6)，应解锁 求婚/订婚
        names = [t["name"] for t in nxt]
        self.assertIn("求婚", names)
        self.assertIn("订婚", names)

    def test_unknown_stage_returns_empty(self):
        self.assertEqual(self.chain.tasks_for_stage("不存在的阶段"), [])
        self.assertEqual(self.chain.next_milestones("不存在的阶段"), [])


class TestCreateMilestoneTasksRealDB(unittest.TestCase):
    """create_milestone_tasks 真实落库 + 去重。"""

    @classmethod
    def setUpClass(cls):
        cls.h = MilestoneHarness()
        cls.chain = MilestoneChain()

    @classmethod
    def tearDownClass(cls):
        cls.h.cleanup()

    def test_01_create_real_tasks_into_db(self):
        created = self.chain.create_milestone_tasks(
            self.h.tasks, PID, "对象")
        # 对象阶段有 3 个沟通里程碑
        self.assertGreaterEqual(len(created), 3,
                                f"创建任务数不足: {len(created)}")
        titles = [t["title"] for t in created]
        print("\n[create_milestone_tasks 落库任务标题]")
        for t in titles:
            print("  -", t)
        for n in COMM_MILESTONES:
            self.assertIn(f"[里程碑]{n}", titles)
        # 真实查库能看到
        db_titles = self.h.active_titles()
        for n in COMM_MILESTONES:
            self.assertIn(f"[里程碑]{n}", db_titles,
                          f"tasks 表未找到: {n}")

    def test_02_duplicate_call_dedup(self):
        before = len(self.h.active_titles())
        created2 = self.chain.create_milestone_tasks(
            self.h.tasks, PID, "对象")
        after = len(self.h.active_titles())
        self.assertEqual(before, after,
                         f"重复调用未去重: before={before} after={after}")
        self.assertEqual(created2, [],
                         f"重复调用应返回空列表: {created2}")

    def test_03_later_stage_adds_new_milestones(self):
        # 升到 未婚夫妻，应新增 求婚/订婚
        created = self.chain.create_milestone_tasks(
            self.h.tasks, PID, "未婚夫妻")
        titles = [t["title"] for t in created]
        self.assertIn("[里程碑]求婚", titles)
        self.assertIn("[里程碑]订婚", titles)
        # 已存在的 3 个沟通里程碑不应重复创建
        for n in COMM_MILESTONES:
            self.assertNotIn(f"[里程碑]{n}", titles)


class TestBreakupCycleIntervention(unittest.TestCase):
    """D 类：复合循环干预任务规划。"""

    @classmethod
    def setUpClass(cls):
        cls.chain = MilestoneChain()

    def test_low_cycle_returns_empty(self):
        out = self.chain.plan_breakup_cycle_intervention(
            {"cycle_count": 1, "entangled": False, "ex_new_partner": False})
        self.assertEqual(out, [])

    def test_cycle_ge_2_returns_tasks(self):
        out = self.chain.plan_breakup_cycle_intervention(
            {"cycle_count": 2, "entangled": False, "ex_new_partner": False})
        self.assertGreaterEqual(len(out), 3)
        titles = [t["title"] for t in out]
        self.assertTrue(any("分手冲动" in t for t in titles),
                        f"缺基础干预任务: {titles}")
        self.assertTrue(any("72 小时" in t or "72小时" in t for t in titles),
                        f"缺冷静期任务: {titles}")
        # 每条都有 steps/difficulty/reward
        for t in out:
            self.assertIn("steps", t)
            self.assertGreaterEqual(len(t["steps"]), 2)
            self.assertIn("difficulty", t)
            self.assertIn("reward", t)

    def test_entangled_adds_extra_task(self):
        base = self.chain.plan_breakup_cycle_intervention(
            {"cycle_count": 3, "entangled": False, "ex_new_partner": False})
        ent = self.chain.plan_breakup_cycle_intervention(
            {"cycle_count": 3, "entangled": True, "ex_new_partner": False})
        self.assertGreater(len(ent), len(base))
        ent_titles = [t["title"] for t in ent]
        self.assertTrue(any("纠缠" in t for t in ent_titles))

    def test_ex_new_partner_adds_extra_task(self):
        out = self.chain.plan_breakup_cycle_intervention(
            {"cycle_count": 2, "entangled": False, "ex_new_partner": True})
        titles = [t["title"] for t in out]
        self.assertTrue(any("视奸" in t or "新伴侣" in t for t in titles),
                        f"前任新伴侣场景未追加任务: {titles}")


if __name__ == "__main__":
    unittest.main()
