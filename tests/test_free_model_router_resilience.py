# -*- coding: utf-8 -*-
"""R3.3-30/31/32 FreeModelRouter 韧性测试：熔断/退避/冷却/探活。

全部离线：mock openai.OpenAI，不发真实网络请求；clock 与 sleep 均可控。
"""
import os
import sys
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.free_model_router import (
    FreeModelRouter,
    COOLDOWN_BASE_SEC, COOLDOWN_MAX_SEC,
    BACKOFF_RETRY_DELAYS, BACKOFF_MAX_RETRIES,
)


# ---- 受控假 openai 客户端 ----
class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content="ok"):
        self.choices = [_Choice(content)]


class _StatusErr(Exception):
    """模拟 openai v3 的 APIStatusError：带 .status_code。"""

    def __init__(self, code, body=""):
        super().__init__(f"HTTP {code}: {body}")
        self.status_code = code


def make_fake_openai(behavior: dict, calls: list):
    """返回一个可作为 openai.OpenAI side_effect 的工厂。

    behavior: {model_name: _Resp / Exception / callable() -> _Resp|Exception}
    calls: 每次 create() 把 model 名 append 进去，便于断言调用序列。
    """

    class _Completions:
        def create(self, **kw):
            model = kw["model"]
            calls.append(model)
            b = behavior.get(model, _Resp("ok"))
            if callable(b):
                b = b()
            if isinstance(b, Exception):
                raise b
            return b

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    return lambda *a, **k: _Client()


def make_router(behavior: dict, calls: list, clock=None):
    """构造一个只含 model-a:free / model-b:free 两个 openrouter 候选的路由器。"""
    cfg = {
        "llm.free.openrouter_key": "sk-or-x",
        "llm.free.include_ollama": "false",
        "llm.free.auto_catalog": "false",
        "llm.free.openrouter_models": "model-a:free,model-b:free",
        "llm.free.nvidia_catalog_md": "false",
        # 隔离零配置 provider：本文件只测 openrouter a/b 的熔断/退避/冷却
        "llm.free.zero_config.enabled": "false",
    }
    r = FreeModelRouter(cfg, http_get=lambda *a, **k: "", clock=clock)
    r._sleep = mock.Mock()  # 不真睡
    return r


A = "model-a:free"
B = "model-b:free"


