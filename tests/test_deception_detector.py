# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - DeceptionDetector 单元测试（反伪装审计系统）

覆盖：
  1. 基线建立（数据充足 / 数据不足）
  2. 异常检测（突然变好 / 突然变差 / 偶然偏离 / 模式转变）
  3. 露馅事件识别（深夜酒后 / 争吵失控 / 第三方负面 / 利益反应 四类场景）
  4. 偶然失误 vs 伪装破裂区分
  5. 时间维度信任（短/中/长期加权）
  6. 目的达成突变检测
  7. 风险评分（低 / 高 等级）
  8. 3 种人设风险评分对比（长期伪装者 / 偶然失误者 / 正常波动者）
  9. 数据不足时不评估
 10. 输出措辞谨慎（不使用"骗子"等定性标签）
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
from services.deception_detector import DeceptionDetector  # noqa: E402


def _m(content: str, direction: str, days_ago: float, hour: int = 10) -> dict:
    """构造一条消息：content / direction / days_ago 天前的指定时刻。"""
    dt = (datetime.now() - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0
    )
    return {
        "content": content,
        "direction": direction,
        "created_at": dt.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _warm_baseline() -> dict:
    """构造一份"热情稳定"的人工基线，供 detect_anomalies 直接使用。"""
    return {
        "available": True,
        "reply_lag": {"mean_min": 10.0, "std_min": 5.0, "median_min": 8.0,
                      "sample_count": 60},
        "emotion": {"positive_freq": 1.5, "negative_freq": 0.1, "density": 2.0},
        "data_span_days": 365,
        "message_count": 200,
    }


class TestDeceptionDetectorBaseline(unittest.TestCase):
    """基线建立测试。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_decept_")
        self.db = Database(os.path.join(self.tmpdir, "test.db"))
        self.db.init_tables()
        self.det = DeceptionDetector(self.db)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_baseline_sufficient(self) -> None:
        """数据跨 12 个月 → 基线可用。"""
        msgs = []
        for d in range(30, 365, 7):
            msgs.append(_m("想你，今天开心吗", "received", d))
            msgs.append(_m("我也想你", "sent", d + 0.1))
        bl = self.det.build_baseline("p1", msgs)
        self.assertTrue(bl["available"])
        self.assertGreater(bl["data_span_days"], 90)
        self.assertIn("reply_lag", bl)
        self.assertIn("stages", bl)

    def test_baseline_insufficient(self) -> None:
        """数据仅 30 天 → 数据不足，不评估。"""
        msgs = [_m("你好", "received", d) for d in range(0, 30)]
        bl = self.det.build_baseline("p2", msgs)
        self.assertFalse(bl["available"])
        self.assertIn("数据不足", bl["reason"])


class TestDeceptionAnomalies(unittest.TestCase):
    """异常偏离检测测试。"""

    def setUp(self) -> None:
        self.det = DeceptionDetector(None)

    def test_sudden_cold_mode_shift(self) -> None:
        """近期回复极慢+情感极低，持续>2周无理由 → sudden_cold 模式转变。"""
        baseline = _warm_baseline()
        recent = []
        # 最近 20 天：我发消息后对方隔 2000 分钟才回，且几乎无情感词
        for d in range(0, 20):
            recent.append(_m("在吗", "sent", d, hour=10))
            recent.append(_m("嗯，忙", "received", d + 0.5, hour=20))
        anomalies = self.det.detect_anomalies("p", recent, baseline)
        cold = [a for a in anomalies if a["type"] == "sudden_cold"]
        self.assertTrue(cold, "应检测到突然变差")
        self.assertEqual(cold[0]["classification"], "模式转变")

    def test_sudden_warmth(self) -> None:
        """近期情感频率显著升高且持续>3天 → sudden_warmth。"""
        baseline = _warm_baseline()
        recent = []
        for d in range(0, 5):
            recent.append(_m("宝贝我好想你，最爱你了，今天特别想你，爱你",
                             "received", d, hour=21))
        anomalies = self.det.detect_anomalies("p", recent, baseline)
        warm = [a for a in anomalies if a["type"] == "sudden_warmth"]
        self.assertTrue(warm, "应检测到突然变好")

    def test_accidental_deviation_with_reason(self) -> None:
        """有合理理由（生病）的偏离 → 偶然偏离，不计入风险。"""
        baseline = _warm_baseline()
        recent = []
        for d in range(0, 20):
            recent.append(_m("在吗", "sent", d))
            # 回复慢，但带合理理由
            recent.append(_m("我发烧了，刚睡，不好意思回晚了", "received", d + 0.2))
        anomalies = self.det.detect_anomalies("p", recent, baseline)
        for a in anomalies:
            self.assertEqual(a["classification"], "偶然偏离")


class TestSlipEvents(unittest.TestCase):
    """露馅事件识别测试。"""

    def setUp(self) -> None:
        self.det = DeceptionDetector(None)

    def test_late_night_slip(self) -> None:
        """凌晨2点的消息 → late_night 场景。"""
        msgs = [_m("其实我觉得咱们也就那样吧", "received", 5, hour=2)]
        events = self.det.detect_slip_events(msgs)
        self.assertTrue(any(e["event_type"] == "late_night" for e in events))

    def test_conflict_outburst(self) -> None:
        """吵架时说狠话 → conflict_outburst。"""
        msgs = [_m("你真烦，滚，不可理喻", "received", 5)]
        events = self.det.detect_slip_events(msgs)
        self.assertTrue(any(e["event_type"] == "conflict_outburst" for e in events))

    def test_thirdparty_badmouthing(self) -> None:
        """背后评价第三方 → thirdparty_badmouthing。"""
        msgs = [_m("我那个闺蜜特别不靠谱，心机得很", "received", 5)]
        events = self.det.detect_slip_events(msgs)
        self.assertTrue(any(e["event_type"] == "thirdparty_badmouthing" for e in events))

    def test_interest_reaction(self) -> None:
        """涉及金钱 → interest_reaction。"""
        msgs = [_m("这个月工资花完了，你先借我点周转一下", "received", 5)]
        events = self.det.detect_slip_events(msgs)
        self.assertTrue(any(e["event_type"] == "interest_reaction" for e in events))

    def test_accidental_vs_break(self) -> None:
        """事后道歉修正 → accidental；事后否认转移 → possible_break。"""
        # 偶然失误：凌晨失态说错话后立刻道歉修正
        msgs_acc = [
            _m("其实也就那样吧", "received", 5, hour=2),
            _m("对不起我刚才说错话了，我不是那个意思，别往心里去", "sent", 4.9),
        ]
        ev_acc = self.det.detect_slip_events(msgs_acc)
        self.assertTrue(ev_acc, "凌晨失态应识别为 late_night 露馅")
        self.assertEqual(ev_acc[0]["classification"], "accidental")

        # 伪装破裂：露馅后否认转移
        msgs_break = [
            _m("我其实根本没把你当回事", "received", 5, hour=3),
            _m("我什么时候说过？你想多了，不说这个了", "sent", 4.9),
        ]
        ev_br = self.det.detect_slip_events(msgs_break)
        self.assertTrue(ev_br)
        self.assertEqual(ev_br[0]["classification"], "possible_break")


class TestTemporalTrust(unittest.TestCase):
    """时间维度信任累积测试。"""

    def setUp(self) -> None:
        self.det = DeceptionDetector(None)

    def test_temporal_weights(self) -> None:
        """短/中/长期三窗口均有返回，长期权重最高。"""
        msgs = []
        # 短期(近30天)
        for d in range(0, 30, 3):
            msgs.append(_m("想你", "received", d))
        # 中期(90-300天)
        for d in range(100, 300, 7):
            msgs.append(_m("想你", "received", d))
        # 长期(>400天)
        for d in range(400, 700, 14):
            msgs.append(_m("想你", "received", d))
        t = self.det.calculate_temporal_trust("p", msgs)
        self.assertIn("short_term_rate", t)
        self.assertIn("mid_term_rate", t)
        self.assertIn("long_term_rate", t)
        self.assertGreaterEqual(t["temporal_trust_score"], 0)

    def test_purpose_achievement_shift(self) -> None:
        """目的节点后情感密度骤降 → 检测到目的达成突变。"""
        msgs = []
        # 节点前 30 天：高情感密度
        for d in range(31, 60):
            msgs.append(_m("宝贝我好想你，爱你，特别在乎你", "received", d))
        # 节点（在一起）
        msgs.append(_m("那我们在一起吧，确认关系", "received", 30))
        # 节点后 30 天：情感密度骤降
        for d in range(1, 29):
            msgs.append(_m("嗯", "received", d))
        t = self.det.calculate_temporal_trust("p", msgs)
        self.assertTrue(t["purpose_achievement_shifts"],
                        "应检测到目的达成突变")


class TestRiskAssessment(unittest.TestCase):
    """风险评分测试。"""

    def setUp(self) -> None:
        self.det = DeceptionDetector(None)

    def test_low_risk(self) -> None:
        """无异常、无露馅、稳定 → 低风险。"""
        baseline = _warm_baseline()
        risk = self.det.assess_risk("p", baseline, anomalies=[], events=[])
        self.assertTrue(risk["data_sufficient"])
        self.assertLess(risk["risk_score"], 60)
        self.assertIn(risk["risk_level"], ("低", "中"))

    def test_high_risk(self) -> None:
        """模式转变 + 多次伪装破裂露馅 + 目的突变 → 高风险(>=60)。"""
        baseline = _warm_baseline()
        anomalies = [
            {"type": "sudden_cold", "classification": "模式转变",
             "duration_days": 20},
            {"type": "purpose_shift", "classification": "模式转变"},
        ]
        events = [
            {"classification": "possible_break"},
            {"classification": "possible_break"},
        ]
        risk = self.det.assess_risk("p", baseline, anomalies, events)
        self.assertGreaterEqual(risk["risk_score"], 60,
                                f"风险分应>=60，实际{risk['risk_score']}")
        self.assertIn(risk["risk_level"], ("高", "极高"))

    def test_insufficient_data_no_assess(self) -> None:
        """基线不可用 → data_sufficient=False，不评分。"""
        risk = self.det.assess_risk("p", {"available": False}, [], [])
        self.assertFalse(risk["data_sufficient"])
        self.assertEqual(risk["risk_score"], 0)
        self.assertIn("数据不足", risk["recommendation"])


class TestThreePersonas(unittest.TestCase):
    """三种人设综合对比测试。"""

    def setUp(self) -> None:
        self.det = DeceptionDetector(None)

    def _build_faker(self) -> dict:
        """人设1：前12月完美，第13月达成目的，第14-18月突然冷淡。"""
        msgs = []
        # 0-12月（365天前到30天前）：热情、秒回
        for d in range(390, 30, -5):
            msgs.append(_m("宝贝我好想你，今天也特别在乎你，爱你", "received", d))
            msgs.append(_m("今天忙什么", "sent", d - 0.1))
            msgs.append(_m("刚忙完，马上回你啦，想你", "received", d - 0.15))
        # 第13月目的节点（确认关系）
        msgs.append(_m("我们在一起吧，确认关系", "received", 30))
        # 最近30天：突然冷淡、回复极慢
        for d in range(0, 30):
            msgs.append(_m("在吗，想你了", "sent", d))
            msgs.append(_m("嗯，忙", "received", d + 0.8))
        # 冷淡期露馅 + 事后否认转移话题（伪装破裂）
        msgs.append(_m("我觉得也就那样", "received", 10, hour=2))
        msgs.append(_m("我什么时候说过？你想多了，不说这个了", "sent", 9.9))
        # 第二次露馅：背后贬低第三方后又否认
        msgs.append(_m("其实我那闺蜜特别不靠谱，心机得很", "received", 8, hour=3))
        msgs.append(_m("我可没这么说，你别脑补", "sent", 7.9))
        return self.det.analyze("faker", msgs)

    def _build_accidental(self) -> dict:
        """人设2：18月整体一致，仅1-2次偶然失误后道歉修正。"""
        msgs = []
        for d in range(540, 0, -5):
            msgs.append(_m("想你，今天过得怎么样", "received", d))
        # 一次失误 + 道歉修正
        msgs.append(_m("这事你办得真不太行", "received", 20))
        msgs.append(_m("对不起我刚才说错话了，我不是那个意思", "sent", 19.9))
        return self.det.analyze("acc", msgs)

    def _build_normal(self) -> dict:
        """人设3：18月正常起伏，工作忙时回复慢但长期一致。"""
        msgs = []
        for d in range(540, 0, -5):
            # 随机起伏：约1/3时间带加班理由
            text = "想你了，今天聊得开心" if d % 3 else "最近加班，回得慢，抱歉"
            msgs.append(_m(text, "received", d))
        return self.det.analyze("normal", msgs)

    def test_faker_high_risk(self) -> None:
        rep = self._build_faker()
        self.assertTrue(rep["data_sufficient"])
        self.assertGreaterEqual(rep["risk_score"], 60,
                                f"伪装者风险分应>=60，实际{rep['risk_score']}")

    def test_accidental_low_risk(self) -> None:
        rep = self._build_accidental()
        if rep["data_sufficient"]:
            self.assertLess(rep["risk_score"], 60,
                            f"偶然失误者风险分应<60，实际{rep['risk_score']}")

    def test_normal_low_risk(self) -> None:
        rep = self._build_normal()
        if rep["data_sufficient"]:
            self.assertLess(rep["risk_score"], 60,
                            f"正常波动者风险分应<60，实际{rep['risk_score']}")


class TestWording(unittest.TestCase):
    """输出措辞谨慎性测试。"""

    def test_no_derogatory_labels(self) -> None:
        """所有报告文字不得出现"骗子"等定性标签。"""
        det = DeceptionDetector(None)
        baseline = _warm_baseline()
        risk = det.assess_risk("p", baseline,
                               anomalies=[{"type": "sudden_cold",
                                           "classification": "模式转变"}],
                               events=[{"classification": "possible_break"}])
        blob = json_dump_safe(risk)
        self.assertNotIn("骗子", blob)
        self.assertNotIn("诈骗", blob)


def json_dump_safe(obj) -> str:
    """把报告对象序列化为字符串用于敏感词检查。"""
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


if __name__ == "__main__":
    unittest.main()
