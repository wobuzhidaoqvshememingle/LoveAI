# -*- coding: utf-8 -*-
"""P4 休眠全内存镜像取证（hiberfil.sys -> raw 内存镜像 -> oracle 扫 key -> 委托 P1 解密）。

两种采集入口：
  * collect(raw_image_path=...)：直接对【已转换好的 raw 内存镜像】分块扫描 key，
    命中后委托 P1SqlcipherDirectAdapter 读消息。这是纯离线只读路径，risk=none。
  * collect(hiberfil_path=...)：仅当本机探测到 hiberfil->raw 转换器（Volatility3 /
    Hibr2DMD）时才执行转换；否则抛带引导的异常，由 available() 给出可执行引导。

合规边界（硬约束）：
  * 读取既有镜像/转储本身是离线只读，不触发任何系统动作；
  * 【开启休眠 / 触发休眠】属高风险动作，本适配器【绝不自动执行】，只在
    available().requirements 与异常文案中给出引导，由用户自行确认后手动操作。
"""
from __future__ import annotations

import logging
import os
import shutil
from typing import List, Optional, Sequence

from ..base import (SourceAdapter, Message, Availability, RISK_NONE,
                    PLAT_PC, ok, missing)
from .. import env

logger = logging.getLogger(__name__)

# 逐字节(step=1)兜底扫描只对小镜像自动启用：真实微信 key 在进程堆里通常 16 字节
# 对齐，step=4 即可命中；step=1 慢一个量级，大镜像上不自动跑，需要时由调用方显式传 step=1。
_THOROUGH_MAX_BYTES = 1024 * 1024 * 1024  # 1 GiB


# ---------------------------------------------------------------------------
# 共享：对内存镜像扫 key -> 委托 P1 解密（P4/P5 复用）
# ---------------------------------------------------------------------------
def _find_message_db(account_root: str) -> str:
    dbs = env.find_message_dbs(account_root)
    if not dbs:
        raise FileNotFoundError(f"账号目录下未发现 message_*.db: {account_root}")
    return dbs[0]


def _resolve_account_root(account_root: Optional[str]) -> str:
    account_root = account_root or (env.xwechat_roots()[0] if env.xwechat_roots() else None)
    if not account_root or not os.path.isdir(account_root):
        raise FileNotFoundError(f"账号目录不存在: {account_root}")
    return account_root


def scan_image_and_read(image_path: str,
                        account_root: Optional[str] = None,
                        step: int = 4,
                        thorough: bool = True,
                        contact_filter: Optional[Sequence[str]] = None,
                        version: str = "4.x") -> List[Message]:
    """对一个 raw 内存镜像 / .dmp 分块扫 key，命中后委托 P1 读消息。

    step=4 对齐扫描（真实 HeapAlloc key 16 字节对齐，快）；未命中且镜像不太大时，
    自动做一次 step=1 逐字节兜底（覆盖非对齐排布，fixture 即此情形）。
    """
    from .. import oracle
    from .p1_sqlcipher import P1SqlcipherDirectAdapter

    account_root = _resolve_account_root(account_root)
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"内存镜像不存在: {image_path}")
    db_path = _find_message_db(account_root)

    key = oracle.scan_file_for_key(image_path, db_path, step=step)
    if key is None and thorough:
        size = os.path.getsize(image_path)
        if size <= _THOROUGH_MAX_BYTES:
            logger.warning("对齐扫描(step=%d)未命中，改做逐字节兜底扫描(step=1)：%s",
                           step, image_path)
            key = oracle.scan_file_for_key(image_path, db_path, step=1)
        else:
            logger.warning("对齐扫描未命中且镜像较大(%d bytes)，跳过自动逐字节扫描；"
                           "如需逐字节穷举请显式传 step=1", size)

    if key is None:
        logger.warning("镜像 %s 中未找到可解 %s 的 key", image_path, db_path)
        return []

    logger.info("从内存镜像命中 key(%d bytes)，委托 P1 解密 %s", len(key), account_root)
    p1 = P1SqlcipherDirectAdapter()
    return p1.collect(key=key, account_root=account_root, version=version,
                      contact_filter=list(contact_filter) if contact_filter else None)


