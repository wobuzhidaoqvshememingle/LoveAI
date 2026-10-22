# -*- coding: utf-8 -*-
"""
P0 卡2：model_routing_matrix 路由矩阵 + NYSE 三级熔断 单测。

覆盖：
  1. 每种颜色任务路由到允许的 tier 集合（不越界）
  2. GREEN 不会走到 L3，BLACK 不会掉到 L2 以下
  3. 错误率 5% / 20% / 50% 三档边界触发正确
  4. 拉黑 24h 后自动恢复（用可注入时钟，不真 sleep）
  5. difficulty_router.color_for() 映射正确
"""
from __future__ import annotations

import pytest

from services.model_routing_matrix import (
    Tier, TaskColor, ProviderState,
    ModelRoutingMatrix, CircuitBreaker,
    COLOR_TIER_MAP, TIER_PROVIDERS,
    THRESHOLD_DEGRADED, THRESHOLD_BACKUP, THRESHOLD_BLOCKED,
    SLIDING_WINDOW_SEC, BLOCK_SECONDS,
    route as module_route, reset_default_matrix,
)
from services.difficulty_router import DifficultyRouter, EASY, MEDIUM, HARD


# ====================================================================== #
# 可注入时钟：测试用假时钟，从 T0 开始推进
# ====================================================================== #
class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += float(dt)


# ====================================================================== #
# 1. 路由表不越界
# ====================================================================== #
class TestRoutingTableBounds:
    """每种颜色的 route() 结果必须落在 COLOR_TIER_MAP 允许的 tier 集合内。"""

    @pytest.mark.parametrize("color", list(TaskColor))
    def test_tier_within_allowed_set(self, color):
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        tier, _ = m.route(color)
        assert tier in COLOR_TIER_MAP[color], (
            f"{color.name} 路由到 {tier.name}，超出允许集合 "
            f"{[t.name for t in COLOR_TIER_MAP[color]]}"
        )

    def test_green_never_l3(self):
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        for _ in range(10):
            tier, _ = m.route(TaskColor.GREEN)
            assert tier is not Tier.L3_BIG_MODEL
            assert tier in (Tier.L0_LOCAL_STAT, Tier.L1_LOCAL_OLLAMA)

    def test_black_never_below_l3(self):
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        tier, provider = m.route(TaskColor.BLACK)
        assert tier is Tier.L3_BIG_MODEL
        assert provider == "big_model"

    def test_yellow_within_l1_l2(self):
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        tier, _ = m.route(TaskColor.YELLOW)
        assert tier in (Tier.L1_LOCAL_OLLAMA, Tier.L2_FREE_CHANNEL)

    def test_red_within_l2_l3(self):
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        tier, _ = m.route(TaskColor.RED)
        assert tier in (Tier.L2_FREE_CHANNEL, Tier.L3_BIG_MODEL)


# ====================================================================== #
# 2. 首选 tier 健康时优先返回
# ====================================================================== #
class TestPreferredTier:
    def test_green_prefers_l0(self):
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        tier, provider = m.route(TaskColor.GREEN)
        assert tier is Tier.L0_LOCAL_STAT
        assert provider is None  # L0 无 provider

    def test_yellow_prefers_l1(self):
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        tier, provider = m.route(TaskColor.YELLOW)
        assert tier is Tier.L1_LOCAL_OLLAMA
        assert provider == "ollama"

    def test_red_prefers_l2_openrouter(self):
        m = ModelRoutingMatrix(clock=FakeClock(), state_path=None)
        tier, provider = m.route(TaskColor.RED)
        assert tier is Tier.L2_FREE_CHANNEL
        assert provider == "openrouter"


# ====================================================================== #
# 3. NYSE 三档熔断边界
# ====================================================================== #
def _fill_window(cb: CircuitBreaker, total: int, failures: int) -> None:
    """在当前时钟点记录 total 次请求（failures 次失败），失败/成功交错排列，
    避免连续失败在样本数刚到 MIN_SAMPLES 时就误触发 BLOCKED。"""
    succ = total - failures
    # 交错：fail_idx 集合里均匀分布失败位置
    fail_positions = set(round(i * total / max(failures, 1)) for i in range(failures))
    for i in range(total):
        cb.record(success=i not in fail_positions)


