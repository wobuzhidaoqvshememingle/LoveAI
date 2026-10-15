# -*- coding: utf-8 -*-
"""P9 Frida / inline hook 取 key 或明文查询（高风险，默认禁用）。

本适配器【绝不】在 collect 里自动注入或运行 wx_key.exe —— 注入/hook 有封号风险。
available() 只做**检测**：frida 模块、随包 tools/wx_key/wx_key.exe、管理员、微信进程。
真正取 key 需用户在充分知情后手动执行；拿到 32B key 后以 key= 传入，
本适配器再委托 P1 解密（解密段真实可跑）。

registry.run 会在没有 confirm_high_risk=True 时自动拦截本通道，无需自己实现门禁。
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional, Sequence

from ..base import (SourceAdapter, Message, Availability, RISK_HIGH,
                    PLAT_PC, ok, missing)
from .. import env

logger = logging.getLogger(__name__)


class P9FridaHookAdapter(SourceAdapter):
    id = "P9_frida_hook"
    name = "Frida/inline hook取key(高风险)"
    platform = PLAT_PC
    requires = frozenset({"admin"})
    risk_level = RISK_HIGH            # 注入/hook：封号高风险
    default_enabled = False
    local_runnable = False            # 真注入无法离线自测；只验证检测+门禁
    success_prior = 0.4
    produces_plaintext = True

    # ------------------------------------------------------------------
    def available(self) -> Availability:
        has_frida = env.has_module("frida") or env.has_module("frida_tools")
        wxkey = os.path.join(env.tools_dir(), "wx_key", "wx_key.exe")
        has_wxkey = os.path.isfile(wxkey)
        admin = env.is_admin()
        procs = env.wechat_processes()

        ready = (has_frida or has_wxkey) and admin and bool(procs)
        if ready:
            return ok(
                "检测到注入工具链与管理员+微信进程（高风险通道，默认禁用，需 confirm_high_risk）",
                frida=has_frida, wx_key=has_wxkey, admin=admin,
                wechat_running=bool(procs))

        reqs: List[str] = []
        if not (has_frida or has_wxkey):
            reqs.append("安装 frida(pip install frida frida-tools)，或放入 tools/wx_key/wx_key.exe")
        if not admin:
            reqs.append("以管理员身份运行（注入远程进程需要）")
        if not procs:
            reqs.append("启动并登录 PC 微信")
        reqs.append("研究向：inline hook 取密钥/明文查询有封号与风控风险，默认禁用；不自动注入")
        return missing(
            "注入/hook 工具链不完整，或非管理员/无运行中的微信进程",
            reqs, frida=has_frida, wx_key=has_wxkey, admin=admin,
            wechat_running=bool(procs))

    # ------------------------------------------------------------------
    def collect(self,
                key=None,
                account_root: Optional[str] = None,
                contact_filter: Optional[Sequence[str]] = None,
                do_inject: bool = False,
                **opts) -> List[Message]:
        # 已由 hook 侧拿到 key -> 走真实解密（复用 P1）
        if key is not None:
            from .p1_sqlcipher import P1SqlcipherDirectAdapter
            return P1SqlcipherDirectAdapter().collect(
                key=key, account_root=account_root, contact_filter=contact_filter)

        # 否则：绝不自动注入 / 运行 wx_key.exe。明确报错，把球交回用户。
        raise RuntimeError(
            "P9 为注入/hook 高风险通道，本适配器不自动执行注入（避免封号）。"
            "请在充分知情后，用 frida 或 tools/wx_key/wx_key.exe 在进程外取出 32 字节 key，"
            "再以 key= 传入本适配器（解密段复用 P1，真实可跑）。"
            "do_inject=True 仅占位，本版本不落地自动注入逻辑。")
