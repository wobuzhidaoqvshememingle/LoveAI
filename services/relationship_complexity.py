# -*- coding: utf-8 -*-
"""R3.3-80 关系复杂度画像引擎（A/C/D/E/F 五类检测）。

纯服务层：关键词 + 规则匹配，无重依赖、无网络请求、不 import PySide6。

设计原则（与 services/relationship_graph.py、persona_analyzer.py 一致）：
  - 每条 detected=True 必须带 evidence（原文片段）；
  - 信息缺失一律标 "未知"，绝不臆造；
  - 法律类只输出合规风险提示文案；伦理/心理类只做客观行为关联，不贴道德标签、不评判条件优劣。
"""
from __future__ import annotations
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _text_of(msg: Any) -> str:
    """统一取消息正文：dict 取 content，否则 str()。"""
    if isinstance(msg, dict):
        return str(msg.get("content", "") or msg.get("text", "") or "")
    return str(msg or "")


def _snippet(text: str, kw: str, span: int = 24) -> str:
    """命中关键词前后截取原文片段，保证 evidence 是真实原文。"""
    idx = text.find(kw)
    if idx < 0:
        idx = 0
    lo = max(0, idx - span)
    hi = min(len(text), idx + len(kw) + span)
    seg = text[lo:hi].replace("\n", " ").strip()
    return ("…" if lo > 0 else "") + seg + ("…" if hi < len(text) else "")


def _hits(messages: list, keywords: list[str]) -> list[str]:
    """返回命中关键词的原文片段列表（去重、限 5 条）。"""
    ev: list[str] = []
    for m in messages:
        text = _text_of(m)
        if not text:
            continue
        for kw in keywords:
            if kw and kw in text:
                ev.append(_snippet(text, kw))
                break  # 同一条消息只记一次证据
        if len(ev) >= 5:
            break
    return ev


def _count_hits(messages: list, keywords: list[str]) -> int:
    """命中关键词的消息条数。"""
    n = 0
    for m in messages:
        text = _text_of(m)
        if any(kw and kw in text for kw in keywords):
            n += 1
    return n


def _confidence(evidence: list[str], single: float = 0.6) -> float:
    """按证据条数给出 0-1 置信度：1 条=single，每多一条 +0.15，封顶 0.95。"""
    if not evidence:
        return 0.0
    return round(min(0.95, single + 0.15 * (len(evidence) - 1)), 2)


# --------------------------------------------------------------------------- #
# A 类：家庭系统
# --------------------------------------------------------------------------- #
_REMARRIED_KW = ["离异", "离婚", "再婚", "二婚", "前夫", "前妻", "丧偶", "带娃"]
_STEPCHILD_KW = ["继子", "继女", "对方的孩子", "他儿子", "她女儿", "带个孩子",
                 "带个娃", "带娃", "不是亲生", "和前任的孩子"]
_STEP_ACCEPT_LOW = ["抵触", "不喜欢", "排斥", "不肯叫", "见都不想见"]
_STEP_ACCEPT_HIGH = ["接受", "慢慢叫", "挺亲", "处得来", "喊妈", "喊爸"]
_GRANDPARENT_KW = ["奶奶", "姥姥", "爷爷", "外婆", "外公", "我婆婆", "我公公"]
_GRAND_DECIDE_KW = ["说了算", "必须", "不同意", "不答应", "点了头", "拍板", "拿主意"]
_GRAND_PRO_KW = ["喜欢", "支持", "满意", "点头", "乐意", "看好"]
_GRAND_CON_KW = ["反对", "不同意", "嫌弃", "挑刺", "不喜欢", "看不上"]
_CAREGIVING_KW = ["养老", "照顾老人", "爸妈年纪", "兄弟姐妹", "几个孩子",
                  "上面还有", "姐弟", "兄妹", "老的小的"]
_DEBT_KW = ["负债", "欠债", "房贷", "借款", "还钱", "高利贷", "网贷", "欠款", "外债"]


