# -*- coding: utf-8 -*-
"""第七批第三批(11-15)：主题字体/性别自动识别/四档窗口/TOP一TOP二/微信自动切换对象。纯函数向后兼容。"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

# ============ 11. 主题/字体大小 ============
UI_PREF = {"theme":"深色","font_size":14}
def set_theme(t:str): UI_PREF["theme"] = t
def set_font(s:int): UI_PREF["font_size"] = max(10, min(24, s))


# ============ 12. 性别自动识别切换 ============
def detect_gender(text:str)->str:
    female = sum(text.count(k) for k in ("美","妆","包","口红","宝","人家"))
    male = sum(text.count(k) for k in ("车","球","兄弟","哥们","游戏"))
    return "女" if female > male else ("男" if male > female else "未知")


# ============ 13. 四档窗口补全 ============
WINDOWS = {"wechat_overlay":"微信代聊浮窗", "danmaku":"弹幕窗",
           "top1_big":"主动任务大窗", "top2_small":"私教小窗"}


# ============ 14. TOP一/TOP二双核心 ============
def top1_execution(tasks:list)->dict: return {"核心":"主动执行+攻略", "active_tasks":tasks}
def top2_ask(question:str)->dict: return {"核心":"被动私教", "answer":f"回答[{question[:20]}]"}


# ============ 15. 微信自动切换对象（框架）============
def match_person_by_window_title(title:str, registry:dict)->dict:
    """检测微信聊天窗口标题→匹配person_id。registry: {person_id: name}"""
    for pid, name in registry.items():
        if name in (title or ""):
            return {"matched":True,"person_id":pid}
    return {"matched":False,"person_id":None}
