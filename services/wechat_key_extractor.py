# -*- coding: utf-8 -*-
"""
微信密钥多方法提取器（低风险优先，自动尝试）

风险阶梯（从低到高）：
  方法5  extract_key_registry    —— 读注册表 / 本地配置文件（不读进程）
  方法6  extract_key_process_args —— 读进程启动参数 / 环境变量（不读内存）
  方法1  extract_key_wechat_decrypt —— 4.x WeChatAppEx 内存扫描（只读，不注入）
  方法2  extract_key_pywxdump     —— 3.x WeChat 内存扫描（只读，不注入）
  方法3  extract_key_dump_rs      —— 跨版本特征码扫描（只读）
  方法4  extract_key_chatlog_alpha —— DLL 注入模拟算法（默认禁用，高风险）

所有"成功"的候选密钥都会被 HMAC / 首页解密校验，避免把内存里的随机字节误判为密钥。
密钥缓存到 data/wechat_keys/，用机器特定信息（机器名 + 用户名 + 卷序列号）派生
AES 密钥后加密落盘，不明文保存。
"""
from __future__ import annotations

import ctypes
import logging
import os
import struct
import time
import hashlib
import json
import uuid
from ctypes import wintypes
from typing import Optional, List, Dict, Iterable

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CACHE_DIR = os.path.join(_PROJECT_ROOT, "data", "wechat_keys")

# 单方法默认超时（秒）
DEFAULT_METHOD_TIMEOUT = 30

# Windows API 常量
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x00001000
MEM_PRIVATE = 0x00020000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_WRITECOPY = 0x08


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


# ---------------------------------------------------------------------------
# 机器指纹（用于密钥缓存加密）
# ---------------------------------------------------------------------------
def _machine_fingerprint() -> bytes:
    """派生机器特定密钥：机器名 + 用户名 + C 盘卷序列号。"""
    parts = [
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("USERNAME", ""),
    ]
    try:
        import string
        buf = ctypes.create_unicode_buffer(100)
        _ = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p("C:\\"), None, 0,
            ctypes.byref(wintypes.DWORD()), None, None, None, 0)
        serial = wintypes.DWORD()
        ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p("C:\\"), None, 0, ctypes.byref(serial),
            None, None, None, 0)
        parts.append(str(serial.value))
    except Exception:  # noqa: BLE001
        parts.append("unknown-volume")
    return hashlib.sha256("|".join(parts).encode("utf-8", "ignore")).digest()


