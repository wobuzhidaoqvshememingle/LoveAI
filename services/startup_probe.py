# -*- coding: utf-8 -*-
"""
R3.3-31 启动时全量探活（probe_all_services）

主窗口显示前跑一遍，把「现在到底哪些东西能用」如实汇报：
- 每一项独立 try/except，单项挂绝不影响其它项，也绝不阻断启动；
- 网络项最多等 PROBE_TIMEOUT_SEC=2s（连不上就算 fail/degraded，不拖慢开机）；
- L3 云端只看配置、不真发请求（不烧额度）；免费源只测公开目录可达性；
- 返回 ProbeReport（dict），供日志 / UI 状态角标使用。

整体分级（summary.overall_status）：
- ok      ：没有任何 fail / degraded；
- degraded：有 fail 但都不在核心集（免费源/onnx/whisper/playwright 挂了，
            主链路仍可用），或任何核心/非核心项 degraded；
- fail    ：核心集（SQLite DB / 本机 Ollama / L3 云端 key）有任何一项挂。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sqlite3
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SEC = 2.0

# 核心项：任何一项 fail → overall = fail
_CORE_ITEMS = {"ollama", "l3_api_key", "sqlite_db"}

# L3 云端 key 候选配置键（llm_gateway._l3_providers 同源）
_L3_KEY_KEYS = (
    "llm.l3.deepseek.api_key", "llm.deepseek.api_key",
    "llm.l3.doubao.api_key", "llm.doubao.api_key",
    "llm.cloud.api_key", "llm.api_key",
)


def _item(name: str, status: str, detail: str = "",
          fallback: str = "") -> dict:
    """统一的单项报告结构。"""
    return {"name": name, "status": status,
            "detail": str(detail)[:300], "fallback": fallback}


def _http_get(url: str, timeout: float = PROBE_TIMEOUT_SEC,
              headers: Optional[dict] = None) -> str:
    """标准库 GET 并返回响应体，2s 超时；异常向上抛由各检查项捕获。"""
    from urllib import request as urlreq
    req = urlreq.Request(url, headers=headers or {"Accept": "application/json"})
    with urlreq.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# 各检查项（单项失败不影响其它项）
# --------------------------------------------------------------------------- #
def _check_ollama(config: dict, http_get: Callable) -> dict:
    """本机 Ollama HTTP /api/tags。"""
    try:
        base = config.get("llm.free.ollama_base",
                          "http://127.0.0.1:11434/v1")
        api_base = base.rstrip("/").removesuffix("/v1")
        body = http_get(api_base + "/api/tags", timeout=PROBE_TIMEOUT_SEC,
                        headers={"Accept": "application/json"})
        data = json.loads(body) if body else {}
        models = [m.get("name") for m in data.get("models", []) if m.get("name")]
        if models:
            return _item("ollama", "ok",
                         f"{api_base} 在线，{len(models)} 个模型",
                         "本机 L2 主后端")
        return _item("ollama", "degraded",
                     f"{api_base} 在线但无已安装模型",
                     "ollama pull 一个小模型，或继续走免费路由/L3")
    except Exception as e:  # noqa: BLE001
        return _item("ollama", "fail",
                     f"连不上 {api_base}: {type(e).__name__}: {e}",
                     "启动 ollama serve，或走 L3/免费路由兜底")


def _check_l3_key(config: dict) -> dict:
    """L3 云端 api_key 是否配置（只看配置，不发请求）。"""
    configured = [k for k in _L3_KEY_KEYS if str(config.get(k, "")).strip()]
    if configured:
        return _item("l3_api_key", "ok",
                     f"已配置 {len(configured)} 个 key: {configured[0]}…",
                     "")
    return _item("l3_api_key", "fail",
                 "未配置任何 L3 云端 api_key（deepseek/doubao/generic 全空）",
                 "填 L3 key，或用免费路由/本地模型顶")


def _check_free_sources(config: dict, http_get: Callable) -> dict:
    """free_model_router 注册的免费源公开目录可达性（不真消耗配额）。

    只 GET OpenRouter 公开 /models 目录（免 key）；挂了不阻塞——
    免费路由有内置静态目录兜底。
    """
    if str(config.get("llm.free.enabled", "true")).lower() in (
            "false", "0", "no", "off"):
        return _item("free_router_sources", "ok", "免费路由整体关闭", "")
    try:
        url = config.get("llm.free.openrouter_models_url",
                         "https://openrouter.ai/api/v1/models")
        http_get(url, timeout=PROBE_TIMEOUT_SEC,
                 headers={"Accept": "application/json"})
        return _item("free_router_sources", "ok",
                     "OpenRouter 公开目录可达",
                     "")
    except Exception as e:  # noqa: BLE001
        return _item("free_router_sources", "degraded",
                     f"免费目录拉取失败: {type(e).__name__}: {e}",
                     "用内置静态免费模型列表兜底")


def _check_sqlite(config: dict) -> dict:
    """SQLite 数据库文件是否可写。"""
    try:
        rel = config.get("db_path", "data/loveai.db")
        if not os.path.isabs(rel):
            try:
                from services import runtime_paths
                path = os.path.join(runtime_paths.writable_data_dir(),
                                    os.path.basename(rel))
            except Exception:  # runtime_paths 不可用时兜底到 cwd
                path = rel
        else:
            path = rel
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        conn = sqlite3.connect(path, timeout=2.0)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _startup_probe ("
                         "id INTEGER PRIMARY KEY)")
            conn.execute("DELETE FROM _startup_probe")
            conn.commit()
        finally:
            conn.close()
        return _item("sqlite_db", "ok", f"{path} 可读写", "")
    except Exception as e:  # noqa: BLE001
        return _item("sqlite_db", "fail",
                     f"DB 不可写: {type(e).__name__}: {e}",
                     "检查磁盘空间/目录权限，人工介入")


def _check_l1_onnx(config: dict) -> dict:
    """onnxruntime L1 模型文件是否存在（L1 只做轻活，缺了非阻塞）。"""
    try:
        rel = config.get("llm.local.l1.onnx_path",
                         "models_local/smollm2-135m/model_q4.onnx")
        try:
            from services import runtime_paths
            path = runtime_paths.resolve_model_path(rel)
        except Exception:
            path = rel
        if os.path.isfile(path):
            return _item("l1_onnx_model", "ok", path, "")
        return _item("l1_onnx_model", "degraded",
                     f"L1 ONNX 模型不存在: {path}",
                     "L1 轻活不可用，不影响 L2/L3 主链路")
    except Exception as e:  # noqa: BLE001
        return _item("l1_onnx_model", "degraded",
                     f"检查异常: {type(e).__name__}: {e}", "L1 跳过")


def _check_voice_imports(config: dict) -> dict:
    """faster-whisper / pyaudio 是否可 import（语音链非阻塞）。"""
    try:
        missing = []
        for mod in ("faster_whisper", "pyaudio"):
            if importlib.util.find_spec(mod) is None:
                missing.append(mod)
        if not missing:
            return _item("voice_deps", "ok", "faster_whisper + pyaudio 可导入", "")
        return _item("voice_deps", "degraded",
                     f"缺失: {', '.join(missing)}",
                     "语音输入不可用，文字聊天不受影响")
    except Exception as e:  # noqa: BLE001
        return _item("voice_deps", "degraded",
                     f"检查异常: {type(e).__name__}: {e}", "语音链跳过")


def _check_playwright(config: dict) -> dict:
    """Playwright/Chromium 是否可用（browser_ai_hub 依赖，默认关，非阻塞）。"""
    try:
        import playwright  # noqa: F401
        try:
            from services import runtime_paths
            bundled = runtime_paths.is_bundled_browser_ai_available()
        except Exception:
            bundled = False
        env_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if bundled or (env_dir and os.path.isdir(env_dir)):
            return _item("playwright", "ok",
                         f"playwright 可导入，浏览器目录={'bundled' if bundled else env_dir}",
                         "")
        return _item("playwright", "degraded",
                     "playwright 可导入但未发现 Chromium 浏览器二进制",
                     "browser_ai 兜底不可用，默认不影响主链路")
    except Exception as e:  # noqa: BLE001
        return _item("playwright", "degraded",
                     f"playwright 不可用: {type(e).__name__}: {e}",
                     "browser_ai 兜底跳过")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def probe_all_services(config: Optional[dict] = None,
                       http_get: Optional[Callable[..., str]] = None) -> dict:
    """启动全量探活。返回 ProbeReport dict：

    {
      "items": [{"name","status"(ok/degraded/fail),"detail","fallback"}, ...],
      "summary": {"overall_status","ts","ok","degraded","fail",
                  "core_fail_items": [...]}
    }
    """
    config = config or {}
    get = http_get or _http_get

    # 每项独立 try/except：单项炸异常也兜成 fail，绝不拖死整次探活
    checks = (
        ("ollama", lambda: _check_ollama(config, get)),
        ("l3_api_key", lambda: _check_l3_key(config)),
        ("free_router_sources", lambda: _check_free_sources(config, get)),
        ("sqlite_db", lambda: _check_sqlite(config)),
        ("l1_onnx_model", lambda: _check_l1_onnx(config)),
        ("voice_deps", lambda: _check_voice_imports(config)),
        ("playwright", lambda: _check_playwright(config)),
    )
    items = []
    for name, fn in checks:
        try:
            items.append(fn())
        except Exception as e:  # noqa: BLE001
            logger.warning("探活项 %s 异常: %s", name, e)
            items.append(_item(name, "fail",
                               f"检查异常: {type(e).__name__}: {e}",
                               "人工检查该项依赖"))

    fail_items = [it for it in items if it["status"] == "fail"]
    core_fails = [it["name"] for it in fail_items if it["name"] in _CORE_ITEMS]
    if core_fails:
        overall = "fail"
    elif any(it["status"] in ("degraded", "fail") for it in items):
        # 非核心项有 fail / 或任何 degraded → 整体降级（不阻塞但要提示）
        overall = "degraded"
    else:
        overall = "ok"

    summary = {
        "overall_status": overall,
        "ts": time.time(),
        "ok": sum(1 for it in items if it["status"] == "ok"),
        "degraded": sum(1 for it in items if it["status"] == "degraded"),
        "fail": len(fail_items),
        "core_fail_items": core_fails,
    }
    report = {"items": items, "summary": summary}
    logger.info("启动探活 overall=%s（ok=%d degraded=%d fail=%d）",
                overall, summary["ok"], summary["degraded"], summary["fail"])
    return report


def write_degraded_to_logfile(report: dict,
                              logs_dir: Optional[str] = None) -> Optional[str]:
    """degraded/fail 时把明细追加写到 logs/startup_probe.log。返回写入路径。"""
    items = report.get("items", [])
    bad = [it for it in items if it["status"] in ("degraded", "fail")]
    if not bad:
        return None
    try:
        if logs_dir is None:
            try:
                from services import runtime_paths
                logs_dir = runtime_paths.writable_logs_dir()
            except Exception:
                logs_dir = "logs"
        os.makedirs(logs_dir, exist_ok=True)
        path = os.path.join(logs_dir, "startup_probe.log")
        lines = [
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"overall={report.get('summary', {}).get('overall_status')}"
        ]
        for it in bad:
            lines.append(f"  [{it['status'].upper()}] {it['name']}: "
                         f"{it['detail']} | 建议: {it['fallback']}")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return path
    except Exception as e:  # 写日志失败不能影响启动
        logger.warning("写 startup_probe.log 失败: %s", e)
        return None
