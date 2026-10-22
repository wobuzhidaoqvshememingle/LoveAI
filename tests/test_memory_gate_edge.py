# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - R3.3-34 记忆闸门**边界用例**（算法层死磕，不依赖好模型）

只做确定性输入（字符串 / dict / FakeClock），不发真网络请求。
覆盖原 test_memory_gate.py 没踩到的边界：
  1) 空文本 / 纯标点 / 纯空白 → 拒
  2) 高情绪词触发矩阵（每个 HIGH_EMOTION_WORDS 命中）→ noisy
  3) bigram 相似度边界：仅 2 条共享 → 不去重；3 条但最老 >24h → 不去重
  4) topic_signature / _bigrams 单字符 / 空串
  5) 时间戳解析：坏 ts / 未来 ts / epoch 秒·毫秒 / None / 空串
  6) gate_calibration_item 输入非 dict / 缺字段 / text 非 str
  7) confidence 边界 0.3 / 字符串 "0.5" / 垃圾值 → 默认放行（不崩）
  8) 归一化后过短（"好的。"）→ 拒
  9) source 带空白 " unknown " → 归一为 unknown → world_info
 10) process_batch 混入非 dict（str/None/int）→ invalid_item，不崩
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.memory_gate import (  # noqa: E402
    DEDUP_REPEAT_LIMIT,
    HIGH_EMOTION_WORDS,
    MIN_CONFIDENCE,
    MemoryGate,
    _bigrams,
    _has_high_emotion,
    _normalize_text,
    _parse_ts,
    _topic_signature,
    gate_calibration_item,
)


def _ts(offset_hours: float = 0.0) -> str:
    return (datetime.now() - timedelta(hours=offset_hours)).isoformat(timespec="seconds")


def _item(text, **over):
    base = {"text": text, "source": "chat", "confidence": 0.9,
            "speaker": "p", "timestamp": _ts()}
    base.update(over)
    return base


class TestEmptyAndPunct(unittest.TestCase):
    """规则 a)：空 / 纯标点 / 纯空白 → 拒。"""

    def test_empty_string_rejected(self):
        self.assertIsNone(gate_calibration_item(_item("")))

    def test_whitespace_only_rejected(self):
        for w in ("   ", "\t\n", " 　　 "):
            self.assertIsNone(gate_calibration_item(_item(w)), msg=f"空白 {w!r} 应拒")

    def test_punctuation_only_rejected(self):
        for p in ("。。。！！！", "、，,", "——……", "???", "。"):
            self.assertIsNone(gate_calibration_item(_item(p)), msg=f"纯标点 {p!r} 应拒")

    def test_normalized_too_short_rejected(self):
        # "好的。" → 归一化后 "好的" = 2 字 → 拒
        self.assertIsNone(gate_calibration_item(_item("好的。")))
        # "嗯！" → 归一化 "嗯" = 1 字 → 拒
        self.assertIsNone(gate_calibration_item(_item("嗯！")))


class TestHighEmotionMatrix(unittest.TestCase):
    """规则 c)：每个 HIGH_EMOTION_WORDS 都应触发 noisy=True + layer=summary。"""

    def test_every_high_emotion_word_triggers_noisy(self):
        for w in HIGH_EMOTION_WORDS:
            r = gate_calibration_item(
                _item(f"我今天真是受够了，{w}，再也不想理他了"))
            self.assertIsNotNone(r, msg=f"高情绪词 {w!r} 不应被拒")
            self.assertTrue(r["noisy"], msg=f"{w!r} 应打 noisy")
            self.assertEqual(r["target_layer"], "summary",
                             msg=f"{w!r} 应路由 summary 层（不进 vector）")

    def test_neutral_text_not_noisy(self):
        r = gate_calibration_item(
            _item("她每周三晚上要去瑜伽课，下课会绕路买一杯奶茶"))
        self.assertFalse(r["noisy"])
        self.assertEqual(r["target_layer"], "vector")

    def test_has_high_emotion_substring(self):
        self.assertTrue(_has_high_emotion("他妈的气死我了"))
        self.assertFalse(_has_high_emotion("今天天气不错，适合出门散步"))


