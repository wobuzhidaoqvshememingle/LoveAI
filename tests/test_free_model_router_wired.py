# -*- coding: utf-8 -*-
"""
P0 卡2 接线测试：FreeModelRouter 注入 routing_matrix 后的行为。

覆盖：
  1. 不传 routing_matrix / 不传 task_color 时行为不变（零回归）
  2. task_color=GREEN 时，L2/L3 通道被剔除
  3. task_color=BLACK 时，L0/L1 通道被剔除
  4. provider 被推到 BLOCKED 后，ranked() 不再返回它
  5. _record_success / _record_5xx 同步回写 matrix
"""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.free_model_router import FreeModelRouter
from services.model_routing_matrix import (
    ModelRoutingMatrix, TaskColor, Tier, ProviderState,
)


# 与既有 test_free_model_router.py 一致：mock 掉本地扫描器，避免真连 Ollama
def _patch_local_collectors(testcase):
    for meth in ("_collect_local_scan_models", "_collect_desktop_models"):
        p = mock.patch.object(FreeModelRouter, meth, lambda self: [])
        p.start()
        testcase.addCleanup(p.stop)


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += float(dt)


def _make_router(matrix=None, config_overrides=None):
    """构造一个离线 FreeModelRouter：含 openrouter + ollama 候选。"""
    cfg = {
        "llm.free.openrouter_key": "sk-or-test",
        "llm.free.nvidia_key": "nvapi-test",
        "llm.free.auto_catalog": "false",
        "llm.free.zero_config.enabled": "false",
        "llm.free.auto_discover_bridge": "false",
        "llm.free.local_scan.enabled": "false",
        "llm.free.desktop_ai.enabled": "false",
    }
    if config_overrides:
        cfg.update(config_overrides)
    return FreeModelRouter(config=cfg, routing_matrix=matrix)


# ====================================================================== #
class TestWiringZeroRegression:
    def test_no_matrix_no_task_color_same_as_before(self):
        """不传 routing_matrix 且不传 task_color：行为与旧版一致。"""
        r = _make_router()
        # 不炸就行
        ranked = r.ranked()
        assert isinstance(ranked, list)

    def test_task_color_none_with_matrix_still_passes_through(self):
        """注入了 matrix 但 task_color=None：不过滤，行为不变。"""
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        r = _make_router(matrix=m)
        ranked_with = r.ranked()
        # 不炸就行
        assert isinstance(ranked_with, list)


# ====================================================================== #
class TestTaskColorFiltering:
    def test_green_excludes_l2(self):
        """GREEN 只允许 L0/L1，openrouter(L2) 应被剔除。"""
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        r = _make_router(matrix=m)
        ranked = r.ranked(task_color=TaskColor.GREEN)
        providers = {c.provider for c in ranked}
        # L2 通道不应出现
        assert "openrouter" not in providers, f"GREEN 不应含 openrouter: {providers}"
        assert "nvidia" not in providers, f"GREEN 不应含 nvidia: {providers}"
        # L1 ollama 可以出现
        assert "ollama" in providers

    def test_black_excludes_l0_l1(self):
        """BLACK 只允许 L3，ollama(L1)/openrouter(L2) 应被剔除。"""
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        r = _make_router(matrix=m)
        ranked = r.ranked(task_color=TaskColor.BLACK)
        providers = {c.provider for c in ranked}
        assert "ollama" not in providers
        assert "openrouter" not in providers

    def test_red_includes_l2_or_l3(self):
        """RED 允许 L2/L3，openrouter 可以出现。"""
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        r = _make_router(matrix=m)
        ranked = r.ranked(task_color=TaskColor.RED)
        providers = {c.provider for c in ranked}
        assert "openrouter" in providers


# ====================================================================== #
class TestBlockedProviderFiltered:
    def test_blocked_provider_not_in_ranked(self):
        """把 openrouter 推到 BLOCKED 后，ranked(task_color=RED) 不再返回它。"""
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        # openrouter 打满失败率 -> BLOCKED
        for _ in range(10):
            m.record("openrouter", success=False)
        # 懒求值：显式触发 state 评估
        m._breaker_state("openrouter", clock.now)
        assert m._breakers["openrouter"].is_blocked()

        r = _make_router(matrix=m)
        ranked = r.ranked(task_color=TaskColor.RED)
        providers = {c.provider for c in ranked}
        assert "openrouter" not in providers
        # nvidia 可能被 price_gate 过滤掉（离线无免费证据），不强制断言它在


# ====================================================================== #
class TestRecordWriteback:
    def test_record_5xx_updates_matrix(self):
        """_record_5xx 调用后 matrix 里 openrouter 错误率上升。"""
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        r = _make_router(matrix=m)
        # 取一个 openrouter 候选
        cands = [c for c in r.candidates() if c.provider == "openrouter"]
        assert cands, "应有 openrouter 候选"
        c = cands[0]

        # 调用 _record_5xx
        r._record_5xx(c, error=RuntimeError("500"))
        # matrix 里应有 openrouter 且记了一次失败
        assert "openrouter" in m._breakers
        assert m._breakers["openrouter"].error_rate() == 1.0

    def test_record_success_updates_matrix(self):
        """_record_success 调用后 matrix 里 provider 记了一次成功。"""
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        r = _make_router(matrix=m)
        cands = [c for c in r.candidates() if c.provider == "openrouter"]
        c = cands[0]

        r._record_success(c)
        assert "openrouter" in m._breakers
        # 1 次成功，错误率 0
        assert m._breakers["openrouter"].error_rate() == 0.0
