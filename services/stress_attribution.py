# -*- coding: utf-8 -*-
"""压力源归因引擎（Stress Attribution）：把对方压力下的行为（服软/狠话/冷战）
归因到压力源（关系威胁 / 现实压力 / 试探 / 情绪崩溃），输出归因结果 + 证据 + 推理要点。

设计哲学（用户定稿口径）——"看数据背后的心理，不是看关键词"：
- 同一个字面行为，在不同上下文里是完全不同的压力源。例如：
    "我错了" 单独看只是服软；但如果它后面紧跟着"就是上课/我爸要我专升本/
     我每天压力过载"，那这个服软是**现实压力**下的退缩性道歉，不是愧疚、
     更不是试探；如果它紧跟"但是我做了""重要吗都在闹离婚了"这种自我拆台，
     则带**试探/耗竭**色彩；如果它伴随"我很坏/把我杀了/时好时坏"的剧烈摆荡，
     才指向**情绪崩溃**。
    "和谁过都是过" 单独看是狠话/贬损；但如果前置是男方连环逼问"还想在一起吗/
     给我明确答案/找你爸妈谈"，且她又说"你想拿我爸妈威胁我"，那这句狠话是
     **关系威胁（被逼到墙角后的防御性去值）**，不是真的想分——下午她就恢复
     温暖、主动要钱要陪伴，反过来证明狠话是应激而非本意。
- 因此本引擎不做"命中某词=某结论"的标签机，而是：
  1) 先把对方(received)每条消息打**行为模式标签**（服软/狠话/冷战/示弱/压力陈述…）；
  2) 再取事发前后窗口，抽取**上下文信号**（我方前置是否逼承诺/逼回应、
     对方后置是否暴露具体外部压力、是否剧烈摆荡、是否秒暖/索取）；
  3) 用"行为模式 × 上下文组合"对四个压力源**加权投票**，最高分为主归因、
     次高为次归因；证据不足或信号打架时，宁标"待确认/数据不足"，不硬猜。

纯本地规则，无外部 LLM 调用；不修改任何既有模块。
"""
from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 压力源类别（内部枚举 -> 中文名）
CATEGORIES = {
    "relationship_threat": "关系威胁",
    "real_world_pressure": "现实压力",
    "testing": "试探",
    "emotional_collapse": "情绪崩溃",
}


# --------------------------------------------------------------------------- #
# 1. 信号词表（仅用于"识别行为模式/上下文信号"，不等于归因结论——结论由组合投票产生）
# --------------------------------------------------------------------------- #
# 对方"服软"行为：道歉 / 退让 / 示弱
_APOLOGY = ["对不起", "抱歉", "不好意思", "我错了", "我的错", "怪我", "忽略你了", "冷落"]
_DEFER = ["听你的", "你说怎么办", "随你", "都行", "随便", "好吗", "行吧", "那就这样",
          "好聚好散", "可以"]
_SELF_DEPREC = ["我没心", "我没有心", "我很坏", "我是个很坏", "很坏的人", "一个很坏",
                "我就是神经病", "我就是个", "我笨", "我迷茫", "不知道怎么办",
                "不知道怎么面对", "定不下来", "我自己都猜不透", "不信任", "保持怀疑",
                "不想学", "不知道怎么信任", "我就这样", "把我杀", "杀了我",
                "我活该", "作茧自缚"]

# 对方"狠话"行为：攻击 / 贬损关系 / 感知到威胁
_HARSH_ATTACK = ["滚", "大傻逼", "傻逼", "吃屎", "操你", "草你", "狗屁", "放屁", "66666"]
_HARSH_DEVALUE = ["没感情", "不爱了", "和谁过都", "跟谁过都", "不适合", "没感觉",
                  "无所谓", "不在乎", "心不在", "心不在你", "就解决人", "谁过都是过",
                  "对我没感情", "对你没感情"]
_HARSH_THREAT_PERCEIVE = ["你逼我", "你一直在逼", "逼我做", "想拿", "威胁我", "纠缠我",
                          "别纠缠", "别找我爸妈", "找我爸妈", "你要找我爸妈"]

