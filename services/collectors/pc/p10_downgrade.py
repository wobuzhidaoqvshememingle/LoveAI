# -*- coding: utf-8 -*-
"""P10 降级 PC 微信到可解密旧版 + 官方迁移灌入 + 旧工具解密（高级/脆弱，默认禁用）。

本通道【不自动执行降级】。available() 只做真实版本探测 + 给出操作引导；
collect() 不碰安装包/不卸载/不改系统。用户完成降级+官方迁移后，
以 key= 传入（或改走 P2/P3），本适配器再委托 P1 解密。
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Sequence

from ..base import (SourceAdapter, Message, Availability, RISK_MEDIUM,
                    PLAT_PC, ok, missing)
from .. import env

logger = logging.getLogger(__name__)


class P10DowngradeAdapter(SourceAdapter):
    id = "P10_downgrade"
    name = "降级旧版微信+官方迁移解密(高级脆弱)"
    platform = PLAT_PC
    requires = frozenset()
    risk_level = RISK_MEDIUM
    default_enabled = False            # 脆弱高级操作，默认关
    local_runnable = False
    success_prior = 0.3               # 历史成功率低、易触发风控
    produces_plaintext = True

    # ------------------------------------------------------------------
    def available(self) -> Availability:
        ver = env.wechat_version()
        reqs = [
            "获取 <4.1.10 的旧版 PC 微信安装包并安装（卸载新版前先备份 xwechat_files）",
            "登录旧版后用官方'聊天记录迁移与备份'把记录搬回本机",
            "旧版登录态下用 P2(进程内存) 取 key，或取到 key 后用 P1 解密",
            "完成降级+迁移后，可直接以 key= 传入本适配器复用 P1",
            "注意：降级脆弱、易触发风控与数据丢失，属高级操作，本通道不自动执行",
        ]
        return missing(
            f"降级方案需手动执行（当前探测微信版本={ver or '未知'}），默认禁用",
            reqs, current_version=ver, advanced=True, fragile=True)

    # ------------------------------------------------------------------
    def collect(self,
                key=None,
                account_root: Optional[str] = None,
                contact_filter: Optional[Sequence[str]] = None,
                **opts) -> List[Message]:
        if key is not None:
            from .p1_sqlcipher import P1SqlcipherDirectAdapter
            return P1SqlcipherDirectAdapter().collect(
                key=key, account_root=account_root, contact_filter=contact_filter)
        raise RuntimeError(
            "P10 不自动降级/不卸载/不改系统。请手动完成：安装 <4.1.10 旧版 -> "
            "官方迁移灌入记录 -> 用 P2 取 key 后以 key= 传入，或直接改走 P3/P1。")
