# -*- coding: utf-8 -*-
"""
检测本机安装了哪些 AI 桌面端软件。

查常见安装路径 + 开始菜单快捷方式。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class InstalledDesktopApp:
    name: str
    exe_path: str
    installed: bool = True


# 常见安装路径 (Windows)
KNOWN_DESKTOP_APPS = [
    {
        "name": "Cherry Studio",
        "exe_hint": "Cherry Studio.exe",
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Cherry Studio\Cherry Studio.exe"),
            r"C:\Program Files\Cherry Studio\Cherry Studio.exe",
        ],
    },
    {
        "name": "Chatbox",
        "exe_hint": "Chatbox.exe",
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Chatbox\Chatbox.exe"),
            r"C:\Program Files\Chatbox\Chatbox.exe",
        ],
    },
    {
        "name": "NextChat",
        "exe_hint": "NextChat.exe",
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\NextChat\NextChat.exe"),
        ],
    },
    {
        "name": "DeepSeek Desktop",
        "exe_hint": "deepseek.exe",
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\DeepSeek\DeepSeek.exe"),
        ],
    },
    {
        "name": "通义千问",
        "exe_hint": "Tongyi Qianwen.exe",
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tongyi\Tongyi.exe"),
        ],
    },
    {
        "name": "Kimi",
        "exe_hint": "Kimi.exe",
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Kimi\Kimi.exe"),
        ],
    },
    {
        "name": "豆包",
        "exe_hint": "Doubao.exe",
        "paths": [
            os.path.expandvars(r"%LOCALAPPDATA%\Doubao\Doubao.exe"),
        ],
    },
]


def detect_installed_apps() -> list[InstalledDesktopApp]:
    """返回本机实际安装的桌面端软件列表。"""
    out: list[InstalledDesktopApp] = []
    for app in KNOWN_DESKTOP_APPS:
        for p in app["paths"]:
            if os.path.exists(p):
                out.append(InstalledDesktopApp(name=app["name"], exe_path=p, installed=True))
                break
    return out
