# -*- coding: utf-8 -*-
"""F类：高级/有风险（默认禁用，用户确认后才启用）。"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

F_METHODS = ["api_hook_readonly", "debugger_readonly", "proxy_mitm", "sandbox_monitor", "multi_instance"]


class WechatAdvancedReader:
    def __init__(self, enabled: bool = False) -> None:
        self._enabled = enabled

    def enable(self) -> None:
        self._enabled = True

    def availability_check(self) -> dict:
        if not self._enabled:
            return {"available": False, "reason": "F类方法有封号风险，需用户确认后启用"}
        return {"available": True, "reason": "已启用（高级模式）"}

    def import_chats(self) -> list:
        return []  # 框架，等用户确认

    def list_methods(self) -> list:
        return list(F_METHODS)
