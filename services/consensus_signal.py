# -*- coding: utf-8 -*-
"""共识 vs 顺从（Consensus Signal）：从"她"(direction=received) 的消息里识别
【主动参与解决】证据，并与【顺从型同意】严格区分。

背景（用户定稿口径）：
    罗盘北极星改为——她"主动参与解决"（她提出方案 / 让步带需求 / 说"我们一起" /
    主动推进话题或修复冲突）为【主信号】，关系满意度为【参考指标】。
    本模块负责从聊天记录里把"主动参与"证据找出来并打分，供罗盘 north_star 段消费。

────────────────────────────────────────────────────────────────────────────
【与既有"回撤≠降频"逻辑的边界 —— 铁律，务必遵守】
────────────────────────────────────────────────────────────────────────────
1. 顺从型同意（compliance，无投入的点头/服软）：
     * 例如 "嗯""好吧""都行""听你的""哦""好的"。
     * 它们是被动附和，**不是**她在为解决关系投入。
     * 规则：compliance 事件 ① 绝不触发回撤/降频信号；
             ② 绝不计入修复/升温证据；
             ③ 绝不进入罗盘主信号达标判定——只做计数展示。
   原因：避免把"她懒得吵了、敷衍式点头"误读成"关系在回稳/她在靠近"，
   否则就会重演"步步对、结局错"——表面太平，实则是她在抽离。

2. 主动参与证据（consensus）：
     * 她提出方案/建议、让步但带自己的需求（"…可以，但你要…"）、
       协作措辞（"我们一起""咱俩""商量"）、主动推进话题/修复冲突。
     * 只有 consensus 事件才计入北极星主信号 active_participation_score。

3. 本模块只做"分类与计数"，**不**改任何既有信号：
     * 只读 chat_messages，不写库；
     * 不修改 suspicion_chain / direction_compass 的 gates / drift / aligned；
     * 罗盘侧把本模块结果放在**附加键** north_star，绝不回流到既有闸门。
────────────────────────────────────────────────────────────────────────────

纯本地规则实现（无外部 LLM 依赖；保留可选 LLM 辅助钩子，但离线规则永远可回退、
永远可测）。每条证据带原文 + 时间 + wechat_msg_id，数字真实、证据驱动。
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 1. 词表 / 模式
# --------------------------------------------------------------------------- #
# 1a. 主动参与——她"提出方案 / 建议"（刻意收紧：只留低误报的联合提议词，
#     剔除裸"要不/不如"——真实库里"要不是…""不如不听""还不如"会大量误命中）
_PROPOSE_WORDS = [
    "要不我们", "要不咱", "要不你", "要不我",
    "不如我们", "不如咱", "不如你",
    "我们可以", "咱可以", "我们要不", "要不然我们",
    "我建议", "建议你", "可以试试", "要不试试",
    "下次我们", "下次一起", "下次咱",
    "要不要一起", "要不要出去", "要不要去", "要不要来",
]

# 放弃/认命式口吻（"就这样吧""先这样吧"是抽离不是提议）——命中即排除出主动参与
_RESIGN_WORDS = ["就这样吧", "就先这样", "先这样吧", "就这样了", "随便你吧",
                 "爱咋咋地", "无所谓了", "随便吧"]

# 1b. 主动参与——协作措辞（"我们一起 / 咱俩 / 商量"）
_COLLAB_WORDS = [
    "我们一起", "咱们一起", "咱俩", "我们俩", "一起去", "一起做",
    "一起吃", "一起玩", "一起过", "一起想",
    "商量一下", "商量商量", "我们商量", "商量着",
    "我们谈谈", "我们聊聊", "聊一下", "聊聊", "说开", "好好说",
    "我们各退", "各让一步", "互相理解", "互相体谅",
]

# 1c. 主动参与——主动推进 / 修复冲突
_RESOLVE_WORDS = [
    "我们和解", "和好吧", "和好", "解决一下", "解决这个",
    "沟通一下", "说清楚", "把话说开", "别吵了", "别闹了",
    "我们说说", "把问题", "面对问题", "一起面对", "一起解决",
]

# 1e. 行动型主动（她主动约见/推进谈/谈妥）——由评估集v2漏检证据驱动补入。
#     真实库里她的主动多是行动型（"我去找你""带妈来好好谈谈"），旧词表只认
#     "我们一起/我们谈谈"这类协作措辞，把行动型主动全漏了。
_ACTION_PROACTIVE_WORDS = [
    "好好谈谈", "坐下来好好谈", "谈一谈", "谈妥",
    "商量", "我去找你", "你来吧",
]

# 1f. 主动修复 / 情感投入（"我也想你了""我也爱你"）——旧规则完全漏掉，
#     导致 624 个顺从点头主导计数、而她真实的情感投入被当成 neutral（伪平稳）。
_WARMTH_REPAIR_WORDS = [
    "想你了", "我想你", "爱你", "我爱你",
]

# 1g. 误报陷阱排除（真实库踩到的假阳性）：
#   - "问他/问她" 指第三方，不是情侣联合行动（如"问他晚上要不要出去吃好吃的"）；
#   - "咱俩的事" 是划边界（"别纠缠我弟/咱俩的事"），不是协作措辞。
#     注意：必须在 _COLLAB 的"咱俩"之前判定，否则"咱俩"先命中。
_TRAP_EXCLUDE = ["问他", "问她", "咱俩的事", "没谈妥"]

# 1h. "爱你"的否定语境护栏（"别提爱我""求你别爱我了"是狠话不是投入）
_NEG_LOVE = ["别爱", "不爱", "别提爱", "不想爱"]

# 1d. 让步但带需求：先让步/答应，再用 但/不过/可是/就是 提出自己的条件。
#     例："可以，但你要早点睡" / "行，不过周末得陪我"。
#     这是【有投入的让步】，属于主动参与，**不是**顺从。
_CONCESSION_RE = re.compile(
    r"(可以|也行|行|好|好啊|好的|没问题|可以啊|行啊)"
    r"\s*[，,。.！!~\s]*"
    r"(但|不过|可是|就是|只不过)"
)

# 2. 顺从型同意——无投入的点头/服软（去标点后**完全等于**下列词之一才算）。
#    刻意做成"精确词表"，避免把"好，我们一起去"误判成顺从。
_EXACT_COMPLIANCE = {
    "嗯", "嗯嗯", "嗯嗯嗯", "嗯嗯嗯嗯", "嗯呢", "嗯呐",
    "哦", "哦哦", "哦哦哦", "噢", "噢噢", "喔", "呵",
    "啊", "啊哈", "哎",
    "好", "好吧", "好的", "好呀", "好了", "好嘞", "好吧好吧",
    "行", "行吧", "行啊", "行行", "可以", "可以的", "可以啊",
    "都行", "都行吧", "随便", "随你", "听你的", "你定", "你看着办",
    "是的", "是", "对", "对对", "对对对", "对的",
    "ok", "okay", "OK", "Ok", "Okk", "okk", "嗯ok",
    "好哒", "哒", "哈", "哈哈",
}

# 标点 / 空白 / 常见表情前缀，归一化时剥掉
_STRIP_CHARS = " \t\r\n，。,.！!？?、~～…·—-：:；;\"'“”‘’()（）[]【】<>"

# 非文本消息（红包/表情图片/系统消息）——content 以 b' 开头或含 <img/SystemMessages
_NONTEXT_RE = re.compile(r"^\s*b['\"]|<img\s|SystemMessages|<msgtype>|<face")


def _is_text(content: str) -> bool:
    """是否为可读文本消息（剔除二进制/图片/红包/系统消息的 repr 串）。"""
    if not content:
        return False
    if _NONTEXT_RE.search(content):
        return False
    return True


def _normalize(content: str) -> str:
    """去掉标点空白后用于精确匹配的串。"""
    s = content.strip()
    for ch in _STRIP_CHARS:
        s = s.replace(ch, "")
    return s


def _parse_dt(s: Optional[str]):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# 2. 单条分类：consensus（主动参与）/ compliance（顺从同意）/ neutral（其它）
# --------------------------------------------------------------------------- #
def classify_one(content: str) -> tuple[str, Optional[str]]:
    """返回 (category, pattern_hit)。category ∈ {consensus, compliance, neutral}。

    判定优先级：先判"主动参与"（让步带需求/方案/协作/修复词），再判"顺从同意"。
    任一主动参与词命中即 consensus——即使句子里也含"好/可以"，因为她附加了投入。
    """
    if not content or not _is_text(content):
        return "neutral", None

    c = content.strip()
    # --- 0) 放弃/认命式口吻：既非主动参与、也非顺从同意，直接 neutral ---
    #     （"就这样吧/随便吧"是抽离，绝不能被误读成升温或主信号）
    for w in _RESIGN_WORDS:
        if w in c:
            return "neutral", f"放弃口吻:{w}"

    # --- 0.5) 误报陷阱排除（必须在"提出方案/协作措辞"之前判）---
    #     "问他/问她"=第三方；"咱俩的事"=划边界而非协作。
    for w in _TRAP_EXCLUDE:
        if w in c:
            return "neutral", f"误报排除:{w}"

    # --- 1) 让步带需求（最强主动信号，先判）---
    m = _CONCESSION_RE.search(c)
    if m:
        after = c[m.end():].strip()
        # 让步后必须还有实质内容（她提了自己的条件/需求），否则只是语气词
        if len(_normalize(after)) >= 2:
            return "consensus", f"让步带需求:{m.group(2)}"

    # --- 2) 提出方案/建议 ---
    for w in _PROPOSE_WORDS:
        if w in c:
            return "consensus", f"提出方案:{w}"

    # --- 3) 协作措辞 ---
    for w in _COLLAB_WORDS:
        if w in c:
            return "consensus", f"协作措辞:{w}"

    # --- 4) 主动修复/推进 ---
    for w in _RESOLVE_WORDS:
        if w in c:
            return "consensus", f"主动推进:{w}"

    # --- 4.5) 行动型主动（约见/推进谈/谈妥）---
    for w in _ACTION_PROACTIVE_WORDS:
        if w in c:
            return "consensus", f"行动主动:{w}"

    # --- 4.6) 主动修复/情感投入（想你了/爱你），先过否定护栏 ---
    if not any(g in c for g in _NEG_LOVE):
        for w in _WARMTH_REPAIR_WORDS:
            if w in c:
                return "consensus", f"情感投入:{w}"

    # --- 5) 顺从型同意（仅在排除上述主动信号后，精确匹配词表）---
    norm = _normalize(c)
    if norm in _EXACT_COMPLIANCE:
        return "compliance", f"顺从同意:{norm}"

    return "neutral", None


# --------------------------------------------------------------------------- #
# 3. 聚合
# --------------------------------------------------------------------------- #
def detect(messages: list[dict], now: Optional[datetime] = None) -> dict:
    """messages: [{direction, content, created_at, wechat_msg_id?}, ...] 时间升序。

    只统计 direction=='received'（她）的文本消息。返回完整信号结构。
    """
    received = [m for m in messages if (m.get("direction") or "") == "received"]
    text_msgs = [m for m in received if _is_text(str(m.get("content") or ""))]

    consensus_events: list[dict] = []
    compliance_events: list[dict] = []

    for m in text_msgs:
        content = str(m.get("content") or "")
        cat, pattern = classify_one(content)
        ev = {
            "at": m.get("created_at") or "",
            "wechat_msg_id": m.get("wechat_msg_id") or "",
            "content": content[:120],
        }
        if cat == "consensus":
            ev["pattern"] = pattern
            ev["counts_toward_main_signal"] = True   # 铁律：只有它进主信号
            consensus_events.append(ev)
        elif cat == "compliance":
            ev["pattern"] = pattern
            ev["counts_toward_main_signal"] = False  # 铁律：顺从绝不进主信号/回撤/升温
            compliance_events.append(ev)

    c_count = len(consensus_events)
    p_count = len(compliance_events)

    # --- 最近 30 天趋势（相对 now，now 缺省取最后一条消息时间）---
    if now is None and text_msgs:
        now = _parse_dt(text_msgs[-1].get("created_at")) or datetime.now()
    now = now or datetime.now()
    cutoff30 = now - timedelta(days=30)
    cutoff60 = now - timedelta(days=60)

    def _in(ev: dict, lo: datetime, hi: datetime) -> bool:
        t = _parse_dt(ev.get("at"))
        return bool(t and lo <= t < hi)

    c_recent = [e for e in consensus_events if _in(e, cutoff30, now + timedelta(seconds=1))]
    c_prev = [e for e in consensus_events if _in(e, cutoff60, cutoff30)]
    p_recent = [e for e in compliance_events if _in(e, cutoff30, now + timedelta(seconds=1))]

    # 趋势：近30天 consensus vs 前30天 consensus
    if len(c_recent) > len(c_prev):
        momentum = "up"
    elif len(c_recent) < len(c_prev):
        momentum = "down"
    else:
        momentum = "flat"

    # --- 主动参与率 active_ratio：在"关系型回应池"里，主动 vs 顺从的占比 ---
    # 关系型回应池 = consensus + compliance（这两类才是"她对关系互动的表态"）
    pool = c_count + p_count
    active_ratio = round(c_count / pool, 3) if pool > 0 else None

    # 近30天活跃度因子（3 次主动参与/月 = 健康基线，封顶 1.0）
    recency_factor = round(min(1.0, len(c_recent) / 3.0), 3)

    # --- active_participation_score：0-100 主信号分 ---
    # 公式：0.7 * 主动占比（她是不是在"投入"而不是"应付"）
    #      + 0.3 * 近30天活跃度（这种主动是不是还在持续）
    if active_ratio is None:
        score: Optional[float] = None
    else:
        score = round(100.0 * (0.7 * active_ratio + 0.3 * recency_factor), 1)

    # --- 主信号达标判定（确定性阈值，标注依据）---
    if score is None:
        main_signal_reached: Optional[bool] = None
        main_signal_note = "无足够关系型回应证据（consensus+compliance=0），主信号暂不可判"
    else:
        # 达标线：active_ratio>=0.5（主动多于顺从）且 momentum 不为 down
        reached = (active_ratio >= 0.5) and (momentum != "down")
        main_signal_reached = reached
        main_signal_note = (
            f"达标条件=主动占比{active_ratio}>=0.5 且 趋势{momentum}!='down'"
        )

    return {
        "person_id": None,  # 由 run 入口填
        "received_message_count": len(received),
        "text_message_count": len(text_msgs),
        "consensus_event_count": c_count,
        "compliance_event_count": p_count,
        "consensus_rate": active_ratio,          # consensus/(consensus+compliance)
        "active_participation_score": score,      # 0-100 主信号分
        "main_signal_reached": main_signal_reached,
        "main_signal_note": main_signal_note,
        "trend_30d": {
            "window_days": 30,
            "consensus_recent": len(c_recent),
            "consensus_prior_30d": len(c_prev),
            "compliance_recent": len(p_recent),
            "momentum": momentum,                 # up/flat/down
            "as_of": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "consensus_events": consensus_events,       # 含 pattern/at/id/content
        "compliance_events": compliance_events,     # 仅展示，绝不进主信号
        "boundary_note": (
            "顺从型同意(compliance)不触发回撤/降频、不计入修复/升温证据、不计入主信号；"
            "仅 consensus(主动参与)计入北极星主信号。"
        ),
    }


# --------------------------------------------------------------------------- #
# 4. 一键入口（参照 suspicion_chain.run_suspicion_chain 的独立读库模式）
# --------------------------------------------------------------------------- #
def run_consensus_signal(db_path: str, person_id: str,
                         cutoff: Optional[str] = None) -> dict:
    """读 chat_messages → 分类聚合 → 输出共识/顺从信号。cutoff 用于回测时间截断。

    只读库；任何异常向上抛前由调用方 try 包裹（罗盘侧失败即该段标 None，不影响主流程）。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql = ("SELECT direction, content, created_at, wechat_msg_id "
           "FROM chat_messages WHERE person_id=? ORDER BY created_at ASC")
    params: list = [person_id]
    if cutoff:
        sql = ("SELECT direction, content, created_at, wechat_msg_id "
               "FROM chat_messages WHERE person_id=? AND created_at < ? "
               "ORDER BY created_at ASC")
        params.append(cutoff)
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    messages = []
    for r in rows:
        content = r["content"]
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        messages.append({
            "direction": r["direction"],
            "content": content or "",
            "created_at": r["created_at"] or "",
            "wechat_msg_id": r["wechat_msg_id"] or "",
        })

    result = detect(messages)
    result["person_id"] = person_id
    result["db_path"] = db_path
    return result


# --------------------------------------------------------------------------- #
# P0 卡11：Reed-Solomon K=3+M=1 多模型竞速共识（薄导出）
# RS 逻辑独立放在 services/rs_consensus.py，不在本模块硬塞；此处仅做命名空间导出。
# rs_consensus.py 不 import 本模块，无循环 import 风险。
# --------------------------------------------------------------------------- #
from services.rs_consensus import RSConsensusK3M1  # noqa: E402,F401
