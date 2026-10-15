# -*- coding: utf-8 -*-
"""O1 官方聊天记录迁移/备份编排（不直接出明文）。

职责：把记录从手机搬到一个"可采集端"（旧版 PC / root 真机 / 平板 / 模拟器），
再由 P1/P4/A4 等通道在该端取明文。本通道自身：
  - produces_plaintext = False（collect() 恒返回 []）；
  - plan() 返回结构化步骤；
  - available() 真实探测本机是否已具备可采集的数据目录作为迁移落点。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_OFFICIAL, ok, missing)
from .. import env

logger = logging.getLogger(__name__)


class O1OfficialMigrationAdapter(SourceAdapter):
    id = "O1_official_migration"
    name = "官方聊天记录迁移/备份到可采集端"
    platform = PLAT_OFFICIAL
    requires = frozenset({"device"})
    risk_level = RISK_NONE
    default_enabled = True
    local_runnable = False
    success_prior = 0.6
    produces_plaintext = False

    def available(self) -> Availability:
        roots = list(env.xwechat_roots()) + list(env.wechat_files_roots())
        if roots:
            return ok("本机已具备可采集的微信数据目录，迁移落点就绪",
                      targets=roots)
        return missing(
            "本机尚未发现可采集的微信数据目录",
            ["在本机登录一次微信（PC/4.x）以生成 db_storage",
             "手机端：我→设置→通用→聊天记录迁移与备份→迁移到本机/另一台设备",
             "迁移完成后在目标端登录一次，再由 P1(已知key)/P4(休眠)/A4(root) 采集"])

    def plan(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": "把官方聊天记录搬到一个可采集端（旧版PC / root真机 / 平板）",
            "steps": [
                "1. 手机微信：我→设置→通用→聊天记录迁移与备份→迁移到另一台设备",
                "2. 选择目标端：旧版3.x客户端 / 4.x PC / root安卓真机 / 平板",
                "3. 保持前台、不锁屏，等待迁移完成（大记录可能较久）",
                "4. 在目标端登录一次账号，生成 db_storage 数据目录",
                "5. 回到本工具：4.x 走 P1(已知key)，旧版走 P1/P4，root 真机走 A4",
            ],
            "risk_note": "零封号风险：官方迁移通道，仅搬运、不破解、不注入",
            "target_ready": self.available().to_dict(),
        }

    def collect(self, **opts) -> List[Message]:
        # 编排通道不出明文：消息由迁移落点上的采集通道产出
        return []
