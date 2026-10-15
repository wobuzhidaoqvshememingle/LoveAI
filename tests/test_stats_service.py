# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - StatsService 单元测试

覆盖：
  - 空库下各统计方法返回完整默认字段
  - 种子数据：消息按天统计 / 好感度趋势 / 任务完成率 / 多对象对比排序
  - 周报结构完整
"""
import os
import sys
import shutil
import tempfile
import unittest
from datetime import date, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.stats_service import StatsService  # noqa: E402


class TestStatsService(unittest.TestCase):
    """StatsService 测试类（临时 SQLite 真实库 + 种子数据）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_stats_")
        self.db = Database(os.path.join(self.tmpdir, "stats.db"))
        self.db.init_tables()
        self.stats = StatsService(self.db)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 种子辅助
    # ------------------------------------------------------------------ #
    def _seed_person(self, pid: str, nickname: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO persons (id, nickname) "
            "VALUES (:id, :n)", {"id": pid, "n": nickname})

    # ------------------------------------------------------------------ #
    # 空库默认值
    # ------------------------------------------------------------------ #
    def test_init_no_crash(self) -> None:
        self.assertIs(self.stats.db, self.db)

    def test_dashboard_empty_defaults(self) -> None:
        """空库仪表盘应返回完整字段且默认为 0。"""
        d = self.stats.get_dashboard_stats()
        for key in ("message_count", "task_completed", "task_active",
                    "medals_unlocked", "login_days", "analysis_count"):
            self.assertEqual(d[key], 0, key)
        self.assertEqual(d["affection_current"], 0.0)
        self.assertEqual(d["response_time_avg"], 0.0)
        self.assertEqual(d["affection_trend"], [])

    def test_message_stats_empty(self) -> None:
        """空库消息统计 daily 为空列表。"""
        r = self.stats.get_message_stats("p_nobody")
        self.assertEqual(r["daily"], [])
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["by_direction"], {"sent": 0, "received": 0})
        self.assertIn("凌晨", r["by_hour"])

    def test_affection_stats_empty(self) -> None:
        """空库好感度统计返回默认结构。"""
        r = self.stats.get_affection_stats("p_nobody")
        self.assertEqual(r["daily"], [])
        self.assertEqual(r["current"], 0.0)
        self.assertEqual(r["trend"], "stable")

    def test_task_stats_empty(self) -> None:
        """空库任务统计 by_status 五个状态键齐全。"""
        r = self.stats.get_task_stats()
        for st in ("pending", "in_progress", "completed", "failed", "expired"):
            self.assertEqual(r["by_status"][st], 0, st)
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["completion_rate"], 0.0)

    def test_usage_stats_empty(self) -> None:
        """空库使用统计功能键齐全。"""
        r = self.stats.get_usage_stats()
        self.assertEqual(r["daily_active"], [])
        for feat in ("分析", "财务", "攻略", "分身预测", "记忆", "聊天"):
            self.assertIn(feat, r["feature_usage"])
        # 全为 0 时 max() 取首个键，仍应为已知功能之一
        self.assertIn(r["most_used_feature"],
                      ("分析", "财务", "攻略", "分身预测", "记忆", "聊天"))

    def test_relation_comparison_empty(self) -> None:
        """空库多对象对比返回空列表。"""
        self.assertEqual(self.stats.get_relation_comparison(), [])

    def test_weekly_report_empty_structure(self) -> None:
        """空库周报应返回完整结构。"""
        r = self.stats.get_weekly_report()
        self.assertIn("~", r["week_range"])
        self.assertIn("metrics", r)
        self.assertIn("vs_last_week", r)
        self.assertIsInstance(r["highlights"], list)
        self.assertIsInstance(r["suggestions"], list)

    # ------------------------------------------------------------------ #
    # 种子数据
    # ------------------------------------------------------------------ #
    def test_message_stats_by_day(self) -> None:
        """消息按天/方向统计正确。"""
        self._seed_person("p1", "小红")
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        rows = [
            ("p1", "sent", f"{today} 10:00:00"),
            ("p1", "received", f"{today} 11:00:00"),
            ("p1", "sent", f"{yesterday} 09:00:00"),
        ]
        for pid, direction, ts in rows:
            self.db.execute(
                "INSERT INTO chat_messages (person_id, direction, content, created_at) "
                "VALUES (:pid, :dir, 'c', :ts)",
                {"pid": pid, "dir": direction, "ts": ts})

        r = self.stats.get_message_stats("p1", days=30)
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["by_direction"]["sent"], 2)
        self.assertEqual(r["by_direction"]["received"], 1)
        self.assertEqual(len(r["daily"]), 2)

    def test_affection_trend_calculated(self) -> None:
        """好感度趋势取快照，当前值以 current_affection 为准。"""
        self._seed_person("p1", "小红")
        today = date.today()
        for i, score in enumerate((50.0, 55.0)):
            d = (today - timedelta(days=i)).isoformat()
            self.db.execute(
                "INSERT INTO affection_snapshots (person_id, date, total_score) "
                "VALUES (:pid, :d, :s)", {"pid": "p1", "d": d, "s": score})
        self.db.execute(
            "INSERT INTO current_affection (person_id, total_score) "
            "VALUES (:pid, :s)", {"pid": "p1", "s": 60.0})

        r = self.stats.get_affection_stats("p1", days=30)
        self.assertEqual(r["current"], 60.0)
        self.assertEqual(r["highest"], 55.0)
        self.assertEqual(r["lowest"], 50.0)
        self.assertEqual(len(r["daily"]), 2)

    def test_task_completion_rate(self) -> None:
        """任务完成率 = completed/total。"""
        self._seed_person("p1", "小红")
        for i in range(4):
            self.db.execute(
                "INSERT INTO tasks (id, person_id, title, task_type, status) "
                "VALUES (:id, :pid, :t, 'main', 'completed')",
                {"id": f"tc{i}", "pid": "p1", "t": f"任务{i}"})
        self.db.execute(
            "INSERT INTO tasks (id, person_id, title, task_type, status) "
            "VALUES ('tf1', 'p1', '失败任务', 'main', 'failed')")

        r = self.stats.get_task_stats("p1")
        self.assertEqual(r["total"], 5)
        self.assertEqual(r["by_status"]["completed"], 4)
        self.assertEqual(r["by_status"]["failed"], 1)
        self.assertEqual(r["completion_rate"], 80.0)
        self.assertEqual(r["failed_rate"], 20.0)

    def test_relation_comparison_sorted(self) -> None:
        """多对象对比按好感度降序。"""
        self._seed_person("pA", "高个")
        self._seed_person("pB", "矮个")
        self.db.execute(
            "INSERT INTO current_affection (person_id, total_score) "
            "VALUES ('pB', 50.0)")
        self.db.execute(
            "INSERT INTO current_affection (person_id, total_score) "
            "VALUES ('pA', 80.0)")

        rows = self.stats.get_relation_comparison()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["person_id"], "pA")
        self.assertEqual(rows[0]["affection_total"], 80.0)
        self.assertEqual(rows[1]["person_id"], "pB")

    def test_dashboard_with_person(self) -> None:
        """按对象取仪表盘，好感度应反映 current_affection。"""
        self._seed_person("p1", "小红")
        self.db.execute(
            "INSERT INTO current_affection (person_id, total_score) "
            "VALUES ('p1', 72.5)")
        d = self.stats.get_dashboard_stats("p1")
        self.assertEqual(d["affection_current"], 72.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