class TestCircuitBreakerThresholds:
    def test_5pct_boundary_degraded(self):
        """错误率刚好 5%（不大于 5%）仍 OK；超过 5% 触发 DEGRADED。"""
        clock = FakeClock()
        cb = CircuitBreaker("openrouter", clock=clock)
        # 100 次里 5 次失败 = 5% -> 不触发（必须 >5%）
        _fill_window(cb, 100, 5)
        assert cb.error_rate() == 0.05
        assert cb.state() is ProviderState.OK

        # 再加 1 次失败（101 次里 6 次失败 ≈ 5.94%）-> 触发 DEGRADED
        cb.record(success=False)
        assert cb.state() is ProviderState.DEGRADED

    def test_20pct_boundary_backup(self):
        clock = FakeClock()
        cb = CircuitBreaker("nvidia", clock=clock)
        # 100 次里 20 次失败 = 20%：已 >5%，所以是 DEGRADED（不是 OK）
        _fill_window(cb, 100, 20)
        assert cb.error_rate() == 0.20
        assert cb.state() is ProviderState.DEGRADED

        # 再加 1 次失败（101 次里 21 次 ≈ 20.79%）-> 跨过 20% 阈值 -> BACKUP_ONLY
        cb.record(success=False)
        assert cb.state() is ProviderState.BACKUP_ONLY

    def test_50pct_boundary_blocked(self):
        clock = FakeClock()
        cb = CircuitBreaker("pollinations", clock=clock)
        # 100 次里 50 次失败 = 50%：已 >20%，所以是 BACKUP_ONLY（不是 OK）
        _fill_window(cb, 100, 50)
        assert cb.error_rate() == 0.50
        assert cb.state() is ProviderState.BACKUP_ONLY
        assert not cb.is_blocked()

        # 再加 1 次失败（101 次里 51 次 ≈ 50.5%）-> 跨过 50% -> BLOCKED
        cb.record(success=False)
        assert cb.state() is ProviderState.BLOCKED
        assert cb.is_blocked()

    def test_sliding_window_eviction(self):
        """旧事件滑出窗口后错误率回落。"""
        clock = FakeClock()
        cb = CircuitBreaker("llm7", window_sec=60.0, clock=clock)
        # 先打满 100% 失败
        for _ in range(10):
            cb.record(success=False)
        assert cb.state() is ProviderState.BLOCKED

        # 时钟推进 60s+，旧事件全部过期
        clock.advance(61.0)
        # 此时没有新事件，错误率为 0
        assert cb.error_rate() == 0.0
        # 但 BLOCKED 状态由 _blocked_until 控制，不会因窗口清空而解除
        # （要等 24h）
        assert cb.is_blocked()


# ====================================================================== #
# 4. 拉黑 24h 后自动恢复
# ====================================================================== #
class TestBlockRecovery:
    def test_blocked_recovers_after_24h(self):
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        # openrouter 打满失败率，触发拉黑
        for _ in range(10):
            m.record("openrouter", success=False)

        # RED 任务：openrouter 被拉黑，应降级到 nvidia
        tier, provider = m.route(TaskColor.RED)
        assert tier is Tier.L2_FREE_CHANNEL
        assert provider == "nvidia"

        # 推进 24h 之前：仍然 blocked
        clock.advance(BLOCK_SECONDS - 1.0)
        assert m._breakers["openrouter"].is_blocked()

        # 推进到刚好 24h：自动恢复
        clock.advance(1.5)
        assert not m._breakers["openrouter"].is_blocked()

        # 现在 RED 任务应回到 openrouter
        tier, provider = m.route(TaskColor.RED)
        assert tier is Tier.L2_FREE_CHANNEL
        assert provider == "openrouter"

    def test_black_fail_open_when_l3_blocked(self):
        """BLACK 任务即使 L3 provider 被拉黑也 fail-open 返回 L3。"""
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        # 把 big_model 打满失败率
        for _ in range(10):
            m.record("big_model", success=False)
        tier, provider = m.route(TaskColor.BLACK)
        assert tier is Tier.L3_BIG_MODEL
        assert provider == "big_model"


# ====================================================================== #
# 5. 熔断导致 tier 自动降级
# ====================================================================== #
class TestTierAutoDowngrade:
    def test_red_downgrades_l3_when_all_l2_blocked(self):
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        # 把 L2 所有 provider 打满失败
        for p in TIER_PROVIDERS[Tier.L2_FREE_CHANNEL]:
            for _ in range(10):
                m.record(p, success=False)
        # RED 任务：L2 全 blocked，自动降级到 L3
        tier, provider = m.route(TaskColor.RED)
        assert tier is Tier.L3_BIG_MODEL
        assert provider == "big_model"

    def test_yellow_downgrades_l2_when_all_l1_blocked(self):
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        # L1 全 blocked
        for p in TIER_PROVIDERS[Tier.L1_LOCAL_OLLAMA]:
            if p is None:
                continue
            for _ in range(10):
                m.record(p, success=False)
        tier, provider = m.route(TaskColor.YELLOW)
        assert tier is Tier.L2_FREE_CHANNEL
        assert provider == "openrouter"

    def test_degraded_provider_deprioritized_but_not_skipped(self):
        """DEGRADED 状态的 provider 仍可用，但同档内优先健康的。"""
        clock = FakeClock()
        m = ModelRoutingMatrix(clock=clock, state_path=None)
        # openrouter 错误率刚过 5%（DEGRADED）
        for _ in range(6):
            m.record("openrouter", success=False)
        for _ in range(94):
            m.record("openrouter", success=True)
        # RED 任务：openrouter 是 DEGRADED，但未 BLOCKED，仍可返回
        # 不过我们的排序逻辑会把 OK 的 nvidia 排在前面
        tier, provider = m.route(TaskColor.RED)
        assert tier is Tier.L2_FREE_CHANNEL
        # openrouter DEGRADED（rank=1），nvidia OK（rank=0）-> nvidia 优先
        assert provider == "nvidia"


