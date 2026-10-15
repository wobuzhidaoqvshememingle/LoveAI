# -*- coding: utf-8 -*-
"""第七批第一批(1-5)：11级闯关/代聊填微信框架/Q版立绘/双数字分身/双咨询模块。纯函数向后兼容。"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

# ============ 1. 11级闯关系统 ============
LEVELS11 = ["相识","相知","好友","密友","暧昧","对象","未婚夫妻","夫妻","伴侣","孩子父母","终身伴侣"]

def stage_to_11(affection: float) -> dict:
    """好感度→11级；过渡期兼容旧阶段。"""
    idx = min(10, max(0, int(affection // 10)))
    return {"level_name": LEVELS11[idx], "level_idx": idx + 1,
            "next": LEVELS11[idx + 1] if idx < 10 else None,
            "progress": round(affection % 10 / 10, 2)}


# ============ 2. 代聊直接填入微信（框架+接口，等密钥通电）============
def fill_wechat_input(text: str) -> dict:
    """框架：把回复填入当前微信输入框。未接密钥时仅返回payload。"""
    return {"action": "fill_wechat_input", "text": text, "ready": False,
            "note": "等密钥通电后调用UI自动化填入"}


# ============ 3. Q版立绘生成（框架）============
def q_avatar(name: str, photo_path: str = "") -> dict:
    return {"name": name, "q_version": f"Q版立绘:{name}", "source": photo_path,
            "ready": bool(photo_path), "note": "保留3D桌宠"}


# ============ 4. 双数字分身 ============
def dual_twin_simulate(partner_view: str, user_view: str) -> dict:
    return {"partner_side": partner_view, "user_side_mock": user_view,
            "user_ask": "如果是我会怎么说"}


# ============ 5. 情感咨询双模块 ============
def consult(question: str, mode: str = "partner") -> dict:
    """mode: partner=对象视角私教 / growth=自我成长咨询。"""
    if mode == "growth":
        return {"mode":"自我成长咨询", "focus": "用户自身能力/边界/心态", "answer": f"针对问题[{question[:20]}]的成长建议"}
    return {"mode":"对象视角私教", "focus": "对方心理/应对策略", "answer": f"针对问题[{question[:20]}]的应对策略"}
