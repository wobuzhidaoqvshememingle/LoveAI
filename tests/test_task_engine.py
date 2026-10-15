# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - TaskEngine 单元测试

覆盖：
  - guide_engine=None 时使用默认攻略方向
  - generate_main_tasks stage=0 创建 3 个任务
  - generate_limited_task 邀约 / 冲突关键词命中，普通消息返回 None
  - _generate_steps 邀约返回 5 步
  - _is_all_steps_done 无分步任务返回 True
  - check_completion 关键词命中推进 / 全部完成后自动收尾
  - get_active_tasks / handle_failed / _advance_loop 接棒
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
from services.task_engine import TaskEngine, _DEFAULT_DIRECTION  # noqa: E402


class TestTaskEngine(unittest.TestCase):
    """TaskEngine 测试类（临时 SQLite + 真实 AffectionEngine）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_task_")
        self.db_path = os.path.join(self.tmpdir, "test_task.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.affection = AffectionEngine(self.db)
        self.engine = TaskEngine(
            self.db, self.affection, guide_engine=None,
            twin=None, signals=None,
        )

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化 / 默认方向
    # ------------------------------------------------------------------ #
    def test_init_default_direction(self) -> None:
        """guide_engine=None 时 _build_direction 返回默认文案。"""
        task = self.engine._create("p1", "测试任务", "main")
        self.assertEqual(task["guide_direction"], _DEFAULT_DIRECTION)

    def test_guide_engine_custom_direction(self) -> None:
        """guide_engine 提供 generate_task_direction 时使用其返回值。"""
        guide = MagicMock()
        guide.generate_task_direction.return_value = "主动制造相处机会"
        eng = TaskEngine(self.db, self.affection, guide_engine=guide)
        task = eng._create("p1", "任务X", "main")
        self.assertEqual(task["guide_direction"], "主动制造相处机会")

    # ------------------------------------------------------------------ #
    # 主线任务
    # ------------------------------------------------------------------ #
    def test_generate_main_tasks_stage0(self) -> None:
        """stage=0 应创建 3 个 main 任务。"""
        tasks = self.engine.generate_main_tasks("p1", stage=0)
        self.assertEqual(len(tasks), 3)
        titles = [t["title"] for t in tasks]
        self.assertIn("完善对方基础档案", titles)
        for t in tasks:
            self.assertIn("id", t)

    # ------------------------------------------------------------------ #
    # 限时任务触发
    # ------------------------------------------------------------------ #
    def test_limited_task_invitation(self) -> None:
        """邀约关键词应触发限时任务。"""
        task = self.engine.generate_limited_task("p1", "周末一起吃饭吗")
        self.assertIsNotNone(task)
        self.assertTrue(task["task_type"].startswith("limited:"))
        self.assertIn("deadline", task)

    def test_limited_task_conflict(self) -> None:
        """冲突关键词（别烦/不想聊）应触发冲突修复任务。"""
        task = self.engine.generate_limited_task("p1", "我不想聊了，别烦我")
        self.assertIsNotNone(task)
        self.assertIn("冲突修复", task["task_type"])

    def test_limited_task_normal_message(self) -> None:
        """普通闲聊消息不应触发限时任务。"""
        task = self.engine.generate_limited_task("p1", "今天天气真好")
        self.assertIsNone(task)

    # ------------------------------------------------------------------ #
    # 分步拆分
    # ------------------------------------------------------------------ #
    def test_generate_steps_invitation(self) -> None:
        """邀约推进分类应拆出 5 步。"""
        steps = self.engine._generate_steps("抓住邀约信号", "邀约推进")
        self.assertEqual(len(steps), 5)
        self.assertIn("name", steps[0])
        self.assertIn("complete_keywords", steps[0])

    def test_generate_steps_unknown_category(self) -> None:
        """未知分类应回退为单步。"""
        steps = self.engine._generate_steps("某任务", "不存在的分类")
        self.assertEqual(len(steps), 1)

    # ------------------------------------------------------------------ #
    # 分步完成判定
    # ------------------------------------------------------------------ #
    def test_is_all_steps_done_no_steps(self) -> None:
        """无分步任务应视为已完成。"""
        task = self.engine._create("p1", "无分步任务", "main")
        self.assertTrue(self.engine._is_all_steps_done(task["id"]))

    def test_check_completion_advances_step(self) -> None:
        """命中当前步关键词后 current_step 应 +1。"""
        self.engine.generate_limited_task("p1", "周末一起吃饭吗")
        before = self.engine.get_active_tasks("p1")
        tid = before[0]["id"]
        prog_before = self.engine.get_task_progress(tid)
        self.engine.check_completion("p1", "我对这家餐厅很感兴趣")
        prog_after = self.engine.get_task_progress(tid)
        self.assertEqual(prog_after["current_step"], prog_before["current_step"] + 1)

    def test_check_completion_full_completes(self) -> None:
        """依次喂入 5 步关键词后任务自动完成并接棒下一任务。"""
        self.engine.generate_limited_task("p1", "周末一起吃饭吗")
        task = self.engine.get_active_tasks("p1")[0]
        tid = task["id"]
        # 逐步喂关键词，命中每一步
        for msg in ["我很感兴趣", "时间地点方案都定了",
                    "约她出来有空吗", "好啊可以行", "定了周六说定"]:
            self.engine.check_completion("p1", msg)
        row = self.db.fetch_one(
            "SELECT status FROM tasks WHERE id=:tid", {"tid": tid})
        self.assertEqual(row["status"], "completed")
        # _advance_loop 应接棒创建一个新任务（日常）
        active = self.engine.get_active_tasks("p1")
        self.assertTrue(any("今天主动聊一次天" in t["title"] for t in active))

    # ------------------------------------------------------------------ #
    # 查询 / 失败 / 接棒
    # ------------------------------------------------------------------ #
    def test_get_active_tasks(self) -> None:
        """get_active_tasks 应返回列表且包含新建任务。"""
        self.engine._create("p1", "活跃任务", "main")
        active = self.engine.get_active_tasks("p1")
        self.assertIsInstance(active, list)
        self.assertGreaterEqual(len(active), 1)

    def test_handle_failed_creates_alternative(self) -> None:
        """任务失败后原任务标记 failed 并创建替代任务。"""
        task = self.engine._create("p1", "要失败的任务", "main")
        self.engine.handle_failed({"id": task["id"], "person_id": "p1",
                                   "title": "要失败的任务"})
        row = self.db.fetch_one(
            "SELECT status FROM tasks WHERE id=:tid", {"tid": task["id"]})
        self.assertEqual(row["status"], "failed")
        alts = self.db.fetch_all(
            "SELECT * FROM tasks WHERE task_type='alternative' "
            "AND person_id=:pid", {"pid": "p1"})
        self.assertEqual(len(alts), 1)
        self.assertTrue(alts[0]["title"].startswith("[替代]"))

    def test_advance_loop_creates_next(self) -> None:
        """_advance_loop 在好感度<70 时创建日常接棒任务。"""
        self.engine._advance_loop("p1")
        active = self.engine.get_active_tasks("p1")
        self.assertTrue(any("今天主动聊一次天" in t["title"] for t in active))


if __name__ == "__main__":
    unittest.main(verbosity=2)
