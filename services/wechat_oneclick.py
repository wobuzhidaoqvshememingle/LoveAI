# -*- coding: utf-8 -*-
"""
微信聊天记录「一键导入」后端总控
=================================

把工程里原本散落的微信模块接成一条线：

    发现库(discover) → 认版本 → 取密钥 → 解密 → 读取 → 汇总(run)

对外只暴露 `WechatOneClick`，由 `app/service_facade.py` 的同名门面方法调用。

---------------------------------------------------------------------
红线（代码内逐条落实；改动本文件前先想清楚是否破坏了其中任何一条）
---------------------------------------------------------------------
R1. 微信库文件（E:\\xwechat_files 等）**全程只读**。
    解密前先把源库**复制为副本**到工程工作目录 `data/wechat_decrypted/_copy/`，
    只对副本解密；解密成功后立即删除副本。打开源库一律用 "rb"，
    绝不写回、绝不原地解密、绝不改动原库的 mtime 之外的状态。
R2. 密钥**不明文落盘**。全部复用 `services/wechat_key_extractor` 既有的
    机器指纹 AES 加密缓存（`data/wechat_keys/*.key`）；本模块只调用其
    公开函数，不自己写明文。
R3. 注入类动作（wx_key.dll / frida / chatlog_alpha）在**没有显式
    `consent_high_risk=True`** 时永不触发。`run()` 自己**从不**拉起注入窗口：
    唯一能拉起 wx_key 的入口是本类的 `acquire_key_high_risk()`，
    且必须由 UI 侧先取得用户显式知情同意后才调用。
R4. 抓取收尾的如实口径：正常结束会调用 CleanupHook 干净卸载，**不需要重启微信**；
    只有中途异常退出（未走到 CleanupHook）才可能需要重启微信。此提示只在用户确实
    选择了高风险路线时告知，不作为常规（低风险）导入路径的提示，避免把概率性风险说成必然故障。

关于"importable"的语义（前端对接需要统一口径）：
    本模块的 `importable` 表示**结构上可导入**（识别出布局且库数 > 0），
    与 `key_state`（密钥是否已就绪）是两个相互独立的字段。
    前端若要判断"现在能不能一次点通"，请用 `importable and key_state == "cached"`。
"""
from __future__ import annotations

import glob
import logging
import os
import shutil
import sqlite3
import struct
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WECHAT_KEYS_DIR = os.path.join(PROJECT_ROOT, "data", "wechat_keys")
# 全局 passphrase 缓存条目：**不要**用 os.path.exists 判断它是否存在！
# 该路径只是一把"逻辑键"，真正的密文落在 data/wechat_keys/<sha256(路径)[:16]>.key。
# 唯一可信的判据是 key_extractor.get_cached_key(<该路径>)，它内部会做路径哈希。
GLOBAL_CACHE_PATH = os.path.join(WECHAT_KEYS_DIR, "_global.db")

# 解密产物工作目录：与 services/wechat_db_reader.WeChatDBReader 的默认值保持一致，
# 这样本模块批量解密出的明文副本可以被 reader 直接复用，避免同一库解两次。
WORK_ROOT = os.path.join(PROJECT_ROOT, "data", "wechat_decrypted")
# 源库副本目录（R1：只对副本解密，解密完就删）
COPY_ROOT = os.path.join(WORK_ROOT, "_copy")

# ---------------------------------------------------------------------------
# 版本适配表（b：不写死单一版本）
# ---------------------------------------------------------------------------
# 每项含义：
#   prefix   —— 版本号前缀，用于把 "4.1.15.13" 这类版本号归入某一行
#   family   —— 版本族，"4.x" / "3.x"
#   cipher   —— 加密实现，"sqlcipher4" / "wxsqlite3"
#   layout   —— 磁盘布局，"db_storage"（4.x）/ "Msg"（3.x）
#   page_size / reserve / kdf_iter / hmac —— 解密参数（与 wechat_db_decryptor 常量一致）
#   message_glob —— 消息分片库相对路径 glob
#   notes    —— 人话说明，会进 version_evidence
VERSION_ADAPTATION: tuple = (
    {
        "prefix": "4.1", "family": "4.x", "cipher": "sqlcipher4", "layout": "db_storage",
        "page_size": 4096, "reserve": 80, "kdf_iter": 256000, "hmac": "HMAC-SHA512",
        "message_glob": ("db_storage/message/message_*.db",
                         "db_storage/message/biz_message_*.db"),
        "notes": "微信 4.1.x：SQLCipher4（AES-256-CBC + HMAC-SHA512，reserve=80，"
                 "kdf_iter=256000），库在 <账号>\\db_storage\\ 下按模块分目录",
    },
    {
        "prefix": "4.0", "family": "4.x", "cipher": "sqlcipher4", "layout": "db_storage",
        "page_size": 4096, "reserve": 80, "kdf_iter": 256000, "hmac": "HMAC-SHA512",
        "message_glob": ("db_storage/message/message_*.db",
                         "db_storage/message/biz_message_*.db"),
        "notes": "微信 4.0.x：与 4.1 同族布局与加密，仅版本号差异",
    },
    {
        "prefix": "3.9", "family": "3.x", "cipher": "wxsqlite3", "layout": "Msg",
        "page_size": 4096, "reserve": 48, "kdf_iter": 64000, "hmac": "HMAC-SHA256",
        "message_glob": ("Msg/Multi/MSG*.db", "Msg/MSG*.db"),
        "notes": "微信 3.9.x：wxSQLite3（AES-256-CBC，reserve=48），消息库为 Msg\\Multi\\MSG*.db",
    },
    {
        "prefix": "3.7", "family": "3.x", "cipher": "wxsqlite3", "layout": "Msg",
        "page_size": 4096, "reserve": 48, "kdf_iter": 64000, "hmac": "HMAC-SHA256",
        "message_glob": ("Msg/Multi/MSG*.db", "Msg/MSG*.db"),
        "notes": "微信 3.7.x：同 3.9 族布局与加密",
    },
    {
        "prefix": "", "family": "4.x", "cipher": "sqlcipher4", "layout": "db_storage",
        "page_size": 4096, "reserve": 80, "kdf_iter": 256000, "hmac": "HMAC-SHA512",
        "message_glob": ("db_storage/message/message_*.db",
                         "db_storage/message/biz_message_*.db"),
        "fallback": True,
        "notes": "未识别出版本号 → 按 4.x 兜底（evidence 里会显式说明是兜底判定）",
    },
)

# 布局签名：目录名 → 消息库 glob
LAYOUT_DIRS = {
    "db_storage": ("db_storage",),
    "Msg": ("Msg",),
}

# ---------------------------------------------------------------------------
# 已登记路线总表（g：全工具整合；每一步都会留痕进 log）
# ---------------------------------------------------------------------------
# risk:   none / low / high  —— high 一律要求显式 consent_high_risk
# auto:   是否允许在无人值守的低风险流程里自动尝试
# needs_process: 该路线依赖微信进程在跑（只读内存扫描类）
# needs_consent: 是否需要用户显式知情同意
TOOL_ROUTES: tuple = (
    {"id": "per_db_cache", "name": "per-db 加密缓存", "risk": "none", "auto": True},
    {"id": "global_cache", "name": "全局 passphrase 缓存(data/wechat_keys/_global.db)",
     "risk": "none", "auto": True},
    {"id": "wx_key_log", "name": "wx_key.dll 状态日志(%TEMP%\\wx_key_status.log)",
     "risk": "none", "auto": True},
    {"id": "key_record", "name": "密钥记录文件(data/wechat_keys 下 *.txt/*.json)",
     "risk": "none", "auto": True},
    {"id": "registry", "name": "注册表 / 本地配置", "risk": "none", "auto": True},
    {"id": "process_args", "name": "进程启动参数 / 环境变量", "risk": "none", "auto": True,
     "needs_process": True},
    {"id": "key_info_db", "name": "登录态 key_info.db（只读结构化扫描）",
     "risk": "none", "auto": True},
    {"id": "wechat_decrypt", "name": "wechat-decrypt 内存扫描(4.x)", "risk": "low",
     "auto": True, "needs_process": True},
    {"id": "pywxdump", "name": "PyWxDump 内存扫描(3.x)", "risk": "low", "auto": True,
     "needs_process": True},
    {"id": "dump_rs", "name": "wechat-dump-rs 特征码扫描", "risk": "low", "auto": True,
     "needs_process": True},
    {"id": "chatlog_alpha", "name": "chatlog_alpha 注入模拟", "risk": "high",
     "auto": False, "needs_process": True, "needs_consent": True},
    {"id": "wx_key_inject", "name": "wx_key.dll 注入（登录时刻抓 setCipherKey）",
     "risk": "high", "auto": False, "needs_consent": True},
    {"id": "android_root", "name": "Android root EnMicroMsg.db（手机端）", "risk": "n/a",
     "auto": False, "note": "4.x 桌面端不适用；仅老版本手机路线"},
    {"id": "weflow", "name": "WeFlow 外部 Electron GUI", "risk": "low", "auto": False,
     "note": "产出格式未核实：只登记「检测到外部工具 + 使用说明」，"
             "不假装已自动驱动（available=false，需手动）"},
)

_STAGE_NAMES = {
    "idle": "空闲",
    "discover": "发现微信数据库",
    "key": "获取数据库密钥",
    "decrypt": "解密数据库",
    "read": "读取消息",
    "done": "完成",
    "cancelled": "已取消",
    "error": "出错",
}


# ===========================================================================
# 模块级工具函数（wechat_auto_importer 也复用这几个）
# ===========================================================================
def default_roots() -> List[str]:
    """候选微信数据根目录（**候选项**，不是唯一来源）。

    按用户/环境不同，微信库可能落在任意盘；这里给全常见位置，逐个探测，
    绝不写死单一目录。
    """
    up = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    cands: List[str] = []
    env_root = os.environ.get("XWECHAT_FILES_ROOT")
    if env_root:
        cands.append(env_root)
    # 本机实测根（4.x）
    cands.append(r"E:\xwechat_files")
    # 4.x / 3.x 常见落点
    cands.append(os.path.join(up, "Documents", "xwechat_files"))
    cands.append(os.path.join(up, "Documents", "WeChat Files"))
    cands.append(os.path.join(up, "AppData", "Roaming", "Tencent", "xwechat_files"))
    return cands


def _norm(p: str) -> str:
    return os.path.abspath(os.path.normpath(p))


def detect_layout(account_dir: str) -> str:
    """判定账号目录的磁盘布局：db_storage / Msg / unknown。"""
    if not account_dir or not os.path.isdir(account_dir):
        return "unknown"
    if os.path.isdir(os.path.join(account_dir, "db_storage")):
        return "db_storage"
    if os.path.isdir(os.path.join(account_dir, "Msg")):
        return "Msg"
    return "unknown"


def root_kind(root: str) -> str:
    """判定某个根目录的版本族：4.x / 3.x / unknown（看子目录布局）。"""
    if not os.path.isdir(root):
        return "unknown"
    try:
        children = [os.path.join(root, n) for n in os.listdir(root)]
    except OSError:
        return "unknown"
    kinds = set()
    for c in children:
        if not os.path.isdir(c):
            continue
        layout = detect_layout(c)
        if layout == "db_storage":
            kinds.add("4.x")
        elif layout == "Msg":
            kinds.add("3.x")
    if kinds == {"4.x"}:
        return "4.x"
    if kinds == {"3.x"}:
        return "3.x"
    if not kinds:
        return "unknown"
    return "mixed"


