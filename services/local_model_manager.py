# -*- coding: utf-8 -*-
r"""
LoveAI - 本地模型管理器（local_model_manager）

职责（全程通过 Ollama HTTP / CLI，**不使用 llama-cpp-python**）：
1. 把随包 ``models_local`` 下的 GGUF 模型**幂等注册**到（便携/本机）Ollama；
2. 固化本地模型分工：
   - ``loveai-qwen15b`` = Qwen2.5-1.5B-Instruct Q4_K_M → **离线主力**
     （中文对话 / 轻分析 / 制服·引导双路线话术草稿）；
   - ``loveai-qwen05b`` = Qwen2.5-0.5B-Instruct Q4_K_M → **快速轻量兜底**
     （超短回复 / 意图 / 低延迟）；
   - SmolLM2-135m（ONNX）中文弱，**保留**作非中文超轻量（由 L1 ONNX 路径使用，
     不在此注册）。
3. 提供 ChatML 模板与显式上下文长度（num_ctx），并给出按任务选模型的 chat 入口。

硬约束：**不删除任何已有模型文件 / Ollama 模型**；只在缺失时 create。

复杂中文理解与深度推理（如临床化人格判定）应分流云端，本模块只承担离线可完成的部分。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger("LoveAI.local_model_manager")

# 注册过程全局锁：避免并发/重复触发的 ensure_registered 竞争导致重复导入 blob
_REGISTER_LOCK = threading.Lock()

# Ollama 默认服务地址
DEFAULT_HOST = "http://127.0.0.1:11434"

# 显式上下文长度（Qwen2.5 支持更长；CPU 下 8192 兼顾内存/速度）
DEFAULT_NUM_CTX = 8192

# Qwen2.5 官方 ChatML 模板（与 gguf 内嵌一致，显式写出以防模板漂移）
CHATML_TEMPLATE = (
    "{{- if .System }}<|im_start|>system\n"
    "{{ .System }}<|im_end|>\n"
    "{{ end }}{{- range $i, $_ := .Messages }}"
    "<|im_start|>{{ .Role }}\n"
    "{{ .Content }}<|im_end|>\n"
    "{{ end }}<|im_start|>assistant\n"
)

# 本地模型注册表（tag -> 元信息）。file 相对 models_local 根。
REGISTRY: dict[str, dict] = {
    "loveai-qwen15b": {
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "role": "离线主力：中文对话/轻分析/双路线话术草稿",
        "temperature": 0.7,
        "top_p": 0.8,
        "repeat_penalty": 1.05,
        "num_ctx": DEFAULT_NUM_CTX,
    },
    "loveai-qwen05b": {
        "file": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "role": "快速轻量兜底：超短回复/意图/低延迟",
        "temperature": 0.7,
        "top_p": 0.8,
        "repeat_penalty": 1.05,
        "num_ctx": DEFAULT_NUM_CTX,
    },
}

# 任务类型 -> 默认 tag（供 llm_gateway 选择）
TASK_MODEL = {
    "dialogue": "loveai-qwen15b",
    "analysis": "loveai-qwen15b",
    "draft": "loveai-qwen15b",
    "intent": "loveai-qwen05b",
    "quick": "loveai-qwen05b",
}


def build_modelfile(tag: str, gguf_abs: str) -> str:
    """为指定 tag 生成 Modelfile 文本（FROM 绝对路径 + ChatML + 参数）。"""
    meta = REGISTRY[tag]
    lines = [
        f"FROM {gguf_abs}",
        f'TEMPLATE """{CHATML_TEMPLATE}"""',
        f"PARAMETER temperature {meta['temperature']}",
        f"PARAMETER top_p {meta['top_p']}",
        f"PARAMETER repeat_penalty {meta['repeat_penalty']}",
        f"PARAMETER num_ctx {meta['num_ctx']}",
        "PARAMETER stop <|im_start|>",
        "PARAMETER stop <|im_end|>",
    ]
    return "\n".join(lines) + "\n"


def list_tags(host: str = DEFAULT_HOST, timeout: float = 5.0) -> set[str]:
    """通过 GET /api/tags 返回已注册的 tag 基础名集合（去掉 :latest）。"""
    req = urllib.request.Request(host.rstrip("/") + "/api/tags")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    tags: set[str] = set()
    for m in data.get("models", []):
        name = m.get("name", "")
        base = name.split(":", 1)[0]
        if base:
            tags.add(base)
    return tags


def sha256_file(path: str, buf_size: int = 1024 * 1024) -> str:
    """计算文件内容的 sha256（流式，避免一次性读入大 gguf）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf_size), b""):
            h.update(chunk)
    return h.hexdigest()


