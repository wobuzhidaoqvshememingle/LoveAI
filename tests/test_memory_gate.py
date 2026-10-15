# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - R3.3-34 记忆闸门（校准数据去噪）单元测试

覆盖：
  a) 太短 / 语气词 → 拒绝
  b) 同 speaker 24h 内同主题重复 ≥3 → 去重，保留最新
  c) 高情绪（脏话）→ noisy=True，target_layer=summary（不进 vector）
  d) source=unknown → target_layer=world_info（不进 vector）
  e) confidence < 0.3 → 拒绝
  + MemoryService.guarded_write_calibration 端到端
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

from services.memory_gate import (  # noqa: E402
    gate_calibration_item,
    MemoryGate,
    MIN_CONFIDENCE,
)
from models.database import Database  # noqa: E402
from services.memory_service import MemoryService  # noqa: E402


def _ts(offset_hours: float = 0.0) -> str:
    return (datetime.now() - timedelta(hours=offset_hours)).isoformat(timespec="seconds")


class TestSingleItemGate(unittest.TestCase):
    """单条闸门规则。"""

    def test_too_short_rejected(self) -> None:
        """a) 太短（<4字）→ None。"""
        self.assertIsNone(gate_calibration_item({"text": "嗯", "source": "chat",
                                                "confidence": 0.9, "speaker": "p",
                                                "timestamp": _ts()}))
        self.assertIsNone(gate_calibration_item({"text": "哦", "source": "chat",
                                                "confidence": 0.9, "speaker": "p",
                                                "timestamp": _ts()}))

    def test_interjection_rejected(self) -> None:
        """a) 纯语气词"好的/哈哈" → None。"""
        for w in ("好的", "哈哈", "好吧", "可以", "是的"):
            self.assertIsNone(gate_calibration_item(
                {"text": w, "source": "chat", "confidence": 0.9,
                 "speaker": "p", "timestamp": _ts()}),
                msg=f"应拒绝语气词 {w}",
            )

    def test_low_confidence_rejected(self) -> None:
        """e) confidence<0.3 → None。"""
        r = gate_calibration_item({
            "text": "她今天好像心情不好，脸色很差",
            "source": "chat", "confidence": 0.1,
            "speaker": "p", "timestamp": _ts(),
        })
        self.assertIsNone(r)

    def test_normal_text_accepted_vector(self) -> None:
        """默认 → vector 层。"""
        r = gate_calibration_item({
            "text": "她喜欢吃火锅，每周至少吃两次",
            "source": "chat", "confidence": 0.8,
            "speaker": "p", "timestamp": _ts(),
        })
        self.assertIsNotNone(r)
        self.assertEqual(r["target_layer"], "vector")
        self.assertFalse(r["noisy"])

    def test_profanity_routes_to_summary(self) -> None:
        """c) 脏话 → noisy=True，target_layer=summary（不进 vector）。"""
        r = gate_calibration_item({
            "text": "他妈的，我真是受够了这种态度",
            "source": "chat", "confidence": 0.9,
            "speaker": "p", "timestamp": _ts(),
        })
        self.assertIsNotNone(r)
        self.assertTrue(r["noisy"])
        self.assertEqual(r["target_layer"], "summary")

    def test_unknown_source_routes_to_world_info(self) -> None:
        """d) source=unknown → world_info（不进 vector）。"""
        r = gate_calibration_item({
            "text": "对方疑似同时在和多个人聊天",
            "source": "unknown", "confidence": 0.7,
            "speaker": "unknown", "timestamp": _ts(),
        })
        self.assertIsNotNone(r)
        self.assertEqual(r["target_layer"], "world_info")


class TestDedup(unittest.TestCase):
    """b) 跨条去重。"""

    def test_repeated_same_topic_keeps_latest(self) -> None:
        gate = MemoryGate(rejected_log_path=os.devnull)
        now = datetime.now()
        items = [
            {"text": "她喜欢吃火锅", "source": "chat", "confidence": 0.9,
             "speaker": "p1", "timestamp": (now - timedelta(hours=20)).isoformat(timespec="seconds")},
            {"text": "她喜欢吃火锅啊", "source": "chat", "confidence": 0.9,
             "speaker": "p1", "timestamp": (now - timedelta(hours=10)).isoformat(timespec="seconds")},
            {"text": "她喜欢吃火锅的", "source": "chat", "confidence": 0.9,
             "speaker": "p1", "timestamp": (now - timedelta(hours=2)).isoformat(timespec="seconds")},
            {"text": "她喜欢吃火锅没错", "source": "chat", "confidence": 0.9,
             "speaker": "p1", "timestamp": now.isoformat(timespec="seconds")},
        ]
        accepted, rejected = gate.process_batch(items)
        # 4 条同主题，去重后应只保留最新 1 条
        self.assertEqual(len(accepted), 1)
        self.assertIn("火锅没错", accepted[0].denoised_text)
        # 其余 3 条标记为 duplicate
        dup = [r for r in rejected if r.reason == "duplicate_within_24h"]
        self.assertEqual(len(dup), 3)

    def test_different_speakers_not_deduped(self) -> None:
        gate = MemoryGate(rejected_log_path=os.devnull)
        items = [
            {"text": "她喜欢吃火锅", "source": "chat", "confidence": 0.9,
             "speaker": "p1", "timestamp": _ts(20)},
            {"text": "她喜欢吃火锅", "source": "chat", "confidence": 0.9,
             "speaker": "p2", "timestamp": _ts(10)},
            {"text": "她喜欢吃火锅", "source": "chat", "confidence": 0.9,
             "speaker": "p2", "timestamp": _ts(5)},
            {"text": "她喜欢吃火锅", "source": "chat", "confidence": 0.9,
             "speaker": "p3", "timestamp": _ts(0)},
        ]
        accepted, _ = gate.process_batch(items)
        # 不同 speaker 不去重，应全部通过
        self.assertEqual(len(accepted), 4)


