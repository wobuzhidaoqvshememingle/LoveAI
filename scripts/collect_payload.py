# -*- coding: utf-8 -*-
r"""
LoveAI 封包基础设施 - 运行时与模型归集脚本（collect_payload）

职责：把「随包组件」归集到统一的 payload 目录，供 Inno Setup 安装器
按组件勾选物理分发（终端用户无需自装 Python / Ollama / pip，全程离线）。

产物布局（<项目根>\dist_bundle\payload\）::

    ollama_runtime\                 # 便携 Ollama（CPU 必需子集，约 72MB）
        ollama.exe
        lib\ollama\ggml*.dll, libllama*.dll, libmtmd.dll, libomp.dll ...
    models_local\                   # 本地大模型（gguf + smollm2 onnx + HF 基座 + lora）
    knowledge\                      # 预置知识库（unified_search.db + chroma_db）
    browser_ai\                     # 浏览器AI能力（playwright 包 + ms-playwright 浏览器二进制）
        playwright_pkg\playwright\
        ms-playwright\chromium-1243\, chromium_headless_shell-1243\, ffmpeg-1011\
    voice\                          # 语音组件骨架（STT/TTS；权重离线缺失时留 README）
    payload_manifest.json          # 各组件文件数/体积清单（供 preflight 与安装器核对）

设计：
- 优先用 **NTFS 硬链接**（os.link）归集大文件，几乎零额外磁盘占用、秒级完成；
  跨卷或失败时自动回退到 copy2。源与目标都在 C: 盘，通常全程硬链接。
- Ollama 默认只带 CPU 必需子集（28 个文件），不带 cuda/rocm/vulkan 后端
  （后者合计约 2.7GB，按需设环境变量 LOVEAI_INCLUDE_GPU=1 再归集）。
- 本脚本**只读源、只写 dist_bundle/**，不碰应用源码。

用法（项目根，用 venv python）::

    .venv\Scripts\python.exe scripts\collect_payload.py
    LOVEAI_INCLUDE_GPU=1 .venv\Scripts\python.exe scripts\collect_payload.py   # 含 GPU 后端
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys

# 项目根 = scripts/ 的上一级
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PAYLOAD_ROOT = os.path.join(ROOT, "dist_bundle", "payload")

# 本机 Ollama 安装目录（组织者已确认）
OLLAMA_SRC = os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\87433\AppData\Local"),
                          "Programs", "Ollama")

# Ollama CPU 必需文件匹配（lib\ollama\ 顶层）。GPU 后端子目录默认不带。
_OLLAMA_LIB_RE = re.compile(
    r"^(ggml|libllama|libmtmd|libomp|libunwind|libwinpthread|libc\+\+|llama-)",
    re.IGNORECASE,
)
_OLLAMA_GPU_DIRS = ("cuda_v12", "cuda_v13", "rocm_v7_1", "vulkan")

# ---- 浏览器 AI（Playwright）物理归集 ----
# Python 包（含 node driver）+ 浏览器二进制（chromium / headless shell / ffmpeg）。
PLAYWRIGHT_PKG_SRC = os.path.join(ROOT, ".venv", "Lib", "site-packages", "playwright")
MS_PLAYWRIGHT_SRC = os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\87433\AppData\Local"), "ms-playwright")
_BROWSER_BIN_DIRS = ("chromium-1243", "chromium_headless_shell-1243", "ffmpeg-1011")

INCLUDE_GPU = os.environ.get("LOVEAI_INCLUDE_GPU", "0").lower() in ("1", "true", "yes")

# ---- 预建便携模型仓库（免运行时 ollama create）----
# 由 tools\build_portable_store 以便携 ollama_runtime 真机 create 生成（blobs+manifests）。
# 安装器勾选「本地大模型」时释放到 {app}\ollama_data\models，启动即已注册，
# 不再在终端用户机上拷贝/转换 gguf（省约 1.5GB 磁盘与内存峰值，8GB 机器可靠性关键）。
PORTABLE_STORE_SRC = os.environ.get(
    "LOVEAI_PORTABLE_STORE", r"E:\LoveAI_build\portable_models\models")


def _link_or_copy(src: str, dst: str) -> str:
    """硬链接（同卷秒级、零额外空间）；失败回退 copy2。返回实际大小。"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        try:
            os.remove(dst)
        except OSError:
            pass
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return os.path.getsize(dst)


