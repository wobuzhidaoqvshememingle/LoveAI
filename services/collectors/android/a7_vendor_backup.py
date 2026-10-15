# -*- coding: utf-8 -*-
"""A7 厂商备份/换机提取：解包并复用 A4 解密 EnMicroMsg.db。

支持的厂商备份布局（至少落地一种可解包格式）：
  - 三星 Smart Switch：<root>/SmartSwitch/AppData/com.tencent.mm/MicroMsg/<uin>/EnMicroMsg.db
  - 华为手机克隆：<root>/HuaweiBackup/mms/.../EnMicroMsg.db（启发式按文件名找）
  - 通用：递归在 <root> 下找名为 EnMicroMsg.db 的文件

不支持的厂商（未解包的 .ovsuite / .bac 等专有容器）给 missing 引导，
不强行解专有格式。
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

from ..base import (SourceAdapter, Message, Availability, RISK_LOW,
                    PLAT_ANDROID, ok, missing)
from .a4_root_db import derive_android_key, read_enmicromsg

logger = logging.getLogger(__name__)


def find_enmicromsg(root: str) -> Optional[str]:
    """在厂商备份根目录下递归找 EnMicroMsg.db；找到第一个返回绝对路径。"""
    if not os.path.isdir(root):
        return None
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            if fn == "EnMicroMsg.db":
                return os.path.join(dirpath, fn)
    return None


def read_manifest(root: str) -> dict:
    """读取厂商备份目录下的 manifest.json（若有），提取 imei/uin。"""
    mp = os.path.join(root, "manifest.json")
    if not os.path.isfile(mp):
        return {}
    try:
        with open(mp, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class A7VendorBackupAdapter(SourceAdapter):
    id = "A7_vendor_backup"
    name = "厂商备份/换机提取解析"
    platform = PLAT_ANDROID
    requires = frozenset()
    risk_level = RISK_LOW
    default_enabled = True
    local_runnable = True
    success_prior = 0.55
    produces_plaintext = True

    def available(self) -> Availability:
        try:
            from sqlcipher3 import dbapi2  # noqa: F401
        except Exception:
            return missing("sqlcipher3 不可用",
                           ["pip install sqlcipher3-binary"])
        return ok("纯离线解析厂商备份目录（含 EnMicroMsg.db）",
                 note="只支持已解包的目录布局；专有容器(.ovsuite/.bac)暂不支持")

    def collect(self,
                root: str = "",
                db_path: Optional[str] = None,
                imei: str = "",
                uin: str = "",
                passphrase: Optional[str] = None,
                talker: Optional[str] = None,
                **opts) -> List[Message]:
        if not db_path:
            if not root or not os.path.isdir(root):
                raise FileNotFoundError(
                    "A7 需要 root 指向已解包的厂商备份目录，或直接传 db_path")
            db_path = find_enmicromsg(root)
            if not db_path:
                raise FileNotFoundError(
                    f"在 {root} 下未找到 EnMicroMsg.db；该厂商备份可能是"
                    f"未解包的专有容器，先用厂商工具解包")
            # 从 manifest 补 imei/uin
            if not imei or not uin:
                manifest = read_manifest(root)
                imei = imei or str(manifest.get("device_imei", ""))
                uin = uin or str(manifest.get("account_uin", ""))
        if passphrase is None:
            if not imei or not uin:
                raise ValueError(
                    "A7 需要 passphrase 或 imei+uin（manifest.json 或显式参数）")
            passphrase = derive_android_key(imei, uin)
        return read_enmicromsg(db_path, passphrase, talker=talker)
