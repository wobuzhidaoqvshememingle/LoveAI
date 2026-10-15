# -*- coding: utf-8 -*-
"""R3.3-30 三层故障分级路由测试：classify_fault + FaultRouter.handle。

全部离线：mock 异常对象，不发真实请求。
"""
import errno
import os
import sys
import threading
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.fault_router import (
    FaultLevel, FaultRouter, classify_fault, DEFAULT_P2_THRESHOLD,
)


class _StatusErr(Exception):
    """模拟 openai v3 APIStatusError：带 .status_code。"""

    def __init__(self, code, body=""):
        super().__init__(f"HTTP {code}: {body}")
        self.status_code = code


class TestClassifyFault(unittest.TestCase):
    """三类异常 → 对应 FaultLevel。"""

    # ---- P0：网络瞬时错误 ----
    def test_timeout_is_p0(self):
        self.assertIs(classify_fault(TimeoutError("read timed out")),
                      FaultLevel.P0_IMMEDIATE_RECOVER)

    def test_connection_reset_is_p0(self):
        self.assertIs(classify_fault(ConnectionResetError("reset by peer")),
                      FaultLevel.P0_IMMEDIATE_RECOVER)

    def test_5xx_is_p0(self):
        for code in (500, 502, 503, 504):
            self.assertIs(classify_fault(_StatusErr(code)),
                          FaultLevel.P0_IMMEDIATE_RECOVER, f"status={code}")

    # ---- P1：认证/限流/模型问题 ----
    def test_401_403_is_p1(self):
        self.assertIs(classify_fault(_StatusErr(401)), FaultLevel.P1_DEGRADE_SWITCH)
        self.assertIs(classify_fault(_StatusErr(403)), FaultLevel.P1_DEGRADE_SWITCH)

    def test_429_is_p1(self):
        self.assertIs(classify_fault(_StatusErr(429)), FaultLevel.P1_DEGRADE_SWITCH)

    def test_404_model_not_found_is_p1(self):
        self.assertIs(classify_fault(_StatusErr(404, "model not found")),
                      FaultLevel.P1_DEGRADE_SWITCH)

    def test_model_crash_is_p1(self):
        err = type("ModelError", (Exception,), {})("onnx infer died")
        self.assertIs(classify_fault(err), FaultLevel.P1_DEGRADE_SWITCH)

    # ---- P2：人工介入 ----
    def test_disk_full_is_p2(self):
        err = OSError(errno.ENOSPC, "No space left on device")
        self.assertIs(classify_fault(err), FaultLevel.P2_MANUAL)

    def test_db_locked_is_p2(self):
        import sqlite3
        err = sqlite3.OperationalError("database is locked")
        self.assertIs(classify_fault(err), FaultLevel.P2_MANUAL)

    def test_missing_config_is_p2(self):
        err = RuntimeError("L3 cloud not configured")
        lvl = classify_fault(err, ctx={"missing_config_key": "llm.cloud.api_key"})
        self.assertIs(lvl, FaultLevel.P2_MANUAL)

    def test_consecutive_p1_streak_is_p2(self):
        err = _StatusErr(429)
        # 未达阈值：仍 P1
        lvl = classify_fault(err, ctx={"consecutive_p1_failures": 1})
        self.assertIs(lvl, FaultLevel.P1_DEGRADE_SWITCH)
        # 达到阈值：升级 P2
        lvl = classify_fault(err, ctx={
            "consecutive_p1_failures": DEFAULT_P2_THRESHOLD,
            "p2_threshold": DEFAULT_P2_THRESHOLD})
        self.assertIs(lvl, FaultLevel.P2_MANUAL)


class TestFaultRouterHandle(unittest.TestCase):
    """按级执行路径验证。"""

    def test_p0_retries_same_layer_then_recovers(self):
        """P0：同层立即重试一次；第二次成功 → recovered，共调 2 次。"""
        calls = []

        def action():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionResetError("reset")
            return "ok-after-retry"

        fr = FaultRouter()
        outcome, result = fr.handle(FaultLevel.P0_IMMEDIATE_RECOVER, action)
        self.assertEqual(outcome, "recovered")
        self.assertEqual(result, "ok-after-retry")
        self.assertEqual(len(calls), 2)  # 重试了同层一次

    def test_p0_still_fails_raises(self):
        """P0：两次都挂 → 原样上抛，不偷偷降级。"""
        def action():
            raise TimeoutError("boom")

        fr = FaultRouter()
        with self.assertRaises(TimeoutError):
            fr.handle(FaultLevel.P0_IMMEDIATE_RECOVER, action)

    def test_p1_degrades_to_next_layer(self):
        """P1：当前层失败 → 沿 fallback_chain 切下一层成功。"""
        calls = []

        def action():
            calls.append("L2")
            raise _StatusErr(401)

        def fb_l3():
            calls.append("L3")
            return "l3-answer"

        fr = FaultRouter()
        outcome, result = fr.handle(
            FaultLevel.P1_DEGRADE_SWITCH, action, fallback_chain=[fb_l3])
        self.assertEqual(outcome, "degraded")
        self.assertEqual(result, "l3-answer")
        self.assertEqual(calls, ["L2", "L3"])
        self.assertEqual(fr.consecutive_p1_failures, 0)  # 降级成功即清零

    def test_p1_streak_escalates_to_p2(self):
        """连续 N 次 P1 失败 → 升级 P2，notifier 被触发。"""
        alerts = []
        fr = FaultRouter(p2_threshold=DEFAULT_P2_THRESHOLD,
                         notifier=lambda r: alerts.append(r))

        def action():
            raise _StatusErr(429)

        # 前两次：降级链也全挂 → 上抛
        for _ in range(DEFAULT_P2_THRESHOLD - 1):
            with self.assertRaises(_StatusErr):
                fr.handle(FaultLevel.P1_DEGRADE_SWITCH, action, fallback_chain=[])
        self.assertEqual(alerts, [])  # 还没到 P2

        # 第三次：达到阈值 → P2 人工介入，不再 raise
        outcome, result = fr.handle(
            FaultLevel.P1_DEGRADE_SWITCH, action, fallback_chain=[])
        self.assertEqual(outcome, "manual_required")
        self.assertIsNone(result)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["level"], "P2")
        self.assertEqual(fr.p2_alerts, 1)

    def test_p2_notifies_and_never_retries(self):
        """P2：只诊断跑一次，直接通知人工，不自动重试。"""
        calls = []
        alerts = []

        def action():
            calls.append(1)
            raise OSError(errno.ENOSPC, "disk full")

        fr = FaultRouter(notifier=lambda r: alerts.append(r))
        outcome, result = fr.handle(FaultLevel.P2_MANUAL, action)
        self.assertEqual(outcome, "manual_required")
        self.assertEqual(len(calls), 1)       # 绝不自动重试
        self.assertEqual(len(alerts), 1)     # 发了人工介入通知
        self.assertIn("disk", alerts[0]["error"].lower())


if __name__ == "__main__":
    unittest.main()
