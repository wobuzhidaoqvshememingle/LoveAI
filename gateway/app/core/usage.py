# -*- coding: utf-8 -*-
"""
用量窗口检查。

从 db.window_usage() 取最近 60s / 当天数据, 跟 ProviderConfig 的
rpm_limit / tpm_limit / rpd_limit / tpd_limit 对比; 任一超限就视为
该 provider 当前不可用, 路由时跳过。

limit=0 表示不限。
"""
from __future__ import annotations

from typing import Optional

from app.config import GatewayConfig, ProviderConfig
from app.db import Database


class UsageTracker:
    def __init__(self, db: Database, cfg: GatewayConfig) -> None:
        self.db = db
        self.cfg = cfg

    # ------------------------------------------------------------------ #
    def _limits(self, provider_name: str) -> Optional[ProviderConfig]:
        return self.cfg.provider_by_name(provider_name)

    def check(self, provider_name: str) -> tuple[bool, dict]:
        """返回 (是否可用, 当前用量)。"""
        pcfg = self._limits(provider_name)
        if pcfg is None:
            return False, {}
        if not pcfg.enabled:
            return False, {}

        usage = self.db.window_usage(provider_name)

        over = []
        if pcfg.rpm_limit > 0 and usage["rpm"] >= pcfg.rpm_limit:
            over.append(f"rpm {usage['rpm']}/{pcfg.rpm_limit}")
        if pcfg.tpm_limit > 0 and usage["tpm"] >= pcfg.tpm_limit:
            over.append(f"tpm {usage['tpm']}/{pcfg.tpm_limit}")
        if pcfg.rpd_limit > 0 and usage["rpd"] >= pcfg.rpd_limit:
            over.append(f"rpd {usage['rpd']}/{pcfg.rpd_limit}")
        if pcfg.tpd_limit > 0 and usage["tpd"] >= pcfg.tpd_limit:
            over.append(f"tpd {usage['tpd']}/{pcfg.tpd_limit}")

        usage["_over"] = over
        usage["_limits"] = {
            "rpm": pcfg.rpm_limit,
            "tpm": pcfg.tpm_limit,
            "rpd": pcfg.rpd_limit,
            "tpd": pcfg.tpd_limit,
        }
        return (len(over) == 0), usage

    def is_within_limits(self, provider_name: str) -> bool:
        ok, _ = self.check(provider_name)
        return ok
