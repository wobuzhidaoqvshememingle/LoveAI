# -*- coding: utf-8 -*-
"""A6 scrcpy 投屏 + 复用 PC 视觉采集解析投屏窗口。

scrcpy 把安卓屏幕镜像到一个本地窗口；本通道：
  - available() 检测 env.scrcpy_dir() 是否存在
  - 不自动启动 scrcpy（避免干扰用户），只在用户已经投屏后，对投屏窗口截图
    并复用 vision.parse_chat_ocr 解析
  - 本地不可独立跑（需要 scrcpy 二进制 + 真机），测试用 monkeypatch 验证检测逻辑
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from typing import List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_MEDIUM,
                    PLAT_ANDROID, ok, missing)
from .. import env, vision

logger = logging.getLogger(__name__)


class A6ScrcpyAdapter(SourceAdapter):
    id = "A6_scrcpy"
    name = "scrcpy投屏窗口视觉采集"
    platform = PLAT_ANDROID
    requires = frozenset({"device", "vision"})
    risk_level = RISK_MEDIUM
    default_enabled = True
    local_runnable = False
    success_prior = 0.5
    produces_plaintext = True

    def available(self) -> Availability:
        scrcpy = env.scrcpy_dir()
        if not scrcpy:
            return missing("未检测到 scrcpy 目录（tools/scrcpy）",
                           ["下载 scrcpy 解压到 tools/scrcpy/",
                            "或把 scrcpy 加入 PATH"])
        adb = env.adb_path()
        if not adb:
            return missing("scrcpy 存在但 adb 不可用",
                           ["检查 tools/platform-tools/adb.exe"])
        devices = env.adb_devices()
        if not devices:
            return missing("scrcpy/adb 就绪但无设备",
                           ["USB 连接手机并开 USB 调试",
                            "运行 scrcpy 投屏后再采集"])
        if not env.ocr_available():
            return missing("设备就绪但本地 OCR 不可用",
                           ["pip install rapidocr-onnxruntime"])
        return ok(f"scrcpy 在 {scrcpy}，设备 {', '.join(devices)} 在线",
                  scrcpy_dir=scrcpy, devices=list(devices))

    def collect(self,
                image_path: Optional[str] = None,
                peer_name: Optional[str] = None,
                **opts) -> List[Message]:
        """image_path 非空时直接对该图 OCR（测试/用户已自行截图）。
        否则尝试用 adb exec-out screencap 拉一张当前投屏设备的截图。"""
        if image_path:
            return self._ocr_one(image_path, peer_name)

        devices = env.adb_devices()
        if not devices:
            raise RuntimeError("无设备；请先 scrcpy 投屏")
        adb = env.adb_path()
        target = devices[0]
        with tempfile.TemporaryDirectory() as td:
            png = os.path.join(td, "scrcpy.png")
            with open(png, "wb") as f:
                subprocess.run([adb, "-s", target, "exec-out",
                                "screencap", "-p"],
                               stdout=f, timeout=30, check=True)
            return self._ocr_one(png, peer_name)

    def _ocr_one(self, path: str, peer_name: Optional[str]) -> List[Message]:
        width = vision.image_width_of(path)
        result = vision.ocr_image(path)
        return vision.parse_chat_ocr(result, width, source_id=self.id,
                                      peer_name=peer_name)
