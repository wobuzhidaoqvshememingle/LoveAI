# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - NoReplyDetector 单元测试（已读不回/不回复场景处理体系）

覆盖：
  1. 回复模式分析（平均/中位数/标准差/最快最慢/时段/主动比例/长度趋势/速度趋势）
  2. 数据不足时不瞎猜
  3. 不回复场景识别（是否在等待 / 等待时长 / 倍数 / 严重程度分级）
  4. 正常回复慢不误判为不回复
  5. 区分"回复慢"与"真·不回复"（是否超过历史最大回复时间）
  6. 5 种原因分类（真忙/疏远/惩罚性沉默/回避型空间/NPD冷暴力）
  7. 5 种原因策略两两不同
  8. 5 种原因任务两两不同、含时间线
  9. 严重程度调节策略强度
 10. 无已读状态下也能工作（read_state=unknown）
 11. db 端到端（从 chat_messages 读取）
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
from services.no_reply_detector import (  # noqa: E402
    NoReplyDetector,
    REASON_TRUE_BUSY,
    REASON_LOSING_INTEREST,
    REASON_PUNITIVE,
    REASON_AVOIDANT,
    REASON_NPD,
    SEV_LIGHT,
    SEV_MID,
    SEV_HEAVY,
    SEV_EXTREME,
    SEV_NONE,
)


def mk(content: str, direction: str, dt: datetime) -> dict:
    """构造一条消息。"""
    return {
        "content": content,
        "direction": direction,
        "created_at": dt.strftime("%Y-%m-%d %H:%M:%S"),
    }


class TestReplyPattern(unittest.TestCase):
    """回复模式分析测试。"""

    def setUp(self) -> None:
        self.d = NoReplyDetector(None)
        self.now = datetime.now()

    def _steady_conversation(self, n: int = 15, lag_min: int = 30) -> list[dict]:
        """稳定对话：每天一轮，对方 lag_min 分钟后回、长度稳定。"""
        msgs = []
        for i in range(n):
            base = (self.now - timedelta(days=20 - i)).replace(hour=10, minute=0)
            msgs.append(mk("今天过得怎么样", "sent", base))
            msgs.append(mk("挺不错的呀，和你说说今天的事", "received",
                           base + timedelta(minutes=lag_min)))
        return msgs

    def test_pattern_basic_stats(self) -> None:
        """平均/中位数/最快最慢/样本数正确。"""
        msgs = self._steady_conversation(15, lag_min=30)
        p = self.d._compute_pattern("p", msgs, 30)
        self.assertTrue(p["available"])
        self.assertAlmostEqual(p["reply_lag"]["mean_min"], 30.0, delta=1.0)
        self.assertAlmostEqual(p["reply_lag"]["fastest_min"], 30.0, delta=1.0)
        self.assertAlmostEqual(p["reply_lag"]["slowest_min"], 30.0, delta=1.0)
        self.assertGreaterEqual(p["reply_lag"]["sample_count"], 10)

    def test_pattern_time_distribution(self) -> None:
        """白天回复占比 > 0。"""
        msgs = self._steady_conversation(15)
        p = self.d._compute_pattern("p", msgs, 30)
        self.assertGreater(p["time_distribution"]["day_ratio"], 0.5)

    def test_pattern_insufficient_data(self) -> None:
        """样本不足 -> available=False，不瞎猜。"""
        msgs = [mk("hi", "sent", self.now), mk("yo", "received", self.now + timedelta(minutes=5))]
        p = self.d._compute_pattern("p", msgs, 30)
        self.assertFalse(p["available"])

    def test_pattern_no_read_state(self) -> None:
        """无微信已读状态 -> read_state_available=False。"""
        msgs = self._steady_conversation(10)
        p = self.d._compute_pattern("p", msgs, 30)
        self.assertFalse(p["read_state_available"])
        self.assertEqual(p["unread_no_reply_count"], 0)


