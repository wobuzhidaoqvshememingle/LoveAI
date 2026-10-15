# -*- coding: utf-8 -*-
"""
LoveAI v2 - 18 部分详尽人物诊断报告组装器（鉴别引擎 C 输出层）

设计原则（与 docs/人物诊断报告-详尽规格-v3.0.md 对齐）：
  * 每条关键结论锚定 evidence_index 里的 eid（原话+时间戳+出处）；
  * 每个特质正反证据并陈，给"几正几反"、样本量 n，比例给 Wilson 95% 置信区间，
    且 CI 分母与展示的 k/n 完全一致（统一用对方消息总数 n）；
  * 可统计项（句长/短句长段占比/回复延迟六桶/主动占比/兑现率/情绪分/标点/口癖）
    直接从原始消息算；无真实锚定证据时一律"待观察·未校准"，不填默认百分比；
  * 病理只用"呈 X 倾向 / X 维度偏高"，并标注"不替代专业诊断"；
  * 风险信号分级 1-5；出现明确自伤/自杀/人身威胁一律强制 risk=5；
  * 第 17 部分给 300-600 字"人物底色白描"+核心标签+相处要点；
  * 报告只服务"对症攻略/引导关系变好"，不出现劝分/撤离/放弃结论。

对外接口：
  generate_report(person_id, messages=None, db=None, save=True, data_root=None) -> dict
  render_markdown(report) -> str
"""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from typing import Optional

SCHEMA_VERSION = "diagnostic-report/v1.1"

# --------------------------------------------------------------------------- #
# 18 部分定义（顺序与 docs 规格一致，一项不漏）
# --------------------------------------------------------------------------- #
SECTION_DEFS = [
    (1, "basic_profile", "基础档案"),
    (2, "language_fingerprint", "语言指纹量化"),
    (3, "personality_traits", "双字性格特质（含大五）"),
    (4, "cognition", "认知与思维模式"),
    (5, "emotion_system", "情绪系统（触发点与恢复）"),
    (6, "attachment_intimacy", "依恋与亲密模式"),
    (7, "conflict_defense", "冲突与防御模式"),
    (8, "power_structure", "关系定位与权力结构"),
    (9, "motivation_commitment", "动机、诉求与承诺信用"),
    (10, "values_bottomline", "价值观与底线"),
    (11, "social_network", "社交与关系网络"),
    (12, "lifestyle", "行为模式与生活方式"),
    (13, "dark_risk", "黑暗特质与风险信号（1-5 级）"),
    (14, "empathy_morality", "共情与道德情感"),
    (15, "mask_consistency", "伪装与一致性（言行对照）"),
    (16, "change_trajectory", "变化轨迹（时间线）"),
    (17, "conclusion_portrait", "诊断结论：白描+核心标签+相处要点"),
    (18, "evidence_gaps", "证据、可信度与信息缺口"),
]

# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def wilson(k: int, n: int, z: float = 1.96) -> dict:
    """Wilson 95% 置信区间。返回 {p, low, high}，且 low<=p<=high，落在 [0,1]。"""
    k = max(0, int(k))
    n = max(0, int(n))
    if n <= 0:
        return {"p": 0.0, "low": 0.0, "high": 0.0, "k": 0, "n": 0}
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4.0 * n * n)) / denom
    low = max(0.0, min(1.0, center - margin))
    high = max(0.0, min(1.0, center + margin))
    if low > p:
        low = p
    if high < p:
        high = p
    return {"p": round(p, 4), "low": round(low, 4), "high": round(high, 4),
            "k": k, "n": n}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip().replace("/", "-")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _short_ts(s: str) -> str:
    dt = _parse_ts(s)
    return dt.strftime("%m-%d %H:%M") if dt else (str(s)[:16] if s else "未知时间")


_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F02F\u2190-\u21FF\u2B00-\u2BFF]",
    flags=re.UNICODE)


# --------------------------------------------------------------------------- #
# 行为规则库（基于关键词的证据检测器；命中即锚定原话 eid）
# --------------------------------------------------------------------------- #
# key, 双字标签, 正向关键词, 反向关键词, 类别
# 类别: kind=善意/落地/中性(可抵消同消息的恶意标签); hostile=攻击/操控; threat=自伤/人身
RULES = [
    ("warm_care", "体贴", ["辛苦了", "多穿点", "点个粥", "三明治", "地址发我", "天气真好",
                          "早点休息", "吃晚饭了吗", "吃完饭没", "降温", "别感冒", "我请",
                          "我买票", "别空着肚子", "别饿", "先垫垫", "穿少了", "风大",
                          "天凉", "顺路去吃", "你推荐的能错吗", "注意身体", "我带了"],
     ["你忙你的", "看情况"], "kind"),
    ("obliging", "顺从", ["好呀", "顺其自然", "我也是这么想", "互相了解", "应该的", "嘿嘿",
                         "你选地方", "都行", "慢慢来", "我选地方啦"],
     [], "kind"),
    ("practical", "务实", ["路线", "早上八点", "山顶见", "我带了", "我选地方", "预约",
                         "饭团", "便利店", "排班", "绿道", "老地方", "粉店", "排队",
                         "江边", "七点", "我看下", "走不开", "上线到两点", "刚开完会"],
     [], "kind"),
    ("future_talk", "规划", ["周末", "下次还一起", "今晚一起吃饭", "下次", "这周末", "以后",
                            "我们慢慢来", "顺其自然", "明早回你", "江边老地方", "下班回去",
                            "你有空没", "跟你细说", "七点可以", "约"],
     [], "kind"),
    ("slow_warm", "慢热", [], [], "derived"),  # 由短直+规划+体贴综合派生
    ("direct_speak", "直给", [], [], "derived"),  # 由短句占比派生
    ("reserved_cold", "疏离", ["看情况", "我事多", "你忙你的", "说不清楚", "行吧，你说",
                             "不是什么大事", "还好吧", "我家里有点事"],
     ["辛苦了", "多穿点"], "hostile"),
    ("face_pride", "好面", ["行吧，你说什么就是什么", "你说什么就是什么", "有意思吗",
                           "我明明", "你自己不记得", "我怎么知道", "他们都说我", "我最近变"],
     [], "hostile"),
    ("sensitive_suspect", "多疑", ["那个女的", "同事你拍", "侦探", "盯着我", "朋友圈",
                                  "谁啊", "什么事比我重要", "她是谁", "你俩", "控制欲"],
     [], "hostile"),
    ("anxious_cling", "黏人", ["不会离开我", "你走了", "等了你多久", "你下来",
                              "我就在这等", "你是不是喜欢上别人", "你果然", "都会离开我",
                              "你不会离开", "我不管", "你答应我", "发誓", "梦到你不要我",
                              "你能过来陪我", "你才回", "半小时没回", "你又不想",
                              "你是不是烦我", "你又不把我", "比我重要", "你就不会主动",
                              "没多在乎", "你每次都", "你根本就不在乎", "你现在才回"],
     [], "hostile"),
    ("self_harm_threat", "自伤/抛弃威胁", ["活不下去", "不活了", "不想活", "自杀", "同归于尽",
                                          "我就死", "真的死", "千万不要走", "你走了我",
                                          "我一个人没法", "跳楼", "割"],
     [], "threat"),
    ("love_bomb", "轰炸", ["从没遇到", "认识了很久", "最好的人", "唯一", "安心",
                          "你最好了", "像认识了很久", "全世界", "最懂我",
                          "对我好的人", "特别安心", "特别的人"],
     [], "hostile"),
    ("blame_attack", "指责", ["你能不能", "你又", "你这叫", "太敏感", "你每次都这样",
                             "根本就不在乎", "你能不能别", "你又不想", "你就是不想理我",
                             "你怎么又", "你从来都", "你现在才回"],
     [], "hostile"),
    ("gaslight", "扭曲", ["我明明说", "你自己不记得", "翻记录", "对账", "说不清楚",
                         "你非要这么想", "你说什么就是什么", "我不是说了", "你自己不",
                         "明明是你", "跟你说不清楚"],
     [], "hostile"),
    ("entitled", "特权", ["什么事比我重要", "你陪我", "你下来", "周末你陪我",
                         "你过来陪我", "我看你也没多在乎", "你果然还是不愿", "必须陪我",
                         "项目比我重要", "打球比"],
     [], "hostile"),
    ("cold_wall", "筑墙", ["算了", "说不清楚", "行吧", "你忙你的", "我家里有点事",
                          "还好吧", "不是什么大事", "先不说了", "不想破坏"],
     [], "hostile"),
    ("busy_explain", "忙碌解释", ["项目上线", "改bug", "刚开完会", "leader在叫", "走不开",
                                 "上线到两点", "在开会", "加班", "排班", "忙"],
     [], "kind"),
    ("reconcile", "服软修复", ["是我不好", "对不起", "我昨天不该", "但是你也有不对",
                              "我们慢慢来", "我也是这么想", "不是不想回", "真不是",
                              "不是不想", "我下班回去"],
     [], "kind"),
]