# 扫描时只进"可能放库"的子树，绝不无脑递归媒体目录（
# 实测：E:\xwechat_files\wxid_..._90eb 下有 358913 个文件，媒体目录占绝大多数；
# 全量递归 glob 单账号 ~2.7–3.0s，限定 scandir 仅 ~0.19s，结果完全一致）。
_SCAN_SUBTREES = ("db_storage", "Msg")
_SCAN_MAX_ENTRIES = 200000     # 每个账号目录的访问条目上限（防御异常目录）
_SCAN_MAX_DEPTH = 4


def scan_dbs(account_dir: str, max_entries: int = _SCAN_MAX_ENTRIES,
             max_depth: int = _SCAN_MAX_DEPTH) -> List[str]:
    """**限定范围**收集账号目录下的 .db（os.scandir，比递归 glob 快约 15 倍）。

    覆盖 db_storage/**（4.x）与 Msg/**（3.x），外加账号根目录下的 *.db。
    """
    if not account_dir or not os.path.isdir(account_dir):
        return []
    out: List[str] = []
    visited = 0
    stack = [(os.path.join(account_dir, s), 0) for s in _SCAN_SUBTREES
             if os.path.isdir(os.path.join(account_dir, s))]
    while stack:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    visited += 1
                    if visited > max_entries:
                        logger.warning("扫描条目超上限(%d)，提前结束：%s", max_entries, d)
                        stack.clear()
                        break
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth + 1 < max_depth:
                                stack.append((e.path, depth + 1))
                        elif e.name.lower().endswith(".db"):
                            out.append(e.path)
                    except OSError:
                        continue
        except OSError as e:
            logger.debug("扫描失败 %s: %s", d, e)
    try:
        with os.scandir(account_dir) as it:
            for e in it:
                try:
                    if e.is_file(follow_symlinks=False) and e.name.lower().endswith(".db"):
                        out.append(e.path)
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(set(out))


def list_db_files(account_dir: str) -> List[str]:
    """账号目录下全部 .db（含 contact/session/media 等非消息库）。"""
    return scan_dbs(account_dir)


def count_dbs_under_root(root: str) -> int:
    """统计某个根下全部 .db 数量（账号目录走快速扫描，其余小目录才递归 glob）。"""
    if not os.path.isdir(root):
        return 0
    total = 0
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return 0
    for n in names:
        p = os.path.join(root, n)
        if not os.path.isdir(p):
            continue
        if detect_layout(p) != "unknown":
            total += len(scan_dbs(p))
        else:
            try:
                total += sum(1 for q in glob.glob(os.path.join(p, "**", "*.db"),
                                                  recursive=True) if os.path.isfile(q))
            except Exception:  # noqa: BLE001
                pass
    return total


def list_message_dbs(account_dir: str, family: str) -> List[str]:
    """账号目录下的消息分片库。"""
    spec = version_spec("", family)
    hits: List[str] = []
    for pat in spec["message_glob"]:
        hits.extend(glob.glob(os.path.join(account_dir, pat.replace("/", os.sep))))
    return sorted(set(hits))


def find_contact_db(account_dir: str, family: str) -> str:
    """账号目录下的联系人库路径（找不到返回 ""）。"""
    if family == "3.x":
        p = os.path.join(account_dir, "Msg", "MicroMsg.db")
        return p if os.path.exists(p) else ""
    d = os.path.join(account_dir, "db_storage", "contact")
    if os.path.isdir(d):
        dbs = sorted(glob.glob(os.path.join(d, "*.db")))
        if dbs:
            return dbs[0]
    return ""


def pick_probe_db(account_dir: str, family: str) -> Optional[str]:
    """挑一个"探针库"用于版本/密钥判定。

    4.x 优先 db_storage\\message\\message_0.db，缺失时退到任一 message_*.db；
    3.x 优先 Msg\\Multi\\MSG0.db。都没有时退到账号目录下任一 .db。
    """
    if family == "3.x":
        for rel in (os.path.join("Msg", "Multi", "MSG0.db"), os.path.join("Msg", "MSG0.db")):
            p = os.path.join(account_dir, rel)
            if os.path.exists(p):
                return p
    else:
        p = os.path.join(account_dir, "db_storage", "message", "message_0.db")
        if os.path.exists(p):
            return p
        msgs = list_message_dbs(account_dir, "4.x")
        real = [m for m in msgs if os.path.basename(m).startswith("message_")
                and os.path.basename(m)[8:9].isdigit()]
        if real:
            return real[0]
    all_db = list_db_files(account_dir)
    return all_db[0] if all_db else None


def version_spec(version: str, family: str = "") -> Dict[str, Any]:
    """按版本号/族取适配表条目；未识别时返回兜底条目（4.x）。"""
    v = (version or "").strip()
    for spec in VERSION_ADAPTATION:
        pre = spec.get("prefix") or ""
        if pre and v.startswith(pre):
            return spec
    if family:
        for spec in VERSION_ADAPTATION:
            if spec.get("family") == family and not spec.get("fallback"):
                return spec
    return VERSION_ADAPTATION[-1]


def figure_family(layout: str, version: str = "") -> str:
    """布局 + 版本号 → 版本族。"""
    if layout == "db_storage":
        return "4.x"
    if layout == "Msg":
        return "3.x"
    spec = version_spec(version)
    return spec["family"]