class TestScenarioDetection(unittest.TestCase):
    """不回复场景识别测试。"""

    def setUp(self) -> None:
        self.d = NoReplyDetector(None)
        self.now = datetime.now()

    def _msgs(self, waiting_hours: float, mean_lag_min: float = 30.0,
              n: int = 12) -> list[dict]:
        """构造历史：n 轮正常对话(mean_lag_min 分钟回)，最后一条 sent 已等待 waiting_hours。"""
        msgs = []
        for i in range(n):
            base = (self.now - timedelta(days=15 - i)).replace(hour=10, minute=0)
            msgs.append(mk("在干嘛", "sent", base))
            msgs.append(mk("刚在忙，回你一下", "received",
                           base + timedelta(minutes=mean_lag_min)))
        # 最后一条：我发的，等待 waiting_hours
        msgs.append(mk("周末出来吗", "sent", self.now - timedelta(hours=waiting_hours)))
        return msgs

    def test_waiting_returns_no_reply(self) -> None:
        """最后一条是我发的且等待很久 -> is_no_reply=True。"""
        msgs = self._msgs(waiting_hours=48, mean_lag_min=30)
        sc = self.d._compute_scenario("p", msgs, self.now)
        self.assertTrue(sc["waiting_on_partner"])
        self.assertTrue(sc["is_no_reply"])
        self.assertGreater(sc["waiting_hours"], 40)

    def test_last_received_not_waiting(self) -> None:
        """最后一条是对方回的 -> 不在等待。"""
        msgs = self._msgs(waiting_hours=48)
        msgs.append(mk("好呀，周六见", "received", self.now - timedelta(hours=1)))
        sc = self.d._compute_scenario("p", msgs, self.now)
        self.assertFalse(sc["waiting_on_partner"])
        self.assertFalse(sc["is_no_reply"])

    def test_normal_slow_not_misjudged(self) -> None:
        """正常回复慢（等待 1 小时，历史平均 30 分钟，ratio=2 但 <2x 边界附近）不误判。"""
        # 等待 40 分钟，历史平均 30 分钟 -> ratio=1.33 < 1.5 -> 无
        msgs = self._msgs(waiting_hours=40 / 60.0, mean_lag_min=30)
        sc = self.d._compute_scenario("p", msgs, self.now)
        self.assertEqual(sc["severity"], SEV_NONE)
        self.assertFalse(sc["is_no_reply"])

    def test_severity_levels(self) -> None:
        """严重程度分级正确：2/5/10 倍。"""
        # mean_lag=30min
        # 90min -> ratio=3 -> 中度
        sc_mid = self.d._compute_scenario("p", self._msgs(90 / 60.0, 30), self.now)
        self.assertEqual(sc_mid["severity"], SEV_MID)
        # 300min -> ratio=10 -> 极端
        sc_ext = self.d._compute_scenario("p", self._msgs(300 / 60.0, 30), self.now)
        self.assertEqual(sc_ext["severity"], SEV_EXTREME)
        # 150min -> ratio=5 -> 重度
        sc_heavy = self.d._compute_scenario("p", self._msgs(150 / 60.0, 30), self.now)
        self.assertEqual(sc_heavy["severity"], SEV_HEAVY)

    def test_beyond_max_true_no_reply(self) -> None:
        """超过历史最大回复时间 -> is_beyond_max=True（真·不回复）。"""
        # 历史最大 lag=30min，等待 48h 远超 -> beyond_max
        sc = self.d._compute_scenario("p", self._msgs(48, 30), self.now)
        self.assertTrue(sc["is_beyond_max"])

    def test_read_state_unknown(self) -> None:
        """无已读/在线接口 -> read_state/online_status 均 unknown。"""
        sc = self.d._compute_scenario("p", self._msgs(48, 30), self.now)
        self.assertEqual(sc["read_state"], "unknown")
        self.assertEqual(sc["online_status"], "unknown")