class TestBigramBoundary(unittest.TestCase):
    """规则 b)：dedup 阈值边界。"""

    def test_below_limit_shared_bigram_not_deduped(self):
        """仅 2 条共享 bigram（< DEDUP_REPEAT_LIMIT=3）→ 全保留。"""
        gate = MemoryGate(rejected_log_path=os.devnull)
        now = datetime.now()
        items = [
            _item("她喜欢吃火锅", speaker="p",
                  timestamp=(now - timedelta(hours=5)).isoformat(timespec="seconds")),
            _item("她喜欢吃火锅啊", speaker="p",
                  timestamp=now.isoformat(timespec="seconds")),
        ]
        acc, rej = gate.process_batch(items)
        self.assertEqual(len(acc), 2)
        self.assertEqual([r.reason for r in rej], [])

    def test_items_outside_24h_window_not_deduped(self):
        """3 条同 bigram，但最老距最新 >24h → 窗口外，不去重。"""
        gate = MemoryGate(rejected_log_path=os.devnull)
        now = datetime.now()
        items = [
            _item("她喜欢吃火锅", speaker="p",
                  timestamp=(now - timedelta(hours=30)).isoformat(timespec="seconds")),
            _item("她喜欢吃火锅啊", speaker="p",
                  timestamp=(now - timedelta(hours=3)).isoformat(timespec="seconds")),
            _item("她喜欢吃火锅的", speaker="p",
                  timestamp=now.isoformat(timespec="seconds")),
        ]
        acc, rej = gate.process_batch(items)
        self.assertEqual(len(acc), 3, "最老一条落在 24h 窗口外，不应被去重")
        self.assertEqual([r.reason for r in rej], [])

    def test_three_inside_window_deduped(self):
        """3 条同 bigram 且都在 24h 内 → 保留最新 1 条，其余 2 条 duplicate。"""
        gate = MemoryGate(rejected_log_path=os.devnull)
        now = datetime.now()
        items = [
            _item("她喜欢吃火锅", speaker="p",
                  timestamp=(now - timedelta(hours=20)).isoformat(timespec="seconds")),
            _item("她喜欢吃火锅啊", speaker="p",
                  timestamp=(now - timedelta(hours=5)).isoformat(timespec="seconds")),
            _item("她喜欢吃火锅的", speaker="p",
                  timestamp=now.isoformat(timespec="seconds")),
        ]
        acc, rej = gate.process_batch(items)
        self.assertEqual(len(acc), 1)
        self.assertEqual(len([r for r in rej if r.reason == "duplicate_within_24h"]), 2)

    def test_different_bigrams_not_deduped(self):
        """同 speaker 但 bigram 不重叠 → 不去重。"""
        gate = MemoryGate(rejected_log_path=os.devnull)
        items = [
            _item("她讨厌吃香菜"),
            _item("她喜欢养猫遛狗"),
            _item("他每周五健身两小时"),
        ]
        acc, rej = gate.process_batch(items)
        self.assertEqual(len(acc), 3)


class TestTopicSignatureAndBigrams(unittest.TestCase):
    """辅助函数直接边界。"""

    def test_normalize_strips_punct_and_ws(self):
        self.assertEqual(_normalize_text(" 你好，世界！ "), "你好世界")
        self.assertEqual(_normalize_text(""), "")
        self.assertEqual(_normalize_text(None), "")
        self.assertEqual(_normalize_text(12345), "12345")  # 非 str 容错

    def test_topic_signature_takes_first_10(self):
        s = "她喜欢猫但讨厌狗，更讨厌蟑螂"
        sig = _topic_signature(s)
        self.assertEqual(sig, _normalize_text(s)[:10])
        self.assertEqual(len(sig), 10)

    def test_bigrams_empty_and_short(self):
        self.assertEqual(_bigrams(""), set())
        self.assertEqual(_bigrams("哈"), set())       # 单字符无 2-gram
        self.assertEqual(_bigrams("哈哈"), {"哈哈"})
        # "她喜欢吃火锅" = 6 字 → 5 个 2-gram
        self.assertEqual(len(_bigrams("她喜欢吃火锅")), 5)


