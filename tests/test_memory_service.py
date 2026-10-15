# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - MemoryService 单元测试

覆盖：
  - 表自动创建 / add_memory / add_fact / add_lesson / add_conclusion
  - get_memories 多条件过滤 / search_memories LIKE 降级
  - update_memory / forget_memory 软删 / delete_memory 硬删
  - consolidate_memories 合并相似 / extract_from_chat 规则提取
  - get_memory_context 分组 / get_memory_stats / apply_decay / access_count
"""
import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.memory_service import MemoryService  # noqa: E402


class TestMemoryService(unittest.TestCase):
    """MemoryService 测试类（临时 SQLite 真实库）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_memory_")
        self.db = Database(os.path.join(self.tmpdir, "mem.db"))
        self.db.init_tables()
        self.mem = MemoryService(self.db, config=None, rag=None, signals=None)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 表创建 / 写入
    # ------------------------------------------------------------------ #
    def test_init_creates_table(self) -> None:
        """初始化后 long_term_memory 表应存在。"""
        row = self.db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='long_term_memory'")
        self.assertIsNotNone(row)

    def test_add_fact_writes(self) -> None:
        r = self.mem.add_fact("p1", "喜欢吃火锅", category="preference")
        self.assertTrue(r["added"])
        self.assertIsNotNone(r["id"])

    def test_add_lesson_and_conclusion(self) -> None:
        self.mem.add_lesson("p1", "不要在生气时发消息")
        self.mem.add_conclusion("p1", "她比较慢热")
        rows = self.db.fetch_all("SELECT memory_type FROM long_term_memory "
                                "WHERE person_id='p1'")
        types = sorted(r["memory_type"] for r in rows)
        self.assertEqual(types, ["conclusion", "lesson"])

    def test_add_memory_invalid_args(self) -> None:
        """空 person_id / content / type 应拒绝写入。"""
        r = self.mem.add_memory("", "fact", "x")
        self.assertFalse(r["added"])
        r2 = self.mem.add_memory("p1", "fact", "")
        self.assertFalse(r2["added"])

    # ------------------------------------------------------------------ #
    # 检索 / 搜索
    # ------------------------------------------------------------------ #
    def test_get_memories_filters(self) -> None:
        """get_memories 按 person / type / category 过滤。"""
        self.mem.add_fact("p1", "事实A", category="preference")
        self.mem.add_fact("p1", "事实B", category="personality")
        self.mem.add_lesson("p1", "教训A")
        self.mem.add_fact("p2", "别人的记忆")

        all_p1 = self.mem.get_memories("p1")
        self.assertEqual(len(all_p1), 3)

        facts = self.mem.get_memories("p1", memory_type="fact")
        self.assertEqual(len(facts), 2)

        pref = self.mem.get_memories("p1", category="preference")
        self.assertEqual(len(pref), 1)
        self.assertIn("事实A", pref[0]["content"])

    def test_search_memories_like_fallback(self) -> None:
        """rag=None 时走 LIKE 关键词降级。"""
        self.mem.add_fact("p1", "她喜欢吃麻辣火锅", category="preference")
        self.mem.add_fact("p1", "她讨厌排队", category="preference")

        hit = self.mem.search_memories("p1", "火锅")
        self.assertEqual(len(hit), 1)
        self.assertIn("火锅", hit[0]["content"])

        miss = self.mem.search_memories("p1", "根本不存在的词xyz")
        self.assertEqual(miss, [])

    # ------------------------------------------------------------------ #
    # 更新 / 遗忘 / 删除
    # ------------------------------------------------------------------ #
    def test_update_memory_fields(self) -> None:
        r = self.mem.add_fact("p1", "旧内容")
        self.mem.update_memory(r["id"], content="新内容", importance=0.9)
        row = self.db.fetch_one("SELECT content, importance FROM long_term_memory "
                               "WHERE id=:i", {"i": r["id"]})
        self.assertEqual(row["content"], "新内容")
        self.assertEqual(row["importance"], 0.9)

    def test_forget_memory_soft_delete(self) -> None:
        """forget_memory 软删（is_active=0），仍可统计。"""
        r = self.mem.add_fact("p1", "将被遗忘")
        self.mem.forget_memory(r["id"])
        active = self.mem.get_memories("p1", active_only=True)
        self.assertEqual(len(active), 0)
        all_rows = self.mem.get_memories("p1", active_only=False)
        self.assertEqual(len(all_rows), 1)
        self.assertEqual(all_rows[0]["is_active"], 0)

    def test_delete_memory_hard(self) -> None:
        """delete_memory 硬删除。"""
        r = self.mem.add_fact("p1", "将被删除")
        self.mem.delete_memory(r["id"])
        row = self.db.fetch_one("SELECT * FROM long_term_memory WHERE id=:i",
                               {"i": r["id"]})
        self.assertIsNone(row)

    # ------------------------------------------------------------------ #
    # 整合 / 提取
    # ------------------------------------------------------------------ #
    def test_consolidate_merges_similar(self) -> None:
        """高度相似的 fact 应被合并，被合并者软删。"""
        self.mem.add_fact("p1", "她喜欢吃火锅", category="preference",
                          importance=0.6)
        self.mem.add_fact("p1", "她喜欢吃火锅，真的", category="preference",
                          importance=0.5)
        result = self.mem.consolidate_memories("p1")
        self.assertGreaterEqual(result["consolidated"], 1)
        active = self.mem.get_memories("p1", memory_type="fact")
        self.assertEqual(len(active), 1)

    def test_extract_from_chat_preference(self) -> None:
        """聊天中"我喜欢"应提取为 preference fact。"""
        added = self.mem.extract_from_chat("p1", ["我喜欢吃辣"])
        self.assertTrue(any(a.get("category") == "preference" for a in added))

    def test_extract_from_chat_finance(self) -> None:
        """财务关键词应提取为 finance fact。"""
        added = self.mem.extract_from_chat("p1", ["我这个月工资到账了"])
        self.assertTrue(any(a.get("category") == "finance" for a in added))

    def test_extract_from_chat_empty(self) -> None:
        """空消息列表返回空。"""
        self.assertEqual(self.mem.extract_from_chat("p1", []), [])

    # ------------------------------------------------------------------ #
    # 上下文 / 统计 / 衰减 / 访问计数
    # ------------------------------------------------------------------ #
    def test_get_memory_context_groups(self) -> None:
        """get_memory_context 按【事实】【结论】分组。"""
        self.mem.add_fact("p1", "喜欢猫")
        self.mem.add_conclusion("p1", "性格慢热")
        ctx = self.mem.get_memory_context("p1")
        self.assertIn("【事实】", ctx)
        self.assertIn("【结论】", ctx)

    def test_get_memory_context_empty(self) -> None:
        self.assertEqual(self.mem.get_memory_context("nobody"), "")

    def test_get_memory_stats(self) -> None:
        """统计 total / active / forgotten。"""
        r1 = self.mem.add_fact("p1", "A")
        self.mem.add_fact("p1", "B")
        self.mem.forget_memory(r1["id"])
        stats = self.mem.get_memory_stats("p1")
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["active"], 1)
        self.assertEqual(stats["forgotten"], 1)

    def test_apply_decay_old_memory(self) -> None:
        """90 天未访问的低重要性记忆应被衰减遗忘。"""
        r = self.mem.add_fact("p1", "陈旧记忆", importance=0.12)
        old = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        self.db.execute(
            "UPDATE long_term_memory SET last_accessed=:t WHERE id=:i",
            {"t": old, "i": r["id"]})
        affected = self.mem.apply_decay("p1")
        self.assertGreaterEqual(affected, 1)
        row = self.db.fetch_one("SELECT is_active FROM long_term_memory WHERE id=:i",
                               {"i": r["id"]})
        self.assertEqual(row["is_active"], 0)  # 0.12*0.7=0.084<0.1

    def test_access_count_increments(self) -> None:
        """检索后 access_count 应 +1。"""
        r = self.mem.add_fact("p1", "被访问")
        self.mem.get_memories("p1")
        row = self.db.fetch_one("SELECT access_count FROM long_term_memory WHERE id=:i",
                               {"i": r["id"]})
        self.assertGreaterEqual(row["access_count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
