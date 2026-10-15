# -*- coding: utf-8 -*-
"""
P1 单测：撤回消息标记 + 画像口径文档化（撤回排除 / 口径字段 / 修复率可辩护）。

覆盖：
  1. 撤回标记与排除逻辑
     - withdrawn_count 统计 is_withdrawn=1 的消息
     - message_count 保留全量（含撤回），other/my_message_count 走非撤回子集
     - 撤回消息不参与冲突/情感分析（撤回的"分手"不计入冲突事件）
  2. 口径字段存在性
     - profile.metric_definitions 含 relationship_satisfaction / conflict_repair，
       且每项都有 definition/basis/confidence/note
  3. 修复率定义可辩护
     - repair_window_days 可配置（窗口内修复=成功，超窗口=未修复）
     - conflict_words / repair_words 词表可注入
  4. 增量导入：store.is_revokemsg 判定（仅 revokemsg XML 算撤回）
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
from services.collectors.store import is_revokemsg  # noqa: E402

BASE = datetime(2026, 9, 1, 9, 0, 0)


def _m(direction, content, minute, withdrawn=False, msg_type="1"):
    return {
        "direction": direction,
        "content": content,
        "msg_type": msg_type,
        "created_at": (BASE + timedelta(minutes=minute)).isoformat(),
        "is_withdrawn": 1 if withdrawn else 0,
    }


def _neutral_chat(n=20, start=0):
    """生成 n 条中性日常对话，避免 analyze() 走数据不足默认分支。"""
    msgs, t = [], start
    for i in range(n):
        msgs.append(_m("sent", f"今天在忙项目，{i}点进度还行", t)); t += 20
        msgs.append(_m("received", f"辛苦了，注意休息，{i}点别太累", t)); t += 25
    return msgs


class FakeDB:
    """忽略 SQL，直接返回预置消息（与 test_persona_v2 同模式）。"""

    def __init__(self, msgs):
        self._m = msgs

    def fetch_all(self, sql, params=None):
        return self._m


def _analyze(msgs):
    pa = PersonaAnalyzer(FakeDB(msgs))
    pa._save_profile = lambda *a, **k: None  # 不落盘
    return pa.analyze("__withdraw_test__")


class TestWithdrawnMarking(unittest.TestCase):
    """撤回消息标记与排除。"""

    def test_withdrawn_count_and_full_message_count(self):
        """message_count=全量(含撤回)；withdrawn_count=撤回数；算术守恒。"""
        msgs = _neutral_chat(10)
        msgs.append(_m("sent", '<sysmsg type="revokemsg">你撤回了一条消息</sysmsg>',
                       9999, withdrawn=True))
        prof = _analyze(msgs)
        self.assertNotIn("error", prof)
        self.assertEqual(prof["withdrawn_count"], 1)
        # 全量 = other + my + withdrawn
        self.assertEqual(
            prof["message_count"],
            prof["other_message_count"] + prof["my_message_count"] + prof["withdrawn_count"],
        )
        # message_count 包含撤回那条
        self.assertEqual(prof["message_count"], len(msgs))

    def test_withdrawn_conflict_excluded(self):
        """撤回消息即使含冲突词（分手），也不计入冲突事件。"""
        msgs = _neutral_chat(10)
        # 撤回的"分手吧"——若不被排除，会触发 1 次冲突
        msgs.append(_m("sent", "我们分手吧，不想过了", 9999, withdrawn=True))
        prof = _analyze(msgs)
        self.assertNotIn("error", prof)
        self.assertEqual(prof["withdrawn_count"], 1)
        self.assertEqual(prof["conflict_repair"]["conflict_count"], 0)

    def test_no_withdrawn_default_zero(self):
        """无撤回消息时 withdrawn_count=0，且不影响既有计数。"""
        msgs = _neutral_chat(5)
        prof = _analyze(msgs)
        self.assertEqual(prof["withdrawn_count"], 0)
        self.assertEqual(
            prof["message_count"],
            prof["other_message_count"] + prof["my_message_count"],
        )


class TestMetricDefinitions(unittest.TestCase):
    """口径字段存在性。"""

    def test_metric_definitions_structure(self):
        """metric_definitions 两项四要素齐全。"""
        prof = _analyze(_neutral_chat(15))
        md = prof["metric_definitions"]
        self.assertIn("relationship_satisfaction", md)
        self.assertIn("conflict_repair", md)
        for block in (md["relationship_satisfaction"], md["conflict_repair"]):
            for key in ("definition", "basis", "confidence", "note"):
                self.assertTrue(block.get(key), f"metric_definitions 缺 {key}")
            self.assertIn(block["confidence"], ("low", "medium", "high"))

    def test_satisfaction_score_unchanged_by_withdrawn(self):
        """撤回排除不改变满意度公式（撤回 blob 不含中文词，分值应与全量一致）。"""
        base = _neutral_chat(15)
        prof_no_wd = _analyze(base)
        base.append(_m("sent", '<sysmsg type="revokemsg">blob</sysmsg>', 9999, withdrawn=True))
        prof_wd = _analyze(base)
        self.assertEqual(
            prof_no_wd["relationship_satisfaction"]["score"],
            prof_wd["relationship_satisfaction"]["score"],
        )


class TestConflictRepairDefensible(unittest.TestCase):
    """修复率口径可辩护：窗口可配置 + 词表可注入。"""

    def setUp(self):
        self.pa = PersonaAnalyzer(db=None)

    def _two_msgs(self, conflict_min=0, repair_min=10):
        return [
            {"direction": "sent", "content": "算了就这样吧，随便",
             "created_at": (BASE + timedelta(minutes=conflict_min)).isoformat(),
             "is_withdrawn": 0},
            {"direction": "received", "content": "对不起，我错了，别生气了",
             "created_at": (BASE + timedelta(minutes=repair_min)).isoformat(),
             "is_withdrawn": 0},
        ]

    def test_window_days_configurable(self):
        """冲突后 1 天修复：3 天窗口算修复；0.01 天窗口算未修复。"""
        # 冲突 T，修复 T+1 天
        msgs = [
            {"direction": "sent", "content": "算了就这样吧",
             "created_at": BASE.isoformat(), "is_withdrawn": 0},
            {"direction": "received", "content": "对不起我错了",
             "created_at": (BASE + timedelta(days=1)).isoformat(), "is_withdrawn": 0},
        ]
        r_wide = self.pa._analyze_conflict_repair(msgs, "", "", repair_window_days=3.0)
        self.assertEqual(r_wide["conflict_count"], 1)
        self.assertEqual(r_wide["repair_count"], 1)
        self.assertEqual(r_wide["repair_rate"], 100.0)

        r_narrow = self.pa._analyze_conflict_repair(msgs, "", "", repair_window_days=0.01)
        self.assertEqual(r_narrow["conflict_count"], 1)
        self.assertEqual(r_narrow["repair_count"], 0)
        self.assertEqual(r_narrow["repair_rate"], 0.0)
        # 结果里应回带窗口参数，供 metric_definitions 引用
        self.assertEqual(r_narrow["repair_window_days"], 0.01)

    def test_word_lists_injectable(self):
        """注入自定义冲突/修复词表，命中自定义词才算事件。"""
        msgs = [
            {"direction": "sent", "content": "我方自定义冲突标记XYZ",
             "created_at": BASE.isoformat(), "is_withdrawn": 0},
            {"direction": "received", "content": "我方自定义修复标记ABC",
             "created_at": (BASE + timedelta(minutes=10)).isoformat(), "is_withdrawn": 0},
        ]
        r = self.pa._analyze_conflict_repair(
            msgs, "", "",
            conflict_words=["自定义冲突标记"],
            repair_words=["自定义修复标记"],
        )
        self.assertEqual(r["conflict_count"], 1)
        self.assertEqual(r["repair_count"], 1)
        self.assertEqual(r["repair_rate"], 100.0)

    def test_default_window_is_three_days(self):
        """默认窗口应为 3 天。"""
        r = self.pa._analyze_conflict_repair(self._two_msgs(), "", "")
        self.assertEqual(r["repair_window_days"], 3.0)


class TestIsRevokemsgHelper(unittest.TestCase):
    """增量导入撤回判定。"""

    def test_detects_xml_revokemsg(self):
        self.assertTrue(is_revokemsg('<sysmsg type="revokemsg"><content>你撤回了一条消息</content></sysmsg>'))
        self.assertTrue(is_revokemsg('b\'(\\xb5/\\xfd <sysmsg type="revokemsg">...'))

    def test_plaintext_not_revokemsg(self):
        """纯文本'你撤回了一条消息'无 XML，不算（与正式库标记口径一致）。"""
        self.assertFalse(is_revokemsg("你撤回了一条消息"))
        self.assertFalse(is_revokemsg("你好呀，在干嘛"))
        self.assertFalse(is_revokemsg(""))
        self.assertFalse(is_revokemsg(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