def extract_family_system(messages: list) -> dict:
    """A 类：重组家庭 / 继子女 / 祖辈话语权 / 多子女养老 / 家庭负债。"""
    # --- 重组家庭 ---
    remarried_ev = _hits(messages, _REMARRIED_KW)
    remarried = {
        "detected": bool(remarried_ev),
        "confidence": _confidence(remarried_ev),
        "evidence": remarried_ev,
        "adaptation": ("存在离异/再婚/带娃背景，推进节奏需给对方留出处理前一段关系"
                       "遗留事项的空间，避免催进度") if remarried_ev else "无重组家庭信号",
    }

    # --- 继子女接受度 ---
    step_ev = _hits(messages, _STEPCHILD_KW)
    accept_low = _count_hits(messages, _STEP_ACCEPT_LOW)
    accept_high = _count_hits(messages, _STEP_ACCEPT_HIGH)
    if step_ev:
        if accept_low >= accept_high and accept_low > 0:
            accept = "低"
        elif accept_high > 0:
            accept = "高" if accept_high >= 2 else "中"
        else:
            accept = "中"
    else:
        accept = "未知"
    stepchildren = {
        "detected": bool(step_ev),
        "acceptance_level": accept,
        "confidence": _confidence(step_ev),
        "evidence": step_ev,
        "adaptation": ("继子女为关系变量，接受度经营优先于两人独处节奏；接受度低时"
                       "不强行要求孩子改口，先做平行朋友") if step_ev else "无继子女信号",
    }

    # --- 祖辈话语权 ---
    grand_ev = _hits(messages, _GRANDPARENT_KW)
    decide_ev = _hits(messages, _GRAND_DECIDE_KW)
    pro_ev = _count_hits(messages, _GRAND_PRO_KW)
    con_ev = _count_hits(messages, _GRAND_CON_KW)
    grand_detected = bool(grand_ev and (decide_ev or pro_ev or con_ev or len(grand_ev) >= 2))
    who = []
    for m in messages:
        t = _text_of(m)
        for g in ["奶奶", "姥姥", "爷爷", "外婆", "外公", "婆婆", "公公"]:
            if g in t and g not in who:
                who.append(g)
    if con_ev > pro_ev:
        stance = "反对"
    elif pro_ev > con_ev:
        stance = "支持"
    elif grand_detected:
        stance = "观望"
    else:
        stance = "未知"
    grandparent = {
        "detected": grand_detected,
        "who": who[:3],
        "stance": stance,
        "confidence": _confidence(grand_ev),
        "evidence": (grand_ev + decide_ev)[:5],
        "adaptation": (f"祖辈（{','.join(who) or '长辈'}）存在隐形话语权，立场为{stance}；"
                      "见家长支线需先经营祖辈认可度") if grand_detected else "无祖辈决策信号",
    }

    # --- 养老压力 ---
    care_n = _count_hits(messages, _CAREGIVING_KW)
    care_ev = _hits(messages, _CAREGIVING_KW)
    care_level = "未知"
    if care_n >= 3:
        care_level = "重"
    elif care_n == 2:
        care_level = "中"
    elif care_n == 1:
        care_level = "轻"
    caregiving = {
        "detected": care_n > 0,
        "level": care_level,
        "evidence": care_ev,
        "adaptation": ("多子女/多老人养老压力存在，任务节奏需避开对方奔波照料期，"
                       "长期规划预留赡养预算讨论位") if care_n > 0 else "无养老压力信号",
    }

    # --- 家庭负债 ---
    debt_n = _count_hits(messages, _DEBT_KW)
    debt_ev = _hits(messages, _DEBT_KW)
    debt_level = "未知"
    if debt_n >= 3:
        debt_level = "重"
    elif debt_n == 2:
        debt_level = "中"
    elif debt_n == 1:
        debt_level = "轻"
    debt = {
        "detected": debt_n > 0,
        "level": debt_level,
        "evidence": debt_ev,
        "adaptation": ("存在家庭负债信号，涉及金钱安排建议留存书面记录并咨询专业"
                       "法律意见；游戏内节奏放慢金钱相关任务") if debt_n > 0 else "无负债信号",
    }

    return {
        "remarried_family": remarried,
        "stepchildren": stepchildren,
        "grandparent_influence": grandparent,
        "caregiving_pressure": caregiving,
        "family_debt": debt,
    }


