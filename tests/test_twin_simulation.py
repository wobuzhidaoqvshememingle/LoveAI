# -*- coding: utf-8 -*-
"""tests/test_twin_simulation.py — 拟真增强 E9-E13 离线确定性测试。

跑法：.venv\\Scripts\\python.exe -m pytest tests\\test_twin_simulation.py -q -p no:cacheprovider --tb=short
全程离线：只读本 data/persons/t_*/persona_profile.json，不联网、不依赖真实 LLM/DB。
"""
from __future__ import annotations

import os
import sys

import pytest

# 工程根（tests/ 的上一级）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from services.twin_sim_pipeline import (  # noqa: E402
    TwinSimPipeline, Y_KEYS, DELAY_BUCKETS,
)

DATA_DIR = os.path.join(_ROOT, "data")
PERSONAS = ["t_healthy", "t_npd", "t_bpd"]


def _pipe(pid: str) -> TwinSimPipeline:
    return TwinSimPipeline(pid, data_dir=DATA_DIR)


# --------------------------------------------------------------------- #
# 基础：三人设各跑多轮事件，三段输出齐全 + 分布合法
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("pid", PERSONAS)
def test_three_stage_output_and_distribution(pid):
    p = _pipe(pid)
    state = p.new_state()
    msgs = [
        ("周末要不要一起吃饭？", {"type": "user_message", "elapsed_minutes": 40}),
        ("你怎么一直不回我？", {"type": "follow_up", "elapsed_minutes": 120}),
        (None, {"type": "life_tick", "elapsed_minutes": 300}),  # 独立生活推进
    ]
    for msg, ev in msgs:
        r = p.run_turn(pid, user_message=msg, event=ev, state=state)
        state = r["state"]

        # E9 叙事补写
        assert "narrative" in r and r["narrative"]["view"] == "third_person_narrator"
        assert r["narrative"]["text"]

        # E10 结构化决定
        d = r["decision"]
        for k in ("seen", "mode", "emotion_params", "tool_calls", "reason"):
            assert k in d, f"decision 缺键 {k}"
        assert d["mode"] in ("reply", "silent", "delay", "deflect", "proactive", "idle")

        # E10 渲染
        assert "render" in r and "message" in r["render"]

        # 反应分布：Y1-Y7 全键、和为 1、显式 Y4/6/7 合计、标注未校准
        dist = r["reaction_distribution"]
        P = dist["P"]
        for y in Y_KEYS:
            assert y in P, f"分布缺 {y}"
        assert abs(sum(P.values()) - 1.0) < 0.01, f"分布和不为1: {P}"
        # 显式"不回/回避 Y4/6/7 合计"
        assert abs(dist["no_reply_avoid"] - (P["Y4"] + P["Y6"] + P["Y7"])) < 1e-6
        # 延迟六桶全键
        for b in DELAY_BUCKETS:
            assert b in dist["delay_buckets"]
        # 2-3 条风格示例
        assert 2 <= len(dist["examples"]) <= 3
        # 未校准标注
        assert dist["calibration"] == "先验·未校准"


def test_personas_sharply_distinct_offline():
    """三人设即使全离线也区分鲜明：同一句邀约，渲染句不同，分布倾向不同。"""
    renders, no_reply = {}, {}
    for pid in PERSONAS:
        p = _pipe(pid)
        r = p.run_turn(pid, user_message="周末要不要一起吃饭？",
                       event={"type": "user_message", "elapsed_minutes": 30},
                       state=p.new_state())
        renders[pid] = r["render"]["message"]
        no_reply[pid] = r["reaction_distribution"]["no_reply_avoid"]

    # 三条回复互不相同
    assert len(set(renders.values())) == 3, f"回复未区分: {renders}"

    # 安全型：积极/正向占比 > 回避/不回占比
    sec = _pipe("t_healthy").run_turn("t_healthy", "周末一起吃饭？",
        {"type": "user_message"}, _pipe("t_healthy").new_state())
    P = sec["reaction_distribution"]["P"]
    assert P["Y1"] + P["Y2"] > P["Y4"] + P["Y6"] + P["Y7"]

    # NPD/回避型：不回/回避合计显著高（冷淡打太极）
    assert no_reply["t_npd"] > 0.4, f"NPD 不回避概率过低: {no_reply}"

    # 渲染风格关键词：安全型暖、回避型冷、焦虑型慌
    assert any(k in renders["t_healthy"] for k in ("没安排", "你想去哪", "可以", "行"))
    assert any(k in renders["t_npd"] for k in ("再说吧", "忙", "看情况"))
    assert any(k in renders["t_bpd"] for k in ("真的吗", "放我鸽子", "几点"))


