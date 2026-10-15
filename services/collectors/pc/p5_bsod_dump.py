# -*- coding: utf-8 -*-
"""P5 蓝屏完整内存转储取证（MEMORY.DMP -> oracle 扫 key -> 委托 P1 解密）。

合规边界（硬约束）：
  * 【强制蓝屏崩溃】属高风险、破坏性动作，本适配器【绝不自动执行】；只在
    available().requirements / 异常文案中给出引导，需用户显式确认后自行触发。
  * 适配器只对【已存在】的完整 .dmp 做离线只读扫描：collect(dump_path=...)。
  * risk=high，default_enabled=False；registry.run 未带 confirm_high_risk=True 时
    会自动拦截，无需本适配器自行实现。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence

from ..base import (SourceAdapter, Availability, RISK_HIGH,
                    PLAT_PC, ok, missing)
from .. import env
from .p4_hiberfil import scan_image_and_read

logger = logging.getLogger(__name__)


class P5BsodDumpAdapter(SourceAdapter):
    id = "P5_bsod_dump"
    name = "蓝屏完整内存转储(MEMORY.DMP)取证"
    platform = PLAT_PC
    requires = frozenset({"admin"})
    risk_level = RISK_HIGH
    default_enabled = False
    local_runnable = False
    success_prior = 0.5
    produces_plaintext = True

    def available(self) -> Availability:
        dumps = env.memory_dmp_locations()
        admin = env.is_admin()
        if not dumps:
            return missing(
                "未发现完整内存转储文件(MEMORY.DMP)",
                ["系统属性→高级→启动和故障恢复→设置→'写入调试信息'选择'完整内存转储'",
                 "以【管理员】身份运行本程序",
                 "手动触发一次系统蓝屏以生成 C:\\Windows\\MEMORY.DMP（强制蓝屏属高风险"
                 "破坏性动作，需你显式确认后自行操作，本适配器绝不自动触发）",
                 "或直接把已有完整转储通过 collect(dump_path=...) 喂入"])
        return ok("检测到已存在的完整内存转储", dumps=dumps, admin=admin)

    def collect(self,
                dump_path: Optional[str] = None,
                account_root: Optional[str] = None,
                step: int = 4,
                thorough: bool = True,
                contact_filter: Optional[Sequence[str]] = None,
                version: str = "4.x",
                **opts) -> List:
        if not dump_path:
            raise ValueError(
                "P5 需要 dump_path= 指向已存在的完整内存转储 .dmp；"
                "本适配器绝不自动触发蓝屏。")
        return scan_image_and_read(
            dump_path, account_root=account_root, step=step,
            thorough=thorough, contact_filter=contact_filter, version=version)
