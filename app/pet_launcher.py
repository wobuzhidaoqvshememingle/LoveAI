"""
LoveAI v2.0 - Electron 桌宠进程拉起器（R3.3-76 整体联动生命周期托管）
======================================================================
主程序启动时，按以下顺序拉起 Electron 桌宠：
  1. 优先找打包好的 exe：desktop-pet-electron/dist/win-unpacked/LoveAIPet.exe
  2. 找不到则用开发模式：npm start（需要 node_modules 已装好）

R3.3-76 改造要点：
- 生命周期托管：本进程拉起的桌宠 PID 被记录；主程序退出时 stop_launched_pet()
  用 taskkill /T /F 整树终止，杜绝"主程序在跑、桌宠是孤儿进程"。
- 状态枚举返回：ok / already_running / no_resources / launch_failed，
  供上层做失败降级提示（验收点6）。
- 预存进程策略：启动前已存在的 LoveAIPet.exe（旧独立使用场景），
  当 config.json 的 pet.lifecycle_tied=true（默认）时一并纳入整体生命周期，
  退出时同杀；=false 时保留不动（留作未来调整）。
- 共享配置注入：拉起前由 pet_config 写好 data/pet_shared.json，启动时通过
  环境变量 LOVEAI_PET_SHARED / LOVEAI_APP_VERSION 传给桌宠，双端单点一致。

设计原则（不变）：
- 幂等：已经在跑就不重复拉。
- 不阻塞：subprocess.Popen 异步。
- 错误隔离：任何异常只记日志/返回状态码，绝不影响主程序启动与退出。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Optional

logger = logging.getLogger("LoveAI.pet_launcher")

# ---- 拉起结果状态枚举（供上层降级提示）----
ST_OK = "ok"                          # 本次真的拉起了新进程
ST_ALREADY_RUNNING = "already_running"  # 启动前已检测到 LoveAIPet.exe
ST_NO_RESOURCES = "no_resources"      # 既无打包 exe 也无开发目录
ST_LAUNCH_FAILED = "launch_failed"    # Popen 异常

# 模块级状态
_state = {
    "proc": None,               # 本次拉起的 Popen 句柄
    "pid": None,                # 本次拉起进程的 PID
    "preexisting_pids": [],     # 启动前已存在的 LoveAIPet.exe PID 列表
    "launched": False,          # 本次是否已尝试过拉起
    "lifecycle_tied": True,     # 是否把预存进程纳入整体生命周期（可被 config 覆盖）
    "last_status": None,         # 最近一次 launch_pet 的状态
}


# ------------------------------------------------------------------ #
# 路径解析（冻结态 / 开发态双兜底）
# ------------------------------------------------------------------ #
def _project_root() -> str:
    """可写安装根（冻结态 = exe 同级目录；开发态 = 仓库根）。

    R3.3-76 约定：冻结态下 _project_root()=exe 目录，
    _pet_dir()=<root>\\desktop-pet-electron。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _app_root() -> str:
    """包内只读资源根（冻结态 = _MEIPASS/_internal；开发态 = 仓库根）。

    当桌宠资源经 PyInstaller datas 打进 _internal 时，作为兜底查找根。
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return os.path.abspath(sys._MEIPASS)  # type: ignore[attr-defined]
    return _project_root()


def _pet_dir() -> str:
    """桌宠运行资源根：<project_root>\\desktop-pet-electron。

    降级测试/运维钩子：环境变量 LOVEAI_PET_DIR_OVERRIDE 可覆盖资源目录
    （指向不存在路径用于验收点6 失败降级测试；不动真目录）。
    """
    override = os.environ.get("LOVEAI_PET_DIR_OVERRIDE")
    if override:
        return override
    return os.path.join(_project_root(), "desktop-pet-electron")


def _find_packaged_exe() -> Optional[str]:
    """找打包好的桌宠 LoveAIPet.exe。

    查找顺序：
      1) <install_dir>\\desktop-pet-electron\\dist\\win-unpacked\\LoveAIPet.exe
         （Inno 安装器物理分发到 {app}\\desktop-pet-electron，首选）
      2) <_internal>\\desktop-pet-electron\\dist\\win-unpacked\\LoveAIPet.exe
         （PyInstaller datas 直接打入只读包内时的兜底）
    """
    exe_name = "LoveAIPet.exe"
    override = os.environ.get("LOVEAI_PET_DIR_OVERRIDE")
    if override:
        # 降级测试/运维钩子：override 设置时完全替换资源发现根，只搜 override 路径
        candidates = [os.path.join(override, "dist", "win-unpacked", exe_name)]
    else:
        candidates = [
            os.path.join(_pet_dir(), "dist", "win-unpacked", exe_name),
            os.path.join(_app_root(), "desktop-pet-electron", "dist", "win-unpacked", exe_name),
        ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


# ------------------------------------------------------------------ #
# 配置读取（生命周期开关 / 版本号）
# ------------------------------------------------------------------ #
def _load_lifecycle_tied() -> bool:
    """从 config.json 读 pet.lifecycle_tied（默认 True）。任何异常按 True 处理。"""
    try:
        cfg_path = os.path.join(_app_root(), "config.json")
        if not os.path.isfile(cfg_path):
            return True
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        pet = cfg.get("pet", {}) if isinstance(cfg, dict) else {}
        v = pet.get("lifecycle_tied", True)
        return bool(v)
    except Exception:
        return True


def _load_app_version() -> str:
    """从 config.json 读 app.version（默认 2.0.0），用于与桌宠版本对齐校验。"""
    try:
        cfg_path = os.path.join(_app_root(), "config.json")
        if not os.path.isfile(cfg_path):
            return "2.0.0"
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        app = cfg.get("app", {}) if isinstance(cfg, dict) else {}
        v = app.get("version", "2.0.0")
        return str(v)
    except Exception:
        return "2.0.0"


# ------------------------------------------------------------------ #
# 进程探测
# ------------------------------------------------------------------ #
def _list_pet_pids():
    """通过 tasklist 列出当前所有 LoveAIPet.exe 的 PID（int 列表）。

    不依赖 psutil；失败返回空列表。
    """
    pids = []
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq LoveAIPet.exe", "/NH", "/FO", "CSV"],
            stderr=subprocess.DEVNULL,
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = out.decode("gbk", errors="ignore")
        # CSV 行："LoveAIPet.exe","1234","Console","1","20,000 K"
        for line in text.splitlines():
            line = line.strip()
            if not line or "LoveAIPet.exe" not in line:
                continue
            parts = [p.strip().strip('"') for p in line.split(",")]
            if len(parts) >= 2 and parts[1].isdigit():
                pids.append(int(parts[1]))
    except Exception as e:
        logger.debug("查询 LoveAIPet.exe PID 失败: %s", e)
    return pids


# ------------------------------------------------------------------ #
# 环境变量注入（共享配置路径 + 版本号）
# ------------------------------------------------------------------ #
def _build_env(shared_config_path: Optional[str]) -> dict:
    """构造桌宠子进程环境：继承当前环境，注入共享配置路径与主程序版本号。"""
    env = dict(os.environ)
    if shared_config_path:
        env["LOVEAI_PET_SHARED"] = shared_config_path
    env["LOVEAI_APP_VERSION"] = _load_app_version()
    return env


# ------------------------------------------------------------------ #
# 对外 API：拉起 / 停止
# ------------------------------------------------------------------ #
def launch_pet(shared_config_path: Optional[str] = None) -> str:
    """拉起 Electron 桌宠，返回状态枚举。

    返回值（见模块顶部 ST_*）：
      - already_running：启动前已检测到 LoveAIPet.exe（已记录其 PID，纳入生命周期）
      - ok             ：本次真的拉起了新进程
      - no_resources   ：既无打包 exe 也无开发目录
      - launch_failed  ：Popen 异常
    """
    if _state["launched"]:
        return _state.get("last_status") or ST_ALREADY_RUNNING
    _state["launched"] = True
    _state["lifecycle_tied"] = _load_lifecycle_tied()

    # 1. 探测启动前已存在的 LoveAIPet.exe（旧独立使用场景）
    existing = _list_pet_pids()
    if existing:
        _state["preexisting_pids"] = existing
        logger.info("检测到 LoveAIPet.exe 已在运行(PID=%s)，跳过拉起；lifecycle_tied=%s",
                    existing, _state["lifecycle_tied"])
        _state["last_status"] = ST_ALREADY_RUNNING
        return ST_ALREADY_RUNNING

    # 2. 优先打包 exe
    exe = _find_packaged_exe()
    env = _build_env(shared_config_path)
    try:
        if exe:
            logger.info("启动桌宠（打包 exe）: %s", exe)
            _state["proc"] = subprocess.Popen(
                [exe],
                cwd=os.path.dirname(exe),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0),
                close_fds=True,
            )
            _state["pid"] = _state["proc"].pid
            logger.info("桌宠进程已拉起 PID=%s", _state["pid"])
            _state["last_status"] = ST_OK
            return ST_OK

        # 3. 开发模式：npm start
        pet_dir = _pet_dir()
        if os.path.isdir(pet_dir) and os.path.isfile(os.path.join(pet_dir, "package.json")):
            npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
            logger.info("启动桌宠（开发模式 npm start）: %s", pet_dir)
            _state["proc"] = subprocess.Popen(
                [npm_cmd, "start"],
                cwd=pet_dir,
                shell=True,  # npm 在 Windows 上是 .cmd，需要 shell
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            _state["pid"] = _state["proc"].pid
            logger.info("桌宠进程已拉起(npm) PID=%s", _state["pid"])
            _state["last_status"] = ST_OK
            return ST_OK

        logger.warning("未找到桌宠资源：打包 exe 与开发目录 %s 均不存在", pet_dir)
        _state["last_status"] = ST_NO_RESOURCES
        return ST_NO_RESOURCES
    except Exception as e:
        logger.warning("拉起桌宠失败（不影响主程序）: %s", e)
        _state["proc"] = None
        _state["pid"] = None
        _state["last_status"] = ST_LAUNCH_FAILED
        return ST_LAUNCH_FAILED


def _kill_pid_tree(pid: int) -> bool:
    """用 taskkill /T /F 终止指定 PID 的整棵进程树。返回是否命令执行成功。"""
    if not pid:
        return False
    try:
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if r.returncode == 0:
            logger.info("已终止桌宠进程树 PID=%s", pid)
            return True
        logger.debug("taskkill PID=%s 返回码=%d（进程可能已退出）", pid, r.returncode)
        return False
    except Exception as e:
        logger.debug("终止桌宠 PID=%s 异常（忽略）: %s", pid, e)
        return False


def stop_launched_pet() -> None:
    """主程序退出时调用：终止桌宠进程，杜绝孤儿进程。

    策略：
      1) 本启动器本次拉起的进程（_state['pid']）必杀；
      2) 若 lifecycle_tied=true（默认），启动前已存在的 LoveAIPet.exe
         也一并纳入整体生命周期同杀；=false 时保留不动。
    全程错误隔离：任何异常都不影响主程序退出流程。
    """
    try:
        # 1) 本次拉起的进程
        own_pid = _state.get("pid")
        if own_pid:
            _kill_pid_tree(own_pid)
            _state["pid"] = None
        # 兜底：Popen 句柄若还在，wait 一下回收（不阻塞退出）
        proc = _state.get("proc")
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.wait(timeout=2)
            except Exception:
                pass
            _state["proc"] = None

        # 2) 启动前已存在的进程（仅当 lifecycle_tied）
        if _state.get("lifecycle_tied"):
            for pid in list(_state.get("preexisting_pids", [])):
                _kill_pid_tree(pid)
        else:
            if _state.get("preexisting_pids"):
                logger.info("lifecycle_tied=false，保留启动前已存在的桌宠 PID=%s",
                            _state["preexisting_pids"])
        _state["preexisting_pids"] = []
        _state["launched"] = False
    except Exception as e:
        # 绝不让桌宠清理异常影响主程序退出
        logger.debug("stop_launched_pet 异常（忽略）: %s", e)


def get_status() -> Optional[str]:
    """返回最近一次 launch_pet 的状态枚举（供上层降级提示）。"""
    return _state.get("last_status")
