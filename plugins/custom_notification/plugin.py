# -*- coding: utf-8 -*-
"""样例插件：分析完成后推送通知（控制台/日志，不弹窗）。"""
import logging
logger = logging.getLogger(__name__)

class Plugin:
    def __init__(self):
        self.channel = "log"
    def initialize(self): pass
    def send_notification(self, msg: str):
        logger.info("[notification] %s", msg)
    def on_analysis_complete(self, result: dict):
        self.send_notification("分析完成：" + str(result.get("person_id", "?")))
        return result
    def cleanup(self): pass