# --------------------------------------------------------------------------- #
# C 类：关系状态
# --------------------------------------------------------------------------- #
_STATUS_PATTERNS = [
    # (status, keywords) —— 按优先级匹配：已婚 > 离异 > 订婚 > 同居 > 恋爱中
    ("已婚", ["我们结婚了", "已经结婚", "领了证", "结婚证", "我老婆", "我老公",
              "我妻子", "我丈夫", "已婚"]),
    ("离异", ["离了婚", "已经离异", "刚离婚", "前夫", "前妻", "我离过婚", "离异"]),
    ("订婚", ["订婚了", "订了婚", "婚约", "已经订婚", "双方家长定了"]),
    ("同居", ["同居", "住在一起", "搬过去住", "住到一起", "合租套房住"]),
    ("恋爱中", ["谈恋爱", "男女朋友", "我们在一起", "正式交往", "对象"]),
]
_LEGAL_MONEY_KW = ["房产", "房子", "署名", "首付", "彩礼", "陪嫁", "共同负债",
                   "一起贷款", "借款", "欠款", "婚前"]


def extract_relationship_state(messages: list) -> dict:
    """C 类：已婚/离异/同居/订婚/恋爱中/未知；缺失必须标"未知"。"""
    markers: list[str] = []
    evidence: list[str] = []
    status = "未知"
    matched_kw: list[str] = []

    for st, kws in _STATUS_PATTERNS:
        ev = _hits(messages, kws)
        if ev:
            status = st
            evidence = ev
            matched_kw = [k for k in kws if any(k in e for e in ev)]
            markers = matched_kw[:4]
            break  # 取最高优先级命中

    # 法律风险提示（仅合规口径，绝不指导规避）
    legal_prompts: list[str] = []
    money_ev = _hits(messages, _LEGAL_MONEY_KW)
    if evidence or money_ev:
        legal_prompts.append(
            "涉及房产署名/首付/彩礼/共同负债等财产安排时，建议留存书面记录并咨询专业法律意见")
    if status == "离异" and (evidence or money_ev):
        legal_prompts.append(
            "离异背景涉及前一段婚姻财产/子女抚养事项，建议以书面方式厘清边界后再推进")

    return {
        "status": status,
        "confidence": _confidence(evidence) if evidence else 0.0,
        "evidence": evidence,
        "markers": markers,
        "legal_risk_prompts": legal_prompts,
    }


# --------------------------------------------------------------------------- #
# D 类：复合/拉扯模式
# --------------------------------------------------------------------------- #
_BREAKUP_KW = ["分手", "散了吧", "结束吧", "拉黑", "别联系了", "算了我们"]
_RECONCILE_KW = ["和好", "复合", "又在一起", "加回来", "原谅你", "我们和好了",
                 "重新在一起"]
_ENTANGLED_KW = ["纠缠", "威胁", "寻死", "割腕", "断联", "复联", "反复", "拉扯",
                 "拉黑又加", "一哭二闹"]
_EX_NEW_KW = ["新女友", "新男友", "新欢", "新女朋友", "新男朋友", "带新女友",
              "他带女朋友", "她和新欢", "新的对象", "对方有别人"]


def detect_rebound_patterns(messages: list) -> dict:
    """D 类：分手-复合循环 / 纠缠式复合 / 前任带新欢博弈。"""
    texts = [_text_of(m) for m in messages if _text_of(m)]
    joined = "\n".join(texts)

    # cycle_count：按消息顺序扫描"分手事件 → 和好事件"配对
    cycle_count = 0
    cycle_evidence: list[str] = []
    awaiting_reconcile = False
    pending_breakup_ev = ""
    for t in texts:
        is_break = any(k in t for k in _BREAKUP_KW)
        is_recon = any(k in t for k in _RECONCILE_KW)
        if is_break and not is_recon:
            awaiting_reconcile = True
            if not pending_breakup_ev:
                pending_breakup_ev = _snippet(t, next(k for k in _BREAKUP_KW if k in t))
        elif is_recon and awaiting_reconcile:
            cycle_count += 1
            recon_ev = _snippet(t, next(k for k in _RECONCILE_KW if k in t))
            cycle_evidence.append(f"{pending_breakup_ev}  →  {recon_ev}")
            awaiting_reconcile = False
            pending_breakup_ev = ""
        elif is_recon:
            # 无前置分手的"和好"不计数，但记一次证据
            if not cycle_evidence:
                cycle_evidence.append(_snippet(t, next(k for k in _RECONCILE_KW if k in t)))

    entangled = any(k in joined for k in _ENTANGLED_KW)
    ex_new = any(k in joined for k in _EX_NEW_KW)

    # escalation_level：按信号强度分级
    score = 0
    if cycle_count >= 3:
        score += 3
    elif cycle_count == 2:
        score += 2
    elif cycle_count == 1:
        score += 1
    if entangled:
        score += 2
    if ex_new:
        score += 2
    escalation = "无"
    if score >= 5:
        escalation = "高"
    elif score >= 3:
        escalation = "中"
    elif score >= 1:
        escalation = "低"

    intervention: list[str] = []
    if cycle_count >= 2:
        intervention.append("分手-复合循环已≥2轮：先落一轮「稳定锚点」任务，连续7天无反复再推进")
    if entangled:
        intervention.append("存在拉扯/威胁/断联复联信号：建立边界练习任务，不接情绪勒索节奏")
    if ex_new:
        intervention.append("前任带新欢博弈信号：暂停对比型任务，落差异化自我建设支线")

    return {
        "cycle_count": cycle_count,
        "cycle_evidence": cycle_evidence[:5],
        "entangled": entangled,
        "ex_new_partner": ex_new,
        "escalation_level": escalation,
        "intervention_tasks": intervention,
    }


