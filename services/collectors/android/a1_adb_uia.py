# -*- coding: utf-8 -*-
"""A1 adb shell uiautomator dump → pull XML → 按 bounds 几何解析消息方向。

免 root、只读界面树。uia 拿不到的场景（如自定义渲染、加密页面）由 A2 OCR 兜底。
available() 用 env.adb_devices 真实检测；无设备时 missing 并给开 USB 调试引导。
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from ..base import (SourceAdapter, Message, Availability, RISK_MEDIUM,
                    PLAT_ANDROID, DIR_SENT, DIR_RECEIVED, ok, missing)
from .. import env

logger = logging.getLogger(__name__)

# 聊天窗口宽度阈值（相对屏幕宽度比例）：
#   右气泡中心 >= 0.62 判为 sent；左气泡中心 <= 0.45 判为 received。
# 与 vision.py 的几何判定保持一致。
_SENT_RATIO = 0.62
_RECV_RATIO = 0.45

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def parse_uia_xml(xml_text: str,
                  screen_width: Optional[int] = None,
                  source_id: str = "A1_adb_uia",
                  peer_name: Optional[str] = None) -> List[Message]:
    """解析 uiautomator dump 的 XML，返回消息列表。

    - 只取 class=android.widget.TextView 且 text 非空的节点
    - 用 bounds 中心点相对屏幕宽度判方向
    - 自动过滤明显的非消息节点（标题栏、发送按钮、输入框等）
    """
    root = ET.fromstring(xml_text)

    # 推断屏幕宽度：取最顶层节点 bounds 的右边界
    if screen_width is None:
        screen_width = 0
        m = _BOUNDS_RE.search(root.attrib.get("bounds", ""))
        # hierarchy 根没有 bounds，向下找第一个有 bounds 的大节点
        for node in root.iter():
            b = node.attrib.get("bounds", "")
            mm = _BOUNDS_RE.search(b)
            if mm:
                right = int(mm.group(3))
                if right > screen_width:
                    screen_width = right
        if screen_width <= 0:
            screen_width = 1080  # 兜底

    items = []
    for node in root.iter("node"):
        cls = node.attrib.get("class", "")
        text = (node.attrib.get("text") or "").strip()
        if not text or cls != "android.widget.TextView":
            continue
        # 可点击的 TextView（发送按钮/复制菜单等）不是消息内容，跳过
        if node.attrib.get("clickable", "false") == "true":
            continue
        b = node.attrib.get("bounds", "")
        mm = _BOUNDS_RE.search(b)
        if not mm:
            continue
        x0, y0, x1, y1 = (int(mm.group(i)) for i in range(1, 5))
        center_x = (x0 + x1) / 2.0
        ratio = center_x / float(screen_width)
        if ratio >= _SENT_RATIO:
            direction = DIR_SENT
        elif ratio <= _RECV_RATIO:
            direction = DIR_RECEIVED
        else:
            continue  # 中间区域（标题/按钮）跳过
        items.append({"dir": direction, "text": text,
                      "top": y0, "bottom": y1,
                      "height": max(1.0, float(y1 - y0))})

    items.sort(key=lambda d: d["top"])

    # 聚合同方向垂直紧邻的行（一个气泡可能折行成多个 TextView）
    messages: List[Message] = []
    cur = None
    for it in items:
        if (cur and it["dir"] == cur["dir"]
                and it["top"] - cur["bottom"] <= cur["height"] * 0.9):
            cur["text"] = cur["text"] + "\n" + it["text"]
            cur["bottom"] = it["bottom"]
        else:
            if cur:
                messages.append(_mk(cur, source_id, peer_name))
            cur = dict(it)
    if cur:
        messages.append(_mk(cur, source_id, peer_name))
    return messages


def _mk(cluster: dict, source_id: str, peer_name: Optional[str]) -> Message:
    return Message(
        direction=cluster["dir"],
        content=cluster["text"].strip(),
        timestamp=None,
        msg_type="text",
        source_id=source_id,
        peer_name=peer_name,
        speaker="我" if cluster["dir"] == DIR_SENT else (peer_name or "对方"),
    )


class A1AdbUiaAdapter(SourceAdapter):
    id = "A1_adb_uia"
    name = "ADB uiautomator界面树解析"
    platform = PLAT_ANDROID
    requires = frozenset({"adb", "device"})
    risk_level = RISK_MEDIUM
    default_enabled = True
    local_runnable = True
    success_prior = 0.75
    produces_plaintext = True

    def available(self) -> Availability:
        adb = env.adb_path()
        if not adb:
            return missing("未找到 adb（随包 tools/platform-tools/adb.exe 或 PATH）",
                           ["检查 tools/platform-tools/adb.exe 是否存在"])
        devices = env.adb_devices()
        if not devices:
            return missing("adb 未检测到已授权设备",
                           ["手机开启 USB 调试（开发者选项）",
                            "用 USB 连接并在手机上点\"允许此电脑调试\"",
                            "执行 adb devices 能看到 device 状态"])
        return ok(f"检测到 {len(devices)} 台已授权设备: {', '.join(devices)}",
                  devices=list(devices))

    def collect(self,
                device: str = "",
                xml_text: Optional[str] = None,
                screen_width: Optional[int] = None,
                peer_name: Optional[str] = None,
                **opts) -> List[Message]:
        if xml_text is not None:
            # 测试/离线路径：直接喂 XML
            return parse_uia_xml(xml_text, screen_width=screen_width,
                                 source_id=self.id, peer_name=peer_name)
        # 真实路径：dump + pull
        adb = env.adb_path()
        if not adb:
            raise RuntimeError("adb 不可用")
        devices = env.adb_devices()
        if not devices:
            raise RuntimeError("无已授权设备；先在手机上开 USB 调试并连接")
        target = device or devices[0]
        prefix = [adb, "-s", target] if target else [adb]
        remote_xml = "/sdcard/window_dump.xml"
        subprocess.run(prefix + ["shell", "uiautomator", "dump", remote_xml],
                       capture_output=True, timeout=30, check=True)
        with tempfile.TemporaryDirectory() as td:
            local_xml = os.path.join(td, "dump.xml")
            with open(local_xml, "wb") as f:
                subprocess.run(prefix + ["exec-out", "cat", remote_xml],
                               stdout=f, timeout=30, check=True)
            with open(local_xml, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        return parse_uia_xml(text, screen_width=screen_width,
                             source_id=self.id, peer_name=peer_name)