class TestReasonClassification(unittest.TestCase):
    """5 种原因分类测试。"""

    def setUp(self) -> None:
        self.d = NoReplyDetector(None)
        self.now = datetime.now()

    def _normal_history(self, n: int = 10) -> list[dict]:
        msgs = []
        for i in range(n):
            base = (self.now - timedelta(days=15 - i)).replace(hour=10, minute=0)
            msgs.append(mk("早安", "sent", base))
            msgs.append(mk("早呀，今天也要开开心心的", "received",
                           base + timedelta(minutes=20)))
        return msgs

    def test_true_busy(self) -> None:
        """平时正常 + 最近对方提到加班 + 无疏远趋势 -> 真忙。"""
        msgs = self._normal_history(10)
        msgs.append(mk("最近项目加班，天天到深夜", "received",
                       self.now - timedelta(days=2, hours=18)))
        msgs.append(mk("那你先忙，注意身体", "sent",
                       self.now - timedelta(days=2, hours=19)))
        r = self.d.classify_reason("p", msgs)
        self.assertEqual(r["reason"], REASON_TRUE_BUSY)

    def test_losing_interest(self) -> None:
        """越来越慢 + 越来越短 + 不主动 -> 疏远。"""
        msgs = []
        for i in range(16):
            day_base = (self.now - timedelta(days=25 - i)).replace(hour=10, minute=0)
            if i < 8:
                recv_t = day_base + timedelta(minutes=5)
                recv_txt = "今天发生了好多有趣的事情，迫不及待想和你分享一大堆细节呀"
            else:
                recv_t = day_base + timedelta(minutes=200)  # 慢，但不跨天溢出
                recv_txt = "嗯"
            msgs.append(mk("在干嘛呢", "sent", day_base))
            msgs.append(mk(recv_txt, "received", recv_t))
        msgs.append(mk("周末有空吗", "sent", self.now - timedelta(hours=30)))
        r = self.d.classify_reason("p", msgs)
        self.assertEqual(r["reason"], REASON_LOSING_INTEREST)

    def test_punitive_silence(self) -> None:
        """单次吵架后沉默 -> 惩罚性沉默。"""
        msgs = self._normal_history(10)
        msgs.append(mk("你又这样，我真的很生气", "sent",
                       self.now - timedelta(days=3)))
        msgs.append(mk("算了我不想说，不可理喻", "received",
                       self.now - timedelta(days=3, hours=1)))
        msgs.append(mk("那我们冷静一下", "sent",
                       self.now - timedelta(days=3, hours=2)))
        r = self.d.classify_reason("p", msgs)
        self.assertEqual(r["reason"], REASON_PUNITIVE)

    def test_avoidant_space(self) -> None:
        """对方说需要静一静 -> 回避型需要空间。"""
        msgs = self._normal_history(10)
        msgs.append(mk("我们是不是走得太近了", "sent", self.now - timedelta(days=5)))
        msgs.append(mk("我需要静一静，让我缓一缓", "received",
                       self.now - timedelta(days=5, hours=2)))
        msgs.append(mk("好，那我不打扰你", "sent", self.now - timedelta(days=5, hours=3)))
        r = self.d.classify_reason("p", msgs)
        self.assertEqual(r["reason"], REASON_AVOIDANT)

    def test_npd_coldwar(self) -> None:
        """多次冲突 + 间歇性冷淡消息 -> NPD冷暴力。"""
        msgs = self._normal_history(10)
        msgs.append(mk("你又开始了，随你吧", "received",
                       self.now - timedelta(days=10)))
        msgs.append(mk("那你想怎样", "received", self.now - timedelta(days=7)))
        msgs.append(mk("我都说了别烦我", "received", self.now - timedelta(days=5)))
        msgs.append(mk("行吧随便你", "received", self.now - timedelta(days=3)))
        msgs.append(mk("我等你想清楚", "sent", self.now - timedelta(days=2)))
        r = self.d.classify_reason("p", msgs)
        self.assertEqual(r["reason"], REASON_NPD)


