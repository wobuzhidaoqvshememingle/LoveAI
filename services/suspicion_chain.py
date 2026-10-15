# -*- coding: utf-8 -*-
"""怀疑链（Suspicion Chain）：人设基线 × 事件校验 → 怀疑度累积 → 分级应对 → 人设联动。

设计（用户定稿口径）：
- 怀疑不是"证据齐了才算"，而是连续累积：一点点证据加深一点怀疑程度；
- 人设是"先验"：新事件对照人设基线 → 一致=无操作 / 偏离=怀疑度+Δ / 矛盾=高Δ；
- 行为不符合人设本身就是最强怀疑信号：背离事件既加怀疑度、也写回人设动态备注；
- 分级应对：怀疑度低=正常忽略，中=观察，偏高=验证，高=设限，完整证据链=止损；
- 铁律：每条怀疑必须带原文证据（消息内容+时间），拿不到原文/线下无法确认的
  一律只标"待验证"低度怀疑，绝不当作实锤（防假阳性，接受用户"严苛真实"约束）。

纯本地规则实现，无外部 LLM 调用；不修改任何既有模块。
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 1. 事件词表（规则用；命中即产生候选事件，后续才判背离，防止纯词命中误报）
# --------------------------------------------------------------------------- #
_PROMISE_WORDS = ["我保证", "我答应", "答应你", "保证", "说好了", "说好",
                  "发誓", "骗你", "再也不", "不会了"]
# 承诺主题行为词：承诺句必须含主题，且窗口内同主题重现才算"言行不一"
_PROMISE_TOPIC = ["吵架", "冷战", "不理", "骗", "喝酒", "抽烟", "熬夜",
                  "消失", "失联", "不回", "找别人", "出轨", "离婚", "打"]
# 反问句（"不是说好了吗/什么说好了"是质疑不是承诺）
_RHETORICAL = ["不是说好了", "什么说好了", "说好什么", "？"]
_AFFECTION_WORDS = ["爱你", "想你", "在乎你", "不会离开", "一辈子", "永远",
                    "我离不开", "最喜欢你", "只爱你"]
_MONEY_WORDS = ["借钱", "借我", "要钱", "转我", "转点", "红包", "给我买",
                "打钱", "微信转", "付一下", "垫付", "垫一下", "还我", "欠我",
                "想要钱", "给我钱", "要我买", "帮我买", "给点钱", "给我点钱",
                "发红包", "发个红包"]
# 困境陈述（不算索取：可能是真实处境；detect 不触发 money 事件，仅提示）
_HARDSHIP_WORDS = ["没钱了", "没钱", "好穷", "穷", "吃土"]
_SELF_CLAIM_WORDS = ["我不说谎", "我从来不骗你", "我发誓", "骗你是小狗",
                     "我这个人", "我就是", "我性格", "我脾气"]

# -- 过度解释/信息修补（用户最在意的说谎补充信号，mq2 #7）--------------------- #
# 我方"追问"的本地信号：sent 句含问号 + 下列追问词之一，才视为一次追问。
_PROBE_WORDS = ["你刚才说的", "到底怎么", "你为什么", "那你解释", "你说清楚",
                "怎么回事", "你啥意思", "你之前说", "你倒是说", "你解释下"]
# 长解释的绝对下限（字符）：避免极短消息误判为"长解释"
_LONG_EXPLAIN_MIN_CHARS = 40


def _median_recv_len(messages: list) -> float:
    """within-person 基线：对方 received 消息字符长度的中位数。

    防 79.5 假阳性教训：一律用个体内基线，不用人群/绝对阈值。
    """
    lens = [len(str(m.get("content") or "")) for m in messages
            if (m.get("direction") or "") == "received"]
    if not lens:
        return 0.0
    lens.sort()
    n = len(lens)
    mid = n // 2
    if n % 2:
        return float(lens[mid])
    return (lens[mid - 1] + lens[mid]) / 2.0


def _probe_topic_terms(probe_content: str) -> list:
    """从追问句去掉模板追问词与标点/代词，抽 2-gram 作为主题词。

    用于判断随后的长回复是否"正面回答了主题"。抽不出主题则视为无法判定，
    调用方应保守不触发。
    """
    t = probe_content
    for w in _PROBE_WORDS:
        t = t.replace(w, "")
    for p in "?？，。,.！!啊吧呢嘛的了吗我你他她":
        t = t.replace(p, "")
    terms = [t[i:i + 2] for i in range(len(t) - 1)]
    return list(dict.fromkeys(terms))  # 去重保序


# -- A2 纯言语说谎近似 + A3 承诺质量分层 ------------------------------------- #
# 过度保证/绝对化词（assurance_surge：突然高频=焦虑/掩饰候选）。
# 反例检查：热恋期/焦虑期本就高频保证，突变≠说谎 → 本信号只观察、绝不自动加分。
_ASSURANCE_WORDS = ["保证", "绝对", "一定", "发誓", "不骗你", "骗你是小狗",
                    "百分百", "相信我", "真的真的"]
# 承诺带具体成本细节（A3-C1：高可验证性；违反时更难抵赖，故违反权重略升）。
# 反例检查：细节也可能是表演（夸张哄骗）→ 仅在"已发生违反"时加重，不凭细节单向加分。
_PROMISE_COST_WORDS = ["明天", "周末", "下周", "今晚", "发工资", "转你",
                       "陪你一天", "陪你", "买给你", "请你吃", "下午", "上午"]


def _median_recv_gap(messages: list) -> float:
    """within-person 基线：对方相邻 received 消息的间隔中位数（分钟）。"""
    times = [_Ctx.parse_dt(m.get("created_at")) for m in messages
             if (m.get("direction") or "") == "received"]
    times = [t for t in times if t is not None]
    gaps = []
    for k in range(1, len(times)):
        g = (times[k] - times[k - 1]).total_seconds() / 60.0
        if 0 <= g < 24 * 60:
            gaps.append(g)
    if not gaps:
        return 0.0
    gaps.sort()
    n = len(gaps)
    mid = n // 2
    return float(gaps[mid]) if n % 2 else (gaps[mid - 1] + gaps[mid]) / 2.0

# --------------------------------------------------------------------------- #
# 2. 背离检测器
# --------------------------------------------------------------------------- #
class _Ctx:
    """单条消息的上下文包装。"""

    def __init__(self, direction: str, content: str, created_at: str):
        self.direction = direction
        self.content = content or ""
        self.created_at = created_at

    # B2 性能：created_at → datetime 的确定性缓存。同一字符串恒得同一结果，
    # datetime 不可变，缓存不改变任何行为语义（1.9M 次 strptime → 每串一次）。
    _DT_CACHE: dict = {}

    @staticmethod
    def parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        if s in _Ctx._DT_CACHE:
            return _Ctx._DT_CACHE[s]
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        _Ctx._DT_CACHE[s] = dt
        return dt


class EventDetector:
    """从消息流中提取候选事件（带原文证据）。纯规则；命中只产生候选，
    是否计入怀疑由 SuspicionChain 对照人设基线判定。"""

    def __init__(self) -> None:
        pass

    def detect(self, messages: list[dict]) -> list[dict]:
        """messages: [{direction, content, created_at}, ...] 按时间升序。
        返回候选事件列表：[{type, content, at, direction, matched, extra}]
        """
        events: list[dict] = []
        for m in messages:
            content = str(m.get("content") or "")
            at = m.get("created_at") or ""
            direction = m.get("direction") or ""
            for w in _PROMISE_WORDS:
                if w in content:
                    events.append({"type": "promise", "content": content, "at": at,
                                   "direction": direction, "matched": w})
                    break
            for w in _AFFECTION_WORDS:
                if w in content:
                    events.append({"type": "affection", "content": content, "at": at,
                                   "direction": direction, "matched": w})
                    break
            for w in _MONEY_WORDS:
                if w in content:
                    events.append({"type": "money", "content": content, "at": at,
                                   "direction": direction, "matched": w})
                    break
            for w in _SELF_CLAIM_WORDS:
                if w in content:
                    events.append({"type": "self_claim", "content": content, "at": at,
                                   "direction": direction, "matched": w})
                    break
        # 过度解释/信息修补（跨消息检测：追问后突然长解释+回避主题）
        events.extend(self._detect_over_explain(messages))
        # A2/A3 新本地近似（均为弱信号，_evaluate 一律 delta=0 待人工验证）
        events.extend(self._detect_memory_inconsistency(messages))
        events.extend(self._detect_assurance_surge(messages))
        events.extend(self._detect_key_silence(messages))
        events.extend(self._detect_repeated_promise(messages))
        return events

    def _detect_memory_inconsistency(self, messages: list) -> list:
        """A2-b 记忆不一致核对：本地关键词近似——锚"年龄"数字。

        对方跨期自述年龄若"越活越年轻"（后报<先报），即矛盾（年龄自然只增不减）。
        能力边界：仅能锚"年龄"这类可数自报量；姓名/关系人物/具体事件需 NER，
        本地做不到，如实标待办。单条矛盾可能是口误/虚岁/笔误 → 只观察不加分。
        """
        claims = []
        for m in messages:
            if (m.get("direction") or "") != "received":
                continue
            content = str(m.get("content") or "")
            mm = re.search(r"我?(\d{1,2})\s*岁", content)
            if not mm:
                continue
            age = int(mm.group(1))
            if 10 <= age <= 90:
                claims.append((age, _Ctx.parse_dt(m.get("created_at")), m))
        claims.sort(key=lambda x: (x[1] or datetime.min))
        for k in range(1, len(claims)):
            prev_age, _, _ = claims[k - 1]
            cur_age, _, cur_m = claims[k]
            if cur_age < prev_age:
                return [{"type": "memory_inconsistency",
                         "content": f"{prev_age}岁→{cur_age}岁",
                         "at": cur_m.get("created_at") or "", "direction": "received",
                         "matched": f"{prev_age}岁/{cur_age}岁",
                         "extra": {"prev": prev_age, "cur": cur_age}}]
        return []

    def _detect_assurance_surge(self, messages: list) -> list:
        """A2-a 用词习惯突变：过度保证词在近期窗口内相对自身历史分布骤增。

        within-person 基线（对比该人自身历史率）；深夜(0-5点)消息不计入。
        反例：热恋/焦虑期本就高频保证 → 本信号只进 pending_manual 观察，绝不自动加分。
        """
        def _daytime(m):
            dt = _Ctx.parse_dt(m.get("created_at"))
            return dt is None or not (0 <= dt.hour < 5)
        recv = [m for m in messages if (m.get("direction") or "") == "received" and _daytime(m)]
        if len(recv) < 12:
            return []
        hist, recent = recv[:-5], recv[-5:]

        def _hits(ms):
            return sum(1 for m in ms
                       if any(w in str(m.get("content") or "") for w in _ASSURANCE_WORDS))
        h, r = _hits(hist), _hits(recent)
        hist_rate, recent_rate = h / max(1, len(hist)), r / max(1, len(recent))
        if r >= 4 and recent_rate >= max(hist_rate * 2, hist_rate + 0.2):
            return [{"type": "assurance_surge", "content": "近期过度保证词骤增",
                     "at": recv[-1].get("created_at") or "", "direction": "received",
                     "matched": "assurance_surge",
                     "extra": {"recent_hits": r, "hist_rate": round(hist_rate, 2),
                               "recent_rate": round(recent_rate, 2)}}]
        return []

    def _detect_key_silence(self, messages: list) -> list:
        """A2-c 关键事实沉默（与 over_explain 互补：追问后不是长解释，而是沉默/极简/换题）。

        within-person 基线：间隔>自身中位间隔×3 且>60 分钟；或回复≤4字且未答主题。
        深夜(0-5点)排除；与已证伪的"深夜/省略号"假阳性路径严格区分（不单独用省略号/短回复判）。
        反例：可能只是忙/没看到 → 只进 pending_manual 观察，不自动加分。
        """
        median_gap = _median_recv_gap(messages)
        out: list = []
        used: set = set()
        n = len(messages)
        for i in range(n):
            m = messages[i]
            if (m.get("direction") or "") != "received":
                continue
            dt = _Ctx.parse_dt(m.get("created_at"))
            if dt is not None and 0 <= dt.hour < 5:
                continue
            probe_idx = -1
            for j in range(i - 1, -1, -1):
                if (messages[j].get("direction") or "") == "sent":
                    probe_idx = j
                    break
            if probe_idx < 0 or probe_idx in used:
                continue
            probe_content = str(messages[probe_idx].get("content") or "")
            if not (("?" in probe_content or "？" in probe_content)
                    and any(w in probe_content for w in _PROBE_WORDS)):
                continue
            pdt = _Ctx.parse_dt(messages[probe_idx].get("created_at"))
            gap = (dt - pdt).total_seconds() / 60.0 if (dt and pdt) else None
            content = str(m.get("content") or "")
            topic = _probe_topic_terms(probe_content)
            addressed = bool(topic) and any(t in content for t in topic)
            long_gap = gap is not None and gap > max(60.0, (median_gap or 0.0) * 3)
            terse = len(content) <= 4 and not addressed
            if long_gap or terse:
                used.add(probe_idx)
                out.append({"type": "key_silence", "content": content[:50],
                            "at": m.get("created_at") or "", "direction": "received",
                            "matched": probe_content[:20],
                            "extra": {"gap_min": round(gap, 1) if gap else None,
                                      "len": len(content)}})
        return out

    def _detect_repeated_promise(self, messages: list) -> list:
        """A3-C3：同一承诺措辞重复出现>=3 次（可验证项）。

        反例：重复也可能只是习惯性口头安慰 → 只进 pending_manual，配合言行不一看，不自动加分。
        """
        from collections import Counter
        c = Counter()
        for m in messages:
            if (m.get("direction") or "") != "received":
                continue
            content = str(m.get("content") or "")
            if any(w in content for w in _PROMISE_WORDS):
                c[content] += 1
        for content, cnt in c.items():
            if cnt >= 3:
                return [{"type": "repeated_promise", "content": content[:50], "at": "",
                         "direction": "received", "matched": "重复承诺",
                         "extra": {"count": cnt}}]
        return []

    def _detect_over_explain(self, messages: list) -> list:
        """跨消息检测"过度解释/信息修补"：我方追问后，对方突然发出远超自身
        中位长度的长回复、且不正面回答追问主题。

        防假阳性（吸收 deception_risk=79.5 教训，全部本地规则）：
          * within-person 基线：阈值=对方自身历史 received 中位长度×2，不用绝对阈值；
          * 同一追问会话只计一次（同一条 sent 追问只触发一次）；
          * 深夜(0-5点)回复不计入本信号（避开已证伪的"深夜堆特征"假阳性路径）；
          * 长回复里能找到追问主题词 → 视为正面回答，不触发；
          * 抽不出追问主题 → 保守不触发；
          * 检测到的候选一律由 _evaluate 判 delta=0/待人工验证，绝不单条加分。
        """
        median_len = _median_recv_len(messages)
        if median_len <= 0:
            return []
        out: list = []
        used_probe_idx: set = set()
        n = len(messages)
        for i in range(n):
            m = messages[i]
            if (m.get("direction") or "") != "received":
                continue
            content = str(m.get("content") or "")
            if len(content) < max(_LONG_EXPLAIN_MIN_CHARS, median_len * 2):
                continue
            # 深夜(0-5点)不触发（避开已证伪假阳性路径）
            dt = _Ctx.parse_dt(m.get("created_at"))
            if dt is not None and 0 <= dt.hour < 5:
                continue
            # 找最近一条 sent 追问
            probe_idx = -1
            for j in range(i - 1, -1, -1):
                if (messages[j].get("direction") or "") == "sent":
                    probe_idx = j
                    break
            if probe_idx < 0 or probe_idx in used_probe_idx:
                continue
            probe_content = str(messages[probe_idx].get("content") or "")
            has_q = ("?" in probe_content or "？" in probe_content)
            if not (has_q and any(w in probe_content for w in _PROBE_WORDS)):
                continue
            topic_terms = _probe_topic_terms(probe_content)
            if not topic_terms:
                continue  # 抽不出主题 → 保守不触发
            if any(t in content for t in topic_terms):
                continue  # 正面回答了主题 → 不触发
            used_probe_idx.add(probe_idx)
            out.append({"type": "over_explain", "content": content,
                        "at": m.get("created_at") or "", "direction": "received",
                        "matched": probe_content[:20],
                        "extra": {"len": len(content),
                                  "median_len": round(median_len, 1)}})
        return out

    def reply_lag_stats(self, messages: list[dict]) -> dict:
        """按周统计对方回复延迟（距我上一条消息的分钟数）。"""
        recv = [m for m in messages if (m.get("direction") or "") == "received"]
        sent = [m for m in messages if (m.get("direction") or "") == "sent"]
        if not recv or not sent:
            return {}
        recv_dt = [(_Ctx.parse_dt(m.get("created_at")), m) for m in recv]
        recv_dt = [(t, m) for t, m in recv_dt if t is not None]
        if not recv_dt:
            return {}
        week_stats: dict[str, list[float]] = {}
        # 简单的"消息-前置消息"延迟：对方每条消息距最近一条对方消息
        prev: Optional[datetime] = None
        for t, m in recv_dt:
            wk = t.strftime("%Y-%m-%d")
            if prev is not None:
                lag_min = (t - prev).total_seconds() / 60.0
                if 0 <= lag_min < 24 * 60:
                    week_stats.setdefault(wk, []).append(round(lag_min, 1))
            prev = t
        return {wk: (round(sum(v) / len(v), 1), len(v), max(v)) for wk, v in week_stats.items()}


class SuspicionChain:
    """怀疑链主流程：人设基线 + 事件校验 → 怀疑度累积 → 分级应对 → 人设联动。

    怀疑度模型（微积分加权版）：
    - 幂次加速：Δ_eff = Δ × (1 + S/100)^k —— S 越高同样证据加得越重（接近实锤时
      边际增大，符合"一点点证据加深证实程度"）；k 默认 1.5。
    - 指数时间衰减：S *= e^(-λ·Δt)，λ=ln2/半衰期，默认半衰期 180 天——
      旧证据随时间淡出（持续背离才累积，偶发背离会慢慢回落），不是永久烙印，
      也避免"10 年前的怀疑永远挂着"。
    - 证据要求：每条怀疑带原文；线下不可验证的只进"待人工验证"清单，不加分。
    """

    LEVELS = [
        (80, "设限", "怀疑度高：停止重大投入/承诺；不揭穿、不对峙，用引导改变（正向强化对方调整行为）替代质问，同时重点核实关键承诺与利益往来"),
        (60, "引导", "怀疑度偏高：不揭穿、不设局试探（揭穿不一定有好处）；改用无压力互动引导对方行为转变——给其在小事上兑现/坦诚的机会并正向强化，观察是否调整"),
        (30, "观察", "怀疑度中等：记录并观察，正常相处，不行动"),
        (0, "正常", "怀疑度低：属正常波动，不处理"),
    ]

    # 事件强度（对照人设后加成；evidence 不足的降档）
    _WEIGHT = {
        "promise": 25,        # 主题承诺言行不一（7天内同主题重现）
        "affection": 15,      # 情感表态 vs 行为背离（延迟翻倍）
        "money": 30,          # 索取前后行为反差（最强信号之一）
        "self_claim": 10,     # 自述声明 vs 人设基线矛盾
        "behavior_shift": 20, # 行为模式突变（无合理理由）
        "over_explain": 10,   # 追问后过度解释/信息修补（重复>=3次才给一次小分）
        # 以下为弱信号：_evaluate 一律 delta=0 待人工验证，仅作可复核证据，不自动加分
        "memory_inconsistency": 15,  # 跨期自述矛盾（年龄锚），待人工核实
        "assurance_surge": 5,        # 过度保证词骤增（热恋期也高频），仅观察
        "key_silence": 10,           # 追问后关键事实沉默（可能只是忙），仅观察
        "repeated_promise": 5,       # 同措辞承诺重复>=3次，配合言行不一
    }
    # 微积分参数
    ACCEL_K = 1.5            # 幂次加速指数
    HALF_LIFE_DAYS = 180.0   # 怀疑度时间半衰期（天）

    def __init__(self, baseline: Optional[dict] = None) -> None:
        self.baseline = baseline or {}
        self.dynamic_notes: list[dict] = []   # 人设动态备注（写回人设的证据）
        self.log: list[dict] = []             # 怀疑链日志（有分事件）
        self.pending_manual: list[dict] = []  # 待人工验证清单（无法自动验证，不加分）
        self.suspicion = 0.0                  # 当前怀疑度 0-100
        self._last_at: Optional[datetime] = None  # 上次累积时间（用于时间衰减）

    # -- 人设基线 ---------------------------------------------------------- #
    def _baseline_trust(self) -> float:
        t = (self.baseline.get("trust") or {}).get("score")
        try:
            return float(t) if t is not None else 55.0
        except (TypeError, ValueError):
            return 55.0

    def _baseline_commitment(self) -> float:
        c = (self.baseline.get("commitment") or {}).get("score")
        try:
            return float(c) if c is not None else 50.0
        except (TypeError, ValueError):
            return 50.0

    def _baseline_type(self) -> str:
        return str((self.baseline.get("attachment") or {}).get("primary_type") or "")

    # -- 事件校验 ---------------------------------------------------------- #
    def _evaluate(self, ev: dict, messages: list[dict]) -> Optional[dict]:
        """对单个候选事件对照人设基线判定背离，返回 {delta, evidence, verdict} 或 None（不算）。"""
        etype = ev.get("type")
        direction = ev.get("direction")
        content = ev.get("content", "")
        at = ev.get("at", "")
        # 只看对方(received)发起的言行背离；自己的表态不构成"对方怀疑"
        if direction != "received":
            return None

        # 1) 承诺：只记"可验证的言行不一"——承诺句须含主题行为词，窗口内
        #    同主题重现才算违反（如"再也不吵架"→ 7 天内又吵）。无主题的承诺
        #    （"我保证"）无法自动验证 → 进"待人工验证"清单，不加分。
        if etype == "promise":
            if any(w in content for w in _RHETORICAL):
                return None  # 反问句（不是说好了吗）不是承诺
            topics = [w for w in _PROMISE_TOPIC if w in content]
            if not topics:
                return {"delta": 0,
                        "evidence": f"无具体时间/条件的空洞承诺（便宜话），待线下确认兑现（原文：{content[:60]} @{at}）",
                        "verdict": "待人工验证"}
            after7 = self._window_after(messages, at, days=7)
            ctx_text = " ".join(str(m.get("content") or "") for m in after7)
            violated = [t for t in topics if t in ctx_text]
            if violated:
                # A3-C1：承诺带具体成本细节 → 违反更难抵赖，权重略升（仅在已违反时）
                cost = any(w in content for w in _PROMISE_COST_WORDS)
                delta = 30.0 if cost else 25.0
                note = "（承诺带具体成本细节，违反更难抵赖）" if cost else ""
                return {"delta": delta,
                        "evidence": (f"承诺'{content[:40]}'@{at}（含主题{'/'.join(topics)}）"
                                     f" 后7天内同主题重现（{'/'.join(violated[:3])}），言行不一{note}"),
                        "verdict": "矛盾（言行不一）"}
            return {"delta": 0,
                    "evidence": f"承诺'{content[:40]}'@{at} 窗口内未见同主题违反（主题{'/'.join(topics)}）",
                    "verdict": "一致"}

        # 2) 情感表态 vs 行为：表态前后回复延迟对比
        #    注意：情感表态本身 NOT 澄清信号（"爱你"说得多不能抵消失信/利益反差——
        #    用户明确批评过"只看到道歉服软，没看到原因"）。一致 → 不算事件；
        #    只有表态后行为显著背离（延迟翻倍）才加怀疑。
        if etype == "affection":
            before = self._window_before(messages, at, days=7)
            after = self._window_after(messages, at, days=7)
            lag_b = self._avg_reply_lag(before)
            lag_a = self._avg_reply_lag(after)
            if lag_a and lag_b and lag_a > lag_b * 2 and lag_a > 120:
                return {"delta": self._WEIGHT["affection"],
                        "evidence": (f"情感表态'{content[:40]}'@{at} 后7天回复延迟 {lag_a:.0f}min"
                                     f" vs 表态前 {lag_b:.0f}min，行为与表态背离"),
                        "verdict": "矛盾"}
            return None  # 行为一致不扣分、不记事件

        # 3) 利益事件：先排除"纯困境陈述"（只有没钱/穷、无索取动作）；
        #    再排除冲突期语境（吵架/冷战期延迟上升是关系张力，不是"要钱后冷淡"）；
        #    最后才看前后行为反差
        if etype == "money":
            has_ask = any(w in content for w in _MONEY_WORDS)
            has_hardship = any(w in content for w in _HARDSHIP_WORDS)
            if has_hardship and not has_ask:
                return None  # 纯困境陈述，不算索取
            ctx = self._window_around(messages, at, days=3)
            ctx_text = " ".join(str(m.get("content") or "") for m in ctx)
            if any(w in ctx_text for w in ["滚", "烦", "冷战", "分手", "离婚", "不想说", "吵", "生气", "拉黑", "免打扰"]):
                return None  # 冲突期语境，延迟上升另有解释
            before = self._window_before(messages, at, days=7)
            after = self._window_after(messages, at, days=7)
            lag_b = self._avg_reply_lag(before)
            lag_a = self._avg_reply_lag(after)
            hot_b = self._recv_count(before)
            hot_a = self._recv_count(after)
            contrast = (lag_a and lag_b and lag_a > lag_b * 2) or (hot_b and hot_a and hot_b > hot_a * 2)
            if contrast:
                return {"delta": self._WEIGHT["money"],
                        "evidence": (f"索取消息'{content[:40]}'@{at} 前后对比："
                                     f"延迟 {lag_b or 0:.0f}→{lag_a or 0:.0f}min / 活跃 {hot_b}→{hot_a}条"),
                        "verdict": "矛盾（利益前后反差）"}
            return None

        # 4) 自述声明 vs 人设基线
        if etype == "self_claim":
            claim = content
            base_type = self._baseline_type()
            if ("我不说谎" in claim or "我从来不骗你" in claim or "骗你是小狗" in claim) \
                    and self._baseline_trust() < 65:
                return {"delta": self._WEIGHT["self_claim"],
                        "evidence": f"自述可信度声明'{content[:50]}'@{at}，但人设基线 trust={self._baseline_trust()}",
                        "verdict": "矛盾（自述 vs 基线）"}
            return None

        # 5) 过度解释/信息修补（用户最在意的说谎补充信号）：
        #    追问后突然长解释且不正面回答主题 → 疑似过度解释。
        #    铁律：首见一律 delta=0、只进 pending_manual（待人工验证），绝不单条加分
        #    （吸收 79.5 假阳性教训）；重复>=3 次的汇总小分由 accumulate 在循环后统一补一次。
        if etype == "over_explain":
            extra = ev.get("extra") or {}
            return {"delta": 0,
                    "evidence": (f"我方追问'{ev.get('matched', '')}'@{at} 后对方长解释"
                                 f"（{extra.get('len', '?')}字 vs 自身中位{extra.get('median_len', '?')}字）"
                                 f"且未正面回答主题，疑似信息修补/过度解释。"
                                 f"（此判断可能不成立：或只是第一次认真解释。原文：{content[:50]}）"),
                    "verdict": "待人工验证"}

        # A2/A3 弱信号：一律 delta=0、只进 pending_manual，绝不自动加分（能力边界：
        # 纯规则无法判定语义真伪，只能给可复核线索供人工核实）。
        if etype == "memory_inconsistency":
            extra = ev.get("extra") or {}
            return {"delta": 0,
                    "evidence": (f"对方跨期自述年龄 {extra.get('prev')}岁→{extra.get('cur')}岁 矛盾@ {at}"
                                 f"（年龄自然只增不减）。此判断可能不成立：可能口误/虚岁/笔误，需人工核实。"),
                    "verdict": "待人工验证"}
        if etype == "assurance_surge":
            extra = ev.get("extra") or {}
            return {"delta": 0,
                    "evidence": (f"对方近期过度保证词骤增（{extra.get('recent_hits')}次，"
                                 f"自身历史率{extra.get('hist_rate')}→{extra.get('recent_rate')}）。"
                                 f"此判断可能不成立：热恋/焦虑期本就高频保证，不等于说谎。仅观察。"),
                    "verdict": "待人工验证"}
        if etype == "key_silence":
            extra = ev.get("extra") or {}
            return {"delta": 0,
                    "evidence": (f"追问'{ev.get('matched', '')}'@{at} 后对方关键事实沉默"
                                 f"（间隔{extra.get('gap_min')}分/回复{extra.get('len')}字）。"
                                 f"此判断可能不成立：可能只是忙/没看到，需人工核实。原文：{content[:40]}"),
                    "verdict": "待人工验证"}
        if etype == "repeated_promise":
            extra = ev.get("extra") or {}
            return {"delta": 0,
                    "evidence": (f"同一承诺措辞重复出现{extra.get('count')}次：'{content[:40]}'。"
                                 f"此判断可能不成立：重复也可能只是习惯性口头安慰，需配合是否兑现核实。"),
                    "verdict": "待人工验证"}

        return None

    # -- 累积 -------------------------------------------------------------- #
    def accumulate(self, events: list[dict], messages: list[dict], reset: bool = True) -> dict:
        """逐个事件累积怀疑度，输出完整怀疑链。
        reset=True：从 0 开始；reset=False：在现有状态上延续（用于跨时段回放）。
        微积分加权：先对现有 S 做指数时间衰减，再以幂次加速计入新事件。
        """
        if reset:
            self.suspicion = 0.0
            self.log = []
            self.dynamic_notes = []
            self.pending_manual = []
            self._last_at = None
        for ev in events:
            res = self._evaluate(ev, messages)
            if not res:
                continue
            at = _Ctx.parse_dt(ev.get("at"))
            if not res.get("delta"):
                # 无分但需留档（待人工验证）
                if "待人工验证" in str(res.get("verdict", "")):
                    self.pending_manual.append({
                        "at": ev.get("at"), "type": ev.get("type"),
                        "content": ev.get("content"), "evidence": res.get("evidence"),
                        "verdict": res.get("verdict"),
                    })
                continue
            # 时间衰减：距上次累积 Δt 天内，S 指数衰减
            if self._last_at is not None and at is not None:
                dt_days = (at - self._last_at).total_seconds() / 86400.0
                if dt_days > 0:
                    lam = math.log(2) / self.HALF_LIFE_DAYS
                    self.suspicion *= math.exp(-lam * dt_days)
            self._last_at = at
            # 幂次加速：S 越高，同样证据加得越重（接近实锤边际增大）
            base = float(res["delta"])
            eff = base * (1.0 + self.suspicion / 100.0) ** self.ACCEL_K
            self.suspicion = min(100.0, max(0.0, self.suspicion + eff))
            entry = {
                "at": ev.get("at"),
                "type": ev.get("type"),
                "content": ev.get("content"),
                "matched": ev.get("matched"),
                "verdict": res.get("verdict"),
                "delta_base": res.get("delta"),
                "delta_eff": round(eff, 1),
                "suspicion_after": round(self.suspicion, 1),
                "evidence": res.get("evidence"),
                "level": self.level_for(self.suspicion),
            }
            self.log.append(entry)
            self.dynamic_notes.append({
                "at": ev.get("at"), "type": ev.get("type"),
                "evidence": res.get("evidence"),
                "delta_eff": round(eff, 1),
                "suspicion_after": round(self.suspicion, 1),
            })
        # 过度解释：同一会话独立重复>=3 次，才补一次小分（每条首见都在 pending_manual，不直接加）
        oe_count = sum(1 for p in self.pending_manual if p.get("type") == "over_explain")
        if oe_count >= 3:
            base = float(self._WEIGHT.get("over_explain", 10))
            eff = base * (1.0 + self.suspicion / 100.0) ** self.ACCEL_K
            self.suspicion = min(100.0, max(0.0, self.suspicion + eff))
            self.log.append({
                "at": None, "type": "over_explain",
                "content": f"过度解释/信息修补独立出现{oe_count}次",
                "matched": None, "verdict": f"模式重复{oe_count}次（待人工核实）",
                "delta_base": self._WEIGHT.get("over_explain", 10),
                "delta_eff": round(eff, 1),
                "suspicion_after": round(self.suspicion, 1),
                "evidence": (f"追问后长解释且回避主题共{oe_count}次（>=3），疑似系统性信息修补；"
                             f"此判断可能不成立，需人工逐条核实原文"),
                "level": self.level_for(self.suspicion),
            })
        return {
            "suspicion": round(self.suspicion, 1),
            "level": self.level_for(self.suspicion),
            "response": self.response_for(self.suspicion),
            "events": self.log,
            "pending_manual": self.pending_manual,
            "dynamic_notes": self.dynamic_notes,
        }

    # -- 分级应对 ---------------------------------------------------------- #
    @classmethod
    def level_for(cls, score: float) -> str:
        for th, name, _ in cls.LEVELS:
            if score >= th:
                return name
        return "正常"

    @classmethod
    def response_for(cls, score: float) -> str:
        for th, name, act in cls.LEVELS:
            if score >= th:
                return act
        return "怀疑度低：属正常波动，不处理"

    # -- 人设联动 ---------------------------------------------------------- #
    def persona_update_hint(self) -> list[str]:
        """根据怀疑链给'人设修正建议'（供画像重跑时参考）。"""
        hints = []
        if self.suspicion >= 60:
            hints.append("怀疑度≥60：建议将 trust 基线向下修正一档，并在画像动态备注中记录怀疑链证据；"
                         "应对以引导改变为主（不揭穿、不试探）")
        if self.suspicion >= 80:
            hints.append("怀疑度≥80：建议对 commitment/信任维度重新评估，标记'待重点核实'状态；"
                         "仍以引导改变为主，仅在持续不调整时考虑设限")
        # 若证据类型 ≥3 且含利益反差 → 接近实锤
        types = {e.get("type") for e in self.log}
        if len(types) >= 3 and "money" in types:
            hints.append("已具备多类型独立证据（含利益反差）：建议按'完整证据链'标准核实，必要时止损")
        return hints

    # -- 工具 -------------------------------------------------------------- #
    @staticmethod
    def _window_after(messages: list[dict], at: str, days: int) -> list[dict]:
        t = _Ctx.parse_dt(at)
        if t is None:
            return []
        end = t + timedelta(days=days)
        return [m for m in messages
                if (_Ctx.parse_dt(m.get("created_at")) or datetime.min) > t
                and (_Ctx.parse_dt(m.get("created_at")) or datetime.min) <= end]

    @staticmethod
    def _window_before(messages: list[dict], at: str, days: int) -> list[dict]:
        t = _Ctx.parse_dt(at)
        if t is None:
            return []
        start = t - timedelta(days=days)
        return [m for m in messages
                if (_Ctx.parse_dt(m.get("created_at")) or datetime.min) >= start
                and (_Ctx.parse_dt(m.get("created_at")) or datetime.min) < t]

    @staticmethod
    def _window_around(messages: list[dict], at: str, days: int) -> list[dict]:
        t = _Ctx.parse_dt(at)
        if t is None:
            return []
        start = t - timedelta(days=days)
        end = t + timedelta(days=days)
        return [m for m in messages
                if (_Ctx.parse_dt(m.get("created_at")) or datetime.min) >= start
                and (_Ctx.parse_dt(m.get("created_at")) or datetime.min) <= end]

    @staticmethod
    def _avg_reply_lag(msgs: list[dict]) -> Optional[float]:
        """简化：窗口内 received 消息的相邻间隔均值（分钟）。"""
        recv = [m for m in msgs if (m.get("direction") or "") == "received"]
        times = [(_Ctx.parse_dt(m.get("created_at")), m) for m in recv]
        times = [(t, m) for t, m in times if t is not None]
        if len(times) < 2:
            return None
        lags = []
        prev = times[0][0]
        for t, _ in times[1:]:
            lag = (t - prev).total_seconds() / 60.0
            if 0 <= lag < 24 * 60:
                lags.append(lag)
            prev = t
        if not lags:
            return None
        return sum(lags) / len(lags)

    @staticmethod
    def _recv_count(msgs: list[dict]) -> int:
        return sum(1 for m in msgs if (m.get("direction") or "") == "received")


def load_baseline(persona_path: str) -> dict:
    """从 persona_profile.json 读人设基线。"""
    if not os.path.exists(persona_path):
        return {}
    with open(persona_path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_back(persona_path: str, result: dict) -> dict:
    """把怀疑链结果写回 persona_profile.json 的 suspicion_chain 字段（人设联动落盘）。
    写前备份 .bak_suspicion_<日期>；只增字段，不改动既有画像内容。
    """
    if not os.path.exists(persona_path):
        return {"ok": False, "error": f"persona 不存在: {persona_path}"}
    # 备份（铁律：改文件前 .bak）
    bak = persona_path + f".bak_suspicion_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        with open(persona_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"读取失败: {e}"}
    with open(bak, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 写入 suspicion_chain 字段（保留 events 摘要与待人工清单）
    sc = {
        "suspicion": result.get("suspicion"),
        "level": result.get("level"),
        "response": result.get("response"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "events": result.get("events", []),
        "pending_manual": result.get("pending_manual", []),
        "persona_update_hints": result.get("persona_update_hints", []),
    }
    data["suspicion_chain"] = sc
    with open(persona_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"ok": True, "backup": bak, "suspicion": sc["suspicion"], "level": sc["level"]}


def run_suspicion_chain(db_path: str, person_id: str, persona_path: str,
                        cutoff: Optional[str] = None,
                        baseline: Optional[dict] = None) -> dict:
    """一键入口：读消息 → 检测事件 → 累积怀疑 → 输出怀疑链。cutoff 用于时间截断回测。
    baseline 传入时优先使用（接画像主流程时用现算基线，与画像一致）；
    否则从 persona_path 读文件基线。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = ("SELECT direction, content, created_at FROM chat_messages "
           "WHERE person_id=? ORDER BY created_at ASC")
    params: list = [person_id]
    if cutoff:
        sql = ("SELECT direction, content, created_at FROM chat_messages "
               "WHERE person_id=? AND created_at < ? ORDER BY created_at ASC")
        params.append(cutoff)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    messages = [dict(r) for r in rows]

    if baseline is None:
        baseline = load_baseline(persona_path)
    det = EventDetector()
    events = det.detect(messages)
    chain = SuspicionChain(baseline)
    result = chain.accumulate(events, messages)
    result["message_count"] = len(messages)
    result["candidate_events"] = len(events)
    result["persona_update_hints"] = chain.persona_update_hint()
    return result
