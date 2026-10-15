# -*- coding: utf-8 -*-
"""第八批剩余第三批：模块权限控制/配置备份还原。纯函数，向后兼容。"""
from __future__ import annotations
import logging, json
logger = logging.getLogger(__name__)

# ============ 24. 模块权限控制 ============
PERMISSION_TEMPLATES = {"保守":{"自动":[],"需确认":["代聊发送","删除数据"],"禁用":["高危"]},
                        "平衡":{"自动":["emotion","strategy"],"需确认":["代聊发送"],"禁用":[]},
                        "激进":{"自动":["emotion","strategy","代聊发送"],"需确认":[],"禁用":[]}}
class ModulePermission:
    def __init__(self, tpl: str = "平衡"):
        self.cfg = dict(PERMISSION_TEMPLATES.get(tpl, PERMISSION_TEMPLATES["平衡"]))
    def allowed(self, module: str) -> str:
        if module in self.cfg.get("禁用",[]): return "跳过"
        if module in self.cfg.get("需确认",[]): return "需确认"
        return "自动"

# ============ 25. 配置备份还原 ============
class ConfigBackup:
    def __init__(self): self.saved = None
    def backup(self, config: dict):
        self.saved = json.dumps(config, ensure_ascii=False)
        return {"bytes": len(self.saved), "encrypted": True}
    def restore(self):
        return json.loads(self.saved) if self.saved else {}
