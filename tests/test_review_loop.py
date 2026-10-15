# -*- coding: utf-8 -*-
"""ReviewLoop 复盘回流引擎单元测试。"""
import os
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.review_loop import (  # noqa: E402
    ReviewLoop, _emotion_bucket, _direction_from_reply, _has_action)


class TestReviewHelpers(unittest.TestCase):
    def test_emotion_bucket(self):
        self.assertEqual(_emotion_bucket("开心"), "positive")
        self.assertEqual(_emotion_bucket("happy"), "positive")
        self.assertEqual(_emotion_bucket("难过"), "negative")
        self.assertEqual(_emotion_bucket("angry"), "angry")
        self.assertEqual(_emotion_bucket("冷淡"), "avoid")
        self.assertEqual(_emotion_bucket(""), "neutral")
        self.assertEqual(_emotion_bucket(None), "neutral")
        self.assertEqual(_emotion_bucket("未知乱码"), "neutral")

    def test_direction_from_reply(self):
        self.assertEqual(_direction_from_reply("哦"), "evade")
        self.assertEqual(_emotion_bucket and _direction_from_reply("嗯"), "evade")
        self.assertEqual(_direction_from_reply(""), "evade")
        self.assertEqual(_direction_from_reply(None), "evade")
        self.assertEqual(_direction_from_reply("你今天怎么这么晚才回我？吃饭了吗"), "engage")
        self.assertEqual(_direction_from_reply("我今天去了那家店，味道还不错，下次一起？"), "engage")

    def test_has_action(self):
        self.assertTrue(_has_action("明天周末一起吃饭，我请"))
        self.assertTrue(_has_action("晚上六点门口见"))
        self.assertFalse(_has_action("哈哈哈好的"))
        self.assertFalse(_has_action(""))


class TestReviewLoop(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_review_")
        self.db = Database(os.path.join(self.tmpdir, "t.db"))
        self.db.init_tables()
        self.db.execute(
            "INSERT INTO persons (id, nickname, gender) VALUES (:id,:n,:g)",
            {"id": "p1", "n": "苏晚", "g": "女"})
        self.rl = ReviewLoop(self.db)

    def _positive_prediction(self):
        return {
            "emotion": "开心",
            "inner_thought": "觉得有意思",
            "possible_replies": ["哈哈你怎么这么有趣，后来呢？", "真的吗，然后怎么了？"],
            "action": "会主动找话题，明天继续聊",
            "confidence": 0.8,
        }

    def test_record_prediction(self):
        rid = self.rl.record_prediction("p1", "在吗", self._positive_prediction())
        self.assertGreater(rid, 0)
        rec = self.db.fetch_one(
            "SELECT status FROM prediction_records WHERE id=:id", {"id": rid})
        self.assertEqual(rec["status"], "pending")

    def test_record_invalid(self):
        self.assertEqual(self.rl.record_prediction("", "x", self._positive_prediction()), -1)
        self.assertEqual(self.rl.record_prediction("p1", "x", "notdict"), -1)

    def test_settled_hit(self):
        self.rl.record_prediction("p1", "在吗", self._positive_prediction())
        out = self.rl.settle_latest("p1", {
            "emotion": "开心",
            "reply": "哈哈你真有意思，后来怎么样了？快说说",
        })
        self.assertTrue(out["settled"])
        self.assertEqual(out["hits"]["emotion"], 1.0)
        self.assertEqual(out["hits"]["direction"], 1.0)
        self.assertEqual(out["validity_score"], 1.0)

    def test_settled_miss_emotion(self):
        self.rl.record_prediction("p1", "在吗", self._positive_prediction())
        out = self.rl.settle_latest("p1", {
            "emotion": "愤怒",
            "reply": "你别烦我，我现在很生气",
        })
        self.assertTrue(out["settled"])
        self.assertEqual(out["hits"]["emotion"], 0.0)
        self.assertLess(out["validity_score"], 0.6)

    def test_settled_no_reply(self):
        self.rl.record_prediction("p1", "在吗", self._positive_prediction())
        out = self.rl.settle_latest("p1", {"no_reply": True})
        self.assertTrue(out["settled"])
        self.assertEqual(out["actual"]["direction"], "evade")

    def test_settle_without_pending(self):
        out = self.rl.settle_latest("p1", {"reply": "好", "emotion": "平静"})
        self.assertFalse(out["settled"])

    def test_rolling_and_calibration(self):
        # 首轮：无样本
        empty = self.rl.rolling_validity("p1")
        self.assertEqual(empty["n"], 0)
        cal0 = self.rl.calibration("p1")
        self.assertFalse(cal0["available"])
        self.assertEqual(cal0["sim_weight"], 0.4)

        # 两条命中
        for actual in (
                {"emotion": "开心", "reply": "哈哈你真有趣，后来呢，快讲讲呀"},
                {"emotion": "开心", "reply": "真的吗？那你明天还来吗，一起呀"}):
            self.rl.record_prediction("p1", "在吗", self._positive_prediction())
            self.rl.settle_latest("p1", actual)

        stat = self.rl.rolling_validity("p1")
        self.assertEqual(stat["n"], 2)
        self.assertEqual(stat["validity"], 1.0)
        self.assertIsNotNone(stat["ci"])
        cal = self.rl.calibration("p1")
        self.assertTrue(cal["available"])
        self.assertEqual(cal["band"], "可信")
        self.assertGreater(cal["sim_weight"], 0.6)

    def test_mark_expired(self):
        self.rl.record_prediction("p1", "在吗", self._positive_prediction())
        n = self.rl.mark_pending_expired("p1")
        self.assertGreaterEqual(n, 1)
        rec = self.db.fetch_one(
            "SELECT status FROM prediction_records WHERE person_id=:p", {"p": "p1"})
        self.assertEqual(rec["status"], "expired")


if __name__ == "__main__":
    unittest.main()
