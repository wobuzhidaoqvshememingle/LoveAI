# -*- coding: utf-8 -*-
"""R3.3-57 ModelAdapterPool 降级链测试。

验证：
- 第一个 adapter 抛异常时切到第二个
- 全挂时走 rule_fallback，且不抛
- 冷却期跳过
- embed / classify 也能跑
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest
# 【废弃 · 2026-10-13】依赖的 services.data_pool.contracts/.model_adapter/.routing/.adapters
# 子模块已不存在（DAL 重构为单文件 services/data_pool.py，原 services/data_pool/ 包被移除）。
# 为避免凭空重建已下线子系统，本文件整体 skip；备份见 *.bak_skip_20261013_145803。
pytest.skip(
    "services.data_pool.contracts/.model_adapter/.routing/.adapters 子模块已随包移除而不存在，"
    "本 ModelAdapterPool 测试整体失效（DAL 现为单文件 services/data_pool.py）。",
    allow_module_level=True,
)

from services.data_pool.contracts import make_message
from services.data_pool.model_adapter import ModelAdapterPool
from services.data_pool.routing import Router
from services.data_pool.adapters import (
    BaseAdapter, OpenAICompatAdapter, OllamaAdapter, OnnxL1Adapter, RuleFallbackAdapter,
)


class TestModelAdapterPool(unittest.TestCase):

    def test_first_adapter_fails_second_succeeds(self):
        calls = []

        def boom(messages):
            calls.append("boom")
            raise RuntimeError("network down")

        def ok(messages):
            calls.append("ok")
            return "from ollama"

        pool = ModelAdapterPool(adapters={
            "openai_compat.free": OpenAICompatAdapter(chat_fn=boom),
            "ollama": OllamaAdapter(chat_fn=ok),
        })
        resp = pool.chat([make_message("user", "hi")], tier="draft")
        self.assertEqual(resp.text, "from ollama")
        self.assertEqual(resp.adapter, "ollama")
        self.assertIn("boom", calls)
        self.assertIn("ok", calls)

    def test_all_fail_falls_back_to_rule(self):
        def boom(messages):
            raise RuntimeError("x")

        pool = ModelAdapterPool(adapters={
            "openai_compat.free": OpenAICompatAdapter(chat_fn=boom),
            "ollama": OllamaAdapter(chat_fn=boom),
            "onnx_l1": OnnxL1Adapter(chat_fn=boom),
        })
        resp = pool.chat([make_message("user", "在吗？")], tier="judgment")
        self.assertFalse(resp.ok)
        self.assertEqual(resp.adapter, "rule_fallback")
        self.assertIn("系统提示", resp.text)

    def test_cooled_down_adapter_skipped(self):
        state = {"n": 0}

        def flaky(messages):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("first fail")
            return "second time ok"

        router = Router()
        pool = ModelAdapterPool(adapters={
            "openai_compat.free": OpenAICompatAdapter(chat_fn=flaky),
            "ollama": OllamaAdapter(chat_fn=lambda m: "ollama"),
        }, router=router)

        # 第一次：openai 失败 -> 切 ollama
        r1 = pool.chat([make_message("user", "a")], tier="draft")
        self.assertEqual(r1.text, "ollama")
        # openai_compat.free 刚被 record_failure，应在冷却 -> 跳过，直接 ollama
        r2 = pool.chat([make_message("user", "b")], tier="draft")
        self.assertEqual(r2.text, "ollama")
        # 没有再调 openai（冷却跳过），flaky 计数器保持 1
        self.assertEqual(state["n"], 1)

    def test_embed_uses_ollama(self):
        pool = ModelAdapterPool(adapters={
            "ollama": OllamaAdapter(embed_fn=lambda t: [0.1, 0.2, 0.3]),
        })
        v = pool.embed("你好")
        self.assertEqual(v, [0.1, 0.2, 0.3])

    def test_classify_uses_onnx(self):
        pool = ModelAdapterPool(adapters={
            "onnx_l1": OnnxL1Adapter(
                classify_fn=lambda t, labels: {l: 0.8 for l in labels}),
        })
        out = pool.classify("今天好累", ["positive", "negative"])
        self.assertEqual(out, {"positive": 0.8, "negative": 0.8})

    def test_onnx_chat_raises_and_degrades(self):
        # onnx_l1 不做自由生成，chat 必须抛 -> 被路由切到下一个
        pool = ModelAdapterPool(adapters={
            "onnx_l1": OnnxL1Adapter(classify_fn=lambda t, l: {x: 0.5 for x in l}),
            "ollama": OllamaAdapter(chat_fn=lambda m: "ollama answer"),
        })
        # judgment 链：[openai_compat.free, ollama, onnx_l1, rule_fallback]
        # openai 没注册 -> 跳过；ollama 注册了 -> 直接成功
        resp = pool.chat([make_message("user", "hi")], tier="judgment")
        self.assertEqual(resp.text, "ollama answer")

    def test_empty_messages_does_not_crash(self):
        pool = ModelAdapterPool()
        resp = pool.chat([], tier="judgment")
        # 空消息应该走 rule_fallback 的兜底（base 对空消息返回 fallback，
        # 但 pool 会继续尝试；最终落到 rule_fallback）
        self.assertIn(resp.adapter, ("rule_fallback", ""))


if __name__ == "__main__":
    unittest.main()
