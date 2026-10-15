# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 投入平衡 + 双向奔赴 + 无事献殷勤 单元测试

覆盖：
  1. 投入比计算（情感/时间/金钱三维）
  2. 健康范围判断（balanced / user_overinvest / other_overinvest）
  3. 无事献殷勤检测（早期超额付出 + 短期索取）
  4. 无事献殷勤风险等级
  5. 用户超额投入时策略包含引导投入
  6. 投入平衡时策略正常（维持节奏）
  7. 引导对方投入类任务生成
  8. 任务描述是攻略导向格式
  9. 措辞是"双向奔赴"而非"捞你"
 10. 无投入数据时默认平衡
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

from services.persona_analyzer import PersonaAnalyzer  # noqa: E402
from services.deception_detector import DeceptionDetector  # noqa: E402
from services.strategy_advisor import StrategyAdvisor  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from models.database import Database  # noqa: E402


BASE = datetime(2026, 9, 1, 9, 0, 0)


def _m(direction, content, t_min):
    """构造一条消息：direction / content / 距 BASE t_min 分钟。"""
    return {"direction": direction, "content": content, "msg_type": 1,
            "created_at": (BASE + timedelta(minutes=t_min)).isoformat()}


class FakeDB:
    """内存假 db：只用于 PersonaAnalyzer 读取消息。"""

    def __init__(self, msgs):
        self._m = msgs

    def fetch_all(self, sql, params=None):
        return self._m


def _analyze_balance(msgs):
    """直接调用 PersonaAnalyzer 的投入平衡分析方法。"""
    pa = PersonaAnalyzer(FakeDB(msgs))
    other = [m for m in msgs if m["direction"] == "received"]
    mine = [m for m in msgs if m["direction"] == "sent"]
    return pa._analyze_investment_balance(msgs, other, mine)


# ------------------------------------------------------------------ #
# 1. 投入比计算（情感/时间/金钱）
# ------------------------------------------------------------------ #
class TestInvestmentRatioCalc(unittest.TestCase):
    """三维投入比计算测试。"""

    def test_emotional_ratio_user_starts_more(self):
        """用户主动发起4次、对方1次 → 情感比=4。"""
        msgs = []
        t = 0.0
        # 4 个 sent 开头簇
        for _ in range(4):
            msgs.append(_m("sent", "嗨", t)); t += 10
            msgs.append(_m("received", "嗯", t)); t += 40
        # 1 个 received 开头簇
        msgs.append(_m("received", "在吗", t)); t += 10
        msgs.append(_m("sent", "在", t))
        ib = _analyze_balance(msgs)
        self.assertEqual(ib["my_starts"], 4)
        self.assertEqual(ib["other_starts"], 1)
        self.assertEqual(ib["emotional_ratio"], 4.0)

    def test_time_ratio_by_char_count(self):
        """用户消息总字数约为对方2倍 → 时间比≈2。"""
        msgs = []
        t = 0.0
        for _ in range(3):
            msgs.append(_m("sent", "这是一条很长很长的用户消息内容用来测试字数统计", t)); t += 10
            msgs.append(_m("received", "好的我知道啦，谢谢你的关心呀", t)); t += 40
        ib = _analyze_balance(msgs)
        self.assertGreater(ib["time_ratio"], 1.5)
        self.assertLess(ib["time_ratio"], 3.0)

    def test_money_ratio_no_money_messages_default_1(self):
        """无任何金钱相关消息 → 金钱比默认 1.0（1:1）。"""
        msgs = []
        t = 0.0
        for _ in range(3):
            msgs.append(_m("sent", "今天天气不错", t)); t += 10
            msgs.append(_m("received", "是啊", t)); t += 40
        ib = _analyze_balance(msgs)
        self.assertEqual(ib["money_ratio"], 1.0)

    def test_money_ratio_user_gives_more(self):
        """用户请客3次、对方0次 → 金钱比=3。"""
        msgs = []
        t = 0.0
        for _ in range(3):
            msgs.append(_m("sent", "今天我请你吃饭，我买单", t)); t += 10
            msgs.append(_m("received", "好", t)); t += 40
        ib = _analyze_balance(msgs)
        self.assertEqual(ib["my_give"], 3)
        self.assertEqual(ib["other_give"], 0)
        self.assertEqual(ib["money_ratio"], 3.0)


