# -*- coding: utf-8 -*-
"""O2 多端同登编排（不直接出明文）。

同一账号在 PC / 安卓真机 / 平板多端同时在线，选最易采集的端取明文：
root 真机(A4) > 旧版 PC(P1/P4) > 4.x PC(P1, 需key) > 平板。本通道自身：
  - produces_plaintext = False（collect() 恒返回 []）；
  - plan() 返回结构化步骤；
  - available() 真实探测本机微信进程 / adb 设备是否具备多端同登落点。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..base import (SourceAdapter, Message, Availability, RISK_LOW,
                    PLAT_OFFICIAL, ok, missing)
from .. import env

logger = logging.getLogger(__name__)


class O2MultiDeviceAdapter(SourceAdapter):
    id = "O2_multi_device"
    name = "多端同登，选最易采集端取数"
    platform = PLAT_OFFICIAL
    requires = frozenset({"device"})
    risk_level = RISK_LOW
    default_enabled = True
    local_runnable = False
    success_prior = 0.55
    produces_plaintext = False

    def available(self) -> Availability:
        procs = env.wechat_processes()
        devices = env.adb_devices()
        if procs or devices:
            return ok("检测到运行中的微信进程或安卓设备，可做多端同登取数",
                      processes=list(procs), devices=list(devices))
        return missing(
            "未检测到可同登的端",
            ["在本机登录微信（PC/4.x）",
             "或连接一台 root/已备份的安卓真机（adb devices 可见）",
             "手机与 PC/平板同账号登录后，在最易采集的端取数"])

    def plan(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": "同一账号多端同登，选最易采集的端取明文",
            "steps": [
                "1. 同一账号在 PC(4.x/3.x) + 安卓真机 + 平板 同时保持登录",
                "2. 评估各端可采集性：root真机(A4) > 旧版PC(P1/P4) > 4.x(P1需key) > 平板",
                "3. 选最易端采集，其余端仅作冗余备份",
                "4. 避免频繁切换设备登录，以防触发人工核实/风控",
            ],
            "risk_note": "低：仅官方多端同登，不注入/不 hook；控制登录切换频率",
            "target_ready": self.available().to_dict(),
        }

    def collect(self, **opts) -> List[Message]:
        # 编排通道不出明文：消息由所选最易端的采集通道产出
        return []
