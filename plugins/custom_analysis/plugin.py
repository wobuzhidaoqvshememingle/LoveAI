# -*- coding: utf-8 -*-
"""样例插件：自定义分析维度。"""
import logging
logger = logging.getLogger(__name__)

class Plugin:
    def __init__(self):
        self.dimensions = ["幽默感", "真诚度"]
    def initialize(self): pass
    def get_extra_dimensions(self):
        return {d: 50 for d in self.dimensions}
    def on_before_analysis(self, ctx: dict): return ctx
    def on_after_analysis(self, result: dict):
        result.setdefault("extra_dimensions", {}).update(self.get_extra_dimensions())
        return result
    def cleanup(self): pass