# ------------------------------------------------------------------ #
# 2. 健康范围判断
# ------------------------------------------------------------------ #
class TestInvestmentStatusRange(unittest.TestCase):
    """投入平衡状态判定测试。"""

    def test_balanced_range(self):
        """双方投入接近 → balanced（0.7-1.3）。"""
        msgs = []
        t = 0.0
        for c in range(10):
            if c % 2 == 0:
                msgs.append(_m("sent", "晚上我请你吃饭，聊聊天", t)); t += 10
                msgs.append(_m("received", "好呀那我请你喝奶茶", t)); t += 40
            else:
                msgs.append(_m("received", "今天我请你看电影，下班走", t)); t += 10
                msgs.append(_m("sent", "好啊那我请你吃晚饭，我买单", t)); t += 40
        ib = _analyze_balance(msgs)
        self.assertEqual(ib["status"], "balanced")
        self.assertGreaterEqual(ib["overall_ratio"], 0.7)
        self.assertLessEqual(ib["overall_ratio"], 1.3)

    def test_user_overinvest(self):
        """用户主动发起80%、字数2倍、频繁请客 → user_overinvest(>1.3)。"""
        msgs = []
        t = 0.0
        for _ in range(8):
            msgs.append(_m("sent", "宝贝我特别想你，晚上我请你吃饭我给你买了礼物", t)); t += 8
            msgs.append(_m("received", "好", t)); t += 40
        for _ in range(2):
            msgs.append(_m("received", "在吗", t)); t += 8
            msgs.append(_m("sent", "在的我给你点了外卖", t)); t += 40
        ib = _analyze_balance(msgs)
        self.assertEqual(ib["status"], "user_overinvest")
        self.assertGreater(ib["overall_ratio"], 1.3)

    def test_other_overinvest(self):
        """对方主动发起多、对方请客多 → other_overinvest(<0.7)。"""
        msgs = []
        t = 0.0
        # 6 个 received 开头簇，对方频繁请客
        for _ in range(6):
            msgs.append(_m("received", "在吗，我请你吃饭，给你买了礼物", t)); t += 10
            msgs.append(_m("sent", "好", t)); t += 40
        # 1 个 sent 开头簇
        msgs.append(_m("sent", "早", t)); t += 10
        msgs.append(_m("received", "早", t))
        ib = _analyze_balance(msgs)
        self.assertEqual(ib["status"], "other_overinvest")
        self.assertLess(ib["overall_ratio"], 0.7)

    def test_empty_messages_default_balanced(self):
        """无消息时默认 balanced、overall=1.0。"""
        pa = PersonaAnalyzer(FakeDB([]))
        ib = pa._analyze_investment_balance([], [], [])
        self.assertEqual(ib["overall_ratio"], 1.0)
        self.assertEqual(ib["status"], "balanced")


# ------------------------------------------------------------------ #
# 3-4. 无事献殷勤检测
# ------------------------------------------------------------------ #
def _uw(content, direction, days_ago, hour=10):
    dt = datetime(2026, 10, 6, hour, 0, 0) - timedelta(days=days_ago)
    return {"content": content, "direction": direction,
            "created_at": dt.strftime("%Y-%m-%d %H:%M:%S")}