# 对方"冷战/回避"行为：极简 / 抽离 / 转场
_COLD_BRIEF_EXACT = {"嗯", "哦", "…", "..", ".", "没", "知道了", "好", "行", "呵", "算了",
                     "没事", "没有", "不知道", "什么", "怎么了", "对", "是", "也许吧",
                     "可能吧", "应该吧", "随便吧"}
_AVOIDANCE = ["我去买菜", "我去做饭", "做饭了", "我去洗澡", "洗澡了", "要睡觉了",
              "我去学习", "学习了", "上课了", "别打扰我", "期间别打扰", "不想说",
              "不想弄", "不想打视频", "不想说话", "我先去", "出去了", "回家了",
              "买完了", "我吃饭了", "我明天有", "明天行吗", "睡了"]

# 对方"现实压力"陈述：具体外部生活负荷（学习/工作/家庭/健康/精力）
_STRESS_DISCLOSURE = ["上课", "考试", "专升本", "专插本", "作业", "学习", "课程",
                      "没完成", "工作", "加班", "实习", "我爸", "我妈", "家里", "老人",
                      "姥爷", "照顾", "压力", "过载", "累", "好累", "没精力", "没心情",
                      "精力", "活就够累", "活着就够", "没那个精力", "照顾老人",
                      "我去郑州", "补习班", "挂科", "旷考"]

# 我方(sent)前置：逼承诺 / 逼答案 / 逼情绪回应（"关系威胁"类压力的常见诱因）
_PUSH_COMMIT = ["还想在一起吗", "想在一起", "给我一个", "明确", "给我答案", "做决定",
                "快点", "选一个", "你自己选", "把证领", "领证", "结婚", "离婚",
                "找你爸妈", "见家长", "商量我们", "你来还是我来", "什么时候来",
                "过来一趟", "你爱不爱", "你还爱我", "你到底", "你想要的到底",
                "输在哪里", "给我什么", "你觉得关系", "还在一起", "一个月"]
_PUSH_REASSURE = ["为什么不回", "说话", "你倒是说", "正反馈", "一点回应", "无视我",
                  "打电话也不能", "发个消息也", "你一直排挤", "怎么不说话", "说啊",
                  "你回答我", "问你话呢", "你想要什么"]

# 对方"秒暖/索取"：风暴后快速回温（用来反证狠话不是本意）
_WARM_RECONNECT = ["我爱你", "晚安宝宝", "宝宝", "老公", "给我买", "给我转钱", "转钱",
                   "报销", "想找你", "再给我", "逛街", "抱抱", "亲亲", "mua", "么么"]


# --------------------------------------------------------------------------- #
# 2. 工具
# --------------------------------------------------------------------------- #
def parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@dataclass
class TaggedMsg:
    at: str
    direction: str
    content: str
    dt: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)


def _has_any(content: str, words) -> Optional[str]:
    for w in words:
        if w in content:
            return w
    return None


class SignalExtractor:
    """把消息流打标签：对方(received)的行为模式 + 我方(sent)的前置诱因。
    标签只是原料，归因结论在 StressAttributor 里由组合投票产生。"""

    def tag_message(self, direction: str, content: str, at: str) -> TaggedMsg:
        c = content or ""
        tags: list[str] = []
        if direction == "received":
            if _has_any(c, _APOLOGY):
                tags.append("apology")
            if _has_any(c, _SELF_DEPREC):
                tags.append("self_deprec")
            if _has_any(c, _HARSH_ATTACK):
                tags.append("harsh_attack")
            if _has_any(c, _HARSH_DEVALUE):
                tags.append("harsh_devalue")
            if _has_any(c, _HARSH_THREAT_PERCEIVE):
                tags.append("harsh_threat_perceive")
            if c.strip() in _COLD_BRIEF_EXACT or (len(c.strip()) <= 3 and c.strip() not in {"", "…"}):
                # 极短回复（≤3字）视为冷战信号；注意排除较长句
                if len(c.strip()) <= 3:
                    tags.append("cold_brief")
            if _has_any(c, _AVOIDANCE):
                tags.append("avoidance")
            if _has_any(c, _STRESS_DISCLOSURE):
                tags.append("stress_disclosure")
            if _has_any(c, _WARM_RECONNECT):
                tags.append("warm_reconnect")
            if _has_any(c, _DEFER):
                tags.append("deference")
        else:  # sent = 我方
            if _has_any(c, _PUSH_COMMIT):
                tags.append("push_commit")
            if _has_any(c, _PUSH_REASSURE):
                tags.append("push_reassure")
        return TaggedMsg(at=at, direction=direction, content=c,
                         dt=parse_dt(at), tags=tags)

    def extract(self, messages: list[dict]) -> list[TaggedMsg]:
        out = []
        for m in messages:
            out.append(self.tag_message(
                direction=str(m.get("direction") or ""),
                content=str(m.get("content") or ""),
                at=str(m.get("created_at") or ""),
            ))
        return out


