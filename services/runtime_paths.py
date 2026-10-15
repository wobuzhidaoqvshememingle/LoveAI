# -*- coding: utf-8 -*-
r"""
LoveAI v3.0 - 随包运行时路径解析（runtime_paths）

【本模块为封包基础设施新增的独立模块】不修改任何既有业务模块，
仅提供一套「开发态 / PyInstaller onedir 冻结态」统一的路径解析规则，
让程序在冻结后能优先使用：

  1. 随包的便携 Ollama（安装目录下的 ollama_runtime/，数据落安装目录，
     不污染用户 %APPDATA% / %USERPROFILE%\\.ollama）；
  2. 随包的本地模型（models_local/，安装目录优先、包内 _internal 兜底）；
  3. 随包的语音组件（voice/）与预置知识库（data/unified_search.db、
     data/chroma_db）。

目录约定（PyInstaller 6.x onedir 布局）::

    <安装目录>\
        LoveAI.exe                  <- sys.executable
        _internal\                  <- sys._MEIPASS（只读包内资源根）
            config.json
            resources/ styles/ prompts/ skills/ plugins/ assets/ knowledge/
            ui/pet_3d.html + ui/js/*
            data/unified_search.db, data/chroma_db   <- 预置知识库（只读）
            models_local/...                        <- 仅当全组件版才打入
            ollama_runtime/...                     <- 仅当全组件版才打入
        data\                       <- 可写用户数据（loveai.db / logs 等）
        models_local\               <- 安装器勾选「本地大模型」时释放（可写兜底）
        ollama_runtime\ollama.exe   <- 安装器勾选「Ollama 运行时」时释放
        ollama_data\models\         <- 便携 Ollama 模型仓库（可写，OLLAMA_MODELS）
        voice\                      <- 安装器勾选「语音组件」时释放

设计要点：
- ``app_root()``  = 包内只读资源根（冻结态 ``_internal``；开发态仓库根）。
- ``install_dir()``= exe 同级可写安装根（冻结态 LoveAI.exe 所在目录；开发态仓库根）。
- ``resolve()``   = 相对路径一律先按 app_root 解析；这是既有 datas 的落点。
- 可写产物（用户库 / 日志 / Ollama 仓库）一律落 ``install_dir()``，不写进只读的
  ``_internal``，避免 Program Files 权限问题与被升级覆盖。

接入方式见 tools/installer/PATCHES.md（本阶段只新增模块，不改既有业务文件，
以免与并行的引擎修复相互踩踏）。
"""
from __future__ import annotations

import os
import sys
import logging

logger = logging.getLogger("LoveAI.runtime_paths")

# 便携 Ollama 默认监听地址（与 free_model_router 的 http://localhost:11434/v1 对齐）
OLLAMA_DEFAULT_HOST = "127.0.0.1:11434"

# 模块级缓存
_FROZEN = bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def is_frozen() -> bool:
    """是否处于 PyInstaller 冻结态。"""
    return _FROZEN


def app_root() -> str:
    """包内只读资源根。

    - 冻结态：``sys._MEIPASS``（onedir 下即 ``<安装目录>\\_internal``，
      config.json / resources / data 预置库 / 打入的 models_local 都在这里）。
    - 开发态：本文件上两级 = LoveAI-v2 仓库根。
    """
    if _FROZEN:
        return os.path.abspath(sys._MEIPASS)  # type: ignore[attr-defined]
    # services/runtime_paths.py -> services -> 仓库根
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def install_dir() -> str:
    """可写安装根（exe 同级目录）。

    - 冻结态：``os.path.dirname(sys.executable)``，即 LoveAI.exe 所在目录。
      用户数据、便携 Ollama 及其模型仓库都放这里，不写进只读 _internal。
    - 开发态：与 app_root() 相同（仓库根）。
    """
    if _FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return app_root()


def resolve(rel_or_abs: str) -> str:
    """把配置里的相对路径解析为绝对路径（相对包内只读资源根 app_root）。

    绝对路径原样返回。空串返回空串。
    与 ``services.llm_gateway._resolve_path`` 的语义保持一致，
    但根目录改为冻结感知的 ``app_root()``。
    """
    if not rel_or_abs:
        return ""
    if os.path.isabs(rel_or_abs):
        return rel_or_abs
    return os.path.join(app_root(), rel_or_abs)


def writable_data_dir() -> str:
    """可写用户数据目录（冻结态 = 安装目录\\data；开发态 = 仓库根\\data）。"""
    return os.path.join(install_dir(), "data")


