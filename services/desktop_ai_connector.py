# -*- coding: utf-8 -*-
"""DesktopAIConnector：本机 AI 桌面软件连接器（零成本 AI 能力中枢 · 路线④）。

对应需求 **R3.2-01**（v3.2.0），并服务于 R3.2-04（统一注册 / 数字诚实）与
R3.2-05（文件边界：本模块独立新建，不接线）。

扫描对象（面向开发者、带云端强模型的 AI 桌面软件；**不含**豆包/元宝等聊天壳，
**不逆向**它们的私有端口）：
  - Trae（CN 国内版 / TRAE SOLO CN / 国际 trae-global / SOLO global）
  - WorkBuddy（国内版 / WorkBuddy AI 国际版）
  - Qoder（阿里）、CodeBuddy（腾讯）

能力边界（数字诚实）：
  - 凭据「借而非硬破解」：只解析 Electron storage.json 的字段名/是否加密，
    绝不返回/记录明文 token；加密串必须由 **App 自带解密程序**（Electron
    safeStorage，App 自身密钥/进程）解开，本模块不自带硬解逻辑。
  - 本机安全 shim：127.0.0.1 随机端口 + 进程内随机 secret；真实 token 只存在
    CredentialVault 进程内映射，转发远程时才注入，绝不回传上层。
  - 离线状态一律标 unknown/needs_login，不宣称 available；客户端大版本更新
    导致协议漂移时抛 WireProtocolDrift，上层可降级（R3.2-04 逐级降级）。
  - 不自动切账号（换人 = 换计费）：发现多个账号时只出一个描述符，不替用户选号。

本模块只用 Python 标准库；所有外部副作用（注册表/文件系统/解密/网络）经构造函数
注入，离线测试用 Fake 替换。**不 import free_model_router / local_ai_scanner**
（本期不接线，未来由封包任务把 SourceDescriptor 映射为 free_model_router.Candidate）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 异常（typed 降级信号）
# ---------------------------------------------------------------------------
class DesktopAIError(Exception):
    """本模块全部异常的基类。"""


class AppNotInstalled(DesktopAIError):
    """未在本机发现该 App（程序目录 / 卸载注册表 / 数据目录均无命中）。"""


class CredentialMissing(DesktopAIError):
    """storage.json 中没有可用凭据记录。"""


class CredentialDecryptUnavailable(DesktopAIError):
    """加密凭据必须由 App 自带解密程序解开；本机无法定位/执行该程序。"""


class WireProtocolDrift(DesktopAIError):
    """App 大版本更新导致远程协议漂移；上层应降级到其它通道（R3.2-04）。"""


class ShimError(DesktopAIError):
    """安全 shim 生命周期错误。"""


# ---------------------------------------------------------------------------
# 特征表 APP_REGISTRY
# ---------------------------------------------------------------------------
# 每项字段：
#   app_id / display / vendor / flavor(cn|cn_solo|global|global_solo)
#   process_names / exe_names：进程与可执行名（小写不敏感匹配）
#   data_dir_spellings：Electron 用户数据目录多拼写候选（%APPDATA% 下并列探测）
#   program_dir_spellings：%LOCALAPPDATA%\Programs\<拼写> 安装目录候选
#   uninstall_display_keywords：卸载注册表 DisplayName 关键字
#   storage_rel：storage.json 相对用户数据目录的路径
#   remote_protocol：trae_llm_utils_chat | openai_passthrough
#   chat_endpoint：远程聊天端点（Trae 国内/国际端点已取证；其余需联网抽验）
#   usage_endpoints：只读用量端点候选
#   model_group：MODEL_GROUPS 键
APP_REGISTRY = [
    {
        "app_id": "trae_cn",
        "display": "Trae",
        "vendor": "ByteDance",
        "flavor": "cn",
        "process_names": ["Trae.exe"],
        "exe_names": ["Trae.exe"],
        "data_dir_spellings": ["Trae", "trae", "Trae CN"],
        "program_dir_spellings": ["Trae", "Trae CN"],
        "uninstall_display_keywords": ["Trae"],
        "storage_rel": r"User\globalStorage\storage.json",
        "remote_protocol": "trae_llm_utils_chat",
        "chat_endpoint": "https://trae-api-cn.mchost.guru/api/agent/v3/llm_utils_chat",
        "usage_endpoints": ["https://api.trae.cn/trae/api/v2/pay",
                            "https://api.trae.cn/trae/api/v2/ug"],
        "identity_headers": ["x-device-id", "x-machine-id", "x-device-cpu",
                             "x-ide-version-code"],
        "model_group": "trae_solo_cn",
    },
    {
        "app_id": "trae_solo_cn",
        "display": "TRAE SOLO",
        "vendor": "ByteDance",
        "flavor": "cn_solo",
        "process_names": ["Trae SOLO.exe", "TRAE SOLO CN.exe"],
        "exe_names": ["Trae SOLO.exe"],
        "data_dir_spellings": ["TRAE SOLO CN", "Trae SOLO", "TRAE SOLO"],
        "program_dir_spellings": ["TRAE SOLO CN", "Trae SOLO"],
        "uninstall_display_keywords": ["TRAE SOLO"],
        "storage_rel": r"User\globalStorage\storage.json",
        "remote_protocol": "trae_llm_utils_chat",
        "chat_endpoint": "https://trae-api-cn.mchost.guru/api/agent/v3/llm_utils_chat",
        "usage_endpoints": ["https://api.trae.cn/trae/api/v2/pay",
                            "https://api.trae.cn/trae/api/v2/ug"],
        "identity_headers": ["x-device-id", "x-machine-id", "x-device-cpu",
                             "x-ide-version-code"],
        "model_group": "trae_solo_cn",
    },
    {
        "app_id": "trae_global",
        "display": "Trae",
        "vendor": "ByteDance",
        "flavor": "global",
        "process_names": ["Trae.exe"],
        "exe_names": ["Trae.exe"],
        "data_dir_spellings": ["Trae", "trae-global", "Trae Global"],
        "program_dir_spellings": ["Trae", "trae-global"],
        "uninstall_display_keywords": ["Trae"],
        "storage_rel": r"User\globalStorage\storage.json",
        "remote_protocol": "trae_llm_utils_chat",
        "chat_endpoint": "https://coresg-normal.trae.ai/api/agent/v3/llm_utils_chat",
        "usage_endpoints": [],
        "identity_headers": ["x-device-id", "x-machine-id", "x-device-cpu",
                             "x-ide-version-code"],
        "model_group": "trae_solo_global",
    },
    {
        "app_id": "trae_solo_global",
        "display": "TRAE SOLO",
        "vendor": "ByteDance",
        "flavor": "global_solo",
        "process_names": ["Trae SOLO.exe", "TRAE SOLO.exe"],
        "exe_names": ["Trae SOLO.exe"],
        "data_dir_spellings": ["Trae SOLO", "TRAE SOLO", "trae-global"],
        "program_dir_spellings": ["Trae SOLO", "trae-global"],
        "uninstall_display_keywords": ["TRAE SOLO"],
        "storage_rel": r"User\globalStorage\storage.json",
        "remote_protocol": "trae_llm_utils_chat",
        "chat_endpoint": "https://coresg-normal.trae.ai/api/agent/v3/llm_utils_chat",
        "usage_endpoints": [],
        "identity_headers": ["x-device-id", "x-machine-id", "x-device-cpu",
                             "x-ide-version-code"],
        "model_group": "trae_solo_global",
    },
    {
        "app_id": "workbuddy",
        "display": "WorkBuddy",
        "vendor": "WorkBuddy",
        "flavor": "cn",
        "process_names": ["WorkBuddy.exe"],
        "exe_names": ["WorkBuddy.exe"],
        "data_dir_spellings": ["WorkBuddy", "workbuddy"],
        "program_dir_spellings": ["WorkBuddy"],
        "uninstall_display_keywords": ["WorkBuddy"],
        "storage_rel": r"User\globalStorage\storage.json",
        "remote_protocol": "openai_passthrough",
        "chat_endpoint": None,   # 需联网抽验
        "usage_endpoints": [],
        "identity_headers": [],
        "model_group": "workbuddy_cn",
    },
    {
        "app_id": "workbuddy_ai",
        "display": "WorkBuddy AI",
        "vendor": "WorkBuddy",
        "flavor": "global",
        "process_names": ["WorkBuddy AI.exe", "WorkBuddy.exe"],
        "exe_names": ["WorkBuddy AI.exe"],
        "data_dir_spellings": ["WorkBuddy AI", "WorkBuddyAI", "workbuddy-ai"],
        "program_dir_spellings": [],   # 国际版无已验证默认安装位置，仅注册表
        "uninstall_display_keywords": ["WorkBuddy AI"],
        "storage_rel": r"User\globalStorage\storage.json",
        "remote_protocol": "openai_passthrough",
        "chat_endpoint": None,   # 需联网抽验
        "usage_endpoints": [],
        "identity_headers": [],
        "model_group": "workbuddy_global",
    },
    {
        "app_id": "qoder",
        "display": "Qoder",
        "vendor": "Alibaba",
        "flavor": "cn",
        "process_names": ["Qoder.exe"],
        "exe_names": ["Qoder.exe"],
        "data_dir_spellings": ["Qoder", "qoder"],
        "program_dir_spellings": ["Qoder"],
        "uninstall_display_keywords": ["Qoder"],
        "storage_rel": r"User\globalStorage\storage.json",
        "remote_protocol": "openai_passthrough",
        "chat_endpoint": None,   # 需联网抽验
        "usage_endpoints": [],
        "identity_headers": [],
        "model_group": "qoder_cn",
    },
    {
        "app_id": "codebuddy",
        "display": "CodeBuddy",
        "vendor": "Tencent",
        "flavor": "cn",
        "process_names": ["CodeBuddy.exe"],
        "exe_names": ["CodeBuddy.exe"],
        "data_dir_spellings": ["CodeBuddy", "codebuddy"],
        "program_dir_spellings": ["CodeBuddy"],
        "uninstall_display_keywords": ["CodeBuddy"],
        "storage_rel": r"User\globalStorage\storage.json",
        "remote_protocol": "openai_passthrough",
        "chat_endpoint": None,   # 需联网抽验
        "usage_endpoints": [],
        "identity_headers": [],
        "model_group": "codebuddy_cn",
    },
]


# ---------------------------------------------------------------------------
# 模型组 MODEL_GROUPS（零 key、零配置自动出现；国内/国际并存）
# ---------------------------------------------------------------------------
MODEL_GROUPS = {
    # Trae SOLO 通道（国内）：端点 trae-api-cn.mchost.guru
    "trae_solo_cn": [
        "DeepSeek-V4-Pro-Official", "DeepSeek-V4-Flash-Official",
        "GLM-5.3", "GLM-5.2", "Kimi-K3", "MiniMax-M3",
        "Qwen3.8-Max", "Doubao-Seed",
    ],
    # Trae SOLO 通道（国际）：端点 coresg-normal.trae.ai，阵容与国内不同
    "trae_solo_global": [
        "DeepSeek-V4-Pro-Official", "DeepSeek-V4-Flash-Official",
        "GLM-5.3", "Kimi-K3", "MiniMax-M3",
    ],
    "workbuddy_cn": [
        "GLM-5.3", "GLM-5.2", "DeepSeek-V4-Pro",
        "DeepSeek-V4-Flash", "Kimi-K3", "MiniMax-M3",
    ],
    "workbuddy_global": [
        "GLM-5.3", "DeepSeek-V4-Pro", "Kimi-K3", "MiniMax-M3",
    ],
    "qoder_cn": [
        "Qwen3.8-Max", "Qwen3.8-Flash", "DeepSeek-V4-Pro",
    ],
    "codebuddy_cn": [
        "DeepSeek-V4-Pro", "DeepSeek-V4-Flash", "Doubao-Seed",
    ],
}


# 需联网/登录后才能逐条抽验的字段（不编造确定性结论）
ONLINE_VERIFICATION_NEEDED = [
    "Trae llm_utils_chat 请求体精确 wire 形状（query/chat_history/tools 字段名）",
    "Trae SSE chunk 精确形状（pending function_call 增量字段名与 id 下发时机）",
    "Trae identity headers 全量设备指纹字段取值生成规则（x-device-id 等 10 头）",
    "WorkBuddy 国内版/国际版远程聊天端点、鉴权头与请求/响应字段",
    "WorkBuddy 国际版模型目录（App 界面接口，UA 分流，上游可能改动）",
    "Qoder / CodeBuddy 远程端点、鉴权头与模型目录字段",
    "用量接口（pay/ug）返回 JSON 中余额/积分字段的精确路径",
    "v10/v11 safeStorage 加密串由 App 自带解密程序解开的精确调用方式",
    "Trae 错误码（1005 档位不足 / 4001·4011 上游不提供 / 限流）映射",
]


# ---------------------------------------------------------------------------
# 纯函数：候选路径拼装
# ---------------------------------------------------------------------------
def build_storage_path_candidates(app, env_map: dict) -> list:
    """纯函数：把 App 的多拼写数据目录与 storage_rel 拼成候选 storage.json 路径。

    app：APP_REGISTRY 条目或 app_id 字符串。env_map 至少含 APPDATA。
    只做字符串拼装，不碰文件系统（文件系统读取在 SystemOps）。
    """
    entry = _as_app_entry(app)
    appdata = (env_map or {}).get("APPDATA") or ""
    out = []
    for spelling in entry.get("data_dir_spellings", []):
        if not spelling:
            continue
        out.append(os.path.join(appdata, spelling, entry["storage_rel"]))
    return out


def build_program_dir_candidates(app, env_map: dict) -> list:
    """纯函数：%LOCALAPPDATA%\\Programs\\<拼写> 安装目录候选（先于注册表查）。"""
    entry = _as_app_entry(app)
    local = (env_map or {}).get("LOCALAPPDATA") or ""
    out = []
    for spelling in entry.get("program_dir_spellings", []):
        if not spelling:
            continue
        out.append(os.path.join(local, "Programs", spelling))
    return out


def _as_app_entry(app):
    if isinstance(app, dict):
        return app
    for e in APP_REGISTRY:
        if e["app_id"] == app:
            return e
    raise AppNotInstalled(f"未知 app_id: {app!r}")


# ---------------------------------------------------------------------------
# 凭据借取：storage.json 解析（只记录元信息，绝不返回明文）
# ---------------------------------------------------------------------------
@dataclass
class CredentialRecord:
    """一条凭据记录：只有字段名/是否加密/归属，**没有值**。"""
    field_name: str
    encrypted: bool = False
    app_id: Optional[str] = None
    flavor: Optional[str] = None


_CRED_HINT = ("token", "refresh", "access", "account", "auth",
              "session", "secret", "credential", "license")
# Electron safeStorage 加密串：v10/v11 前缀 + base64 blob（仅识别，不硬解）
_SAFE_BLOB_RE = re.compile(r"^(?:v10|v11)[A-Za-z0-9+/=]{8,}$")


def parse_storage_credentials(json_text: str, app_id: str = None,
                              flavor: str = None) -> list:
    """纯函数：解析 Electron storage.json，返回 CredentialRecord 列表。

    只做两件事：①识别疑似凭据的字段名；②识别 v10/v11 safeStorage 加密串。
    **明文值绝不放进返回值、绝不记日志**——调用方拿到记录后才决定是否向
    App 自带解密程序借解加密串。
    """
    records: list = []
    if json_text is None:
        return records
    try:
        data = json.loads(json_text) if isinstance(json_text, str) else json_text
    except Exception:
        return records

    def _consider(name: str, value: str):
        low = name.lower()
        if not any(h in low for h in _CRED_HINT):
            return
        if not value:
            return
        encrypted = bool(_SAFE_BLOB_RE.match(value.strip()))
        records.append(CredentialRecord(field_name=name, encrypted=encrypted,
                                       app_id=app_id, flavor=flavor))

    def _walk(obj, prefix: str):
        if isinstance(obj, dict):
            for k, v in obj.items():
                name = f"{prefix}.{k}" if prefix else str(k)
                if isinstance(v, str):
                    _consider(name, v)
                else:
                    _walk(v, name)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _walk(v, f"{prefix}[{i}]")

    _walk(data, "")
    return records


# ---------------------------------------------------------------------------
# 解密接口：必须走 App 自带解密程序（不自带硬破解）
# ---------------------------------------------------------------------------
class AppDecryptor:
    """解密器接口。真实实现须以 App 自身身份执行其 Electron safeStorage
    运行时（例如定位 %LOCALAPPDATA%\\Programs\\<App>\\<App>.exe 后以 App
    身份调起解密入口），绝不使用本进程硬编码密钥。"""

    def decrypt(self, app, sealed_blob: str) -> str:  # pragma: no cover
        raise NotImplementedError


class UnavailableDecryptor:
    """无法定位 App 自带解密程序时的降级实现（抛 typed 异常，不假装成功）。"""

    def decrypt(self, app, sealed_blob: str) -> str:
        entry = _as_app_entry(app)
        raise CredentialDecryptUnavailable(
            f"无法定位 {entry['display']} 自带的 safeStorage 解密程序，"
            f"加密凭据暂不可借取（不自带硬破解）。")


# ---------------------------------------------------------------------------
# CredentialVault：真实 token 只存在本进程内，对外只发随机句柄
# ---------------------------------------------------------------------------
class CredentialVault:
    """进程内 token 保管库。store 返回随机 secret 句柄；真实 token 映射不出
    本模块、不进日志、不进任何对外描述符。"""

    def __init__(self):
        self._map: dict = {}
        self._lock = threading.Lock()

    def store(self, real_token: str) -> str:
        handle = secrets.token_urlsafe(32)
        with self._lock:
            self._map[handle] = real_token
        return handle

    def resolve(self, handle: str) -> str:
        with self._lock:
            if handle not in self._map:
                raise CredentialMissing("句柄不存在或已释放")
            return self._map[handle]

    def release(self, handle: str):
        with self._lock:
            self._map.pop(handle, None)


# ---------------------------------------------------------------------------
# 安全 shim：鉴权 / 路由（纯函数）
# ---------------------------------------------------------------------------
def authorize_request(headers: dict, expected_secret: str) -> bool:
    """纯函数：每个请求必须携带进程内随机 secret（Authorization: Bearer
    或 X-LoveAI-Secret）。缺失/错误一律拒绝；恒定时间比较。"""
    if not expected_secret:
        return False
    presented = ""
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    auth = str(lowered.get("authorization", ""))
    if auth.lower().startswith("bearer "):
        presented = auth.split(" ", 1)[1].strip()
    elif lowered.get("x-loveai-secret"):
        presented = str(lowered["x-loveai-secret"]).strip()
    if not presented:
        return False
    return secrets.compare_digest(presented, expected_secret)


def route_request(method: str, path: str, body: dict, secret: str,
                  *, models: list = None, remote_caller=None,
                  app=None) -> tuple:
    """纯函数路由分发：返回 (status, body_dict)。

    - GET  /v1/models            -> 200 模型目录
    - POST /v1/chat/completions  -> 经 openai_to_remote_chat 翻译后交给注入的
                                    remote_caller（真实 token 在 caller 内部用
                                    secret 句柄向 Vault 解析并注入，不外泄）
    - 其它                        -> 404；未接远程通道 -> 503
    """
    method = (method or "GET").upper()
    path = (path or "").split("?")[0]
    if path == "/v1/models" and method == "GET":
        catalog = models or []
        return 200, {"object": "list",
                     "data": [{"id": m, "object": "model", "owned_by": "desktop_app"}
                              for m in catalog]}
    if path == "/v1/chat/completions" and method == "POST":
        if remote_caller is None:
            return 503, {"error": {"message": "desktop connector not connected",
                                   "type": "connector_unavailable"}}
        remote_req = openai_to_remote_chat(app, body or {})
        result = remote_caller(remote_req, secret=secret)
        return 200, result
    return 404, {"error": {"message": "not found", "type": "not_found"}}


# ---------------------------------------------------------------------------
# Bridge 协议翻译（OpenAI <-> 远程会话，全部纯函数可单测）
# ---------------------------------------------------------------------------
def openai_to_remote_chat(app, openai_req: dict) -> dict:
    """OpenAI ChatCompletion 入参 -> 远程会话请求。

    Trae（llm_utils_chat 风格）：system 与历史消息 -> chat_history，最后一条
    user 消息 -> query；tools -> function_call 定义。
    通用透传（WorkBuddy/Qoder/CodeBuddy）：OpenAI 形状整体透传，端点由
    app.chat_endpoint 声明（未取证端点为 None，调用方收到 503 前会如实报错）。
    """
    entry = _as_app_entry(app)
    protocol = entry.get("remote_protocol", "openai_passthrough")
    messages = openai_req.get("messages") or []
    system = [m.get("content", "") for m in messages if m.get("role") == "system"]
    convo = [m for m in messages if m.get("role") != "system"]
    query = ""
    for m in reversed(convo):
        if m.get("role") == "user" and m.get("content"):
            query = m["content"]
            break
    remote_tools = []
    for t in (openai_req.get("tools") or []):
        fn = t.get("function") or {}
        remote_tools.append({"type": "function",
                             "function": {"name": fn.get("name", ""),
                                          "description": fn.get("description", ""),
                                          "parameters": fn.get("parameters", {})}})
    if protocol == "trae_llm_utils_chat":
        body = {
            "model": openai_req.get("model", ""),
            "query": query,
            "chat_history": ([{"role": "system", "content": s} for s in system if s] +
                             [{"role": m.get("role"), "content": m.get("content")}
                              for m in convo]),
            "stream": True,
        }
        if remote_tools:
            body["tools"] = remote_tools
        return {"method": "POST", "endpoint": entry["chat_endpoint"],
                "body": body, "auth": "app_credential",
                "identity_headers": True}
    # 通用 OpenAI 风格透传适配器（真实映射，非空壳）
    body = dict(openai_req)
    body["stream"] = True
    return {"method": "POST", "endpoint": entry.get("chat_endpoint"),
            "body": body, "auth": "app_credential", "identity_headers": False}


def parse_sse_events(text: str) -> list:
    """纯函数：标准 SSE 解析。按 \\r?\\n 切行；data: 行累积（多 data 行
    用 \\n 连接）；空行分隔事件；data 为 [DONE] 的事件标记 done=True。"""
    events: list = []
    if not text:
        return events
    data_lines: list = []
    event_name = "message"

    def _flush():
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        done = raw.strip() == "[DONE]"
        events.append({"event": event_name, "data": raw, "done": done})

    for line in re.split(r"\r?\n", text):
        if line == "":
            _flush()
            data_lines = []
            event_name = "message"
            continue
        if line.startswith(":"):      # 注释行（心跳）
            continue
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    _flush()
    return events


def remote_events_to_openai(app, events: list, model: str = None) -> dict:
    """纯函数：远程 SSE 增量 -> OpenAI ChatCompletion（聚合后一次性返回）。

    - content delta 聚合到 choices[0].message.content；
    - pending function_call / tool_calls 增量按 index 聚合：id 可能在后续
      chunk 才下发，arguments 为增量拼接；
    - finish_reason：有工具调用 -> tool_calls，否则 stop。
    """
    content_parts: list = []
    slots: dict = {}
    order: list = []

    def _slot(idx: int) -> dict:
        if idx not in slots:
            slots[idx] = {"id": "", "name": "", "arguments": ""}
            order.append(idx)
        return slots[idx]

    for ev in events:
        if ev.get("done"):
            break
        raw = ev.get("data") or ""
        try:
            chunk = json.loads(raw)
        except Exception:
            continue
        for c in (chunk.get("choices") or []):
            delta = c.get("delta") or {}
            if isinstance(delta.get("content"), str):
                content_parts.append(delta["content"])
            for tc in (delta.get("tool_calls") or []):
                idx = tc.get("index", 0)
                slot = _slot(idx)
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            # 旧式 function_call（Trae pending 风格）
            fc = delta.get("function_call") or chunk.get("function_call")
            if isinstance(fc, dict):
                slot = _slot(0)
                if fc.get("name"):
                    slot["name"] = fc["name"]
                if fc.get("arguments"):
                    slot["arguments"] += fc["arguments"]
                if fc.get("id"):
                    slot["id"] = fc["id"]

    tool_calls_out = []
    for idx in order:
        slot = slots[idx]
        if not slot["name"]:
            continue
        tool_calls_out.append({
            "id": slot["id"] or f"call_{idx}",
            "type": "function",
            "function": {"name": slot["name"],
                         "arguments": slot["arguments"]},
        })
    message = {"role": "assistant", "content": "".join(content_parts)}
    if tool_calls_out:
        message["tool_calls"] = tool_calls_out
    finish = "tool_calls" if tool_calls_out else "stop"
    return {
        "id": "chatcmpl-desktopai",
        "object": "chat.completion",
        "model": model or (app.get("display", "desktop") if isinstance(app, dict) else "desktop"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {},
    }


def build_tool_result_message(app, tool_call_id: str, output: str) -> dict:
    """纯函数：工具在本地执行后的结果，回传远程会话的消息构造。"""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": output}


# ---------------------------------------------------------------------------
# 只读用量/积分解析（取不到如实 unknown，不编造）
# ---------------------------------------------------------------------------
def parse_usage(payload) -> dict:
    """纯函数：从用量接口 payload best-effort 解析余额/已用/总额。

    任何字段取不到一律 "unknown"，绝不臆造数值。"""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    flat: dict = {}

    def _collect(obj, depth: int):
        if depth > 2 or not isinstance(obj, dict):
            return
        for k, v in obj.items():
            if isinstance(v, (int, float)):
                flat[str(k).lower()] = v
            elif isinstance(v, dict):
                _collect(v, depth + 1)

    _collect(payload, 0)
    out = {"balance": "unknown", "used": "unknown", "total": "unknown",
           "unit": "unknown", "keys_seen": sorted(flat)}
    for k in ("balance", "available", "credit", "credits", "remaining", "points"):
        if k in flat:
            out["balance"] = flat[k]
            break
    for k in ("used", "consumed"):
        if k in flat:
            out["used"] = flat[k]
            break
    for k in ("total", "quota"):
        if k in flat:
            out["total"] = flat[k]
            break
    return out


# ---------------------------------------------------------------------------
# 系统操作隔离层（可注入；真实实现仿 local_ai_scanner.WindowsSystem）
# ---------------------------------------------------------------------------
class SystemOps:
    """文件系统 / 注册表只读操作接口，全部异常静默返回空值。"""

    def env(self) -> dict:  # pragma: no cover - 接口
        raise NotImplementedError

    def exists(self, path: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    def read_text(self, path: str) -> Optional[str]:  # pragma: no cover
        raise NotImplementedError

    def uninstall_entries(self) -> list:  # pragma: no cover
        raise NotImplementedError


class WindowsSystemOps(SystemOps):
    """真实 Windows 只读快照：注册表 Uninstall 键 + 文件系统（懒加载 winreg）。"""

    UNINSTALL_PATHS = [
        (0x80000002, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (0x80000002, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (0x80000001, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    def env(self) -> dict:
        return dict(os.environ)

    def exists(self, path: str) -> bool:
        try:
            return os.path.exists(path)
        except Exception:
            return False

    def read_text(self, path: str) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except Exception:
            return None

    def uninstall_entries(self) -> list:
        out = []
        try:
            import winreg
        except Exception:
            return out

        def rv(key, name):
            try:
                v, _ = winreg.QueryValueEx(key, name)
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
                    child = winreg.EnumKey(root, i)
                    k = winreg.OpenKey(root, child)
                    name = rv(k, "DisplayName")
                    if not name:
                        continue
                    out.append({"name": str(name).strip(),
                                "install_location": str(rv(k, "InstallLocation") or "").strip()})
                except Exception:
                    continue
        return out


# ---------------------------------------------------------------------------
# 对外注册描述符（数字诚实；未来映射 free_model_router.Candidate，本期不 import）
# ---------------------------------------------------------------------------
@dataclass
class SourceDescriptor:
    source_id: str
    kind: str = "desktop_app"
    display: str = ""
    flavor: str = ""
    models: list = field(default_factory=list)
    openai_base_url: str = ""        # shim 地址（start 后才有值）
    requires_login: bool = True
    zero_key: bool = True
    status: str = "unknown"          # unknown|available|needs_login|failed|unavailable
    confidence: float = 0.0
    notes: str = ""


# ---------------------------------------------------------------------------
# DesktopAIConnector
# ---------------------------------------------------------------------------
class DesktopAIConnector:
    """本机 AI 桌面软件连接器。只读发现 + 凭据借取元信息 + 描述符输出。

    构造注入：system_ops（文件/注册表）、decryptor（App 自带解密程序）、clock。
    """

    def __init__(self, config: dict = None, system_ops: SystemOps = None,
                 decryptor: AppDecryptor = None, clock=None):
        self.config = config or {}
        self.system_ops = system_ops or WindowsSystemOps()
        self.decryptor = decryptor or UnavailableDecryptor()
        self.clock = clock
        self.registry = APP_REGISTRY
        self.vault = CredentialVault()

    # ------------------------------------------------------------------
    # 发现（只读；先 Programs 目录，再卸载注册表，再 Electron 数据目录）
    # ------------------------------------------------------------------
    def discover(self, app) -> Optional[SourceDescriptor]:
        entry = _as_app_entry(app)
        ops = self.system_ops
        env = ops.env()

        # ① 先查 %LOCALAPPDATA%\Programs\<App>
        install_loc = None
        for p in build_program_dir_candidates(entry, env):
            if ops.exists(p):
                install_loc = p
                break
        # ② 再查卸载注册表（HKCU/HKLM Uninstall 键）
        if install_loc is None:
            for e in ops.uninstall_entries():
                name = (e.get("name") or "").lower()
                if any(kw.lower() in name for kw in entry["uninstall_display_keywords"]):
                    install_loc = e.get("install_location") or e.get("name") or ""
                    break

        # ③ Electron 用户数据目录：并列多拼写探测 storage.json
        storage_path = None
        for sp in build_storage_path_candidates(entry, env):
            if ops.exists(sp):
                storage_path = sp
                break

        if install_loc is None and storage_path is None:
            return None  # 未安装：不宣称任何可用

        creds: list = []
        if storage_path:
            text = ops.read_text(storage_path)
            if text:
                creds = parse_storage_credentials(text, entry["app_id"], entry["flavor"])

        # 数字诚实：有凭据≠远程可达；离线一律不宣称 available
        if not creds:
            status = "needs_login"
            notes = "已发现安装，但未在 storage.json 识别到登录凭据记录。"
        else:
            status = "unknown"
            enc = sum(1 for c in creds if c.encrypted)
            notes = (f"识别到 {len(creds)} 条凭据记录（其中 {enc} 条 safeStorage "
                     f"加密，须由 App 自带解密程序借取）；远程可达性待联网抽验，"
                     f"不自动切账号。")

        return SourceDescriptor(
            source_id=entry["app_id"],
            display=entry["display"],
            flavor=entry["flavor"],
            models=list(MODEL_GROUPS.get(entry.get("model_group"), [])),
            requires_login=True,
            zero_key=True,
            status=status,
            confidence=0.0,
            notes=notes,
        )

    def discover_all(self) -> list:
        """扫描全注册表。未安装的 App 不产出描述符（不夸大可用性）。"""
        out = []
        for entry in self.registry:
            try:
                desc = self.discover(entry)
            except Exception as e:  # 单个 App 失败不拖垮整批
                logger.info("发现 %s 失败: %s", entry.get("app_id"), e)
                continue
            if desc is not None:
                out.append(desc)
        return out


# ---------------------------------------------------------------------------
# SecureShim：127.0.0.1 随机端口 + 进程内 secret（守护线程生命周期）
# ---------------------------------------------------------------------------
class SecureShim:
    """本机安全 shim。绑定 ('127.0.0.1', 0) 随机端口；每请求校验进程内随机
    secret；真实 token 仅在 remote_caller 转发远程时由 Vault 注入，绝不回传。"""

    def __init__(self, app, vault: CredentialVault = None,
                 remote_caller=None, models: list = None,
                 host: str = "127.0.0.1"):
        self.app = _as_app_entry(app)
        self.vault = vault or CredentialVault()
        self.remote_caller = remote_caller
        self.models = models or list(MODEL_GROUPS.get(self.app["model_group"], []))
        self.host = host
        self._secret = secrets.token_urlsafe(32)
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def client_secret(self) -> str:
        """发给同进程上层的随机句柄；真实 token 永远不离开本进程。"""
        return self._secret

    @property
    def base_url(self) -> str:
        if self._server is None:
            return ""
        return f"http://{self.host}:{self._server.server_address[1]}/v1"

    def start(self):
        if self._server is not None:
            raise ShimError("shim 已在运行")
        server = ThreadingHTTPServer((self.host, 0), _make_shim_handler(self))
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever,
                                        daemon=True, name="desktop-ai-shim")
        self._thread.start()
        return self.base_url

    def stop(self):
        if self._server is None:
            return
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        self._server = None
        self._thread = None


def _make_shim_handler(shim: SecureShim):
    class _ShimHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # 静默（绝不打印请求体/凭据）
            return

        def _send(self, status: int, obj: dict):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method: str):
            headers = {k: v for k, v in self.headers.items()}
            if not authorize_request(headers, shim.client_secret):
                self._send(401, {"error": {"message": "unauthorized",
                                           "type": "invalid_secret"}})
                return
            raw = b""
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    raw = self.rfile.read(n)
            except Exception:
                raw = b""
            try:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                body = {}
            status, resp = route_request(method, self.path, body, shim.client_secret,
                                         models=shim.models,
                                         remote_caller=shim.remote_caller,
                                         app=shim.app)
            self._send(status, resp)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

    return _ShimHandler


# ---------------------------------------------------------------------------
# 优雅降级探测：playwright 缺失时不炸（路线②浏览器自动化不在本模块范围）
# ---------------------------------------------------------------------------
def playwright_available(importer=None) -> bool:
    """探测 playwright 是否可用；任何异常（未安装/导入失败）一律返回 False，
    由调用方走「本地凭据 + shim」通路降级，绝不向用户抛错。"""
    if importer is None:
        def importer():
            import playwright  # noqa: F401
            return True
    try:
        return bool(importer())
    except Exception:
        return False
