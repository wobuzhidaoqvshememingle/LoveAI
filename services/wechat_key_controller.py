# -*- coding: utf-8 -*-
"""微信密钥「一键获取」控制器 —— 路线 C：Python ctypes 直调控制器 DLL。

本模块落地《微信密钥获取-DLL 控制器法-移植说明书》的第六节（微信生命周期编排）
与第七节（ctypes 调用骨架），把「检测微信 → 关微信 → 重启微信 → 前台化露二维码
→ 扫码/代点登录 → 注入 → 轮询回收密钥 → 落加密缓存」这一段接成一条线，
下游的「解密 → 入库」仍复用既有的 wechat_db_decryptor / wechat_oneclick.collect_messages。

────────────────────────────────────────────────────────────────────────────
红线（写进代码，不靠口头约定）
────────────────────────────────────────────────────────────────────────────
1. 注入类动作（InitializeHook / 远程 Shellcode）**只在 consent_injection=True 时执行**，
   且调用方（UI）必须已经弹过一次显式知情同意。未给同意时本模块不 kill、不 inject、
   不 attach 微信进程，只做只读探测，并返回人话原因 + next_action。
2. 微信数据库文件全程只读：本模块只读安装目录/进程/库文件头，绝不写微信数据目录。
3. 密钥不明文落盘：拿到 64 位 hex 后立即交给 wechat_key_extractor.import_key_plain()
   写入机器指纹 AES 加密缓存；本模块的日志/返回值只给掩码，不给明文。
4. 退出前必须 CleanupHook()：否则残留在微信进程里的 Shellcode 会让微信后续崩溃。
   注意：有干净卸载时「抓完微信必崩」**不是必然**；异常退出才可能需要重启微信。

避坑（说明书第八节，逐条落实）
- PollKeyData 缓冲区给 128 字节（至少 65），不是 64。
- 同一个微信进程只能 Hook 一次；重来必须先 CleanupHook 再 InitializeHook。
- 轮询放后台线程、约 100ms 一次，不在调用线程里死循环。
- GetStatusMessage 要循环取直到返回 false（一次可能积压多条）。
- DLL 所在路径不能含中文；含中文时自动复制到 ASCII 临时目录再加载。
- 仅 x64：DLL machine 必须是 0x8664。
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from ctypes import wintypes
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 常量：DLL 身份 / 特征码表 / 阶段
# ---------------------------------------------------------------------------

DLL_PRIMARY = os.path.join(PROJECT_ROOT, "tools", "wx_key", "wx_key.dll")
DLL_FALLBACK = os.path.join(os.path.dirname(PROJECT_ROOT), "wx_key_src", "wx_key-main",
                            "assets", "dll", "wx_key.dll")
# 启动期自检用：期望的导出名与 sha256（本机实测值；被换掉即报错，不静默用错 DLL）
DLL_EXPECTED_EXPORTS = ("InitializeHook", "PollKeyData", "GetStatusMessage",
                        "CleanupHook", "GetLastErrorMsg")
DLL_EXPECTED_SHA256 = ("482f02ac9fbdf9c39fccb954f48c96e3"
                       "2476281f74cea2528ab0fb7e142f1b1d")

# 特征码表：与 wx_key-main/wx_key/src/remote_scanner.cpp 的 VersionConfigManager 一字不差。
# 用途有二：① 按版本预测 DLL 会选哪一档（signature_branch）；② 静态体检该档特征码
# 是否真的命中本机 Weixin.dll（available 的可证伪依据，而不是「应该可以」）。
SIG_CONFIGS: List[Dict[str, Any]] = [
    {
        "label": ">=4.1.4",
        "pattern": bytes([0x24, 0x08, 0x48, 0x89, 0x6c, 0x24, 0x10, 0x48, 0x89,
                          0x74, 0x00, 0x18, 0x48, 0x89, 0x7c, 0x00, 0x20, 0x41,
                          0x56, 0x48, 0x83, 0xec, 0x50, 0x41]),
        "mask": "xxxxxxxxxx?xxxx?xxxxxxxx",
        "delta": -3,
    },
    {
        "label": "<4.1.4",
        "pattern": bytes([0x24, 0x50, 0x48, 0xc7, 0x45, 0x00, 0xfe, 0xff, 0xff,
                          0xff, 0x44, 0x89, 0xcf, 0x44, 0x89, 0xc3, 0x49, 0x89,
                          0xd6, 0x48, 0x89, 0xce, 0x48, 0x89]),
        "mask": "xxxxxxxxxxxxxxxxxxxxxxxx",
        "delta": -0xF,
    },
]
VERSION_BASELINE_414 = (4, 1, 4, 0)

# ---------------------------------------------------------------------------
# signature_branch 取值口径（**唯一权威说明**，前后端都看这里）
# ---------------------------------------------------------------------------
# 冻结给前端的契约取值为：`ge_4.1.4` / `lt_4.1.4` / `unknown`。
# 历史实现曾直接回传 SIG_CONFIGS 的 label（`>=4.1.4` / `<4.1.4`），前端已做双写法兼容。
# 为避免以后有人按老口径写新代码，这里明确：
#   * 契约字段 `signature_branch` **统一回传契约写法**（ge_4.1.4 / lt_4.1.4 / unknown）；
#   * 同时额外回传 `signature_branch_aliases`（含两种写法）与 `signature_branch_label`
#     （原始 label），任何按老口径取值的代码都能拿到值，不会静默失效。
SIG_BRANCH_GE = "ge_4.1.4"
SIG_BRANCH_LT = "lt_4.1.4"
SIG_BRANCH_UNKNOWN = "unknown"
SIG_BRANCH_CANONICAL = (SIG_BRANCH_GE, SIG_BRANCH_LT, SIG_BRANCH_UNKNOWN)
# 两套写法 <-> 契约写法的双向映射
SIG_BRANCH_ALIASES: Dict[str, str] = {
    "ge_4.1.4": SIG_BRANCH_GE, ">=4.1.4": SIG_BRANCH_GE,
    "ge_4.1.4.0": SIG_BRANCH_GE, ">=4.1.4.0": SIG_BRANCH_GE,
    "lt_4.1.4": SIG_BRANCH_LT, "<4.1.4": SIG_BRANCH_LT,
    "lt_4.1.4.0": SIG_BRANCH_LT, "<4.1.4.0": SIG_BRANCH_LT,
    "unknown": SIG_BRANCH_UNKNOWN, "": SIG_BRANCH_UNKNOWN,
}


def canonical_branch(label: Any) -> str:
    """把任意写法的档位标识归一成契约写法（`ge_4.1.4`/`lt_4.1.4`/`unknown`）。"""
    if label is None:
        return SIG_BRANCH_UNKNOWN
    return SIG_BRANCH_ALIASES.get(str(label).strip().lower(), SIG_BRANCH_UNKNOWN)


def branch_aliases(label: Any) -> List[str]:
    """同一档位的全部可接受写法（合同写法 + 历史写法），供双写法消费者查表。"""
    canon = canonical_branch(label)
    out = [canon]
    for k, v in SIG_BRANCH_ALIASES.items():
        if v == canon and k and k not in out:
            out.append(k)
    return out

STAGES: Tuple[Tuple[str, str], ...] = (
    ("detect", "检测微信与版本"),
    ("kill", "关闭微信"),
    ("launch", "启动微信"),
    ("foreground", "前台化并露出二维码"),
    ("wait_login", "等待扫码登录"),
    ("inject", "注入控制器 DLL"),
    ("poll_key", "轮询回收密钥"),
    ("persist", "写入加密缓存"),
    ("cleanup", "卸载 Hook 并收尾"),
)
STAGE_PERCENT = {"detect": 5, "kill": 15, "launch": 25, "foreground": 35,
                 "wait_login": 50, "inject": 65, "poll_key": 80,
                 "persist": 92, "cleanup": 100}
STAGE_NAMES = dict(STAGES)

WIN_TITLE_MARKERS = ("微信", "weixin", "wechat")
# 等微信界面出现的默认上限：实测冷启动可达 60s+，30s 会误判，取 120s。
WINDOW_WAIT_SECONDS = 120.0
READY_TEXT_MARKERS = ("聊天", "登录", "账号")
LOGIN_TEXT_MARKERS = ("登录", "扫码", "二维码")
READY_CLASS_MARKERS = ("WeChat", "Weixin", "TXGuiFoundation", "Qt5", "ChatList",
                       "MainWnd", "BrowserWnd", "ListView", "Qt", "MMUI")
READY_CHILD_COUNT = 14


# ---------------------------------------------------------------------------
# 小工具：文件版本 / PE 导出表
# ---------------------------------------------------------------------------

def _read_file_version(path: str) -> str:
    """读 PE 文件 FileVersion（如 4.1.15.13）。失败返回 ""。只读。"""
    if not path or not os.path.exists(path):
        return ""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size, buf):
            return ""
        rp = ctypes.c_void_p()
        ln = wintypes.UINT()
        if not ver.VerQueryValueW(buf, ctypes.c_wchar_p("\\"),
                                  ctypes.byref(rp), ctypes.byref(ln)):
            return ""
        ffi = ctypes.cast(rp, ctypes.POINTER(ctypes.c_uint32))
        ms, ls = ffi[2], ffi[3]
        if ms == 0 and ls == 0:
            return ""
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception as e:  # noqa: BLE001
        logger.debug("读文件版本失败 %s: %s", path, e)
        return ""


def pe_machine_and_exports(path: str) -> Dict[str, Any]:
    """极简 PE 解析：machine 类型 + 导出函数名列表。只读文件。"""
    out: Dict[str, Any] = {"ok": False, "machine": 0, "is_x64": False,
                           "exports": [], "reason": ""}
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 0x200 or data[:2] != b"MZ":
            out["reason"] = "不是 MZ/PE 文件"
            return out
        import struct
        e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
            out["reason"] = "PE 签名缺失"
            return out
        coff = e_lfanew + 4
        machine = struct.unpack_from("<H", data, coff)[0]
        nsec = struct.unpack_from("<H", data, coff + 2)[0]
        opt_size = struct.unpack_from("<H", data, coff + 16)[0]
        magic = struct.unpack_from("<H", data, coff + 20)[0]
        out["machine"] = machine
        out["is_x64"] = (machine == 0x8664)
        if magic != 0x20B:                      # PE32+
            out["reason"] = "不是 PE32+（x64）"
            return out
        # 数据目录第 0 项 = 导出表
        opt = coff + 20
        dd_off = opt + (0x70 if magic == 0x20B else 0x60)
        exp_rva, exp_size = struct.unpack_from("<II", data, dd_off)
        if not exp_rva or not exp_size:
            out["reason"] = "无导出表"
            return out
        # RVA -> 文件偏移
        sec_off = coff + 20 + opt_size
        secs = []
        for i in range(nsec):
            o = sec_off + i * 40
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, o + 8)
            secs.append((vaddr, vsize, rawptr, rawsize))

        def rva2off(rva: int) -> Optional[int]:
            for vaddr, vsize, rawptr, rawsize in secs:
                if vaddr <= rva < vaddr + max(vsize, rawsize):
                    return rawptr + (rva - vaddr)
            return None

        eo = rva2off(exp_rva)
        if eo is None:
            out["reason"] = "导出表 RVA 无法换算"
            return out
        n_names = struct.unpack_from("<I", data, eo + 24)[0]
        names_rva = struct.unpack_from("<I", data, eo + 32)[0]
        no = rva2off(names_rva)
        if no is None:
            out["reason"] = "导出名数组 RVA 无法换算"
            return out
        names: List[str] = []
        for i in range(min(n_names, 512)):
            nrva = struct.unpack_from("<I", data, no + i * 4)[0]
            so = rva2off(nrva)
            if so is None:
                continue
            end = data.find(b"\0", so)
            names.append(data[so:end].decode("ascii", "ignore"))
        out["exports"] = names
        out["ok"] = True
        return out
    except Exception as e:  # noqa: BLE001
        out["reason"] = "%s: %s" % (type(e).__name__, e)
        return out


def sha256_of_file(path: str) -> str:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# 版本判定（注册表 Version 只当原始证据，绝不用来定版本）
# ---------------------------------------------------------------------------

def parse_version(s: str) -> Optional[Tuple[int, int, int, int]]:
    m = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)\.(\d+)\s*$", s or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def pick_sig_config(version: str) -> Optional[Dict[str, Any]]:
    """按 DLL 内部 GetConfigForVersion 的同一套规则挑档（>=4.1.4 → configs[0]）。"""
    v = parse_version(version)
    if not v:
        return None
    if v >= VERSION_BASELINE_414:
        return SIG_CONFIGS[0]
    if v[0] == 4 and ((v[1] == 1 and v[2] < 4) or v[1] == 0):
        return SIG_CONFIGS[1]
    return None


def _pe_image_complete(path: str) -> Tuple[bool, str]:
    """PE 完整性体检：文件是否比节表声明的映像还短。

    为什么必须有（实测根因）：`scan_signature` 只按字节内容找特征码，**无法从内容判断
    文件有没有被截断**——一个被截断的模块会「扫完且 0 命中」，被读成「该版本不支持」，
    这就是「体检失败」被误当「特征码不存在」的另一条通路（与 MemoryError 同类）。
    判据用 PE 节表自洽：`file_size + 4096 >= max(RawPtr + RawSize)`。
    （本机真实 `F:\\Weixin\\4.1.15.13\\Weixin.dll` 实测：文件 201,552,944 B、
    节表声明映像 201,542,144 B，判据成立。）
    任何解析失败都算「不能证明完整」→ 结论未定。宁可说「未知」，不可误判「不支持」。
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(4096)
            if len(head) < 64 or head[:2] != b"MZ":
                return False, "缺 MZ 头"
            e = struct.unpack_from("<I", head, 0x3C)[0]
            if e + 24 > len(head) or head[e:e + 4] != b"PE\x00\x00":
                return False, "缺 PE 签名"
            nsec = struct.unpack_from("<H", head, e + 6)[0]
            opt = struct.unpack_from("<H", head, e + 20)[0]
            if not (1 <= nsec <= 96) or opt <= 0:
                return False, "节表头异常（nsec=%d, opt=%d）" % (nsec, opt)
            sec0 = e + 24 + opt
            if sec0 + 40 * nsec <= len(head):
                sh = head[sec0:sec0 + 40 * nsec]
            else:
                f.seek(sec0)
                sh = f.read(40 * nsec)
            if len(sh) < 40 * nsec:
                return False, "节表读不满（需要 %d 字节）" % (40 * nsec)
            need = 0
            for i in range(nsec):
                off = 40 * i
                _vs, _va, rsize, raddr = struct.unpack_from("<IIII", sh, off + 8)
                need = max(need, raddr + rsize)
            if size + 4096 < need:
                return False, ("文件被截断：大小 %d B，节表声明的映像需要 %d B"
                               % (size, need))
            return True, "PE 完整（大小 %d B >= 映像 %d B）" % (size, need)
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, e)