# ---------------------------------------------------------------------------
# hiberfil -> raw 转换器探测 / 转换（仅探测到转换器才会真正执行）
# ---------------------------------------------------------------------------
def detect_converter() -> Optional[dict]:
    """真实探测 hiberfil->raw 转换器：PATH 上的 volatility 系列，或随包 tools/。

    返回 {"name":..., "path":...}；未安装返回 None。
    """
    for name in ("vol", "vol.py", "vol3", "volatility3", "Hibr2DMD"):
        found = shutil.which(name)
        if found:
            return {"name": name, "path": found}
    td = env.tools_dir()
    for fn in ("Hibr2DMD.exe", "volatility3.exe", "vol.exe", "vol.py"):
        cand = os.path.join(td, fn)
        if os.path.isfile(cand):
            return {"name": fn, "path": cand}
    return None


def convert_hiberfil(hiberfil_path: str, converter: dict) -> str:
    """把 hiberfil.sys 转成 raw 内存镜像，返回临时 raw 路径（调用方负责清理）。

    仅在 detect_converter() 命中时被调用；本函数【不】触发休眠/开启休眠。
    """
    import subprocess
    import tempfile

    out = tempfile.NamedTemporaryFile(prefix="p4_hiber2raw_", suffix=".img",
                                      delete=False).name
    name = converter["name"].lower()
    if name.startswith("vol"):
        # Volatility3 可直接打开 hiberfil 并 dump 出物理内存映射
        cmd = [converter["path"], "-f", hiberfil_path, "-q",
               "-o", os.path.dirname(out), "windows.memmap.Memmap"]
    else:
        # Hibr2DMD：hiberfil.sys -> crash-dump / raw
        cmd = [converter["path"], hiberfil_path, out]
    logger.info("运行 hiberfil->raw 转换: %s", cmd)
    subprocess.run(cmd, check=True, timeout=3600)
    return out


# ---------------------------------------------------------------------------
# 适配器
# ---------------------------------------------------------------------------
class P4HiberfilAdapter(SourceAdapter):
    id = "P4_hiberfil"
    name = "休眠全内存镜像取证(hiberfil.sys)"
    platform = PLAT_PC
    requires = frozenset()
    risk_level = RISK_NONE
    default_enabled = True
    local_runnable = True
    success_prior = 0.6
    produces_plaintext = True

    def available(self) -> Availability:
        h = env.hiberfil_path()
        if not h:
            return missing(
                "未发现休眠文件 C:\\hiberfil.sys",
                ["以【管理员】开启休眠: powercfg /hibernate on",
                 "触发一次休眠并正常唤醒以生成 hiberfil.sys（注意：触发休眠会中断当前"
                 "会话，属高风险动作，需你自行确认后手动执行，本适配器绝不自动触发）",
                 "或直接把已转换好的 raw 内存镜像通过 collect(raw_image_path=...) 喂入"])
        conv = detect_converter()
        if not conv:
            return missing(
                f"检测到 hiberfil.sys({h})，但未发现 hiberfil->raw 内存转换器",
                ["安装 Volatility3: pip install volatility3，或 Hibr2DMD",
                 "或先用上述工具把 hiberfil.sys 转成 raw 镜像，再 collect(raw_image_path=...) 喂入"])
        return ok(f"检测到 hiberfil.sys 与转换器 {conv['name']}",
                  hiberfil=h, converter=conv["name"], needs_account=True)

    def collect(self,
                raw_image_path: Optional[str] = None,
                hiberfil_path: Optional[str] = None,
                account_root: Optional[str] = None,
                step: int = 4,
                thorough: bool = True,
                contact_filter: Optional[Sequence[str]] = None,
                version: str = "4.x",
                **opts) -> List[Message]:
        # 入口1：已转换好的 raw 镜像，直接扫（纯离线只读）
        if raw_image_path:
            return scan_image_and_read(
                raw_image_path, account_root=account_root, step=step,
                thorough=thorough, contact_filter=contact_filter, version=version)

        # 入口2：hiberfil.sys，需先转换
        hiberfil_path = hiberfil_path or env.hiberfil_path()
        if not hiberfil_path or not os.path.isfile(hiberfil_path):
            raise FileNotFoundError(f"未提供/不存在 hiberfil.sys: {hiberfil_path}")
        conv = detect_converter()
        if not conv:
            raise RuntimeError(
                "未检测到 hiberfil->raw 转换器：请安装 Volatility3/Hibr2DMD，"
                "或先把 hiberfil.sys 转成 raw 镜像后用 collect(raw_image_path=...) 喂入。"
                "（本适配器绝不自动开启休眠/触发休眠）")
        raw_path = convert_hiberfil(hiberfil_path, conv)
        try:
            return scan_image_and_read(
                raw_path, account_root=account_root, step=step,
                thorough=thorough, contact_filter=contact_filter, version=version)
        finally:
            try:
                os.remove(raw_path)
            except OSError:
                pass