def writable_logs_dir() -> str:
    """可写日志目录（冻结态 = 安装目录\\logs；开发态 = 仓库根\\logs）。

    让 loveai.log 落在安装目录而非只读 _internal（Program Files 管理员安装场景）。
    """
    return os.path.join(install_dir(), "logs")


def models_local_dir() -> str:
    """本地模型根目录。

    解析顺序（谁存在用谁）：
      1) 安装目录\\models_local（安装器勾选「本地大模型」释放，可写、可升级）；
      2) 包内 _internal\\models_local（全组件版 PyInstaller 直接打入，只读）；
      3) 安装目录\\models_local（兜底返回，供首次落盘创建）。
    """
    cand = os.path.join(install_dir(), "models_local")
    if os.path.isdir(cand):
        return cand
    bundled = os.path.join(app_root(), "models_local")
    if os.path.isdir(bundled):
        return bundled
    return cand  # 兜底（调用方据此创建/报错）


def resolve_model_path(rel: str) -> str:
    """解析 models_local 下的相对模型路径（gguf/onnx/lora）。

    绝对路径原样返回；相对路径先在「安装目录 models_local」找，
    再回退到「包内 models_local」。
    """
    if not rel:
        return ""
    if os.path.isabs(rel):
        return rel
    # 去掉可能重复的 models_local/ 前缀，统一拼到 models_local 根下
    norm = rel.replace("\\", "/")
    if norm.startswith("models_local/"):
        norm = norm[len("models_local/"):]
    primary = os.path.join(install_dir(), "models_local", norm)
    if os.path.exists(primary):
        return primary
    fallback = os.path.join(app_root(), "models_local", norm)
    if os.path.exists(fallback):
        return fallback
    return primary  # 兜底


def ollama_dir() -> str:
    """便携 Ollama 程序目录（ollama.exe 所在）。

    依次探测：
      1) 安装目录\\ollama_runtime（安装器勾选「Ollama 运行时」释放，可升级）；
      2) 包内 _internal\\ollama_runtime（全组件版 PyInstaller 直接打入）；
      3) 安装目录\\ollama_runtime（兜底返回，供首次落盘）。
    """
    cand = os.path.join(install_dir(), "ollama_runtime")
    if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "ollama.exe")):
        return cand
    bundled = os.path.join(app_root(), "ollama_runtime")
    if os.path.isdir(bundled) and os.path.isfile(os.path.join(bundled, "ollama.exe")):
        return bundled
    return cand


def ollama_exe() -> str:
    """便携 ollama.exe 绝对路径。"""
    return os.path.join(ollama_dir(), "ollama.exe")


def is_portable_ollama_available() -> bool:
    """随包便携 Ollama 是否就位（ollama.exe 存在）。"""
    return os.path.isfile(ollama_exe())


def ollama_data_dir() -> str:
    """便携 Ollama 可写数据根（模型仓库父目录）。

    落 ``安装目录\\ollama_data``，配合 ``OLLAMA_MODELS`` 让模型不进
    用户 %USERPROFILE%\\.ollama。
    """
    return os.path.join(install_dir(), "ollama_data")


def ollama_models_dir() -> str:
    """便携 Ollama 模型仓库（OLLAMA_MODELS 指向此处）。"""
    return os.path.join(ollama_data_dir(), "models")


def voice_dir() -> str:
    """语音组件目录（STT/TTS 资源）。"""
    return os.path.join(install_dir(), "voice")


def knowledge_preset_dir() -> str:
    """预置知识库目录（只读，包内 data/：unified_search.db、chroma_db）。"""
    return os.path.join(app_root(), "data")


# ---- 浏览器 AI（Playwright）随包路径 ----
def browser_ai_dir() -> str:
    """浏览器AI能力根目录（安装器勾选「浏览器AI能力」释放）。"""
    return os.path.join(install_dir(), "browser_ai")


def playwright_browsers_dir() -> str:
    """ms-playwright 浏览器二进制目录（chromium-XXXX / headless shell / ffmpeg）。"""
    return os.path.join(browser_ai_dir(), "ms-playwright")


def playwright_pkg_dir() -> str:
    """随包 playwright Python 包的父目录（sys.path 兜底用）。"""
    return os.path.join(browser_ai_dir(), "playwright_pkg")


def is_bundled_browser_ai_available() -> bool:
    """随包 ms-playwright 是否就位。"""
    return os.path.isdir(playwright_browsers_dir())