# ====================================================================== #
# 6. difficulty_router.color_for() 映射
# ====================================================================== #
class TestDifficultyColorMapping:
    def test_easy_to_green(self):
        r = DifficultyRouter()
        assert r.color_for(EASY) == "GREEN"

    def test_medium_to_yellow(self):
        r = DifficultyRouter()
        assert r.color_for(MEDIUM) == "YELLOW"

    def test_hard_to_red(self):
        r = DifficultyRouter()
        assert r.color_for(HARD) == "RED"

    def test_color_of_text(self):
        r = DifficultyRouter()
        # "你好" 是 easy -> GREEN
        assert r.color_of("你好") == "GREEN"
        # 短文本 easy
        assert r.color_of("嗨") == "GREEN"
        # 长文本 hard -> RED
        assert r.color_of("侧写我的依恋类型并给出挽回策略") == "RED"

    def test_unknown_tier_defaults_yellow(self):
        r = DifficultyRouter()
        assert r.color_for("nonexistent") == "YELLOW"


# ====================================================================== #
# 7. 模块级入口
# ====================================================================== #
class TestModuleRoute:
    def test_module_route_returns_tuple(self):
        reset_default_matrix()
        tier, provider = module_route(TaskColor.GREEN)
        assert isinstance(tier, Tier)
        reset_default_matrix()


# ====================================================================== #
# 8. JSON 持久化（gap B）
# ====================================================================== #
import json as _json
import os as _os


class TestPersistence:
    def test_state_written_and_reloaded(self, tmp_path):
        """写盘 -> 新建实例同路径读出来 -> 状态一致。"""
        clock = FakeClock()
        state_file = str(tmp_path / "cb.json")
        m1 = ModelRoutingMatrix(clock=clock, state_path=state_file)
        # openrouter: 6 次失败 + 94 次成功 ≈ 6% -> DEGRADED（交错，6% 失败率）
        for i in range(100):
            m1.record("openrouter", success=(i % 100 >= 6))
        # nvidia: 10 次失败 -> 先触发 state 评估置 BLOCKED
        for _ in range(10):
            m1.record("nvidia", success=False)
        # 懒求值：显式触发 state 评估，让 _blocked_until 落盘
        m1._breaker_state("openrouter", clock.now)
        m1._breaker_state("nvidia", clock.now)

        # 确认文件已写
        assert _os.path.exists(state_file)
        with open(state_file, "r", encoding="utf-8") as f:
            data = _json.load(f)
        assert "openrouter" in data["providers"]
        assert "nvidia" in data["providers"]
        # nvidia 的 blocked_until 应已落盘
        assert data["providers"]["nvidia"]["blocked_until"] is not None

        # 新实例从同路径加载
        m2 = ModelRoutingMatrix(clock=clock, state_path=state_file)
        # openrouter 仍是 DEGRADED
        assert m2._breaker_state("openrouter", clock.now) is ProviderState.DEGRADED
        # nvidia 仍是 BLOCKED（blocked_until 从盘上恢复）
        assert m2._breaker_state("nvidia", clock.now) is ProviderState.BLOCKED
        assert m2._breakers["nvidia"].is_blocked()

    def test_bad_json_does_not_crash(self, tmp_path):
        """坏 json 文件不炸启动，回退空表。"""
        clock = FakeClock()
        state_file = tmp_path / "cb.json"
        state_file.write_text("{not valid json", encoding="utf-8")
        # 不应抛异常
        m = ModelRoutingMatrix(clock=clock, state_path=str(state_file))
        assert m._breakers == {}
        # route 仍正常
        tier, _ = m.route(TaskColor.GREEN)
        assert tier is Tier.L0_LOCAL_STAT

    def test_missing_file_ok(self, tmp_path):
        """文件不存在也正常（首次启动）。"""
        clock = FakeClock()
        state_file = str(tmp_path / "nonexistent" / "cb.json")
        m = ModelRoutingMatrix(clock=clock, state_path=state_file)
        assert m._breakers == {}
        m.record("openrouter", success=False)
        # 记录后文件自动创建
        assert _os.path.exists(state_file)

    def test_24h_block_persists_across_restart(self, tmp_path):
        """24h 拉黑状态重启后仍在（用 FakeClock 跳时间）。"""
        clock = FakeClock()
        state_file = str(tmp_path / "cb.json")
        m1 = ModelRoutingMatrix(clock=clock, state_path=state_file)
        # 把 openrouter 推到 BLOCKED
        for _ in range(10):
            m1.record("openrouter", success=False)
        # 懒求值：显式触发 state 评估
        m1._breaker_state("openrouter", clock.now)
        assert m1._breakers["openrouter"].is_blocked()

        # 模拟重启：新实例 + 同一时钟（时钟不跳）
        m2 = ModelRoutingMatrix(clock=clock, state_path=state_file)
        assert m2._breakers["openrouter"].is_blocked()

        # 跳 24h 后自动恢复
        clock.advance(BLOCK_SECONDS + 1.0)
        # 再次读（状态查询会自动解除到期拉黑）
        assert not m2._breakers["openrouter"].is_blocked()