def preseed_model_blob(gguf_abs: str, models_store_dir: Optional[str],
                       digest: Optional[str] = None) -> bool:
    """把随包 gguf 以**同卷硬链接**预置为 Ollama blob，零额外磁盘占用。

    Ollama 的模型层 blob 名即 gguf 内容的 sha256。若该 blob 已存在，
    后续 ``ollama create`` 命中相同摘要即不再拷贝，仅写小的 template/params
    与 manifest。任何失败都安全回退到常规 create（返回 False），不抛错。
    """
    try:
        if not models_store_dir:
            return False
        digest = digest or sha256_file(gguf_abs)
        blobs = os.path.join(models_store_dir, "blobs")
        os.makedirs(blobs, exist_ok=True)
        target = os.path.join(blobs, "sha256-" + digest)
        if os.path.exists(target):
            return True
        if os.path.lexists(target):
            os.remove(target)
        os.link(gguf_abs, target)  # 仅当同卷；跨卷会抛 OSError -> 回退
        return os.path.exists(target)
    except Exception as e:  # noqa: BLE001
        logger.info("硬链接预置 blob 未生效，回退常规 create: %s", e)
        return False


def _run_create(ollama_exe: str, tag: str, modelfile_path: str,
                env: Optional[dict], timeout: float) -> tuple[int, str]:
    """执行 `ollama create <tag> -f <modelfile>`，返回 (退出码, 输出)。"""
    cmd = [ollama_exe, "create", tag, "-f", modelfile_path]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, **(env or {})},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def ensure_registered(
    models_local_root: str,
    ollama_exe: Optional[str] = None,
    host: str = DEFAULT_HOST,
    env: Optional[dict] = None,
    modelfile_dir: Optional[str] = None,
    create_timeout: float = 180.0,
    create_runner: Optional[Callable[..., tuple[int, str]]] = None,
    tags_lister: Optional[Callable[[], set[str]]] = None,
    models_store_dir: Optional[str] = None,
) -> dict:
    """确保 REGISTRY 中所有模型已注册到 Ollama；已存在则跳过（幂等）。

    - 只 create 缺失 tag，**绝不删除**；
    - 注册前以同卷硬链接预置 blob，避免 ``ollama create`` 再拷贝一份 gguf；
    - 返回状态 dict：{tag: {"status": present|created|failed|missing_file, ...}}。
    """
    runner = create_runner or (lambda tag, mf: _run_create(
        ollama_exe or "ollama", tag, mf, env, create_timeout))
    lister = tags_lister or (lambda: list_tags(host))

    if models_store_dir is None:
        models_store_dir = os.environ.get("OLLAMA_MODELS") or None

    with _REGISTER_LOCK:
        try:
            present = lister()
        except Exception as e:  # service not up yet
            logger.warning("Ollama 未就绪，暂不注册模型: %s", e)
            return {"status": "OLLAMA_UNAVAILABLE", "models": {}, "error": str(e)}

        if modelfile_dir is None:
            modelfile_dir = os.path.join(models_local_root, ".modelfiles")
        os.makedirs(modelfile_dir, exist_ok=True)

        result: dict = {"status": "OK", "models": {}}
        for tag, meta in REGISTRY.items():
            if tag in present:
                result["models"][tag] = {"status": "present", "role": meta["role"]}
                logger.info("本地模型已存在，跳过: %s", tag)
                continue
            gguf_abs = os.path.join(models_local_root, meta["file"])
            if not os.path.isfile(gguf_abs):
                result["models"][tag] = {
                    "status": "missing_file", "file": gguf_abs, "role": meta["role"]}
                logger.warning("随包 GGUF 缺失，无法注册 %s: %s", tag, gguf_abs)
                continue
            # 同卷硬链接预置 blob（零额外磁盘）；失败则常规 create 仍可工作
            seeded = preseed_model_blob(gguf_abs, models_store_dir)
            logger.info("blob 硬链接预置 %s: %s", tag, seeded)
            mf_text = build_modelfile(tag, gguf_abs)
            mf_path = os.path.join(modelfile_dir, f"{tag}.modelfile")
            with open(mf_path, "w", encoding="utf-8") as f:
                f.write(mf_text)
            try:
                code, out = runner(tag, mf_path)
            except subprocess.TimeoutExpired:
                result["models"][tag] = {"status": "failed", "reason": "timeout"}
                logger.error("注册模型超时: %s", tag)
                continue
            ok = (code == 0)
            result["models"][tag] = {
                "status": "created" if ok else "failed",
                "role": meta["role"],
                "exit_code": code,
                "blob_preseeded": seeded,
                "output_tail": out[-400:] if not ok else "",
            }
            if ok:
                logger.info("本地模型已注册: %s (%s)", tag, meta["file"])
            else:
                logger.error("注册模型失败 %s exit=%s: %s", tag, code, out[-300:])
        return result


