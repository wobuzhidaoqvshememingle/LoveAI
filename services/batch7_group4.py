# -*- coding: utf-8 -*-
"""第七批第四批(16-21)：代理配置/微信多版本UI适配器/密钥全盘扫描/学科挂载/放权机制/数据统计面板。纯函数向后兼容。"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

# ============ 16. 内置代理功能 ============
PROXY = {"enabled": False, "url": ""}
def set_proxy(url:str):
    PROXY.update({"enabled":bool(url), "url":url})
def proxy_for_model_request()->str|None:
    return PROXY["url"] if PROXY["enabled"] else None


# ============ 17. 微信多版本UI适配器 ============
WECHAT_UI_TPL = {"4.0":{"chat_list":"wx_chatlist_v4","input":"wx_input_v4"},
                 "4.1":{"chat_list":"wx_chatlist_v41","input":"wx_input_v41"},
                 "4.2":{"chat_list":"wx_chatlist_v42","input":"wx_input_v42"}}
def wechat_ui_template(version:str)->dict:
    return WECHAT_UI_TPL.get(version, WECHAT_UI_TPL["4.2"])


# ============ 18. 搜索密钥全盘扫描（框架，等用户操作）============
def scan_wechat_files(root:str)->dict:
    return {"scanned_root":root, "key_found":False,
            "candidates":[], "note":"等用户实际操作后通电"}


# ============ 19. 法学/经济学/社会学学科挂载 ============
SUBJECTS = {"法学":"法律风险/承诺效力", "经济学":"沉没成本/相对代价",
            "社会学":"社交层级/群体规范"}
def subject_for(scene:str)->str|None:
    if any(k in scene for k in ("钱","承诺","借条")): return "法学"
    if any(k in scene for k in ("付出","成本","投资")): return "经济学"
    if any(k in scene for k in ("朋友","圈子","介绍")): return "社会学"
    return None


# ============ 20. 放权机制（无设备练习）============
def autonomy_task(stage:str)->dict:
    return {"type":"无设备练习", "task":"不依赖AI给出独立应对话术",
            "goal":"培养用户独立能力", "difficulty":"中"}


# ============ 21. 数据统计面板 ============
def stats_panel(records:dict)->dict:
    """records: {analyze_count, task_done, task_total, affection_series, reply_usage}"""
    rate = round(records.get("task_done",0)/max(1,records.get("task_total",1)),2)
    return {"analyze_count":records.get("analyze_count",0),
            "task_completion_rate":rate,
            "affection_trend":records.get("affection_series",[]),
            "reply_usage":records.get("reply_usage",{})}