# --------------------------------------------------------------------------- #
# E 类：条件差 / 异地
# --------------------------------------------------------------------------- #
_INCOME_KW = ["收入差", "工资比", "月薪", "年薪", "赚得比", "收入高", "收入低"]
_INCOME_HIGH_ME = ["我工资比他高", "我收入比她高", "我赚得比他多", "条件我好"]
_INCOME_HIGH_OTHER = ["他收入比我高", "她工资高", "他条件好", "她家境好", "他赚得多"]
_FAMILY_GAP_KW = ["门当户对", "家境", "家里条件", "父母嫌", "嫌他穷", "嫌她穷",
                  "两家条件差"]
_DISTANCE_KW = ["异地", "两个城市", "跨省", "相距", "多久见一次", "高铁几个小时",
                "来回跑"]


def model_condition_gap(messages: list) -> dict:
    """E 类：收入差/家庭条件差/异地长期化；只做适配，不评判优劣。"""
    inc_ev = _hits(messages, _INCOME_KW)
    me_high = _count_hits(messages, _INCOME_HIGH_ME)
    other_high = _count_hits(messages, _INCOME_HIGH_OTHER)
    if inc_ev:
        if me_high > other_high:
            direction = "我高"
        elif other_high > me_high:
            direction = "对方高"
        else:
            direction = "未知"
    else:
        direction = "未知"
    income_gap = {
        "detected": bool(inc_ev),
        "direction": direction,
        "evidence": inc_ev,
    }

    fam_ev = _hits(messages, _FAMILY_GAP_KW)
    family_gap = {"detected": bool(fam_ev), "evidence": fam_ev}

    dist_ev = _hits(messages, _DISTANCE_KW)
    months: int | None = None
    if dist_ev:
        for m in messages:
            t = _text_of(m)
            mt = re.search(r"异地[了快]?\s*(\d+)\s*(个)?月", t) or \
                re.search(r"(\d+)\s*个多月?[没未]?见", t) or \
                re.search(r"分开\s*(\d+)\s*(个)?月", t)
            if mt:
                months = int(mt.group(1))
                break
    long_distance = {"detected": bool(dist_ev), "months": months, "evidence": dist_ev}

    # tension_level：按信号强度
    tension_score = sum([
        bool(inc_ev), bool(fam_ev), bool(dist_ev),
        months is not None and months >= 6,
    ])
    tension = "未知"
    if tension_score >= 3:
        tension = "高"
    elif tension_score == 2:
        tension = "中"
    elif tension_score == 1:
        tension = "低"

    # task_difficulty_adjustment：与 TASK_DIFFICULTY_XP 口径一致
    if tension == "高" or (dist_ev and months and months >= 6):
        difficulty = "困难"
    elif tension == "中":
        difficulty = "普通"
    elif tension == "低":
        difficulty = "简单"
    else:
        difficulty = "普通"

    script_parts: list[str] = []
    if inc_ev:
        script_parts.append("收入相关话题不攀比、不贴标签，聊规划不聊数字")
    if fam_ev:
        script_parts.append("家庭条件差异不做价值判断，聚焦共同生活方案")
    if dist_ev:
        script_parts.append("异地场景话术增加'固定见面节奏'与'共享日常'设计")
    script_adjustment = "；".join(script_parts) if script_parts else "无特殊适配"

    return {
        "income_gap": income_gap,
        "family_gap": family_gap,
        "long_distance": long_distance,
        "tension_level": tension,
        "task_difficulty_adjustment": difficulty,
        "script_adjustment": script_adjustment,
    }


