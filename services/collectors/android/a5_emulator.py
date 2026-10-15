# -*- coding: utf-8 -*-
"""A5 模拟器 + 官方迁移 + root 读库（高风险，默认禁用）。

2026 年模拟器登录微信有明确封号/不解封实锤。本通道：
  - 默认 default_enabled=False（registry.run 会在 confirm_high_risk=False 时拦截）
  - available() 只做环境检测与引导，不自动登录、不自动跑模拟器
  - collect() 在显式确认后也只返回引导文本，不代用户执行任何登录动作
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_HIGH,
                    PLAT_ANDROID, ok, missing)
from .. import env

logger = logging.getLogger(__name__)

BAN_WARNING = (
    "【高风险】2026 年模拟器登录/迁移微信有明确封号且人工核实不解封的实锤。"
    "本通道默认禁用；如需使用必须显式 confirm_high_risk=True 并自行承担封号后果。"
)


def _detect_emulator() -> bool:
    """粗测本机是否装了常见安卓模拟器（BlueStacks / Nox / MuMu / LDPlayer / 夜神）。"""
    names = ["Bluestacks.exe", "Nox.exe", "MuMuPlayer.exe",
             "dnplayer.exe", "LdVBoxHeadless.exe"]
    for n in names:
        if shutil.which(n):
            return True
    # 常见安装目录
    for d in (r"C:\Program Files\BlueStacks",
              r"C:\Program Files (x86)\Nox\bin",
              r"C:\Program Files\Netease\MuMuPlayer-12.0",
              r"C:\Program Files\LDPlayer"):
        if os.path.isdir(d):
            return True
    return False


class A5EmulatorAdapter(SourceAdapter):
    id = "A5_emulator"
    name = "模拟器登录+迁移(高风险)"
    platform = PLAT_ANDROID
    requires = frozenset({"device"})
    risk_level = RISK_HIGH
    default_enabled = False
    local_runnable = False
    success_prior = 0.2
    produces_plaintext = True

    def available(self) -> Availability:
        has_emu = _detect_emulator()
        adb = env.adb_path()
        devices = env.adb_devices()
        if not has_emu:
            return missing(
                "未检测到安卓模拟器环境",
                ["如需使用：安装 MuMu/LDPlayer/BlueStacks 等模拟器",
                 "在模拟器内登录微信（封号风险自担）",
                 "开启 root 后用 A4 路径读 EnMicroMsg.db"],
                warning=BAN_WARNING)
        return ok("检测到模拟器环境；但模拟器登录微信 = 高封号风险，默认不启用",
                  devices=list(devices), warning=BAN_WARNING)

    def collect(self, **opts) -> List[Message]:
        """不自动登录、不自动跑采集。只返回引导文本（空消息列表 + 提示）。"""
        # 本通道故意不做任何自动化动作——登录必须用户手动在模拟器里完成
        logger.warning("A5 collect() 被调用；本通道不自动登录/采集。")
        return []
