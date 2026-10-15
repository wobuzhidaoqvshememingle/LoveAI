# -*- coding: utf-8 -*-
"""R3.3-32 模型冷却表测试：CooldownTable + FreeModelRouter 选模型接线。

全部离线：clock 可控，不发真实请求。
"""
import os
import sys
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.free_model_router import (
    CooldownTable, FreeModelRouter,
    COOLDOWN_BASE_SEC, COOLDOWN_MAX_SEC,
)


class TestCooldownTable(unittest.TestCase):
    """CooldownTable 单元行为。"""

    def setUp(self):
        self.t = [1000.0]
        self.cd = CooldownTable(clock=lambda: self.t[0])

    def test_first_failure_cools_down_30s(self):
        """失败一次 → 冷却 30s。"""
        sec = self.cd.record_failure("m1", "HTTP 500")
        self.assertAlmostEqual(sec, COOLDOWN_BASE_SEC, places=3)
        self.assertAlmostEqual(self.cd.table["m1"]["unlock_at"] - self.t[0],
                               COOLDOWN_BASE_SEC, places=3)
        self.assertEqual(self.cd.table["m1"]["consecutive_failures"], 1)
        self.assertIn("500", self.cd.table["m1"]["last_error"])

    def test_cooled_down_within_window(self):
        """冷却期内 is_cooled_down=True；过期后 False。"""
        self.cd.record_failure("m1")
        # t=1010，冷却到 1030 → 仍在冷却
        self.assertTrue(self.cd.is_cooled_down("m1", now=1010.0))
        # t=1031 → 已恢复
        self.assertFalse(self.cd.is_cooled_down("m1", now=1031.0))
        # 从未失败的模型 → 不在冷却
        self.assertFalse(self.cd.is_cooled_down("never-fail", now=1010.0))

    def test_consecutive_failures_double_to_cap(self):
        """连续失败：30 → 60 → 120 → 240 → 300(封顶)。"""
        expected = [30.0, 60.0, 120.0, 240.0, 300.0, 300.0]
        for i, want in enumerate(expected):
            sec = self.cd.record_failure("m1", f"5xx#{i+1}")
            self.assertAlmostEqual(sec, want, places=3,
                                   msg=f"第{i+1}次失败冷却应为{want}s")
            self.assertEqual(self.cd.table["m1"]["consecutive_failures"], i + 1)
            # 时钟往前走 1s，保证每轮基于新时间戳计算
            self.t[0] += 1.0

    def test_success_clears_cooldown(self):
        """成功一次 → 整条记录清除。"""
        self.cd.record_failure("m1")
        self.assertIn("m1", self.cd.table)
        self.cd.record_success("m1")
        self.assertNotIn("m1", self.cd.table)
        self.assertFalse(self.cd.is_cooled_down("m1", now=self.t[0]))

    def test_snapshot_shape(self):
        """snapshot() 返回 UI 可直接渲染的 dict 列表。"""
        self.cd.record_failure("m-a", "err-a")
        self.cd.record_failure("m-b", "err-b")
        snap = self.cd.snapshot()
        names = {row["model"] for row in snap}
        self.assertEqual(names, {"m-a", "m-b"})
        for row in snap:
            self.assertGreater(row["remaining_sec"], 0)
            self.assertIn("unlock_at", row)
            self.assertIn("consecutive_failures", row)
            self.assertIn("last_error", row)


# ---- 路由器集成：冷却期跳过 / 全冷却返回 None ----
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
    def __init__(self, code, body=""):
        super().__init__(f"HTTP {code}: {body}")
        self.status_code = code


def make_fake_openai(behavior, calls):
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