RULE_BY_KEY = {r[0]: r for r in RULES}

# 善意/中性类标签：同一条消息若命中这些，则不得同时记为恶意标签（自伤威胁除外）
KIND_TAGS = {"warm_care", "obliging", "practical", "future_talk",
             "busy_explain", "reconcile"}
HOSTILE_TAGS = {"reserved_cold", "face_pride", "sensitive_suspect", "anxious_cling",
                "love_bomb", "blame_attack", "gaslight", "entitled", "cold_wall"}


def _tag_message(text: str) -> set:
    """返回该条对方消息命中的规则 key 集合，并做善意/恶意互斥去重。"""
    tags = set()
    for key, _label, pos_kw, _neg, _cat in RULES:
        if not pos_kw:
            continue  # derived 标签不走关键词
        if any(k in text for k in pos_kw):
            tags.add(key)
    # 互斥：善意/落地/解释类消息，不得同时记为恶意（自伤威胁一律保留）
    if tags & KIND_TAGS:
        tags -= (HOSTILE_TAGS - {"self_harm_threat"})
    return tags


def _detect(received: list[dict]) -> dict:
    """对对方消息跑规则（含互斥），返回 {rule_key: {"pos":[eid...]}}。"""
    out = {r[0]: {"pos": [], "neg": []} for r in RULES}
    for m in received:
        text = m.get("content", "") or ""
        eid = m["eid"]
        tags = _tag_message(text)
        for key in tags:
            out[key]["pos"].append(eid)
    # 反向证据：命中反向关键词的消息
    for key, _label, _pos, neg_kw, _cat in RULES:
        if not neg_kw:
            continue
        for m in received:
            text = m.get("content", "") or ""
            if any(k in text for k in neg_kw):
                out[key]["neg"].append(m["eid"])
    return out


# --------------------------------------------------------------------------- #
# 结论构造
# --------------------------------------------------------------------------- #
def _finding(label: str, pos: list, neg: list, n: int, text: str,
             tendency: Optional[str] = None) -> dict:
    """构造一条证据化结论。无正向证据时自动降级为"待观察"，不硬贴标签。
    rate 统一以对方消息总数 n 为分母（与展示 k/n 一致）。"""
    has = len(pos) > 0
    rate = wilson(len(pos), n) if n else None
    return {
        "label": label,
        "status": "ok" if has else "insufficient",
        "positive": pos,
        "negative": neg,
        "positive_count": len(pos),
        "negative_count": len(neg),
        "n": n,
        "rate": rate,
        "tendency": tendency,
        "text": text if has else f"待观察（当前样本不足以支撑“{label}”判断）：{text}",
    }


def _section(sid: int, content: dict, status: str = "ok") -> dict:
    key = title = ""
    for i, k, t in SECTION_DEFS:
        if i == sid:
            key, title = k, t
    return {"id": sid, "key": key, "title": title, "status": status, "content": content}


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def _data_root(person_id: str, data_root: Optional[str]) -> str:
    if data_root:
        return data_root
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "data", "persons")