def chat(tag: str, messages: list[dict], host: str = DEFAULT_HOST,
         stream: bool = False, options: Optional[dict] = None,
         timeout: float = 180.0) -> dict:
    """调用 Ollama /api/chat。

    messages: [{"role":"system|user|assistant", "content":...}, ...]
    非流式返回 {"text":..., "stats": {...}}；stream=True 时返回生成器。
    """
    meta = REGISTRY.get(tag)
    opts = {
        "num_ctx": (meta or {}).get("num_ctx", DEFAULT_NUM_CTX),
        "temperature": (meta or {}).get("temperature", 0.7),
        "top_p": (meta or {}).get("top_p", 0.8),
        "repeat_penalty": (meta or {}).get("repeat_penalty", 1.05),
    }
    if options:
        opts.update(options)
    body = json.dumps({
        "model": tag, "messages": messages, "stream": stream,
        "options": opts,
    }).encode("utf-8")
    req = urllib.request.Request(
        host.rstrip("/") + "/api/chat", data=body,
        headers={"Content-Type": "application/json"})

    if not stream:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8", "replace"))
        return {"text": (obj.get("message") or {}).get("content", ""), "stats": obj}

    def _gen():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                obj = json.loads(line)
                c = (obj.get("message") or {}).get("content", "")
                if c:
                    yield c
                if obj.get("done"):
                    return
    return {"generator": _gen()}


def model_for(task: str) -> str:
    """按任务类型返回本地 tag。复杂/未知任务默认离线主力。"""
    return TASK_MODEL.get(task, "loveai-qwen15b")


def division_of_labor() -> dict:
    """返回当前固化的本地模型分工说明（供设置页/日志展示）。"""
    return {
        "loveai-qwen15b": REGISTRY["loveai-qwen15b"]["role"],
        "loveai-qwen05b": REGISTRY["loveai-qwen05b"]["role"],
        "smollm2-135m": "非中文超轻量（ONNX，L1）；中文弱，保留不删",
        "cloud": "复杂中文理解/深度推理/临床化人格判定分流云端",
    }


if __name__ == "__main__":
    # 手动自查：打印分工与当前已注册 tag
    print(json.dumps(division_of_labor(), ensure_ascii=False, indent=2))
    try:
        print("registered:", sorted(list_tags()))
    except Exception as e:  # noqa: BLE001
        print("ollama unavailable:", e)
