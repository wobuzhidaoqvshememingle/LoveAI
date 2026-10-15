# -*- coding: utf-8 -*-
"""P3 全进程 MiniDump + oracle 直接扫描解 message_0.db。

低风险/离线：dump 一旦拿到，之后全在本地对 .dmp 文件做只读字节扫描，
不触网、不注入、不触碰运行中进程（生成 dump 才需管理员+SeDebug，
能力引用现成 tools/wechat_forensics/dump_wechat.py）。

取 key：services.collectors.oracle.scan_file_for_key(dump, 首个message_db)。
取到 key 后委托 P1SqlcipherDirectAdapter 产出标准消息。
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Sequence

from ..base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_PC, ok, missing)
from .. import env

logger = logging.getLogger(__name__)


class P3MiniDumpOracleAdapter(SourceAdapter):
    id = "P3_minidump_oracle"
    name = "MiniDump转储+oracle扫描取key"
    platform = PLAT_PC
    requires = frozenset({"key"})
    risk_level = RISK_NONE           # 扫描离线只读；生成dump的步骤引导用户自行执行
    default_enabled = True
    local_runnable = True            # 可用 fixtures/forensics/*.dmp 离线自测
    success_prior = 0.7
    produces_plaintext = True

    # ------------------------------------------------------------------
    def available(self) -> Availability:
        tool = os.path.join(env.tools_dir(), "wechat_forensics", "dump_wechat.py")
        has_tool = os.path.isfile(tool)
        admin = env.is_admin()
        procs = env.wechat_processes()

        # 路径A：本机已是管理员且有微信进程 -> 可现场生成 dump
        if admin and procs:
            return ok(
                "管理员权限且检测到微信进程，可生成 MiniDump；亦可用 dump_path 指定已有转储",
                can_generate=True, has_tool=has_tool)

        # 路径B：无法现场生成，但用户可自备 dump_path（离线扫描零风险）
        reqs: List[str] = []
        if not admin:
            reqs.append("以管理员身份运行（生成转储需要 SeDebug 权限）")
        if not procs:
            reqs.append("先启动并登录 PC 微信")
        if has_tool:
            reqs.append(f"用 {tool} --now 生成全进程 MiniDump，再以 dump_path= 传入")
        else:
            reqs.append("自备全进程 MiniDump(.dmp) 后以 dump_path= 传入（离线 oracle 扫描，零风险）")
        return missing(
            "当前环境无法现场生成 MiniDump（需管理员+微信进程）；可直接提供已有转储 dump_path",
            reqs, has_tool=has_tool, admin=admin, wechat_running=bool(procs))

    # ------------------------------------------------------------------
    def collect(self,
                dump_path: Optional[str] = None,
                account_root: Optional[str] = None,
                step: int = 1,
                chunk_size: int = 128 * 1024,
                max_bytes: Optional[int] = None,
                **opts) -> List[Message]:
        if not dump_path or not os.path.isfile(dump_path):
            raise FileNotFoundError(f"需提供存在的转储文件 dump_path: {dump_path}")
        account_root = account_root or (
            env.xwechat_roots()[0] if env.xwechat_roots() else None)
        if not account_root or not os.path.isdir(account_root):
            raise FileNotFoundError(f"账号目录不存在: {account_root}")
        msg_dbs = env.find_message_dbs(account_root)
        if not msg_dbs:
            raise FileNotFoundError(f"账号目录下未发现 message_*.db: {account_root}")

        from .. import oracle
        logger.info("P3 扫描转储 %s -> %s", dump_path, msg_dbs[0])
        key = oracle.scan_file_for_key(
            dump_path, msg_dbs[0],
            chunk_size=chunk_size, overlap=64, step=step, max_bytes=max_bytes)
        if not key:
            # 不得假阳性：扫不到就明确失败
            raise RuntimeError(
                "转储中未扫描到可通过 HMAC 校验的密钥：该 dump 不含此账号密钥，"
                "或版本/账号不匹配")

        from .p1_sqlcipher import P1SqlcipherDirectAdapter
        return P1SqlcipherDirectAdapter().collect(
            key=key.hex(), account_root=account_root)
