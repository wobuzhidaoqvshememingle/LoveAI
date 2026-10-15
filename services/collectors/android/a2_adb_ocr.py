# -*- coding: utf-8 -*-
"""A2 adb exec-out screencap 截图 → 本地 OCR → 标准消息。

uia 拿不到（自定义渲染/加密页面）时的兜底。截图走本地 rapidocr，默认不上传。
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


class A2AdbOcrAdapter(SourceAdapter):
    id = "A2_adb_ocr"
    name = "ADB截图OCR(本地)"
    platform = PLAT_ANDROID
    requires = frozenset({"adb", "device", "vision"})
    risk_level = RISK_MEDIUM
    default_enabled = True
    local_runnable = True
    success_prior = 0.65
    produces_plaintext = True

    def available(self) -> Availability:
        adb = env.adb_path()
        if not adb:
            return missing("未找到 adb",
                           ["检查 tools/platform-tools/adb.exe"])
        devices = env.adb_devices()
        if not devices:
            return missing("无已授权设备",
                           ["手机开 USB 调试并连接",
                            "adb devices 看到 device 状态"])
        if not env.ocr_available():
            return missing("本地 OCR 引擎不可用",
                           ["pip install rapidocr-onnxruntime"])
        return ok(f"设备 {', '.join(devices)} 在线且本地 OCR 可用",
                  devices=list(devices))

    def collect(self,
                device: str = "",
                image_path: Optional[str] = None,
                peer_name: Optional[str] = None,
                **opts) -> List[Message]:
        """image_path 非空时直接对本地图片 OCR（测试/离线路径）；
        否则通过 adb exec-out screencap -p 拉一张截图再 OCR。"""
        if image_path:
            return self._ocr_one(image_path, peer_name)

        adb = env.adb_path()
        if not adb:
            raise RuntimeError("adb 不可用")
        devices = env.adb_devices()
        if not devices:
            raise RuntimeError("无已授权设备")
        target = device or devices[0]
        prefix = [adb, "-s", target] if target else [adb]
        with tempfile.TemporaryDirectory() as td:
            local_png = os.path.join(td, "screen.png")
            with open(local_png, "wb") as f:
                subprocess.run(prefix + ["exec-out", "screencap", "-p"],
                               stdout=f, timeout=30, check=True)
            return self._ocr_one(local_png, peer_name)

    def _ocr_one(self, path: str,
                 peer_name: Optional[str]) -> List[Message]:
        width = vision.image_width_of(path)
        result = vision.ocr_image(path)
        return vision.parse_chat_ocr(result, width, source_id=self.id,
                                      peer_name=peer_name)
