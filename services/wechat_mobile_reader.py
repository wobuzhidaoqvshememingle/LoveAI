# -*- coding: utf-8 -*-
"""E类：手机端读取（备份/root/Xposed预留/投屏OCR/通知同步）。"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)


class WechatMobileReader:
    def availability_check(self) -> dict:
        return {"available": False, "reason": "手机端接口预留，即将推出"}

    def read_backup(self) -> list: return []
    def read_root_db(self) -> list: return []  # 预留
    def read_xposed(self) -> list: return []  # 预留
    def read_screencast_ocr(self) -> list: return []
    def read_notification_sync(self) -> list: return []

    def import_chats(self) -> list:
        return self.read_backup() + self.read_screencast_ocr() + self.read_notification_sync()