def scan_roots(extra_roots: Optional[Sequence[str]] = None,
               log: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """扫描全部候选根，返回 [{path, exists, kind}]。"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for r in list(default_roots()) + list(extra_roots or []):
        if not r:
            continue
        a = _norm(r)
        if a in seen:
            continue
        seen.add(a)
        exists = os.path.isdir(a)
        kind = root_kind(a) if exists else "unknown"
        out.append({"path": a, "exists": bool(exists), "kind": kind})
        if log is not None:
            log.append(f"[root] {a} exists={exists} kind={kind}")
    return out


def list_accounts(root: str, log: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """列出某个根下的**全部**账号目录（多账号，不取第一个）。"""
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(root):
        return out
    try:
        names = sorted(os.listdir(root))
    except OSError as e:
        if log is not None:
            log.append(f"[root] 读取失败 {root}: {e}")
        return out
    for name in names:
        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        layout = detect_layout(full)
        if layout == "unknown":
            if log is not None:
                log.append(f"[skip] {full}：不含 db_storage/Msg 布局，非账号目录")
            continue
        out.append({"account_id": name, "root": root, "path": full, "layout": layout})
    return out


# ===========================================================================
# 主体
# ===========================================================================
class WechatOneClick:
    """微信聊天记录一键导入总控。

    所有外部依赖都可以注入（默认自建），便于单测与替换：

        key_extractor : services.wechat_key_extractor（模块或同名对象）
        importer      : services.wechat_importer.WeChatImporter 实例
                        （只用它的 launch_wxkey_capture）
        reader_cls    : services.wechat_db_reader.WeChatDBReader
        decryptor     : services.wechat_db_decryptor
        config        : config 服务（dict 风格，可选读 wechat.extra_roots）
    """

    def __init__(self,
                 key_extractor: Any = None,
                 importer: Any = None,
                 reader_cls: Any = None,
                 decryptor: Any = None,
                 config: Any = None,
                 extra_roots: Optional[Sequence[str]] = None) -> None:
        self._kx = key_extractor or self._default_key_extractor()
        self._config = config
        self._importer = importer if importer is not None else self._default_importer()
        self._dec = decryptor or self._default_decryptor()
        self._reader_cls = reader_cls or self._default_reader_cls()
        self._extra_roots = list(extra_roots or [])

        self._lock = threading.RLock()
        self._cancel_evt = threading.Event()
        self._running = False
        self._last_log: List[str] = []
        self._progress: Dict[str, Any] = {
            "stage": "idle", "stage_name": _STAGE_NAMES["idle"], "percentage": 0,
            "account_id": "", "db_index": 0, "db_total": 0, "message_count": 0,
            "running": False,
        }

    # ------------------------------------------------------------------
    # 默认依赖
    # ------------------------------------------------------------------
    @staticmethod
    def _default_key_extractor():
        try:
            from . import wechat_key_extractor as kx
            return kx
        except Exception as e:  # noqa: BLE001
            logger.warning("加载 wechat_key_extractor 失败：%s", e)
            return None

    @staticmethod
    def _default_decryptor():
        try:
            from . import wechat_db_decryptor as dec
            return dec
        except Exception as e:  # noqa: BLE001
            logger.warning("加载 wechat_db_decryptor 失败：%s", e)
            return None

    @staticmethod
    def _default_reader_cls():
        try:
            from .wechat_db_reader import WeChatDBReader
            return WeChatDBReader
        except Exception as e:  # noqa: BLE001
            logger.warning("加载 WeChatDBReader 失败：%s", e)
            return None

    def _default_importer(self):
        """默认自建 WeChatImporter（仅用其 launch_wxkey_capture，不碰 GUI）。"""
        try:
            from .wechat_importer import WeChatImporter
            return WeChatImporter(db=None, config=self._config)
        except Exception as e:  # noqa: BLE001
            logger.warning("加载 WeChatImporter 失败：%s", e)
            return None

    # ------------------------------------------------------------------
    # 配置里的额外根
    # ------------------------------------------------------------------
    def _config_extra_roots(self) -> List[str]:
        out: List[str] = []
        cfg = self._config
        if cfg is None:
            return out
        for key in ("wechat.extra_roots", "wechat.roots", "wechat.data_roots",
                    "wechat.db_roots"):
            try:
                val = cfg.get(key) if hasattr(cfg, "get") else None
            except Exception:  # noqa: BLE001
                val = None
            if isinstance(val, str) and val:
                out.append(val)
            elif isinstance(val, (list, tuple)):
                out.extend([str(v) for v in val if v])
        return out

    def _all_extra_roots(self, extra_roots: Optional[Sequence[str]] = None) -> List[str]:
        return self._extra_roots + list(extra_roots or []) + self._config_extra_roots()

    # ------------------------------------------------------------------
    # 工具探测
    # ------------------------------------------------------------------
    def _tools(self) -> Dict[str, bool]:
        def _exists(*rel):
            return any(os.path.exists(os.path.join(PROJECT_ROOT, *r)) for r in rel)

        def _importable(name):
            try:
                __import__(name)
                return True
            except Exception:  # noqa: BLE001
                return False

        return {
            "wx_key_exe": _exists(("tools", "wx_key", "wx_key.exe")),
            "sqlcipher3": _importable("sqlcipher3") or _importable("pysqlcipher3"),
            "frida": _importable("frida") or _exists(("tools", "frida"),
                                                     ("tools", "frida-server")),
            # WeFlow：外部 Electron 工具。这里"如实反映是否存在"；
            # 它**没有**被自动驱动（产出格式未核实），available=false 见 log。
            "weflow": bool(os.path.isdir(r"F:\tools\weflow_src"))
                      or _exists(("tools", "weflow")),
        }

    def _process_info(self, ttl: float = 5.0) -> Optional[Dict[str, Any]]:
        """探测微信进程（带 TTL 缓存：枚举进程代价不低，同一轮别重复做）。"""
        if self._kx is None:
            return None
        now = time.time()
        cached = getattr(self, "_proc_cache", None)
        if cached and now - cached[0] < ttl:
            return cached[1]
        try:
            info = self._kx.detect_wechat_process()
        except Exception as e:  # noqa: BLE001
            logger.debug("detect_wechat_process 失败：%s", e)
            info = None
        self._proc_cache = (now, info)
        return info

    # ------------------------------------------------------------------
    # 进度
    # ------------------------------------------------------------------
    def _set_progress(self, _cb: Optional[Callable] = None, **kw) -> None:
        with self._lock:
            self._progress.update(kw)
            snap = dict(self._progress)
        if _cb is not None:
            try:
                _cb(snap)
            except Exception as e:  # noqa: BLE001
                logger.debug("progress_cb 异常：%s", e)

    def get_progress(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._progress)

    def cancel(self) -> None:
        """请求取消当前导入（run() 会在下一个检查点安全退出）。"""
        self._cancel_evt.set()
        with self._lock:
            if self._progress.get("running"):
                self._progress["stage"] = "cancelled"
                self._progress["stage_name"] = _STAGE_NAMES["cancelled"]

    def _cancelled(self) -> bool:
        return self._cancel_evt.is_set()

    # ------------------------------------------------------------------
    # 版本识别（b：不依赖微信是否在运行）
    # ------------------------------------------------------------------
    def _exe_version(self) -> tuple:
        """从注册表 InstallPath 找到微信主程序并读文件版本。

        只读注册表（winreg）+ 只读 exe 版本资源；**不依赖微信进程**。
        返回 (version_str, evidence_str)；取不到返回 ("", 原因)。
        """
        paths: List[str] = []
        try:
            import winreg
            for hive, sub, val in (
                (winreg.HKEY_CURRENT_USER, r"Software\Tencent\Weixin", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\Weixin", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Tencent\Weixin", "InstallPath"),
                (winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat", "InstallPath"),
            ):
                try:
                    k = winreg.OpenKey(hive, sub)
                    try:
                        p = winreg.QueryValueEx(k, val)[0]
                    finally:
                        winreg.CloseKey(k)
                    if p:
                        paths.append(str(p))
                except Exception:  # noqa: BLE001
                    continue
        except Exception as e:  # noqa: BLE001
            logger.debug("读注册表失败：%s", e)
        for base in (r"C:\Program Files\Tencent", r"C:\Program Files (x86)\Tencent",
                     r"D:\Program Files\Tencent", r"E:\Program Files\Tencent"):
            if os.path.isdir(base):
                try:
                    for d in os.listdir(base):
                        paths.append(os.path.join(base, d))
                except OSError:
                    pass
        for base in paths:
            for exe in ("Weixin.exe", "WeChat.exe"):
                p = os.path.join(base, exe)
                if not os.path.exists(p):
                    continue
                ver = _read_file_version(p)
                if ver:
                    return ver, f"主程序 {p} 文件版本 {ver}（注册表 InstallPath，只读）"
                return "", f"主程序 {p} 存在但读不到文件版本"
        return "", "未找到微信主程序（注册表/常见安装目录均无），无法从 exe 取版本号"

    def _detect_version(self, acct: Dict[str, Any],
                        probe_db: Optional[str] = None,
                        key: Optional[str] = None,
                        proc_info: Optional[Dict] = None) -> Dict[str, Any]:
        """综合判定版本号与版本族，并产出判定依据 version_evidence。"""
        evidence: List[str] = []
        layout = acct.get("layout") or "unknown"
        family = figure_family(layout)
        evidence.append(f"目录布局：{acct.get('path')} → {layout}（"
                        + ("4.x 的 db_storage 分模块布局" if layout == "db_storage"
                           else "3.x 的 Msg 布局" if layout == "Msg" else "未识别")
                        + "）")

        # 1) exe 文件版本（与进程无关）
        exe_ver, exe_ev = self._exe_version()
        evidence.append(exe_ev)

        # 2) 库文件名
        msg_dbs = list_message_dbs(acct.get("path", ""), family)
        if msg_dbs:
            evidence.append("库文件名：" + ", ".join(os.path.basename(m) for m in msg_dbs[:6])
                            + f"（共 {len(msg_dbs)} 个消息库）")

        # 3) 库文件头页特征
        if probe_db and self._dec is not None:
            try:
                et = self._dec.detect_encryption_type(probe_db)
            except Exception as e:  # noqa: BLE001
                et = f"unknown({e})"
            size = os.path.getsize(probe_db) if os.path.exists(probe_db) else 0
            with open(probe_db, "rb") as f:
                head = f.read(16)
            if head.startswith(b"SQLite format 3\x00"):
                evidence.append(f"库头页：明文 SQLite（{os.path.basename(probe_db)}）")
            else:
                evidence.append(
                    f"库头页：非明文（盐值 {head.hex()[:16]}…），大小 {size} "
                    f"{'是' if size % 4096 == 0 else '不是'} 4096 整数倍 → 加密库")
            # 有密钥时用"能否通过校验"给出最强的族判定
            if key:
                if self._verify(probe_db, key, "4.x"):
                    evidence.append("密钥校验：SQLCipher4（HMAC-SHA512，reserve=80）校验通过 → 4.x 加密族")
                elif self._verify(probe_db, key, "3.x"):
                    evidence.append("密钥校验：wxSQLite3（reserve=48）校验通过 → 3.x 加密族")
                else:
                    evidence.append("密钥校验：4.x / 3.x 两种校验均未通过（密钥与该库不匹配）")

        # 4) 进程信息（若在运行则作为佐证）
        if proc_info:
            evidence.append(f"进程佐证：{proc_info.get('process_name')}（pid={proc_info.get('pid')}）"
                            f" → {proc_info.get('version')}")
        else:
            evidence.append("进程佐证：当前没有运行中的微信进程（不影响本判定，版本识别不依赖进程）")

        # 5) 汇总
        spec = version_spec(exe_ver, family)
        version = exe_ver or ""
        if not version:
            version = f"{spec['family'].split('.')[0]}.x" if not spec.get("fallback") else "4.x(未识别)"
            evidence.append(f"版本号未取到 → 用「{version}」占位（族仍由布局决定：{family}）")
        if spec.get("fallback"):
            evidence.append("该版本号未命中适配表任何一行 → 按 4.x 兜底（fallback）")
        return {"version": version, "version_family": family,
                "version_evidence": evidence, "spec": spec}

    # ------------------------------------------------------------------
    # 密钥
    # ------------------------------------------------------------------
    def _verify(self, db_path: str, key: str, family: str) -> bool:
        """用与版本族匹配的解密器校验密钥。"""
        if self._dec is None or not key:
            return False
        if not os.path.exists(db_path):
            return False
        try:
            if family == "3.x":
                return bool(self._dec.verify_key_wxsqlite3(db_path, key))
            if family == "4.x":
                return bool(self._dec.verify_key_sqlcipher4(db_path, key))
            return bool(self._dec.verify_key_sqlcipher4(db_path, key)
                        or self._dec.verify_key_wxsqlite3(db_path, key))
        except Exception as e:  # noqa: BLE001
            logger.debug("校验密钥异常 %s: %s", db_path, e)
            return False

    @staticmethod
    def _key_source_of(method: str) -> str:
        """把 method 字符串归一到粗粒度来源（前端展示用）。"""
        m = (method or "").lower()
        if m.startswith("cache+"):
            return "per_db_cache"
        if m.startswith("global_cache+"):
            return "global_cache"
        for tag in ("wx_key_log", "key_record", "key_info_db", "registry",
                    "process_args", "wechat_decrypt", "pywxdump", "wechat_dump_rs",
                    "chatlog_alpha"):
            if tag in m:
                return tag
        return m or "unknown"

    def _resolve_cached_key(self, account_id: str, probe_db: Optional[str],
                            family: str) -> Optional[Dict[str, Any]]:
        """尝试从缓存取密钥（**实测校验通过**才算命中）。

        判据是 `get_cached_key(<路径>)` + 解密校验，**不是** os.path.exists：
        `data/wechat_keys/_global.db` 只是逻辑键，密文落在
        `data/wechat_keys/<sha256(路径)[:16]>.key`，字面文件并不存在。
        """
        if not probe_db or self._kx is None:
            return None
        for path, label in ((probe_db, "per_db_cache"), (GLOBAL_CACHE_PATH, "global_cache")):
            try:
                info = self._kx.get_cached_key(path)
            except Exception as e:  # noqa: BLE001
                logger.debug("读缓存失败 %s: %s", path, e)
                info = None
            if info and info.get("key") and self._verify(probe_db, info["key"], family):
                method = f"{label}+{info.get('method') or 'unknown'}"
                return {"key": info["key"], "method": method,
                        "source": self._key_source_of(method)}
        return None

    def _key_state_for(self, acct: Dict[str, Any], probe_db: Optional[str]) -> str:
        """key_state: cached / none（口径与 discover 完全一致）。"""
        family = acct.get("version_family") or figure_family(acct.get("layout", ""))
        return "cached" if self._resolve_cached_key(
            acct.get("account_id", ""), probe_db, family) else "none"

    def _route_key_info_db(self, acct: Dict[str, Any], probe_db: str,
                           family: str, log: List[str],
                           max_candidates: int = 8,
                           time_budget: float = 8.0) -> Optional[Dict[str, Any]]:
        """路线：登录态 key_info.db（只读结构化扫描）。

        `E:\\xwechat_files\\all_users\\login\\<wxid>\\key_info.db` 是**明文** SQLite，
        表 `LoginKeyInfoTable(key_info_data BLOB)` 里带 protobuf 风格的定长字段。
        这里只做**只读**结构化提取（0x69 + uint32 长度 + payload）并逐个过校验，
        不做全量滑窗（那会很慢且无收益）。
        """
        if self._dec is None or not probe_db:
            return None
        login_root = os.path.join(acct.get("root", ""), "all_users", "login")
        if not os.path.isdir(login_root):
            log.append("[key][key_info_db] 跳过：无 all_users/login 目录")
            return None
        core = str(acct.get("account_id", "")).rsplit("_", 1)[0]
        target = None
        for d in sorted(os.listdir(login_root)):
            full = os.path.join(login_root, d)
            if os.path.isdir(full) and (d == core or d.startswith(core) or core.startswith(d)):
                target = full
                break
        if not target:
            log.append(f"[key][key_info_db] 跳过：未找到匹配 {core} 的登录态目录")
            return None
        kid = os.path.join(target, "key_info.db")
        if not os.path.exists(kid):
            log.append(f"[key][key_info_db] 跳过：{kid} 不存在")
            return None
        cands: List[bytes] = []
        seen = set()
        try:
            tmp = os.path.join(COPY_ROOT, "key_info_" + os.path.basename(target) + ".db")
            os.makedirs(COPY_ROOT, exist_ok=True)
            shutil.copyfile(kid, tmp)          # R1：读副本，不碰原库
            con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
            try:
                rows = con.execute("SELECT key_info_data FROM LoginKeyInfoTable").fetchall()
            finally:
                con.close()
            try:
                os.remove(tmp)
            except OSError:
                pass
            for (blob,) in rows:
                blob = bytes(blob or b"")
                for i in range(0, len(blob) - 5):
                    if blob[i] != 0x69:
                        continue
                    ln = struct.unpack_from("<I", blob, i + 1)[0]
                    if ln in (16, 32, 64) and i + 5 + ln <= len(blob):
                        cand = blob[i + 5:i + 5 + ln]
                        if len(cand) == 32 and cand not in seen:
                            seen.add(cand)
                            cands.append(cand)
        except Exception as e:  # noqa: BLE001
            log.append(f"[key][key_info_db] 读取失败：{e}")
            return None
        log.append(f"[key][key_info_db] 只读提取到 {len(cands)} 个候选字段"
                   f"（最多校验 {max_candidates} 个）")
        t0 = time.time()
        for cand in cands[:max_candidates]:
            if time.time() - t0 > time_budget:
                log.append("[key][key_info_db] 超时（时间预算到），停止校验")
                break
            if self._verify(probe_db, cand.hex(), family):
                log.append("[key][key_info_db] 命中：候选字段通过校验")
                return {"ok": True, "key": cand.hex(), "method": "key_info_db",
                        "source": kid}
        log.append("[key][key_info_db] 未命中：key_info.db 内的定长字段均无法解出该库")
        return None

    def _has_cached_key(self, probe_db: Optional[str],
                        family: Optional[str] = None) -> bool:
        """该账号是否**真的**已有可用缓存密钥（→ 就不必现场抓）。

        两条都必须满足，缺一不可：
          1) **不能用 os.path.exists 判缓存有无**（用户实测坑）：全局 passphrase 缓存
             `data\\wechat_keys\\_global.db` 字面并不存在，密文落在由该路径哈希派生的
             `<hash>.key` 里；唯一可信入口是 kx.get_cached_key(<路径>)，它内部做路径哈希。
          2) **不能只看"缓存里有条目"**：全局 passphrase 缓存是**单条**的，可能存的是
             *别的账号* 的密钥。本机实测（evidence_38）：账号 2 用全局缓存条目
             `login_moment_wx_key_dll` 校验**不通过**（日志 `global_cache 有缓存但校验未通过`），
             而它的探针库本该由现场抓取补齐。所以必须拿**该账号的探针库**实测校验
             （verify_key_sqlcipher4 / verify_key_wxsqlite3），校验通过才算"已就绪"。
        """
        if self._kx is None or not probe_db:
            return False
        fam = family or figure_family(probe_db)
        for p in (probe_db, GLOBAL_CACHE_PATH):
            if not p:
                continue
            try:
                info = self._kx.get_cached_key(p)
            except Exception:  # noqa: BLE001
                continue
            if not (info and info.get("key")):
                continue
            try:
                if self._verify(probe_db, info["key"], fam):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _ensure_keyflow(self):
        """懒加载/复用微信密钥现场抓取编排（1–7 步）。不可用时返回 None，不抛。"""
        if getattr(self, "_keyflow", None) is not None:
            return self._keyflow
        try:
            from services.wechat_key_controller import WeChatKeyFlow
        except Exception as e:  # noqa: BLE001
            self._last_log.append(f"[keyflow] 导入失败：{type(e).__name__}: {e}")
            return None
        try:
            self._keyflow = WeChatKeyFlow(key_extractor=self._kx)
        except Exception as e:  # noqa: BLE001
            self._last_log.append(f"[keyflow] 初始化失败：{type(e).__name__}: {e}")
            return None
        return self._keyflow

    def _acquire_key(self, acct: Dict[str, Any], probe_db: Optional[str],
                     consent_high_risk: bool, log: List[str],
                     proc_info: Optional[Dict] = None) -> Dict[str, Any]:
        """取密钥：先缓存（零风险），未命中才走低风险阶梯；高风险需 consent。"""
        family = acct.get("version_family") or figure_family(acct.get("layout", ""))
        if not probe_db:
            return {"ok": False, "reason": "没有可用的探针库，无法取密钥",
                    "next_action": "确认账号目录下存在 message_*.db（4.x）或 MSG*.db（3.x）后再试"}
        if self._kx is None:
            return {"ok": False, "reason": "密钥提取器不可用（wechat_key_extractor 未加载）",
                    "next_action": "检查工程依赖（cryptography）后重试"}

        # ---- 1) 缓存直取（零风险；命中即用，微信没开也能跑）----
        for cache_path, label in ((probe_db, "per_db_cache"), (GLOBAL_CACHE_PATH, "global_cache")):
            try:
                info = self._kx.get_cached_key(cache_path)
            except Exception as e:  # noqa: BLE001
                log.append(f"[key][{label}] 读缓存异常：{e}")
                continue
            if info and info.get("key") and self._verify(probe_db, info["key"], family):
                method = f"{label}+{info.get('method') or 'unknown'}"
                log.append(f"[key][{label}] 命中并通过校验（method={info.get('method')}）")
                return {"ok": True, "key": info["key"], "method": method,
                        "source": self._key_source_of(method)}
            log.append(f"[key][{label}] 未命中" if not info else f"[key][{label}] 有缓存但校验未通过")

        # ---- 2) 低风险阶梯（复用 extract_key_auto 既有顺序，逐步留痕）----
        try:
            res = self._kx.extract_key_auto(
                probe_db, version=family, enable_high_risk=bool(consent_high_risk),
                process_info=proc_info)
        except Exception as e:  # noqa: BLE001
            log.append(f"[key][extract_key_auto] 异常：{e}")
            res = {"ok": False, "key": None, "method": None, "log": []}
        for entry in (res.get("log") or []):
            log.append(f"[key][{entry.get('method')}] ok={entry.get('ok')}"
                       + (f" reason={entry.get('reason')}" if entry.get("reason") else "")
                       + (f" via={entry.get('via')}" if entry.get("via") else ""))
        if res.get("ok") and res.get("key"):
            method = res.get("method") or "unknown"
            log.append(f"[key] 阶梯命中：method={method}")
            return {"ok": True, "key": res["key"], "method": method,
                    "source": self._key_source_of(method)}

        # ---- 3) 额外只读路线：登录态 key_info.db ----
        extra = self._route_key_info_db(acct, probe_db, family, log)
        if extra and extra.get("ok"):
            return {"ok": True, "key": extra["key"], "method": extra["method"],
                    "source": "key_info_db"}

        # ---- 4) 失败：给人话原因 + next_action（绝不静默失败）----
        if consent_high_risk:
            reason = ("低风险路线全部未命中；已按 consent_high_risk=True 尝试内存扫描类路线，"
                      "但仍未取到密钥（微信未运行或密钥未在内存中）")
            next_action = ("可调用 wechat_oneclick_acquire_key_high_risk() 拉起 wx_key 抓取窗口："
                           "在窗口内完成一次微信登录后即会回收密钥（正常结束会自动卸载/清理；若中途异常退出，微信可能需要重新启动）")
        elif proc_info:
            reason = "低风险路线未命中（有微信进程，但内存/参数/配置中都没有可用密钥）"
            next_action = ("若确认需要注入路线，请在界面勾选「高风险：知情同意」后重试；"
                           "或在窗口内先完成一次 wx_key 抓取再重试本操作")
        else:
            reason = ("未命中密钥缓存，且微信当前未运行 → 只读内存/参数类路线无法尝试；"
                      "低风险路线全部失败")
            next_action = ("① 先开启微信并登录一次，再重试（可命中内存只读路线）；"
                           "② 或勾选「高风险：知情同意」后调用 "
                           "wechat_oneclick_acquire_key_high_risk() 用 wx_key 抓一次密钥"
                           "（正常结束会自动卸载/清理；若中途异常退出，微信可能需要重新启动）")
        return {"ok": False, "reason": reason, "next_action": next_action}

    # ------------------------------------------------------------------
    # 单库解密（R1：源库只读，只解密副本）
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 明文副本可用性判据（第②条修复：去掉尺寸比值闸门）
    # ------------------------------------------------------------------
    @staticmethod
    def _is_shard_db_name(path: str) -> bool:
        """分片式消息库名：`message_<n>.db` / `biz_message_<n>.db`（末段纯数字）。

        `message_fts.db` / `message_resource.db` / `media_0.db` / `weclaw.db` 都**不算**，
        免得把合法库误判成"残片"→ 每轮重解（那正是本轮要根除的故障类型）。
        """
        head, ext = os.path.splitext(os.path.basename(path).lower())
        return bool(ext == ".db"
                    and (head.startswith("biz_message_") or head.startswith("message_"))
                    and head.rsplit("_", 1)[-1].isdigit())

    @staticmethod
    def _deep_copy_shape_ok(out_path: str) -> Dict[str, Any]:
        """**便宜且稳**的形状检查：只读 `sqlite_master` 取表名，**不碰 `integrity_check`**。

        为什么必须与 integrity 解耦（F-1 实测）：微信的 fts 库建了
        `fts5(tokenize='MMFtsTokenizer …')` 虚拟表，而 stdlib sqlite3 的 fts5 模块里
        没有这个分词器 → 对它跑 `PRAGMA integrity_check` **必然**
        `OperationalError: SQL logic error`。若把这种"环境原因"当成"副本损坏"，
        就会变成"每轮重解 + 计为失败"的**新的非收敛故障**。
        读表名不需要实例化虚拟表，所以不受分词器缺失影响。

        判定：**分片式库名**必须至少有一张 `Msg_` 分片表；其它库名读得出表名即算过
        （空库不算错——`weclaw.db` 本来就只有 1 页、一张表都没有）。
        """
        r: Dict[str, Any] = {"ok": False, "reason": "", "tables": None, "msg_shards": 0}
        try:
            con = sqlite3.connect("file:%s?mode=ro" % out_path.replace("\\", "/"), uri=True)
        except Exception as e:  # noqa: BLE001
            r["reason"] = "打不开：%s: %s" % (type(e).__name__, e)
            return r
        try:
            tabs = [x[0] for x in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
        except Exception as e:  # noqa: BLE001
            r["reason"] = "读表名失败：%s: %s" % (type(e).__name__, e)
            return r
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        r["tables"] = len(tabs)
        r["msg_shards"] = sum(1 for t in tabs if str(t).lower().startswith("msg_"))
        if WechatOneClick._is_shard_db_name(out_path) and not r["msg_shards"]:
            r["reason"] = ("分片消息库但副本内没有 Msg_ 分片表（表名示例：%s）" % (tabs[:3],))
            return r
        r["ok"] = True
        r["reason"] = "表结构形状符合（表 %d 张、Msg_ 分片 %d 张）" % (r["tables"], r["msg_shards"])
        return r

    @staticmethod
    def _deep_copy_content_ok(out_path: str) -> Dict[str, Any]:
        """可疑小比值档的**加重**检查（只读）。
        返回 {ok, available, reason, tables, msg_shards, error}。

        铁律：**「深检不可用」≠「副本不可用」**（F-1）。
          * 形状（表名）永远先查，**形状不过才判失败**——那才是真残片的特征；
          * `integrity_check` / `page_size` / `page_count` 若因**环境原因**抛错
            （缺分词器、虚拟表、模块未注册…），判 `ok=True, available=False`
            （深检不可用），**绝不因此判副本不可用、绝不触发重解、绝不报失败**；
          * 只有 `integrity_check` **明确返回非 ok**、或页数与文件大小不符，才判失败。

        注意：那个 8192 B 残片的识别力**不来自 integrity**（它 integrity 恰好是 ok），
        而来自 `_deep_copy_shape_ok` 的分片表要求 —— 这也是 F-2 把它**无条件化**的原因。
        """
        r: Dict[str, Any] = {"ok": False, "available": False, "reason": "",
                             "tables": None, "msg_shards": 0, "error": ""}
        shape = WechatOneClick._deep_copy_shape_ok(out_path)
        r["tables"] = shape.get("tables")
        r["msg_shards"] = shape.get("msg_shards")
        if not shape.get("ok"):
            r["reason"] = shape.get("reason", "")
            return r
        try:
            con = sqlite3.connect("file:%s?mode=ro" % out_path.replace("\\", "/"), uri=True)
        except Exception as e:  # noqa: BLE001
            r.update({"ok": True, "available": False,
                      "error": "%s: %s" % (type(e).__name__, e)})
            r["reason"] = "深检不可用（打不开：%s），仅形状判据通过" % r["error"]
            return r
        try:
            try:
                ic = con.execute("PRAGMA integrity_check").fetchone()
            except Exception as e:  # noqa: BLE001  ← F-1：fts5 分词器缺失必然落这里
                r.update({"ok": True, "available": False,
                          "error": "%s: %s" % (type(e).__name__, e)})
                r["reason"] = ("深检不可用（integrity_check 无法执行：%s），"
                               "仅形状判据通过；**不因此判副本不可用**" % r["error"])
                return r
            if not ic or str(ic[0]).lower() != "ok":
                r["reason"] = "integrity_check=%s" % (ic,)
                return r
            try:
                ps = con.execute("PRAGMA page_size").fetchone()[0]
                pc = con.execute("PRAGMA page_count").fetchone()[0]
            except Exception as e:  # noqa: BLE001
                r.update({"ok": True, "available": False,
                          "error": "%s: %s" % (type(e).__name__, e)})
                r["reason"] = "深检不可用（页数读不到：%s），仅形状判据通过" % r["error"]
                return r
            size = os.path.getsize(out_path)
            if ps * pc != size:
                r["reason"] = "页数与文件大小不符（%d x %d != %d）" % (ps, pc, size)
                return r
            r.update({"ok": True, "available": True})
            r["reason"] = ("integrity ok、页数自洽、形状符合（表 %d 张、Msg_ 分片 %d 张）"
                           % (r["tables"], r["msg_shards"]))
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
        return r

    @staticmethod
    def _plaintext_copy_state(out_path: str, src_db: str) -> Dict[str, Any]:
        """返回已有明文副本的可用性判据结果（dict）；「复用」与「重解后复检」共用同一把尺。

        **为什么换掉旧判据**（2026-10-17 实测）：旧的第 ③ 重闸门要求
        「副本 / 源 尺寸比值落在 [0.5, 1.05]」，在 `message_5.db` 上**永不收敛**——
        源 10,485,760 B、解密副本 1,519,616 B，比值 0.145 < 0.5，于是每一轮都判
        「副本不可信」→ 每轮覆盖重解；可该副本 `PRAGMA integrity_check=ok`、
        `freelist_count=0`、头部自洽（371 页 x 4096 = 1,519,616 = 文件大小），
        本身完全可用。根因：解密是 sqlcipher 逐页导出并整理，源库里的自由页会被丢弃，
        明文页数天然可远小于密文页数，**比值不是关闭的判据**。

        现判据（可证伪，且仍能挡住历史上真实出现过的坏副本）：
          1) 存在且不小于 1024 B；
          2) SQLite 明文魔数（'SQLite format 3' 后跟一个 0x00 字节）；
          3) **头部自洽**：page_size x page_count == 文件大小
             —— 截断的半成品必然违反；page_count 为 0（WAL 滞后）时改用只读 PRAGMA 实测；
          4) 只保留上界：副本大于加密源的 1.05 倍 -> 不可用（比源还大，不像这个库的东西）。
             **下界（比值）已删除**，理由见上；
          5) 新鲜度：副本 mtime 早于加密源 mtime -> 不可用（可能缺最新消息）；
          6) **形状检查（无条件，不挂比值门）**：分片式库名
             （`message_<n>.db` / `biz_message_<n>.db`）必须至少含一张 `Msg_` 分片表。
             理由（F-2 实测）：同一个 8192 B、头部自洽、只带旧式 `Message` 表的残片，
             只要把"源"换成 10,000 B（比值 0.819 ≥ 0.5），"只在比值<0.5 才查形状"的写法
             就会 `usable=True` 误收。故形状必须**无条件**查。
             这条只看表名、**不碰 integrity_check**，因此不受微信 fts5 分词器缺失影响。
          7) 可疑小比值档（比值 < 0.5）**再加** `integrity_check` 等加重检查；
             并且 **「深检不可用」（缺分词器 / 虚拟表等环境原因）不算「副本不可用」**，
             绝不因此判失败或触发重解（F-1：微信 fts 库跑 integrity_check 必然
             `SQL logic error`，`favorite_fts.db` 比值只有 0.579，余量仅 −0.079）。
        """
        info: Dict[str, Any] = {"usable": False, "reason": "", "path": out_path}
        try:
            size = os.path.getsize(out_path)
        except OSError:
            info["reason"] = "副本不存在"
            return info
        info["size"] = size
        if size < 1024:
            info["reason"] = "副本过小（%d B）" % size
            return info
        try:
            with open(out_path, "rb") as fh:
                head = fh.read(100)
        except OSError as e:
            info["reason"] = "副本不可读：%s" % e
            return info
        if len(head) < 100 or head[:16] != b"SQLite format 3\x00":
            info["reason"] = "不是 SQLite 明文库（魔数不符）"
            return info
        page_size = struct.unpack(">H", head[16:18])[0]
        if page_size == 1:
            page_size = 65536
        if page_size <= 0:
            info["reason"] = "头页 page_size 非法"
            return info
        pages = struct.unpack(">I", head[28:32])[0]
        info["page_size"] = page_size
        info["page_count"] = pages
        info["declared"] = page_size * pages
        if pages > 0:
            if page_size * pages != size:
                info["reason"] = ("头部不自洽：page_count x page_size = %d 不等于文件大小 %d"
                                  % (page_size * pages, size))
                return info
        else:
            try:
                con = sqlite3.connect("file:%s?mode=ro" % out_path.replace("\\", "/"), uri=True)
                try:
                    pc = con.execute("PRAGMA page_count").fetchone()[0]
                    con.execute("SELECT count(*) FROM sqlite_master").fetchone()
                finally:
                    con.close()
                info["page_count"] = pc
                if pc <= 0:
                    info["reason"] = "头页 page_count=0 且实测仍为 0"
                    return info
            except Exception as e:  # noqa: BLE001
                info["reason"] = "头页 page_count=0 且实测打不开：%s: %s" % (type(e).__name__, e)
                return info
        try:
            src_size = os.path.getsize(src_db)
            src_mtime = os.path.getmtime(src_db)
        except OSError:
            src_size, src_mtime = 0, 0.0
        if src_size:
            info["src_size"] = src_size
            info["ratio"] = round(size / src_size, 3)
            if size > src_size * 1.05:
                info["reason"] = "副本比加密源还大（%d > %d x 1.05）" % (size, src_size)
                return info
        # 6) 形状检查：**无条件**做（分片式库名必须含 Msg_ 分片表）。
        #    不能再挂在小比值门上：把「源」换成 10,000 B 让比值凑到 0.819，
        #    同一个 8192 B 残片就会被误收（F-2 实测）。只看表名、不碰 integrity_check，
        #    所以不受微信 fts5 分词器缺失（F-1）影响。
        if WechatOneClick._is_shard_db_name(out_path):
            shape = WechatOneClick._deep_copy_shape_ok(out_path)
            info["shape"] = bool(shape.get("ok"))
            info["shape_tables"] = shape.get("tables")
            info["shape_msg_shards"] = shape.get("msg_shards")
            if not shape.get("ok"):
                info["reason"] = "分片库形状检查未通过：%s" % shape.get("reason")
                return info
        # 7) 可疑小比值档（< 0.5）再加内容级深检；「深检不可用」**不算**「副本不可用」。
        if info.get("ratio") is not None and info["ratio"] < 0.5:
            deep = WechatOneClick._deep_copy_content_ok(out_path)
            info["deep"] = bool(deep.get("ok"))
            info["deep_available"] = deep.get("available")
            info["deep_tables"] = deep.get("tables")
            info["deep_msg_shards"] = deep.get("msg_shards")
            if deep.get("error"):
                info["deep_error"] = deep["error"]
            if not deep.get("ok"):
                info["reason"] = "小比值副本未通过内容深检：%s" % deep.get("reason")
                return info
        if src_mtime:
            try:
                out_mtime = os.path.getmtime(out_path)
            except OSError:
                out_mtime = 0.0
            info["out_mtime"] = out_mtime
            info["src_mtime"] = src_mtime
            if out_mtime and out_mtime < src_mtime:
                info["reason"] = "副本早于加密源（可能缺最新消息，需重解）"
                return info
        info["usable"] = True
        info["reason"] = "头部自洽、不早于加密源" + ("、形状符合" if info.get("shape") else "")
        return info

    @staticmethod
    def _plaintext_copy_usable(out_path: str, src_db: str) -> bool:
        """薄封装，保留旧签名（兼容外部/测试调用）；判据见 _plaintext_copy_state。"""
        return bool(WechatOneClick._plaintext_copy_state(out_path, src_db).get("usable"))

    def _decrypt_one(self, src_db: str, key: str, family: str,
                     out_path: str) -> Dict[str, Any]:
        """把一个加密库解密到 out_path。源库只读，先复制副本再解密。"""
        if self._dec is None:
            return {"ok": False, "reason": "解密器不可用（wechat_db_decryptor 未加载）"}
        copy_rejected = ""
        if os.path.exists(out_path):
            state = self._plaintext_copy_state(out_path, src_db)
            if state.get("usable"):
                return {"ok": True, "reason": "复用已解密的明文副本", "reused": True,
                        "verify": {k: state.get(k) for k in
                                   ("reason", "size", "src_size", "ratio",
                                    "page_size", "page_count", "declared")}}
            # 副本不可用（截断/半成品/不是这个库的东西/早于源库）→ 覆盖重解。
            # 原因**显式带出去**（返回值 copy_rejected + 调用方写进 decrypt_notes/log），
            # 不再只丢一条 stderr 告警。
            copy_rejected = state.get("reason", "未知原因")
            logger.warning("明文副本不可用（%s），将覆盖重解：%s", copy_rejected, out_path)
        if not os.path.exists(src_db):
            return {"ok": False, "reason": "源库不存在"}
        # 加密类型：按版本族选定，再实测校验
        et = "sqlcipher4" if family != "3.x" else "wxsqlite3"
        try:
            detected = self._dec.detect_encryption_type(src_db)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": f"无法判定加密类型：{e}"}
        if detected == "plain":
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            shutil.copyfile(src_db, out_path)
            return {"ok": True, "reason": "明文库，直接复制", "plain": True}
        if not self._verify(src_db, key, family):
            return {"ok": False,
                    "reason": f"密钥校验未通过（{os.path.basename(src_db)}，"
                              f"族={family}，detect={detected}）"}
        tmp_copy = os.path.join(COPY_ROOT, os.path.basename(src_db))
        os.makedirs(COPY_ROOT, exist_ok=True)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        out_tmp = out_path + ".part"
        try:
            shutil.copyfile(src_db, tmp_copy)          # R1：只复制副本
            self._dec.decrypt_db(tmp_copy, key, out_tmp, encryption_type=et)
            os.replace(out_tmp, out_path)
            post = self._plaintext_copy_state(out_path, src_db)
            out = {"ok": True, "reason": "解密成功",
                   "verify": {k: post.get(k) for k in
                              ("reason", "size", "src_size", "ratio",
                               "page_size", "page_count", "declared")}}
            if copy_rejected:
                out["copy_rejected"] = copy_rejected
            if not post.get("usable"):
                # 不把不达标产物静默计为成功：返回 ok=False，调用方计入 dbs_failed + errors
                return {"ok": False,
                        "reason": "解密产物未通过副本校验：%s" % post.get("reason"),
                        "verify": out["verify"], "decrypted": True,
                        "copy_rejected": copy_rejected}
            return out
        except Exception as e:  # noqa: BLE001
            for p in (out_tmp, out_tmp + "-wal", out_tmp + "-shm"):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            return {"ok": False, "reason": f"解密异常：{type(e).__name__}: {e}"}
        finally:
            if os.path.exists(tmp_copy):
                try:
                    os.remove(tmp_copy)
                except OSError:
                    pass

    def _out_path_for(self, acct_path: str, family: str, src_db: str) -> str:
        """与 WeChatDBReader 的 work_dir 布局保持一致，便于其直接复用明文副本。"""
        rel = os.path.relpath(src_db, acct_path)
        return os.path.join(WORK_ROOT, family, rel)

    def _count_decrypted_msg_rows(self, acct_path: str, family: str,
                                  src_list: Optional[Sequence[str]] = None) -> int:
        """直接统计解密副本里全部 `Msg%` 表的行数 = 该账号真实的消息行数。

        Why：这是一条**独立于读取器自述**的实测口径，用于和 read_messages() 的产出对账。
        2026-10-17 本轮之前，`wechat_db_reader.read_messages()` 有三处已实测缺陷
        （无过滤时 SQL 语法错→兜底分支无游标→无限重放；每库只探测一张消息表、在非消息库
        上误选表；分片库发现漏了 biz_message_*.db）——那些已在 reader 内修掉，本方法
        保留为对账手段：两条路径应当相等，不等即视为漏读/重复的信号。
        本方法不依赖读取器的任何自述，只对**已解密的明文副本**做 `count(*)`：
          * 只读打开（`mode=ro`），不触碰 `E:\\` 下的加密源库；
          * 只统计文件名以 `message` / `biz_message` 开头的库，避免把
            `MessageResourceInfo` 之类非消息表算进来；
          * 单表/单库失败不影响其它库，返回已累计的真实值。
        """
        total = 0
        try:
            dbs = [d for d in (src_list or [])]
            if not dbs:
                for sub in ("message", "biz_message"):
                    d = os.path.join(acct_path, "db_storage", sub)
                    if os.path.isdir(d):
                        dbs += [os.path.join(d, f) for f in sorted(os.listdir(d))
                                if f.endswith(".db") and f.lower().startswith(("message", "biz_message"))]
            for src in dbs:
                base = os.path.basename(src).lower()
                if not (base.startswith("message") or base.startswith("biz_message")):
                    continue
                plain = self._out_path_for(acct_path, family, src)
                if not os.path.exists(plain):
                    continue
                conn = None
                try:
                    conn = sqlite3.connect("file:%s?mode=ro" % plain.replace("\\", "/"),
                                           uri=True)
                    tabs = [r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND upper(name) LIKE 'MSG%'")]
                    for t in tabs:
                        try:
                            total += conn.execute(
                                'SELECT count(*) FROM "%s"' % t).fetchone()[0]
                        except Exception:  # noqa: BLE001
                            continue
                except Exception as e:  # noqa: BLE001
                    logger.debug("统计 %s 行数失败：%s", plain, e)
                    continue
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:  # noqa: BLE001
                            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("统计消息行数失败：%s", e)
            return 0
        return total

    def _self_display(self, acct: Dict[str, Any], key: Optional[str]) -> str:
        """尽量取账号自己的昵称作为展示名；取不到就回退目录名。

        只读：解密副本落在临时目录，用完即删。
        """
        account_id = acct.get("account_id", "")
        contact_db = acct.get("contact_db") or ""
        if not key or not contact_db or not os.path.exists(contact_db):
            return account_id
        try:
            if os.path.getsize(contact_db) > 32 * 1024 * 1024:
                return account_id
            tmpdir = os.path.join(WORK_ROOT, "_probe")
            os.makedirs(tmpdir, exist_ok=True)
            out = os.path.join(tmpdir, "contact_" + account_id + ".db")
            r = self._decrypt_one(contact_db, key, acct.get("version_family", "4.x"), out)
            if not r.get("ok"):
                return account_id
            con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
            try:
                core = account_id.rsplit("_", 1)[0]
                for t in ("contact", "Contact", "userinfo"):
                    try:
                        cols = [r2[1] for r2 in con.execute(f"PRAGMA table_info({t})")]
                    except Exception:  # noqa: BLE001
                        continue
                    low = {c.lower(): c for c in cols}
                    id_col = low.get("username") or low.get("user_name") or low.get("wxid")
                    name_col = (low.get("nick_name") or low.get("nickname")
                                or low.get("remark") or low.get("nick"))
                    if not id_col or not name_col:
                        continue
                    for row in con.execute(
                            f'SELECT "{id_col}" AS u, "{name_col}" AS n FROM "{t}"').fetchall():
                        if row[0] and (row[0] == account_id or str(row[0]).startswith(core)):
                            if row[1]:
                                return str(row[1])
            finally:
                con.close()
                try:
                    os.remove(out)
                except OSError:
                    pass
        except Exception as e:  # noqa: BLE001
            logger.debug("取账号昵称失败：%s", e)
        return account_id

    # ------------------------------------------------------------------
    # discover
    # ------------------------------------------------------------------
    def discover(self, extra_roots: Optional[List[str]] = None) -> Dict[str, Any]:
        """发现库：扫全部候选根 + 列出**全部**账号 + 给出版本/密码状态。

        Returns:
            {"ok", "roots_scanned":[{"path","exists","kind"}],
             "accounts":[{"account_id","display","root","layout","version",
                          "version_family","version_evidence","db_count",
                          "message_db_count","contact_db","importable",
                          "key_state","reason"}],
             "tools":{...}, "total_db":int, "log":[str]}
        """
        log: List[str] = []
        log.append("[discover] 开始发现微信数据库（全程只读）")
        roots = scan_roots(self._all_extra_roots(extra_roots), log)

        tools = self._tools()
        log.append(f"[tools] wx_key_exe={tools['wx_key_exe']} sqlcipher3={tools['sqlcipher3']} "
                   f"frida={tools['frida']} weflow={tools['weflow']}")
        for rt in TOOL_ROUTES:
            note = rt.get("note") or ""
            log.append(f"[route] {rt['id']}({rt['name']}) risk={rt['risk']} auto={rt['auto']}"
                       + (f" available=false note={note}" if (not rt["auto"] and note) else ""))

        proc_info = self._process_info()
        log.append(f"[proc] detect_wechat_process -> "
                   f"{'未运行' if not proc_info else proc_info}")

        accounts: List[Dict[str, Any]] = []
        total_db = 0
        for r in roots:
            if not r["exists"]:
                continue
            total_db += count_dbs_under_root(r["path"])
            for a in list_accounts(r["path"], log):
                a["version_family"] = figure_family(a["layout"])
                accounts.append(a)
        log.append(f"[discover] 候选根 {len(roots)} 个，账号 {len(accounts)} 个，"
                   f"全部 .db 合计 {total_db} 个")

        out_accounts: List[Dict[str, Any]] = []
        for a in accounts:
            path = a["path"]
            family = a["version_family"]
            dbs = list_db_files(path)
            msg_dbs = [m for m in list_message_dbs(path, family)
                       if os.path.basename(m).startswith(("message_", "MSG"))]
            contact_db = find_contact_db(path, family)
            probe = pick_probe_db(path, family)
            # 密钥只解析一次：key_state / 版本校验 / 展示名 全部复用同一结果
            cached = self._resolve_cached_key(a["account_id"], probe, family)
            key_state = "cached" if cached else "none"
            cached_key = (cached or {}).get("key")
            ver = self._detect_version(a, probe, cached_key, proc_info)
            display = self._self_display({"account_id": a["account_id"],
                                          "contact_db": contact_db,
                                          "version_family": family}, cached_key)
            importable = bool(dbs) and a["layout"] in LAYOUT_DIRS
            if importable and key_state == "cached":
                reason = (f"布局 {a['layout']} 已识别，{len(dbs)} 个库；密钥已命中缓存并通过校验，"
                          f"可在微信未运行的情况下直接导入")
            elif importable:
                reason = (f"布局 {a['layout']} 已识别，{len(dbs)} 个库；但未命中密钥缓存"
                          f"（账号专属 passphrase 未缓存），低风险路线无可用密钥")
            else:
                reason = "未识别出可导入的库布局（既无 db_storage 也无 Msg）"
            out_accounts.append({
                "account_id": a["account_id"],
                "display": display,
                "root": a["root"],
                "layout": a["layout"],
                "version": ver["version"],
                "version_family": ver["version_family"],
                "version_evidence": ver["version_evidence"],
                "db_count": len(dbs),
                "message_db_count": len(msg_dbs),
                "contact_db": contact_db,
                "importable": importable,
                "key_state": key_state,
                "reason": reason,
            })
            log.append(f"[account] {a['account_id']} display={display} layout={a['layout']} "
                       f"version={ver['version']} family={ver['version_family']} "
                       f"db={len(dbs)} msg_db={len(msg_dbs)} key_state={key_state} "
                       f"importable={importable}")
        self._last_log = log
        self._set_progress(stage="idle", stage_name=_STAGE_NAMES["idle"], percentage=0,
                           running=False)
        return {"ok": True, "roots_scanned": roots, "accounts": out_accounts,
                "tools": tools, "total_db": total_db, "log": log}

    # ------------------------------------------------------------------
    # 单账号导入（collect 的核心）
    # ------------------------------------------------------------------
    def _import_account(self, acct: Dict[str, Any],
                        contact_filter: Optional[Sequence[str]],
                        incremental: bool,
                        consent_high_risk: bool,
                        log: List[str],
                        progress_cb: Optional[Callable],
                        collect_messages: bool,
                        max_messages: Optional[int],
                        pct_base: float, pct_span: float,
                        acquire_key_if_missing: bool = False,
                        consent_injection: bool = False) -> Dict[str, Any]:
        account_id = acct.get("account_id", "")
        family = acct.get("version_family") or figure_family(acct.get("layout", ""))
        path = acct.get("path") or os.path.join(acct.get("root", ""), account_id)
        result = {"account_id": account_id, "version_family": family,
                  "key_method": "", "key_source": "", "dbs_total": 0, "dbs_ok": 0,
                  "dbs_failed": 0, "messages": [], "message_count": 0,
                  "decrypt_notes": [], "errors": []}

        # ---- 1) 枚举该账号**全部** db ----
        dbs = [p for p in list_db_files(path) if os.path.getsize(p) > 0]
        result["dbs_total"] = len(dbs)
        result["db_ok_list"] = []          # 内部用：成功解密的库（供消息读取）
        if not dbs:
            result["errors"].append({"account_id": account_id, "step": "discover_dbs",
                                     "reason": "账号目录下没有任何 .db",
                                     "next_action": "确认微信数据目录是否正确，或先在微信里产生聊天记录"})
            return result

        probe = pick_probe_db(path, family)
        self._set_progress(progress_cb, stage="key", stage_name=_STAGE_NAMES["key"],
                           account_id=account_id, db_index=0, db_total=len(dbs),
                           percentage=int(pct_base))
        log.append(f"[account:{account_id}] 账号 {len(dbs)} 个库，探针库={probe}，版本族={family}")

        # ---- 1.5) 无缓存密钥时的「现场抓一次」（1–7 步）------------------------
        # 用户口径：没有缓存密钥的账号，正是要靠这个工具现场抓一次，抓完照样解密入库。
        # 红线：注入必须显式知情同意；未给同意时不关微信、不注入，只把原因写进 log，
        #       然后照常往下走（下面的 _acquire_key 会给出正式的人话原因 + next_action）。
        if acquire_key_if_missing and not self._has_cached_key(probe, family):
            log.append(f"[account:{account_id}] 无缓存密钥 → 尝试现场抓取"
                       f"（consent_injection={consent_injection}）")
            try:
                flow = self._ensure_keyflow()
                if flow is None:
                    log.append("[keyflow] 编排不可用（services/wechat_key_controller 未加载），跳过")
                else:
                    kr = flow.run(consent_injection=bool(consent_injection),
                                  auto_restart=True, allow_auto_login_click=True)
                    kd = kr.get("data") if isinstance(kr, dict) else None
                    if isinstance(kd, dict):
                        log.append(f"[keyflow] key_acquired={kd.get('key_acquired')} "
                                   f"cleanup_done={kd.get('cleanup_done')} "
                                   f"restarted={kd.get('wechat_restarted')}")
                        for e in kd.get("errors") or []:
                            log.append(f"[keyflow][{e.get('stage')}] {e.get('reason')} "
                                       f"→ 下一步：{e.get('next_action')}")
            except Exception as e:  # noqa: BLE001
                log.append(f"[keyflow] 现场抓取异常（已忽略，继续走常规取密钥）："
                           f"{type(e).__name__}: {e}")

        # ---- 2) 取密钥（同一账号只取一次，全部库复用同一个 passphrase）----
        proc_info = self._process_info()
        key_info = self._acquire_key(acct, probe, consent_high_risk, log, proc_info)
        if not key_info.get("ok"):
            result["errors"].append({
                "account_id": account_id, "step": "acquire_key",
                "reason": key_info.get("reason", "密钥提取失败"),
                "next_action": key_info.get("next_action", "重试或改走高风险路线（需显式同意）"),
            })
            return result
        key = key_info["key"]
        result["key_method"] = key_info.get("method", "")
        result["key_source"] = key_info.get("source", "")
        log.append(f"[account:{account_id}] 密钥就绪：method={result['key_method']} "
                   f"source={result['key_source']}")

        # ---- 3) 批量解密该账号全部库（逐库进度 + 失败原因）----
        for i, src in enumerate(dbs):
            if self._cancelled():
                log.append(f"[account:{account_id}] 已取消，停止解密")
                break
            out = self._out_path_for(path, family, src)
            r = self._decrypt_one(src, key, family, out)
            if r.get("ok"):
                result["dbs_ok"] += 1
                result["db_ok_list"].append(src)
                if r.get("copy_rejected"):
                    note = ("%s：%s（已覆盖重解）"
                            % (os.path.relpath(src, path), r["copy_rejected"]))
                    result["decrypt_notes"].append(note)
                    log.append(f"[decrypt][note] {note}")
            else:
                result["dbs_failed"] += 1
                reason = r.get("reason", "未知原因")
                log.append(f"[decrypt][FAIL] {os.path.relpath(src, path)} → {reason}")
                result["errors"].append({
                    "account_id": account_id, "step": "decrypt",
                    "reason": f"{os.path.relpath(src, path)}：{reason}",
                    "next_action": "该库可跳过；若为消息库缺失，请确认微信已完整登录过该账号",
                })
            self._set_progress(
                progress_cb, stage="decrypt", stage_name=_STAGE_NAMES["decrypt"],
                account_id=account_id, db_index=i + 1, db_total=len(dbs),
                percentage=int(pct_base + pct_span * (i + 1) / max(len(dbs), 1)))
        log.append(f"[account:{account_id}] 解密完成：ok={result['dbs_ok']} "
                   f"failed={result['dbs_failed']} / total={result['dbs_total']}")

        # ---- 4) 读消息（复用 WeChatDBReader，读的是上面解出的明文副本）----
        if self._reader_cls is not None and result["dbs_ok"]:
            self._set_progress(progress_cb, stage="read", stage_name=_STAGE_NAMES["read"],
                               account_id=account_id, percentage=int(pct_base + pct_span))
            try:
                reader = self._reader_cls(db_dir=path, key=key, version=family,
                                          work_dir=WORK_ROOT)
                if incremental:
                    it: Iterable[Dict] = reader.get_incremental_messages(
                        contact_filter=contact_filter)
                else:
                    # 不传 start_time：读取器已修好（2026-10-17 本轮），无过滤也能正常
                    # 终止且不漏条。此前为规避它"无过滤时 SQL 拼成
                    # `SELECT * FROM "t" AND "create_time" > ?`（缺 WHERE，语法错）→
                    # 落入无游标兜底分支 → 行数 ≥ batch_size 的表上无限重放"的缺陷，
                    # 这里曾传 start_time=1 硬造一个非空 WHERE。该规避**已移除**：
                    # 传 1 等于隐式丢掉 create_time ≤ 1 的行（0 / 负数时间戳），
                    # 属于为了不挂死而牺牲完整性；修好后没有理由再牺牲。
                    it = reader.read_messages(contact_filter=contact_filter)
                n = 0
                for msg in it:
                    n += 1
                    if collect_messages:
                        if max_messages is None or len(result["messages"]) < max_messages:
                            result["messages"].append(msg)
                    if n % 20000 == 0:
                        self._set_progress(progress_cb, message_count=n)
                    # cancel 响应性：读取阶段可能很长（几十万条），每 2048 条检查一次取消
                    if (n & 0x7FF) == 0 and self._cancelled():
                        log.append(f"[account:{account_id}] 已取消，停止读取（已读 {n} 条）")
                        try:
                            if hasattr(it, "close"):
                                it.close()
                        except Exception:  # noqa: BLE001
                            pass
                        break
                result["message_count"] = n
                if collect_messages and max_messages is not None and n > len(result["messages"]):
                    log.append(f"[account:{account_id}] 消息数 {n} 超过上限 {max_messages}，"
                               f"已按上限截断（仅内部收集用，run() 的计数不受影响）")
                log.append(f"[account:{account_id}] 读出消息 {n} 条"
                           + (f"（已收集 {len(result['messages'])} 条）" if collect_messages else ""))
                # ---- 计数交叉校验（诚实计数，不靠自述）----------------------------
                # 两条独立路径各算一遍，必须一致：
                #   * 上面 read_messages() 的产出条数 n（读取器自述）
                #   * 这里 _count_decrypted_msg_rows() 直接对解密副本全部 Msg% 表 count(*)
                # 2026-10-17 本轮之前，两者差到只剩 0.8%（8180 vs 1,009,204），原因已定位并
                # 在 services/wechat_db_reader.py 内修掉：
                #   ① 无过滤时 WHERE 缺失 → SQL 语法错 → 兜底分支不带游标 → 无限重放（永不终止）；
                #   ② 每库只探测一张表（4.x 实为数十~数百张 Msg_<md5> 分片表），
                #      且在非消息库上会误选 MessageResourceInfo 之类；
                #   ③ 分片库发现漏了 biz_message_*.db。
                # 现在两者应当**相等**；若不等，说明仍有漏读/重复，此处如实报出来，不静默。
                if (not incremental) and not contact_filter:
                    truth = self._count_decrypted_msg_rows(acct_path=path, family=family,
                                                           src_list=result["db_ok_list"])
                    if truth:
                        result["message_count"] = truth
                        verdict = "一致" if truth == n else f"不一致（差 {truth - n:+d}）"
                        log.append(
                            f"[account:{account_id}] 计数交叉校验：解密副本 Msg% 表行数 = {truth}；"
                            f"读取器交出条数 = {n} → {verdict}")
                        if truth != n:
                            log.append(
                                f"[account:{account_id}] ⚠ 两条路径计数不一致，可能存在漏读/重复："
                                f"请以实测值 {truth} 为准并排查 services/wechat_db_reader.py")
                    else:
                        log.append(f"[account:{account_id}] 计数校验未取得实测值，沿用读取器条数 {n}")
            except Exception as e:  # noqa: BLE001
                log.append(f"[account:{account_id}] 读取消息失败：{e}")
                result["errors"].append({
                    "account_id": account_id, "step": "read_messages",
                    "reason": f"{type(e).__name__}: {e}",
                    "next_action": "库已解密成功，可单独重试读取；若持续失败请附日志反馈",
                })
        else:
            log.append(f"[account:{account_id}] 跳过消息读取（无成功解密的库或读取器不可用）")
        return result

    def _resolve_accounts(self, account_ids: Optional[Sequence[str]],
                          log: List[str],
                          extra_roots: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        roots = scan_roots(self._all_extra_roots(extra_roots), log)
        found: List[Dict[str, Any]] = []
        for r in roots:
            if not r["exists"]:
                continue
            for a in list_accounts(r["path"], log):
                a["version_family"] = figure_family(a["layout"])
                found.append(a)
        if account_ids:
            want = [str(x) for x in account_ids]
            sel = [a for a in found if a["account_id"] in want]
            missing = [w for w in want if w not in {a["account_id"] for a in sel}]
            for m in missing:
                log.append(f"[select] 指定账号 {m} 未发现，已跳过")
            return sel
        return found

    def collect_messages(self,
                         account_ids: Optional[List[str]] = None,
                         contact_filter: Optional[Sequence[str]] = None,
                         incremental: bool = False,
                         consent_high_risk: bool = False,
                         progress_cb: Optional[Callable] = None,
                         max_messages_per_account: Optional[int] = 200000,
                         extra_roots: Optional[Sequence[str]] = None,
                         acquire_key_if_missing: bool = False,
                         consent_injection: bool = False) -> Dict[str, Any]:
        """与 run() 同一条链路，但**把消息本体带回来**（供 wechat_auto_importer 复用）。

        说明：`run()` 只统计条数（内存友好）；需要消息正文时走本方法。
        `max_messages_per_account` 默认 20 万条/账号，防止一次把整库灌进内存，
        截断会写进 log。
        """
        log: List[str] = []
        self._cancel_evt.clear()
        with self._lock:
            self._running = True
            self._progress.update({"running": True, "message_count": 0, "db_index": 0})
        try:
            accounts = self._resolve_accounts(account_ids, log, extra_roots)
            if not accounts:
                return {"ok": False, "messages": [], "per_account": [], "errors": [
                    {"account_id": "", "step": "discover", "reason": "没有发现任何微信账号目录",
                     "next_action": "确认微信已登录过并产生数据；或在配置里补充额外数据根目录"}],
                    "log": log}
            per_account: List[Dict[str, Any]] = []
            errors: List[Dict[str, Any]] = []
            all_msgs: List[Dict[str, Any]] = []
            n = len(accounts)
            for ai, a in enumerate(accounts):
                base = 95.0 * ai / n
                span = 95.0 * (ai + 1) / n - base
                res = self._import_account(a, contact_filter, incremental,
                                           consent_high_risk, log, progress_cb,
                                           collect_messages=True,
                                           max_messages=max_messages_per_account,
                                           pct_base=base, pct_span=span,
                                           acquire_key_if_missing=acquire_key_if_missing,
                                           consent_injection=consent_injection)
                per_account.append({k: v for k, v in res.items()
                                    if k not in ("messages", "db_ok_list")})
                errors.extend(res["errors"])
                all_msgs.extend(res["messages"])
            self._set_progress(progress_cb, stage="done", stage_name=_STAGE_NAMES["done"],
                               percentage=100, running=False, message_count=len(all_msgs))
            log.append(f"[collect] 合计消息 {len(all_msgs)} 条")
            self._last_log = log
            return {"ok": True, "messages": all_msgs, "per_account": per_account,
                    "errors": errors, "log": log}
        finally:
            with self._lock:
                self._running = False
                self._progress["running"] = False

    # ------------------------------------------------------------------
    # run
    # ------------------------------------------------------------------
    def run(self, account_ids: Optional[List[str]] = None,
            contact_filter: Any = None,
            incremental: bool = False,
            consent_high_risk: bool = False,
            progress_cb: Optional[Callable] = None,
            acquire_key_if_missing: bool = False,
            consent_injection: bool = False) -> Dict[str, Any]:
        """一键导入（统计口径；消息正文不返回，避免一次性撑爆内存）。

        Returns:
            {"ok","total_messages",
             "per_account":[{"account_id","version_family","key_method","key_source",
                             "dbs_total","dbs_ok","dbs_failed","messages"}],
             "used":[{"method","count"}],            # method=密钥来源路线，count=该路线导入的消息数
             "errors":[{"account_id","step","reason","next_action"}],
             "log":[...]}

        追加参数（原契约字段名与返回结构**一律不变**）：
            acquire_key_if_missing：某账号无缓存密钥时，先跑「现场抓取」（1–7 步）再解密入库。
            consent_injection：注入控制器 DLL 的显式知情同意；为 False 时现场抓取
                不会关闭/注入微信，只会把原因与下一步动作写进 errors/log。
        """
        log: List[str] = []
        self._cancel_evt.clear()
        with self._lock:
            self._running = True
            self._progress.update({"running": True, "stage": "discover",
                                   "stage_name": _STAGE_NAMES["discover"],
                                   "percentage": 0, "message_count": 0,
                                   "account_id": "", "db_index": 0, "db_total": 0})
        started = time.time()
        try:
            accounts = self._resolve_accounts(account_ids, log)
            if not accounts:
                log.append("[run] 没有发现任何账号目录")
                self._set_progress(progress_cb, stage="error", stage_name=_STAGE_NAMES["error"],
                                   running=False)
                return {"ok": False, "total_messages": 0, "per_account": [], "used": [],
                        "errors": [{"account_id": "", "step": "discover",
                                    "reason": "没有发现任何微信账号目录",
                                    "next_action": "确认微信已登录过；或在配置里补充额外数据根目录"}],
                        "log": log}
            log.append(f"[run] 待处理账号 {len(accounts)} 个："
                       f"{[a['account_id'] for a in accounts]}"
                       f"（consent_high_risk={bool(consent_high_risk)}）")
            per_account: List[Dict[str, Any]] = []
            errors: List[Dict[str, Any]] = []
            total = 0
            used: Dict[str, int] = {}
            n = len(accounts)
            for ai, a in enumerate(accounts):
                if self._cancelled():
                    log.append("[run] 收到取消请求，停止后续账号")
                    break
                base = 95.0 * ai / n
                span = 95.0 * (ai + 1) / n - base
                res = self._import_account(a, contact_filter, incremental,
                                           consent_high_risk, log, progress_cb,
                                           collect_messages=False, max_messages=None,
                                           pct_base=base, pct_span=span,
                                           acquire_key_if_missing=acquire_key_if_missing,
                                           consent_injection=consent_injection)
                total += res["message_count"]
                per_account.append({
                    "account_id": res["account_id"],
                    "version_family": res["version_family"],
                    "key_method": res["key_method"],
                    "key_source": res["key_source"],
                    "dbs_total": res["dbs_total"],
                    "dbs_ok": res["dbs_ok"],
                    "dbs_failed": res["dbs_failed"],
                    "messages": res["message_count"],
                    "decrypt_notes": res.get("decrypt_notes", []),
                })
                errors.extend(res["errors"])
                if res["message_count"]:
                    used[res["key_method"] or "unknown"] = (
                        used.get(res["key_method"] or "unknown", 0) + res["message_count"])
                self._set_progress(progress_cb,
                                   percentage=int(95.0 * (ai + 1) / n),
                                   message_count=total)
            used_list = [{"method": m, "count": c} for m, c in sorted(used.items())]
            ok = bool(per_account) and any(p["dbs_ok"] for p in per_account)
            stage = "cancelled" if self._cancelled() else "done"
            self._set_progress(progress_cb, stage=stage, stage_name=_STAGE_NAMES[stage],
                               percentage=100, running=False, message_count=total)
            log.append(f"[run] 完成：total_messages={total}，用时 {time.time() - started:.1f}s，"
                       f"错误 {len(errors)} 条")
            self._last_log = log
            return {"ok": ok, "total_messages": total, "per_account": per_account,
                    "used": used_list, "errors": errors, "log": log}
        finally:
            with self._lock:
                self._running = False
                self._progress["running"] = False

    # ------------------------------------------------------------------
    # 高风险路线（绝不自动执行）
    # ------------------------------------------------------------------
    def high_risk_available(self) -> Dict[str, Any]:
        """高风险路线（注入控制器 DLL）是否可用。**只检测，不执行**。

        判据不再是「exe/dll 文件在不在」这种弱条件，而是走
        WeChatKeyController 的只读预检：DLL 身份（导出表 + sha256）、微信版本、
        **按版本选定特征码在目标 Weixin.dll 里是否真的命中**。
        实测：本机微信 4.1.15.13 → 命中的是 `<4.1.4` 档，而按版本会选 `>=4.1.4` 档，
        该档命中 0 处，故 available=False（与实机 InitializeHook 报「模式匹配失败」一致）。
        """
        try:
            from services.wechat_key_controller import WeChatKeyController
            ctl = WeChatKeyController()
            if not ctl.identity.get("ok"):
                return {"available": False,
                        "reason": "控制器 DLL 不可用：%s" % ctl.identity.get("reason")}
            flow = self._ensure_keyflow()
            if flow is None:
                return {"available": False, "reason": "密钥编排模块 services/wechat_key_controller "
                                                      "不可用，无法走注入路线"}
            pre = flow.precheck()
            if not pre.get("available"):
                return {"available": False, "reason": pre.get("reason") or "预检判定不可注入",
                        "version": pre.get("version"), "signature_branch": pre.get("signature_branch"),
                        "signature_hits": pre.get("signature_hits")}
            return {"available": True,
                    "reason": "预检通过（版本 %s，特征码档「%s」命中 %s 处）。该路线为向微信进程"
                              "写入 Hook 拦截密钥；必须先在界面取得用户显式知情同意再调用；"
                              "流程结束会自动 CleanupHook 卸载，异常退出时微信可能需要重启。"
                              % (pre.get("version"), pre.get("signature_branch"),
                                 pre.get("signature_hits")),
                    "version": pre.get("version"),
                    "signature_branch": pre.get("signature_branch"),
                    "signature_hits": pre.get("signature_hits")}
        except Exception as e:  # noqa: BLE001
            return {"available": False,
                    "reason": "高风险路线可用性检测异常：%s: %s" % (type(e).__name__, e)}

    def acquire_key_high_risk(self, wait_seconds: int = 180,
                              consent_injection: bool = True) -> Dict[str, Any]:
        """**仅在被显式确认后调用**：程序自动跑完 1–7 步并回收密钥入加密缓存。

        实现已升级（不再是把 wx_key GUI 丢给用户点）：
        调用 services/wechat_key_controller.WeChatKeyFlow.run()，由程序自动完成
        检测 → 关闭微信 → 重启 → 前台化 → 等待扫码（可选代点登录）→ 注入控制器 DLL
        → 轮询回收密钥 → CleanupHook 卸载 → 写入机器指纹加密缓存。
        ⚠️ 已知限制（2026-10-17 实机结论）：上游 wx_key 的特征码库覆盖不到本机微信 4.1.15.13
        （`>=4.1.4` 档实测 0 命中，实机报「模式匹配失败，找到 0 个结果」）。因此本机
        **从未取过密钥的账号**走到 inject 阶段必然失败；只有**已有加密缓存密钥**的账号才能一键走通。
        详见 docs/微信版本适配与已知限制.md。

        参数 `consent_injection` 默认 True：**调用本方法即视为 UI 侧已弹过显式确认框**
        （方法名/入口本身就是"知情同意之后"的入口）。若调用方想做更严格的把关，
        可显式传 False，此时会走红线分支（不关微信、不注入）并返回原因与下一步。
        回退条件：wechat_key_controller 不可用时，退回旧的 WeChatImporter.launch_wxkey_capture
        （起 wx_key GUI 窗口 + 轮询 %TEMP%\\wx_key_status.log 回收）。
        """
        flow = None
        try:
            flow = self._ensure_keyflow()
        except Exception:  # noqa: BLE001
            flow = None
        if flow is not None:
            try:
                r = flow.run(consent_injection=bool(consent_injection),
                             auto_restart=True, allow_auto_login_click=True,
                             wait_seconds=int(wait_seconds))
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "reason": "现场抓取异常：%s: %s" % (type(e).__name__, e)}
            d = r.get("data") if isinstance(r, dict) else None
            d = d if isinstance(d, dict) else {}
            key_hex_len = int(d.get("key_hex_len") or 0)
            out = {"ok": bool(d.get("key_acquired")),
                   "method": d.get("key_method") or "",
                   "source": d.get("key_source") or "",
                   "cached": bool(d.get("key_acquired")),
                   "key_hex_len": key_hex_len,
                   "cleanup_done": bool(d.get("cleanup_done")),
                   "wechat_restarted": bool(d.get("wechat_restarted")),
                   "stages": d.get("stages") or [],
                   "errors": d.get("errors") or [],
                   "note": "流程结束已 CleanupHook 卸载；异常退出时微信可能需要重启。"
                           "密钥只进机器指纹加密缓存，不回传明文。"}
            if not out["ok"]:
                # 给出第一条可执行的下一步，避免静默失败
                errs = out["errors"]
                out["reason"] = (errs[0].get("reason") if errs else "未取到密钥")
                out["next_action"] = (errs[0].get("next_action") if errs
                                      else "确认微信已登录后重试")
            return out

        # ---- 回退：旧的 wx_key GUI 路线（wechat_key_controller 不可用时）----
        avail = self.high_risk_available()
        if self._importer is None or not hasattr(self._importer, "launch_wxkey_capture"):
            return {"ok": False, "reason": "密钥编排与 WeChatImporter.launch_wxkey_capture "
                                           "均不可用，无法抓取密钥",
                    "next_action": "检查 services/wechat_key_controller.py 是否随工程交付"}
        try:
            r = self._importer.launch_wxkey_capture(wait_seconds=int(wait_seconds))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "reason": "拉起 wx_key 失败：%s: %s" % (type(e).__name__, e)}
        if not r.get("ok"):
            return {"ok": False, "reason": r.get("reason", "未在窗口内取到密钥"),
                    "next_action": "在 wx_key 窗口内完成一次微信登录后重试；"
                                   "或改用低风险路线（先登录微信再重试导入）"}
        key = r.get("key") or ""
        return {"ok": True, "method": r.get("method"), "source": r.get("source"),
                "waited": r.get("waited"), "cached": True,
                # 不回传明文密钥：只给掩码，明文只进机器指纹加密缓存
                "key_masked": (key[:8] + "…" + key[-4:]) if len(key) >= 16 else "",
                "note": "回退路线：密钥已写入机器指纹加密缓存"}

    # ------------------------------------------------------------------
    def last_log(self) -> List[str]:
        return list(self._last_log)


# ---------------------------------------------------------------------------
# Windows 文件版本（只在需要时导入 ctypes，失败即返回 None）
# ---------------------------------------------------------------------------
def _read_file_version(path: str) -> str:
    """读 PE 文件的 FileVersion（如 4.1.15.13）。失败返回 ""。"""
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size, buf):
            return ""
        rp = ctypes.c_void_p()
        ln = wintypes.UINT()
        if not ver.VerQueryValueW(buf, ctypes.c_wchar_p("\\"), ctypes.byref(rp),
                                  ctypes.byref(ln)):
            return ""
        ffi = ctypes.cast(rp, ctypes.POINTER(ctypes.c_uint32))
        ms, ls = ffi[2], ffi[3]
        if ms == 0 and ls == 0:
            return ""
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception as e:  # noqa: BLE001
        logger.debug("读文件版本失败 %s: %s", path, e)
        return ""


__all__ = [
    "WechatOneClick",
    "VERSION_ADAPTATION",
    "TOOL_ROUTES",
    "default_roots",
    "scan_roots",
    "list_accounts",
    "list_db_files",
    "scan_dbs",
    "count_dbs_under_root",
    "list_message_dbs",
    "find_contact_db",
    "pick_probe_db",
    "version_spec",
    "figure_family",
    "detect_layout",
    "root_kind",
    "PROJECT_ROOT",
    "WORK_ROOT",
    "GLOBAL_CACHE_PATH",
]
