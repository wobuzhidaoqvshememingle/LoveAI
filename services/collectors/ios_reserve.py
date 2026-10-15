# -*- coding: utf-8 -*-
"""iOS：本期仅预留 iTunes 备份解析接口，不实现。"""
from __future__ import annotations

from typing import List

from .base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_IOS, missing)


class IosReserveAdapter(SourceAdapter):
    id = "iOS_itunes_reserve"
    name = "iOS iTunes备份解析(预留)"
    platform = PLAT_IOS
    requires = frozenset({"backup"})
    risk_level = RISK_NONE
    default_enabled = False
    local_runnable = False
    success_prior = 0.3
    produces_plaintext = True

    def available(self) -> Availability:
        return missing(
            "iOS 通道本期未实现，仅预留接口",
            ["未来：提供 iTunes/访达加密备份(.mddata/.mdbackup)与备份口令",
             "将解析 Manifest.db -> SMS/Chat 表并映射为标准 Message"])

    def collect(self, **opts) -> List[Message]:
        return []
