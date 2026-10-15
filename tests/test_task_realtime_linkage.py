# -*- coding: utf-8 -*-
"""任务系统实时联动补链测试：自定义任务去重 / 爽约区分 / 状态调速。"""
import os, sys, tempfile, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from models.database import Database
from services.affection_engine import AffectionEngine
from services.task_engine import TaskEngine


class TestTaskLinkage(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_tasklink_")
        self.db = Database(os.path.join(self.tmpdir, "t.db"))
        self.db.init_tables()
        self.aff = AffectionEngine(self.db)
        self.te = TaskEngine(self.db, self.aff, guide_engine=None,
                             twin=None, signals=None, commitment_tracker=None)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_add_custom_task_dedup(self):
        self.te.add_custom_task("pX", "约对方见面", source="test")
        again = self.te.add_custom_task("pX", "约对方见面", source="test")
        # 同一活跃任务不重复创建 → 第二次返回 None
        self.assertIsNone(again)
        active = self.te.get_active_tasks("pX")
        self.assertEqual(len(active), 1)

    def test_appointment_delay_vs_cold(self):
        # 有事耽误：有具体理由+主动补约 → delay
        r1 = self.te.check_appointment_break("pY", "抱歉今天临时加班，去不了，下次我请你吃饭补")
        self.assertEqual(r1["type"], "delay")
        # 无理由敷衍 → cold
        r2 = self.te.check_appointment_break("pZ", "今天去不了，到时候看再说吧")
        self.assertEqual(r2["type"], "cold")

    def test_adjust_pauses_push_when_avoidant(self):
        # 造一个进行中的推进型任务
        self.te._create("pW", "约对方见面确认关系", task_type="main")
        row = self.db.fetch_one("SELECT id FROM tasks WHERE title=:t", {"t": "约对方见面确认关系"})
        self.db.execute("UPDATE tasks SET status='in_progress' WHERE id=:i", {"i": row["id"]})
        # 对方回避激活 → 暂停推进任务、发空间任务
        adj = self.te.adjust_tasks_for_state("pW", {
            "emotion": "冷漠/回避", "attachment_activation": "回避激活（此刻在疏离）",
            "trend": "近期转冷/越来越冷淡"})
        self.assertIsNotNone(adj)
        self.assertEqual(adj["adjusted"], "downshift")
        # 原推进任务被暂停
        still_active = self.db.fetch_one("SELECT status FROM tasks WHERE id=:i", {"i": row["id"]})
        self.assertEqual(still_active["status"], "paused")


if __name__ == "__main__":
    unittest.main(verbosity=2)
