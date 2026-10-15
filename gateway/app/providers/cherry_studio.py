# -*- coding: utf-8 -*-
"""
Cherry Studio 桌面端适配器 (参考实现)。

Cherry Studio: https://github.com/CherryHQ/cherry-studio
免费开源, 内置多家模型。
"""
from __future__ import annotations

import time
from typing import Any

from app.config import ProviderConfig
from app.providers.desktop_base import BaseDesktopProvider, DesktopProviderError


class CherryStudioProvider(BaseDesktopProvider):
    desktop_app_name = "Cherry Studio"
    desktop_exe_hint = "Cherry Studio.exe"

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        import os
        self.install_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Cherry Studio\Cherry Studio.exe"),
            r"C:\Program Files\Cherry Studio\Cherry Studio.exe",
        ]

    def _locate_chat_window(self) -> Any:
        auto = self._ensure_automation()
        # Cherry Studio 是 Electron 应用, 主窗口类名一般是 Chrome_WidgetWin_1
        # 标题含 "Cherry Studio"
        root = auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_1")
        if root.Exists(3):
            name = root.Name or ""
            if "cherry" in name.lower():
                return root
        return None

    def _send_message(self, window: Any, text: str) -> None:
        auto = self._ensure_automation()
        # 找输入框 (ChatInput / contenteditable div)
        edit = window.EditControl(searchDepth=10, SubName="")
        if not edit.Exists(2):
            # 退化: 直接用键盘
            auto.SendKeys(text, interval=0.02)
            auto.SendKeys("{Enter}")
            return
        edit.SetFocus()
        edit.SendKeys(text, interval=0.02)
        time.sleep(0.3)
        # Ctrl+Enter 发送 (Cherry Studio 默认)
        auto.SendKeys("{Ctrl}{Return}")

    def _read_reply(self, window: Any, since_ts: float) -> str:
        # Cherry Studio 消息在自定义 WebView 里, uiautomation 不一定读得到文本节点
        # 退化方案: 用剪贴板/Ctrl+A 选中回复区域
        auto = self._ensure_automation()
        try:
            # 等回复稳定 (最后一条消息不再变化 2 秒)
            prev = ""
            stable = 0
            for _ in range(15):  # 最多 30 秒
                time.sleep(2)
                # 尝试取所有文本块
                texts = []
                for ctrl in window.GetChildren():
                    try:
                        if ctrl.Name:
                            texts.append(ctrl.Name)
                    except Exception:
                        pass
                cur = "\n".join(texts)
                if cur == prev:
                    stable += 1
                    if stable >= 1:
                        # 取最后一段作为回复
                        parts = [t for t in texts if len(t) > 5]
                        return parts[-1] if parts else ""
                else:
                    prev = cur
                    stable = 0
            return prev
        except Exception as e:
            raise DesktopProviderError(f"read reply failed: {e}", provider=self.name)