class TestBatchMixed(unittest.TestCase):
    """混合场景：一条 batch 里同时命中 a/c/e 多条规则。"""

    def test_mixed_batch(self) -> None:
        gate = MemoryGate(rejected_log_path=os.devnull)
        items = [
            # a) 太短
            {"text": "嗯", "source": "chat", "confidence": 0.9,
             "speaker": "p", "timestamp": _ts()},
            # e) 低置信
            {"text": "她好像对我有点意思", "source": "chat", "confidence": 0.1,
             "speaker": "p", "timestamp": _ts()},
            # c) 脏话 → summary
            {"text": "我操，他又骗我", "source": "chat", "confidence": 0.9,
             "speaker": "p", "timestamp": _ts()},
            # d) unknown source → world_info
            {"text": "据观察对方作息不规律", "source": "unknown", "confidence": 0.8,
             "speaker": "unknown", "timestamp": _ts()},
            # 正常 → vector
            {"text": "她每周三晚上要去瑜伽课", "source": "chat", "confidence": 0.9,
             "speaker": "p", "timestamp": _ts()},
        ]
        accepted, rejected = gate.process_batch(items)
        # 应拒绝 2 条（太短 + 低置信）
        reasons = sorted(r.reason for r in rejected)
        self.assertIn("too_short_or_interjection", reasons)
        self.assertIn("low_confidence", reasons)
        # 应接受 3 条
        self.assertEqual(len(accepted), 3)
        layers = sorted(a.target_layer for a in accepted)
        self.assertEqual(layers, ["summary", "vector", "world_info"])


class TestGuardedHookInMemoryService(unittest.TestCase):
    """端到端：MemoryService.guarded_write_calibration 真的写库。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_memgate_")
        self.db = Database(os.path.join(self.tmpdir, "g.db"))
        self.db.init_tables()
        self.mem = MemoryService(self.db, config=None, rag=None, signals=None)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_guarded_write_writes_correct_layers(self) -> None:
        items = [
            # 脏话 → summary 层
            {"text": "我操他又骗我", "source": "chat", "confidence": 0.9,
             "speaker": "p", "timestamp": _ts()},
            # 正常 → vector 层
            {"text": "她喜欢吃火锅每周两次", "source": "chat", "confidence": 0.9,
             "speaker": "p", "timestamp": _ts()},
            # unknown source → world_info
            {"text": "对方作息不规律", "source": "unknown", "confidence": 0.8,
             "speaker": "unknown", "timestamp": _ts()},
            # 太短 → 拒绝
            {"text": "嗯", "source": "chat", "confidence": 0.9,
             "speaker": "p", "timestamp": _ts()},
            # 低置信 → 拒绝
            {"text": "她可能有点喜欢我", "source": "chat", "confidence": 0.1,
             "speaker": "p", "timestamp": _ts()},
        ]
        res = self.mem.guarded_write_calibration("p1", items)
        self.assertEqual(res["accepted"], 3)
        self.assertEqual(res["rejected"], 2)
        self.assertEqual(len(res["written_ids"]), 3)

        # 校验落库层
        rows = self.db.fetch_all(
            "SELECT layer, content FROM long_term_memory WHERE person_id='p1'"
        )
        layer_map = {r["layer"]: r["content"] for r in rows}
        self.assertIn("summary", layer_map)
        self.assertIn("vector", layer_map)
        self.assertIn("world_info", layer_map)
        self.assertIn("操", layer_map["summary"])
        self.assertIn("火锅", layer_map["vector"])
        self.assertIn("作息", layer_map["world_info"])

    def test_guarded_write_empty(self) -> None:
        res = self.mem.guarded_write_calibration("p1", [])
        self.assertEqual(res["accepted"], 0)
        self.assertEqual(res["rejected"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
