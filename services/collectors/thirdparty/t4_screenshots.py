# -*- coding: utf-8 -*-
"""T4 任意来源"聊天截图文件夹"批量 OCR → 标准消息（本地 OCR，默认不上传）。

零/低风险、只读图片。按文件名排序逐张识别，跨张去重交给 store。
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_LOW,
                    PLAT_THIRDPARTY, missing, ok)
from .. import env, vision

logger = logging.getLogger(__name__)

_IMG_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


class T4ScreenshotFolderAdapter(SourceAdapter):
    id = "T4_screenshot_ocr"
    name = "聊天截图文件夹批量OCR"
    platform = PLAT_THIRDPARTY
    requires = frozenset({"vision"})
    risk_level = RISK_LOW
    default_enabled = True
    local_runnable = True
    success_prior = 0.8
    produces_plaintext = True

    def available(self) -> Availability:
        if not env.ocr_available():
            return missing("本地 OCR 引擎不可用",
                           ["安装 rapidocr-onnxruntime（pip install rapidocr-onnxruntime）",
                            "或在设置中配置可用的本地视觉模型"])
        return ok("本地 OCR(rapidocr) 可用")

    def collect(self, folder: str = "", files: Optional[List[str]] = None,
                peer_name: Optional[str] = None, **opts) -> List[Message]:
        paths = self._gather(folder, files)
        out: List[Message] = []
        for p in paths:
            try:
                width = vision.image_width_of(p)
                result = vision.ocr_image(p)
                msgs = vision.parse_chat_ocr(result, width, source_id=self.id,
                                             peer_name=peer_name)
                out.extend(msgs)
            except Exception as e:  # 单张失败不阻断整批
                logger.warning("OCR 跳过 %s: %s", p, e)
        return out

    @staticmethod
    def _gather(folder: str, files: Optional[List[str]]) -> List[str]:
        out: List[str] = []
        if files:
            out.extend(files)
        if folder and os.path.isdir(folder):
            for fn in sorted(os.listdir(folder)):
                if fn.lower().endswith(_IMG_EXT):
                    out.append(os.path.join(folder, fn))
        # 去重保序
        seen = set()
        uniq = []
        for p in out:
            ap = os.path.abspath(p)
            if ap not in seen:
                seen.add(ap)
                uniq.append(p)
        return uniq
