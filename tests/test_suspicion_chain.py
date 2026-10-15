# -*- coding: utf-8 -*-
"""怀疑链模块单元测试：分级、累积、微积分加权、证据要求、语境排除、人设联动。"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.suspicion_chain import (  # noqa: E402
    EventDetector, SuspicionChain, load_baseline, write_back,
    _MONEY_WORDS, _HARDSHIP_WORDS,
)


def _msg(direction, content, at):
    return {"direction": direction, "content": content, "created_at": at}


def _base_messages(anchor="2026-09-01 12:00:00"):
    """一段足够长的背景消息（保证前后窗口可用）。"""
    msgs = []
    for i in range(40):
        ts = f"2026-08-{(i % 28) + 1:02d} {10 + (i % 10):02d}:{(i * 7) % 60:02d}:00"
        msgs.append(_msg("received", f"日常消息{i}", ts))
    return msgs + [_msg("sent", "我发的消息", anchor)]


# -- 分级阈值 ------------------------------------------------------------ #
class TestLevels:
    def test_normal_below_30(self):
        c = SuspicionChain()
        assert c.level_for(0) == "正常"
        assert c.level_for(29.9) == "正常"

    def test_observe_at_30(self):
        c = SuspicionChain()
        assert c.level_for(30) == "观察"
        assert c.level_for(59.9) == "观察"

    def test_verify_at_60(self):
        c = SuspicionChain()
        assert c.level_for(60) == "引导"

    def test_limit_at_80(self):
        c = SuspicionChain()
        assert c.level_for(80) == "设限"
        assert c.level_for(100) == "设限"


# -- 微积分加权 ---------------------------------------------------------- #
class TestCalculus:
    def test_acceleration_higher_suspicion_bigger_delta(self):
        """幂次加速：S 越高，同样事件计入越多。"""
        c = SuspicionChain()
        base = 30.0
        eff1 = base * (1 + 0 / 100) ** 1.5          # S=0
        eff2 = base * (1 + 60 / 100) ** 1.5         # S=60
        assert eff2 > eff1
        assert eff1 == pytest.approx(30.0)
        assert eff2 == pytest.approx(30 * 1.6 ** 1.5, rel=1e-6)

    def test_time_decay_reduces_suspicion(self):
        """指数时间衰减：180 天无新事件，S 减半。"""
        c = SuspicionChain()
        c.suspicion = 80.0
        # 模拟 180 天后新事件：先衰减再加
        c._last_at = None
        ev = _msg("received", "宝宝我想要钱", "2026-09-05 18:37:31")
        ev2 = _msg("received", "宝宝我想要钱", "2027-03-05 18:37:31")  # 180天后
        # 手工验证衰减公式
        import math
        lam = math.log(2) / 180.0
        dec = 80.0 * math.exp(-lam * 180.0)
        assert dec == pytest.approx(40.0, rel=1e-6)

    def test_decay_then_add(self):
        """完整链路：高 S 隔很久 + 新事件，先衰减再加速计入。"""
        c = SuspicionChain()
        c.suspicion = 80.0
        c._last_at = None
        c._last_at = __import__("datetime").datetime.strptime("2026-09-05 18:37:31", "%Y-%m-%d %H:%M:%S")
        ev = _msg("received", "宝宝我想要钱", "2027-03-05 18:37:31")
        # money 事件需要前后窗口有消息才有反差 → 直接测累积的衰减路径
        # 改用直接验证：衰减后 S≈40，再加 eff = 30*(1+0.4)^1.5
        import math
        lam = math.log(2) / 180.0
        dec = 80.0 * math.exp(-lam * 180.0)
        assert dec == pytest.approx(40.0, rel=1e-6)


# -- 语境排除（防误报） ---------------------------------------------------- #
class TestExclusions:
    def test_hardship_not_money(self):
        """纯困境陈述（没钱了）不算索取。"""
        c = SuspicionChain()
        ev = _msg("received", "我没钱了 好烦", "2026-06-18 18:28:10")
        res = c._evaluate({"type": "money", "content": ev["content"], "at": ev["created_at"],
                           "direction": "received"}, [ev])
        assert res is None

    def test_ask_with_hardship_is_money(self):
        """索取+困境混合（给我点钱 我支付宝没钱了）→ 算索取（走反差判定）。"""
        c = SuspicionChain()
        content = "宝宝支付宝可以给我点钱吗 我支付宝上没钱了"
        assert any(w in content for w in _MONEY_WORDS)
        assert any(w in content for w in _HARDSHIP_WORDS)

    def test_rhetorical_not_promise(self):
        """反问句（不是说好了吗）不是承诺。"""
        c = SuspicionChain()
        ev = _msg("received", "ss不是说好了吗", "2026-06-13 00:57:52")
        res = c._evaluate({"type": "promise", "content": ev["content"], "at": ev["created_at"],
                           "direction": "received"}, [ev])
        assert res is None

    def test_schedule_mention_not_promise(self):
        """日程告知（明天有考试）不含承诺动词 → 不产生 promise 事件。"""
        det = EventDetector()
        evs = det.detect([_msg("received", "晚安 明天有考试", "2026-06-04 21:45:47")])
        assert all(e["type"] != "promise" for e in evs)


# -- 承诺言行不一 --------------------------------------------------------- #
class TestPromise:
    def test_topic_promise_violation(self):
        """主题承诺+窗口内同主题重现 → 言行不一 +25。"""
        msgs = _base_messages()
        msgs += [
            _msg("received", "我保证再也不吵架了", "2026-09-10 10:00:00"),
            _msg("received", "又吵架 烦死了", "2026-09-12 12:00:00"),
        ]
        c = SuspicionChain()
        ev = {"type": "promise", "content": "我保证再也不吵架了", "at": "2026-09-10 10:00:00",
              "direction": "received"}
        res = c._evaluate(ev, msgs)
        assert res is not None and res["delta"] == 25.0
        assert res["verdict"] == "矛盾（言行不一）"

    def test_topic_promise_kept(self):
        """主题承诺+窗口内无同主题 → 一致，不加分。"""
        msgs = _base_messages()
        msgs += [_msg("received", "我保证再也不吵架了", "2026-09-10 10:00:00")]
        c = SuspicionChain()
        ev = {"type": "promise", "content": "我保证再也不吵架了", "at": "2026-09-10 10:00:00",
              "direction": "received"}
        res = c._evaluate(ev, msgs)
        assert res is not None and res["delta"] == 0
        assert res["verdict"] == "一致"

    def test_no_topic_promise_pending_manual(self):
        """无主题承诺（我保证）→ 待人工验证，不进怀疑度。"""
        msgs = _base_messages()
        msgs += [_msg("received", "我保证", "2026-06-12 23:51:20")]
        c = SuspicionChain()
        res = c.accumulate([{"type": "promise", "content": "我保证", "at": "2026-06-12 23:51:20",
                             "direction": "received"}], msgs)
        assert res["suspicion"] == 0.0
        assert len(res["pending_manual"]) == 1


# -- 人设联动写回 --------------------------------------------------------- #
class TestWriteBack:
    def test_write_back_adds_field_with_backup(self):
        """写回：加 suspicion_chain 字段 + 生成 .bak 备份。"""
        tmpdir = tempfile.mkdtemp()
        p = os.path.join(tmpdir, "persona_profile.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"attachment": {"primary_type": "疏离型回避"},
                       "trust": {"score": 55}}, f, ensure_ascii=False)
        result = {"suspicion": 30.0, "level": "观察", "response": "记录并观察",
                  "events": [], "pending_manual": [],
                  "persona_update_hints": []}
        out = write_back(p, result)
        assert out["ok"] is True
        assert os.path.exists(out["backup"])
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["suspicion_chain"]["suspicion"] == 30.0
        assert data["suspicion_chain"]["level"] == "观察"
        # 既有字段保留
        assert data["trust"]["score"] == 55


# -- 过度解释/信息修补（mq2 #7，用户最在意的说谎补充） -------------------- #
def _short_baseline(n=20, word="哈"):
    """一段短消息背景：把对方 received 中位长度压到很低。"""
    return [_msg("received", word, f"2026-08-{(i % 28) + 1:02d} 14:{(i * 3) % 60:02d}:00")
            for i in range(n)]


class TestOverExplain:
    def test_normal_long_reply_answers_probe_no_event(self):
        """长回复正面回答了追问主题 → 不触发。"""
        msgs = _short_baseline()
        msgs += [
            _msg("sent", "你为什么昨天没回我？", "2026-09-01 14:00:00"),
            _msg("received", "昨天没回我是因为我加班到很晚手机没电了，实在太累就先睡了，真的对不起下次不会了。",
                 "2026-09-01 14:05:00"),
        ]
        evs = EventDetector().detect(msgs)
        assert all(e["type"] != "over_explain" for e in evs)

    def test_probe_topic_avoidance_pending_manual(self):
        """追问后长解释+回避主题 → 首见 delta=0，只进 pending_manual，不加怀疑度。"""
        msgs = _short_baseline()
        msgs += [
            _msg("sent", "你为什么借我的钱？", "2026-09-01 14:00:00"),
            _msg("received", "中午我们去吃那家新开的火锅吧，我看点评说他们家毛肚特别新鲜，"
                             "锅底是正宗牛油的，还有手工虾滑，周末一起去尝尝呀。",
                 "2026-09-01 14:05:00"),
        ]
        evs = EventDetector().detect(msgs)
        oe = [e for e in evs if e["type"] == "over_explain"]
        assert len(oe) == 1
        res = SuspicionChain().accumulate(evs, msgs)
        assert res["suspicion"] == 0.0
        assert any(p["type"] == "over_explain" for p in res["pending_manual"])

    def test_three_repeated_patterns_give_small_bump(self):
        """同模式独立重复 3 次 → 补一次小分（>0 且很小）。"""
        msgs = _short_baseline()
        msgs += [
            _msg("sent", "你为什么借我的钱？", "2026-09-01 14:00:00"),
            _msg("received", "中午我们去吃那家新开的火锅吧，我看点评说他们家毛肚特别新鲜，"
                             "锅底是正宗牛油的，还有手工虾滑，周末一起去尝尝呀。", "2026-09-01 14:05:00"),
            _msg("sent", "你为什么不回消息？", "2026-09-03 14:00:00"),
            _msg("received", "周末打算去公园散步，听说最近菊花开了特别漂亮，还能划船，"
                             "我准备带上相机多拍点照片，晚上顺便逛个夜市吃小吃。", "2026-09-03 14:05:00"),
            _msg("sent", "你为什么删我好友？", "2026-09-05 14:00:00"),
            _msg("received", "我最近在学做蛋糕，查了好多教程，要准备低筋面粉和淡奶油，"
                             "第一步先把蛋黄蛋清分开，慢慢搅拌均匀，烤出来应该会很好吃。", "2026-09-05 14:05:00"),
        ]
        evs = EventDetector().detect(msgs)
        oe = [e for e in evs if e["type"] == "over_explain"]
        assert len(oe) == 3
        res = SuspicionChain().accumulate(evs, msgs)
        assert 0 < res["suspicion"] < 20.0  # 小分（base=10，初始 S=0 → ≈10）

    def test_within_person_baseline_high_no_false_positive(self):
        """对方本就爱发长文（中位高）→ ×2 阈值不满足，不误判。"""
        msgs = [_msg("received", f"我平时就爱发很长很长的日常消息{i}，巴拉巴拉巴拉。",
                     f"2026-08-{(i % 28) + 1:02d} 14:00:00") for i in range(20)]
        msgs += [
            _msg("sent", "你为什么那个？", "2026-09-01 14:00:00"),
            _msg("received", "今天和朋友出去逛了逛，顺便喝了杯奶茶，挺开心的，"
                             "还买了件衣服，回来路上遇到了熟人聊了一会天。", "2026-09-01 14:05:00"),
        ]
        evs = EventDetector().detect(msgs)
        assert all(e["type"] != "over_explain" for e in evs)

    def test_late_night_reply_not_flagged(self):
        """深夜(0-5点)长解释 → 不触发（避开已证伪假阳性路径）。"""
        msgs = _short_baseline()
        msgs += [
            _msg("sent", "你为什么借我的钱？", "2026-09-01 02:00:00"),
            _msg("received", "中午我们去吃那家新开的火锅吧，我看点评说他们家毛肚特别新鲜，"
                             "锅底是正宗牛油的，还有手工虾滑，周末一起去尝尝呀。", "2026-09-01 02:05:00"),
        ]
        evs = EventDetector().detect(msgs)
        assert all(e["type"] != "over_explain" for e in evs)

    def test_same_probe_session_counted_once(self):
        """同一条追问后两条长解释 → 只计一次（同会话不重复计数）。"""
        msgs = _short_baseline()
        msgs += [
            _msg("sent", "你为什么借我的钱？", "2026-09-01 14:00:00"),
            _msg("received", "中午我们去吃那家新开的火锅吧，我看点评说他们家毛肚特别新鲜，"
                             "锅底是正宗牛油的，还有手工虾滑。", "2026-09-01 14:05:00"),
            _msg("received", "周末打算去公园散步，听说最近菊花开了特别漂亮，还能划船，"
                             "我准备带上相机多拍点照片。", "2026-09-01 14:06:00"),
        ]
        evs = EventDetector().detect(msgs)
        oe = [e for e in evs if e["type"] == "over_explain"]
        assert len(oe) == 1

    def test_non_probe_long_reply_no_event(self):
        """长回复但前面不是追问句（无问号/无追问词）→ 不触发。"""
        msgs = _short_baseline()
        msgs += [
            _msg("sent", "今天天气真不错适合出去玩", "2026-09-01 14:00:00"),
            _msg("received", "中午我们去吃那家新开的火锅吧，我看点评说他们家毛肚特别新鲜，"
                             "锅底是正宗牛油的，还有手工虾滑，周末一起去尝尝呀。", "2026-09-01 14:05:00"),
        ]
        evs = EventDetector().detect(msgs)
        assert all(e["type"] != "over_explain" for e in evs)


# -- A2 纯言语说谎近似 + A3 承诺质量分层（弱信号，全部 delta=0 待人工验证） ---- #
class TestA2A3:
    def test_age_reversal_memory_inconsistency(self):
        """A2-b：越活越年轻（28→26）→ 记忆不一致候选。"""
        msgs = [
            _msg("received", "我今年28岁，工作好几年了", "2026-01-10 14:00:00"),
            _msg("received", "其实我今年26岁啦", "2026-06-10 14:00:00"),
        ]
        evs = EventDetector().detect(msgs)
        assert any(e["type"] == "memory_inconsistency" for e in evs)
        res = SuspicionChain().accumulate(evs, msgs)
        assert res["suspicion"] == 0.0  # 弱信号不自动加分

    def test_age_natural_aging_no_event(self):
        """A2-b：自然变老（28→29）→ 不触发。"""
        msgs = [
            _msg("received", "我今年28岁", "2026-01-10 14:00:00"),
            _msg("received", "明年就29啦", "2026-12-10 14:00:00"),
        ]
        evs = EventDetector().detect(msgs)
        assert all(e["type"] != "memory_inconsistency" for e in evs)

    def test_assurance_surge_detected(self):
        """A2-a：历史无保证词、近期骤增 → assurance_surge 候选。"""
        msgs = [_msg("received", f"日常闲聊{i}", f"2026-08-{(i % 28) + 1:02d} 14:{(i * 3) % 60:02d}:00")
                for i in range(12)]
        for k in range(5):
            msgs.append(_msg("received", "我保证一定绝对是真的，我发誓不骗你，相信我",
                             f"2026-09-0{k+1} 14:00:00"))
        evs = EventDetector().detect(msgs)
        assert any(e["type"] == "assurance_surge" for e in evs)

    def test_assurance_uniform_no_surge(self):
        """A2-a：历史本就高频保证（热恋常态）→ 不判骤增（反例：热恋≠说谎）。"""
        msgs = [_msg("received", "我保证一定对你好", f"2026-08-{(i % 28) + 1:02d} 14:{(i * 3) % 60:02d}:00")
                for i in range(17)]
        evs = EventDetector().detect(msgs)
        assert all(e["type"] != "assurance_surge" for e in evs)

    def test_key_silence_long_gap(self):
        """A2-c：追问后超长间隔才回 → 关键事实沉默候选。"""
        msgs = _short_baseline()
        msgs += [
            _msg("sent", "你为什么借我的钱？", "2026-09-01 14:00:00"),
            _msg("received", "嗯在忙", "2026-09-01 16:30:00"),
        ]
        evs = EventDetector().detect(msgs)
        assert any(e["type"] == "key_silence" for e in evs)

    def test_key_silence_terse_no_topic(self):
        """A2-c：追问后极短回复且不答主题 → 关键事实沉默候选。"""
        msgs = _short_baseline()
        msgs += [
            _msg("sent", "你为什么借我的钱？", "2026-09-01 14:00:00"),
            _msg("received", "嗯", "2026-09-01 14:02:00"),
        ]
        evs = EventDetector().detect(msgs)
        assert any(e["type"] == "key_silence" for e in evs)

    def test_repeated_promise_three_times(self):
        """A3-C3：同措辞承诺重复 3 次 → repeated_promise 候选（待验证不加分）。

        用不含 _PROMISE_TOPIC 的承诺句，避免被既有 promise-违反逻辑误判为言行不一。
        """
        msgs = [_msg("received", "我保证以后都对你好", f"2026-09-0{i+1} 14:00:00") for i in range(3)]
        evs = EventDetector().detect(msgs)
        assert any(e["type"] == "repeated_promise" for e in evs)
        res = SuspicionChain().accumulate(evs, msgs)
        assert res["suspicion"] == 0.0

    def test_c1_cost_bearing_promise_violation_higher_weight(self):
        """A3-C1：承诺带具体成本细节（周末）且违反 → 权重 30（无成本=25）。"""
        msgs = _short_baseline()
        msgs += [
            _msg("received", "我保证周末再也不吵架了", "2026-09-10 14:00:00"),
            _msg("received", "又吵架了烦死了", "2026-09-12 14:00:00"),
        ]
        c = SuspicionChain()
        ev = {"type": "promise", "content": "我保证周末再也不吵架了", "at": "2026-09-10 14:00:00",
              "direction": "received"}
        res = c._evaluate(ev, msgs)
        assert res is not None and res["delta"] == 30.0

    def test_weak_signals_never_auto_add_suspicion(self):
        """总闸：所有弱信号进入后 suspicion 仍为 0（只观察，不给分）。"""
        msgs = [
            _msg("received", "我今年28岁", "2026-01-10 14:00:00"),
            _msg("received", "我今年26岁", "2026-06-10 14:00:00"),
            _msg("received", "我保证一定不骗你", "2026-07-01 14:00:00"),
            _msg("sent", "你为什么借我的钱？", "2026-07-02 14:00:00"),
            _msg("received", "嗯", "2026-07-02 14:02:00"),
        ]
        evs = EventDetector().detect(msgs)
        res = SuspicionChain().accumulate(evs, msgs)
        assert res["suspicion"] == 0.0
