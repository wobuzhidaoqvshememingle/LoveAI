# -*- coding: utf-8 -*-
"""
LoveAI v3.1 加固补丁 · P-07 拒绝信号自动降级（红线）

设计目标（严格对齐《统一实施总纲 v3.1 加固补丁》P-07）：
  1. 识别【明确拒绝】（"别说了/不想聊/别烦我"等）与【暗示性拒绝】
     （连续单字敷衍、话题反复转移、长冷/不回复）。
  2. 触发后：分身 / 任务 / 话术统一降级为"尊重边界、停止推进"；
     邀约/表白/推进类任务立即软暂停（沿用 G.4，软暂停不删除）。
  3. 与主体性协调：本模块只拦"骚扰式推进"；针对"捞取/不对等"的反制、
     "引导对等投入"主线、给空间/设边界等中性手段【一律不拦、不降级】。

工程约束（可单测、离线）：
  - 全部为纯函数，不 import PySide6 / 不碰 DB / 不加载模型 / 不联网；
  - 长冷结论直接复用 NoReplyDetector.detect_no_reply_scenario 的返回 dict
    （只读其结论，不重复实现其时间序列算法）；
  - 发送永远手动：本模块只产出"降级决策 / 应软暂停哪些任务 / 给分身的提示语"，
    绝不触发任何自动发送。
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------- #
# 词表
# --------------------------------------------------------------------------- #

# 明确拒绝（强停止）：对方明确叫我们别再说 / 别再发 / 别再找
EXPLICIT_STRONG: tuple[str, ...] = (
    "别说了", "别发了", "不想聊", "不想谈", "不想再说", "不要再说",
    "别烦我", "别理我", "别找我", "别联系", "别打扰", "你别再",
    "能不能别", "求你别", "先不要发", "你别这样", "滚",
)

# 明确要空间（边界请求）：不叫我们滚，但明确要求暂停 / 给空间
EXPLICIT_SPACE: tuple[str, ...] = (
    "让我静静", "我想静静", "给我点空间", "给我点时间", "我需要冷静",
    "我们冷静一下", "各自冷静", "先别联系", "让我一个人", "我需要静一静",
    "我需要喘口气", "让我缓一缓", "我们都冷静", "最近别聊",
)

# 暗示性拒绝：话题反复转移 / 回避 markers
TOPIC_SHIFT: tuple[str, ...] = (
    "对了", "不说这个", "换个话题", "不说他了", "以后再说", "到时候再说",
    "再说吧", "不知道", "无所谓", "随便吧", "嗯再说", "这个话题过了",
)

# 暗示性拒绝：连续单字敷衍（冷场填充词）
COLD_SINGLE_CHARS: set[str] = {
    "哦", "嗯", "啊", "额", "呵", "嗨", "喔", "噢", "唔", "哎", "emm",
    "嗯。", "哦。", "额。", "啊。", "哈", "哈哈哈",
}

# 推进类任务关键词（命中且未被 PROTECTED 保护时 → 软暂停）
PUSH_TASK_KEYWORDS: tuple[str, ...] = (
    "约", "表白", "告白", "确认关系", "独处", "见面", "升温", "升级",
    "推进", "牵手", "肢体接触", "肢体",
)

# 中性 / 反制 / 给空间类任务：即使含推进字眼也【绝不软暂停】（保护主体）
PROTECTED_TASK_MARKERS: tuple[str, ...] = (
    "给空间", "设边界", "反制", "减量", "条件交换", "降温", "止损",
    "测试承诺", "引导对方投入", "富兰克林", "给台阶", "轻量接触",
    "不追问", "不逼表态", "现实检验", "边界",
)

# 长冷判定所采纳的 NoReplyDetector 严重度（轻度不算，避免误伤正常慢回）
LONG_COLD_SEVERITIES: set[str] = {"中度", "重度", "极端"}


# --------------------------------------------------------------------------- #
# 输入归一
# --------------------------------------------------------------------------- #
def _normalize_messages(messages: Any) -> list[tuple[str, str]]:
    """把多种输入归一为 [(direction, content)]，direction ∈ {"sent","received"}。

    支持：
      - [{"direction":"received","content":"..."}, ...]（推荐，与 chat_messages 一致）；
      - ["对方说的话", ...]（纯字符串列表，一律视为对方 received）。
    """
    out: list[tuple[str, str]] = []
    for m in messages or []:
        if isinstance(m, dict):
            d = m.get("direction") or "received"
            c = m.get("content") or m.get("text") or ""
            out.append((d, str(c)))
        else:
            out.append(("received", str(m)))
    return out


# --------------------------------------------------------------------------- #
# 单信号检测
# --------------------------------------------------------------------------- #
def is_explicit_refusal(text: str) -> bool:
    """对方这条消息是否构成明确拒绝（强停止 或 明确要空间）。"""
    t = (text or "").strip()
    if not t:
        return False
    return any(k in t for k in EXPLICIT_STRONG) or any(k in t for k in EXPLICIT_SPACE)


def _trailing_cold_run(msgs: list[tuple[str, str]]) -> int:
    """从最近一条对方消息向前数，连续的"单字冷敷衍"长度。"""
    run = 0
    for d, c in reversed(msgs):
        if d != "received":
            break  # 连续单字必须是对方连发，我方插话即中断
        core = re.sub(r"[。!?！?~\s.…、,，]", "", c or "")
        if len(core) <= 2 and core in COLD_SINGLE_CHARS:
            run += 1
        else:
            break
    return run


def _topic_shift_count(msgs: list[tuple[str, str]], window: int = 6) -> int:
    """最近 window 条对方消息里，话题转移/回避 marker 命中数。"""
    recent = [c for d, c in msgs[-window:] if d == "received"]
    hits = 0
    for c in recent:
        if any(k in (c or "") for k in TOPIC_SHIFT):
            hits += 1
    return hits


# --------------------------------------------------------------------------- #
# 主入口：拒绝信号检测 + 统一降级决策
# --------------------------------------------------------------------------- #
def detect_refusal(
    messages: Any = None,
    no_reply: dict | None = None,
    single_char_run_threshold: int = 3,
    topic_shift_threshold: int = 3,
) -> dict:
    """检测对方是否正在发出拒绝信号，并给出统一降级决策。

    Args:
        messages: 最近消息列表（dict 带 direction/content，或纯字符串列表）。
        no_reply: 可选；NoReplyDetector.detect_no_reply_scenario 的返回 dict，
                  用于复用其"长冷/不回复"结论（不重复实现时间序列算法）。
        single_char_run_threshold: 连续单字敷衍触发阈值（默认 3）。
        topic_shift_threshold: 话题反复转移触发阈值（默认 3）。

    Returns:
        dict: {
          "triggered": bool,            # 是否触发降级
          "level": "none"|"soft"|"hard",# hard=明确拒绝；soft=暗示/要空间/长冷
          "reasons": [str],             # 人类可读的命中原因
          "downgrade": "hold_boundary"|"none",
          "boundary_note": str,         # 给任务/分身/话术的统一降级说明
        }
    """
    msgs = _normalize_messages(messages)
    reasons: list[str] = []
    level = "none"

    # 1) 明确拒绝（扫描最近若干条对方消息）
    recent_partner = [c for d, c in msgs[-8:] if d == "received"]
    strong_hit = next((c for c in recent_partner
                       if any(k in c for k in EXPLICIT_STRONG)), None)
    space_hit = next((c for c in recent_partner
                      if any(k in c for k in EXPLICIT_SPACE)), None)
    if strong_hit is not None:
        level = "hard"
        reasons.append(f"明确拒绝（强停止）：「{strong_hit.strip()[:20]}」")
    elif space_hit is not None:
        level = "soft"
        reasons.append(f"明确要空间：「{space_hit.strip()[:20]}」")

    # 2) 暗示性拒绝 · 连续单字敷衍
    run = _trailing_cold_run(msgs)
    if run >= single_char_run_threshold and level == "none":
        level = "soft"
        reasons.append(f"连续 {run} 条单字冷敷衍（哦/嗯/额…）")
    elif run >= single_char_run_threshold:
        reasons.append(f"另伴连续 {run} 条单字冷敷衍")

    # 3) 暗示性拒绝 · 话题反复转移
    shift = _topic_shift_count(msgs)
    if shift >= topic_shift_threshold and level == "none":
        level = "soft"
        reasons.append(f"近 {shift} 次话题转移/回避（对了/以后再说/随便吧…）")
    elif shift >= topic_shift_threshold:
        reasons.append(f"另伴近 {shift} 次话题转移/回避")

    # 4) 长冷 / 不回复（复用 NoReplyDetector 结论）
    nr = no_reply or {}
    if (nr.get("is_no_reply")
            and nr.get("waiting_on_partner")
            and (nr.get("severity") in LONG_COLD_SEVERITIES)):
        cold_note = f"长冷不回复（严重度={nr.get('severity')}，已等待{nr.get('waiting_hours')}h）"
        if level == "none":
            level = "soft"
        reasons.append(cold_note)

    triggered = level != "none"
    downgrade = "hold_boundary" if triggered else "none"

    if triggered:
        boundary_note = (
            "对方正在表达边界：本轮起所有【邀约/表白/升温/确认关系】类动作立即软暂停，"
            "不再主动推进、不连发、不逼表态；改为低压力、给空间的轻量接触。"
            "（注意：针对捞取/不对等的反制、引导对等投入主线不受影响，仍以双向奔赴为目标。）"
        )
    else:
        boundary_note = ""

    return {
        "triggered": triggered,
        "level": level,
        "reasons": reasons,
        "downgrade": downgrade,
        "boundary_note": boundary_note,
    }


# --------------------------------------------------------------------------- #
# 任务软暂停判定（只拦推进类，保护反制/中性手段）
# --------------------------------------------------------------------------- #
def is_push_task(title: str) -> bool:
    """该任务是否属于"推进类"（邀约/表白/升温/升级…），拒绝触发时应软暂停。

    保护规则：标题含中性/反制/给空间 marker 时一律返回 False（不误伤主线）。
    """
    t = title or ""
    if not t:
        return False
    if any(p in t for p in PROTECTED_TASK_MARKERS):
        return False
    return any(k in t for k in PUSH_TASK_KEYWORDS)


def filter_push_titles(titles: list[str]) -> list[str]:
    """从一批任务标题中筛出"应软暂停"的推进类标题。"""
    return [t for t in (titles or []) if is_push_task(t)]


# --------------------------------------------------------------------------- #
# 给数字分身（digital_twin）的降级提示语
# --------------------------------------------------------------------------- #
def build_boundary_prompt_addendum(decision: dict) -> str:
    """把降级决策转成分身 System Prompt 里的一句约束（未触发返回空串）。

    digital_twin 在 _build_system_prompt 末尾追加本返回值即可：
    触发时分身预测/话术不再"替用户加火推进"，而是按对方边界降温。
    """
    if not decision or not decision.get("triggered"):
        return ""
    level = decision.get("level", "soft")
    head = "【边界降级·P-07】"
    if level == "hard":
        body = (
            "对方已明确表达不想再聊/要停止推进。预测其反应与给话术时，"
            "严禁再引导用户邀约、表白、升温、逼表态；应模拟"
            "「尊重对方空间、降低联系频率、不追问」的冷而不僵姿态。"
        )
    else:
        body = (
            "对方近期在给边界（敷衍/转移话题/长冷）。预测与话术应降温："
            "不要替用户安排推进/邀约/升温，优先呈现"
            "「低压力轻量接触、给空间、等对方主动」的方向；"
            "但反制捞取、引导对方对等投入的主线判断不受此影响。"
        )
    return f"{head}{body}"
