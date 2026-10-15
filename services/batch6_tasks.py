# -*- coding: utf-8 -*-
"""第六批第二批：任务链/每日任务+限时挑战/截图分析/关系危机预警。纯函数向后兼容。"""
from __future__ import annotations
import logging
import time
logger = logging.getLogger(__name__)

# ============ 5. 任务链系统 ============
TASK_CHAINS = {
    "初识": ["加好友破冰", "找共同话题", "建立日常聊天"],
    "暧昧": ["主动邀约", "提升亲密表达", "试探心意"],
    "表白": ["营造氛围", "真诚表达", "等待回应"],
    "稳定": ["共同投入", "承诺建设", "维护激情"],
}

def task_chain(stage: str, completed: list[str]) -> dict:
    """完成前一个解锁下一个，难度递增。"""
    chain = TASK_CHAINS.get(stage, TASK_CHAINS["初识"])
    unlocked = [t for t in chain if t not in completed]
    return {"stage_chain": stage, "all": chain, "next": unlocked[0] if unlocked else None,
            "remaining": len(unlocked)}


# ============ 6. 每日任务 + 限时挑战 ============
def daily_tasks(stage: str, relation_state: dict) -> list[dict]:
    base = {"初识": ["主动打招呼", "找一个话题", "回复带情绪"],
            "暧昧": ["主动联系一次", "邀约线索", "夸对方一句"],
            "稳定": ["关心对方近况", "一起做一件事", "表达感谢"]}
    tasks = [{"id": f"daily_{i}", "title": t, "xp": 10, "expired": False}
             for i, t in enumerate(base.get(stage, base["暧昧"]))]
    if time.strftime("%A") in ("Saturday", "Sunday"):
        tasks.append({"id": "limit_invite", "title": "周末邀约(限时)", "xp": 30, "expired": False})
    return tasks


# ============ 7. 一键截图分析 ============
def screenshot_analyze(text: str) -> dict:
    """OCR文本→言外之意/情绪/3条回复（纯规则，OCR外部接入）。"""
    t = text or ""
    emotion = "平静"
    if any(k in t for k in ("？", "?", "怎么", "为什么")): emotion = "疑惑"
    if any(k in t for k in ("。。", "...", "呵", "随便")): emotion = "冷淡"
    replies = [f"针对「{t[:8]}」的共情回复", "中性确认回复", "推进性回复"]
    return {"text": t[:200], "subtext": "需结合上下文", "emotion": emotion,
            "suggested_replies": replies}


# ============ 8. 关系危机预警 ============
def crisis_alert(signals: dict) -> dict:
    """黄/橙/红三级。signals: trust_drop_days/freq_drop_pct/neg_emotion_pct/reply_slow/no_reply_days。"""
    score = 0
    if signals.get("trust_drop_days", 0) >= 3: score += 2
    if signals.get("freq_drop_pct", 0) >= 50: score += 2
    if signals.get("neg_emotion_pct", 0) >= 60: score += 2
    if signals.get("reply_slow", False): score += 1
    if signals.get("no_reply_days", 0) >= 3: score += 2
    level = "红" if score >= 5 else ("橙" if score >= 3 else ("黄" if score >= 1 else "正常"))
    return {"level": level, "score": score,
            "repair_task": f"生成{level}级修复任务" if level != "正常" else None}