def _tree_size(path: str) -> int:
    total = 0
    for dp, _, fns in os.walk(path):
        for fn in fns:
            try:
                total += os.path.getsize(os.path.join(dp, fn))
            except OSError:
                pass
    return total


def _count_files(path: str) -> int:
    n = 0
    for dp, _, fns in os.walk(path):
        n += len(fns)
    return n


def collect_dir_tree(src: str, dst: str, manifest: dict, key: str,
                     include_re: re.Pattern | None = None,
                     exclude_dirs: set[str] | None = None,
                     exclude_files: set[str] | None = None) -> None:
    """归集整棵目录树（可选文件名正则过滤、可选排除子目录名/文件名）。"""
    if not os.path.isdir(src):
        manifest[key] = {"status": "MISSING", "src": src, "bytes": 0, "files": 0}
        print(f"  [缺失] {key}: {src}")
        return
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, exist_ok=True)
    files = 0
    for dp, dns, fns in os.walk(src):
        rel = os.path.relpath(dp, src)
        # 排除子目录
        if exclude_dirs:
            parts = set(rel.replace("\\", "/").split("/"))
            if parts & exclude_dirs:
                dns[:] = []
                continue
        for fn in fns:
            if include_re and not include_re.match(fn):
                continue
            if exclude_files and fn in exclude_files:
                continue
            s = os.path.join(dp, fn)
            d = os.path.join(dst, os.path.relpath(s, src))
            _link_or_copy(s, d)
            files += 1
    manifest[key] = {
        "status": "OK",
        "src": src,
        "bytes": _tree_size(dst),
        "files": files,
    }
    print(f"  [OK] {key}: {files} 文件, {_tree_size(dst)/1024/1024:.1f} MB -> {dst}")


def collect_ollama(manifest: dict) -> None:
    """归集便携 Ollama（CPU 必需子集）。"""
    print(f"[Ollama] 源: {OLLAMA_SRC}  (INCLUDE_GPU={INCLUDE_GPU})")
    dst = os.path.join(PAYLOAD_ROOT, "ollama_runtime")
    if not os.path.isdir(OLLAMA_SRC):
        manifest["ollama"] = {"status": "MISSING", "src": OLLAMA_SRC, "bytes": 0, "files": 0}
        print("  [缺失] Ollama 安装目录不存在")
        return
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    files = 0
    # 1) ollama.exe（CLI/server；不带 ollama app.exe 托盘与 unins*）
    files += 1 if _link_or_copy(os.path.join(OLLAMA_SRC, "ollama.exe"),
                                os.path.join(dst, "ollama.exe")) else 0
    # 2) lib\ollama 顶层 CPU 必需 dll/exe
    lib_src = os.path.join(OLLAMA_SRC, "lib", "ollama")
    lib_dst = os.path.join(dst, "lib", "ollama")
    for fn in os.listdir(lib_src):
        s = os.path.join(lib_src, fn)
        if os.path.isfile(s) and _OLLAMA_LIB_RE.match(fn):
            _link_or_copy(s, os.path.join(lib_dst, fn))
            files += 1
    # 3) GPU 后端（可选）
    gpu_note = []
    if INCLUDE_GPU:
        for g in _OLLAMA_GPU_DIRS:
            gs = os.path.join(lib_src, g)
            if os.path.isdir(gs):
                collect_dir_tree(gs, os.path.join(lib_dst, g), manifest, f"ollama_gpu_{g}")
                gpu_note.append(g)
    manifest["ollama"] = {
        "status": "OK",
        "src": OLLAMA_SRC,
        "bytes": _tree_size(dst),
        "files": files,
        "cpu_only": not INCLUDE_GPU,
        "gpu_backends_included": gpu_note,
        "excluded_by_default": list(_OLLAMA_GPU_DIRS),
    }
    print(f"  [OK] ollama_runtime: {files} 文件(CPU), {_tree_size(dst)/1024/1024:.1f} MB")
    print(f"       GPU 后端默认不带: {list(_OLLAMA_GPU_DIRS)}"
          + (f"（本次已含: {gpu_note}）" if gpu_note else ""))