def scan_signature(path: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """静态体检：把某一档特征码拿到目标模块里扫。

    返回 hits 语义（三者严格区分，绝不混同）：
      hits=0    且 scanned=True  → 完整扫描后确实不存在（可据此判定该档不适用）
      hits>0                     → 命中（ok=True 仅当唯一命中）
      hits=None 且 scanned=False → 体检没跑完（文件缺失/IO 或内存错误）→ **结论未定**，
                                   调用方不得据此判定「不支持」。
    """
    res = {"label": cfg["label"], "hits": 0, "offsets": [], "ok": False,
           "reason": "", "scanned": False}
    if not path or not os.path.exists(path):
        res["reason"] = "目标模块不存在"
        res["scanned"] = False
        res["hits"] = None
        return res
    # 先证明「文件是完整的 PE」再谈特征码有没有：截断/非 PE 文件扫出 0 命中没有意义，
    # 必须报「结论未定」，否则会被读成「该版本不支持」。
    complete, why = _pe_image_complete(path)
    if not complete:
        res["hits"] = None
        res["scanned"] = False
        res["error"] = why
        res["reason"] = ("目标模块不完整/不是有效 PE（%s），特征码体检未完成，"
                         "结论未定——不得据此判定「不支持」" % why)
        return res
    pat, mask = cfg["pattern"], cfg["mask"]
    n = len(pat)
    fixed = [k for k in range(n) if mask[k] != "?"]
    wild = [k for k in range(n) if mask[k] == "?"]
    core = bytes(pat[k] for k in fixed)
    core_off = fixed[0]
    hits: List[int] = []
    # 分块扫描（CHUNK+OVERLAP）：目标模块可能近 200 MB，一次性 read() 曾实测抛
    # MemoryError；而 MemoryError 绝不能被当成「命中 0 处」——那是把「体检失败」
    # 误报成「该版本不支持」，会导致闸门给出错误结论。
    CHUNK = 8 << 20
    OVERLAP = n + 8
    try:
        total = os.path.getsize(path)
        base = 0
        tail = b""
        with open(path, "rb") as f:
            while True:
                buf = f.read(CHUNK)
                if not buf:
                    break
                data = tail + buf
                dbase = base - len(tail)
                p = data.find(core)
                while p >= 0:
                    start = dbase + p - core_off
                    if start >= 0 and start + n <= total:
                        absp = start - dbase
                        if 0 <= absp and absp + n <= len(data) and \
                                all(data[absp + k] == pat[k] for k in wild):
                            if start not in hits:
                                hits.append(start)
                    p = data.find(core, p + 1)
                if len(hits) > 64:
                    break
                tail = data[-OVERLAP:] if len(data) > OVERLAP else data
                base += len(buf)
        hits.sort()
        res["hits"] = len(hits)
        res["offsets"] = hits[:8]
        res["scanned"] = True
        res["ok"] = (len(hits) == 1)
        if len(hits) == 0:
            res["reason"] = "特征码在目标模块中不存在（已完整扫描 %.1f MB）" % (total / 1048576.0)
        elif len(hits) > 1:
            res["reason"] = "特征码命中 %d 处，scanner 取最小地址可能选错函数" % len(hits)
        else:
            res["reason"] = "唯一命中（文件偏移 0x%X）" % hits[0]
        return res
    except Exception as e:  # noqa: BLE001
        # 体检失败 ≠ 未命中：把三者分开，绝不把 ERROR 当 0 命中上报
        res["hits"] = None
        res["scanned"] = False
        res["error"] = "%s: %s" % (type(e).__name__, e)
        res["reason"] = "特征码体检未能完成（%s: %s），结论未定——不得据此判定「不支持」" \
                        % (type(e).__name__, e)
        return res


# ---------------------------------------------------------------------------
# 微信进程 / 安装目录 / 窗口
# ---------------------------------------------------------------------------

def _dep_state(name: str) -> str:
    """依赖三态：installed / missing / broken。用于 tools 字段与「代点登录」的降级决策。"""
    import importlib.util
    try:
        if importlib.util.find_spec(name) is None:
            return "missing"
    except (ImportError, ValueError):
        return "missing"
    try:
        __import__(name)
        return "installed"
    except Exception as e:  # noqa: BLE001
        return "broken(%s)" % type(e).__name__


def find_wechat_processes() -> List[Dict[str, Any]]:
    """枚举微信进程（4.x 进程名 Weixin.exe；3.x 为 WeChat.exe）。只读。

    本机实测：微信 4.x 登录后会有 3 个 `WeChatAppEx.exe`（小程序/内置浏览器渲染进程，
    路径在 %APPDATA%\\Tencent\\xwechat\\xplugin\\...）——它们**不是**主进程，
    拿主 PID（注入目标）时必须靠「有可见主窗口」来区分，不能只看进程名。
    """
    out: List[Dict[str, Any]] = []
    try:
        import psutil
    except ImportError:
        logger.warning("psutil 未安装，无法枚举微信进程")
        return out
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            nm = (p.info.get("name") or "").lower()
            if nm not in ("weixin.exe", "wechat.exe", "wechatappex.exe"):
                continue
            if nm == "wechatappex.exe":
                role = "appex"
            elif nm == "wechat.exe":
                role = "main_3x"
            else:
                role = "main"
            out.append({"pid": p.info["pid"], "name": p.info.get("name") or "",
                        "exe": p.info.get("exe") or "", "role": role})
        except Exception:  # noqa: BLE001
            continue
    return out


def _registry_evidence() -> List[str]:
    """把注册表值**原样**收进来当证据（含那条不可解码的 Version）。只读。"""
    ev: List[str] = []
    try:
        import winreg
        for hive, hive_name in ((winreg.HKEY_CURRENT_USER, "HKCU"),
                                (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            for sub in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat"):
                try:
                    k = winreg.OpenKey(hive, sub)
                except OSError:
                    continue
                try:
                    n = winreg.QueryInfoKey(k)[1]
                    for i in range(n):
                        name, val, _t = winreg.EnumValue(k, i)
                        ev.append("%s\\%s\\%s = %r（原始值，仅作证据）"
                                  % (hive_name, sub, name, val))
                finally:
                    winreg.CloseKey(k)
    except Exception as e:  # noqa: BLE001
        ev.append("读注册表失败：%s" % e)
    return ev


def get_install_dir(hint: Optional[str] = None) -> Dict[str, Any]:
    """定位微信安装目录：hint → 注册表 InstallPath → 卸载信息 → 常见路径。只读。"""
    ev: List[str] = []

    def _ok_dir(d: str) -> bool:
        return bool(d) and os.path.isdir(d) and (
            os.path.exists(os.path.join(d, "Weixin.exe")) or
            os.path.exists(os.path.join(d, "WeChat.exe")))

    cands: List[Tuple[str, str]] = []
    if hint:
        cands.append((hint, "配置/入参指定"))
    try:
        import winreg
        for hive, hive_name in ((winreg.HKEY_CURRENT_USER, "HKCU"),
                                (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            for sub in (r"Software\Tencent\Weixin", r"Software\Tencent\WeChat"):
                try:
                    k = winreg.OpenKey(hive, sub)
                except OSError:
                    continue
                try:
                    for vname in ("InstallPath", "InstallDir", "Install"):
                        try:
                            v, _t = winreg.QueryValueEx(k, vname)
                        except OSError:
                            continue
                        if isinstance(v, str) and v.strip():
                            cands.append((v.strip(), "%s\\%s\\%s" % (hive_name, sub, vname)))
                finally:
                    winreg.CloseKey(k)
    except Exception as e:  # noqa: BLE001
        ev.append("注册表 InstallPath 读取失败：%s" % e)
    for d, src in (("F:\\Weixin", "常见盘符"), ("C:\\Program Files\\Tencent\\Weixin", "常见路径"),
                   ("C:\\Program Files (x86)\\Tencent\\Weixin", "常见路径"),
                   ("D:\\Weixin", "常见盘符"), ("E:\\Weixin", "常见盘符")):
        cands.append((d, src))

    for d, src in cands:
        d = os.path.normpath(d) if d else d
        if _ok_dir(d):
            exe = os.path.join(d, "Weixin.exe")
            if not os.path.exists(exe):
                exe = os.path.join(d, "WeChat.exe")
            ev.append("命中安装目录 %s（来源：%s）" % (d, src))
            return {"dir": d, "exe": exe, "source": src, "evidence": ev, "ok": True}
    ev.append("未找到合格的微信安装目录（候选 %d 个均无 Weixin.exe/WeChat.exe）"
              % len(cands))
    return {"dir": "", "exe": "", "source": "unknown", "evidence": ev, "ok": False}


def get_version_info(install: Dict[str, Any]) -> Dict[str, Any]:
    """版本判定：路径 A = Weixin.dll 文件版本（推荐）；路径 B = 安装目录版本子目录名；
    exe 文件版本作旁证；注册表 Version 只当原始证据。只读。"""
    ev: List[str] = []
    info: Dict[str, Any] = {"version": "", "source": "unknown", "evidence": ev,
                            "version_dir": "", "weixin_dll": "",
                            "exe_version": "", "registry_raw": []}
    d = install.get("dir") or ""
    info["registry_raw"] = _registry_evidence()

    # 版本子目录（路径 B）
    version_dir = ""
    if d and os.path.isdir(d):
        try:
            for name in os.listdir(d):
                if re.match(r"^4\.\d+\.\d+\.\d+$", name) and os.path.isdir(os.path.join(d, name)):
                    version_dir = name
                    break
        except OSError:
            pass
    info["version_dir"] = version_dir
    if version_dir:
        ev.append("安装目录下的版本子目录名 = %s（路径 B）" % version_dir)

    # Weixin.dll（路径 A 的宿主模块）
    weixin_dll = ""
    probes = [os.path.join(d, "Weixin.dll")]
    if version_dir:
        probes.insert(0, os.path.join(d, version_dir, "Weixin.dll"))
    for p in probes:
        if p and os.path.exists(p):
            weixin_dll = p
            break
    if not weixin_dll and d and version_dir:
        vd = os.path.join(d, version_dir)
        try:
            for name in os.listdir(vd):
                if name.lower() == "weixin.dll":
                    weixin_dll = os.path.join(vd, name)
                    break
        except OSError:
            pass
    info["weixin_dll"] = weixin_dll
    dll_ver = _read_file_version(weixin_dll) if weixin_dll else ""
    if dll_ver:
        ev.append("Weixin.dll 文件版本 = %s（%s，路径 A，推荐）" % (dll_ver, weixin_dll))
    exe_ver = ""
    if install.get("exe"):
        exe_ver = _read_file_version(install["exe"])
        if exe_ver:
            ev.append("%s 文件版本 = %s（旁证）"
                      % (os.path.basename(install["exe"]), exe_ver))
    info["exe_version"] = exe_ver

    if dll_ver:
        info["version"], info["source"] = dll_ver, "file_version"
    elif version_dir:
        info["version"], info["source"] = version_dir, "version_dir"
    elif exe_ver:
        info["version"], info["source"] = exe_ver, "exe_file_version"
    else:
        info["source"] = "unknown"
        ev.append("三条可靠路径都没拿到版本号（注册表 Version 不参与判定）")
    return info


# --- 窗口枚举 ---------------------------------------------------------------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_uint]


def _win_text(hwnd) -> str:
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value or ""


def _win_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value or ""


def _win_pid(hwnd) -> int:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _child_infos(hwnd) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    def cb(h, _l):
        out.append({"hwnd": int(h), "title": _win_text(h), "class": _win_class(h)})
        return True

    try:
        _user32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("枚举子窗口失败 %s: %s", hwnd, e)
    return out


def list_wechat_windows(pid: Optional[int] = None) -> List[Dict[str, Any]]:
    """列出标题含「微信/Weixin/WeChat」的可见顶层窗口；pid 给定时按 PID 过滤。"""
    out: List[Dict[str, Any]] = []

    def cb(h, _l):
        try:
            if not _user32.IsWindowVisible(h):
                return True
            title = _win_text(h).strip()
            if not title:
                return True
            if not any(m in title.lower() or m in title for m in WIN_TITLE_MARKERS):
                return True
            w_pid = _win_pid(h)
            if pid is not None and w_pid != int(pid):
                return True
            out.append({"hwnd": int(h), "pid": w_pid, "title": title,
                        "class": _win_class(h)})
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        _user32.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception as e:  # noqa: BLE001
        logger.debug("EnumWindows 失败: %s", e)
    return out


def find_main_wechat_pid() -> Optional[int]:
    """主窗口 PID（传给 InitializeHook 的那个）。等价于参考实现的 findMainWeChatPid()。

    实测偏好顺序：① 拥有可见窗口的「主进程」（Weixin.exe / WeChat.exe）→ ② 任一带窗口
    的微信进程 → ③ 任一主进程。刻意避开 WeChatAppEx.exe（小程序渲染进程）的窗口。
    """
    wins = list_wechat_windows(None)
    if wins:
        main_pids = {int(p["pid"]) for p in find_wechat_processes()
                     if p.get("role") in ("main", "main_3x")}
        for w in wins:
            if int(w["pid"]) in main_pids:
                return int(w["pid"])
        return int(wins[0]["pid"])
    procs = [p for p in find_wechat_processes() if p.get("role") in ("main", "main_3x")]
    return int(procs[0]["pid"]) if procs else None


def probe_login_screen(pid: Optional[int] = None) -> Dict[str, Any]:
    """用 UI Automation 判断当前是否停在登录/扫码界面，并找登录按钮。**只查不点。**

    为什么必须用 UIA：微信 4.x 是 Qt 自绘，登录表单没有带标题的原生子控件
    （实测子窗口类只有 `OimeTsfNotifyWindow` / `MMUIRenderSubWindowHW`），
    靠 GetWindowText 永远判不出「是否在登录界面」。
    本机未装 `uiautomation` 包，走已装的 comtypes 直连 UIAutomationCore。
    """
    out: Dict[str, Any] = {"known": False, "at_login": None, "login_button": False,
                           "chat_ui": False, "method": "unavailable", "detail": ""}
    pid = pid or find_main_wechat_pid()
    if not pid:
        out["detail"] = "没有微信主窗口"
        return out
    try:
        import comtypes.client  # type: ignore
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA  # type: ignore
        uia = comtypes.client.CreateObject(UIA.CUIAutomation,
                                           interface=UIA.IUIAutomation)
        root = uia.GetRootElement()
        pid_cond = uia.CreatePropertyCondition(UIA.UIA_ProcessIdPropertyId, int(pid))
        win = root.FindFirst(UIA.TreeScope_Children, pid_cond)
        if not win:
            out.update({"method": "comtypes_uia", "known": True, "at_login": None,
                        "detail": "UIA 未定位到 PID=%s 的顶层窗口" % pid})
            return out
        for key, names in (("login_button", ("登录", "登录微信", "进入微信", "Enter")),
                           ("chat_ui", ("聊天", "通讯录", "WeChat", "微信"))):
            for n in names:
                cond = uia.CreatePropertyCondition(UIA.UIA_NamePropertyId, n)
                if win.FindFirst(UIA.TreeScope_Descendants, cond):
                    out[key] = True
                    break
        out.update({"method": "comtypes_uia", "known": True,
                    "at_login": bool(out["login_button"]) and not bool(out["chat_ui"]),
                    "detail": "UIA 探测到 登录按钮=%s / 主界面元素=%s"
                              % (out["login_button"], out["chat_ui"])})
    except Exception as e:  # noqa: BLE001
        out["detail"] = "UIA 不可用（%s: %s）；登录界面判定降级为「未知」，不臆造" \
                        % (type(e).__name__, e)
    return out


def window_readiness(pid: Optional[int] = None, deep: bool = False) -> Dict[str, Any]:
    """界面就绪判据（说明书第六节 _hasReadyComponents 的三级放宽 + 登录标记）。

    `deep=True` 时额外跑一次 UIA 登录探测（较慢，约 1–3s），用于 wait_login 阶段；
    默认 deep=False 只做廉价的窗口/子窗口统计。`at_login` 在 UIA 不可用时为 None
    （未知），不返回误导性的 False。
    """
    wins = list_wechat_windows(pid)
    snap: List[Dict[str, Any]] = []
    ready = False
    logged_in = False
    at_login: Optional[bool] = False
    for w in wins:
        kids = _child_infos(w["hwnd"])
        snap.append({"hwnd": w["hwnd"], "title": w["title"],
                     "class": w["class"], "child_count": len(kids),
                     "sample": ["%s:%s" % (k["class"], k["title"]) for k in kids[:6]]})
        titles = [k["title"].replace(" ", "") for k in kids if k["title"]]
        classes = [k["class"] for k in kids if k["class"]]
        if any("聊天" in t for t in titles):
            logged_in = True
        if any(any(m in t for m in LOGIN_TEXT_MARKERS) for t in titles):
            at_login = True
        if any(any(m in t for m in READY_TEXT_MARKERS) for t in titles):
            ready = True
        if any(any(m in c for m in READY_CLASS_MARKERS) for c in classes):
            ready = True
        if len(kids) >= READY_CHILD_COUNT:
            ready = True
        if sum(1 for c in classes if len(c) > 5) >= 3:
            ready = True
    if not wins:
        ready = False
    result = {"ready": bool(ready), "logged_in": bool(logged_in),
              "at_login": at_login, "windows": snap, "login_probe": None}
    if deep and wins:
        probe = probe_login_screen(wins[0]["pid"])
        result["login_probe"] = probe
        if probe.get("known"):
            result["at_login"] = probe.get("at_login")
            if probe.get("chat_ui"):
                result["logged_in"] = True
    return result


# ---------------------------------------------------------------------------
# 控制器 DLL 的 ctypes 封装（说明书第七节）
# ---------------------------------------------------------------------------

class WeChatKeyController:
    """加载 wx_key.dll → InitializeHook(pid) → 轮询 PollKeyData → CleanupHook()。

    只做 DLL FFI 与轮询；进程/窗口编排在 WeChatKeyFlow。
    """

    def __init__(self, dll_path: Optional[str] = None):
        self.dll_path = dll_path or self.resolve_dll_path()[0]
        self.identity = self.check_dll(self.dll_path)
        self._dll = None
        self._inited = False
        self._lock = threading.Lock()

    # -- 定位与自检 --------------------------------------------------------
    @staticmethod
    def resolve_dll_path() -> Tuple[str, List[str]]:
        """优先工程内工具目录；路径含中文时复制到 ASCII 临时目录（README 硬要求）。"""
        notes: List[str] = []
        src = None
        for cand in (DLL_PRIMARY, DLL_FALLBACK):
            if cand and os.path.exists(cand):
                src = cand
                break
        if src is None:
            return "", ["未找到 wx_key.dll（工具目录与源码 assets 都没有）"]
        notes.append("DLL 命中：%s" % src)
        try:
            src.encode("ascii")
            return src, notes
        except UnicodeEncodeError:
            pass
        tmp_dir = os.path.join(tempfile.gettempdir(), "_lai_wxkey_dll")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
            dst = os.path.join(tmp_dir, "wx_key.dll")
            shutil.copy2(src, dst)
            notes.append("DLL 原路径含中文，已复制到 ASCII 路径再加载：%s" % dst)
            return dst, notes
        except Exception as e:  # noqa: BLE001
            notes.append("复制到 ASCII 路径失败：%s" % e)
            return src, notes

    @staticmethod
    def check_dll(path: str) -> Dict[str, Any]:
        """启动期自检：存在 / x64 / 导出名齐 / sha256（被换掉就报错，不静默用错 DLL）。"""
        out: Dict[str, Any] = {"ok": False, "path": path, "sha256": "", "exports": [],
                               "is_x64": False, "size": 0,
                               "sha_match_expected": False, "reason": ""}
        if not path or not os.path.exists(path):
            out["reason"] = "DLL 不存在"
            return out
        out["size"] = os.path.getsize(path)
        out["sha256"] = sha256_of_file(path)
        out["sha_match_expected"] = (out["sha256"] == DLL_EXPECTED_SHA256)
        pe = pe_machine_and_exports(path)
        out["exports"] = pe.get("exports") or []
        out["is_x64"] = bool(pe.get("is_x64"))
        if not pe.get("ok"):
            out["reason"] = "PE 解析失败：%s" % pe.get("reason")
            return out
        if not out["is_x64"]:
            out["reason"] = "DLL 不是 x64（machine=0x%X），仅支持 64 位" % pe.get("machine", 0)
            return out
        missing = [n for n in DLL_EXPECTED_EXPORTS if n not in out["exports"]]
        if missing:
            out["reason"] = "导出缺失：%s" % ",".join(missing)
            return out
        out["ok"] = True
        if not out["sha_match_expected"]:
            out["reason"] = ("导出齐全，但 sha256 与本机实测基准不一致"
                             "（可能被替换/升级，请复核）")
        else:
            out["reason"] = "导出齐全且 sha256 与实测基准一致"
        return out

    # -- FFI --------------------------------------------------------------
    def load(self) -> bool:
        if self._dll is not None:
            return True
        if not self.identity.get("ok"):
            logger.error("拒绝加载 wx_key.dll：%s", self.identity.get("reason"))
            return False
        d = ctypes.WinDLL(self.dll_path)
        d.InitializeHook.argtypes = [wintypes.DWORD]
        d.InitializeHook.restype = ctypes.c_bool
        d.PollKeyData.argtypes = [ctypes.c_char_p, ctypes.c_int]
        d.PollKeyData.restype = ctypes.c_bool
        d.GetStatusMessage.argtypes = [ctypes.c_char_p, ctypes.c_int,
                                       ctypes.POINTER(ctypes.c_int)]
        d.GetStatusMessage.restype = ctypes.c_bool
        d.CleanupHook.argtypes = []
        d.CleanupHook.restype = ctypes.c_bool
        d.GetLastErrorMsg.argtypes = []
        d.GetLastErrorMsg.restype = ctypes.c_char_p
        self._dll = d
        return True

    def last_error(self) -> str:
        if self._dll is None:
            return ""
        try:
            b = self._dll.GetLastErrorMsg()
            return b.decode("utf-8", "ignore") if b else ""
        except Exception:  # noqa: BLE001
            return ""

    def initialize(self, pid: int) -> bool:
        """!! 仅可在用户显式知情同意后调用（红线 1）!!"""
        if not self.load():
            return False
        with self._lock:
            ok = bool(self._dll.InitializeHook(int(pid)))
            self._inited = ok
        if not ok:
            logger.warning("InitializeHook(%s) 返回 false：%s", pid, self.last_error())
        return ok

    def poll_key(self) -> Optional[str]:
        """取密钥；读一次即被 DLL 清空。缓冲区给 128（避坑清单第 5 条）。"""
        if self._dll is None:
            return None
        buf = ctypes.create_string_buffer(128)
        if self._dll.PollKeyData(buf, 128):
            s = buf.value.decode("ascii", "ignore").strip()
            return s or None
        return None

    def drain_status(self, max_items: int = 16) -> List[Tuple[int, str]]:
        """循环取 DLL 内部日志直到返回 false；level 0=Info 1=Success 2=Error。"""
        out: List[Tuple[int, str]] = []
        if self._dll is None:
            return out
        for _ in range(max(1, int(max_items))):
            buf = ctypes.create_string_buffer(512)
            lvl = ctypes.c_int(0)
            try:
                if not self._dll.GetStatusMessage(buf, 512, ctypes.byref(lvl)):
                    break
            except Exception:  # noqa: BLE001
                break
            out.append((int(lvl.value), buf.value.decode("utf-8", "ignore")))
        return out

    def cleanup(self) -> bool:
        """退出前必须调用（避坑清单第 1 条）。"""
        with self._lock:
            if self._dll is None:
                return True
            try:
                if self._inited:
                    self._dll.CleanupHook()
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning("CleanupHook 异常：%s", e)
                return False
            finally:
                self._inited = False

    @property
    def hooked(self) -> bool:
        return bool(self._inited)


# ---------------------------------------------------------------------------
# 生命周期动作（关 / 启 / 前台化 / 等窗口 / 代点登录）
# ---------------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def kill_wechat(timeout: float = 15.0) -> Dict[str, Any]:
    """关闭微信进程（TerminateProcess）。参考 killWeChatProcesses()。

    只关**主进程**（role=main / main_3x）；`WeChatAppEx.exe` 是小程序渲染子进程，
    没有独立的密码学上下文、随主进程退出而退出，杀它无意义还可能弹窗，故不动。
    """
    all_procs = find_wechat_processes()
    procs = [p for p in all_procs if p.get("role") in ("main", "main_3x")]
    if not all_procs:
        return {"ok": True, "killed": [], "detail": "微信本来就没在跑"}
    if not procs:
        return {"ok": True, "killed": [],
                "detail": "只检测到 %d 个进程且都不是主进程（无主进程可关）" % len(all_procs)}
    killed: List[int] = []
    failed: List[Dict[str, Any]] = []
    try:
        import psutil
    except ImportError:
        return {"ok": False, "killed": [], "detail": "psutil 未安装，无法关闭微信"}
    for p in procs:
        try:
            pr = psutil.Process(int(p["pid"]))
            pr.terminate()
            try:
                pr.wait(timeout=max(1.0, timeout / max(1, len(procs))))
            except Exception:  # noqa: BLE001
                try:
                    pr.kill()
                except Exception:  # noqa: BLE001
                    pass
            killed.append(int(p["pid"]))
        except Exception as e:  # noqa: BLE001
            failed.append({"pid": p["pid"], "reason": "%s: %s" % (type(e).__name__, e)})
    deadline = time.time() + max(3.0, timeout)
    while time.time() < deadline and [p for p in find_wechat_processes()
                                      if p.get("role") in ("main", "main_3x")]:
        time.sleep(0.4)
    left = [p for p in find_wechat_processes() if p.get("role") in ("main", "main_3x")]
    ok = not left
    return {"ok": ok, "killed": killed, "failed": failed,
            "remaining": [p["pid"] for p in left],
            "detail": ("微信主进程已关闭（%d 个）" % len(killed)) if ok
                      else "仍有微信主进程存活：%s（可能以更高权限运行，需以管理员身份重试）"
                           % [p["pid"] for p in left]}


def launch_wechat(exe: str) -> Dict[str, Any]:
    """detached 启动微信，等 2 秒后确认进程在跑。参考 launchWeChat()。"""
    if not exe or not os.path.exists(exe):
        return {"ok": False, "pid": None, "detail": "微信可执行文件不存在：%s" % exe}
    try:
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            flags |= int(getattr(subprocess, name, 0))
        subprocess.Popen([exe], close_fds=True, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "pid": None, "detail": "启动失败：%s: %s" % (type(e).__name__, e)}
    time.sleep(2.0)
    procs = find_wechat_processes()
    if not procs:
        return {"ok": False, "pid": None, "detail": "已调用启动，但 2 秒后未发现微信进程"}
    return {"ok": True, "pid": int(procs[0]["pid"]),
            "pids": [int(p["pid"]) for p in procs], "detail": "微信已启动"}


def wait_for_window(pid: Optional[int] = None, max_wait: float = WINDOW_WAIT_SECONDS,
                    want_login_screen: bool = True, deep: bool = False) -> Dict[str, Any]:
    """等主窗口 + 等界面组件加载完（含「登录」标记）。参考 waitForWeChatWindowComponents()。

    实测教训：冷启动微信（进程已起但窗口还没画出来）可达 60s+，默认 30s 会误判超时，
    故默认值取 WINDOW_WAIT_SECONDS=120。`deep=True` 时最后再做一次 UIA 登录探测。
    """
    deadline = time.time() + max(1.0, max_wait)
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        p = pid or find_main_wechat_pid()
        if p:
            last = window_readiness(int(p))
            if last.get("ready"):
                if deep:
                    last = window_readiness(int(p), deep=True)
                if last.get("logged_in"):
                    phase = "已在主界面"
                elif last.get("at_login"):
                    phase = "登录/二维码界面"
                else:
                    phase = "界面就绪、登录态未知"
                return {"ok": True, "pid": int(p), "state": last,
                        "detail": "界面组件已就绪（%s）" % phase}
        time.sleep(0.5)
    return {"ok": False, "pid": pid, "state": last,
            "detail": "等待窗口/界面就绪超时（%.0fs）" % max_wait}


def foreground_wechat(pid: Optional[int] = None) -> Dict[str, Any]:
    """把微信窗口前置并把登录二维码露出来（ShowWindow + SetForegroundWindow）。"""
    pid = pid or find_main_wechat_pid()
    wins = list_wechat_windows(pid) if pid else []
    if not wins:
        return {"ok": False, "hwnd": None, "detail": "没找到可见的微信窗口"}
    hwnd = wins[0]["hwnd"]
    try:
        SW_RESTORE, SW_SHOW = 9, 5
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.ShowWindow(hwnd, SW_SHOW)
        # 前置：先让本线程与目标窗口线程挂上，提高 SetForegroundWindow 成功率
        SetWindowPos = _user32.SetWindowPos
        HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
        SWP_NOSIZE, SWP_NOMOVE, SWP_SHOWWINDOW = 0x1, 0x2, 0x40
        SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                     SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
        _user32.SetForegroundWindow(hwnd)
        _user32.BringWindowToTop(hwnd)
        SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                     SWP_NOSIZE | SWP_NOMOVE | SWP_SHOWWINDOW)
        fg = int(_user32.GetForegroundWindow())
        return {"ok": True, "hwnd": hwnd, "foreground_now": fg == hwnd,
                "detail": "已尝试前置微信窗口（hwnd=%s）" % hwnd}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "hwnd": hwnd, "detail": "%s: %s" % (type(e).__name__, e)}


def click_login_button(pid: Optional[int] = None) -> Dict[str, Any]:
    """代点登录（best-effort，三级降级；本机无 uiautomation，故主用 comtypes + win32）。

    ① comtypes + UIAutomationClient：找 Name ∈ {登录, 进入微信, 登录微信} 的按钮并 Invoke；
    ② pywin32：子窗口标题像「登录/进入」→ PostMessage(BM_CLICK)；
    ③ SetForegroundWindow + 回车键（SendInput）。
    两级都做不成就如实返回，由用户手点二维码——**不静默失败**。
    """
    pid = pid or find_main_wechat_pid()
    if not pid:
        return {"ok": False, "method": "none", "detail": "没有微信主窗口，无法代点"}

    # ① UIAutomation（本机无 uiautomation 包，用已装的 comtypes 直连 UIAutomationCore）
    uia_err = ""
    try:
        import comtypes.client  # type: ignore
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA  # type: ignore
        uia = comtypes.client.CreateObject(UIA.CUIAutomation,
                                           interface=UIA.IUIAutomation)
        root = uia.GetRootElement()
        # 先按 PID 锁定微信窗口元素
        pid_cond = uia.CreatePropertyCondition(UIA.UIA_ProcessIdPropertyId, int(pid))
        win_el = root.FindFirst(UIA.TreeScope_Children, pid_cond)
        scope = win_el if win_el else root
        for name in ("登录", "登录微信", "进入微信", "Enter", "Log In"):
            cond = uia.CreatePropertyCondition(UIA.UIA_NamePropertyId, name)
            el = scope.FindFirst(UIA.TreeScope_Descendants, cond)
            if not el:
                continue
            try:
                pat = el.GetCurrentPattern(UIA.UIA_InvokePatternId)
                pat.Invoke()
                return {"ok": True, "method": "uiautomation",
                        "detail": "UIA 命中按钮「%s」并已 Invoke" % name}
            except Exception as e:  # noqa: BLE001
                uia_err = "UIA 找到按钮「%s」但 Invoke 失败：%s: %s" % (name, type(e).__name__, e)
                break
        else:
            uia_err = "UIA 未命中登录按钮（窗口元素=%s）" % ("已定位" if win_el else "未定位")
    except Exception as e:  # noqa: BLE001
        uia_err = "UIA 不可用：%s: %s" % (type(e).__name__, e)

    # ② pywin32 BM_CLICK
    try:
        import win32api  # type: ignore
        import win32con  # type: ignore
        import win32gui  # type: ignore

        target = None

        def _enum_child(h, _l):
            nonlocal target
            t = win32gui.GetWindowText(h) or ""
            c = win32gui.GetClassName(h) or ""
            if target is None and t and ("登录" in t or "进入" in t) and \
                    ("Button" in c or "button" in c):
                target = h
            return True

        wins = list_wechat_windows(pid)
        if wins:
            try:
                win32gui.EnumChildWindows(wins[0]["hwnd"], _enum_child, None)
            except Exception:  # noqa: BLE001
                pass
        if target:
            win32api.PostMessage(target, win32con.BM_CLICK, 0, 0)
            return {"ok": True, "method": "pywin32_bm_click",
                    "detail": "已对按钮 hwnd=%s 发送 BM_CLICK" % target}
        winmsg_err = "pywin32 未找到像登录按钮的子窗口"
    except Exception as e:  # noqa: BLE001
        winmsg_err = "pywin32 不可用：%s: %s" % (type(e).__name__, e)

    # ③ 前置 + 回车（只能算"已尝试"，拿不到点击是否真的命中的证据，故 ok=False）
    try:
        foreground_wechat(pid)
        VK_RETURN, KEYEVENTF_KEYUP = 0x0D, 0x0002
        ctypes.windll.user32.keybd_event(VK_RETURN, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
        return {"ok": False, "method": "sendinput_enter", "sent": True,
                "detail": "已前置微信窗口并发送回车（**仅表示已尝试，无点击命中证据**）；"
                          "UIA：%s；win32：%s" % (uia_err, winmsg_err)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "method": "none",
                "detail": "三种代点方式都失败：UIA(%s) / win32(%s) / 回车(%s: %s)"
                          % (uia_err, winmsg_err, type(e).__name__, e)}


# ---------------------------------------------------------------------------
# 编排：WeChatKeyFlow（阶段机 + 后台线程 + 进度 + 取消）
# ---------------------------------------------------------------------------

class WeChatKeyFlow:
    """把 1–7 接成一条线，产出 64 位 hex 并写进机器指纹加密缓存。

    9 个固定阶段（前端按 stage 显示）：
      detect / kill / launch / foreground / wait_login / inject / poll_key / persist / cleanup
    """

    def __init__(self, key_extractor: Any = None, install_hint: Optional[str] = None,
                 dll_path: Optional[str] = None,
                 logger_obj: Optional[Any] = None):
        self._key_extractor = key_extractor
        self._install_hint = install_hint
        self._dll_path = dll_path
        self._log: List[str] = []
        self._lock = threading.RLock()
        self._cancel_evt = threading.Event()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stages: List[Dict[str, Any]] = []
        self._result: Optional[Dict[str, Any]] = None
        self._progress: Dict[str, Any] = {"stage": "idle", "stage_name": "空闲",
                                         "percentage": 0, "detail": "",
                                         "running": False}
        self._cb: Optional[Callable] = None

    # -- 进度 / 状态 -------------------------------------------------------
    def get_progress(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._progress)

    def cancel(self) -> None:
        self._cancel_evt.set()
        with self._lock:
            self._log.append("[cancel] 收到取消请求")

    def last_log(self) -> List[str]:
        with self._lock:
            return list(self._log)

    def _log_line(self, line: str) -> None:
        with self._lock:
            self._log.append(line)
        logger.info("[keyflow] %s", line)

    def _set_stage(self, stage: str, detail: str = "", ok: bool = True) -> None:
        with self._lock:
            self._progress.update({"stage": stage,
                                   "stage_name": STAGE_NAMES.get(stage, stage),
                                   "percentage": STAGE_PERCENT.get(stage, 0),
                                   "detail": detail, "running": bool(self._running)})
        self._record(stage, detail, ok)
        if self._cb:
            try:
                self._cb(dict(self.get_progress()))
            except Exception:  # noqa: BLE001
                pass

    def _record(self, stage: str, detail: str, ok: bool) -> None:
        with self._lock:
            for st in self._stages:
                if st["stage"] == stage:
                    st.update({"ok": ok, "detail": detail})
                    return
            self._stages.append({"stage": stage, "stage_name": STAGE_NAMES.get(stage, stage),
                                 "ok": ok, "detail": detail})

    # -- 只读预检 ---------------------------------------------------------
    def precheck(self) -> Dict[str, Any]:
        """纯只读：不 kill、不 inject、不 attach。供 UI 决策与告知用户。"""
        dll_path, dll_notes = WeChatKeyController.resolve_dll_path()
        ident = WeChatKeyController.check_dll(dll_path)
        install = get_install_dir(self._install_hint)
        ver = get_version_info(install)
        procs = find_wechat_processes()
        main_pid = find_main_wechat_pid()
        cfg = pick_sig_config(ver.get("version") or "")
        branch_label = cfg["label"] if cfg else ""
        branch = canonical_branch(branch_label)     # 契约写法：ge_4.1.4 / lt_4.1.4 / unknown
        sig = scan_signature(ver.get("weixin_dll") or "", cfg) if cfg else \
            {"ok": False, "hits": None, "scanned": False,
             "reason": "版本未知，无法定档"}
        available = bool(ident.get("ok") and install.get("ok") and ver.get("version")
                         and sig.get("ok"))
        reason_bits: List[str] = []
        if not ident.get("ok"):
            reason_bits.append("控制器 DLL 不可用（%s）" % ident.get("reason"))
        if not install.get("ok"):
            reason_bits.append("未定位到微信安装目录")
        if not ver.get("version"):
            reason_bits.append("未能可靠识别微信版本")
        if cfg and sig.get("scanned") and not sig.get("ok"):
            reason_bits.append("按版本选定特征码档位「%s」，静态体检结果：%s"
                               % (cfg["label"], sig.get("reason")))
        elif cfg and not sig.get("scanned"):
            reason_bits.append("按版本选定特征码档位「%s」，但静态体检未完成：%s"
                               % (cfg["label"], sig.get("reason")))
        if not reason_bits:
            reason_bits.append("DLL 自检通过、版本已识别（%s/%s）、特征码档位「%s」唯一命中"
                               % (ver.get("version"), ver.get("source"), branch))
        admin = is_admin()
        if not admin:
            reason_bits.append("当前进程非管理员：InitializeHook 可能因权限被拒（实测结论见报告）")
        return {
            "wechat_dir": install.get("dir") or "",
            "version": ver.get("version") or "",
            "version_source": ver.get("source") or "unknown",
            "process_running": bool(procs),
            "pids": [int(p["pid"]) for p in procs],
            "main_pid": int(main_pid) if main_pid else None,
            "exe": install.get("exe") or "",
            "is_admin": bool(admin),
            "need_admin": (not admin),
            "dll_path": dll_path,
            "dll_ok": bool(ident.get("ok")),
            "dll_exports": list(ident.get("exports") or []),
            "signature_branch": branch,
            "available": available,
            "reason": "；".join(reason_bits),
            # ↓ 以下为便于排障的追加字段（前端可忽略，不影响既有字段）
            # signature_branch 口径：见文件顶部 SIG_BRANCH_* 说明。
            #   signature_branch        = 契约写法（ge_4.1.4 / lt_4.1.4 / unknown）
            #   signature_branch_label  = 原始 label（>=4.1.4 / <4.1.4 / ""）
            #   signature_branch_aliases= 两种写法都在，供按老口径取值的代码查表
            "signature_branch_label": branch_label,
            "signature_branch_aliases": branch_aliases(branch_label),
            "signature_hits": (None if sig.get("hits") is None else int(sig.get("hits"))),
            "signature_scanned": bool(sig.get("scanned")),
            "signature_detail": sig.get("reason") or "",
            "weixin_dll": ver.get("weixin_dll") or "",
            "version_dir": ver.get("version_dir") or "",
            "dll_sha256": ident.get("sha256") or "",
            "dll_sha_match_expected": bool(ident.get("sha_match_expected")),
            "dll_notes": dll_notes,
            "evidence": (install.get("evidence") or []) + (ver.get("evidence") or [])
                        + (ver.get("registry_raw") or []),
        }

    # -- 执行 -------------------------------------------------------------
    def run(self, consent_injection: bool = False, auto_restart: bool = True,
            allow_auto_login_click: bool = False,
            wait_seconds: int = 180, progress_cb: Optional[Callable] = None) -> Dict[str, Any]:
        """完整编排。consent_injection=False 时**只做只读预检**并返回拒绝原因。"""
        if not consent_injection:
            pre = self.precheck()
            return {"ok": False, "data": {
                "stages": [], "key_acquired": False, "key_hex_len": 0,
                "key_method": "", "key_source": "", "cleanup_done": True,
                "wechat_restarted": False,
                "errors": [{
                    "stage": "inject",
                    "reason": "未取得用户显式知情同意（consent_injection=False），"
                              "按红线不执行任何注入/关闭微信的动作",
                    "next_action": "在界面上向用户展示『将向微信进程写入 Hook 以拦截密钥；"
                                   "流程结束后自动卸载；异常退出时微信可能需要重启。』"
                                   "确认后再调用（consent_injection=True）",
                }],
                "precheck": pre}}
        self._cb = progress_cb
        with self._lock:
            self._cancel_evt.clear()
            self._log = []
            self._stages = []
            self._result = None
            self._running = True
            self._progress.update({"running": True, "percentage": 0,
                                   "stage": "detect", "stage_name": STAGE_NAMES["detect"],
                                   "detail": "开始"})
        try:
            return self._run_inner(auto_restart, allow_auto_login_click, wait_seconds)
        finally:
            with self._lock:
                self._running = False
                self._progress["running"] = False
            self._thread = None

    def start(self, **kwargs: Any) -> threading.Thread:
        t = threading.Thread(target=self.run, kwargs=kwargs, daemon=True,
                             name="wechat-keyflow")
        self._thread = t
        t.start()
        return t

    def _run_inner(self, auto_restart: bool, allow_auto_login_click: bool,
                   wait_seconds: int) -> Dict[str, Any]:
        errors: List[Dict[str, Any]] = []
        key_hex = ""
        key_method = ""
        key_source = ""
        restarted = False
        controller: Optional[WeChatKeyController] = None
        cleanup_done = True

        # ---- detect ----
        pre = self.precheck()
        self._log_line("预检：%s" % pre["reason"])
        for line in pre.get("evidence") or []:
            self._log_line("证据：%s" % line)
        self._set_stage("detect",
                        "版本=%s（%s），进程=%s，DLL 自检=%s，特征码档=%s"
                        % (pre["version"] or "?", pre["version_source"],
                           pre["pids"] or "未运行", "OK" if pre["dll_ok"] else "失败",
                           pre["signature_branch"]))
        pid = pre.get("main_pid") or (pre["pids"][0] if pre["pids"] else None)
        if not pre["dll_ok"]:
            errors.append({"stage": "detect", "reason": pre["reason"],
                           "next_action": "修复/替换 tools\\wx_key\\wx_key.dll 后重试"})
        # ★ 关键闸门：静态体检**完整扫描后确认 0 命中**时，绝不进入 kill。
        #   本机 4.1.15.13 实测：InitializeHook 报「模式匹配失败，找到 0 个结果」，
        #   与静态扫描一致。此时若继续 kill 会把用户正在用的微信无谓关掉。
        #   注意：只有 scanned=True 才算「确认不存在」；体检没跑完（hits=None）
        #   时结论未定，不得据此拦停（否则会把「体检失败」误报成「不支持」）。
        if pre.get("signature_scanned") and pre.get("signature_hits") == 0:
            errors.append({
                "stage": "detect",
                "reason": "所选特征码档「%s」在 %s 中命中 0 处（%s）；"
                          "实测 InitializeHook 亦报『模式匹配失败，找到 0 个结果』"
                          % (pre.get("signature_branch"), pre.get("weixin_dll") or "目标模块",
                             pre.get("signature_detail") or ""),
                "next_action": "该微信 %s 暂无匹配的控制器 DLL。可行方案：①按 findbyrva.cpp "
                               "为该版本重新生成特征码并重编 DLL（需 MSVC，本机暂无）；"
                               "②换用 README 已支持版本的微信；"
                               "③改用低风险只读取密钥路线。已停止流程，未关闭微信。"
                               % (pre.get("version") or "?")})
            return self._finish(False, key_hex, key_method, key_source, False,
                                cleanup_done, errors,
                                "静态体检发现该版本特征码不命中，按闸门在 kill 前停止（未关闭微信）")
        if not pre["available"] and not errors:
            errors.append({"stage": "detect",
                           "reason": "预检判定当前不可注入：%s" % pre["reason"],
                           "next_action": "按 reason 修复后重试"})
        if self._cancel_evt.is_set():
            return self._finish(False, key_hex, key_method, key_source, restarted,
                                cleanup_done, errors, "用户在 detect 阶段取消")

        # ---- kill ----
        if auto_restart:
            kr = kill_wechat()
            self._set_stage("kill", kr["detail"], bool(kr.get("ok")))
            self._log_line("关闭微信：%s" % kr["detail"])
            if not kr.get("ok"):
                errors.append({"stage": "kill", "reason": kr["detail"],
                               "next_action": "手动关闭微信后重试（或确认权限足够）"})
        else:
            self._set_stage("kill", "auto_restart=False，跳过关闭微信")
        if self._cancel_evt.is_set():
            return self._finish(False, key_hex, key_method, key_source, restarted,
                                cleanup_done, errors, "用户在 kill 阶段取消")

        # ---- launch ----
        if auto_restart:
            lr = launch_wechat(pre.get("exe") or "")
            self._set_stage("launch", lr["detail"], bool(lr.get("ok")))
            self._log_line("启动微信：%s" % lr["detail"])
            if lr.get("ok"):
                restarted = True
                # 优先用「有可见主窗口」的那个 PID（避开 WeChatAppEx.exe 小程序进程）
                pid = find_main_wechat_pid() or lr.get("pid")
            else:
                errors.append({"stage": "launch", "reason": lr["detail"],
                               "next_action": "确认微信安装可执行文件路径后重试"})
                return self._finish(False, key_hex, key_method, key_source, restarted,
                                    cleanup_done, errors, "启动微信失败")
        else:
            self._set_stage("launch", "auto_restart=False，跳过重启微信")

        # ---- foreground ----
        fr = foreground_wechat(pid)
        self._set_stage("foreground", fr["detail"], bool(fr.get("ok")))
        self._log_line("前台化：%s" % fr["detail"])

        # ---- wait_login ----
        # 窗口没等到也**不中止**：注入只依赖进程与模块，不依赖窗口；窗口只影响第 4/5 步。
        wr = wait_for_window(pid, max_wait=WINDOW_WAIT_SECONDS, deep=True)
        self._set_stage("wait_login", wr["detail"], bool(wr.get("ok")))
        self._log_line("等界面就绪：%s" % wr["detail"])
        if wr.get("ok"):
            state = wr.get("state") or {}
            probe = state.get("login_probe")
            if probe:
                self._log_line("UIA 登录探测：%s" % probe.get("detail"))
            if not state.get("logged_in"):
                self._log_line("界面已就绪但尚未进主界面：用户需要扫码")
                if allow_auto_login_click:
                    cl = click_login_button(pid)
                    self._log_line("代点登录：%s" % cl.get("detail"))
        else:
            errors.append({"stage": "wait_login", "reason": wr["detail"],
                           "next_action": "微信窗口未在 %.0fs 内出现。若微信未登录，请手动打开"
                                          "微信完成扫码；已继续尝试注入（注入不依赖窗口）"
                                          % WINDOW_WAIT_SECONDS})

        # ---- inject（红线：只有走到这里且拿到 consent 才注入）----
        controller = WeChatKeyController(self._dll_path)
        if not controller.identity.get("ok"):
            self._set_stage("inject", "DLL 自检未通过：%s" % controller.identity.get("reason"),
                            False)
            errors.append({"stage": "inject",
                           "reason": "控制器 DLL 自检未通过：%s"
                                     % controller.identity.get("reason"),
                           "next_action": "恢复 tools\\wx_key\\wx_key.dll 后重试"})
            return self._finish(False, key_hex, key_method, key_source, restarted,
                                cleanup_done, errors, "DLL 不可用")
        ok_init = controller.initialize(int(pid)) if pid else False
        statuses = controller.drain_status()
        for lvl, msg in statuses:
            self._log_line("DLL[L%d] %s" % (lvl, msg))
        err_msg = controller.last_error()
        if ok_init:
            self._set_stage("inject", "InitializeHook(%s) 成功" % pid, True)
            self._log_line("注入成功：InitializeHook(%s)=True" % pid)
        else:
            self._set_stage("inject", "InitializeHook(%s) 失败：%s" % (pid, err_msg or "无错误信息"),
                            False)
            errors.append({"stage": "inject",
                           "reason": "InitializeHook(%s) 返回 false，DLL 报：%s；内部日志：%s"
                                     % (pid, err_msg or "（空）",
                                        "; ".join(m for _l, m in statuses) or "（无）"),
                           "next_action": "若 DLL 报「特征码未找到」，说明该微信版本需按 "
                                          "findbyrva.cpp 重新生成特征码并重编 DLL；"
                                          "若报权限问题，请以管理员身份运行后重试"})
            cleanup_done = controller.cleanup()
            return self._finish(False, key_hex, key_method, key_source, restarted,
                                cleanup_done, errors, "注入失败")

        # ---- poll_key ----
        self._set_stage("poll_key", "开始轮询密钥（最长 %ss，100ms 间隔）" % wait_seconds)
        deadline = time.time() + max(10, int(wait_seconds))
        last_seq_note = 0
        while time.time() < deadline and not self._cancel_evt.is_set():
            k = controller.poll_key()
            if k:
                key_hex = k
                break
            for lvl, msg in controller.drain_status():
                if lvl >= 1:
                    self._log_line("DLL[L%d] %s" % (lvl, msg))
                last_seq_note += 1
            if last_seq_note % 50 == 0 and last_seq_note:
                self._set_stage("poll_key", "轮询中…（尚未拿到密钥，%.0fs）"
                                % (time.time() - (deadline - max(10, int(wait_seconds)))))
            time.sleep(0.1)
        if key_hex:
            self._set_stage("poll_key", "已拿到密钥（64 位 hex，长度 %d）" % len(key_hex))
        else:
            self._set_stage("poll_key", "轮询超时，未拿到密钥（可能尚未触发读库："
                                        "请在微信里打开一次聊天窗口再重试）", False)
            errors.append({"stage": "poll_key",
                           "reason": "钩子已挂上但 %ss 内未捕获到 setCipherKey 调用"
                                     % wait_seconds,
                           "next_action": "保持微信登录并打开一次聊天（触发读库），"
                                          "或加大 wait_seconds 后重试"})

        # ---- persist ----
        if key_hex:
            if len(key_hex) != 64 or not re.fullmatch(r"[0-9a-fA-F]{64}", key_hex):
                self._set_stage("persist", "DLL 返回的密钥格式异常（len=%d），拒绝入库"
                                % len(key_hex), False)
                errors.append({"stage": "persist",
                               "reason": "DLL 返回内容不是 64 位 hex",
                               "next_action": "重试；若持续异常请附 DLL 日志反馈"})
                key_hex = ""
            else:
                r = self._persist(key_hex)
                key_method = r.get("method", "wx_key_dll_auto_controller")
                key_source = r.get("source", "controller_dll")
                self._set_stage("persist", r.get("detail", ""), bool(r.get("ok")))
                self._log_line("落缓存：%s" % r.get("detail", ""))
                if not r.get("ok"):
                    errors.append({"stage": "persist", "reason": r.get("detail", ""),
                                   "next_action": "检查 data\\wechat_keys\\ 是否可写后重试"})
        else:
            self._set_stage("persist", "无密钥可写，跳过")

        # ---- cleanup（无论如何都要做）----
        cleanup_done = controller.cleanup()
        self._set_stage("cleanup", "CleanupHook 已调用（cleanup_done=%s）" % cleanup_done,
                        bool(cleanup_done))
        self._log_line("收尾：CleanupHook done=%s" % cleanup_done)
        return self._finish(bool(key_hex), key_hex, key_method, key_source, restarted,
                            cleanup_done, errors, "流程结束")

    # -- 落缓存 -----------------------------------------------------------
    def _persist(self, key_hex: str) -> Dict[str, Any]:
        ke = self._key_extractor
        if ke is None:
            try:
                from services import wechat_key_extractor as ke  # type: ignore
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "detail": "密钥模块不可用：%s" % e}
        method = "wx_key_dll_auto_controller"
        try:
            r = ke.import_key_plain(key_hex, method=method)
        except TypeError:
            try:
                r = ke.import_key_plain(key_hex, None, method)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "detail": "写缓存失败：%s: %s" % (type(e).__name__, e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "detail": "写缓存失败：%s: %s" % (type(e).__name__, e)}
        ok = bool(isinstance(r, dict) and r.get("ok"))
        return {"ok": ok, "method": method, "source": "controller_dll",
                "detail": "密钥已写入机器指纹加密缓存（method=%s）%s"
                          % (method, "" if ok else "；原返回：%s" % r)}

    def _finish(self, ok: bool, key_hex: str, key_method: str, key_source: str,
                restarted: bool, cleanup_done: bool,
                errors: List[Dict[str, Any]], detail: str) -> Dict[str, Any]:
        data = {
            "stages": list(self._stages),
            "key_acquired": bool(key_hex),
            "key_hex_len": len(key_hex),
            "key_masked": (key_hex[:8] + "…" + key_hex[-4:]) if len(key_hex) >= 16 else "",
            "key_method": key_method,
            "key_source": key_source,
            "cleanup_done": bool(cleanup_done),
            "wechat_restarted": bool(restarted),
            "errors": errors,
            "detail": detail,
            "log": self.last_log(),
        }
        self._result = {"ok": bool(ok), "data": data}
        with self._lock:
            self._progress.update({"detail": detail, "running": False})
        return self._result


__all__ = [
    "WeChatKeyController",
    "WeChatKeyFlow",
    "STAGES",
    "STAGE_NAMES",
    "SIG_CONFIGS",
    "DLL_EXPECTED_EXPORTS",
    "DLL_EXPECTED_SHA256",
    "find_wechat_processes",
    "find_main_wechat_pid",
    "list_wechat_windows",
    "window_readiness",
    "get_install_dir",
    "get_version_info",
    "pick_sig_config",
    "scan_signature",
    "pe_machine_and_exports",
    "is_admin",
    "kill_wechat",
    "launch_wechat",
    "foreground_wechat",
    "wait_for_window",
    "click_login_button",
]