# --------------------------------------------------------------------------- #
# F 类：创伤驱动
# --------------------------------------------------------------------------- #
_TRAUMA_RULES = [
    {
        "type": "原生家庭创伤",
        "kw": ["家暴", "小时候打", "原生家庭", "父母吵架", "父母天天吵架", "天天吵架",
               "童年阴影", "缺爱", "从小没人管", "父母离异"],
        "behavior_link": "原生家庭冲突史 → 当前在亲密关系中对冲突高度敏感/回避谈家庭话题",
    },
    {
        "type": "被抛弃恐惧",
        "kw": ["被抛弃", "怕你离开", "你不要我", "被丢下", "被抛弃过", "怕被甩",
               "一不回消息就慌"],
        "behavior_link": "既往被抛弃经历 → 当前频繁确认爱意/对失联过度反应/易恐慌",
    },
    {
        "type": "讨好型损耗",
        "kw": ["讨好", "不敢拒绝", "委屈自己", "一直在付出", "迁就", "怕你不高兴",
               "从来都是我让步"],
        "behavior_link": "长期讨好模式 → 当前关系中自我需求后置/情绪积压/难以表达边界",
    },
]


def analyze_trauma_drivers(messages: list, persona_result: dict | None = None) -> dict:
    """F 类：原生家庭创伤 / 被抛弃恐惧 / 讨好型损耗；对接 persona attachment。"""
    traumas: list[dict] = []
    for rule in _TRAUMA_RULES:
        ev = _hits(messages, rule["kw"])
        if ev:
            traumas.append({
                "type": rule["type"],
                "evidence": ev,
                "behavior_link": rule["behavior_link"],
            })

    # attachment_link：对接 persona_result.attachment.primary_type/type
    attachment_link = "未提供 persona 结果，未做依恋联动"
    if persona_result:
        att = persona_result.get("attachment") or {}
        att_type = str(att.get("primary_type") or att.get("type") or "")
        if att_type:
            if "焦虑" in att_type or "恐惧" in att_type:
                attachment_link = (f"对方依恋主类型为{att_type}，与'被抛弃恐惧'创伤行为链路"
                                   "相互印证：高焦虑+既往抛弃史会放大确认需求")
            elif "回避" in att_type:
                attachment_link = (f"对方依恋主类型为{att_type}，与'原生家庭创伤'行为链路"
                                   "相互印证：回避型倾向在压力下情感退缩")
            else:
                attachment_link = f"对方依恋主类型为{att_type}，创伤链路需以行为证据继续观察"
        else:
            attachment_link = "persona 结果中未识别到依恋主类型"

    adaptations: list[str] = []
    trauma_types = {t["type"] for t in traumas}
    if "原生家庭创伤" in trauma_types:
        adaptations.append("不催促谈原生家庭话题，家庭支线节奏放慢")
    if "被抛弃恐惧" in trauma_types:
        adaptations.append("主动给稳定的可预期回应，减少突然失联；不玩'消失试探'")
    if "讨好型损耗" in trauma_types:
        adaptations.append("引导对方表达真实偏好，任务设计避免单向付出")

    return {
        "traumas": traumas,
        "attachment_link": attachment_link,
        "strategy_adaptations": adaptations,
    }