def collect_models(manifest: dict) -> None:
    """归集本地模型 models_local/。"""
    print("[models_local]")
    src = os.path.join(ROOT, "models_local")
    dst = os.path.join(PAYLOAD_ROOT, "models_local")
    # 预建便携仓库已含两模型的就绪规范化版本（免运行时 create），安装包不再重复
    # 携带两个原始 GGUF（源仓库文件保留不删），消除约 1.5GB 冗余，使单文件 < 4.2GB。
    exclude = None
    if os.path.isdir(PORTABLE_STORE_SRC):
        exclude = {"qwen2.5-1.5b-instruct-q4_k_m.gguf",
                   "qwen2.5-0.5b-instruct-q4_k_m.gguf"}
        print("       预建仓库存在：安装包 models_local 排除两个原始 GGUF（就绪版在 portable_models）")
    collect_dir_tree(src, dst, manifest, "models_local", exclude_files=exclude)
    # 细分组件体积
    detail = {}
    for sub in ("qwen2.5-1.5b-instruct-q4_k_m.gguf",
                "qwen2.5-0.5b-instruct-q4_k_m.gguf", "smollm2-135m",
                "Qwen2.5-0.5B-Instruct", "lora"):
        p = os.path.join(src, sub)
        if os.path.exists(p):
            b = _tree_size(p) if os.path.isdir(p) else os.path.getsize(p)
            detail[sub] = round(b / 1024 / 1024, 1)
    manifest["models_local"]["breakdown_mb"] = detail
    print(f"       细分(MB): {detail}")


def collect_knowledge(manifest: dict) -> None:
    """归集预置知识库：data/unified_search.db + data/chroma_db/。"""
    print("[knowledge]")
    dst = os.path.join(PAYLOAD_ROOT, "knowledge")
    if os.path.exists(dst):
        shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, exist_ok=True)
    files = 0
    db_src = os.path.join(ROOT, "data", "unified_search.db")
    if os.path.isfile(db_src):
        _link_or_copy(db_src, os.path.join(dst, "unified_search.db"))
        files += 1
    chroma_src = os.path.join(ROOT, "data", "chroma_db")
    if os.path.isdir(chroma_src):
        for dp, _, fns in os.walk(chroma_src):
            for fn in fns:
                s = os.path.join(dp, fn)
                d = os.path.join(dst, "chroma_db", os.path.relpath(s, chroma_src))
                # chroma.sqlite3 易被运行中的进程/pytest 占用：用独立拷贝而非硬链接，
                # 避免源文件锁传导到安装器压缩阶段。
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copy2(s, d)
                files += 1
    manifest["knowledge"] = {
        "status": "OK" if files else "MISSING",
        "bytes": _tree_size(dst),
        "files": files,
        "contents": ["unified_search.db", "chroma_db/"],
    }
    print(f"  [OK] knowledge: {files} 文件, {_tree_size(dst)/1024/1024:.1f} MB")


def collect_browser_ai(manifest: dict) -> None:
    """归集浏览器 AI 能力：playwright Python 包 + ms-playwright 浏览器二进制。

    安装器勾选「浏览器AI能力」时释放；运行时设 PLAYWRIGHT_BROWSERS_PATH 指向
    释放后的 ms-playwright，终端免 pip / 免 playwright install、不联网。
    """
    print("[browser_ai]")
    base = os.path.join(PAYLOAD_ROOT, "browser_ai")
    if os.path.exists(base):
        shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base, exist_ok=True)

    # 1) playwright Python 包（排除 __pycache__）。释放到 {app}\\browser_ai\\playwright_pkg，
    #    运行时仅在冻结环境未自带 playwright 时才追加 sys.path 兜底。
    pkg_dst = os.path.join(base, "playwright_pkg", "playwright")
    collect_dir_tree(PLAYWRIGHT_PKG_SRC, pkg_dst, manifest,
                     "browser_ai_part_pkg", exclude_dirs={"__pycache__"})

    # 2) 浏览器二进制（chromium / headless shell / ffmpeg）
    for d in _BROWSER_BIN_DIRS:
        s = os.path.join(MS_PLAYWRIGHT_SRC, d)
        if os.path.isdir(s):
            collect_dir_tree(s, os.path.join(base, "ms-playwright", d), manifest,
                             f"browser_ai_part_{d}")
        else:
            manifest[f"browser_ai_part_{d}"] = {"status": "MISSING", "src": s,
                                                "bytes": 0, "files": 0}
            print(f"  [缺失] {d}: {s}")

    # .links（版本映射元数据，体积极小，一并带上）
    links = os.path.join(MS_PLAYWRIGHT_SRC, ".links")
    if os.path.isdir(links):
        collect_dir_tree(links, os.path.join(base, "ms-playwright", ".links"),
                         manifest, "browser_ai_part_links")

    manifest["browser_ai"] = {
        "status": "OK",
        "bytes": _tree_size(base),
        "files": _count_files(base),
        "playwright_pkg_src": PLAYWRIGHT_PKG_SRC,
        "ms_playwright_src": MS_PLAYWRIGHT_SRC,
        "browser_dirs": list(_BROWSER_BIN_DIRS),
        "contents": ["browser_ai/playwright_pkg/", "browser_ai/ms-playwright/"],
    }
    print(f"  [OK] browser_ai 合计: {_count_files(base)} 文件, "
          f"{_tree_size(base)/1024/1024:.1f} MB")