# --------------------------------------------------------------------- #
# E11 跳脱性：低兴趣/自说自话 -> 按权重跳脱/反问，不有问必答
# --------------------------------------------------------------------- #
def test_e11_detach_probe():
    p = _pipe("t_npd")
    # 纯自说自话、不含场景关键词、不指向他 -> 低兴趣
    monologue = "下午整理了抽屉，翻出旧照片，看了半天，挺怀念的。"
    r = p.run_turn("t_npd", user_message=monologue,
                   event={"type": "user_message", "elapsed_minutes": 15},
                   state=p.new_state())
    assert r["detach"]["detached"] is True
    assert r["detach"]["topic_interest"] < 0.35
    # 回避型打太极/跳脱，而非正面展开聊他的照片
    assert r["decision"]["mode"] == "deflect"

    # BPD 焦虑型：低兴趣(你冷淡) -> 真·跳脱/反问，把话题抛回；不得黏人错位
    p2 = _pipe("t_bpd")
    r2 = p2.run_turn("t_bpd", user_message=monologue,
                     event={"type": "user_message", "elapsed_minutes": 15},
                     state=p2.new_state())
    assert r2["detach"]["detached"] is True
    assert r2["decision"]["mode"] == "deflect"
    # 真跳脱=反问/转移，而不是黏人冲突句
    assert "？" in r2["render"]["message"]
    for bad in ("分手", "睡不着", "不理我", "不要我"):
        assert bad not in r2["render"]["message"]


# --------------------------------------------------------------------- #
# E12 Alter：累积 / 动态阈值触发 / 氛围注入 / 同向增 / 反向减 / <0.2 清除 / 上限2.0
# --------------------------------------------------------------------- #
def test_e12_alter_lifecycle():
    p = _pipe("t_healthy")
    state = p.new_state()

    def feed(alter):
        nonlocal state
        r = p.run_turn("t_healthy", event={"type": "user_message",
                       "elapsed_minutes": 30, "alter": alter,
                       "device_available": True}, state=state)
        state = r["state"]
        return r["alter"]

    # 累积 +4 三轮，直到超动态阈值触发氛围注入
    a1 = feed(4)   # accum=4
    a2 = feed(4)   # accum=8
    assert a1["triggered"] is False and a2["triggered"] is False
    a3 = feed(4)   # accum>=阈值 -> 触发
    assert a3["triggered"] is True
    assert len(a3["atmosphere"]) == 1
    assert a3["injection_text"], "触发后应有氛围句注入"
    entry = a3["atmosphere"][0]
    assert entry["direction"] == 1
    assert abs(entry["weight"] - 1.0) < 1e-6
    assert entry["intensity"] <= 2.0

    # 同向 +4：权重 +0.05
    a4 = feed(4)
    same = next(e for e in a4["atmosphere"] if e["direction"] == 1)
    assert abs(same["weight"] - 1.05) < 1e-6, f"同向应增0.05: {same['weight']}"

    # 反向 -3：权重 -0.15
    a5 = feed(-3)
    opp = next(e for e in a5["atmosphere"] if e["direction"] == 1)
    assert abs(opp["weight"] - 0.90) < 1e-6, f"反向应减0.15: {opp['weight']}"

    # 强度上限
    for e in a5["atmosphere"]:
        assert e["intensity"] <= 2.0

    # 低于 minWeight(0.2) 自动清除：新流水线，触发后持续喂反向小量
    p2 = _pipe("t_healthy")
    st2 = p2.new_state()
    def feed2(alter):
        nonlocal st2
        rr = p2.run_turn("t_healthy", event={"type": "user_message",
                         "elapsed_minutes": 30, "alter": alter}, state=st2)
        st2 = rr["state"]; return rr["alter"]
    feed2(4); feed2(4); trig = feed2(4)          # 触发 serious 条目 w=1.0
    assert len(trig["atmosphere"]) == 1
    cleared = None
    for _ in range(8):                          # 喂反向 -1，权重逐步降到<0.2
        after = feed2(-1)
        if len(after["atmosphere"]) == 0:
            cleared = after
            break
    assert cleared is not None, "权重应降到<0.2 后被自动清除"


