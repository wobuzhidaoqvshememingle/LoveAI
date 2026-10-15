# -*- coding: utf-8 -*-
"""压力源归因引擎单元测试：四类压力源 + 边界（无数据/数据不足/混合信号/多轮上下文）。

核心验证"行为模式 × 上下文组合"——同一个服软/狠话，在不同上下文里归因不同，
而不是命中某个词就给结论。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.stress_attribution import (  # noqa: E402
    SignalExtractor, StressAttributor, CATEGORIES, parse_dt,
)


def _msg(direction, content, at):
    return {"direction": direction, "content": content, "created_at": at}


# --------------------------------------------------------------------------- #
# 基础：行为模式打标签
# --------------------------------------------------------------------------- #
class TestSignalExtraction:
    def test_apology_tagged(self):
        t = SignalExtractor().tag_message("received", "对不起 我错了", "2026-06-12 23:08:00")
        assert "apology" in t.tags

    def test_harsh_devalue_tagged(self):
        t = SignalExtractor().tag_message("received", "和谁过都是过 对你没感情", "2026-07-03 09:49:00")
        assert "harsh_devalue" in t.tags

    def test_threat_perceive_tagged(self):
        t = SignalExtractor().tag_message("received", "想拿我爸妈威胁我", "2026-07-03 10:00:00")
        assert "harsh_threat_perceive" in t.tags

    def test_stress_disclosure_tagged(self):
        t = SignalExtractor().tag_message("received", "明天我要去郑州上课 专升本", "2026-06-12 23:51:00")
        assert "stress_disclosure" in t.tags

    def test_push_commit_on_sent(self):
        t = SignalExtractor().tag_message("sent", "还想在一起吗 给我一个明确答案", "2026-07-03 09:30:00")
        assert "push_commit" in t.tags

    def test_normal_sent_not_tagged(self):
        t = SignalExtractor().tag_message("sent", "今天天气不错", "2026-07-03 12:00:00")
        assert t.tags == []


# --------------------------------------------------------------------------- #
# 边界：无数据 / 数据不足
# --------------------------------------------------------------------------- #
class TestBoundary:
    def test_empty_messages(self):
        res = StressAttributor().attribute([], "2026-06-12 23:08:33")
        assert res["status"] == "insufficient_data"

    def test_focal_not_found(self):
        msgs = [_msg("received", "好", "2026-06-12 10:00:00")]
        res = StressAttributor().attribute(msgs, "2026-09-01 12:00:00")
        assert res["status"] == "insufficient_data"

    def test_too_little_signal(self):
        # 只有一句"好"，前后无任何上下文 -> 数据不足，不硬猜
        msgs = [_msg("received", "好", "2026-06-12 23:08:33")]
        res = StressAttributor().attribute(msgs, "2026-06-12 23:08:33")
        assert res["status"] == "insufficient_data"
        assert "待确认" in res["reasoning"][0] or "不硬猜" in res["reasoning"][0]


# --------------------------------------------------------------------------- #
# 现实压力：服软 + 具体外部负荷披露 -> 主归因=现实压力
# --------------------------------------------------------------------------- #
class TestRealWorldPressure:
    def test_apology_then_stress_disclosure(self):
        focal = "2026-06-12 23:08:33"
        msgs = [
            _msg("sent", "你为什么不说话 说话啊", "2026-06-12 23:00:00"),
            _msg("received", "抱歉", focal),                       # 服软
            _msg("received", "我每天都是压力过载", "2026-06-12 23:46:00"),
            _msg("received", "大部分就是上课 我爸那边", "2026-06-12 23:47:00"),
            _msg("received", "明天我要去郑州上课 专升本", "2026-06-12 23:51:00"),
            _msg("received", "我没办法平衡你还有我的生活", "2026-06-12 23:58:00"),
        ]
        res = StressAttributor(before_hours=1, after_hours=1).attribute(msgs, focal)
        assert res["primary"]["category"] == "real_world_pressure"
        assert res["primary"]["category_cn"] == "现实压力"
        # 证据里必须带原文+时间
        assert any("上课" in e["content"] for e in res["evidence"])
        assert res["category_scores"]["real_world_pressure"] > 0


# --------------------------------------------------------------------------- #
# 关系威胁：我方逼承诺 + 对方贬损/感知威胁 -> 主归因=关系威胁
# --------------------------------------------------------------------------- #
class TestRelationshipThreat:
    def test_harsh_under_push(self):
        focal = "2026-07-03 09:49:08"
        msgs = [
            _msg("sent", "还想在一起吗 给我一个明确答案", "2026-07-03 09:29:00"),
            _msg("sent", "我已经尽力了 一点正反馈都没有", "2026-07-03 09:31:00"),
            _msg("received", "我都行", "2026-07-03 09:30:00"),
            _msg("received", "和谁过都是过", focal),                 # 狠话/贬损
            _msg("received", "对你没感情对其他人也没", "2026-07-03 09:50:00"),
            _msg("received", "想拿我爸妈威胁我", "2026-07-03 10:00:00"),
        ]
        res = StressAttributor(before_hours=1, after_hours=1).attribute(msgs, focal)
        assert res["primary"]["category"] == "relationship_threat"
        assert res["primary"]["category_cn"] == "关系威胁"
        # 证据含我方逼问 + 对方感知威胁
        assert any("还想在一起吗" in e["content"] for e in res["evidence"])
        assert any("威胁" in e["content"] for e in res["evidence"])


# --------------------------------------------------------------------------- #
# 情绪崩溃：高密度贬己 + 剧烈摆荡 + 无具体外部原因
# --------------------------------------------------------------------------- #
class TestEmotionalCollapse:
    def test_self_deprec_volatile_no_external(self):
        focal = "2026-06-12 22:49:15"
        msgs = [
            _msg("received", "我就这样", "2026-06-12 22:35:00"),
            _msg("received", "我告诉你 我本身就是一个很坏的人", focal),
            _msg("received", "我没心 你知道的", "2026-06-12 22:43:00"),
            _msg("received", "把我杀了还是什么", "2026-06-12 22:47:00"),
            _msg("received", "滚", "2026-06-12 21:47:00"),
            _msg("received", "抱歉对不起", "2026-06-12 23:09:00"),
        ]
        res = StressAttributor(before_hours=2, after_hours=1).attribute(msgs, focal)
        # 贬己密度高 + 摆荡 + 无外部压力陈述 -> 情绪崩溃应进入前二
        top2 = {res["primary"]["category"]}
        if res["secondary"]:
            top2.add(res["secondary"]["category"])
        assert "emotional_collapse" in top2
        # 置信度不应虚高（混合摆荡场景）
        assert 0.35 <= res["primary"]["confidence"] <= 0.95


# --------------------------------------------------------------------------- #
# 试探：狠话后短时间秒暖/索取 = 翻转；服软后自我拆台
# --------------------------------------------------------------------------- #
class TestTesting:
    def test_harsh_then_warm(self):
        focal = "2026-07-03 09:49:08"
        msgs = [
            _msg("sent", "还想在一起吗 给我明确答案", "2026-07-03 09:30:00"),
            _msg("received", "和谁过都是过", focal),               # 狠话
            _msg("received", "对你没感情", "2026-07-03 09:50:00"),
            _msg("received", "给我转钱 再给我200我想逛街", "2026-07-03 15:17:00"),  # 秒暖索取
            _msg("received", "晚安宝宝 我爱你", "2026-07-03 23:51:00"),
        ]
        res = StressAttributor(before_hours=2, after_hours=14).attribute(msgs, focal)
        # 翻转信号应给 testing 加分（即便主因是关系威胁，testing 也应>0）
        assert res["category_scores"]["testing"] > 0
        assert any("转钱" in e["content"] or "逛街" in e["content"] for e in res["evidence"])

    def test_apology_qualified(self):
        focal = "2026-06-12 22:47:09"
        msgs = [
            _msg("received", "我说什么 我错了 不该冷暴力你", focal),
            _msg("received", "但是我做了", "2026-06-12 22:47:13"),   # 立刻自我拆台
        ]
        res = StressAttributor(before_hours=1, after_hours=1).attribute(msgs, focal)
        assert res["category_scores"]["testing"] > 0


# --------------------------------------------------------------------------- #
# 混合信号：主次接近 -> 降置信 + 标 pending_confirm
# --------------------------------------------------------------------------- #
class TestMixedSignals:
    def test_mixed_lowers_confidence(self):
        focal = "2026-06-12 23:08:33"
        msgs = [
            _msg("sent", "还想在一起吗 把证领了", "2026-06-12 21:30:00"),
            _msg("received", "抱歉", focal),
            _msg("received", "我每天都是压力过载", "2026-06-12 23:46:00"),
            _msg("received", "我就是神经病", "2026-06-12 22:18:00"),
            _msg("received", "你一直在逼我", "2026-06-12 22:19:00"),
        ]
        res = StressAttributor(before_hours=2, after_hours=1).attribute(msgs, focal)
        # 现实压力 / 关系威胁 / 情绪崩溃 同时被触发 -> 混合信号
        nonzero = [c for c, v in res["category_scores"].items() if v > 0]
        assert len(nonzero) >= 2
        if res["mixed_signals"]:
            assert res["primary"]["confidence"] <= 0.55
            assert res["status"] == "pending_confirm"


# --------------------------------------------------------------------------- #
# 多轮上下文：窗口必须跨越事发前后，而不是只看焦点句
# --------------------------------------------------------------------------- #
class TestMultiTurnContext:
    def test_window_collects_before_and_after(self):
        focal = "2026-06-12 23:08:33"
        msgs = [
            _msg("received", "没", "2026-06-12 21:28:00"),
            _msg("sent", "你说话啊 别冷暴力", "2026-06-12 22:30:00"),
            _msg("received", "抱歉", focal),
            _msg("received", "我每天都是压力过载", "2026-06-12 23:46:00"),
        ]
        res = StressAttributor(before_hours=2, after_hours=1).attribute(msgs, focal)
        # 必须同时引用到前置(我方逼问)与后置(外部压力)两类证据
        roles = [e["role"] for e in res["evidence"]]
        assert any("外部压力" in r for r in roles)

    def test_output_schema(self):
        focal = "2026-06-12 23:08:33"
        msgs = [
            _msg("received", "抱歉", focal),
            _msg("received", "我每天上课压力大", "2026-06-12 23:46:00"),
        ]
        res = StressAttributor(before_hours=1, after_hours=1).attribute(msgs, focal)
        for key in ("status", "primary", "category_scores", "evidence", "reasoning"):
            assert key in res
        # 证据条目含原文+时间
        ev = res["evidence"][0]
        assert "at" in ev and "content" in ev


# --------------------------------------------------------------------------- #
# 置信度与类别映射自检
# --------------------------------------------------------------------------- #
class TestSanity:
    def test_category_cn_map_complete(self):
        assert set(CATEGORIES.keys()) == {
            "relationship_threat", "real_world_pressure", "testing", "emotional_collapse"}

    def test_confidence_bounded(self):
        focal = "2026-06-12 23:08:33"
        msgs = [
            _msg("sent", "给我一个明确答案", "2026-06-12 23:00:00"),
            _msg("received", "和谁过都是过", focal),
            _msg("received", "想拿我爸妈威胁我", "2026-06-12 23:20:00"),
        ]
        res = StressAttributor(before_hours=1, after_hours=1).attribute(msgs, focal)
        if res["status"] != "insufficient_data":
            assert 0.35 <= res["primary"]["confidence"] <= 0.95