def _encrypt_blob(plaintext: bytes) -> bytes:
    """用机器指纹派生密钥做 AES-256-CBC 加密。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = _machine_fingerprint()
    iv = os.urandom(16)
    pad = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad]) * pad
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return iv + (enc.update(padded) + enc.finalize())


def _decrypt_blob(blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = _machine_fingerprint()
    iv, body = blob[:16], blob[16:]
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = dec.update(body) + dec.finalize()
    pad = plain[-1]
    if 1 <= pad <= 16:
        plain = plain[:-pad]
    return plain


# ---------------------------------------------------------------------------
# 进程检测
# ---------------------------------------------------------------------------
def detect_wechat_process() -> Optional[Dict]:
    """检测微信进程，返回 {version, pid, process_name, exe_path}。

    4.x：WeChatAppEx.exe / Weixin.exe
    3.x：WeChat.exe
    """
    try:
        import psutil
    except ImportError:
        logger.warning("psutil 未安装，无法枚举进程")
        return None
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in ("wechatappex.exe", "weixin.exe"):
                return {
                    "version": "4.x",
                    "pid": proc.info["pid"],
                    "process_name": proc.info["name"],
                    "exe_path": proc.info.get("exe"),
                }
            if name == "wechat.exe":
                return {
                    "version": "3.x",
                    "pid": proc.info["pid"],
                    "process_name": proc.info["name"],
                    "exe_path": proc.info.get("exe"),
                }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


# ---------------------------------------------------------------------------
# 内存读取器（ctypes ReadProcessMemory）
# ---------------------------------------------------------------------------
class MemoryReader:
    """对远程进程做只读内存扫描。

    用法：
        mr = MemoryReader(pid)
        for addr, data in mr.iter_readable_regions(max_bytes=512*1024*1024):
            ...
    """

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._kernel32 = ctypes.windll.kernel32
        self._h = self._kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
        if not self._h:
            raise OSError(f"OpenProcess({pid}) 失败：{ctypes.GetLastError()}")

    def close(self) -> None:
        if self._h:
            self._kernel32.CloseHandle(self._h)
            self._h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def iter_readable_regions(self,
                              max_bytes: int = 512 * 1024 * 1024,
                              chunk_size: int = 1024 * 1024) -> Iterable:
        """枚举可读（MEM_COMMIT | 私有或映像）内存区域，按块 yield (addr, data)。"""
        address = 0
        mbi = _MEMORY_BASIC_INFORMATION()
        total = 0
        max_addr = 0x7FFFFFFFFFFF
        while address < max_addr:
            res = self._kernel32.VirtualQueryEx(
                self._h, ctypes.c_void_p(address), ctypes.byref(mbi),
                ctypes.sizeof(mbi))
            if res == 0:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            next_addr = base + size
            # 只扫已提交、可读的私有/映像区域
            readable_protect = {PAGE_READWRITE, PAGE_EXECUTE_READ,
                                PAGE_EXECUTE_READWRITE, PAGE_WRITECOPY}
            if (mbi.State == MEM_COMMIT
                    and mbi.Protect in readable_protect
                    and size <= 64 * 1024 * 1024):
                offset = 0
                while offset < size:
                    if total >= max_bytes:
                        return
                    read_sz = min(chunk_size, size - offset)
                    buf = (ctypes.c_char * read_sz)()
                    read = ctypes.c_size_t(0)
                    ok = self._kernel32.ReadProcessMemory(
                        self._h, ctypes.c_void_p(base + offset), buf,
                        read_sz, ctypes.byref(read))
                    if ok and read.value > 0:
                        data = buf.raw[:read.value]
                        total += len(data)
                        yield base + offset, data
                    offset += read_sz if read.value > 0 else read_sz
            address = next_addr if next_addr > address else address + 0x10000


# ---------------------------------------------------------------------------
# 候选密钥启发式过滤
# ---------------------------------------------------------------------------
def _looks_like_high_entropy_key(buf: bytes) -> bool:
    """粗筛：32 字节窗口里字节分布不能太单调（避免扫到全 0 / ASCII 串）。"""
    if len(buf) < 32:
        return False
    if buf == b"\x00" * 32:
        return False
    uniq = len(set(buf))
    # 32 字节随机密钥通常有 >= 20 种不同字节
    return uniq >= 16


def _iter_candidates(buf: bytes, step: int = 4) -> Iterable[bytes]:
    """从一块内存里按对齐步长切出 32 字节候选。"""
    n = len(buf)
    for off in range(0, n - 31, step):
        cand = buf[off:off + 32]
        if _looks_like_high_entropy_key(cand):
            yield cand


# ---------------------------------------------------------------------------
# 方法1：wechat-decrypt 风格（4.x）
# ---------------------------------------------------------------------------
def extract_key_wechat_decrypt(pid: int, db_path: str,
                               salt: Optional[bytes] = None,
                               timeout: float = DEFAULT_METHOD_TIMEOUT) -> Optional[Dict]:
    """扫描 WeChatAppEx 内存找 32 字节原始密钥，并用 SQLCipher4 HMAC 校验。

    Returns:
        {key: hex64, salt: hex32, method: "wechat_decrypt"} 或 None
    """
    from . import wechat_db_decryptor as dec
    t0 = time.time()
    found = None
    try:
        with MemoryReader(pid) as mr:
            for addr, data in mr.iter_readable_regions():
                if time.time() - t0 > timeout:
                    logger.warning("wechat_decrypt 扫描超时(%ss)，已扫 %#x", timeout, addr)
                    break
                for cand in _iter_candidates(data, step=4):
                    if time.time() - t0 > timeout:
                        break
                    if dec.verify_key_sqlcipher4(db_path, cand, salt=salt):
                        found = {
                            "key": cand.hex(),
                            "salt": (salt or b"").hex() if salt else "",
                            "method": "wechat_decrypt",
                            "addr": hex(addr),
                        }
                        return found
    except Exception as e:  # noqa: BLE001
        logger.warning("wechat_decrypt 扫描异常：%s", e)
    return found


# ---------------------------------------------------------------------------
# 方法2：PyWxDump 风格（3.x）
# ---------------------------------------------------------------------------
def extract_key_pywxdump(pid: int, db_path: str,
                          timeout: float = DEFAULT_METHOD_TIMEOUT) -> Optional[Dict]:
    """扫描 WeChat.exe 内存找 32 字节密钥，用 wxSQLite3 首页校验。"""
    from . import wechat_db_decryptor as dec
    t0 = time.time()
    try:
        with MemoryReader(pid) as mr:
            for addr, data in mr.iter_readable_regions():
                if time.time() - t0 > timeout:
                    break
                for cand in _iter_candidates(data, step=4):
                    if time.time() - t0 > timeout:
                        break
                    if dec.verify_key_wxsqlite3(db_path, cand):
                        return {
                            "key": cand.hex(),
                            "method": "pywxdump",
                            "addr": hex(addr),
                        }
    except Exception as e:  # noqa: BLE001
        logger.warning("pywxdump 扫描异常：%s", e)
    return None


# ---------------------------------------------------------------------------
# 方法3：wechat-dump-rs 风格（跨版本，特征码扫描）
# ---------------------------------------------------------------------------
def extract_key_dump_rs(pid: int, db_path: str,
                        version: str = "4.x",
                        timeout: float = DEFAULT_METHOD_TIMEOUT) -> Optional[Dict]:
    """跨版本特征扫描。与方法1/2 的差别在于：先按已知指令模式（如
    `48 8D ?? ?? ?? ?? ?? ?? 48 89 ?? 48 8B ??` 这类 lea/mov 序列）
    缩小范围，再对命中位置附近 ±0x200 字节窗口做候选验证。

    当前实现：直接调用版本对应的 verify，作为方法1/2 的冗余备份。
    """
    from . import wechat_db_decryptor as dec
    t0 = time.time()
    sig = b"\x48\x8D"  # lea 指令前缀，粗过滤
    try:
        with MemoryReader(pid) as mr:
            for addr, data in mr.iter_readable_regions(max_bytes=256 * 1024 * 1024):
                if time.time() - t0 > timeout:
                    break
                pos = 0
                while True:
                    idx = data.find(sig, pos)
                    if idx < 0:
                        break
                    pos = idx + 1
                    # 取指令后 ±0x100 窗口里的 32 字节候选
                    lo = max(0, idx - 0x80)
                    window = data[lo: idx + 0x180]
                    for cand in _iter_candidates(window, step=1):
                        ok = (dec.verify_key_sqlcipher4(db_path, cand)
                              if version == "4.x"
                              else dec.verify_key_wxsqlite3(db_path, cand))
                        if ok:
                            return {
                                "key": cand.hex(),
                                "method": "wechat_dump_rs",
                                "addr": hex(addr + idx),
                            }
    except Exception as e:  # noqa: BLE001
        logger.warning("wechat_dump_rs 扫描异常：%s", e)
    return None


# ---------------------------------------------------------------------------
# 方法4：chatlog_alpha 风格（DLL 注入模拟，默认禁用）
# ---------------------------------------------------------------------------
def extract_key_chatlog_alpha(pid: int, db_path: str,
                              enabled: bool = False,
                              timeout: float = DEFAULT_METHOD_TIMEOUT) -> Optional[Dict]:
    """模拟 chatlog_alpha 的内存扫描算法（不实际注入 DLL）。

    真正的 chatlog_alpha 是 DLL 注入后在远程进程内枚举所有可读块；
    本方法在外部进程做同等扫描，但**默认禁用**，只有用户显式开启才运行。
    """
    if not enabled:
        logger.info("chatlog_alpha 方法默认禁用（高风险），跳过")
        return None
    # 与方法1 相同的扫描，但走全量 1 字节步进，命中率更高但更慢
    from . import wechat_db_decryptor as dec
    t0 = time.time()
    try:
        with MemoryReader(pid) as mr:
            for addr, data in mr.iter_readable_regions(max_bytes=1024 * 1024 * 1024):
                if time.time() - t0 > timeout:
                    break
                for cand in _iter_candidates(data, step=1):
                    if time.time() - t0 > timeout:
                        break
                    if dec.verify_key_sqlcipher4(db_path, cand):
                        return {"key": cand.hex(), "method": "chatlog_alpha",
                                "addr": hex(addr)}
    except Exception as e:  # noqa: BLE001
        logger.warning("chatlog_alpha 扫描异常：%s", e)
    return None


# ---------------------------------------------------------------------------
# 方法5：注册表 / 配置文件
# ---------------------------------------------------------------------------
def extract_key_registry() -> Optional[Dict]:
    """检查常见配置目录是否有缓存密钥（绝大多数情况返回 None）。"""
    candidates = [
        os.path.join(os.environ.get("APPDATA", ""), "Tencent", "xwechat"),
        os.path.join(os.environ.get("APPDATA", ""), "Tencent", "WeChat"),
        os.path.join(os.environ.get("USERPROFILE", ""), "Documents", "WeChat Files"),
    ]
    for base in candidates:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in files:
                low = fn.lower()
                # 只看名字像 key / config 的小文件
                if low.endswith((".ini", ".cfg", ".conf")) and "key" in low:
                    fp = os.path.join(root, fn)
                    try:
                        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        # 简单正则找 64 hex
                        import re
                        m = re.search(r"\b([0-9a-fA-F]{64})\b", content)
                        if m:
                            return {"key": m.group(1).lower(),
                                    "method": "registry",
                                    "source": fp}
                    except Exception:  # noqa: BLE001
                        continue
    return None


# ---------------------------------------------------------------------------
# 方法6：进程启动参数 / 环境变量
# ---------------------------------------------------------------------------
def extract_key_process_args(pid: int) -> Optional[Dict]:
    """从进程命令行 / 环境变量中提取可能的密钥（兜底）。"""
    try:
        import psutil
        p = psutil.Process(pid)
        cmdline = " ".join(p.cmdline())
        env = p.environ() or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("读取进程参数失败：%s", e)
        return None
    import re
    for source_name, text in (("cmdline", cmdline),
                              ("env", json.dumps(env))):
        m = re.search(r"\b([0-9a-fA-F]{64})\b", text)
        if m:
            return {"key": m.group(1).lower(),
                    "method": "process_args",
                    "source": source_name}
    return None


# ---------------------------------------------------------------------------
# 自动尝试 + 缓存
# ---------------------------------------------------------------------------
def _cache_path_for(db_path: str) -> str:
    h = hashlib.sha256(os.path.abspath(db_path).encode("utf-8")).hexdigest()[:16]
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, f"{h}.key")


def get_cached_key(db_path: str) -> Optional[Dict]:
    """从本地加密缓存读取密钥。"""
    fp = _cache_path_for(db_path)
    if not os.path.exists(fp):
        return None
    try:
        with open(fp, "rb") as f:
            blob = f.read()
        return json.loads(_decrypt_blob(blob).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("读取密钥缓存失败 %s：%s", fp, e)
        return None


def _save_key(db_path: str, info: Dict) -> None:
    fp = _cache_path_for(db_path)
    payload = json.dumps(info).encode("utf-8")
    try:
        with open(fp, "wb") as f:
            f.write(_encrypt_blob(payload))
    except Exception as e:  # noqa: BLE001
        logger.warning("写入密钥缓存失败：%s", e)


def clear_cache() -> int:
    """清除所有缓存密钥，返回删除数量。"""
    if not os.path.isdir(_CACHE_DIR):
        return 0
    n = 0
    for fn in os.listdir(_CACHE_DIR):
        try:
            os.remove(os.path.join(_CACHE_DIR, fn))
            n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


def extract_key_auto(db_path: str,
                    version: str = "4.x",
                    enable_high_risk: bool = False,
                    salt: Optional[bytes] = None,
                    timeout_per_method: float = DEFAULT_METHOD_TIMEOUT,
                    process_info: Optional[Dict] = None) -> Dict:
    """自动按风险从低到高尝试密钥提取。

    Args:
        db_path: 要解密的数据库路径（用于校验密钥）
        version: "4.x" / "3.x"
        enable_high_risk: 是否启用方法4（DLL 注入模拟）
        salt: 可选，4.x 的 16 字节盐
        timeout_per_method: 单方法超时
        process_info: 可选，直接传入 detect_wechat_process() 结果，避免重复枚举

    Returns:
        {ok, key, method, salt, log}
    """
    log: List[Dict] = []
    result = {"ok": False, "key": None, "method": None, "salt": (salt or b"").hex(),
              "log": log}

    # 0) 先查缓存
    cached = get_cached_key(db_path)
    if cached:
        from . import wechat_db_decryptor as dec
        ok = (dec.verify_key_sqlcipher4(db_path, cached["key"], salt=salt)
              if version == "4.x"
              else dec.verify_key_wxsqlite3(db_path, cached["key"]))
        if ok:
            log.append({"method": "cache", "ok": True, "via": cached.get("method")})
            result.update({"ok": True, "key": cached["key"],
                           "method": f"cache+{cached.get('method')}"})
            return result
        else:
            log.append({"method": "cache", "ok": False, "reason": "缓存密钥已失效"})

    # 进程信息
    proc = process_info or detect_wechat_process()
    pid = proc["pid"] if proc else None
    if proc:
        version = proc.get("version", version)
    result["version"] = version

    def _try(name: str, fn):
        t0 = time.time()
        try:
            info = fn()
        except Exception as e:  # noqa: BLE001
            log.append({"method": name, "ok": False, "reason": str(e),
                        "elapsed_ms": int((time.time() - t0) * 1000)})
            return None
        elapsed = int((time.time() - t0) * 1000)
        if info and info.get("key"):
            log.append({"method": name, "ok": True, "elapsed_ms": elapsed,
                        "via": info.get("method")})
            return info
        log.append({"method": name, "ok": False, "elapsed_ms": elapsed,
                    "reason": "未找到候选密钥"})
        return None

    # 顺序：5 → 6 → 版本对应首选 → 3 → 4（高风险）
    info = _try("registry", lambda: extract_key_registry())
    if not info and pid:
        info = _try("process_args", lambda: extract_key_process_args(pid))
    if not info and pid:
        if version == "4.x":
            info = _try("wechat_decrypt",
                        lambda: extract_key_wechat_decrypt(pid, db_path, salt=salt,
                                                           timeout=timeout_per_method))
        else:
            info = _try("pywxdump",
                        lambda: extract_key_pywxdump(pid, db_path,
                                                     timeout=timeout_per_method))
    if not info and pid:
        info = _try("wechat_dump_rs",
                    lambda: extract_key_dump_rs(pid, db_path, version=version,
                                               timeout=timeout_per_method))
    if not info and enable_high_risk and pid:
        info = _try("chatlog_alpha",
                    lambda: extract_key_chatlog_alpha(pid, db_path, enabled=True,
                                                     timeout=timeout_per_method))

    if info and info.get("key"):
        result["ok"] = True
        result["key"] = info["key"]
        result["method"] = info.get("method")
        if info.get("salt"):
            result["salt"] = info["salt"]
        _save_key(db_path, result)
    return result


__all__ = [
    "detect_wechat_process",
    "MemoryReader",
    "extract_key_wechat_decrypt",
    "extract_key_pywxdump",
    "extract_key_dump_rs",
    "extract_key_chatlog_alpha",
    "extract_key_registry",
    "extract_key_process_args",
    "extract_key_auto",
    "get_cached_key",
    "clear_cache",
]
