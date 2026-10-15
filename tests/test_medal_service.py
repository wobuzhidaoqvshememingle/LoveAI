# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - MedalService 单元测试

覆盖：
  - 表自动创建 / 默认 25 勋章初始化
  - get_all_medals 返回 25 条
  - check_and_unlock 好感度 70 解锁确认关系及前置勋章
  - get_unlocked_medals / get_medal_stats / get_hero_wall_data
  - award_medal 手动授予与重复检测
  - 按分类统计 / 未知事件类型
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
from services.medal_service import MedalService  # noqa: E402


class TestMedalService(unittest.TestCase):
    """MedalService 测试类（临时 SQLite）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_medal_")
        self.db_path = os.path.join(self.tmpdir, "test_medal.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.service = MedalService(self.db, signals=None)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def test_init_creates_tables(self) -> None:
        """medals 与 user_medals 表应存在。"""
        rows = self.db.fetch_all("SELECT * FROM medals")
        self.assertGreater(len(rows), 0)
        rows2 = self.db.fetch_all("SELECT * FROM user_medals")
        self.assertIsInstance(rows2, list)

    def test_init_default_medals_count(self) -> None:
        """应初始化 25 个默认勋章。"""
        self.assertEqual(len(self.service.medals_cache), 25)

    def test_get_all_medals(self) -> None:
        """get_all_medals 应返回 25 条且带 unlocked 标记。"""
        medals = self.service.get_all_medals("p1")
        self.assertEqual(len(medals), 25)
        for m in medals:
            self.assertIn("unlocked", m)

    # ------------------------------------------------------------------ #
    # 好感度解锁
    # ------------------------------------------------------------------ #
    def _set_affection(self, pid: str, score: float) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO current_affection (person_id, total_score) "
            "VALUES (:pid, :ts)",
            {"pid": pid, "ts": score},
        )

    def test_check_unlock_affection_70(self) -> None:
        """好感度 70 应解锁'确认关系'(medal_009)。"""
        self._set_affection("p1", 70.0)
        unlocked = self.service.check_and_unlock("p1", "affection", 70.0)
        ids = [m["id"] for m in unlocked]
        self.assertIn("medal_009", ids)

    def test_check_unlock_auto_prerequisites(self) -> None:
        """好感度 70 应同时解锁 55/60/65/70 四档前置勋章。"""
        self._set_affection("p1", 70.0)
        unlocked = self.service.check_and_unlock("p1", "affection", 70.0)
        ids = {m["id"] for m in unlocked}
        for mid in ("medal_006", "medal_007", "medal_008", "medal_009"):
            self.assertIn(mid, ids)

    def test_get_unlocked_medals(self) -> None:
        """解锁后 get_unlocked_medals 应返回已解锁列表。"""
        self._set_affection("p1", 70.0)
        self.service.check_and_unlock("p1", "affection", 70.0)
        unlocked = self.service.get_unlocked_medals("p1")
        self.assertGreaterEqual(len(unlocked), 4)
        self.assertIn("name", unlocked[0])

    def test_get_medal_stats(self) -> None:
        """统计应返回 total/unlocked/by_category 等字段。"""
        self._set_affection("p1", 70.0)
        self.service.check_and_unlock("p1", "affection", 70.0)
        stats = self.service.get_medal_stats("p1")
        self.assertEqual(stats["total"], 25)
        self.assertGreaterEqual(stats["unlocked"], 1)
        self.assertIn("by_category", stats)
        self.assertIn("by_rarity", stats)
        self.assertGreaterEqual(stats["completion_rate"], 0.0)

    def test_category_stats_correct(self) -> None:
        """解锁的 4 枚关系类勋章应计入 by_category['关系']。"""
        self._set_affection("p1", 70.0)
        self.service.check_and_unlock("p1", "affection", 70.0)
        stats = self.service.get_medal_stats("p1")
        self.assertGreaterEqual(stats["by_category"].get("关系", 0), 4)

    # ------------------------------------------------------------------ #
    # 英雄墙
    # ------------------------------------------------------------------ #
    def test_get_hero_wall_data(self) -> None:
        """英雄墙应返回完整结构。"""
        self._set_affection("p1", 65.0)
        self.service.check_and_unlock("p1", "affection", 65.0)
        wall = self.service.get_hero_wall_data("p1")
        self.assertEqual(wall["person_id"], "p1")
        self.assertEqual(wall["total_medals"], 25)
        self.assertIn("unlocked_medals", wall)
        self.assertIn("achievements", wall)
        self.assertIn("stats", wall)
        self.assertEqual(len(wall["achievements"]), 4)

    # ------------------------------------------------------------------ #
    # 手动授予
    # ------------------------------------------------------------------ #
    def test_award_medal_manual(self) -> None:
        """手动授予新勋章应返回 True。"""
        ok = self.service.award_medal("p1", "medal_001")
        self.assertTrue(ok)

    def test_award_medal_duplicate(self) -> None:
        """重复授予同一勋章应返回 False。"""
        self.service.award_medal("p1", "medal_001")
        again = self.service.award_medal("p1", "medal_001")
        self.assertFalse(again)

    # ------------------------------------------------------------------ #
    # 边界
    # ------------------------------------------------------------------ #
    def test_unknown_event_type_returns_empty(self) -> None:
        """未知 event_type 应返回空列表不崩溃。"""
        self.assertEqual(self.service.check_and_unlock("p1", "bogus_event"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
