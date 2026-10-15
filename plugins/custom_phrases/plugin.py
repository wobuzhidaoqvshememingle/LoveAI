# -*- coding: utf-8 -*-
"""样例插件：自定义话术库。纯 Python，不 import PySide6。"""
import json, os, logging
logger = logging.getLogger(__name__)

class Plugin:
    def __init__(self):
        self.phrases = []
    def initialize(self):
        try:
            p = os.path.join(os.path.dirname(__file__), "sample_phrases.json")
            with open(p, "r", encoding="utf-8") as f:
                self.phrases = json.load(f)
        except Exception as e:
            logger.debug("话术加载失败: %s", e)
    def get_phrases(self, scene: str = None):
        if scene is None: return self.phrases
        return [x for x in self.phrases if x.get("scene") == scene]
    def on_analysis_complete(self, result: dict):
        return result
    def cleanup(self):
        self.phrases = []
