# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - R3.3-34 记忆闸门单元测试

覆盖：
  1. 正常记忆直接 status=active
  2. 与已有记忆矛盾（已有"喜欢猫"，新写"讨厌猫"）→ pending_calibration
  3. 太短 / 纯语气词（"哦"）→ 丢弃不写
  4. resolve_pending confirm → active；reject → 删除
  5. review_pending 返回所有 pending
  附加：单条内自相矛盾（"喜欢...但是...讨厌..."）→ pending
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
from services.memory_service import MemoryService  # noqa: E402


class TestMemoryGate(unittest.TestCase):
    """记忆闸门测试类（临时 SQLite 真实库）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_gate_")
        self.db = Database(os.path.join(self.tmpdir, "gate.db"))
        self.db.init_tables()
        self.mem = MemoryService(self.db, config=None, rag=None, signals=None)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    def test_normal_memory_is_active(self) -> None:
        """1. 正常记忆直接 active，且对读取路径可见。"""
        r = self.mem.add_fact("p1", "她喜欢吃火锅", category="preference")
        self.assertTrue(r["added"])
        self.assertEqual(r["status"], "active")

        row = self.db.fetch_one(
            "SELECT status, is_active FROM long_term_memory WHERE id=:i",
            {"i": r["id"]},
        )
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["is_active"], 1)

        # 正常读取路径能看到
        visible = self.mem.get_memories("p1")
        self.assertEqual(len(visible), 1)

    def test_contradiction_marks_pending(self) -> None:
        """2. 已有"喜欢猫"，新写"讨厌猫" → pending_calibration，且不污染活跃记忆。"""
        r1 = self.mem.add_fact("p1", "她喜欢猫", category="preference")
        self.assertEqual(r1["status"], "active")

        r2 = self.mem.add_fact("p1", "她讨厌猫", category="preference")
        self.assertTrue(r2["added"])  # 仍写入库，但挂起
        self.assertEqual(r2["status"], "pending_calibration")

        row = self.db.fetch_one(
            "SELECT status, is_active FROM long_term_memory WHERE id=:i",
            {"i": r2["id"]},
        )
        self.assertEqual(row["status"], "pending_calibration")
        self.assertEqual(row["is_active"], 0)  # 不进入活跃读取

        # 活跃读取路径只能看到原"喜欢猫"，看不到挂起的"讨厌猫"
        active = self.mem.get_memories("p1")
        self.assertEqual(len(active), 1)
        self.assertIn("喜欢猫", active[0]["content"])

    def test_too_short_interjection_dropped(self) -> None:
        """3. 语气词"哦"直接丢弃，不写库。"""
        r = self.mem.add_fact("p1", "哦")
        self.assertFalse(r["added"])
        self.assertTrue(r.get("dropped"))
        self.assertEqual(r["status"], "drop")

        # 数据库里没有任何行
        cnt = self.db.fetch_one(
            "SELECT COUNT(*) AS c FROM long_term_memory WHERE person_id='p1'"
        )
        self.assertEqual(cnt["c"], 0)

    def test_vague_phrase_dropped(self) -> None:
        """太泛的套话（"她挺好的"）也应丢弃。"""
        r = self.mem.add_fact("p1", "她挺好的")
        self.assertFalse(r["added"])

    def test_self_contradiction_marks_pending(self) -> None:
        """附加：同一条内"喜欢...但是...讨厌..." → pending。"""
        r = self.mem.add_fact(
            "p1", "她平时挺喜欢猫的，但其实又有点讨厌猫的味道"
        )
        self.assertTrue(r["added"])
        self.assertEqual(r["status"], "pending_calibration")

    # ------------------------------------------------------------------ #
    def test_review_pending_returns_all_pending(self) -> None:
        """5. review_pending 返回所有 pending_calibration 记忆。"""
        # 先放一条正常的
        self.mem.add_fact("p1", "她喜欢火锅")
        # 再制造两条 pending
        self.mem.add_fact("p1", "她讨厌火锅")  # 与"喜欢火锅"矛盾 → pending
        self.mem.add_fact("p2", "她喜欢电影")
        self.mem.add_fact("p2", "她讨厌电影")  # 跨 pid 的矛盾 → pending

        pending = self.mem.review_pending()
        contents = [p["content"] for p in pending]
        # 两条 pending 都应被列出
        self.assertEqual(len(pending), 2)
        self.assertTrue(any("讨厌火锅" in c for c in contents))
        self.assertTrue(any("讨厌电影" in c for c in contents))
        # 每条都带状态
        self.assertTrue(all(p["status"] == "pending_calibration" for p in pending))

    def test_resolve_confirm_activates(self) -> None:
        """4a. resolve_pending confirm → status=active, is_active=1。"""
        self.mem.add_fact("p1", "她喜欢猫")
        r = self.mem.add_fact("p1", "她讨厌猫")  # pending
        mid = r["id"]

        res = self.mem.resolve_pending(mid, "confirm")
        self.assertTrue(res["resolved"])

        row = self.db.fetch_one(
            "SELECT status, is_active FROM long_term_memory WHERE id=:i",
            {"i": mid},
        )
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["is_active"], 1)

        # 现在活跃读取能看到这条
        active_contents = [m["content"] for m in self.mem.get_memories("p1")]
        self.assertEqual(len(active_contents), 2)

    def test_resolve_reject_deletes(self) -> None:
        """4b. resolve_pending reject → 硬删除。"""
        self.mem.add_fact("p1", "她喜欢猫")
        r = self.mem.add_fact("p1", "她讨厌猫")  # pending
        mid = r["id"]

        res = self.mem.resolve_pending(mid, "reject")
        self.assertTrue(res["resolved"])

        row = self.db.fetch_one(
            "SELECT * FROM long_term_memory WHERE id=:i",
            {"i": mid},
        )
        self.assertIsNone(row)

        # 只剩原"喜欢猫"
        active = self.mem.get_memories("p1")
        self.assertEqual(len(active), 1)
        self.assertIn("喜欢猫", active[0]["content"])

    def test_resolve_unknown_action_rejected(self) -> None:
        """未知 action 不生效。"""
        r = self.mem.add_fact("p1", "她喜欢猫")
        res = self.mem.resolve_pending(r["id"], "bogus")
        self.assertFalse(res["resolved"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