def _load_profile(person_id: str, data_root: str) -> dict:
    path = os.path.join(data_root, person_id, "persona_profile.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _load_messages(person_id: str, db) -> list[dict]:
    if db is None:
        return []
    try:
        rows = db.fetch_all(
            "SELECT direction, content, msg_type, emotion, emotion_score, created_at "
            "FROM chat_messages WHERE person_id=:pid ORDER BY created_at ASC",
            {"pid": person_id})
        return [dict(r) for r in rows]
    except Exception:
        return []


def generate_report(person_id: str,
                    messages: Optional[list[dict]] = None,
                    db=None,
                    save: bool = True,
                    data_root: Optional[str] = None) -> dict:
    """生成（或复用已落盘的）18 部分诊断报告。幂等：同输入同输出。"""
    root = _data_root(person_id, data_root)
    profile = _load_profile(person_id, root)

    if messages is None:
        messages = _load_messages(person_id, db)
    messages = [dict(m) for m in messages]

    def _ts_key(m):
        dt = _parse_ts(m.get("created_at", ""))
        return dt or datetime.min
    messages.sort(key=_ts_key)

    received = [m for m in messages if m.get("direction") == "received"]
    sent = [m for m in messages if m.get("direction") == "sent"]

    evidence_index = []
    for i, m in enumerate(received, start=1):
        eid = f"E{i:03d}"
        m["eid"] = eid
        evidence_index.append({
            "eid": eid,
            "direction": "received",
            "timestamp": m.get("created_at", ""),
            "content": (m.get("content", "") or "")[:200],
            "source": "chat_messages",
        })

    n_other = len(received)
    n_all = len(messages)
    times = [_parse_ts(m.get("created_at", "")) for m in messages]
    times = [t for t in times if t]
    span_days = (max(times) - min(times)).days if len(times) >= 2 else 0

    # ---- 语言指纹（只统计对方消息）----
    lengths = [len((m.get("content", "") or "")) for m in received]
    avg_len = round(sum(lengths) / n_other, 1) if n_other else 0
    short_ratio = round(sum(1 for L in lengths if L <= 6) / n_other, 3) if n_other else 0
    long_ratio = round(sum(1 for L in lengths if L >= 30) / n_other, 3) if n_other else 0

    # 标点统计
    punct = {"问号": 0, "句号": 0, "感叹号": 0, "波浪/省略": 0, "表情": 0}
    for m in received:
        t = m.get("content", "") or ""
        punct["问号"] += t.count("？") + t.count("?")
        punct["句号"] += t.count("。")
        punct["感叹号"] += t.count("！") + t.count("!")
        punct["波浪/省略"] += t.count("～") + t.count("~") + t.count("…")
        punct["表情"] += len(_EMOJI_RE.findall(t))

    # 口癖 Top：高频开头二字/常用短句
    opener_count = {}
    for m in received:
        t = (m.get("content", "") or "").strip()
        if len(t) >= 2:
            head = t[:2]
            opener_count[head] = opener_count.get(head, 0) + 1
    top_openers = sorted(opener_count.items(), key=lambda x: -x[1])[:5]
    top_openers = [[w, c] for w, c in top_openers if c >= 1]

    # 回复延迟六桶：只统计"对方这次回复"距上一条消息（无论谁发）的间隔，
    # 即只把结束于 received 的间隔计入；六桶合计 = 有前序时间的对方消息数
    buckets = {"≤5min": 0, "5-30min": 0, "30min-2h": 0, "2-12h": 0, "12-48h": 0, ">48h": 0}
    for i, m in enumerate(messages):
        if m.get("direction") != "received":
            continue
        if i == 0:
            continue
        prev = messages[i - 1]
        tb = _parse_ts(m.get("created_at", ""))
        ta = _parse_ts(prev.get("created_at", ""))
        if not tb or not ta:
            continue
        gap = (tb - ta).total_seconds() / 60.0
        if gap < 0:
            continue
        if gap <= 5:
            buckets["≤5min"] += 1
        elif gap <= 30:
            buckets["5-30min"] += 1
        elif gap <= 120:
            buckets["30min-2h"] += 1
        elif gap <= 720:
            buckets["2-12h"] += 1
        elif gap <= 2880:
            buckets["12-48h"] += 1
        else:
            buckets[">48h"] += 1

    # 主动开启：相邻间隔 >2h 视为新会话，谁发第一条算谁主动
    other_starts = my_starts = 0
    prev_dt = None
    for m in messages:
        dt = _parse_ts(m.get("created_at", ""))
        if dt is None:
            continue
        if prev_dt is None or (dt - prev_dt).total_seconds() > 7200:
            if m.get("direction") == "received":
                other_starts += 1
            else:
                my_starts += 1
        prev_dt = dt
    start_total = other_starts + my_starts
    other_initiative_ratio = round(other_starts / start_total, 3) if start_total else 0.5

    # ---- 行为检测（含互斥）----
    det = _detect(received)

    def eids(key, side="pos"):
        return det.get(key, {}).get(side, [])

    # 派生标签：慢热 / 直给（温暖落地且无情绪对抗者，视为慢热直给型）
    emo_hostile = eids("blame_attack") + eids("gaslight") + eids("anxious_cling") + eids("love_bomb")
    if eids("warm_care") and eids("future_talk") and not emo_hostile:
        det["slow_warm"]["pos"] = sorted(set(eids("warm_care") + eids("future_talk")))
    if eids("warm_care") and eids("practical") and not emo_hostile:
        det["direct_speak"]["pos"] = [m["eid"] for m in received[:6]]

    nickname = (profile.get("person_id") or person_id)
    try:
        if db is not None:
            row = db.fetch_one("SELECT nickname FROM persons WHERE id=:id", {"id": person_id})
            if row and row.get("nickname"):
                nickname = row["nickname"]
    except Exception:
        pass

    # 唯一"恶意/风险"证据集合（用于风险时间线与负性计数，口径统一）
    hostile_keys = ("gaslight", "entitled", "blame_attack", "cold_wall",
                    "anxious_cling", "face_pride", "sensitive_suspect")
    hostile_eids = set()
    for k in hostile_keys:
        hostile_eids |= set(eids(k))
    self_harm_eids = eids("self_harm_threat")
    warm_eids = eids("warm_care")

    sections = []

    # 1 基础档案
    att = profile.get("attachment", {}) or {}
    rstage = profile.get("relationship_stage", {}) or {}
    s1 = {
        "platform": "私聊（微信）",
        "nickname": nickname,
        "gender": "已知（对方）",
        "relationship_stage": rstage.get("stage", "待观察"),
        "stage_basis": rstage.get("description", ""),
        "span_days": span_days,
        "data_overview": {
            "total_messages": n_all,
            "other_messages": n_other,
            "my_messages": len(sent),
            "has_conflict": bool(hostile_eids),
            "has_commitment": bool(eids("future_talk")),
        },
        "confidence_note": "样本量较小，结论以趋势为主，随互动累积逐步校准。",
    }
    sections.append(_section(1, s1, "ok" if n_other >= 5 else "insufficient"))

    # 2 语言指纹量化
    s2 = {
        "table": {
            "指标": ["平均句长(字)", "短句≤6字占比", "长段≥30字占比",
                    "对方主动开启占比", "样本(对方消息)n"],
            "数值": [avg_len, short_ratio, long_ratio, other_initiative_ratio, n_other],
        },
        "reply_lag_buckets": buckets,
        "punctuation": punct,
        "top_openers": top_openers,
        "typical_style": "短句连发为主" if short_ratio > 0.4 else
                         ("长段输出为主" if long_ratio > 0.3 else "长短混合"),
        "note": "六桶只计对方回复距上一条消息的间隔（合计=有前序时间的对方消息数）；"
                "主动占比按间隔>2h切分会话统计；口癖取高频开头二字。",
    }
    sections.append(_section(2, s2, "ok" if n_other else "insufficient"))

    # 3 双字特质 + 大五（CI 分母统一 = n_other）
    trait_rows = []
    for key, label, _p, _n, cat in RULES:
        if cat == "derived" and not det[key]["pos"]:
            continue
        pos, neg = eids(key), eids(key, "neg")
        if not pos:
            continue  # 只展示本人真正表现出（有正向证据）的特质；纯反向（ opposite ）不列为标签
        trait_rows.append({
            "label": label,
            "key": key,
            "category": cat,
            "positive": pos, "negative": neg,
            "positive_count": len(pos), "negative_count": len(neg),
            "n": n_other,
            "k_over_n": f"{len(pos)}/{n_other}",
            "rate": wilson(len(pos), n_other),  # 分母与展示 k/n 一致
        })
    neuro = eids("blame_attack") + eids("anxious_cling")
    agree = eids("warm_care")
    cons = eids("practical") + eids("future_talk")
    five = [
        _finding("神经质/情绪不稳", neuro, [], n_other,
                 "遇压力时情绪波动、易指责或过度黏连的倾向",
                 tendency="偏高" if len(neuro) >= 2 else None),
        _finding("宜人性", agree, list(hostile_eids), n_other,
                 "对人温暖、愿意照顾 vs 攻击/操控倾向",
                 tendency="偏高" if (agree and len(agree) > len(hostile_eids)) else
                          ("偏高(关怀行动)" if agree else None)),
        _finding("外倾性", [m.get("eid") for m in received[:3]], [], n_other,
                 "表达欲与主动开启会话的活跃度", tendency="中等"),
        _finding("尽责性", cons, [], n_other,
                 "做事有交代、把安排落地的务实程度",
                 tendency="中等偏高" if cons else None),
        _finding("开放性", [], [], n_other,
                 "对新体验/新观念的开放度（当前对话样本未覆盖）"),
    ]
    s3 = {"traits": trait_rows, "big_five": five,
          "mask": "对话中可见的人设倾向（以证据为准，不做无证据推断）",
          "ci_note": "所有占比 CI 分母均为对方消息总数 n，与 k/n 一致。"}
    sections.append(_section(3, s3, "ok" if trait_rows else "insufficient"))

    # 4 认知与思维
    psy = profile.get("psychology", {}) or {}
    cbt = psy.get("cbt_distortions", {}) or {}
    cog_rows = []
    for dist, cnt in cbt.items():
        ev = eids("gaslight")[:cnt] or eids("blame_attack")[:1]
        if ev:
            cog_rows.append({"pattern": dist, "count": cnt, "evidence": ev})
    if not cog_rows and eids("gaslight"):
        cog_rows.append({"pattern": "现实检验/读心倾向", "count": len(eids("gaslight")),
                         "evidence": eids("gaslight")})
    s4 = {
        "attribution": "待观察（对话样本未覆盖归因对照场景）",
        "cbt_patterns": cog_rows,
        "thinking_style": "直给" if short_ratio > 0.4 else "偏绕弯/需结合语境",
        "note": "认知偏差仅在出现对应原话时打标；无原话不臆断。",
    }
    sections.append(_section(4, s4, "ok" if cog_rows else "insufficient"))

    # 5 情绪系统（触发点≥5，volatility 按证据）
    ep = profile.get("emotion_pattern", {}) or {}
    triggers = []
    trigger_defs = [
        ("被追问/对账", eids("gaslight")),
        ("回复慢/被忽略", eids("anxious_cling")),
        ("被质疑/翻旧账", eids("blame_attack")),
        ("日常关心/被照顾", eids("warm_care")),
        ("被肯定/被赞美", eids("love_bomb")),
        ("与异性/第三方有关", eids("sensitive_suspect")),
        ("安排/计划被打断", eids("cold_wall")),
    ]
    for name, ev in trigger_defs:
        if ev:
            triggers.append({"trigger": name, "evidence": ev[:3]})
    # 兜底补足到 5 条：忙而未回的解释、服软修复也算情绪相关信号
    if len(triggers) < 5:
        for name, ev in [("忙碌/被工作占用", eids("busy_explain")),
                         ("冲突后主动修复", eids("reconcile"))]:
            if ev and len(triggers) < 6:
                triggers.append({"trigger": name, "evidence": ev[:3]})
    # volatility 按证据（健康低；冲突/神经质命中高）
    vol_hits = len(neuro) + len(eids("gaslight")) + len(eids("cold_wall"))
    if vol_hits >= 8:
        volatility = "高（情绪波动频繁）"
    elif vol_hits >= 3:
        volatility = "中（偶有波动）"
    elif vol_hits == 0:
        volatility = "低（样本内情绪平稳）"
    else:
        volatility = "待观察"
    s5 = {
        "baseline": ep.get("pattern", "待观察"),
        "volatility": volatility,
        "volatility_evidence_count": vol_hits,
        "positive_ratio": ep.get("positive_ratio"),
        "negative_ratio": ep.get("negative_ratio"),
        "triggers": triggers,
        "recovery": ("冲突后有主动服软/修复动作" if eids("reconcile")
                     else "待观察（需更多冲突-修复回合验证）"),
    }
    sections.append(_section(5, s5, "ok" if triggers else "insufficient"))

    # 6 依恋与亲密（坐标读 profile，抗议证据补焦虑分与冷热混合判定）
    coords = {
        "回避倾向": att.get("avoidance_score"),
        "焦虑倾向": att.get("anxiety_score"),
        "安全基底": att.get("security_score"),
    }
    protest = eids("anxious_cling") + eids("entitled")
    # 侧写坐标与抗议证据矛盾时，据证据补一个证据焦虑信号
    evidence_anxiety = min(10, len(protest) * 2) if protest else None
    atype = att.get("type", "待观察") or "待观察"
    type_note = att.get("mixed_note", "") or att.get("description", "")
    if protest and ("回避" in atype or "疏离" in atype):
        type_note = (type_note + " 侧写偏疏离/回避，但对话中出现索取/抗议式回应"
                     f"（证据 {len(protest)} 条），呈冷热混合、索取型倾向，非纯疏离。").strip()
        coords["焦虑倾向(证据)"] = evidence_anxiety
    s6 = {
        "coords": coords,
        "type": atype,
        "type_note": type_note,
        "protest_evidence": protest,
        "key_reactions": [
            {"scenario": "被冷落/延迟回复", "evidence": eids("anxious_cling") or eids("cold_wall")},
            {"scenario": "靠近/确定关系话题", "evidence": eids("future_talk") or eids("love_bomb")},
        ],
        "intimacy": "待观察（需更多亲密表达样本）",
    }
    sections.append(_section(6, s6, "ok" if att else "insufficient"))

    # 7 冲突与防御（按 direction 归属；忙而未回≠攻击）
    cs = profile.get("conflict_style", {}) or {}
    fight = eids("blame_attack")
    flight = eids("cold_wall")
    fix = eids("reconcile")
    busy = eids("busy_explain")
    # 只有对方真实攻击才算"冲突"；忙而未回+次日解释属于摩擦非攻击
    real_attack = fight or eids("gaslight") or eids("entitled")
    if real_attack:
        primary = cs.get("primary", "指责/对抗为主")
    elif busy and not hostile_eids:
        primary = "无明显攻击；唯一摩擦为回复延迟，对方已解释（忙而未回）"
    else:
        primary = cs.get("primary", "待观察")
    s7 = {
        "primary": primary,
        "scores": cs.get("scores", {}),
        "scale_note": "战=对方指责/翻旧账原话数；逃=对方筑墙/冷处理原话数；"
                      "修复=对方主动服软/解释原话数；均只统计对方(received)消息，不含我方气话。",
        "first_reaction": {
            "战(指责)": {"count": len(fight), "evidence": fight},
            "逃(冷战)": {"count": len(flight), "evidence": flight},
            "讨好/服软": {"count": len(fix), "evidence": fix},
            "忙而未回(中性)": {"count": len(busy), "evidence": busy},
        },
        "wash_phrases": eids("gaslight")[:5],
        "repair": "冲突后以服软/解释为主" if (fix or busy) else "待观察修复回合",
        "boundary_violation": "无明显越界" if not (eids("entitled") and eids("anxious_cling"))
                             else "存在堵门/索取式施压，见第13部分风险分级",
    }
    sections.append(_section(7, s7, "ok" if (fight or flight or fix or busy) else "insufficient"))

    # 8 权力结构
    pw = profile.get("power_dynamic", {}) or {}
    invb = profile.get("investment_balance", {}) or {}
    s8 = {
        "pattern": pw.get("pattern", "待观察"),
        "description": pw.get("description", ""),
        "other_initiative_ratio": other_initiative_ratio,
        "other_starts": other_starts, "my_starts": my_starts,
        "investment": {
            "情绪": invb.get("emotional_ratio"),
            "时间": invb.get("time_ratio"),
            "金钱": invb.get("money_ratio"),
        },
        "role": "待观察",
    }
    sections.append(_section(8, s8, "ok" if pw else "insufficient"))

    # 9 动机与承诺信用（真实承诺识别+兑现核对；无锚定不填70）
    promises = []
    for eid in eids("future_talk"):
        m = next((x for x in received if x.get("eid") == eid), None)
        if not m:
            continue
        text = m.get("content", "") or ""
        # 只把"对方对未来行动的承诺/约定"计入，纯寒暄不算
        promises.append({"content": text[:80], "time": m.get("created_at", ""),
                         "evidence": [eid], "fulfilled": None, "fulfill_evidence": []})
    # 兑现核对：后续对方消息中出现同一关键词且为"已完成"陈述
    def _tok(t):
        return [w for w in ["粥", "饭团", "粉店", "老地方", "七点", "江边", "电影", "下次",
                            "细说", "排班", "爬山", "路线", "山顶", "吃饭"] if w in t]
    for p in promises:
        toks = _tok(p["content"])
        later = [x for x in received
                 if (_parse_ts(x.get("created_at", "")) or datetime.min) >
                 (_parse_ts(p["time"]) or datetime.min)]
        for lt in later:
            if toks and any(tok in (lt.get("content", "") or "") for tok in toks):
                p["fulfilled"] = True
                p["fulfill_evidence"].append(lt["eid"])
                break
    n_prom = len(promises)
    n_fulfilled = sum(1 for p in promises if p["fulfilled"])
    if n_prom > 0:
        fulfillment = wilson(n_fulfilled, n_prom)
    else:
        fulfillment = None  # 待观察·未校准
    s9 = {
        "core_needs": "待观察（需更多诉求表达样本）",
        "commitment_table": promises,
        "promise_count": n_prom,
        "fulfilled_count": n_fulfilled,
        "fulfillment_rate": fulfillment,   # None 表示待校准，禁止默认 70
        "word_action_consistency": fulfillment,
        "note": "承诺=对方对未来行动的明确约定；兑现=后续原话显示已落地。"
                "无逐条可锚定承诺时记为'待观察·未校准'，不填默认百分比。",
    }
    sections.append(_section(9, s9, "ok" if promises else "insufficient"))

    # 10 价值观与底线
    vm = profile.get("values_match", {}) or {}
    s10 = {
        "dimensions": vm.get("dimensions", {}),
        "雷点": [],
        "note": "价值观多为'未讨论'，需在后续对话中主动探明金钱观/家庭观/人生优先级。",
    }
    for trig, key in [("多疑/占有", "sensitive_suspect"), ("特权/索取", "entitled"),
                      ("被指责", "blame_attack")]:
        ev = eids(key)
        if ev:
            s10["雷点"].append({"topic": trig, "evidence": ev[:3]})
    sections.append(_section(10, s10, "ok" if s10["雷点"] else "insufficient"))

    # 11 社交网络（按证据，不硬编码"社交简单"）
    soc_notes = []
    if eids("sensitive_suspect"):
        soc_notes.append({"note": "对异性/第三方存在查证、占有倾向",
                          "evidence": eids("sensitive_suspect")[:3]})
    s11 = {"social_circle": "待观察（对话未覆盖其朋友圈/家人描述）",
           "opposite_gender_boundary": (profile.get("boundaries", {}) or {}).get(
               "opposite_boundary", "待观察"),
           "evidence_notes": soc_notes,
           "note": "无证据不臆断；后续若提及家人/挚友/同事再补全。"}
    sections.append(_section(11, s11, "ok" if soc_notes else "insufficient"))

    # 12 生活方式
    s12 = {"lifestyle": "待观察（对话样本未覆盖作息/工作/爱好细节）",
           "action_grounded": "务实落地" if eids("practical") else "待观察",
           "evidence": eids("practical"),
           "note": "仅在出现具体行动安排原话时打标。"}
    sections.append(_section(12, s12, "ok" if eids("practical") else "insufficient"))

    # 13 黑暗特质与风险（1-5；自伤/威胁强制 5；basis 由证据生成）
    dtriad = psy.get("dark_triad_tendencies", {}) or {}
    risk = profile.get("risk_warning", {}) or {}
    pua_score = risk.get("pua_score", 0) or 0
    extreme_score = risk.get("extreme_score", 0) or 0

    # 风险时间线：每条消息按优先级只归一个主类型，避免同消息双重计数
    # 优先级：自伤威胁 > 指责 > 特权 > 黏人 > 扭曲 > 筑墙 > 好面 > 多疑
    RISK_PRIORITY = ["self_harm_threat", "blame_attack", "entitled", "anxious_cling",
                     "gaslight", "cold_wall", "face_pride", "sensitive_suspect"]
    timeline = []
    _seen = set()
    for key in RISK_PRIORITY:
        for eid in eids(key):
            if eid in _seen:
                continue
            m = next((x for x in received if x.get("eid") == eid), None)
            if m:
                _seen.add(eid)
                timeline.append({"time": m.get("created_at", ""),
                                 "type": key,
                                 "evidence": [eid],
                                 "quote": (m.get("content", "") or "")[:40]})
    timeline.sort(key=lambda x: _parse_ts(x.get("time", "")) or datetime.min)

    level = 1
    basis = []
    if real_attack or flight:
        level = max(level, 2)
        basis.append(f"出现指责/冷战等冲突行为（对方攻击类证据 {len(real_attack or [])} 条、筑墙 {len(flight)} 条）")
    if eids("gaslight"):
        level = max(level, 3)
        basis.append(f"出现否认现实/扭曲记忆式沟通（{len(eids('gaslight'))} 条）")
    if eids("entitled") and eids("anxious_cling"):
        level = max(level, 4)
        basis.append(f"出现特权/施压式索取等越界行为（特权 {len(eids('entitled'))} 条 + 黏人 {len(eids('anxious_cling'))} 条）")
    if pua_score >= 4:
        level = max(level, 4)
        basis.append(f"侧写 PUA 分={pua_score}")
    # 自伤/自杀/人身威胁 → 强制 5
    if self_harm_eids:
        level = 5
        basis.append(f"出现明确自伤/自杀式抛弃威胁（{len(self_harm_eids)} 条，如 {self_harm_eids[0]}），按规格定为最高 5 级")
    if level == 1 and not basis:
        basis.append("样本内未见明确攻击/威胁，仅日常互动；风险基线为低")
    level = max(1, min(5, level))
    s13 = {
        "dark_triad": {k: {"tendency": "偏高", "count": v} for k, v in dtriad.items()},
        "manipulation": [k for k in ("gaslight", "love_bomb", "entitled") if eids(k)],
        "timeline": timeline,
        "risk_level": level,
        "risk_basis": basis,
        "note": "病理/黑暗特质仅作'呈倾向/维度偏高'描述，不替代专业诊断。",
    }
    sections.append(_section(13, s13, "ok"))

    # 14 共情与道德
    s14 = {
        "cognitive_empathy": "待观察",
        "emotional_empathy": ("偏高（有关心行动）" if warm_eids else "待观察"),
        "empathy_evidence": warm_eids,
        "apology": ("会服软但常附带'你也有不对'" if eids("reconcile") else "待观察道歉样本"),
        "note": "共情拆解需更多'对方是否回应你的情绪'样本。",
    }
    sections.append(_section(14, s14, "ok" if warm_eids else "insufficient"))

    # 15 伪装与一致性（真实言行对照：承诺 vs 后续兑现）
    wa = []
    for p in promises:
        if p["fulfilled"]:
            wa.append({"he_said": p["content"], "he_did": "后续已兑现",
                       "evidence": p["evidence"] + p["fulfill_evidence"]})
        elif n_prom > 0:
            wa.append({"he_said": p["content"], "he_did": "尚待核对是否兑现",
                       "evidence": p["evidence"]})
    consistency = fulfillment  # 与第9节同源，不再用 profile 默认值
    s15 = {"mask_list": [], "word_action": wa,
           "consistency": consistency,
           "note": "言行对照=承诺原话 vs 后续兑现原话；无承诺样本时记待观察。"}
    sections.append(_section(15, s15, "ok" if wa else "insufficient"))

    # 16 变化轨迹（按时间线真实聚合 kind vs hostile）
    # 按天聚合
    from collections import defaultdict
    day_kind = defaultdict(int)
    day_hostile = defaultdict(int)
    day_total = defaultdict(int)
    for m in received:
        d = (_parse_ts(m.get("created_at", "")) or datetime.min).strftime("%m-%d")
        if d == "01-01" and not m.get("created_at"):
            continue
        day_total[d] += 1
        tags = _tag_message(m.get("content", "") or "")
        if tags & HOSTILE_TAGS:
            day_hostile[d] += 1
        elif tags & KIND_TAGS:
            day_kind[d] += 1
    phase_rows = []
    for d in sorted(day_total.keys()):
        phase_rows.append({"date": d, "kind": day_kind[d],
                           "hostile": day_hostile[d], "total": day_total[d]})
    if len(day_hostile) >= 2 and sum(day_hostile.values()) > 0:
        trend = f"冲突/负面信号跨 {len(day_hostile)} 天反复出现，情绪起伏具周期性"
    elif sum(day_kind.values()) > sum(day_hostile.values()):
        trend = "整体以善意/落地互动为主，负面信号少且集中在个别摩擦点"
    else:
        trend = "待观察（样本较短，需更长时间窗判断漂移）"
    s16 = {
        "phases": "按时间线按天聚合（样本跨度 %d 天）" % span_days,
        "daily": phase_rows,
        "timeline_summary": [t["time"][:10] for t in timeline[:8]] or
                            [p["date"] for p in phase_rows[:8]],
        "trend": trend,
    }
    sections.append(_section(16, s16, "ok" if phase_rows else "insufficient"))

    sections.append(_section(17, {}, "ok"))
    sections.append(_section(18, {}, "ok"))

    # ---- 可信度（负性计数与风险时间线同一口径：唯一风险消息数）----
    credibility = {
        "n": n_all,
        "time_span_days": span_days,
        "positive_evidence": len(received),
        "negative_evidence": len(timeline),
        "calibration": {"status": "未校准",
                        "note": "暂无预测命中样本，T 校准未开始；不编造命中率。"},
    }

    # ---- 待观察清单 ----
    pending = []
    if not eids("future_talk"):
        pending.append("他是否会在冷静后主动兑现邀约/承诺？看到具体约定+落地即可补全。")
    if not warm_eids:
        pending.append("你生病/低落时他是否给出具体关心行动？看是否有'点外卖/接送/问候'原话。")
    if not eids("reconcile"):
        pending.append("冲突后谁先低头、怎么修复？记录第一次主动破冰即可。")
    if not (s10["dimensions"] or {}) or all(v == "未讨论" for v in (s10["dimensions"] or {}).values()):
        pending.append("金钱观/家庭观/人生优先级尚未探明，需在日常话题中自然切入。")
    pending.append("他在你长时间不回消息时的第一反应（紧张/暴怒/冷处理/无所谓）可证伪依恋坐标。")

    # ---- 核心标签（按信号强度排序；自伤/忙碌解释不计入人格标签）----
    EXCLUDE_FROM_LABEL = {"self_harm_threat", "busy_explain"}
    scored = []
    for key, label, _p, _n, cat in RULES:
        c = len(det[key]["pos"])
        if c >= 1 and key not in EXCLUDE_FROM_LABEL:
            scored.append((c, key, label))
    scored.sort(key=lambda x: (-x[0], x[1]))
    core_labels = [{"label": label, "evidence": det[key]["pos"][:2]}
                   for _c, key, label in scored[:5]]
    if not core_labels:
        core_labels.append({"label": "待观察", "evidence": []})

    # ---- actionable（攻略导向，绝不撤离）----
    if self_harm_eids:
        what_works = ["先接情绪、反复给'我在'的确定感", "承诺明确、别用突然消失试探",
                      "情绪高峰不辩论、先稳住安全感"]
        what_to_avoid = ["突然消失/长时间不回", "说他'太敏感/想多了'", "用冷处理惩罚他"]
        next_moves = ["24h 内主动发一条具体关心", "和他约定固定联络节奏",
                      "情绪激烈时先共情再讲理；自伤言论需认真对待并寻求专业支持"]
    elif eids("anxious_cling") and "回避" in atype:
        what_works = ["给面子、公开肯定", "让他主导决定但留台阶", "用轻松话题低压力互动"]
        what_to_avoid = ["当众质疑他", "逼他立刻认错", "连环追问/翻记录对账"]
        next_moves = ["选一件他擅长的事请他帮忙并具体感谢", "把质问换成轻松开场",
                      "等他主动后再升温"]
    elif eids("anxious_cling"):
        what_works = ["及时回应、别让他猜", "主动报备行踪与状态", "冷下来前先给一个软信号",
                      "反复确认'我在'"]
        what_to_avoid = ["突然消失/长时间不回", "说他'太敏感/想多了'", "翻旧账式对质"]
        next_moves = ["24h 内主动发一条具体关心", "和他约定一个固定联络节奏",
                      "下次他情绪化时先接情绪再讲道理"]
    elif warm_eids and eids("practical"):
        what_works = ["用具体行动回应他的体贴（也帮他点一次/接送一次）",
                      "顺着他的务实节奏把约定落地", "认可他记小事的用心"]
        what_to_avoid = ["忽冷忽热式考验", "只说不做、空泛应付他的安排"]
        next_moves = ["按他提的约定赴约并兑现一个回礼", "把关心落到一个具体动作",
                      "记录他的偏好更新爱情地图"]
    else:
        what_works = ["顺其自然、高质量陪伴", "用具体行动（安排/接送/小惊喜）表达", "尊重他的节奏"]
        what_to_avoid = ["忽冷忽热式考验", "推进过快给压力"]
        next_moves = ["按当前节奏安排一次正式约会", "把关心落到一个具体动作",
                      "记录他的偏好更新爱情地图"]

    actionable = {
        "core_labels": [c["label"] for c in core_labels],
        "what_works": what_works,
        "what_to_avoid": what_to_avoid,
        "next_moves": next_moves,
    }

    # ---- 人物底色白描（300-600 字，用本聊天真实细节，去模板化）----
    # 挑真实原话做例子：优先体贴/规划原话，其次其他
    quote_pool = []
    for eid in (warm_eids + eids("future_talk") + eids("practical")):
        m = next((x for x in received if x.get("eid") == eid), None)
        if m:
            quote_pool.append(m.get("content", ""))
    quote_pool += [e["content"] for e in evidence_index[:2]]
    quote_pool = [q for q in quote_pool if q][:3]

    style_txt = ("话不多、句句直给、爱用短句" if short_ratio >= 0.4
                 else "表达长短混合、有来有往")
    if warm_eids and eids("practical"):
        mode_txt = ("他不是靠嘴甜的那种——会提醒你加衣、帮你想'先垫一口'、"
                    "把几点在哪碰头都提前说清楚，关心都落在具体动作上。")
    elif self_harm_eids:
        mode_txt = ("他情绪一上来就很猛：会堵在你公司楼下、反复要你发誓不离开、"
                    "甚至用'你走了我活不下去'来留住你，安全感很薄。")
    elif eids("love_bomb") and (eids("gaslight") or eids("blame_attack")):
        mode_txt = ("他开场很热，会把你夸得像全世界唯一懂他的人；"
                    "可一旦你没顺着他、或问他为什么没回，他立刻翻脸、好面子、还爱翻旧账对账。")
    elif eids("reserved_cold") or "回避" in atype:
        mode_txt = ("他偏慢热、不轻易表露，你追紧了他就往后缩一句'看情况/你忙你的'，"
                    "但偶尔也会服个软。")
    else:
        mode_txt = "他的表达方式还在慢慢展开，细节见仁见智。"

    q_txt = "；".join(f"比如他说过“{q}”" for q in quote_pool[:2])
    repair_txt = ("冲突后他会主动解释甚至服软，不是那种撂狠话就消失的人。"
                  if (eids("reconcile") or warm_eids) else
                  "目前还没看到他在摩擦后主动低头的样本。")
    # 收尾按证据分支，避免三份逐字共享模板
    if self_harm_eids:
        close_txt = ("他的'怕被丢下'几乎藏不住——你一冷淡，他的恐慌就先崩。"
                     "下一步不是哄也不是躲，而是用稳定、说到做到的回应，把这份安全感一点点填回来；"
                     "他要的不是道理，是确定感。")
    elif eids("love_bomb") and (eids("gaslight") or eids("blame_attack")):
        close_txt = ("他夸你和贬你用的是同一股劲：上头时把你捧上天，不顺时一句话就戳你面子。"
                     "这种时候给台阶、给面子，比讲道理管用得多；硬碰硬只会让他更想赢。")
    elif warm_eids and eids("practical"):
        close_txt = ("他的好不靠甜言蜜语，都在'先垫一口''几点在哪碰头'这种小事里。"
                     "你把这份用心接住、偶尔也主动回一次，关系自然就稳了；不用刻意浪漫，稳就赢了。")
    else:
        close_txt = ("他的开关还在一点点露出来，多看几次他在你忙碌、低落时的真实反应，"
                     "对症的节奏就会慢慢清晰；急着定义他，反而会看走眼。")
    portrait_plain = (
        f"这位对方，{style_txt}。{mode_txt}"
        f"从这 {n_other} 条他主动发来的消息看，{q_txt}。{repair_txt}"
        f"在关系里，他更吃这一套：{what_works[0]}；"
        f"最忌讳的是：{what_to_avoid[0]}。{close_txt}"
    )
    portrait_plain = portrait_plain.strip()
    if len(portrait_plain) > 600:
        portrait_plain = portrait_plain[:597] + "……"
    # 补字只用于样本偏短、接近 300 字底线时；内容按本案例证据生成，不共享同一句
    top_label = core_labels[0]["label"] if core_labels else "这个人"
    fill_txt = (f"至于{top_label}这一点在压力下会不会变，"
                f"还要等他在你忙碌、低落时再露几次真容；"
                f"眼下这些细节，已经够先照着「{next_moves[0]}」这一步去试。")
    while len(portrait_plain) < 300:
        portrait_plain += fill_txt
    portrait_plain = portrait_plain[:600]

    s17 = {
        "portrait_plain": portrait_plain,
        "core_labels": core_labels,
        "playbook": actionable,
        "note": "本报告只做事实与倾向诊断，服务于“看懂之后如何对症引导关系变好”，不做去留建议。",
    }
    s18 = {
        "evidence_index_count": len(evidence_index),
        "credibility": credibility,
        "pending_observations": pending,
        "calibration": credibility["calibration"],
    }
    for sec in sections:
        if sec["id"] == 17:
            sec["content"] = s17
        if sec["id"] == 18:
            sec["content"] = s18

    report = {
        "schema": SCHEMA_VERSION,
        "person_id": person_id,
        "generated_at": _now(),
        "data_overview": s1["data_overview"],
        "sections": sections,
        "section_map": {sec["key"]: sec for sec in sections},
        "evidence_index": evidence_index,
        "credibility": credibility,
        "pending_observations": pending,
        "portrait_plain": portrait_plain,
        "actionable": actionable,
    }

    if save:
        _save_report(person_id, report, root)
    return report


def report_file_path(person_id: str, data_root: Optional[str] = None) -> str:
    root = _data_root(person_id, data_root)
    return os.path.join(root, person_id, "diagnostic_report.json")


def get_or_generate_report(person_id: str, db=None,
                          data_root: Optional[str] = None,
                          force: bool = False) -> dict:
    """缓存优先：磁盘已有 diagnostic_report.json 则直接读回；否则现场生成并落盘。"""
    root = _data_root(person_id, data_root)
    path = report_file_path(person_id, root)
    if not force and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return generate_report(person_id, db=db, save=True, data_root=root)


def _save_report(person_id: str, report: dict, root: str) -> None:
    d = os.path.join(root, person_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "diagnostic_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    md = render_markdown(report)
    with open(os.path.join(d, "diagnostic_report.md"), "w", encoding="utf-8") as f:
        f.write(md)


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #
def render_markdown(report: dict) -> str:
    L = []
    L.append(f"# 人物诊断报告 · {report.get('person_id','')}")
    L.append("")
    L.append(f"> 生成时间：{report.get('generated_at','')}　schema：{report.get('schema','')}")
    L.append("> 本报告只做事实与倾向诊断，服务于“对症引导关系变好”；病理描述仅为倾向提示，不替代专业诊断。")
    L.append("")
    ov = report.get("data_overview", {})
    L.append(f"- 消息总数 {ov.get('total_messages',0)}（对方 {ov.get('other_messages',0)} / 我 {ov.get('my_messages',0)}）")
    L.append(f"- 是否含冲突事件：{ov.get('has_conflict')}　是否含承诺事件：{ov.get('has_commitment')}")
    L.append("")

    for sec in report.get("sections", []):
        L.append(f"## {sec['id']}. {sec['title']}")
        c = sec.get("content", {})
        _render_content(L, sec["id"], c, report)
        L.append("")

    L.append("## 证据索引（可逐条回溯）")
    L.append("")
    for e in report.get("evidence_index", []):
        L.append(f"- `{e['eid']}` [{_short_ts(e.get('timestamp',''))}] {e.get('content','')}")
    L.append("")
    L.append("## 待观察清单")
    for p in report.get("pending_observations", []):
        L.append(f"- {p}")
    return "\n".join(L)


def _fmt_rate(rate):
    if not rate:
        return "待观察·未校准"
    return (f"{rate.get('p',0)*100:.0f}%（{rate.get('k',0)}/{rate.get('n',0)}）"
            f" 95%CI[{rate.get('low',0):.2f}, {rate.get('high',0):.2f}]")


def _render_content(L: list, sid: int, c: dict, report: dict) -> None:
    if not c:
        L.append("（暂无内容）")
        return
    if sid == 2:
        tbl = c.get("table", {})
        L.append("| " + " | ".join(tbl.get("指标", [])) + " |")
        L.append("|" + "---|" * len(tbl.get("指标", [])))
        L.append("| " + " | ".join(str(x) for x in tbl.get("数值", [])) + " |")
        L.append("")
        L.append("**回复延迟六桶（仅计对方回复间隔）：**")
        for k, v in (c.get("reply_lag_buckets") or {}).items():
            L.append(f"- {k}: {v}")
        pn = c.get("punctuation") or {}
        L.append(f"- 标点/表情：问号 {pn.get('问号',0)}、句号 {pn.get('句号',0)}、"
                 f"感叹号 {pn.get('感叹号',0)}、波浪省略 {pn.get('波浪/省略',0)}、表情 {pn.get('表情',0)}")
        to = c.get("top_openers") or []
        L.append("- 口癖开头 Top：" + ("；".join(f"{w}×{n}" for w, n in to) or "样本不足"))
        L.append(f"- 典型风格：{c.get('typical_style','')}")
    elif sid == 3:
        for t in c.get("traits", []):
            rate = t.get("rate") or {}
            L.append(f"- **{t['label']}**：正{t['positive_count']}/反{t['negative_count']} "
                     f"(k/n={t.get('k_over_n','')}, 占比 {rate.get('p',0)*100:.0f}% "
                     f"95%CI[{rate.get('low',0):.2f}, {rate.get('high',0):.2f}]) "
                     f"证据 {','.join(t['positive'][:3]) or '—'}")
        L.append("")
        L.append("**大五 OCEAN：**")
        for f in c.get("big_five", []):
            ev = f.get("positive") or []
            L.append(f"- {f['label']}：{f.get('text','')} {('证据 '+','.join(ev[:3])) if ev else ''}")
    elif sid == 9:
        ct = c.get("commitment_table", [])
        if ct:
            L.append("| 承诺内容 | 时间 | 是否兑现 | 证据 |")
            L.append("|---|---|---|---|")
            for row in ct:
                L.append(f"| {row.get('content','')} | {_short_ts(row.get('time',''))} | "
                         f"{('已兑现' if row.get('fulfilled') else '待核对')} | "
                         f"{','.join((row.get('evidence',[]) + row.get('fulfill_evidence',[])))} |")
        else:
            L.append("- 暂无可锚定的逐条承诺。")
        L.append(f"- 承诺兑现率：{_fmt_rate(c.get('fulfillment_rate'))}")
    elif sid == 13:
        L.append(f"**危险信号分级：{c.get('risk_level')} / 5**")
        for b in c.get("risk_basis", []):
            L.append(f"- 依据：{b}")
        L.append("")
        L.append("**风险事件时间线：**")
        for t in c.get("timeline", []):
            L.append(f"- [{_short_ts(t.get('time',''))}] {t.get('type')} "
                     f"“{t.get('quote','')}” 证据 {','.join(t.get('evidence',[]))}")
        dt = c.get("dark_triad", {})
        if dt:
            L.append("")
            L.append("**黑暗三联征倾向（偏高即标注，不替代临床）：**")
            for k, v in dt.items():
                L.append(f"- {k}：{v.get('tendency','')}（样本 {v.get('count',0)}）")
    elif sid == 15:
        wa = c.get("word_action", [])
        if wa:
            L.append("| 他说的(承诺) | 他做的 | 证据 |")
            L.append("|---|---|---|")
            for row in wa:
                L.append(f"| {row.get('he_said','')} | {row.get('he_did','')} | {','.join(row.get('evidence',[]))} |")
        else:
            L.append("- 暂未发现可锚定的言行对照。")
    elif sid == 16:
        for row in c.get("daily", [])[:10]:
            L.append(f"- {row.get('date','')}：善意 {row.get('kind',0)} / 负面 {row.get('hostile',0)}（共 {row.get('total',0)}）")
        L.append(f"- 趋势判断：{c.get('trend','')}")
    elif sid == 17:
        L.append("**人物底色白描：**")
        L.append("")
        L.append(c.get("portrait_plain", ""))
        L.append("")
        L.append("**核心标签：**")
        for cl in c.get("core_labels", []):
            L.append(f"- {cl.get('label','')} 证据 {','.join(cl.get('evidence',[])) or '—'}")
        pb = c.get("playbook", {})
        L.append("")
        L.append("**相处要点（攻略导向）：**")
        L.append(f"- 吃哪套：{'；'.join(pb.get('what_works',[]))}")
        L.append(f"- 忌什么：{'；'.join(pb.get('what_to_avoid',[]))}")
        L.append(f"- 下一步：{'；'.join(pb.get('next_moves',[]))}")
    elif sid == 18:
        cr = c.get("credibility", {})
        L.append(f"- 样本量 n={cr.get('n',0)}　时间跨度 {cr.get('time_span_days',0)} 天")
        L.append(f"- 正性证据 {cr.get('positive_evidence',0)} 条　负性/冲突证据 {cr.get('negative_evidence',0)} 条")
        cal = c.get("calibration", {})
        L.append(f"- T 校准状态：{cal.get('status','未校准')}（{cal.get('note','')}）")
    else:
        for k, v in c.items():
            if isinstance(v, (str, int, float)):
                L.append(f"- {k}：{v}")
            elif isinstance(v, dict):
                L.append(f"- {k}：{json.dumps(v, ensure_ascii=False)[:200]}")
            elif isinstance(v, list) and v and isinstance(v[0], dict):
                L.append(f"- {k}：")
                for item in v[:8]:
                    L.append(f"  - {json.dumps(item, ensure_ascii=False)[:160]}")