# --------------------------------------------------------------------------- #
# 门面函数
# --------------------------------------------------------------------------- #
def analyze_relationship_complexity(person_id: str, messages: list,
                                    persona_result: dict | None = None) -> dict:
    """按冻结 schema 返回完整复杂度画像。"""
    family_system = extract_family_system(messages)
    relationship_state = extract_relationship_state(messages)
    rebound_pattern = detect_rebound_patterns(messages)
    condition_gap = model_condition_gap(messages)
    trauma_drivers = analyze_trauma_drivers(messages, persona_result)

    flags: list[dict] = []

    def _flag(category: str, name: str, detected: bool, ev: list[str], adaptation: str):
        if detected:
            flags.append({
                "category": category,
                "flag": name,
                "evidence": ev[0] if ev else "",
                "adaptation": adaptation,
            })

    # A 类：至少一条 flag；无信号时也落一条"待观察"flag（保证每类都可观测）
    _flag("A", "重组家庭", family_system["remarried_family"]["detected"],
          family_system["remarried_family"]["evidence"],
          family_system["remarried_family"]["adaptation"])
    _flag("A", "继子女变量", family_system["stepchildren"]["detected"],
          family_system["stepchildren"]["evidence"],
          family_system["stepchildren"]["adaptation"])
    _flag("A", "祖辈话语权", family_system["grandparent_influence"]["detected"],
          family_system["grandparent_influence"]["evidence"],
          family_system["grandparent_influence"]["adaptation"])
    _flag("A", "养老压力", family_system["caregiving_pressure"]["detected"],
          family_system["caregiving_pressure"]["evidence"],
          family_system["caregiving_pressure"]["adaptation"])
    _flag("A", "家庭负债", family_system["family_debt"]["detected"],
          family_system["family_debt"]["evidence"],
          family_system["family_debt"]["adaptation"])
    if not any(f["category"] == "A" for f in flags):
        flags.append({"category": "A", "flag": "家庭系统待观察",
                      "evidence": "未检测到家庭系统关键信号", "adaptation": "保持常规节奏"})

    # C 类
    if relationship_state["status"] != "未知":
        flags.append({"category": "C", "flag": f"关系状态={relationship_state['status']}",
                      "evidence": relationship_state["evidence"][0]
                      if relationship_state["evidence"] else "",
                      "adaptation": "按当前状态推进对应任务分支"})
    else:
        flags.append({"category": "C", "flag": "关系状态未知",
                      "evidence": "聊天中未出现状态关键词",
                      "adaptation": "不臆断状态，通过常规话题自然确认"})

    # D 类
    if rebound_pattern["cycle_count"] > 0 or rebound_pattern["entangled"] \
            or rebound_pattern["ex_new_partner"]:
        flags.append({
            "category": "D",
            "flag": f"复合循环{rebound_pattern['cycle_count']}轮/"
                    f"纠缠={rebound_pattern['entangled']}/新欢博弈={rebound_pattern['ex_new_partner']}",
            "evidence": rebound_pattern["cycle_evidence"][0]
            if rebound_pattern["cycle_evidence"] else "",
            "adaptation": "；".join(rebound_pattern["intervention_tasks"]) or "保持稳定节奏",
        })
    else:
        flags.append({"category": "D", "flag": "无复合循环信号",
                      "evidence": "未检测到分手-配对事件", "adaptation": "常规推进"})

    # E 类
    e_any = (condition_gap["income_gap"]["detected"]
             or condition_gap["family_gap"]["detected"]
             or condition_gap["long_distance"]["detected"])
    flags.append({
        "category": "E",
        "flag": f"条件张力={condition_gap['tension_level']}/难度={condition_gap['task_difficulty_adjustment']}",
        "evidence": (condition_gap["income_gap"]["evidence"]
                     or condition_gap["long_distance"]["evidence"]
                     or condition_gap["family_gap"]["evidence"]
                     or ["未检测到条件差异信号"])[0],
        "adaptation": condition_gap["script_adjustment"],
    })

    # F 类
    if trauma_drivers["traumas"]:
        flags.append({
            "category": "F",
            "flag": "创伤驱动:" + "/".join(t["type"] for t in trauma_drivers["traumas"]),
            "evidence": trauma_drivers["traumas"][0]["evidence"][0],
            "adaptation": "；".join(trauma_drivers["strategy_adaptations"]),
        })
    else:
        flags.append({"category": "F", "flag": "无明确创伤信号",
                      "evidence": "未检测到创伤关键词",
                      "adaptation": trauma_drivers["attachment_link"]})

    return {
        "person_id": person_id,
        "family_system": family_system,
        "relationship_state": relationship_state,
        "rebound_pattern": rebound_pattern,
        "condition_gap": condition_gap,
        "trauma_drivers": trauma_drivers,
        "complexity_flags": flags,
    }
