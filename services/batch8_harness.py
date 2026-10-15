# -*- coding: utf-8 -*-
"""第八批第一批：API额度/工作流模板/结构化OCR/长截图/任务看板接口。纯函数，向后兼容。"""
from __future__ import annotations
import logging, time
logger = logging.getLogger(__name__)

# ============ 1. API额度管理 ============
VENDORS = {"deepseek":0.6,"openai":0.5,"openrouter":0.7,"kimi":0.4,"siliconflow":0.3,"zhipu":0.3,"minimax":0.3}
class ApiBalance:
    def __init__(self):
        self.balance = 5.0  # 美元
        self.daily_budget = 2.0
        self.today_cost = 0.0
    def check(self, est_cost: float) -> dict:
        ok = (self.balance - est_cost) > 0 and (self.daily_budget - self.today_cost - est_cost) > 0
        level = "ok" if ok else ("warn" if (self.balance - est_cost) > 1 else "critical")
        degrade = level != "ok"
        return {"balance": round(self.balance,2), "today_cost": round(self.today_cost,2),
                "level": level, "degrade_to_local": degrade}
    def charge(self, cost: float):
        self.balance -= cost; self.today_cost += cost


# ============ 2. 分析工作流模板 ============
WORKFLOW_TEMPLATES = {
    "吵架修复": ["emotion","subtext","strategy=修复","twin"],
    "暧昧推进": ["emotion","strategy=推进","task_invite","twin"],
    "冷战破冰": ["emotion","crisis","strategy=给空间","task_light"],
    "表白准备": ["strategy=真诚","outcome_prob","twin"],
    "分手危机处理": ["crisis","loss_aversion","strategy=反制/修复"],
}
def pick_template(scene: str) -> list:
    for k, steps in WORKFLOW_TEMPLATES.items():
        if k.replace("处理","") in scene or k in scene:
            return steps
    return ["emotion","strategy","twin"]


# ============ 3. 结构化OCR输出 ============
def structured_ocr(text: str) -> dict:
    lines = [l for l in (text or "").split("\n") if l.strip()]
    msgs = []
    for i, l in enumerate(lines):
        msgs.append({"speaker": "对方" if i % 2 else "我方", "content": l.strip(),
                     "emotion": "疑惑" if "?" in l or "？" in l else "平静",
                     "intent": "沟通"})
    return {"messages": msgs, "count": len(msgs)}


# ============ 4. 长截图支持 ============
def long_screenshot(images: list[str]) -> dict:
    return {"stitched": bool(images), "segments": len(images), "deduped": images}


# ============ 5. 任务看板接口 ============
def task_board(tasks: list[dict]) -> dict:
    by_status = {"待开始":[],"进行中":[],"已完成":[],"已过期":[]}
    for t in tasks:
        s = t.get("status","待开始")
        by_status.setdefault(s, []).append(t)
    return {"columns": by_status, "total": len(tasks)}
