# -*- coding: utf-8 -*-
"""P2 旧版微信(<4.1.10)进程内存只读提取 key（PyWxDump 式 ReadProcessMemory）。

只读、不注入。新版微信(>=4.1.10)已把常驻密钥打散，纯内存扫描失效，
此时 available() 直接 missing 并引导改用 P3(MiniDump oracle) / P9(hook)。

本适配器不重写扫描算法：extract_key() 直接包装 services.wechat_key_extractor
（复用其 MemoryReader / 候选枚举 / HMAC 验密）。取到 key 后委托 P1 读标准消息。
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Sequence, Tuple

from ..base import (SourceAdapter, Message, Availability, RISK_MEDIUM,
                    PLAT_PC, ok, missing)
from .. import env

logger = logging.getLogger(__name__)

# 纯内存扫描失效的版本阈值（>= 该版本即失效）
_DEAD_VERSION = (4, 1, 10)


def _parse_ver(ver: str) -> Tuple[int, ...]:
    """把 'a.b.c.d' 解析成可比较的整数元组；非法段按 0。"""
    parts = []
    for seg in (ver or "").strip().split("."):
        num = ""
        for ch in seg:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    return tuple(parts) if parts else (0,)


def _ver_ge(ver: str, target: Tuple[int, ...]) -> bool:
    v = _parse_ver(ver)
    # 补齐到 target 长度再逐段比
    n = max(len(v), len(target))
    v = v + (0,) * (n - len(v))
    t = target + (0,) * (n - len(target))
    return v >= t


class P2MemoryKeyAdapter(SourceAdapter):
    id = "P2_memory_key"
    name = "旧版微信进程内存只读取key"
    platform = PLAT_PC
    requires = frozenset({"admin"})
    risk_level = RISK_MEDIUM          # 跨进程只读内存，低频但属主动读取
    default_enabled = True
    local_runnable = False            # 依赖真实运行中的旧版微信进程，无法离线 fixture
    success_prior = 0.55
    produces_plaintext = True

    # ------------------------------------------------------------------
    def available(self) -> Availability:
        procs = env.wechat_processes()
        if not procs:
            return missing(
                "未检测到运行中的微信进程",
                ["先启动并登录 PC 微信（需 <4.1.10 的旧版）",
                 "新版(>=4.1.10)纯内存扫描已失效，请改用 P3_minidump_oracle / P9_frida_hook"])
        ver = env.wechat_version()
        if not ver:
            return missing(
                "检测到微信进程，但无法读取其版本资源",
                ["以管理员权限运行以便读取进程版本信息",
                 "确认本机微信为 <4.1.10 旧版；新版纯内存扫描已失效，请改用 P3/P9"])
        if _ver_ge(ver, _DEAD_VERSION):
            return missing(
                f"检测到微信 {ver}，新版(>=4.1.10)纯内存扫描已失效，请改用 P3/P9",
                ["改用 P3_minidump_oracle（MiniDump + oracle 扫描，低风险离线）",
                 "或 P9_frida_hook（inline hook，高风险默认禁用）"])
        return ok(
            f"检测到旧版微信 {ver}(<4.1.10)，可进程内存只读提取 key",
            version=ver, procs=[p[0] for p in procs])

    # ------------------------------------------------------------------
    def extract_key(self,
                    account_root: Optional[str] = None,
                    version: str = "4.x",
                    timeout: float = 30.0,
                    **opts) -> str:
        """包装 services.wechat_key_extractor：复用 MemoryReader/候选/验密，不重写。

        Returns: 64 hex key 字符串。失败抛 RuntimeError（不静默返回空）。
        """
        from services import wechat_key_extractor as kx

        account_root = account_root or (
            env.xwechat_roots()[0] if env.xwechat_roots() else None)
        if not account_root or not os.path.isdir(account_root):
            raise FileNotFoundError(f"账号目录不存在: {account_root}")
        msg_dbs = env.find_message_dbs(account_root)
        if not msg_dbs:
            raise FileNotFoundError(f"账号目录下未发现 message_*.db: {account_root}")

        res = kx.extract_key_auto(
            db_path=msg_dbs[0], version=version,
            enable_high_risk=False,           # 方法4(DLL注入模拟)永不开启
            timeout_per_method=timeout)
        if not res.get("ok") or not res.get("key"):
            log = res.get("log") or []
            raise RuntimeError(
                "进程内存扫描未取到有效密钥（新版微信内存扫描通常已失效，"
                f"建议改用 P3）。明细: {log}")
        return res["key"]

    # ------------------------------------------------------------------
    def collect(self,
                account_root: Optional[str] = None,
                version: str = "4.x",
                contact_filter: Optional[Sequence[str]] = None,
                **opts) -> List[Message]:
        key = self.extract_key(account_root=account_root, version=version, **opts)
        from .p1_sqlcipher import P1SqlcipherDirectAdapter
        return P1SqlcipherDirectAdapter().collect(
            key=key, account_root=account_root, version=version,
            contact_filter=contact_filter)
