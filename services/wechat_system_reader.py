# -*- coding: utf-8 -*-
"""D类：系统级读取（剪贴板/通知/输入法/DNS/网络/文件传输助手/收藏）。"""
from __future__ import annotations
import logging, subprocess

logger = logging.getLogger(__name__)


def _read_clipboard_text() -> str:
    """ctypes GetClipboardData CF_UNICODETEXT，只读当前剪贴板。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        CF_UNICODETEXT = 13
        if not user32.OpenClipboard(0):
            return ""
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h: return ""
            buf = ctypes.c_wchar_p(ctypes.windll.kernel32.GlobalLock(h))
            text = buf.value or ""
            ctypes.windll.kernel32.GlobalUnlock(h)
            return text
        finally:
            user32.CloseClipboard()
    except Exception as e:
        logger.debug("剪贴板读取失败: %s", e)
        return ""


def _read_dns() -> str:
    try:
        r = subprocess.run(["ipconfig", "/displaydns"], capture_output=True, text=True, timeout=5)
        return r.stdout[:2000]
    except Exception:
        return ""


class WechatSystemReader:
    def availability_check(self) -> dict:
        return {"available": True, "reason": ""}

    def read_clipboard_text(self) -> str:
        return _read_clipboard_text()

    def read_clipboard_history(self) -> list:
        txt = _read_clipboard_text()
        return [{"text": txt}] if txt else []

    def read_notifications(self) -> list:
        """读取 Windows 通知数据库（%LOCALAPPDATA%\\Microsoft\\Windows\\Notifications）。"""
        try:
            import glob, os
            path = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications")
            return glob.glob(os.path.join(path, "**", "*"), recursive=True)[:50]
        except Exception:
            return []

    def read_ime_user_dict(self) -> list:
        return []

    def read_dns_cache(self) -> str:
        return _read_dns()

    def read_network_metadata(self) -> list:
        try:
            import psutil
            out = []
            for c in psutil.net_connections(kind="inet"):
                if c.status == "ESTABLISHED":
                    out.append({"laddr": str(c.laddr), "raddr": str(c.raddr), "pid": c.pid})
            return out[:100]
        except Exception:
            return []

    def read_file_helper(self) -> list:
        return []  # 需要微信内部API

    def read_favorites(self) -> list:
        return []  # 需要微信内部API

    def import_chats(self) -> list:
        return self.read_clipboard_history()
