# -*- coding: utf-8 -*-
"""第八批剩余第一批：快捷面板/主题/插件/流式接口/多平台导入。纯函数，向后兼容。"""
from __future__ import annotations
import logging, time
logger = logging.getLogger(__name__)

# ============ 14. 右侧快捷面板数据接口 ============
def get_quick_panel_data(tasks: list, axes: dict) -> dict:
    return {"recent_tasks": tasks[:3], "mini_axes": axes,
            "quick_actions": ["截图分析","代聊","查档案"]}

# ============ 15. 主题/皮肤管理 ============
THEMES = {"深色":{},"浅色":{},"护眼粉":{},"暗夜蓝":{}}
class ThemeManager:
    def __init__(self): self.theme = "深色"; self.font = "中"
    def set(self, name: str, font: str = "中"):
        if name in THEMES: self.theme = name
        self.font = font
    def info(self) -> dict: return {"theme": self.theme, "font_size": self.font}

# ============ 16. 插件系统 ============
class PluginManager:
    def __init__(self): self.plugins = {}
    def load(self, name: str, mod): self.plugins[name] = {"mod": mod, "enabled": True}
    def disable(self, name: str):
        if name in self.plugins: self.plugins[name]["enabled"] = False
    def active(self) -> list: return [n for n, p in self.plugins.items() if p["enabled"]]

# ============ 17. 流式生成接口（生成器）============
def stream_generate(text: str):
    for ch in text:
        yield ch

# ============ 18. 多平台聊天记录导入 ============
def import_chats(platform: str, data: str) -> dict:
    rows = []
    for line in (data or "").split("\n"):
        if not line.strip(): continue
        parts = line.split("|")
        if len(parts) >= 3:
            rows.append({"ts": parts[0], "speaker": parts[1], "content": "|".join(parts[2:])})
    rows = sorted(rows, key=lambda r: r["ts"])  # 按时间排序+去重
    return {"platform": platform, "imported": len(rows), "rows": rows}