def collect_portable_store(manifest: dict) -> None:
    """归集预建便携模型仓库（已注册的 blobs+manifests）。

    释放到 {app}\\ollama_data\\models；启动时便携 ollama 直接读到两个模型，
    ensure_registered 命中 present 即跳过 create，终端零拷贝/零转换。
    若发行方未预建，则标 MISSING（安装器仍可回退到运行时 create）。
    """
    print("[portable_store]")
    dst = os.path.join(PAYLOAD_ROOT, "portable_models")
    if not os.path.isdir(PORTABLE_STORE_SRC):
        manifest["portable_models"] = {
            "status": "MISSING", "src": PORTABLE_STORE_SRC, "bytes": 0, "files": 0}
        print("  [缺失] 预建便携模型仓库不存在（安装器将回退运行时 create）")
        return
    collect_dir_tree(PORTABLE_STORE_SRC, dst, manifest, "portable_models")


def collect_voice(manifest: dict) -> None:
    """语音组件骨架。本机离线无 faster-whisper 权重，建目录 + README。"""
    print("[voice]")
    dst = os.path.join(PAYLOAD_ROOT, "voice")
    os.makedirs(dst, exist_ok=True)
    readme = os.path.join(dst, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(
                "LoveAI 语音组件（STT/TTS）\n"
                "=========================\n"
                "本目录为安装器勾选「语音组件」时释放的语音资源骨架。\n"
                "当前离线环境未随附 faster-whisper 模型权重与 pyaudio/sounddevice\n"
                "运行时（venv 中 faster_whisper / pyaudio 均缺失）。\n\n"
                "补全方式（需联网，由发行方在最终封包前完成）：\n"
                "  1) pip install faster-whisper pyaudio sounddevice（或随 PyInstaller 打入）；\n"
                "  2) 下载 whisper-small 到 models_local/whisper-small/（与 speech_service.py\n"
                "     的 models_local/whisper-{size} 约定一致）；\n"
                "  3) 本 voice/ 目录可放置 TTS 音色资源。\n\n"
                "未补全时 SpeechService 会安全降级（可录音但不转写），不影响核心功能。\n"
            )
    manifest["voice"] = {
        "status": "STUB",
        "bytes": _tree_size(dst),
        "files": _count_files(dst),
        "note": "离线无 STT/TTS 权重；骨架已就位，权重待联网补全",
    }
    print("  [STUB] voice: 骨架已建（权重离线缺失，待联网补全）")


def main() -> int:
    print("=" * 64)
    print("LoveAI payload 归集  ->", PAYLOAD_ROOT)
    print("=" * 64)
    os.makedirs(PAYLOAD_ROOT, exist_ok=True)
    manifest: dict = {
        "root": ROOT,
        "payload_root": PAYLOAD_ROOT,
        "include_gpu": INCLUDE_GPU,
    }
    collect_ollama(manifest)
    collect_models(manifest)
    collect_portable_store(manifest)
    collect_knowledge(manifest)
    collect_browser_ai(manifest)
    collect_voice(manifest)

    # 总计：ollama / browser_ai 的 bytes 已含其整棵目录树（含 GPU 后端 /
    # 浏览器二进制子目录），因此不再累加 ollama_gpu_* / browser_ai_part_* 子键，
    # 否则会重复计数。
    total = sum(v.get("bytes", 0) for k, v in manifest.items()
                if isinstance(v, dict)
                and not k.startswith("ollama_gpu_")
                and not k.startswith("browser_ai_part_"))
    manifest["total_mb"] = round(total / 1024 / 1024, 1)

    mpath = os.path.join(PAYLOAD_ROOT, "payload_manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("-" * 64)
    print(f"payload 总大小: {manifest['total_mb']} MB")
    print(f"清单已写: {mpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
