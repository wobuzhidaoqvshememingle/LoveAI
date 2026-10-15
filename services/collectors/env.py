# -*- coding: utf-8 -*-
"""采集体系共享环境探测（全部为真实探测，结果进程内缓存）。

不硬编码任何"可用"。所有探测函数在失败/缺失时返回 False / 空值，
供各 SourceAdapter.available() 组合判断。
"""
from __future__ import annotations

import ctypes
import importlib.util
import os
import shutil
import sys
from functools import lru_cache
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def project_root() -> str:
    """仓库/安装根（services/collectors/env.py 上两级：collectors->services->根）。"""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def tools_dir() -> str:
    return os.path.join(project_root(), "tools")


def adb_path() -> str:
    """返回随包 adb 路径；不存在则回退 PATH 上的 adb；再无返回空串。"""
    bundled = os.path.join(tools_dir(), "platform-tools", "adb.exe")
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which("adb")
    return found or ""


def scrcpy_dir() -> str:
    """scrcpy 目录（随包或 tools/scrcpy）。"""
    cand = os.path.join(tools_dir(), "scrcpy")
    return cand if os.path.isdir(cand) else ""


# ---------------------------------------------------------------------------
# Python 模块可用性
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def has_module(name: str) -> bool:
    """真实判断某 Python 模块是否可导入。"""
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def ocr_available() -> bool:
    return has_module("rapidocr_onnxruntime") or has_module("rapidocr")


def vision_llm_available() -> bool:
    """是否具备可做视觉结构化的模型通道（本地优先，复用既有路由）。"""
    # 本地/已配置路由：free_model_router 或 llm_gateway 存在即具备编排可能
    return has_module("services.free_model_router") or has_module("services.llm_gateway")


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def is_admin() -> bool:
    """是否以管理员身份运行（真实探测）。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 驱动器 / 微信数据根
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def fixed_drives() -> tuple:
    """真实枚举本地固定驱动器号，如 ('C:\\','D:\\','E:\\')。"""
    drives = []
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask & (1 << i):
                letter = chr(65 + i) + ":\\"
                # 仅固定盘（DRIVE_FIXED=3）
                if ctypes.windll.kernel32.GetDriveTypeW(letter) == 3:
                    drives.append(letter)
    except Exception:
        # 兜底：扫常见盘符
        for letter in ("C:\\", "D:\\", "E:\\", "F:\\"):
            if os.path.isdir(letter):
                drives.append(letter)
    return tuple(drives)


@lru_cache(maxsize=1)
def xwechat_roots() -> tuple:
    """真实查找 4.x 数据根 xwechat_files（逐固定盘根探测 + APPDATA）。

    返回该目录下含 db_storage 的账号目录绝对路径元组。
    """
    candidates: List[str] = []
    for drv in fixed_drives():
        candidates.append(os.path.join(drv, "xwechat_files"))
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidates.append(os.path.join(appdata, "Tencent", "xwechat"))
    accounts: List[str] = []
    seen = set()
    for root in candidates:
        if not os.path.isdir(root):
            continue
        try:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if not os.path.isdir(p) or name in ("all_users",):
                    continue
                if os.path.isdir(os.path.join(p, "db_storage")) and p not in seen:
                    seen.add(p)
                    accounts.append(p)
        except Exception:
            continue
    return tuple(accounts)


@lru_cache(maxsize=1)
def wechat_files_roots() -> tuple:
    """真实查找旧版 3.x 'WeChat Files' 根（逐盘 + Documents）。"""
    candidates: List[str] = []
    for drv in fixed_drives():
        candidates.append(os.path.join(drv, "WeChat Files"))
    up = os.environ.get("USERPROFILE", "")
    if up:
        candidates.append(os.path.join(up, "Documents", "WeChat Files"))
    out = []
    for c in candidates:
        if os.path.isdir(c):
            out.append(c)
    return tuple(dict.fromkeys(out))


def find_message_dbs(account_root: str) -> List[str]:
    """列出某账号 db_storage/message 下 message_*.db（主库，非 media/biz/fts）。"""
    msg_dir = os.path.join(account_root, "db_storage", "message")
    if not os.path.isdir(msg_dir):
        return []
    out = []
    for fn in os.listdir(msg_dir):
        # message_0.db ... message_N.db，排除 -wal/-shm/material/kvdb
        if fn.startswith("message_") and fn.endswith(".db"):
            middle = fn[len("message_"):-len(".db")]
            if middle.isdigit():
                out.append(os.path.join(msg_dir, fn))
    return sorted(out)


# ---------------------------------------------------------------------------
# 微信进程 / 版本
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def wechat_processes() -> tuple:
    """真实枚举微信相关进程，返回 (name, pid, exe_path) 元组。"""
    try:
        import psutil
    except Exception:
        return tuple()
    target = {"wechatappex.exe", "weixin.exe", "wechat.exe"}
    out = []
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (p.info.get("name") or "").lower()
            if name in target:
                out.append((name, p.info.get("pid"), p.info.get("exe") or ""))
        except Exception:
            continue
    return tuple(out)


def wechat_version() -> Optional[str]:
    """从运行中微信 exe 的同级版本资源探测版本；探测不到返回 None。"""
    procs = wechat_processes()
    if not procs:
        return None
    try:
        exe = next((e for _n, _pid, e in procs if e), "")
        if not exe:
            return None
        # 4.x 版本目录通常在安装根 version 文件夹或 exe 的 FileVersion
        import win32api  # type: ignore
        info = win32api.GetFileVersionInfo(exe, "\\")
        ms = info["FileVersionMS"]
        ls = info["FileVersionLS"]
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ADB 设备
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def adb_devices() -> tuple:
    """真实跑 `adb devices`，返回设备序列号元组（空 = 无设备）。"""
    adb = adb_path()
    if not adb:
        return tuple()
    try:
        import subprocess
        r = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=15)
        out = []
        for line in r.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].strip() == "device":
                out.append(parts[0].strip())
        return tuple(out)
    except Exception:
        return tuple()


# ---------------------------------------------------------------------------
# 休眠 / 转储文件
# ---------------------------------------------------------------------------

def hiberfil_path() -> str:
    """返回 hiberfil.sys 路径（存在才返回，否则空串）。"""
    p = r"C:\hiberfil.sys"
    return p if os.path.isfile(p) else ""


def memory_dmp_locations() -> List[str]:
    """常见蓝屏/手动完整内存转储路径（存在才返回）。"""
    cands = [r"C:\Windows\MEMORY.DMP"]
    up = os.environ.get("USERPROFILE", "")
    if up:
        cands.append(os.path.join(up, "Documents", "MEMORY.DMP"))
    return [p for p in cands if os.path.isfile(p)]


def reset_caches() -> None:
    """清除本模块全部 lru_cache（测试/重探用）。"""
    for fn in (has_module, is_admin, fixed_drives, xwechat_roots,
               wechat_files_roots, wechat_processes, adb_devices):
        try:
            fn.cache_clear()
        except Exception:
            pass