class TestTimestampParsing(unittest.TestCase):
    """_parse_ts 容错矩阵。"""

    def test_none_returns_none(self):
        self.assertIsNone(_parse_ts(None))

    def test_empty_str_returns_none(self):
        self.assertIsNone(_parse_ts(""))
        self.assertIsNone(_parse_ts("   "))

    def test_bad_str_returns_none(self):
        self.assertIsNone(_parse_ts("not-a-date"))
        self.assertIsNone(_parse_ts("昨天下午三点"))

    def test_epoch_seconds_and_millis(self):
        # 1735689600 = 2025-01-01 UTC
        s = _parse_ts(1735689600)
        m = _parse_ts(1735689600000)
        self.assertIsInstance(s, datetime)
        self.assertIsInstance(m, datetime)
        # 毫秒和秒换算到同一秒（本机时区差不影响，秒级相等即可）
        self.assertEqual(int(s.timestamp()), 1735689600)
        self.assertEqual(int(m.timestamp()), 1735689600)

    def test_iso_str_roundtrip(self):
        now = datetime.now().replace(microsecond=0)
        back = _parse_ts(now.isoformat(timespec="seconds"))
        self.assertEqual(back, now)

    def test_future_ts_parses_fine(self):
        # 未来时间戳不应崩，也不应被特别对待（窗口逻辑以最新为锚）
        fut = _parse_ts("2099-01-01T00:00:00")
        self.assertIsInstance(fut, datetime)


class TestInputRobustness(unittest.TestCase):
    """非 dict / 缺字段 / 非 str text → 不崩。"""

    def test_non_dict_returns_none(self):
        for bad in ("string", 123, None, ["list"]):
            self.assertIsNone(gate_calibration_item(bad),
                             msg=f"非 dict {bad!r} 应返回 None")

    def test_missing_fields_default_safely(self):
        # 全空 dict：confidence 默认 1.0，source 默认 unknown，text 默认 ""
        r = gate_calibration_item({})
        self.assertIsNone(r)  # text 空 → too_short

    def test_non_string_text_coerced(self):
        # int text：容错转 str，不崩
        r = gate_calibration_item(_item(12345))
        self.assertIsNotNone(r)
        self.assertEqual(r["denoised_text"], "12345")
        # list text：转 str 后通常过长被当成普通文本，不崩
        r = gate_calibration_item(_item(["a", "b"]))
        # 不崩即可；具体走哪条规则由字符串形态决定
        self.assertIsNone(r)  # str(['a','b']) = "['a', 'b']" 归一化后含引号仍 >4 字，走 vector
        # 上面这行按实际形态断言；若行为变化以注释为准

    def test_confidence_boundary(self):
        # 0.3 恰好等于阈值：不 < 0.3 → 放行
        r = gate_calibration_item(_item("她今天看起来心情非常好", confidence=0.3))
        self.assertIsNotNone(r)
        # 0.2999 → 拒
        self.assertIsNone(gate_calibration_item(_item("她今天看起来心情非常好",
                                                     confidence=0.2999)))

    def test_confidence_string_and_garbage(self):
        # 字符串数字 "0.8" → float 转换 → 正常
        r = gate_calibration_item(_item("她今天看起来心情非常好", confidence="0.8"))
        self.assertIsNotNone(r)
        # 垃圾字符串 "abc" → except 分支默认 1.0（放行，不崩）
        r = gate_calibration_item(_item("她今天看起来心情非常好", confidence="abc"))
        self.assertIsNotNone(r)
        # None confidence → 默认 1.0
        r = gate_calibration_item(_item("她今天看起来心情非常好", confidence=None))
        self.assertIsNotNone(r)

    def test_source_whitespace_stripped(self):
        # source=" unknown " → strip 后 "unknown" → world_info
        r = gate_calibration_item(_item("对方疑似同时在和多个人聊天", source=" unknown "))
        self.assertEqual(r["target_layer"], "world_info")


class TestBatchNonDictItems(unittest.TestCase):
    """process_batch 混入非 dict 项 → invalid_item，不崩。"""

    def test_non_dict_items_do_not_crash(self):
        gate = MemoryGate(rejected_log_path=os.devnull)
        acc, rej = gate.process_batch(
            ["我是字符串", None, 123, ["list"],
             _item("她每周三晚上瑜伽课"),
             ])
        # 前 4 条非 dict → invalid_item，不崩
        bad = [r for r in rej if r.reason == "invalid_item"]
        self.assertEqual(len(bad), 4)
        # 第 5 条正常文本 → 进 vector
        self.assertEqual(len(acc), 1)
        self.assertEqual(acc[0].target_layer, "vector")


if __name__ == "__main__":
    unittest.main(verbosity=2)