# --------------------------------------------------------------------- #
# E13 Agency：无理由不主动 / 过三条件才立即 / 否则重查 / 绝不自动发送
# --------------------------------------------------------------------- #
def test_e13_agency_gate():
    p = _pipe("t_healthy")
    state = p.new_state()

    # 1) 无真实生活理由 -> 不主动
    r0 = p.run_turn("t_healthy", event={"type": "life_tick", "elapsed_minutes": 60},
                    state=state)
    state = r0["state"]
    assert r0["agency"]["action"] == "drop"
    assert r0["agency"]["auto_send"] is False

    # 2) 有理由 + 意愿足 + 三条件过 -> 立即（仅草稿，不发送）
    r1 = p.run_turn("t_healthy", event={"type": "life_tick", "elapsed_minutes": 60,
                     "proactive_reasons": [{"text": "昨天答应帮你查资料", "willingness": 0.8}]},
                     state=state)
    state = r1["state"]
    assert r1["agency"]["action"] == "immediate"
    assert r1["agency"]["draft"]
    assert r1["agency"]["auto_send"] is False, "LoveAI 绝不自动发送"

    # 3) 有理由但日程负荷高 -> 稍后重查
    r2 = p.run_turn("t_healthy", event={"type": "life_tick", "elapsed_minutes": 60,
                     "proactive_reasons": [{"text": "想问候一下", "willingness": 0.8}],
                     "schedule_load": 0.9}, state=state)
    assert r2["agency"]["action"] == "proactive-check"

    # 4) 意愿低于阈值 -> 重查
    r3 = p.run_turn("t_healthy", event={"type": "life_tick", "elapsed_minutes": 60,
                     "proactive_reasons": [{"text": "可有可无的事", "willingness": 0.2}]},
                     state=state)
    assert r3["agency"]["action"] == "proactive-check"

    # 全局：任何分支都不自动发送
    for rr in (r0, r1, r2, r3):
        assert rr["agency"]["auto_send"] is False


# --------------------------------------------------------------------- #
# 状态可持久化 / 续跑
# --------------------------------------------------------------------- #
def test_state_resumable():
    p = _pipe("t_bpd")
    s = p.new_state()
    r1 = p.run_turn("t_bpd", user_message="你怎么不理我？",
                    event={"type": "user_message", "elapsed_minutes": 20}, state=s)
    turn1 = r1["state"]["turn_index"]
    r2 = p.run_turn("t_bpd", user_message="说话啊！",
                    event={"type": "follow_up", "elapsed_minutes": 10},
                    state=r1["state"])
    assert r2["state"]["turn_index"] == turn1 + 1
    assert r2["narrative"]["elapsed_minutes"] >= 0


# ===================================================================== #
# 验收 P1-11 回归
# ===================================================================== #
def test_same_scene_different_input_differs():
    """同场景(邀约)不同话术 -> 反应分布与理由都不同，不是场景查表常量。"""
    p = _pipe("t_healthy")
    r1 = p.run_turn("t_healthy", "周末一起看纪录片？",
                    {"type": "user_message"}, p.new_state())
    r2 = p.run_turn("t_healthy", "周末去爬山好不好？",
                    {"type": "user_message"}, p.new_state())
    assert r1["decision"]["reason"] != r2["decision"]["reason"]
    assert r1["reaction_distribution"]["P"] != r2["reaction_distribution"]["P"]
    # 分布仍合法
    for r in (r1, r2):
        assert abs(r["reaction_distribution"]["sum"] - 1.0) < 0.01


def test_intent_aware_render():
    """忙/道歉/礼物 三种意图分别得到对应回应，不再答非所问。"""
    p = _pipe("t_healthy")
    s = p.new_state()
    cases = {
        "在忙吗": ("忙", ["在", "会", "晚点", "改"]),
        "昨天是我语气急了，对不起": ("道歉", ["没事", "说开", "过去", "翻篇"]),
        "给你带了爱喝的那款": ("礼物", ["谢", "记着", "有心"]),
    }
    for msg, (_label, keys) in cases.items():
        r = p.run_turn("t_healthy", msg, {"type": "user_message", "elapsed_minutes": 20}, s)
        s = r["state"]
        assert r["decision"]["intent"] in ("busy_probe", "apology", "gift")
        assert any(k in r["render"]["message"] for k in keys), f"{msg} -> {r['render']['message']}"


# p_slowwarm 真实回复（tools/cases/p_slowwarm.txt 中"对方"行）
_P_SLOWWARM = [
    "你好，嗯，每周跑两三次", "江边绿道，人少", "吃完饭没",
    "楼下便利店买个饭团先垫垫，别空着肚子", "这周末我看下排班，明早回你",
    "早，周六早上七点可以，江边老地方", "刚跑过你那边了，往回走五分钟",
    "今天风大，你穿少了", "项目上线，一直在改bug", "真不是，刚开完会",
    "先不说了，leader在叫我", "早，昨晚上线到两点",
    "你昨天说的事，我下班回去跟你细说", "刚到家。不是不想回，是真的走不开。",
    "周六跑完步顺路去吃你说的那家粉店，你有空没", "粉店排队四十分钟，值了",
    "你推荐的能错吗", "还没，你说",
]


