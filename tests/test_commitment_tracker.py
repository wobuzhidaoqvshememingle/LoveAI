# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - CommitmentTracker 单元测试

覆盖：
  - 承诺提取（received 对方答应 / sent 我方答应）
  - 提取去重
  - 四种履约分类：time_delay / event_delay / no_reason / reneged
  - 正常兑现 fulfilled
  - 兑现率计算（高兑现率 high / 低兑现率 low / 欺骗 critical）
  - scan_and_update 批量更新计数
  - 与 TaskEngine 联动：高兑现率任务策略 / 低兑现率任务策略
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
from services.affection_engine import AffectionEngine  # noqa: E402
from services.commitment_tracker import CommitmentTracker  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402


def _ago(days: float) -> str:
    """返回 N 天前的时间字符串。"""
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class TestCommitmentTracker(unittest.TestCase):
    """CommitmentTracker 测试类（临时 SQLite）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_commit_")
        self.db_path = os.path.join(self.tmpdir, "test_commit.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.tracker = CommitmentTracker(self.db)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 承诺提取
    # ------------------------------------------------------------------ #
    def test_extract_promise_received(self) -> None:
        """对方说'我答应你…'应提取为 direction=received 的 pending 承诺。"""
        msgs = [{
            "person_id": "p1", "direction": "received",
            "content": "我答应你周末一起吃饭", "created_at": _now(),
        }]
        rows = self.tracker.extract_promises(msgs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "received")
        self.assertEqual(rows[0]["status"], "pending")

    def test_extract_promise_sent(self) -> None:
        """我方说'我保证…'应提取为 direction=sent。"""
        msgs = [{
            "person_id": "p1", "direction": "sent",
            "content": "我保证下次请你喝咖啡", "created_at": _now(),
        }]
        rows = self.tracker.extract_promises(msgs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direction"], "sent")

    def test_extract_no_promise_keyword(self) -> None:
        """普通闲聊不应提取承诺。"""
        msgs = [{
            "person_id": "p1", "direction": "received",
            "content": "今天天气不错", "created_at": _now(),
        }]
        self.assertEqual(self.tracker.extract_promises(msgs), [])

    def test_extract_dedup(self) -> None:
        """同一 person+text+time 重复喂入不应重复插入。"""
        msgs = [{
            "person_id": "p1", "direction": "received",
            "content": "说好了周末去看电影", "created_at": _now(),
        }]
        first = self.tracker.extract_promises(msgs)
        second = self.tracker.extract_promises(msgs)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        all_rows = self.db.fetch_all(
            "SELECT COUNT(*) AS c FROM commitments WHERE person_id='p1'")
        self.assertEqual(all_rows[0]["c"], 1)

    # ------------------------------------------------------------------ #
    # 四种履约分类
    # ------------------------------------------------------------------ #
    def _make_promise(self, person: str, days_ago: float = 1.0,
                      text: str = "我答应你周末一起吃饭") -> int:
        """插入一条 received pending 承诺，返回 id。"""
        self.db.execute(
            "INSERT INTO commitments (person_id, promise_text, promised_at, "
            "status, direction) VALUES (:p, :t, :pa, 'pending', 'received')",
            {"p": person, "t": text, "pa": _ago(days_ago)},
        )
        row = self.db.fetch_one(
            "SELECT id FROM commitments WHERE person_id=:p AND direction='received' "
            "ORDER BY id DESC LIMIT 1", {"p": person})
        return row["id"]

    def test_classify_time_delay(self) -> None:
        """加班+改期 → delayed / time_delay。"""
        pid = self._make_promise("p2", days_ago=1.0, text="我保证周六到")
        followup = [{
            "direction": "received",
            "content": "对不起周六要加班，周日行吗", "created_at": _now(),
        }]
        res = self.tracker.classify_fulfillment("p2", pid, followup)
        self.assertEqual(res["status"], "delayed")
        self.assertEqual(res["fulfillment_type"], "time_delay")

    def test_classify_event_delay(self) -> None:
        """生病/家人有事 → delayed / event_delay。"""
        pid = self._make_promise("p3", days_ago=1.0, text="说好了明天陪你")
        followup = [{
            "direction": "received",
            "content": "不好意思我突然发烧了，家里有事来不了", "created_at": _now(),
        }]
        res = self.tracker.classify_fulfillment("p3", pid, followup)
        self.assertEqual(res["status"], "delayed")
        self.assertEqual(res["fulfillment_type"], "event_delay")

    def test_classify_no_reason(self) -> None:
        """承诺超 7 天无兑现无解释 → broken / no_reason。"""
        pid = self._make_promise("p4", days_ago=10.0, text="我答应你周末一起吃饭")
        followup = [{
            "direction": "received",
            "content": "最近在忙别的", "created_at": _now(),
        }]
        res = self.tracker.classify_fulfillment("p4", pid, followup)
        self.assertEqual(res["status"], "broken")
        self.assertEqual(res["fulfillment_type"], "no_reason")

    def test_classify_reneged(self) -> None:
        """'我什么时候说过' → deceived / reneged。"""
        pid = self._make_promise("p5", days_ago=2.0, text="说好了下周去旅游")
        followup = [{
            "direction": "received",
            "content": "我什么时候说过去旅游了？你记错了吧", "created_at": _now(),
        }]
        res = self.tracker.classify_fulfillment("p5", pid, followup)
        self.assertEqual(res["status"], "deceived")
        self.assertEqual(res["fulfillment_type"], "reneged")

    def test_classify_fulfilled(self) -> None:
        """后续出现兑现行为 → fulfilled。"""
        pid = self._make_promise("p6", days_ago=1.0, text="我答应你把东西做好")
        followup = [{
            "direction": "received",
            "content": "放心，我已经做好了", "created_at": _now(),
        }]
        res = self.tracker.classify_fulfillment("p6", pid, followup)
        self.assertEqual(res["status"], "fulfilled")

    # ------------------------------------------------------------------ #
    # 兑现率
    # ------------------------------------------------------------------ #
    def _seed(self, person: str, results: list[tuple[str, str]]) -> None:
        """批量播种已决承诺：results = [(status, fulfillment_type), ...]。"""
        for st, ft in results:
            self.db.execute(
                "INSERT INTO commitments (person_id, promise_text, promised_at, "
                "status, fulfillment_type, direction) "
                "VALUES (:p, :t, :pa, :st, :ft, 'received')",
                {"p": person, "t": f"承诺{st}", "pa": _ago(1.0),
                 "st": st, "ft": ft},
            )

    def test_rate_high(self) -> None:
        """多次答应并兑现 → 兑现率>=80，trustworthy=high。"""
        self._seed("p7", [
            ("fulfilled", None), ("fulfilled", None),
            ("fulfilled", None), ("delayed", "time_delay"),
        ])
        rate = self.tracker.get_fulfillment_rate("p7")
        self.assertEqual(rate["trustworthy"], "high")
        self.assertGreaterEqual(rate["rate"], 80)

    def test_rate_low(self) -> None:
        """总不兑现 → 低兑现率。"""
        self._seed("p8", [
            ("broken", "no_reason"), ("broken", "no_reason"),
            ("broken", "no_reason"), ("fulfilled", None),
        ])
        rate = self.tracker.get_fulfillment_rate("p8")
        # 1/4 = 25% → critical
        self.assertIn(rate["trustworthy"], ("low", "critical"))
        self.assertLess(rate["rate"], 60)

    def test_rate_deceived_critical(self) -> None:
        """有 deceived 记录一律 critical。"""
        self._seed("p9", [
            ("fulfilled", None), ("fulfilled", None),
            ("deceived", "reneged"),
        ])
        rate = self.tracker.get_fulfillment_rate("p9")
        self.assertEqual(rate["trustworthy"], "critical")
        self.assertEqual(rate["deceived"], 1)

    # ------------------------------------------------------------------ #
    # scan_and_update 批量更新
    # ------------------------------------------------------------------ #
    def test_scan_and_update(self) -> None:
        """扫描多个 pending 承诺，统计新 broken / deceived 数量。"""
        # 两条 10 天前的承诺，无后续解释 → 应被判 broken
        for i in range(2):
            self.db.execute(
                "INSERT INTO commitments (person_id, promise_text, promised_at, "
                "status, direction) VALUES (:p, :t, :pa, 'pending', 'received')",
                {"p": "p10", "t": f"忘了{i}", "pa": _ago(10.0)},
            )
        summary = self.tracker.scan_and_update("p10", [
            {"direction": "received", "content": "随便聊聊", "created_at": _now()}
        ])
        self.assertEqual(summary["newly_broken"], 2)
        self.assertEqual(summary["newly_deceived"], 0)
        self.assertEqual(summary["updated"], 2)

    # ------------------------------------------------------------------ #
    # 与 TaskEngine 联动
    # ------------------------------------------------------------------ #
    def _make_engine(self) -> TaskEngine:
        affection = AffectionEngine(self.db)
        return TaskEngine(self.db, affection, guide_engine=None,
                          twin=None, signals=None,
                          commitment_tracker=self.tracker)

    def test_task_strategy_high_rate(self) -> None:
        """高兑现率 → 任务攻略方向含'大胆推进'。"""
        self._seed("p11", [("fulfilled", None), ("fulfilled", None),
                            ("fulfilled", None)])
        engine = self._make_engine()
        tasks = engine.generate_main_tasks("p11", stage=5)
        self.assertTrue(len(tasks) > 0)
        row = self.db.fetch_one(
            "SELECT guide_direction FROM tasks WHERE id=:tid",
            {"tid": tasks[0]["id"]})
        self.assertIn("大胆推进", row["guide_direction"])
        self.assertIn("履约", row["guide_direction"])

    def test_task_strategy_low_rate(self) -> None:
        """低兑现率 → 任务攻略方向含'保持观察'或'小承诺建立信任闭环'（攻略导向）。"""
        self._seed("p12", [("broken", "no_reason"), ("broken", "no_reason"),
                            ("deceived", "reneged")])
        engine = self._make_engine()
        tasks = engine.generate_main_tasks("p12", stage=5)
        row = self.db.fetch_one(
            "SELECT guide_direction FROM tasks WHERE id=:tid",
            {"tid": tasks[0]["id"]})
        text = row["guide_direction"]
        self.assertTrue(
            ("保持观察" in text) or ("小承诺建立信任" in text) or ("验证对方真实意图" in text),
            f"任务方向未包含攻略导向策略(保持观察/小承诺建立信任): {text}",
        )

    def test_task_engine_backward_compat(self) -> None:
        """不注入 commitment_tracker 时行为不变（无履约说明）。"""
        affection = AffectionEngine(self.db)
        engine = TaskEngine(self.db, affection, guide_engine=None)
        tasks = engine.generate_main_tasks("p13", stage=0)
        row = self.db.fetch_one(
            "SELECT guide_direction FROM tasks WHERE id=:tid",
            {"tid": tasks[0]["id"]})
        self.assertNotIn("履约", row["guide_direction"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
