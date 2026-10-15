# -*- coding: utf-8 -*-
"""LoveAI 微信多通道采集体系。

统一 SourceAdapter 接口 + CollectorRegistry，覆盖：
  PC(P1-P11) / 安卓(A1-A7) / 官方编排(O1-O2) / 第三方导入(T1-T4) / iOS 预留。

入口：
    from services.collectors import build_registry
    reg = build_registry()
    print(reg.render_table())
"""
from __future__ import annotations

from .base import (SourceAdapter, Message, Availability,
                   RISK_NONE, RISK_LOW, RISK_MEDIUM, RISK_HIGH,
                   PLAT_PC, PLAT_ANDROID, PLAT_OFFICIAL, PLAT_THIRDPARTY, PLAT_IOS,
                   DIR_SENT, DIR_RECEIVED, ok, missing)
from .registry import CollectorRegistry, MODULE_SPECS
from .store import MessageStore


def build_registry(auto_discover: bool = True) -> CollectorRegistry:
    """构造并返回一个已完成通道发现的 CollectorRegistry。"""
    return CollectorRegistry(auto_discover=auto_discover)


__all__ = [
    "SourceAdapter", "Message", "Availability", "MessageStore",
    "CollectorRegistry", "MODULE_SPECS", "build_registry",
    "RISK_NONE", "RISK_LOW", "RISK_MEDIUM", "RISK_HIGH",
    "PLAT_PC", "PLAT_ANDROID", "PLAT_OFFICIAL", "PLAT_THIRDPARTY", "PLAT_IOS",
    "DIR_SENT", "DIR_RECEIVED", "ok", "missing",
]
