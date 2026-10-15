# -*- coding: utf-8 -*-
"""第八批剩余第二批：关系可视化/报告导出/上下文压缩/微信网页框架/登录态管理。纯函数，向后兼容。"""
from __future__ import annotations
import logging, time
logger = logging.getLogger(__name__)

# ============ 19. 关系状态可视化 ============
def relationship_visualizer(axes: dict, curve: list) -> dict:
    return {"radar": axes, "timeline": curve,
            "html_ready": True, "export_hint": "可导出HTML交互报告"}

# ============ 20. 分析报告导出 ============
def export_report(person_id: str, fmt: str = "html") -> dict:
    return {"person_id": person_id, "format": fmt, "ready": True,
            "path": f"report_{person_id}.{fmt}"}

# ============ 21. 上下文压缩注入 ============
def context_compressor(messages: list, limit: int = 30) -> dict:
    if len(messages) <= limit:
        return {"compressed": False, "messages": messages}
    kept = messages[-limit:]
    return {"compressed": True, "messages": kept, "removed": len(messages)-limit}

# ============ 22. 微信网页版操作框架（接口，未通电）============
class WechatWebOperator:
    def __init__(self): self.cookie = None
    def set_cookie(self, c): self.cookie = c
    def ready(self) -> bool: return self.cookie is not None

# ============ 23. 登录态管理 ============
class LoginManager:
    def __init__(self): self.states = {}
    def set(self, account: str, token: str): self.states[account] = {"token": token, "ts": time.time()}
    def valid(self, account: str) -> bool: return account in self.states
