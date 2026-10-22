# -*- coding: utf-8 -*-
"""
LoveAI v3.3 — 承诺结构层（Rusbult 投资模型落地 · 零 LLM）

本模块只做一件事：把聊天文本变成 Rusbult 承诺公式里那两项缺失结构变量的可复算数值。
全部为纯规则 + 纯算术，不调用任何大模型；同一输入必定得到同一输出。

为什么要有这个文件
------------------
既有 affection_engine 的 18 维权重体系，在关系科学口径下只覆盖了 Rusbult
公式三项中的一项（O_X 关系结果价值 / 满意度侧）。另两项——投资量 I_X 与
替代性 O_Y——在代码里一个维度都没有。这不是"再补几个心理维度"，而是公式缺项：

    O_X = sum(w_i * a_i)     关系结果价值（奖励 - 成本）
    SAT = O_X - CL           满意度
    A_Y = O_Y - CL           替代性
    I_X = sum(w_j * r_j)     投资量（分手即归零的不可携带资源）
    COM = O_X + I_X - O_Y    承诺

Rusbult (1980) 实测：满意度单独预测承诺 R = .65；加入替代性与投资量后 R = .78，
删掉任一项预测力显著下降；满意度与承诺仅弱相关 R = .24（高投资 + 差替代 =
不满意也不走）。Le & Agnew (2003) 元分析 52 研究 / 11,582 人 / 5 国复现该结构。

本模块不产生好感度总分，只产生 I_X / O_Y 两项原料指标 + 一条可手算的合成链。

======================================================================
借鉴来源（全部为可溯源外部公开研究，非本项目自造）
======================================================================

【来源 A】Rusbult 投资模型本体
  C. E. Rusbult (1980). Commitment and Satisfaction in Romantic Associations:
  A Test of the Investment Model. JESP, 16(2), 172-186.
    A1 投资两分类（本模块词表按此划分）
       内在投资 intrinsic  —— 时间、情绪能量、自我表露、个人隐私、为伴侣作的牺牲
       外在投资 extrinsic  —— 双方共同的朋友、共同回忆、该关系特有的活动与所有物
    A2 投资的判别条件：不能独立从关系中抽取，关系结束即随之消失
       （该条件排除了"金钱"这类可携带资源，故本模块不计礼物金额）
    A3 承诺方程 COM = O_X + I_X - O_Y

【来源 B】投资模型量表中文版（提供"可测量"的合法性）
  刘新春、赵琳、张乐乐、杨丽.《中国临床心理学杂志》2015(6): 1020-1023.
    374 名恋爱关系持续一周以上的大三学生
    四因素结构拟合：chi2/df=3.29, RMSEA=0.078, SRMR=0.076, CFI=0.95, NFI=0.93, IFI=0.95
    各分量表内部一致性 alpha = 0.76~0.93；与承诺量表、双向适应量表 |r| = 0.20~0.63

【来源 C】Johnson 三种承诺（解释"为什么 I_X 独立于满意度"）
  M. P. Johnson (1999)，转引自《亲密关系》第 6 版
    个人承诺 personal   —— 想留（对应满意度 O_X）
    强迫承诺 constraint —— 不得不留（对应投资量 I_X + 替代性 O_Y）
    道德承诺 moral      —— 觉得应该留
  本模块算的正是第二种：离开代价。

【来源 D】社会渗透理论（自我表露深度的层次判据）
  Altman & Taylor (1973). Social Penetration: The Development of Interpersonal
  Relationships.
    表露由"广"与"深"两维构成；深度按话题层级由表层推进至核心层
    本模块的 _DISCLOSURE_* 即"核心层话题"命中集（家庭/创伤/恐惧/长远打算），
    不计兴趣爱好等表层

【来源 E】自我知觉与努力合理化（支撑 I_X 的因果方向）
  富兰克林效应；认知失调理论（Festinger, 1957）
    "让你产生好感的往往是你为其付出过的人，而非一直付出给你的人"
     大脑对"我为何投入"作自我合理化 -> 投入本身生成好感
    推论：I_X 不只是承诺的结果，也是承诺的成因，故单列成轨有方向意义，非重复计分
    *注：该效应实验重复性在学界仍有争议，此处仅取方向性提示，不参与任何数值计算*

【来源 F】依恋安全感的操作化定义（用于 predictability 轨）
  M. Ainsworth & S. Bell (1970), Child Development（陌生情境实验）
  Ainsworth, Blehar, Waters & Wall (1978), Patterns of Attachment
    原始 106 个孩子的三类比例：安全型约 65%、回避型约 21%、反抗型约 14%
  van IJzendoorn & Kroonenberg (1988)：32 项研究 / 8 国 / 1990 组配对
    跨国平均比例复现为同一组数 65 / 21 / 14
  核心操作化（本模块据此定义 predictability）：
    安全感的单位不是"被爱了多少"，而是"说到做到"的次数
    它不靠浓度，靠可预报性
    信号检测论（Tanner & Swets, 1954）：信息不足时误报与漏报不可同时降低，
      只能选择承担哪一边；Nesse (1994) 将该账推及身体防御系统
    推论：焦虑型的"过度报警"不是缺陷而是最优策略——当回应不可预报时，
      唯一理性做法是把信号开到最大；故可预报性低会同时抬高报警强度。

======================================================================
诚实边界（不得夸大）
======================================================================
  · 本模块是文本层代理指标，不是心理测量。所有数字都只是"聊天记录里出现了
    这类信号多少次"，不等于当事人真的有那么多投资或替代。
  · 词典法对反讽、玩梗、方言、缩写天然失效；低命中不等于关系差，
    只等于"文本层没有这类信号"。
  · I_X / O_Y 的绝对水平不可跨案例比较（各人聊天风格差异极大），
    只有同一人跨时间的变化才有解释力。这是本模块最重要的使用约束。
  · 合成式 COM = O_X + I_X - O_Y 来自 Rusbult 的回归结构，
    本模块的线性合成未经本项目样本标定，只作通路演示，不作判决。
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Iterable

# ======================================================================
# 词表（种子集；按同一口径可持续扩充，扩充时须同步补验收样例）
# ======================================================================

# ---- I_X：共同经历（对应 Rusbult 的"外在投资"）----
# 判据：共有指代 + 事件锚点。单纯出现"我们"过泛，故分强弱两档。
_SHARED_STRONG = (
    "第一次", "一起去", "一起做", "一起去过", "我们的", "咱俩", "咱们",
    "你和我", "我和你", "共同的", "陪你去", "陪你过",
)
_SHARED_WEAK = ("我们", "一起", "记得", "那次", "上次", "当时", "之前那")

# ---- I_X：自我表露核心层（对应 Rusbult 的"内在投资"）----
# 判据：Altman & Taylor 社会渗透的"深层"话题，不计兴趣爱好等表层。
_DISCLOSURE_FAMILY = ("我爸", "我妈", "我父母", "我家人", "我家里", "我姐", "我哥", "我弟", "我妹", "我奶奶", "我爷爷")
_DISCLOSURE_PAST = ("小时候", "以前我", "我受过", "被伤害", "我经历", "我离过", "我前任", "我一直没")
_DISCLOSURE_FEAR = ("我怕", "我担心", "我害怕", "我焦虑", "我不敢", "我讨厌自己", "我不够好", "我不配")
_DISCLOSURE_FUTURE = ("我以后", "我想成为", "我打算", "我的目标", "我想过", "将来我", "我理想")

# ---- I_X：付出事件（内在投资中的"为伴侣作牺牲"）----
_GIVING = (
    "我给你", "我帮你", "我陪你", "我特意", "我买了", "我带你去", "我请你",
    "我为你", "我抽时间", "我早起", "我等你", "我跑一趟", "我给你做",
    "我送你", "我接你", "我寄", "我给你留",
)

# ---- O_Y：对方侧替代信号（"她还有别的选择"的文本证据）----
_ALT_PARTNER = (
    "相亲", "介绍对象", "有人追", "别人追", "我前任", "还有人在", "追我的人",
    "家里介绍", "我妈介绍", "别人约我", "有人约", "同事追",
)
# ---- O_Y：我方侧替代信号（我自己的外部选项，按 Rusbult 定义属 O_Y）----
_ALT_SELF = (
    "我前任", "相亲", "我相亲", "有人给我介绍", "我朋友介绍", "我也可以找",
    "我不缺人", "我身边也有", "别人也喜欢我",
)

# ---- 比较性表达（替代价值被抬高的语言标志）----
_COMPARE = ("你看人家", "别人都", "人家都", "谁谁谁", "你看别人", "人家男朋友", "人家女朋友", "别人家的")

# ---- 撤回/冷淡信号（替代性上升的可观测后果，单独成项不混入 O_Y）----
_WITHDRAWAL_WORDS = ("哦", "嗯", "好的", "知道了", "再说吧", "随便", "都行", "呵呵", "呵", "嗯嗯", "行吧", "随你", "你决定")
_WITHDRAWAL_SHORT_LEN = 4  # 收到消息长度 <= 4 字计为短回复

# 可靠样本下限：每 100 条命中率在 n < 50 时会剧烈抖动
# （3 条命中 1 次即 33.3），故所有带 n 的输出都附 reliable 标记；
# reliable=False 时数值只能用于通路自检，不得据以下结论。
_MIN_RELIABLE = 50

# ---- 可预报性：承诺语句（"说到做到"的分子端）----
_PROMISE = ("我明天", "我晚点", "我一定", "我会", "我回头", "下次", "我尽快", "我马上", "到时候", "我等下")

_PUNCT = re.compile(r"[\s\u3000]+")
_DT_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?")


def _norm(text: str) -> str:
    return _PUNCT.sub("", text or "")


def _hit_once(text: str, words: Iterable[str]) -> int:
    """长词优先贪心匹配：命中的片段被挖掉，避免"一起去"与"一起"重复计数。"""
    t = _norm(text)
    if not t:
        return 0
    n = 0
    for w in sorted(words, key=len, reverse=True):
        if w and w in t:
            n += t.count(w)
            t = t.replace(w, "\u0000")
    return n


def _hit_weighted(text: str, ranked) -> int:
    """带权贪心匹配：ranked 为 [(词, 权重), ...]。

    与 _hit_once 的关键差别：**多张词表共享同一遍挖空状态**。
    因此 "一起去爬山" 只会被计一次（命中 strong 的"一起去"后，
    weak 的"一起"已被挖空、不可见），不会双重计数。
    """
    t = _norm(text)
    if not t:
        return 0
    total = 0
    for w, wt in sorted(ranked, key=lambda x: len(x[0]), reverse=True):
        if not w or w not in t:
            continue
        c = t.count(w)
        total += c * wt
        t = t.replace(w, "\u0000")
    return total


def _rate_raw(hits: int, total: int) -> float:
    """未取整的命中率（供需要再运算的场景使用，如求和多类率）。"""
    if total <= 0:
        return 0.0
    return hits / total * 100.0


def _rate(hits: int, total: int) -> float:
    """归一化口径：每 100 条消息命中几次（保留 2 位小数）。"""
    return round(_rate_raw(hits, total), 2)


def _iter_msgs(messages: Iterable[dict]):
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content", "") or ""
        if not content.strip():
            continue
        yield m, content


def _ts_of(m: dict):
    """从消息里取时间戳（兼容多种字段名），取不到返回 None。"""
    for k in ("ts", "timestamp", "time", "create_time", "date"):
        v = m.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and v.strip():
            s = v.strip()
            try:
                return float(s)
            except ValueError:
                m2 = _DT_RE.match(s)
                if m2:
                    y, mo, d, h, mi, s2 = (int(x) if x else 0 for x in m2.groups())
                    try:
                        return _dt.datetime(y, mo, d, h, mi, s2).timestamp()
                    except ValueError:
                        return None
    return None


# ======================================================================
# I_X —— 投资量
# ======================================================================
def investment_index(messages: Iterable[dict]) -> dict:
    """投资量 I_X 代理指标（Rusbult 1980 Eq.[4]：I_X = sum(w_j * r_j)）。

    只计"分手即归零、不可携带"的投入，分三类：
      shared     共同经历（外在投资）
      disclosure 自我表露核心层（内在投资）
      giving     付出事件（内在投资中的牺牲）

    按 direction 分侧：sent = 我方投入，received = 对方投入。
    I_X 在 Rusbult 模型里是个体自己的量，故调用时一般取 self。

    Args:
        messages: 每条 dict，需含 "content"；可选 "direction"
                  （sent = 我方发出 / received = 对方发来），缺省按 received 计。

    Returns:
        {"self": {...三类率 + index...}, "partner": {...},
         "totals": {...}, "detail": {...}}
    """
    hits = {
        "sent": {"shared": 0, "disclosure": 0, "giving": 0},
        "received": {"shared": 0, "disclosure": 0, "giving": 0},
    }
    totals = {"sent": 0, "received": 0}

    for m, content in _iter_msgs(messages):
        side = "sent" if m.get("direction") == "sent" else "received"
        totals[side] += 1
        h = hits[side]
        # strong 权重 2 / weak 权重 1，同一遍挖空匹配——"一起去"不会被
        # strong 与 weak 各计一次（两次独立调用会重复计数）。
        h["shared"] += _hit_weighted(
            content,
            [(w, 2) for w in _SHARED_STRONG] + [(w, 1) for w in _SHARED_WEAK],
        )
        h["disclosure"] += (
            _hit_once(content, _DISCLOSURE_FAMILY)
            + _hit_once(content, _DISCLOSURE_PAST)
            + _hit_once(content, _DISCLOSURE_FEAR)
            + _hit_once(content, _DISCLOSURE_FUTURE)
        )
        h["giving"] += _hit_once(content, _GIVING)

    def _side(s: str) -> dict:
        n = totals[s]
        # 先算未取整的精确率，再统一取两位——避免"先各自 round 再相加"引入的
        # 0.01 级累加误差（233.33 + 33.33 + 33.33 = 299.99，而非 300.00）。
        raw = {
            "shared": _rate_raw(hits[s]["shared"], n),
            "disclosure": _rate_raw(hits[s]["disclosure"], n),
            "giving": _rate_raw(hits[s]["giving"], n),
        }
        rate = {k: round(v, 2) for k, v in raw.items()}
        # index = 三类率之和。未标定、等权；clip 到 100 会在高命中时掩盖差异，
        # 故同时给出未截断的 raw_sum 供核对。
        rate["raw_sum"] = round(sum(raw.values()), 2)
        rate["index"] = round(min(100.0, rate["raw_sum"]), 2)
        rate["n"] = n
        rate["reliable"] = n >= _MIN_RELIABLE
        return rate

    return {
        "self": _side("sent"),
        "partner": _side("received"),
        "totals": {"self_msgs": totals["sent"], "partner_msgs": totals["received"]},
        "detail": {
            "self_hits": dict(hits["sent"]),
            "partner_hits": dict(hits["received"]),
        },
    }


# ======================================================================
# O_Y —— 替代性
# ======================================================================
def alternative_index(messages: Iterable[dict]) -> dict:
    """替代性 O_Y 代理指标（Rusbult 1980 Eq.[3]：A_Y = O_Y - CL）。

    Rusbult 的 O_Y 是个体自己对"离开这段关系后最好选择"的评价。
    文本层能观测到的是双方各自释放的替代信号，故分两侧报告：

      self_side    我方外部选项信号（相亲/被介绍/身边有别人）
      partner_side 对方外部选项信号（相亲/有人追/前任）
      compare      比较性表达（抬高外部参照，属替代价值被感知的信号）

    本模块不把冷淡并入 O_Y——冷淡是替代性上升的后果而非替代性本身，
    单列为 withdrawal（见 withdrawal_index）。
    """
    h = {"self_side": 0, "partner_side": 0, "compare": 0}
    n_self = n_partner = 0

    for m, content in _iter_msgs(messages):
        side = "sent" if m.get("direction") == "sent" else "received"
        if side == "sent":
            n_self += 1
        else:
            n_partner += 1
        h["compare"] += _hit_once(content, _COMPARE)
        if side == "sent":
            h["self_side"] += _hit_once(content, _ALT_SELF)
        else:
            h["partner_side"] += _hit_once(content, _ALT_PARTNER)

    both = n_self + n_partner
    self_rate = _rate(h["self_side"], n_self)
    partner_rate = _rate(h["partner_side"], n_partner)

    return {
        "self_side": self_rate,
        "partner_side": partner_rate,
        "compare": _rate(h["compare"], both),
        "pressure": partner_rate,
        "detail": {"hits": dict(h), "self_msgs": n_self, "partner_msgs": n_partner},
    }


# ======================================================================
# 撤回/冷淡（单列，不混入 O_Y）
# ======================================================================
def withdrawal_index(messages: Iterable[dict]) -> dict:
    """撤回/冷淡信号：敷衍短句 + 话题终结词（只统计 received 侧）。

    口径：短回复 = 去空白后长度 <= _WITHDRAWAL_SHORT_LEN 的消息；
          敷衍词按 _WITHDRAWAL_WORDS 命中计。
    """
    hit_word = short = n = 0
    for m, content in _iter_msgs(messages):
        if m.get("direction") == "sent":
            continue
        n += 1
        if len(_norm(content)) <= _WITHDRAWAL_SHORT_LEN:
            short += 1
        hit_word += _hit_once(content, _WITHDRAWAL_WORDS)
    short_rate = _rate(short, n)
    filler_rate = _rate(hit_word, n)
    return {
        "short_reply_rate": short_rate,
        "filler_rate": filler_rate,
        # 从原始计数直接合成（(short+filler)/n*100/2），不经过"两次 round 再平均"，
        # 否则 66.67*0.5=33.335 这类中间值会引入 0.01 级舍入噪声。
        "index": round(_rate(short + hit_word, n) / 2.0, 2),
        "n": n,
        "reliable": n >= _MIN_RELIABLE,
        "total": n,
    }


# ======================================================================
# 可预报性（安全感操作化，来源 F）
# ======================================================================
def predictability_index(messages: Iterable[dict]) -> dict:
    """可预报性：依恋安全感的核心操作化量（来源 F）。

    安斯沃斯一系的结论是——安全感的单位不是"被爱了多少"，而是"说到做到"的次数。
    文本层能算两件事：

      reply_consistency  回复间隔的一致性 = 1 / (1 + 变异系数)
         CV = 标准差 / 均值。间隔越稳定，CV 越小，该值越接近 1。
         需消息带时间戳；缺时间戳时返回 None 并在 note 里说明。
      promise_density_*  承诺语句密度（每 100 条命中"我会/我明天/下次"）
         这是"说到做到"的发出端；本模块无法验证兑现端，
         因此只作密度报告，不得单独作为"安全感低"的判据。
    """
    ts_list = []
    promise_self = promise_partner = 0
    n_self = n_partner = 0

    for m, content in _iter_msgs(messages):
        side = "sent" if m.get("direction") == "sent" else "received"
        if side == "sent":
            n_self += 1
            promise_self += _hit_once(content, _PROMISE)
        else:
            n_partner += 1
            promise_partner += _hit_once(content, _PROMISE)
        t = _ts_of(m)
        if t is not None:
            ts_list.append(t)

    out = {
        "promise_density_self": _rate(promise_self, n_self),
        "promise_density_partner": _rate(promise_partner, n_partner),
        "n_with_ts": len(ts_list),
    }

    if len(ts_list) < 5:
        out.update({
            "reply_consistency": None,
            "cv": None,
            "note": "带时间戳的消息仅 %d 条（需 >=5），无法计算回复一致性。" % len(ts_list),
        })
        return out

    ts_list.sort()
    gaps = [b - a for a, b in zip(ts_list, ts_list[1:]) if b > a]
    if len(gaps) < 4:
        out.update({
            "reply_consistency": None,
            "cv": None,
            "note": "有效时间间隔不足 4 个，无法计算一致性。",
        })
        return out

    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        out.update({"reply_consistency": None, "cv": None, "note": "间隔均值为 0，异常输入。"})
        return out
    var = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    cv = (var ** 0.5) / mean
    out.update({
        "cv": round(cv, 4),
        "reply_consistency": round(1.0 / (1.0 + cv), 4),
        "note": (
            "回复间隔一致性 = 1/(1+CV)。该值只描述时间节奏的稳定度，"
            "不区分'稳定地秒回'与'稳定地隔天才回'——两者可预报性都高。"
        ),
    })
    return out


# ======================================================================
# 承诺通路链（Rusbult 1980 Eq.[5]）
# ======================================================================
def commitment_chain(satisfaction, investment, alternatives) -> dict:
    """按 Rusbult 承诺方程合成三项。COM = O_X + I_X - O_Y。

    三项必须同尺度才能相减，统一要求 0~100：

      satisfaction  O_X 侧——由既有 18 维总分（get_total）或本模块外的
                    满意度代理提供。不得用 50 当默认值掩盖缺失。
      investment    I_X 侧——取 investment_index()["self"]["index"]。
      alternatives  O_Y 侧——取 alternative_index()["pressure"]（对方侧压力）。

    合成口径（线性、未标定，仅作通路演示）：
        COM = satisfaction + investment - alternatives
        再线性映射回 0~100：COM_100 = (COM + 100) / 300 * 100
    """
    missing = []
    vals = {"o_x": satisfaction, "i_x": investment, "o_y": alternatives}
    for k, v in vals.items():
        if v is None:
            missing.append(k)

    if missing:
        return {
            **{k: (None if v is None else round(float(v), 2)) for k, v in vals.items()},
            "com_raw": None,
            "com_100": None,
            "missing": missing,
            "note": "缺少 " + "、".join(missing) + "，无法合成承诺——通路链要求三项齐全。",
        }

    o_x, i_x, o_y = (float(v) for v in (satisfaction, investment, alternatives))
    com_raw = o_x + i_x - o_y            # 理论范围 -100 ~ 200
    com_100 = (com_raw + 100.0) / 300.0 * 100.0
    return {
        "o_x": round(o_x, 2),
        "i_x": round(i_x, 2),
        "o_y": round(o_y, 2),
        "com_raw": round(com_raw, 2),
        "com_100": round(max(0.0, min(100.0, com_100)), 2),
        "missing": [],
        "note": (
            "COM = O_X + I_X - O_Y。三项缺任何一项即无法合成——这就是通路链的约束。"
            "该合成为线性、未经本项目样本标定，只作结构演示，不作判决。"
        ),
    }


def analyze_commitment(messages: Iterable[dict], satisfaction=None) -> dict:
    """一站式：I_X / O_Y / 撤回 / 可预报性 + 承诺链（satisfaction 缺失则不合成）。"""
    msgs = list(messages or [])
    inv = investment_index(msgs)
    alt = alternative_index(msgs)
    wd = withdrawal_index(msgs)
    pred = predictability_index(msgs)
    chain = commitment_chain(
        satisfaction=satisfaction,
        investment=inv["self"]["index"],
        alternatives=alt["pressure"],
    )
    return {
        "investment": inv,
        "alternatives": alt,
        "withdrawal": wd,
        "predictability": pred,
        "commitment": chain,
        "engine": "commitment_model/zero-llm",
    }


if __name__ == "__main__":  # 自检样例（可手算核对）
    demo = [
        {"content": "我们第一次一起去爬山，记得当时你怕高", "direction": "sent"},
        {"content": "嗯", "direction": "received"},
        {"content": "我给你带了你爱吃的那个", "direction": "sent"},
        {"content": "哦", "direction": "received"},
        {"content": "我妈身体不太好，我一直担心", "direction": "sent"},
        {"content": "相亲", "direction": "received"},
    ]
    r = analyze_commitment(demo, satisfaction=60.0)
    print("I_X self :", r["investment"]["self"])
    print("O_Y      :", {k: r["alternatives"][k] for k in ("self_side", "partner_side", "compare", "pressure")})
    print("冷淡     :", r["withdrawal"])
    print("可预报性 :", r["predictability"])
    print("承诺链   :", r["commitment"])