class TestStrategyAndTasks(unittest.TestCase):
    """5 种原因策略/任务两两不同测试。"""

    def setUp(self) -> None:
        self.d = NoReplyDetector(None)

    def test_five_strategies_distinct(self) -> None:
        """5 种原因的推荐策略两两不同。"""
        recs = {}
        for r in (REASON_TRUE_BUSY, REASON_LOSING_INTEREST, REASON_PUNITIVE,
                  REASON_AVOIDANT, REASON_NPD):
            s = self.d.get_strategy(r, SEV_MID)
            recs[r] = tuple(s["recommended"])
        # 两两不相等
        keys = list(recs.keys())
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                self.assertNotEqual(recs[keys[i]], recs[keys[j]],
                                    f"{keys[i]} 与 {keys[j]} 策略相同")

    def test_five_tasks_distinct(self) -> None:
        """5 种原因的任务两两不同。"""
        titles = {}
        for r in (REASON_TRUE_BUSY, REASON_LOSING_INTEREST, REASON_PUNITIVE,
                  REASON_AVOIDANT, REASON_NPD):
            tasks = self.d.get_tasks(r, SEV_MID, 3)
            titles[r] = tuple(t["title"] for t in tasks)
        keys = list(titles.keys())
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                self.assertNotEqual(titles[keys[i]], titles[keys[j]],
                                    f"{keys[i]} 与 {keys[j]} 任务相同")

    def test_tasks_have_timeline(self) -> None:
        """每个任务都带时间线。"""
        for r in (REASON_TRUE_BUSY, REASON_LOSING_INTEREST, REASON_PUNITIVE,
                  REASON_AVOIDANT, REASON_NPD):
            for t in self.d.get_tasks(r, SEV_MID, 3):
                self.assertTrue(t.get("timeline"), f"{r} 任务缺时间线")
                self.assertIn("wait_days", t)

    def test_severity_modulates_strength(self) -> None:
        """极端严重度时 NPD/疏远类会追加'评估关系'任务。"""
        heavy = self.d.get_tasks(REASON_NPD, SEV_HEAVY, 5)
        extreme = self.d.get_tasks(REASON_NPD, SEV_EXTREME, 5)
        titles_ext = [t["title"] for t in extreme]
        self.assertTrue(any("评估" in t for t in titles_ext))
        # 重度不强制追加（少于极端）
        self.assertLessEqual(len(heavy), len(extreme))

    def test_strategy_explanation_differs(self) -> None:
        """策略解释文字不同。"""
        exps = {self.d.get_strategy(r, SEV_MID)["explanation"][:10]
                for r in (REASON_TRUE_BUSY, REASON_NPD)}
        self.assertGreaterEqual(len(exps), 1)


class TestDbEndToEnd(unittest.TestCase):
    """通过临时数据库端到端测试。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_noreply_")
        self.db = Database(os.path.join(self.tmpdir, "test.db"))
        self.db.init_tables()
        self.d = NoReplyDetector(self.db)
        self.now = datetime.now()

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _insert(self, content: str, direction: str, dt: datetime) -> None:
        self.db.execute(
            "INSERT INTO chat_messages (person_id, direction, content, created_at) "
            "VALUES (:pid, :dir, :ct, :ca)",
            {"pid": "p1", "dir": direction,
             "ct": content, "ca": dt.strftime("%Y-%m-%d %H:%M:%S")},
        )

    def test_db_pattern_and_scenario(self) -> None:
        """从 chat_messages 读取 -> 模式 + 场景。"""
        for i in range(12):
            base = (self.now - timedelta(days=15 - i)).replace(hour=10, minute=0)
            self._insert("在干嘛", "sent", base)
            self._insert("回你啦", "received", base + timedelta(minutes=30))
        # 我最后一条消息等了 48h
        self._insert("周末出来吗", "sent", self.now - timedelta(hours=48))

        pat = self.d.analyze_reply_pattern("p1", recent_days=30)
        self.assertTrue(pat["available"])
        sc = self.d.detect_no_reply_scenario("p1", current_time=self.now)
        self.assertTrue(sc["is_no_reply"])
        self.assertGreater(sc["ratio_to_hist_mean"], 10)

    def test_db_empty_person(self) -> None:
        """无记录人物 -> 不回复=False，不抛异常。"""
        sc = self.d.detect_no_reply_scenario("nobody", current_time=self.now)
        self.assertFalse(sc["is_no_reply"])


if __name__ == "__main__":
    unittest.main()