def test_terse_fingerprint_no_filler():
    """短直人格从真实回复学指纹：不加他从不用的 呀/呢/啦 语气词。"""
    p = TwinSimPipeline("p_slowwarm", persona_profile={}, own_messages=_P_SLOWWARM)
    assert p.spec["verbosity"] == "terse"
    r = p.run_turn("p_slowwarm", "周末一起看纪录片？",
                   {"type": "user_message"}, p.new_state())
    msg = r["render"]["message"]
    for filler in ("呀", "呢", "啦", "哈", "嘛"):
        assert filler not in msg, f"短直人格不应出现语气词 {filler}: {msg}"


def test_p_slowwarm_five_scenarios():
    """放行标准：p_slowwarm 五场景 >=4/5 贴合短直、无答非所问。"""
    p = TwinSimPipeline("p_slowwarm", persona_profile={}, own_messages=_P_SLOWWARM)
    s = p.new_state()
    scen = ["周末一起看纪录片？", "昨天是我语气急了，对不起",
            "给你带了爱喝的那款", "在忙吗", "风大想有人散步"]
    good = 0
    rendered = []
    for m in scen:
        r = p.run_turn("p_slowwarm", m, {"type": "user_message", "elapsed_minutes": 20}, s)
        s = r["state"]
        rendered.append((m, r["render"]["message"]))
        msg = r["render"]["message"]
        # 无答非所问：道歉/礼物/忙 必须接对应意图
        if m.endswith("对不起") and any(k in msg for k in ("没事", "说开", "过去")):
            good += 1
        elif "爱喝" in msg or "谢" in msg or "记着" in msg:
            good += 1
        elif "在忙吗" in m and any(k in msg for k in ("在", "会", "改", "晚点")):
            good += 1
        elif "纪录片" in m and any(k in msg for k in ("行", "可以", "点")):
            good += 1
        elif "散步" in m and any(k in msg for k in ("走", "可以", "行", "江边")):
            good += 1
    assert good >= 4, f"五场景贴合不足: {rendered}"


def test_multi_turn_no_verbatim_repeat():
    """连续多轮：渲染句不逐字重复、叙事活动不重复。"""
    p = _pipe("t_bpd")
    s = p.new_state()
    msgs = ["你在干嘛？", "今天累了", "周末有空吗", "你怎么老不回", "给你点了外卖"]
    renders, acts = [], []
    for m in msgs:
        r = p.run_turn("t_bpd", m, {"type": "user_message", "elapsed_minutes": 30}, s)
        s = r["state"]
        if r["render"]["message"]:
            renders.append(r["render"]["message"])
        acts.append(r["narrative"]["doing"])
    # 渲染句去重（池足够大且按 used 去重）
    assert len(renders) == len(set(renders)), f"渲染逐字重复: {renders}"
    # 叙事活动至少有 2 种，不逐字重复
    assert len(set(acts)) >= 2, f"叙事逐字重复: {acts}"


def test_five_scenarios_no_verbatim_repeat():
    """连续覆盖五场景+多轮：叙事句与渲染回复跨场景不逐字重复，示例组也不同。"""
    p = TwinSimPipeline("p_slowwarm", persona_profile={}, own_messages=_P_SLOWWARM)
    s = p.new_state()
    scen = ["周末一起看纪录片？", "昨天是我语气急了，对不起",
            "给你带了爱喝的那款", "在忙吗", "风大想有人散步",
            "下班顺路接我？", "今晚吃啥"]
    acts, rends, exgroups = [], [], []
    for m in scen:
        r = p.run_turn("p_slowwarm", m, {"type": "user_message", "elapsed_minutes": 20}, s)
        s = r["state"]
        acts.append(r["narrative"]["doing"])
        if r["render"]["message"]:
            rends.append(r["render"]["message"])
        exgroups.append(tuple(r["reaction_distribution"]["examples"]))
    assert len(acts) == len(set(acts)), f"叙事跨场景逐字重复: {acts}"
    assert len(rends) == len(set(rends)), f"渲染跨场景逐字重复: {rends}"
    assert len(exgroups) == len(set(exgroups)), f"示例组跨场景逐字重复: {exgroups}"
    # 同时短直风格不回退：无语气 filler
    for msg in rends:
        for f in ("呀", "呢", "啦"):
            assert f not in msg, f"短直回退出现filler {f}: {msg}"