def make_router(behavior, calls, clock=None):
    cfg = {
        "llm.free.openrouter_key": "sk-or-x",
        "llm.free.include_ollama": "false",
        "llm.free.auto_catalog": "false",
        "llm.free.openrouter_models": "model-a:free,model-b:free",
        "llm.free.nvidia_catalog_md": "false",
        # 零配置免费 provider（pollinations/llm7）默认开，会在 openrouter 全 5xx
        # 时被 mock 当作"成功"接住，破坏"全冷却→抛 RuntimeError"的断言。
        # 与 ollama/nvidia/local_scan/desktop 一样显式关闭，把候选隔离到 A/B。
        "llm.free.zero_config.enabled": "false",
    }
    r = FreeModelRouter(cfg, http_get=lambda *a, **k: "", clock=clock)
    r._sleep = mock.Mock()
    return r


A = "model-a:free"
B = "model-b:free"


class TestRouterCooldownWiring(unittest.TestCase):

    def setUp(self):
        for meth in ("_collect_local_scan_models", "_collect_desktop_models"):
            p = mock.patch.object(FreeModelRouter, meth, lambda self: [])
            p.start()
            self.addCleanup(p.stop)

    @mock.patch("openai.OpenAI")
    def test_cooled_model_skipped(self, mock_openai):
        """冷却期内的模型在选模型时被跳过。"""
        calls = []
        behavior = {A: _StatusErr(500), B: _Resp("b-ok")}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        t = [1000.0]
        r = make_router(behavior, calls, clock=lambda: t[0])
        r.chat("sys", "hi")  # a 5xx 进冷却，b 成功
        self.assertIn(A, r._cooldown)

        calls.clear()
        out = r.chat("sys", "hi")
        self.assertEqual(out["text"], "b-ok")
        self.assertEqual(calls, [B])  # a 在冷却，根本没被调

    @mock.patch("openai.OpenAI")
    def test_all_cooled_returns_none(self, mock_openai):
        """所有模型都冷却 → next_available_candidate 返回 None，让上层走兜底。"""
        calls = []
        behavior = {A: _StatusErr(500), B: _StatusErr(503)}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        t = [1000.0]
        r = make_router(behavior, calls, clock=lambda: t[0])
        with self.assertRaises(RuntimeError):  # a、b 都 5xx
            r.chat("sys", "hi")

        # 此时 a、b 都在冷却 → 无可选候选
        self.assertIsNone(r.next_available_candidate())
        self.assertEqual(r.ranked(), [])

        # 时钟走过冷却 → 恢复可选
        t[0] += 31.0
        self.assertIsNotNone(r.next_available_candidate())

    @mock.patch("openai.OpenAI")
    def test_429_uses_backoff_not_cooldown(self, mock_openai):
        """429 走 BACKOFF_RETRY_DELAYS 同轮重试，不进冷却表。"""
        calls = []
        a_tries = []

        def a_behavior():
            a_tries.append(1)
            if len(a_tries) < 3:
                return _StatusErr(429)
            return _Resp("a-ok")

        behavior = {A: a_behavior, B: _Resp("b-ok")}
        mock_openai.side_effect = make_fake_openai(behavior, calls)
        r = make_router(behavior, calls, clock=lambda: 1000.0)

        out = r.chat("sys", "hi")
        self.assertEqual(out["text"], "a-ok")
        # 429 重试后成功 → 不该进冷却表
        self.assertNotIn(A, r._cd.table)

    @mock.patch("openai.OpenAI")
    def test_success_clears_cooling(self, mock_openai):
        """模型恢复并成功后，冷却记录被清掉（路由层接线）。"""
        calls = []
        behavior = {A: _StatusErr(500), B: _StatusErr(503)}
        mock_openai.side_effect = make_fake_openai(behavior, calls)

        t = [1000.0]
        r = make_router(behavior, calls, clock=lambda: t[0])
        with self.assertRaises(RuntimeError):
            r.chat("sys", "hi")
        self.assertIn(A, r._cooldown)

        # 时钟走过冷却；a 恢复成功
        t[0] += 31.0
        behavior[A] = _Resp("a-back")
        calls.clear()
        out = r.chat("sys", "hi")
        self.assertEqual(out["text"], "a-back")
        self.assertNotIn(A, r._cooldown)
        self.assertEqual(r._consecutive_5xx[A], 0)


if __name__ == "__main__":
    unittest.main()
