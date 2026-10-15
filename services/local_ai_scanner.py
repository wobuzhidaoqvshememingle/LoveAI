# -*- coding: utf-8 -*-
"""LocalAIScanner：只读地探测本机已安装的 AI 客户端 / 本地引擎 / 聚合器。

冻结契约 SCANNER_CONTRACT.md 的逐字实现。硬约束：
- 仅用 Python 标准库；只读；不启停任何第三方软件；不逆向私有协议。
- 任何 apiKey 只记录“是否存在”，绝不在返回/notes 中出现明文。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.request

# ---------------------------------------------------------------------------
# 特征库 KNOWN_SOFTWARE
# ---------------------------------------------------------------------------
# 每项：{id, display, kind, vendor, name_keywords, dir_keywords, process_keywords,
#        exact_dir_process, block_substrings}
# kind ∈ cloud_client / local_engine / aggregator
KNOWN_SOFTWARE = [
    # ---- cloud_client ----
    {"id": "doubao", "display": "豆包", "kind": "cloud_client", "vendor": "字节跳动",
     "name_keywords": ["doubao", "豆包"], "dir_keywords": ["doubao"],
     "process_keywords": ["doubao"], "exact_dir_process": True,
     "block_substrings": ["ime", "输入法", "input"]},
    {"id": "yuanbao", "display": "腾讯元宝", "kind": "cloud_client", "vendor": "腾讯",
     "name_keywords": ["yuanbao", "元宝"], "dir_keywords": ["yuanbao"],
     "process_keywords": ["yuanbao"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "kimi", "display": "Kimi", "kind": "cloud_client", "vendor": "月之暗面",
     "name_keywords": ["kimi"], "dir_keywords": ["kimi"],
     "process_keywords": ["kimi"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "deepseek", "display": "DeepSeek", "kind": "cloud_client", "vendor": "深度求索",
     "name_keywords": ["deepseek"], "dir_keywords": ["deepseek"],
     "process_keywords": ["deepseek"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "qianwen", "display": "通义千问", "kind": "cloud_client", "vendor": "阿里",
     "name_keywords": ["qianwen", "千问", "qianwenapp"], "dir_keywords": ["qianwenapp", "qianwen"],
     "process_keywords": ["qianwen"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "chatgpt", "display": "ChatGPT", "kind": "cloud_client", "vendor": "OpenAI",
     "name_keywords": ["chatgpt"], "dir_keywords": ["chatgpt"],
     "process_keywords": ["chatgpt"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "claude", "display": "Claude", "kind": "cloud_client", "vendor": "Anthropic",
     "name_keywords": ["claude"], "dir_keywords": ["claude"],
     "process_keywords": ["claude"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "gemini", "display": "Gemini", "kind": "cloud_client", "vendor": "Google",
     "name_keywords": ["gemini"], "dir_keywords": ["gemini"],
     "process_keywords": ["gemini"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "wenxin", "display": "文心一言", "kind": "cloud_client", "vendor": "百度",
     "name_keywords": ["文心", "ernie"], "dir_keywords": ["wenxin", "ernie"],
     "process_keywords": ["wenxin"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "zhipu", "display": "智谱清言", "kind": "cloud_client", "vendor": "智谱",
     "name_keywords": ["智谱", "glm"], "dir_keywords": ["zhipu", "glm"],
     "process_keywords": ["zhipu"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "hailuo", "display": "海螺", "kind": "cloud_client", "vendor": "MiniMax",
     "name_keywords": ["海螺", "hailuo"], "dir_keywords": ["hailuo"],
     "process_keywords": ["hailuo"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "minimax", "display": "MiniMax", "kind": "cloud_client", "vendor": "MiniMax",
     "name_keywords": ["minimax"], "dir_keywords": ["minimax"],
     "process_keywords": ["minimax"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "stepfun", "display": "阶跃星辰", "kind": "cloud_client", "vendor": "阶跃星辰",
     "name_keywords": ["阶跃", "stepfun"], "dir_keywords": ["stepfun"],
     "process_keywords": ["stepfun"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "workbuddy", "display": "WorkBuddy", "kind": "cloud_client", "vendor": "WorkBuddy",
     "name_keywords": ["workbuddy"], "dir_keywords": ["workbuddy"],
     "process_keywords": ["workbuddy"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "quark", "display": "夸克", "kind": "cloud_client", "vendor": "阿里",
     "name_keywords": ["quark", "夸克"], "dir_keywords": ["quark"],
     "process_keywords": ["quark"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "tencentima", "display": "腾讯IMA", "kind": "cloud_client", "vendor": "腾讯",
     "name_keywords": ["ima"], "dir_keywords": ["ima"],
     "process_keywords": ["ima"], "exact_dir_process": False,
     "block_substrings": []},
    # ---- local_engine ----
    {"id": "ollama", "display": "Ollama", "kind": "local_engine", "vendor": "Ollama",
     "name_keywords": ["ollama"], "dir_keywords": ["ollama"],
     "process_keywords": ["ollama"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "lmstudio", "display": "LM Studio", "kind": "local_engine", "vendor": "LM Studio",
     "name_keywords": ["lm studio", "lmstudio"], "dir_keywords": ["lm studio", "lmstudio"],
     "process_keywords": ["lm studio", "lmstudio"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "jan", "display": "Jan", "kind": "local_engine", "vendor": "Jan",
     "name_keywords": ["jan"], "dir_keywords": ["jan"],
     "process_keywords": ["jan"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "gpt4all", "display": "GPT4All", "kind": "local_engine", "vendor": "GPT4All",
     "name_keywords": ["gpt4all"], "dir_keywords": ["gpt4all"],
     "process_keywords": ["gpt4all"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "llamacpp", "display": "llama.cpp", "kind": "local_engine", "vendor": "ggml",
     "name_keywords": ["llama.cpp", "llama"], "dir_keywords": ["llama"],
     "process_keywords": ["llama"], "exact_dir_process": False,
     "block_substrings": []},
    # ---- aggregator ----
    {"id": "cherry_studio", "display": "Cherry Studio", "kind": "aggregator", "vendor": "Cherry",
     "name_keywords": ["cherry studio", "cherry"], "dir_keywords": ["cherry studio", "cherry"],
     "process_keywords": ["cherry studio", "cherry"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "chatbox", "display": "Chatbox", "kind": "aggregator", "vendor": "Chatbox",
     "name_keywords": ["chatbox"], "dir_keywords": ["chatbox"],
     "process_keywords": ["chatbox"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "lobechat", "display": "Lobe Chat", "kind": "aggregator", "vendor": "LobeHub",
     "name_keywords": ["lobe chat", "lobechat", "lobe"], "dir_keywords": ["lobe"],
     "process_keywords": ["lobe"], "exact_dir_process": False,
     "block_substrings": []},
    {"id": "nextchat", "display": "NextChat", "kind": "aggregator", "vendor": "NextChat",
     "name_keywords": ["nextchat", "next chat"], "dir_keywords": ["nextchat", "next chat"],
     "process_keywords": ["nextchat"], "exact_dir_process": False,
     "block_substrings": []},
]

# 本地引擎知名端口
KNOWN_ENGINE_PORTS = (11434, 1234)


# ---------------------------------------------------------------------------
# 匹配辅助
# ---------------------------------------------------------------------------
def _stem(name):
    """取文件/目录名的 stem（小写、去扩展名、去路径）。"""
    base = os.path.basename((name or "").strip().strip("\\/"))
    stem = os.path.splitext(base)[0]
    return stem.lower()


def _text_matches(text, keywords, exact=False):
    """大小写不敏感匹配：优先整段相等，其次子串。"""
    if not text:
        return False
    t = text.strip().lower()
    if exact:
        return any(t == kw.lower() for kw in keywords)
    for kw in keywords:
        if t == kw.lower():
            return True
    for kw in keywords:
        if kw.lower() in t:
            return True
    return False


def _entry_blocked(stem_text, entry):
    bl = (entry.get("block_substrings") or [])
    if not bl:
        return False
    s = (stem_text or "").lower()
    return any(b in s for b in bl)


def _match_entry_by_name(name, publisher, entries):
    """注册表 DisplayName 匹配。返回匹配到的 entry 或 None。"""
    if not name:
        return None
    for e in entries:
        if _entry_blocked(_stem(name), e):
            continue
        if _text_matches(name, e["name_keywords"]):
            return e
    return None


def _match_entry_by_dir(dirpath, entries):
    stem_text = _stem(dirpath)
    if not stem_text:
        return None
    for e in entries:
        if _entry_blocked(stem_text, e):
            continue
        # 目录名整段精确相等（避免 DoubaoCache/KimiData 这类含子串的目录误报）
        if _text_matches(stem_text, e["dir_keywords"], exact=True):
            return e
    return None


def _match_entry_by_process(proc_name, entries):
    stem_text = _stem(proc_name)
    if not stem_text:
        return None
    for e in entries:
        if _entry_blocked(stem_text, e):
            continue
        if _text_matches(stem_text, e["process_keywords"], exact=e.get("exact_dir_process", False)):
            return e
    return None


# ---------------------------------------------------------------------------
# WindowsSystem：真实系统快照（懒加载 winreg / subprocess）
# ---------------------------------------------------------------------------
class WindowsSystem:
    """真实 Windows 只读系统快照。所有方法异常静默。"""

    UNINSTALL_PATHS = [
        (0x80000002, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),        # HKLM
        (0x80000002, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),  # HKLM WOW6432
        (0x80000001, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),        # HKCU
    ]

    def list_installed_programs(self):
        out = []
        seen = set()
        try:
            import winreg
        except Exception:
            return out

        def rv(key, value_name):
            try:
                v, _ = winreg.QueryValueEx(key, value_name)
                return v
            except Exception:
                return None
        for hive, sub in self.UNINSTALL_PATHS:
            try:
                root = winreg.OpenKey(hive, sub)
            except Exception:
                continue
            try:
                count = winreg.QueryInfoKey(root)[0]
            except Exception:
                count = 0
            for i in range(count):
                try:
                    child_name = winreg.EnumKey(root, i)
                except Exception:
                    break
                try:
                    k = winreg.OpenKey(root, child_name)
                    name = rv(k, "DisplayName")
                    if not name:
                        continue
                    name = str(name).strip()
                    if name.lower() in seen:
                        continue
                    seen.add(name.lower())
                    vendor = rv(k, "Publisher") or ""
                    loc = rv(k, "InstallLocation") or ""
                    out.append({"name": name,
                                "vendor": str(vendor).strip(),
                                "install_location": str(loc).strip()})
                except Exception:
                    continue
        return out

    def list_program_directories(self):
        out = []
        seen = set()

        def _add(p):
            try:
                if p and os.path.isdir(p) and os.path.abspath(p) not in seen:
                    seen.add(os.path.abspath(p))
                    out.append({"path": os.path.abspath(p)})
            except Exception:
                pass

        # 已知根目录枚举
        roots = []
        for env in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                roots.append(base)
        for base in roots:
            try:
                for child in os.listdir(base):
                    _add(os.path.join(base, child))
            except Exception:
                continue
        # 固定盘根目录一级子目录
        for drive in self._fixed_drives():
            try:
                for child in os.listdir(drive):
                    _add(os.path.join(drive, child))
            except Exception:
                continue
        return out

    @staticmethod
    def _fixed_drives():
        drives = []
        try:
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for letter in range(26):
                if bitmask & (1 << letter):
                    root = "%s:\\" % chr(ord("A") + letter)
                    try:
                        if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
                            drives.append(root)
                    except Exception:
                        continue
        except Exception:
            pass
        return drives

    def list_listen_ports(self):
        out = []
        pid_names = self._pid_to_names()
        try:
            proc = subprocess.run(["netstat", "-ano"], capture_output=True,
                                  text=True, errors="replace", timeout=15)
            lines = (proc.stdout or "").splitlines()
        except Exception:
            return out
        for line in lines:
            parts = line.split()
            # 形如: TCP  127.0.0.1:5284  0.0.0.0:0  LISTENING  1234
            if len(parts) < 5:
                continue
            if parts[0].upper() not in ("TCP", "TCPV6"):
                continue
            if parts[-2].upper() != "LISTENING":
                continue
            try:
                local = parts[1]
                pid = int(parts[-1])
            except Exception:
                continue
            addr, port = self._split_addr_port(local)
            out.append({"address": addr, "port": port, "pid": pid,
                        "process_name": pid_names.get(pid, "")})
        return out

    @staticmethod
    def _split_addr_port(local):
        local = local.strip()
        if local.startswith("["):
            addr, _, port = local[1:].partition("]:")
            return addr, int(port)
        addr, _, port = local.rpartition(":")
        return addr, int(port)

    def list_processes(self):
        out = []
        try:
            proc = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                                  capture_output=True, text=True, errors="replace",
                                  timeout=15)
            lines = (proc.stdout or "").splitlines()
        except Exception:
            return out
        import csv
        for row in csv.reader(lines):
            if len(row) < 2:
                continue
            try:
                name = row[0].strip().strip('"')
                pid = int(row[1].strip().strip('"'))
                out.append({"name": name, "pid": pid})
            except Exception:
                continue
        return out

    def _pid_to_names(self):
        m = {}
        for p in self.list_processes():
            m.setdefault(p["pid"], p["name"])
        return m

    def cherry_studio_db_path(self):
        base = os.environ.get("APPDATA", "")
        return os.path.join(base, "CherryStudio", "Data", "cherrystudio.sqlite")


# ---------------------------------------------------------------------------
# 默认 http_get（urllib）
# ---------------------------------------------------------------------------
def _default_http_get(url, timeout=None, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# LocalAIScanner
# ---------------------------------------------------------------------------
class LocalAIScanner:
    def __init__(self, config=None, system=None, http_get=None, clock=None):
        self.config = config or {}
        self.system = system or WindowsSystem()
        self.http_get = http_get or _default_http_get
        self.clock = clock or time.monotonic
        self.known = KNOWN_SOFTWARE

    # ---- config helpers ----
    def _f(self, key, default):
        try:
            return float(self.config.get(key, default))
        except Exception:
            return default

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------
    def scan(self):
        notes = []
        installed_map = {}   # id -> dict

        # 三源发现
        progs = self._safe(self.system.list_installed_programs, [])
        dirs = self._safe(self.system.list_program_directories, [])
        procs = self._safe(self.system.list_processes, [])
        ports = self._safe(self.system.list_listen_ports, [])

        proc_names = [p.get("name", "") for p in procs]
        proc_stems = [_stem(n) for n in proc_names]

        def _touch(entry, vendor_hint=None):
            info = installed_map.get(entry["id"])
            if info is None:
                info = {"id": entry["id"], "name": entry["display"],
                        "vendor": entry["vendor"], "kind": entry["kind"],
                        "running": False, "vendor_override": ""}
                installed_map[entry["id"]] = info
            if vendor_hint:
                info["vendor_override"] = vendor_hint
            return info

        # source 1: registry
        for pr in progs:
            e = _match_entry_by_name(pr.get("name", ""), pr.get("vendor", ""), self.known)
            if e:
                _touch(e, pr.get("vendor"))
        # source 2: directories
        for d in dirs:
            e = _match_entry_by_dir(d.get("path", ""), self.known)
            if e:
                _touch(e)
        # source 3: processes
        for p in procs:
            e = _match_entry_by_process(p.get("name", ""), self.known)
            if e:
                _touch(e)

        # running 判定：进程出现 或 监听端口 process_name 命中
        port_records = self._loopback_ports(ports)
        port_owner = {}   # port -> entry id
        for rec in port_records:
            pn = rec.get("process_name", "")
            e = _match_entry_by_process(pn, self.known)
            if e:
                port_owner[rec["port"]] = e["id"]
        # 知名引擎端口即使 netstat 未列出也尝试握手
        probe_ports = {rec["port"] for rec in port_records}
        for p in KNOWN_ENGINE_PORTS:
            probe_ports.add(p)

        for info in installed_map.values():
            e = self._by_id(info["id"])
            running = False
            for stem_text in proc_stems:
                if _text_matches(stem_text, e["process_keywords"],
                                 exact=e.get("exact_dir_process", False)):
                    if not _entry_blocked(stem_text, e):
                        running = True
                        break
            if not running:
                for rec in port_records:
                    if _match_entry_by_process(rec.get("process_name", ""), self.known) is e:
                        running = True
                        break
            info["running"] = running

        # ---- OpenAI 握手 ----
        handshake = {}   # port -> {"models": [...], "process_name": str}
        http_timeout = self._f("scan.http_timeout", 1.2)
        total_timeout = self._f("scan.total_timeout", 6.0)
        start = self.clock()
        rec_by_port = {rec["port"]: rec for rec in port_records}
        for port in sorted(probe_ports):
            if self.clock() - start >= total_timeout:
                notes.append("探测总超时(%ss)，剩余端口未握手。" % total_timeout)
                break
            body = None
            try:
                body = self.http_get(
                    "http://127.0.0.1:%d/v1/models" % port,
                    timeout=http_timeout,
                    headers={"Accept": "application/json"})
            except Exception:
                body = None
            models = self._parse_models(body)
            if models:
                handshake[port] = {
                    "models": models,
                    "process_name": rec_by_port.get(port, {}).get("process_name", ""),
                }

        # ---- access 归并 ----
        for info in installed_map.values():
            kind = info["kind"]
            owned = any(port_owner.get(p) == info["id"] for p in handshake)
            if kind == "aggregator":
                access = "aggregator"
            elif kind == "local_engine":
                access = "openai_compatible" if owned else "local_not_serving"
            else:  # cloud_client
                access = "openai_compatible" if owned else "vendor_private_no_open_access"
            info["access"] = access

        # ---- openai_endpoints ----
        openai_endpoints = []
        for port in sorted(handshake):
            h = handshake[port]
            openai_endpoints.append({
                "base_url": "http://127.0.0.1:%d" % port,
                "port": port,
                "process_name": h["process_name"],
                "models": h["models"],
            })

        # ---- installed 输出 ----
        installed = []
        for info in sorted(installed_map.values(), key=lambda x: x["id"]):
            vendor = info.get("vendor_override") or info["vendor"]
            installed.append({
                "id": info["id"], "name": info["name"], "vendor": vendor,
                "kind": info["kind"], "running": info["running"],
                "access": info["access"],
            })

        # ---- aggregator_providers (Cherry Studio) ----
        aggregator_providers = self._read_cherry(notes)

        # ---- usable_models ----
        usable = []
        # 来源 A：端口握手
        for ep in openai_endpoints:
            e = _match_entry_by_process(ep["process_name"], self.known)
            pkind = e["kind"] if e else "local_engine"
            for m in ep["models"]:
                usable.append({
                    "provider_kind": pkind,
                    "base_url": ep["base_url"],
                    "model": m,
                    "key_present": False,
                    "origin": "port_handshake:" + (ep["process_name"] or "unknown"),
                })
        # 来源 B：Cherry
        for prov in aggregator_providers:
            bu = prov.get("base_url")
            has_key = prov.get("has_key", False)
            if not bu:
                continue
            local = _is_local_base(bu)
            if not (has_key or local):
                continue
            if local:
                pkind = "local_engine"
            elif bu.lower().startswith("https://") and has_key:
                pkind = "cloud_client"
            else:
                pkind = "aggregator"
            for m in prov.get("models", []):
                usable.append({
                    "provider_kind": pkind,
                    "base_url": bu,
                    "model": m,
                    "key_present": has_key,
                    "origin": "cherry_studio:" + prov.get("name", ""),
                })
        # 去重 + 排序
        seen = set()
        dedup = []
        for u in usable:
            key = (u["base_url"], u["model"], u["origin"])
            if key in seen:
                continue
            seen.add(key)
            dedup.append(u)
        usable = sorted(dedup, key=lambda x: (x["base_url"], x["model"], x["origin"]))

        # ---- notes ----
        for info in installed:
            if info["kind"] == "cloud_client" and info["access"] == "vendor_private_no_open_access":
                notes.append("「%s」使用厂商私有内部协议，未开放第三方标准(OpenAI)接入，无法自动调用。"
                             % info["name"])
        notes.append("已安装 AI 相关软件 %d 个；标准 OpenAI 端点 %d 个；可直接调用模型 %d 个。"
                     % (len(installed), len(openai_endpoints), len(usable)))

        return {
            "installed": installed,
            "openai_endpoints": openai_endpoints,
            "aggregator_providers": aggregator_providers,
            "usable_models": usable,
            "notes": notes,
        }

    # ------------------------------------------------------------------
    def _by_id(self, sid):
        for e in self.known:
            if e["id"] == sid:
                return e
        return {"id": sid, "display": sid, "kind": "local_engine", "vendor": "",
                "name_keywords": [], "dir_keywords": [], "process_keywords": []}

    @staticmethod
    def _loopback_ports(ports):
        out = []
        for rec in ports:
            a = (rec.get("address") or "").strip().lower().strip("[]")
            if a in ("127.0.0.1", "0.0.0.0", "::", "::1", "localhost") or a.startswith("127."):
                out.append(rec)
        return out

    @staticmethod
    def _parse_models(body):
        if not body:
            return []
        try:
            data = json.loads(body)
        except Exception:
            return []
        ids = []

        def _collect(obj):
            if isinstance(obj, dict):
                if "id" in obj and isinstance(obj["id"], str):
                    ids.append(obj["id"])
                elif "name" in obj and isinstance(obj["name"], str):
                    ids.append(obj["name"])
            elif isinstance(obj, str):
                ids.append(obj)

        if isinstance(data, dict):
            if isinstance(data.get("data"), list):
                for it in data["data"]:
                    _collect(it)
            elif isinstance(data.get("models"), list):
                for it in data["models"]:
                    _collect(it)
        elif isinstance(data, list):
            for it in data:
                _collect(it)
        # 去重保序
        seen = set()
        uniq = []
        for i in ids:
            if i and i not in seen:
                seen.add(i)
                uniq.append(i)
        return uniq

    # ------------------------------------------------------------------
    # Cherry Studio sqlite（复制到 temp 后只读）
    # ------------------------------------------------------------------
    def _read_cherry(self, notes):
        result = []
        try:
            db_path = self.system.cherry_studio_db_path()
        except Exception:
            db_path = None
        if not db_path or not os.path.exists(db_path):
            notes.append("未发现 Cherry Studio 数据库，聚合器供应商列表为空。")
            return result

        tmpdir = tempfile.mkdtemp(prefix="lai_cherry_")
        try:
            base = os.path.basename(db_path)
            tmp_db = os.path.join(tmpdir, base)
            shutil.copyfile(db_path, tmp_db)
            for suffix in ("-wal", "-shm"):
                side = db_path + suffix
                if os.path.exists(side):
                    try:
                        shutil.copyfile(side, tmp_db + suffix)
                    except Exception:
                        pass
            conn = sqlite3.connect(tmp_db)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT provider_id, name, endpoint_configs, default_chat_endpoint,"
                    " api_keys, is_enabled FROM user_provider").fetchall()
            except Exception:
                notes.append("读取 Cherry Studio user_provider 表失败，聚合器供应商列表为空。")
                return result
            model_rows = []
            try:
                model_rows = conn.execute(
                    "SELECT provider_id, model_id, is_hidden FROM user_model").fetchall()
            except Exception:
                model_rows = []
            conn.close()

            # provider_id -> [model_id]（非隐藏）
            prov_models = {}
            for mr in model_rows:
                try:
                    if (mr["is_hidden"] or 0) == 0:
                        prov_models.setdefault(mr["provider_id"], []).append(mr["model_id"])
                except Exception:
                    continue

            for r in rows:
                try:
                    pid = r["provider_id"]
                    name = r["name"] or pid
                    enabled = bool(r["is_enabled"])
                    base_url = _extract_base_url(r["endpoint_configs"],
                                                 r["default_chat_endpoint"])
                    has_key = _has_api_key(r["api_keys"])
                    result.append({
                        "source": "cherry_studio",
                        "name": name,
                        "base_url": base_url,
                        "has_key": has_key,
                        "models": prov_models.get(pid, []),
                        "enabled": enabled,
                    })
                except Exception:
                    continue
        except Exception:
            notes.append("解析 Cherry Studio 数据库异常，聚合器供应商列表为空。")
            return []
        finally:
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass
        result.sort(key=lambda x: x.get("name", ""))
        return result

    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default


# ---------------------------------------------------------------------------
# Cherry 解析辅助
# ---------------------------------------------------------------------------
def _extract_base_url(endpoint_configs, default_chat_endpoint):
    cfg = None
    if isinstance(endpoint_configs, dict):
        cfg = endpoint_configs
    elif isinstance(endpoint_configs, str) and endpoint_configs.strip():
        try:
            cfg = json.loads(endpoint_configs)
        except Exception:
            cfg = None
    if not isinstance(cfg, dict):
        return None
    if default_chat_endpoint and isinstance(cfg.get(default_chat_endpoint), dict):
        v = cfg[default_chat_endpoint]
        bu = v.get("baseUrl") or v.get("base_url")
        if bu:
            return bu
    for v in cfg.values():
        if isinstance(v, dict):
            bu = v.get("baseUrl") or v.get("base_url")
            if bu:
                return bu
    return None


def _has_api_key(api_keys_raw):
    arr = None
    if isinstance(api_keys_raw, list):
        arr = api_keys_raw
    elif isinstance(api_keys_raw, str) and api_keys_raw.strip():
        try:
            arr = json.loads(api_keys_raw)
        except Exception:
            arr = None
    if not isinstance(arr, list):
        return False
    for el in arr:
        if isinstance(el, str):
            if el.strip():
                return True
        elif isinstance(el, dict):
            for f in ("key", "apiKey", "api_key", "token", "apikey"):
                v = el.get(f)
                if isinstance(v, str) and v.strip():
                    return True
    return False


def _is_local_base(url):
    u = (url or "").lower()
    return ("127.0.0.1" in u) or ("localhost" in u) or ("[::1]" in u) or ("::1" in u)
