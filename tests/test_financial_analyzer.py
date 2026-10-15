# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - FinancialAnalyzer 单元测试

覆盖：
  - 表自动创建
  - _extract_signals 空消息 / 命中收入信号
  - _estimate_from_signals 兜底估算
  - generate_report 完整报告（mock LLM）
  - check_demand_feasibility low / medium / high / dangerous 分档
  - record_expense / get_expense_summary
  - 无人物不崩溃 / LLM 不可用降级
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
from services.financial_analyzer import FinancialAnalyzer  # noqa: E402


class TestFinancialAnalyzer(unittest.TestCase):
    """FinancialAnalyzer 测试类（临时 SQLite）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_fin_")
        self.db_path = os.path.join(self.tmpdir, "test_fin.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.db.execute(
            "INSERT INTO persons (id, nickname, gender, occupation) "
            "VALUES (:id,:n,:g,:o)",
            {"id": "p1", "n": "苏晚", "g": "女", "o": "设计师"},
        )

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _insert_message(self, pid: str, content: str, direction: str = "received") -> None:
        self.db.execute(
            "INSERT INTO chat_messages (person_id, direction, content, created_at) "
            "VALUES (:pid,:d,:c, datetime('now'))",
            {"pid": pid, "d": direction, "c": content},
        )

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def test_init_creates_tables(self) -> None:
        """__init__ 应自动创建 financial_reports 与 expense_records 表。"""
        FinancialAnalyzer(self.db, llm=None)
        rows = self.db.fetch_all("SELECT * FROM financial_reports")
        self.assertIsInstance(rows, list)
        rows2 = self.db.fetch_all("SELECT * FROM expense_records")
        self.assertIsInstance(rows2, list)

    # ------------------------------------------------------------------ #
    # 信号提取
    # ------------------------------------------------------------------ #
    def test_extract_signals_empty(self) -> None:
        """无聊天记录时信号列表为空。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        self.assertEqual(fa._extract_signals("nobody"), [])

    def test_extract_signals_income_hit(self) -> None:
        """含工资数字的消息应提取出 income 信号。"""
        self._insert_message("p1", "我这个月工资到手8000块")
        fa = FinancialAnalyzer(self.db, llm=None)
        signals = fa._extract_signals("p1")
        self.assertGreaterEqual(len(signals), 1)
        self.assertEqual(signals[0]["type"], "income")

    # ------------------------------------------------------------------ #
    # 兜底估算
    # ------------------------------------------------------------------ #
    def test_estimate_from_signals_fallback(self) -> None:
        """LLM 不可用时 _estimate_from_signals 返回完整基础报告。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        report = fa._estimate_from_signals([])
        for k in ("income_estimate", "disposable_income", "consumption_level",
                  "large_purchase_capacity", "stress_index",
                  "suggested_amount_limit", "summary"):
            self.assertIn(k, report)
        self.assertGreater(report["suggested_amount_limit"], 0)

    def test_generate_report_llm_available(self) -> None:
        """LLM 可用时应返回 LLM 分析报告并落库。"""
        llm = MagicMock()
        llm.chat.return_value = (
            '{"income_estimate":"8k-12k","disposable_income":"4k-6k",'
            '"consumption_level":"mid_high","large_purchase_capacity":"千元级",'
            '"stress_index":40,"suggested_amount_limit":5000,"summary":"消费偏中高"}'
        )
        fa = FinancialAnalyzer(self.db, llm=llm)
        report = fa.generate_report("p1")
        self.assertEqual(report["consumption_level"], "mid_high")
        self.assertEqual(report["suggested_amount_limit"], 5000)
        self.assertIn("raw_signals", report)

    def test_generate_report_llm_unavailable_degrades(self) -> None:
        """LLM=None 时 generate_report 走规则兜底，不崩溃。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        report = fa.generate_report("p1")
        self.assertIn("consumption_level", report)
        self.assertGreaterEqual(report["stress_index"], 0)

    # ------------------------------------------------------------------ #
    # 需求可行性分档
    # ------------------------------------------------------------------ #
    def test_check_demand_low(self) -> None:
        """小额需求 ratio<0.3 -> low 且 feasible。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        res = fa.check_demand_feasibility("p1", 1000.0, "一杯奶茶")
        self.assertEqual(res["risk_level"], "low")
        self.assertTrue(res["feasible"])

    def test_check_demand_medium(self) -> None:
        """中等金额 0.3<=ratio<0.6 -> medium 仍 feasible。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        res = fa.check_demand_feasibility("p1", 3000.0, "一双鞋")
        self.assertEqual(res["risk_level"], "medium")
        self.assertTrue(res["feasible"])

    def test_check_demand_high(self) -> None:
        """较高金额 0.6<=ratio<=1.0 -> high 且不可行。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        res = fa.check_demand_feasibility("p1", 5000.0, "一个包")
        self.assertEqual(res["risk_level"], "high")
        self.assertFalse(res["feasible"])

    def test_check_demand_dangerous(self) -> None:
        """超额 ratio>1.0 -> dangerous + 安全警告。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        res = fa.check_demand_feasibility("p1", 20000.0, "奢侈品包")
        self.assertEqual(res["risk_level"], "dangerous")
        self.assertFalse(res["feasible"])
        self.assertTrue(len(res["warning"]) > 0)

    # ------------------------------------------------------------------ #
    # 支出记录
    # ------------------------------------------------------------------ #
    def test_record_expense_and_summary(self) -> None:
        """record_expense 写入后 get_expense_summary 应正确汇总。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        fa.record_expense("p1", 100.0, "午饭")
        fa.record_expense("p1", 200.0, "电影")
        summary = fa.get_expense_summary("p1")
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["total_amount"], 300.0)
        self.assertEqual(summary["avg_amount"], 150.0)

    def test_no_person_no_crash(self) -> None:
        """对不存在的人物 generate_report 不应崩溃。"""
        fa = FinancialAnalyzer(self.db, llm=None)
        report = fa.generate_report("ghost_person")
        self.assertIn("suggested_amount_limit", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