def bootstrap_playwright_env() -> dict:
    """启动早期调用：让随包 Playwright/Chromium 离线可用。

    1) 随包 ms-playwright 就位时，设 ``PLAYWRIGHT_BROWSERS_PATH`` 指向它，
       浏览器从此目录加载，免 ``playwright install``、不联网；
    2) 若当前环境尚不能 ``import playwright``，把随包 playwright_pkg 追加到
       sys.path 兜底（PyInstaller 已 collect_all 时通常无需此步）。
    幂等可重复调用，返回实际生效情况。不抛错。
    """
    info = {"browsers_path": None, "pkg_path_added": False,
            "playwright_importable": False}
    bdir = playwright_browsers_dir()
    if os.path.isdir(bdir):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bdir
        info["browsers_path"] = bdir
        logger.info("PLAYWRIGHT_BROWSERS_PATH=%s", bdir)

    try:
        import playwright  # noqa: F401
        info["playwright_importable"] = True
    except Exception:
        pdir = playwright_pkg_dir()
        if os.path.isdir(pdir) and pdir not in sys.path:
            sys.path.insert(0, pdir)
            info["pkg_path_added"] = True
            try:
                import playwright  # noqa: F401
                info["playwright_importable"] = True
            except Exception as e:  # noqa: BLE001
                logger.debug("随包 playwright 仍不可导入: %s", e)
    return info


def ensure_runtime_dirs() -> None:
    """创建运行期需要的可写目录（冻结态下这些不在只读 _internal 里）。

    应在应用启动早期调用一次。已存在则跳过；创建失败不阻断启动。
    """
    for sub in (
        os.path.join(install_dir(), "data"),
        os.path.join(install_dir(), "logs"),
        ollama_data_dir(),
        ollama_models_dir(),
        os.path.join(install_dir(), "models_local"),
        os.path.join(install_dir(), "backups"),
    ):
        try:
            os.makedirs(sub, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.debug("可写目录创建失败(忽略): %s -> %s", sub, e)


def portable_ollama_env(host: str = OLLAMA_DEFAULT_HOST) -> dict:
    """构造「便携 Ollama 子进程」需要的环境变量覆盖（不污染用户 AppData）。

    返回一个 dict，用于 ``subprocess.Popen(..., env={**os.environ, **portable_ollama_env()})``。
    - ``OLLAMA_MODELS`` 指向安装目录内的 ollama_data\\models；
    - ``OLLAMA_HOST``   固定 127.0.0.1:11434；
    - ``OLLAMA_ORIGINS`` 放行本机来源（QWebEngine/Qt 调用需要）；
    - ``OLLAMA_NOPRUNE`` 防止退出时清理。
    """
    return {
        "OLLAMA_MODELS": ollama_models_dir(),
        "OLLAMA_HOST": host,
        "OLLAMA_ORIGINS": "*",
        "OLLAMA_NOPRUNE": "1",
    }


def bootstrap_ollama_env(host: str = OLLAMA_DEFAULT_HOST) -> dict:
    """在进程启动早期调用：把便携 Ollama 环境变量写入当前进程 os.environ。

    这样即使后续代码直接 ``import ollama`` 或访问 localhost:11434，
    也会落到随包便携实例而非用户全局安装；同时让数据目录不污染 AppData。
    仅当随包 ollama_runtime 就位时才设置（避免覆盖用户刻意的全局 Ollama）。
    返回最终生效的环境 dict。幂等可重复调用。
    """
    env = portable_ollama_env(host)
    if is_portable_ollama_available():
        os.environ.update(env)
        logger.info("便携 Ollama 已就绪: %s (MODELS=%s)", ollama_exe(), env["OLLAMA_MODELS"])
    else:
        # 未随包时不强行覆盖，回退到用户系统里已有的 Ollama（若有）
        env = {k: os.environ.get(k, d) for k, d in {
            "OLLAMA_HOST": host,
        }.items()}
        logger.info("未检测到随包 ollama_runtime，使用系统/已有 Ollama（若运行）")
    return env


def summary() -> dict:
    """返回当前运行时路径快照（供自检/日志/关于页展示）。"""
    return {
        "frozen": is_frozen(),
        "app_root": app_root(),
        "install_dir": install_dir(),
        "writable_data": writable_data_dir(),
        "models_local": models_local_dir(),
        "ollama_dir": ollama_dir(),
        "ollama_exe": ollama_exe(),
        "portable_ollama_ready": is_portable_ollama_available(),
        "ollama_models": ollama_models_dir(),
        "voice_dir": voice_dir(),
        "knowledge_preset": knowledge_preset_dir(),
        "browser_ai_dir": browser_ai_dir(),
        "playwright_browsers": playwright_browsers_dir(),
        "bundled_browser_ai_ready": is_bundled_browser_ai_available(),
    }


if __name__ == "__main__":  # 手动 `python -m services.runtime_paths` 自查
    import json
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
