# -*- coding: utf-8 -*-
"""证据锚定六层引擎（services/evidence_engine）单元测试。

逐层覆盖：
  1. raw_stream：时间归档 + 分段（时间断点）；
  2. sequence_features：词→消息→段→关系层次聚合；
  3. emotion_channel：逐句情绪标注；
  4. evidence_anchor：有证据→标签输出且绑定证据句；无证据→标签被抑制；
  5. dyadic_pattern：追逃 / 推拉识别；
  6. confidence：随样本量变化且不越诚实天花板。
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.evidence_engine.raw_stream import RawStream  # noqa: E402
from services.evidence_engine.emotion_channel import EmotionChannel  # noqa: E402
from services.evidence_engine.sequence_features import SequenceFeatures  # noqa: E402
from services.evidence_engine.evidence_anchor import EvidenceAnchor  # noqa: E402
from services.evidence_engine.dyadic_pattern import DyadicPattern  # noqa: E402
from services.evidence_engine.confidence import Confidence  # noqa: E402
from services.evidence_engine.engine import EvidenceProfileEngine  # noqa: E402

BASE = datetime(2026, 9, 1, 9, 0, 0)


def _m(direction, content, t_offset_min):
    return {"direction": direction, "content": content, "msg_type": 1,
            "created_at": (BASE + timedelta(minutes=t_offset_min)).isoformat()}


class TestRawStream(unittest.TestCase):
    def test_time_order_and_index(self):
        msgs = [_m("sent", "b", 30), _m("received", "a", 0)]  # 乱序输入
        out = RawStream().build(msgs)
        contents = [m["content"] for m in out["messages"]]
        self.assertEqual(contents, ["a", "b"])           # 按时间升序
        self.assertEqual([m["idx"] for m in out["messages"]], [0, 1])

    def test_segment_break_on_gap(self):
        msgs = [_m("received", "第一段", 0),
                _m("received", "第二段", 60 * 10)]         # 10 小时间隔
        out = RawStream().build(msgs)
        self.assertGreaterEqual(len(out["segments"]), 2)


class TestEmotionChannel(unittest.TestCase):
    def test_per_message_emotion(self):
        msgs = [{"idx": 0, "direction": "received", "content": "今天好开心，哈哈", "dt": None, "ts": None},
                {"idx": 1, "direction": "received", "content": "真的很生气", "dt": None, "ts": None}]
        EmotionChannel().annotate(msgs)
        self.assertEqual(msgs[0]["polarity"], "pos")
        self.assertEqual(msgs[1]["polarity"], "neg")


class TestSequenceFeatures(unittest.TestCase):
    def test_hierarchical_aggregation(self):
        msgs = [_m("received", "你太敏感了，跟你说不清楚", 0)]
        stream = RawStream().build(msgs)
        seq = SequenceFeatures().build(stream["messages"], stream["segments"])
        self.assertIn("跟你说不清楚", seq["relationship"]["avoid_hits"])
        self.assertIn("贬低感受", seq["relationship"]["defensive_hits"])
        self.assertEqual(seq["relationship"]["n_other"], 1)


class TestEvidenceAnchor(unittest.TestCase):
    def _run(self, other_contents):
        msgs = []
        for i, c in enumerate(other_contents):
            msgs.append(_m("received", c, i * 5))
        stream = RawStream().build(msgs)
        EmotionChannel().annotate(stream["messages"])
        seq = SequenceFeatures().build(stream["messages"], stream["segments"])
        return EvidenceAnchor().bind(seq["relationship"], seq["msg_vectors"],
                                     stream["messages"], 0.6)

    def test_label_output_when_evidence_present(self):
        out = self._run(["你是不是烦我了", "你根本就不在乎我"])
        self.assertIn("依恋-焦虑倾向", out["labels"])
        ev = out["labels"]["依恋-焦虑倾向"]["evidence"]
        self.assertTrue(ev and ev[0]["quote"], "证据句应绑定原文")
        self.assertIsNotNone(ev[0]["ts"])

    def test_label_suppressed_without_evidence(self):
        out = self._run(["你好呀，天气不错", "吃饭了吗"])
        self.assertNotIn("依恋-焦虑倾向", out["labels"])
        self.assertNotIn("暗黑-自恋倾向", out["labels"])
        self.assertIn("依恋-焦虑倾向", out["suppressed"])


class TestDyadicPattern(unittest.TestCase):
    def test_pursue_withdraw(self):
        msgs = [_m("sent", "你为什么不回我，说清楚", 0),
                _m("received", "算了，跟你说不清楚", 1)]
        pats = DyadicPattern().detect(RawStream().build(msgs)["messages"])
        pw = [p for p in pats if "追逃" in p["pattern"]]
        self.assertTrue(pw and pw[0]["count"] >= 1)

    def test_push_pull(self):
        msgs = [_m("received", "你是我遇到过最好的人", 0),
                _m("received", "你走了我真的活不下去，你跟他们一样", 1)]
        pats = DyadicPattern().detect(RawStream().build(msgs)["messages"])
        pp = [p for p in pats if "推拉" in p["pattern"]]
        self.assertTrue(pp and pp[0]["count"] >= 1)


class TestConfidence(unittest.TestCase):
    def test_scales_with_samples_and_ceiling(self):
        low = Confidence().calibrate(50)
        high = Confidence().calibrate(500, span_days=40, n_disagreement=4, repeat_patterns=6)
        self.assertGreater(high["value"], low["value"])
        self.assertLessEqual(high["value"], Confidence.HARD_CEILING)


class TestEngineEndToEnd(unittest.TestCase):
    def test_engine_runs_and_binds_evidence(self):
        msgs = [_m("sent", "你为什么不回我", 0),
                _m("received", "你是不是烦我了，你根本不在乎我", 1),
                _m("received", "你走了我真的活不下去", 2)]
        out = EvidenceProfileEngine(db=None).analyze("__t__", messages=msgs)
        self.assertIn("labels", out)
        self.assertIn("confidence", out)
        self.assertTrue(out["labels"]["依恋-焦虑倾向"]["evidence"])


if __name__ == "__main__":
    unittest.main()
