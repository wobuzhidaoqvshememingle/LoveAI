# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 桌宠共享配置（R3.3-76 验收点3：单点配置全局生效、双端一致）
======================================================================
单一事实源：主程序根 config.json 的 ``pet`` 段。

运行时投影：主程序启动、拉起桌宠【之前】，把 pet 段投影写成
``data/pet_shared.json``（可写数据目录），并通过环境变量
``LOVEAI_PET_SHARED`` 把路径传给桌宠进程。桌宠 settings-store 启动时读取
该文件（不存在则回退本地 userData/settings.json），实现"主程序配一次、
桌宠跟着生效"。

上行通道：桌宠侧在设置面板切角色 / 沉浸式 / 置顶 / 音量 / 拖动窗口等改动，
经 WS 发 ``{event:"config_update", data:{...}}`` 上行；本模块把回写落到
``pet_shared.json`` 与主 config.json 的 pet 段，并发射 config_changed 信号，
保证双端最终一致。

设计：
- 任何读写异常都不抛、不阻断主程序；只记日志。
- 投影形状刻意对齐桌宠 settings-store 的嵌套结构（window/pet/system），
  方便桌宠直接 deepMerge，无需再做字段映射。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("LoveAI.pet_config")

# 默认 pet 段（config.json 缺字段时的兜底）
_DEFAULT_PET = {
    "enabled": True,
    "lifecycle_tied": True,
    "model": "cybergf",
    "volume": 0.8,
    "always_on_top": True,
    "immersive": False,
    "opacity": 1.0,
    "window": {"width": 400, "height": 600, "x": None, "y": None},
    "auto_launch": False,
    "heartbeat_interval": 15,
    "heartbeat_timeout": 45,
}


def _app_root() -> str:
    try:
        from services import runtime_paths as _rp
        return _rp.app_root()
    except Exception:
        # 兜底：开发态仓库根
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _writable_data_dir() -> str:
    try:
        from services import runtime_paths as _rp
        return _rp.writable_data_dir()
    except Exception:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _config_path() -> str:
    return os.path.join(_app_root(), "config.json")


def shared_config_path() -> str:
    """共享运行时配置文件绝对路径：<install_dir>\\data\\pet_shared.json。"""
    return os.path.join(_writable_data_dir(), "pet_shared.json")


def _read_config() -> dict:
    try:
        p = _config_path()
        if not os.path.isfile(p):
            return {}
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("读取主 config.json 失败（用默认 pet 段）: %s", e)
        return {}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def pet_section() -> dict:
    """返回主 config.json 的 pet 段（已与默认值合并）。"""
    cfg = _read_config()
    pet = cfg.get("pet", {}) if isinstance(cfg, dict) else {}
    merged = _deep_merge(_DEFAULT_PET, pet if isinstance(pet, dict) else {})
    return merged


def app_version() -> str:
    cfg = _read_config()
    app = cfg.get("app", {}) if isinstance(cfg, dict) else {}
    return str(app.get("version", "2.0.0"))


def build_shared_payload() -> dict:
    """把主 config.json 的 pet 段投影成桌宠 settings-store 形状。"""
    pet = pet_section()
    win = pet.get("window", {}) or {}
    payload = {
        "window": {
            "width": win.get("width", 400),
            "height": win.get("height", 600),
            "opacity": pet.get("opacity", 1.0),
            "alwaysOnTop": pet.get("always_on_top", True),
            "immersive": pet.get("immersive", False),
            "x": win.get("x"),
            "y": win.get("y"),
        },
        "pet": {
            "currentModel": pet.get("model", "cybergf"),
            "volume": pet.get("volume", 0.8),
        },
        "system": {
            "autoLaunch": pet.get("auto_launch", False),
        },
        "app": {
            "version": app_version(),
        },
    }
    return payload


def write_shared_config() -> Optional[str]:
    """拉起桌宠前调用：把 pet 段投影写入 data/pet_shared.json。返回路径（失败 None）。"""
    try:
        data_dir = _writable_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        payload = build_shared_payload()
        path = shared_config_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        logger.info("桌宠共享配置已写入: %s", path)
        return path
    except Exception as e:
        logger.warning("写入桌宠共享配置失败（不影响启动）: %s", e)
        return None


# ------------------------------------------------------------------ #
# 上行回写：桌宠侧设置变更 -> 主 config.json pet 段 + 共享文件
# ------------------------------------------------------------------ #
# 允许桌宠上行修改的白名单字段（点路径 -> 主 config.json pet 段字段名）
_UPLINK_WHITELIST = {
    "window.x": ("window", "x"),
    "window.y": ("window", "y"),
    "window.alwaysOnTop": ("always_on_top",),
    "window.opacity": ("opacity",),
    "window.immersive": ("immersive",),
    "pet.currentModel": ("model",),
    "pet.volume": ("volume",),
    "system.autoLaunch": ("auto_launch",),
}


def _set_by_path(obj: dict, keys, value):
    cur = obj
    for k in keys[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[keys[-1]] = value


def apply_uplink(patch: dict) -> None:
    """处理桌宠上行的 config_update patch。

    patch 形如 {"window.x": 100, "pet.currentModel": "haru", ...}（点路径）。
    仅白名单字段生效；回写主 config.json pet 段，并刷新 pet_shared.json。
    全程错误隔离。
    """
    if not isinstance(patch, dict) or not patch:
        return
    try:
        cfg = _read_config()
        if not isinstance(cfg, dict):
            cfg = {}
        pet = cfg.get("pet", {})
        if not isinstance(pet, dict):
            pet = {}
        changed = False
        for dotpath, value in patch.items():
            keys = _UPLINK_WHITELIST.get(dotpath)
            if not keys:
                continue
            # 基本类型白名单校验：只收 数字/字符串/布尔
            if not isinstance(value, (int, float, str, bool)):
                continue
            try:
                _set_by_path(pet, keys, value)
                changed = True
            except Exception:
                continue
        if not changed:
            return
        cfg["pet"] = pet
        # 写回主 config.json（原子替换）
        try:
            p = _config_path()
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
            os.replace(tmp, p)
        except Exception as e:
            logger.warning("回写主 config.json pet 段失败: %s", e)
        # 刷新共享文件
        write_shared_config()
        # 发信号通知主程序 UI（若信号总线可用）
        try:
            from app.signals import get_signals
            get_signals().config_changed.emit("pet")
        except Exception:
            pass
        logger.info("桌宠上行配置已回写: %s", list(patch.keys()))
    except Exception as e:
        logger.debug("处理桌宠上行配置异常（忽略）: %s", e)