class TestUnwarrantedAttention(unittest.TestCase):
    """无事献殷勤识别测试。"""

    def setUp(self):
        self.det = DeceptionDetector(None)

    def test_early_overgive_then_demand_detected(self):
        """关系早期对方超前承诺+贵重付出，随后索取 → detected=true。"""
        msgs = [
            _uw("我养你，以后我挣钱给你花", "received", 50),
            _uw("嫁给我吧，我们结婚", "received", 45),
            _uw("给你买了个手机，最新款", "received", 40),
            _uw("谢谢", "sent", 38),
            _uw("最近手头紧，你先借我点钱周转一下", "received", 25),
            _uw("帮我个忙，转我5000块", "received", 20),
        ]
        uw = self.det.detect_unwarranted_attention(msgs)
        self.assertTrue(uw["detected"])
        self.assertGreaterEqual(uw["risk_score"], 60)

    def test_risk_score_level_high(self):
        """早期大额付出+立即索取 → high/critical 等级。"""
        msgs = [
            _uw("我养你，房产证写你名字", "received", 50),
            _uw("送你个包，最新款", "received", 48),
            _uw("给你买了个手机", "received", 46),
            _uw("借我点钱周转一下", "received", 45),  # 付出后1-5天即索取
            _uw("转我5000", "received", 44),
        ]
        uw = self.det.detect_unwarranted_attention(msgs)
        self.assertIn(uw["risk_level"], ("high", "critical"))

    def test_normal_pursuit_no_demand_not_detected(self):
        """正常追求期殷勤但无索取 → 不检测为无事献殷勤。"""
        msgs = [
            _uw("想你，今天开心吗", "received", 50),
            _uw("周末一起吃饭吧", "received", 40),
            _uw("好呀", "sent", 39),
            _uw("今天聊得挺开心", "received", 30),
        ]
        uw = self.det.detect_unwarranted_attention(msgs)
        self.assertFalse(uw["detected"])

    def test_note_wording_is_guide_not_accusation(self):
        """note 用"引导双向奔赴"而非"捞你"。"""
        msgs = [
            _uw("我养你，嫁给我", "received", 50),
            _uw("给你买了个手机", "received", 45),
            _uw("借我点钱周转一下", "received", 30),
        ]
        uw = self.det.detect_unwarranted_attention(msgs)
        self.assertIn("引导双向奔赴", uw["note"])
        self.assertNotIn("捞你", uw["note"])


# ------------------------------------------------------------------ #
# 5-6. 策略：引导投入 / 平衡
# ------------------------------------------------------------------ #
class TestInvestmentStrategy(unittest.TestCase):
    """投入平衡策略测试。"""

    def setUp(self):
        self.adv = StrategyAdvisor(None)

    def test_user_overinvest_strategy_guides_investment(self):
        """用户超额投入 → 推荐引导对方投入类技巧。"""
        persona = {"investment_balance": {
            "status": "user_overinvest", "overall_ratio": 2.5,
            "emotional_ratio": 3.0, "time_ratio": 2.0, "money_ratio": 2.5,
        }}
        r = self.adv.analyze(persona, 5)
        self.assertIn("请对方帮你一个小忙", r["recommended_raw"])
        self.assertIn("引导对方主动发起一次约会", r["recommended_raw"])
        self.assertIn("减少主动联系频率，观察对方是否会主动找你", r["recommended_raw"])
        self.assertIn("继续单方面超额付出", r["forbidden_raw"])
        self.assertIn("无底线讨好", r["forbidden_raw"])

    def test_balanced_strategy_maintains_rhythm(self):
        """投入平衡 → 推荐维持双向奔赴节奏，不生成引导投入禁忌。"""
        persona = {"investment_balance": {
            "status": "balanced", "overall_ratio": 1.0,
        }}
        r = self.adv.analyze(persona, 5)
        self.assertTrue(any("维持双向奔赴" in x for x in r["recommended_raw"]))
        # 平衡时不应推荐"请对方帮小忙"等引导投入技巧
        self.assertNotIn("请对方帮你一个小忙", r["recommended_raw"])

    def test_technique_has_purpose_annotation(self):
        """引导投入类推荐技巧带"（目的：xxx）"攻略目的说明。"""
        persona = {"investment_balance": {
            "status": "user_overinvest", "overall_ratio": 2.5,
        }}
        r = self.adv.analyze(persona, 5)
        guide = [x for x in r["recommended"] if "请对方帮你一个小忙" in x]
        self.assertTrue(guide, "应包含富兰克林效应技巧")
        self.assertIn("目的：", guide[0])

    def test_unwarranted_high_risk_strategy(self):
        """无事献殷勤高风险 → 策略含观察索取目的，禁忌单方面接受大额好处。"""
        persona = {
            "investment_balance": {"status": "balanced", "overall_ratio": 1.0},
            "unwarranted_attention": {
                "detected": True, "risk_score": 75, "risk_level": "high",
                "evidence": [], "note": "建议观察是否有明确目的，引导双向奔赴",
            },
        }
        r = self.adv.analyze(persona, 5)
        self.assertIn("观察索取目的+引导双向奔赴", r["recommended_raw"])
        self.assertIn("单方面接受大额好处", r["forbidden_raw"])

    def test_wording_no_lao_ni(self):
        """策略 reasoning 不含"捞你"，含"双向奔赴"。"""
        persona = {"investment_balance": {
            "status": "user_overinvest", "overall_ratio": 2.5,
        }}
        r = self.adv.analyze(persona, 5)
        self.assertIn("双向奔赴", r["reasoning"])
        self.assertNotIn("捞你", r["reasoning"])