# --------------------------------------------------------------------------- #
# 3. 归因器主流程
# --------------------------------------------------------------------------- #
class StressAttributor:
    """压力源归因：行为模式 × 上下文组合 -> 四压力源加权投票。

    投票权重设计（体现"组合"而非"单词命中"）：
    - 现实压力 real_world_pressure：靠"具体外部负荷披露"的密度 + 与焦点行为的邻近度。
      对方若反复说出上课/考试/家里/老人/过载/没精力等**具体**外部事实，服软/狠话
      就更可能是被生活压垮后的应激，而非关系本身出问题。
    - 关系威胁 relationship_threat：靠"我方前置逼承诺/逼回应" + 对方"感知威胁/
      贬损关系/反讽"的组合。被逼到墙角时的狠话=防御性去值，是关系威胁反应。
    - 试探 testing：靠"服软后立刻自我拆台/有条件" + "秒暖索取"的翻转——像在用
      服软或狠话换东西，而非真实情绪。
    - 情绪崩溃 emotional_collapse：靠"示弱贬己密度高 + 短时间内剧烈摆荡
      （狠话↔示弱↔狠话）+ 缺乏具体外部原因"。
    """

    # 投票权重（可在测试里直接复核）
    W = {
        "real_world_pressure": {
            "stress_disclosure_near": 9.0,   # 焦点窗口内有具体外部压力披露
            "stress_disclosure_dense": 6.0,   # 多条（>2）外部压力披露
            "apology_then_disclose": 7.0,     # 服软后紧接着暴露外部压力
        },
        "relationship_threat": {
            "push_commit_before": 8.0,        # 我方前置逼承诺/领证/要答案
            "push_reassure_before": 4.0,      # 我方前置逼情绪回应
            "harsh_threat_perceive": 9.0,     # 对方明说"你逼我/想拿XX威胁我"
            "harsh_devalue_under_push": 7.0,  # 逼问下出现贬损关系的狠话
            "cold_under_push": 3.0,           # 逼问下的极简冷战
        },
        "testing": {
            "apology_qualified": 6.0,         # 服软立刻自我拆台（但是/重要吗都…）
            "harsh_then_warm": 7.0,           # 狠话后短时间秒暖/索取（翻转）
            "deference_flip": 3.0,            # 一味"都行/随你"后又主动索取
        },
        "emotional_collapse": {
            "self_deprec_dense": 6.0,         # 高密度贬己（我很坏/我没心/我猜不透）
            "volatile_swing": 8.0,            # 短窗口内狠话↔示弱剧烈摆荡
            "no_external_cause": 4.0,         # 缺乏具体外部原因却情绪高浓
        },
    }

    def __init__(self, before_hours: float = 12.0, after_hours: float = 12.0) -> None:
        self.before_hours = before_hours
        self.after_hours = after_hours

    # -- 焦点行为判定 ------------------------------------------------------ #
    @staticmethod
    def _focal_behavior(t: TaggedMsg) -> str:
        rt = set(t.tags)
        if rt & {"harsh_attack", "harsh_devalue", "harsh_threat_perceive"}:
            return "狠话"
        if rt & {"apology", "deference", "self_deprec"}:
            return "服软"
        if rt & {"cold_brief", "avoidance"}:
            return "冷战回避"
        return "unknown"

    # -- 主入口 ------------------------------------------------------------ #
    def attribute(self, messages: list[dict], focal_at: str,
                  tagged: list[TaggedMsg] | None = None) -> dict:
        """对 focal_at 时刻的对方行为做四压力源归因。

        Args:
            messages: 全量消息流 [{direction, content, created_at}, ...]
            focal_at: 焦点行为时间戳
            tagged: 预打标签的消息列表（可选，避免重复 extract）。
                    若提供则复用，否则内部自动 extract。
        """
        if tagged is None:
            tagged = SignalExtractor().extract(messages)
        focal = self._find_focal(tagged, focal_at)
        if focal is None:
            return {"status": "insufficient_data",
                    "reason": f"在消息流里找不到焦点消息 {focal_at}",
                    "category_scores": {c: 0.0 for c in CATEGORIES}}

        win = self._window(tagged, focal)
        before = win["before"]
        after = win["after"]
        around = before + [focal] + after

        behavior = self._focal_behavior(focal)
        evidence: list[dict] = []
        scores = {c: 0.0 for c in CATEGORIES}
        reasoning: list[str] = []

        # ---- 统计窗口内信号 ----
        recv = [t for t in around if t.direction == "received"]
        sent = [t for t in around if t.direction == "sent"]

        stress_disc = [t for t in recv if "stress_disclosure" in t.tags]
        push_commit = [t for t in sent if "push_commit" in t.tags]
        push_reassure = [t for t in sent if "push_reassure" in t.tags]
        self_dep = [t for t in recv if "self_deprec" in t.tags]
        threat_perc = [t for t in recv if "harsh_threat_perceive" in t.tags]
        devalue = [t for t in recv if "harsh_devalue" in t.tags]
        warm = [t for t in recv if "warm_reconnect" in t.tags]
        apology = [t for t in recv if "apology" in t.tags]
        harsh = [t for t in recv if t.tags and ("harsh_attack" in t.tags or "harsh_devalue" in t.tags)]
        avoids = [t for t in recv if "avoidance" in t.tags or "cold_brief" in t.tags]

        # ---- 现实压力 ----
        if stress_disc:
            scores["real_world_pressure"] += self.W["real_world_pressure"]["stress_disclosure_near"]
            evidence += [self._ev(t, "外部压力陈述") for t in stress_disc[:4]]
        if len(stress_disc) >= 2:
            scores["real_world_pressure"] += self.W["real_world_pressure"]["stress_disclosure_dense"]
        # 服软后紧接着暴露外部压力 = 退缩性道歉
        if behavior == "服软" and stress_disc:
            scores["real_world_pressure"] += self.W["real_world_pressure"]["apology_then_disclose"]

        # ---- 关系威胁 ----
        if push_commit:
            scores["relationship_threat"] += self.W["relationship_threat"]["push_commit_before"]
            evidence += [self._ev(t, "我方前置逼承诺/要答案") for t in push_commit[:3]]
        if push_reassure:
            scores["relationship_threat"] += self.W["relationship_threat"]["push_reassure_before"]
        if threat_perc:
            scores["relationship_threat"] += self.W["relationship_threat"]["harsh_threat_perceive"]
            evidence += [self._ev(t, "对方感知到被威胁/被逼迫") for t in threat_perc[:3]]
        if devalue and push_commit:
            scores["relationship_threat"] += self.W["relationship_threat"]["harsh_devalue_under_push"]
            evidence += [self._ev(t, "逼问下的关系贬损") for t in devalue[:3]]
        if avoids and push_commit and not stress_disc:
            scores["relationship_threat"] += self.W["relationship_threat"]["cold_under_push"]

        # ---- 试探 ----
        # 服软后自我拆台：焦点道歉句后 ~30 分钟内出现转折/否定
        qualified = self._apology_qualified(focal, after)
        if behavior == "服软" and qualified:
            scores["testing"] += self.W["testing"]["apology_qualified"]
            evidence.append(self._ev(qualified, "服软后自我拆台/有条件"))
        # 狠话后短时间秒暖/索取 = 翻转（像换东西）
        if behavior == "狠话" and warm:
            scores["testing"] += self.W["testing"]["harsh_then_warm"]
            evidence += [self._ev(t, "狠话后短时间秒暖/主动索取") for t in warm[:3]]
        if behavior == "服软" and warm and devalue:
            scores["testing"] += self.W["testing"]["deference_flip"]

        # ---- 情绪崩溃 ----
        if len(self_dep) >= 2:
            scores["emotional_collapse"] += self.W["emotional_collapse"]["self_deprec_dense"]
            evidence += [self._ev(t, "高密度贬己/自我否定") for t in self_dep[:3]]
        # 剧烈摆荡：窗口内同时出现狠话与示弱，且时间紧凑
        swings = self._count_swings(around)
        if swings >= 2:
            scores["emotional_collapse"] += self.W["emotional_collapse"]["volatile_swing"]
            reasoning.append(f"窗口内检测到 {swings} 次'狠话↔示弱'剧烈摆荡")
        if not stress_disc and (self_dep or harsh):
            scores["emotional_collapse"] += self.W["emotional_collapse"]["no_external_cause"]

        # 焦点行为本身入证据
        evidence.insert(0, self._ev(focal, f"焦点行为={behavior}"))

        # ---- 汇总 ----
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top_cat, top_score = ranked[0]
        second_cat, second_score = ranked[1]

        # 数据不足：没有任何有效信号
        total_signal = sum(scores.values())
        if total_signal < 8.0:
            return {"status": "insufficient_data",
                    "focal_at": focal_at, "focal_behavior": behavior,
                    "category_scores": {c: round(v, 1) for c, v in scores.items()},
                    "evidence": evidence,
                    "reasoning": ["窗口内行为/上下文信号过少，无法可靠归因，标待确认，不硬猜"],
                    "total_signal": round(total_signal, 1)}

        # 置信度：基于 top 与 second 的分差 + 绝对强度（0-1 归一）
        gap = top_score - second_score
        confidence = self._confidence(top_score, gap)
        # 信号打架（top 与 second 接近）→ 降置信并标注
        mixed = bool(second_score >= top_score * 0.6 and second_score > 0)
        if mixed:
            confidence = min(confidence, 0.55)

        # 推理要点（人话解释组合逻辑）
        reasoning = self._build_reasoning(
            behavior, scores, stress_disc, push_commit, threat_perc,
            devalue, self_dep, warm, qualified, mixed)

        result = {
            "status": "pending_confirm" if mixed else "attributed",
            "focal_at": focal_at,
            "focal_behavior": behavior,
            "primary": {"category": top_cat, "category_cn": CATEGORIES[top_cat],
                        "score": round(top_score, 1),
                        "confidence": round(confidence, 2)},
            "secondary": ({"category": second_cat, "category_cn": CATEGORIES[second_cat],
                           "score": round(second_score, 1)}
                          if second_score > 0 else None),
            "category_scores": {c: round(v, 1) for c, v in scores.items()},
            "evidence": evidence,
            "reasoning": reasoning,
            "mixed_signals": mixed,
        }
        return result

    # -- 辅助 ------------------------------------------------------------- #
    def _find_focal(self, tagged: list[TaggedMsg], focal_at: str) -> Optional[TaggedMsg]:
        # 精确时间戳匹配，允许秒级偏差
        ft = parse_dt(focal_at)
        if ft is None:
            return None
        best = None
        best_diff = None
        for t in tagged:
            if t.dt is None:
                continue
            diff = abs((t.dt - ft).total_seconds())
            if diff <= 120:  # 焦点时间戳前后 2 分钟
                if best_diff is None or diff < best_diff:
                    best, best_diff = t, diff
        return best

    def _window(self, tagged: list[TaggedMsg], focal: TaggedMsg) -> dict:
        before, after = [], []
        if focal.dt is None:
            return {"before": before, "after": after}
        lo = focal.dt - timedelta(hours=self.before_hours)
        hi = focal.dt + timedelta(hours=self.after_hours)
        for t in tagged:
            if t.dt is None or t is focal:
                continue
            if lo <= t.dt < focal.dt:
                before.append(t)
            elif focal.dt < t.dt <= hi:
                after.append(t)
        return {"before": before, "after": after}

    @staticmethod
    def _ev(t: TaggedMsg, role: str) -> dict:
        return {"at": t.at, "direction": t.direction,
                "content": t.content[:120], "role": role,
                "tags": list(t.tags)}

    def _apology_qualified(self, focal: TaggedMsg, after: list[TaggedMsg]) -> Optional[TaggedMsg]:
        if "apology" not in focal.tags:
            return None
        # 服软后 30 分钟内出现转折/否定词 → 自我拆台
        qual_words = ["但是我", "重要吗", "都在闹", "无所谓", "也没用", "不重要",
                      "我做了", "算了", "有什么好", "没什么好"]
        cutoff = focal.dt + timedelta(minutes=30) if focal.dt else None
        for t in after:
            if cutoff and t.dt and t.dt > cutoff:
                continue
            if _has_any(t.content, qual_words):
                return t
        return None

    @staticmethod
    def _count_swings(around: list[TaggedMsg]) -> int:
        """统计'狠话↔示弱'摆荡次数：相邻接收消息在(贬损/攻击)与(贬己/道歉)间切换。"""
        recv = [t for t in around if t.direction == "received" and t.dt is not None]
        recv.sort(key=lambda t: t.dt)
        swings = 0
        prev_kind = None
        for t in recv:
            kind = None
            if "harsh_attack" in t.tags or "harsh_devalue" in t.tags:
                kind = "harsh"
            elif "self_deprec" in t.tags or "apology" in t.tags:
                kind = "soft"
            if kind and prev_kind and kind != prev_kind:
                swings += 1
            if kind:
                prev_kind = kind
        return swings

    @staticmethod
    def _confidence(top_score: float, gap: float) -> float:
        # top 越高、与次名差距越大 → 越可信
        strength = min(1.0, top_score / 30.0)
        gap_factor = min(1.0, gap / 15.0)
        return max(0.35, min(0.95, 0.45 + 0.35 * strength + 0.20 * gap_factor))

    @staticmethod
    def _build_reasoning(behavior, scores, stress_disc, push_commit, threat_perc,
                         devalue, self_dep, warm, qualified, mixed) -> list[str]:
        r = [f"焦点行为=【{behavior}】；四压力源投票分：" +
             " / ".join(f"{CATEGORIES[c]}={v:.1f}" for c, v in scores.items())]
        if stress_disc:
            r.append(f"对方窗口内 {len(stress_disc)} 次暴露具体外部负荷"
                      f"（如'{stress_disc[0].content[:30]}'）→ 指向现实压力")
        if push_commit:
            r.append(f"我方前置 {len(push_commit)} 次逼承诺/要答案（如'{push_commit[0].content[:30]}'）"
                     f"→ 对方处于被墙角化位置")
        if threat_perc:
            r.append("对方明说被逼迫/被威胁 → 狠话是关系威胁下的防御，而非本意想分")
        if devalue and warm:
            r.append("贬损关系后短时间内出现秒暖/主动索取 → 反证狠话是应激、非真实去值")
        if qualified:
            r.append("服软后立刻自我拆台 → 道歉有条件、非纯粹愧疚")
        if len(self_dep) >= 2 and not stress_disc:
            r.append("高密度贬己且无具体外部原因 → 情绪耗竭/崩溃成分")
        if mixed:
            r.append("⚠ 主次信号接近（混合信号），已降置信，建议人工复核，标待确认")
        return r


# --------------------------------------------------------------------------- #
# 4. 一键入口：从正式库取消息 -> 归因
# --------------------------------------------------------------------------- #
def attribute_event(db_path: str, person_id: str, focal_at: str,
                    before_hours: float = 12.0, after_hours: float = 12.0) -> dict:
    """从 chat_messages 取该 person 全部消息，对 focal_at 时刻的对方行为做压力源归因。
    正式库只读。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT direction, content, created_at FROM chat_messages "
        "WHERE person_id=? ORDER BY created_at ASC", (person_id,)).fetchall()
    conn.close()
    messages = [dict(r) for r in rows]
    attr = StressAttributor(before_hours=before_hours, after_hours=after_hours)
    result = attr.attribute(messages, focal_at)
    result["message_count"] = len(messages)
    return result