class TestResilience(unittest.TestCase):

    def setUp(self):
        # 避免本地扫描/桌面AI捡到真机端点
        for meth in ("_collect_local_scan_models", "_collect_desktop_models"):
            p = mock.patch.object(FreeModelRouter, meth, lambda self: [])
            p.start()
            self.addCleanup(p.stop)

    @mock.patch("openai.OpenAI")
    def test_5xx_enters_cooldown(self, mock_openai):
        """1. 模拟 5xx → 该模型进 cooldown，30s 内不再选。"""
        calls = []
        behavior = {A: _StatusErr(500), B: _Resp("b-ok")}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        t = [1000.0]
        r = make_router(behavior, calls, clock=lambda: t[0])

        # 第一次：a 500 熔断 → 切 b 成功
        out = r.chat("sys", "hi")
        self.assertEqual(out["text"], "b-ok")
        self.assertEqual(calls, [A, B])
        self.assertIn(A, r._cooldown)
        self.assertAlmostEqual(r._cooldown[A] - t[0], COOLDOWN_BASE_SEC, places=1)
        self.assertEqual(r._consecutive_5xx[A], 1)

        # 第二次（时钟未走）：a 在冷却期内，直接被 ranked() 跳过，只调 b
        calls.clear()
        out = r.chat("sys", "hi")
        self.assertEqual(out["text"], "b-ok")
        self.assertEqual(calls, [B])  # a 根本没被调用

    @mock.patch("openai.OpenAI")
    def test_401_permanently_disables(self, mock_openai):
        """2. 模拟 401 → 该模型永久禁用，后续不再选。"""
        calls = []
        behavior = {A: _StatusErr(401), B: _Resp("b-ok")}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        r = make_router(behavior, calls, clock=lambda: 1000.0)
        r.chat("sys", "hi")
        self.assertIn(A, r._disabled)
        self.assertEqual(r._alive[A], False)

        # 之后无论调多少次，a 都不再出现
        calls.clear()
        r.chat("sys", "hi")
        r.chat("sys", "hi")
        self.assertEqual(calls, [B, B])

        # 状态查询里能看到永久禁用标记
        status = r.get_cooldown_status()
        self.assertTrue(status[A].get("permanently_disabled"))

    @mock.patch("openai.OpenAI")
    def test_429_exponential_backoff(self, mock_openai):
        """3. 模拟 429 → 指数退避 1/2/4s，同模型重试 3 次后成功，不切下一个。"""
        calls = []
        a_tries = []

        def a_behavior():
            a_tries.append(1)
            if len(a_tries) <= BACKOFF_MAX_RETRIES:
                return _StatusErr(429)
            return _Resp("a-after-backoff")

        behavior = {A: a_behavior, B: _Resp("b-should-not-be-called")}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        r = make_router(behavior, calls, clock=lambda: 1000.0)
        out = r.chat("sys", "hi")

        self.assertEqual(out["text"], "a-after-backoff")
        # a 被尝试 4 次（1 次初始 + 3 次退避），b 一次都没轮到
        self.assertEqual(calls, [A, A, A, A])
        # 退避序列 1s→2s→4s
        self.assertEqual(r._sleep.call_count, BACKOFF_MAX_RETRIES)
        waited = [c.args[0] for c in r._sleep.call_args_list]
        self.assertEqual(waited, list(BACKOFF_RETRY_DELAYS))

    @mock.patch("openai.OpenAI")
    def test_cooldown_expires_then_selectable(self, mock_openai):
        """4. cooldown 过期后模型重新可选。"""
        calls = []
        behavior = {A: _StatusErr(500), B: _Resp("b-ok")}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        t = [1000.0]
        r = make_router(behavior, calls, clock=lambda: t[0])

        # 第一次：a 500 → 冷却 30s（到 1030），b 成功
        r.chat("sys", "hi")
        self.assertIn(A, r._cooldown)
        a_cand = [c for c in r.candidates() if c.model == A][0]

        # 时钟走到冷却期内（t=1010）：a 仍不可选
        t[0] += 10.0
        self.assertFalse(r._eligible(a_cand))

        # 时钟走过冷却（t=1041）：a 恢复可选
        t[0] += 31.0
        self.assertTrue(r._eligible(a_cand))

        # 行为验证：让 b 也 5xx，这样路由会触达 a（证明 a 真被重新选中）
        behavior[B] = _StatusErr(503)
        calls.clear()
        with self.assertRaises(RuntimeError):  # a、b 都 5xx，最终抛错
            r.chat("sys", "hi")
        self.assertIn(A, calls)
        # 连续 5xx 升级为 2 次 → 冷却翻倍到 60s
        self.assertEqual(r._consecutive_5xx[A], 2)
        self.assertAlmostEqual(r._cooldown[A] - t[0], 60.0, places=1)

    @mock.patch("openai.OpenAI")
    def test_success_clears_cooldown(self, mock_openai):
        """5. 成功后清除该模型的 cooldown 记录与连续失败计数。"""
        calls = []
        behavior = {A: _StatusErr(500), B: _Resp("b-ok")}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        t = [1000.0]
        r = make_router(behavior, calls, clock=lambda: t[0])

        # 先把 a 打进冷却
        r.chat("sys", "hi")
        self.assertIn(A, r._cooldown)
        self.assertEqual(r._consecutive_5xx[A], 1)

        # 时钟走过冷却；让 b 失败（否则 b 成功率高会排在 a 前面直接命中），a 改为成功
        t[0] += 31.0
        behavior[B] = _StatusErr(503)
        behavior[A] = _Resp("a-recovered")
        calls.clear()
        out = r.chat("sys", "hi")

        self.assertEqual(out["text"], "a-recovered")
        self.assertEqual(calls, [B, A])  # b 先试（成功率高）失败，再触达 a 成功
        # 冷却与连续计数都被清掉
        self.assertNotIn(A, r._cooldown)
        self.assertEqual(r._consecutive_5xx[A], 0)
        self.assertEqual(r._alive[A], True)

    @mock.patch("openai.OpenAI")
    def test_probe_all_dead_not_selected(self, mock_openai):
        """R3.3-31：probe_all 标记 dead 的模型不被选中，alive 的优先。"""
        calls = []
        behavior = {A: _StatusErr(500), B: _Resp("b-ok")}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        r = make_router(behavior, calls, clock=lambda: 1000.0)
        # 探活：a 抛 500 → dead；b 成功 → alive
        result = r.probe_all()
        self.assertFalse(result[A])
        self.assertTrue(result[B])

        # 之后 chat()：a dead 被排除，直接选 b
        calls.clear()
        out = r.chat("sys", "hi")
        self.assertEqual(out["text"], "b-ok")
        self.assertEqual(calls, [B])


if __name__ == "__main__":
    unittest.main()