# ------------------------------------------------------------------ #
# 7-8. 任务系统：引导对方投入类任务
# ------------------------------------------------------------------ #
class TestInvestmentTasks(unittest.TestCase):
    """任务引擎引导投入类任务测试。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_invest_")
        self.db = Database(os.path.join(self.tmpdir, "t.db"))
        self.db.init_tables()
        self.aff = AffectionEngine(self.db)
        self.adv = StrategyAdvisor(None)
        self.engine = TaskEngine(self.db, self.aff, guide_engine=None,
                                 strategy_advisor=self.adv)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _save_persona(self, pid, ib):
        """把画像写到磁盘，供 task_engine.load_persona_from_disk 读取。"""
        import json
        pdir = os.path.join(_PROJECT_ROOT, "data", "persons", pid)
        os.makedirs(pdir, exist_ok=True)
        persona = {"investment_balance": ib}
        with open(os.path.join(pdir, "persona_profile.json"), "w", encoding="utf-8") as f:
            json.dump(persona, f, ensure_ascii=False)

    def test_overinvest_generates_guide_tasks(self):
        """用户超额投入 → 生成引导对方投入类任务。"""
        pid = "t_over"
        self._save_persona(pid, {
            "status": "user_overinvest", "overall_ratio": 2.5,
        })
        tasks = self.engine.generate_main_tasks(pid, stage=5)
        titles = [t["title"] for t in tasks]
        guide = [t for t in titles if ("小忙" in t or "约会" in t or "后撤" in t
                                       or "回报" in t or "富兰克林" in t)]
        self.assertGreaterEqual(len(guide), 3, f"应生成>=3个引导投入任务，实际{guide}")

    def test_task_description_guide_format(self):
        """任务描述使用"通过X，引导对方Y"攻略导向格式。"""
        pid = "t_fmt"
        self._save_persona(pid, {
            "status": "user_overinvest", "overall_ratio": 2.5,
        })
        tasks = self.engine.generate_main_tasks(pid, stage=5)
        titles = [t["title"] for t in tasks]
        guide = [t for t in titles if "小忙" in t]
        self.assertTrue(guide)
        self.assertIn("通过", guide[0])
        self.assertIn("引导", guide[0])

    def test_balanced_no_guide_tasks(self):
        """投入平衡时不生成引导投入类任务。"""
        pid = "t_bal"
        self._save_persona(pid, {
            "status": "balanced", "overall_ratio": 1.0,
        })
        tasks = self.engine.generate_main_tasks(pid, stage=5)
        titles = [t["title"] for t in tasks]
        guide = [t for t in titles if "富兰克林" in t or "后撤" in t or "小忙" in t]
        self.assertEqual(len(guide), 0, f"平衡时不应生成引导投入任务，实际{guide}")


if __name__ == "__main__":
    unittest.main()
