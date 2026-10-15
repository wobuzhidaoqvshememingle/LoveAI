# -*- coding: utf-8 -*-
"""
NPD/BPD 依恋识别重构（v3）单元测试

针对本次检测架构层重构，逐条断言：
  1. 内容情感退缩：秒回含贬低/打太极 → avoid>0；秒回温暖/中性 → 不计 avoid 也不计 anx。
  2. 秒回假阳性：NPD 式秒回贬低 → 不增 anx（修正"秒回=焦虑"）。
  3. 依恋交叉反馈：自恋/否定现实文本 → avoidance 抬升；BPD"操纵"文本 → 不抬 avoidance。
  4. BPD 焦虑短语 → anxiety 抬升，且主类型为 焦虑型/恐惧型，绝不落回安全型。
  5. 冲突风格：NPD 非建设性；"你能不能别"不计建设性；健康者判建设性。
  6. 情绪模式：analyze() 输出键为 emotion_pattern 且 pattern 非 None。
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.persona_analyzer import PersonaAnalyzer  # noqa: E402

BASE = datetime(2026, 9, 1, 9, 0, 0)


def _m(direction, content, t_offset_min):
    """构造一条消息，t_offset_min 为相对 BASE 的分钟偏移。"""
    return {"direction": direction, "content": content, "msg_type": 1,
            "created_at": (BASE + timedelta(minutes=t_offset_min)).isoformat()}


class _FakeDB:
    def __init__(self, msgs):
        self._m = msgs

    def fetch_all(self, sql, params=None):
        return self._m


def _analyze_full(msgs):
    pa = PersonaAnalyzer(_FakeDB(msgs))
    pa._save_profile = lambda *a, **k: None  # 不落盘
    return pa.analyze("__recog_test__")


def _neutral_thread(n=18, gap=120):
    """生成 n 轮中性、稀疏的往返消息（避免触发 fast/burst/ghosting）。"""
    msgs, t = [], 0
    for _ in range(n):
        msgs.append(_m("sent", "今天忙完了吗", t)); t += gap
        msgs.append(_m("received", "刚忙完，你呢", t)); t += gap
    return msgs


class TestContentWithdrawal(unittest.TestCase):
    """1+2: 内容退缩检测 与 秒回假阳性修正。"""

    def _thread_with_fast_reply(self, fast_reply_content):
        """构造一个以"我发起→对方秒回"为焦点、其余消息中性稀疏的真实小线程。"""
        msgs, t = [], 0
        # 前置中性往返（间隔适中，使中位延迟不触发"慢=回避"）
        for _ in range(6):
            msgs.append(_m("sent", "今天吃饭了吗", t)); t += 10
            msgs.append(_m("received", "吃啦，你呢", t)); t += 10
        # 焦点：我发起，对方 1 分（秒回）
        msgs.append(_m("sent", "你在干嘛，怎么不回我", t)); t += 1
        msgs.append(_m("received", fast_reply_content, t))
        pa = PersonaAnalyzer(_FakeDB(msgs))
        return pa._attachment_behavior_signals(msgs)

    def test_fast_cold_reply_counts_avoid_not_anx(self):
        # NPD 式秒回贬低/打太极：应计 avoid，且不计 anx（修正假阳性）
        sig = self._thread_with_fast_reply("你太敏感了，我怎么知道")
        self.assertGreater(sig["avoid"], 0, "秒回含贬低应计入 avoid")
        self.assertEqual(sig["anx"], 0, "秒回含贬低不得计入 anx")

    def test_fast_warm_reply_neither_avoid_nor_anx(self):
        # 温暖/中性正常秒回：既不是 avoid 也不是 anx
        sig = self._thread_with_fast_reply("辛苦啦，我理解你，我们聊聊，别担心")
        self.assertEqual(sig["avoid"], 0, "温暖秒回不得计 avoid")
        self.assertEqual(sig["anx"], 0, "温暖秒回不得计 anx")

    def test_short_dismissive_reply_counts_avoid(self):
        # 极短打发（行吧/算了）即使秒回也计 avoid
        sig = self._thread_with_fast_reply("行吧")
        self.assertGreater(sig["avoid"], 0)


class TestCrossFeedback(unittest.TestCase):
    """3: 依恋交叉反馈，且保护 BPD（操纵不抬 avoidance）。"""

    def _attach(self, other_extra, my_extra=""):
        msgs = _neutral_thread(n=18)
        pa = PersonaAnalyzer(_FakeDB(msgs))
        other_text = " ".join(m["content"] for m in msgs if m["direction"] == "received")
        my_text = " ".join(m["content"] for m in msgs if m["direction"] == "sent")
        return pa._analyze_attachment(other_text + other_extra, my_text + my_extra, msgs)

    def test_npd_dark_defensive_lifts_avoidance(self):
        base = self._attach("")
        lifted = self._attach(" 什么事比我重要 你自己不记得 你太敏感了")
        self.assertGreater(
            lifted["avoidance_score"], base["avoidance_score"],
            "自恋+否定现实+贬低感受应交叉抬升 avoidance")

    def test_bpd_manipulation_does_not_lift_avoidance(self):
        base = self._attach("")
        # BPD 的"操纵"语：你下来/我就在这等/我就/你最好 —— 是焦虑抗议，不计回避
        same = self._attach(" 你下来 我就在这等 我就 你最好")
        self.assertEqual(
            same["avoidance_score"], base["avoidance_score"],
            "BPD 操纵语不得抬升 avoidance（否则把 BPD 误判成回避）")


class TestBPDAnxiety(unittest.TestCase):
    """4: BPD 焦虑短语 → anxiety 抬升且类型为焦虑型/恐惧型。"""

    def test_bpd_anxious_text_yields_anxious_type(self):
        # 中性打底线程 + BPD 真实焦虑行为（秒回追问 + 时间紧邻的连发抗议）
        msgs = _neutral_thread(n=15)  # 30 条，达稳定评分阈值
        last_t = datetime.fromisoformat(msgs[-1]["created_at"])
        offset = int((last_t - BASE).total_seconds() // 60)
        msgs.append(_m("sent", "我在忙项目，晚点找你", offset))
        msgs.append(_m("received", "你是不是烦我了，你就是不想理我", offset + 1))
        msgs.append(_m("received", "你现在才回，你根本就不在乎我", offset + 2))
        # 追加更多抛弃恐惧/反复确认语料（来自真实 BPD 文本）
        extra = (" 你不会离开我吧 你是不是喜欢上别人 你走了我真的活不下去 "
                 "你每次都这样 你跟他们一样 你又不想跟我说话 你能过来陪我吗 "
                 "你千万不要走 你答应我")
        pa = PersonaAnalyzer(_FakeDB(msgs))
        other_text = " ".join(m["content"] for m in msgs if m["direction"] == "received") + extra
        my_text = " ".join(m["content"] for m in msgs if m["direction"] == "sent")
        res = pa._analyze_attachment(other_text, my_text, msgs)
        self.assertGreaterEqual(res["anxiety_score"], 55,
                                f"BPD 焦虑分应≥55，实为 {res['anxiety_score']}")
        self.assertIn(res["primary_type"], ("焦虑型", "恐惧型"),
                      f"BPD 主类型应为焦虑型/恐惧型，实为 {res['primary_type']}")


class TestConflictStyle(unittest.TestCase):
    """5: 冲突风格误判修复。"""

    def _cs(self, other_text, my_text=""):
        return PersonaAnalyzer(None)._analyze_conflict_style(other_text, my_text, [])

    def test_npd_not_constructive(self):
        cs = self._cs(
            "你能不能别像个侦探一样盯着我 算了 跟你说不清楚 "
            "你自己不记得 你太敏感了 行吧 你说什么就是什么")
        self.assertNotEqual(cs["primary_key"], "constructive",
                            f"NPD 不得判为建设性，实为 {cs['primary']}")

    def test_neng_wo_bie_not_constructive(self):
        # "你能不能别…"（拒绝）不得计建设性分
        cs = self._cs("你能不能别像个侦探")
        self.assertEqual(cs["scores"].get("constructive", 0), 0,
                         "建设性用法之外的'能不能别'不得计入 constructive")

    def test_healthy_is_constructive(self):
        cs = self._cs("我理解你 我们聊聊 一起解决", my_text="我们可以慢慢来")
        self.assertEqual(cs["primary_key"], "constructive",
                         f"健康者应判建设性，实为 {cs['primary']}")


class TestEmotionPatternKey(unittest.TestCase):
    """6: analyze() 输出 emotion_pattern 且 pattern 非 None。"""

    def test_emotion_pattern_present_not_none(self):
        msgs = _neutral_thread(n=18)
        for m in msgs:
            if m["direction"] == "received":
                m["content"] = "今天好开心呀，哈哈"
        profile = _analyze_full(msgs)
        self.assertIn("emotion_pattern", profile)
        self.assertIsNotNone(profile["emotion_pattern"].get("pattern"))
        # 旧错键 emotional_pattern 不应作为主键存在
        self.assertNotIn("emotional_pattern", profile)


if __name__ == "__main__":
    unittest.main()
