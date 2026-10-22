# -*- coding: utf-8 -*-
"""
P0 卡2：模型降级路由矩阵（ModelRoutingMatrix）

把现有 difficulty_router（easy/medium/hard）与 free_router_kernel 的 provider
命名拼成一张写死的 L0-L3 × 绿黄红黑 四色路由表，并叠加 NYSE 式三级熔断：

  - 单 provider 滑动窗口错误率 >5%   → 该 provider 降一档（DEGRADED）
  - 单 provider 滑动窗口错误率 >20%   → 切备用通道（BACKUP_ONLY）
  - 单 provider 滑动窗口错误率 >50%   → 拉黑 24h（BLOCKED，到期自动恢复）

四档 Tier：
  - L0_LOCAL_STAT     本地统计（无 LLM，对应 services/local_statistical_fallback）
  - L1_LOCAL_OLLAMA   本地小模型（ollama 等）
  - L2_FREE_CHANNEL   免费通道（openrouter/nvidia/pollinations/llm7，复用 free_router_kernel 命名）
  - L3_BIG_MODEL      大模型（付费强推理，兜底）

四色 TaskColor：
  - GREEN  简单闲聊/分类        → {L0, L1}
  - YELLOW 中等推理/摘要        → {L1, L2}
  - RED    复杂推理/对抗/侧写    → {L2, L3}
  - BLACK  关键决策/高风险       → {L3}

设计原则：
  1. 纯标准库，不依赖 free_model_router / difficulty_router 的运行时状态；
     与 free_router_kernel 的 provider 命名对齐，但不 import 它（避免循环依赖）；
  2. route() 是纯函数入口，未来 free_model_router 可直接调用；
  3. 时钟可注入（clock: callable -> float），便于单测模拟 24h 拉黑到期；
  4. BLACK 永远至少返回 L3（哪怕 L3 全 blocked，也 fail-open 返回 L3，因为高风险任务
     不能因为熔断而直接拒绝）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "Tier", "TaskColor", "ProviderState",
    "COLOR_TIER_MAP", "TIER_PROVIDERS",
    "SLIDING_WINDOW_SEC", "BLOCK_SECONDS",
    "THRESHOLD_DEGRADED", "THRESHOLD_BACKUP", "THRESHOLD_BLOCKED",
    "DEFAULT_STATE_PATH",
    "CircuitBreaker", "ModelRoutingMatrix",
    "route",  # 模块级便捷入口，使用全局默认 matrix
]

# 默认落盘路径（相对工程根：LoveAI-v2/data/.state/circuit_breaker.json）
# 不直接硬编码绝对路径——由调用方传入；这里只给一个默认值，便于开箱即用。
DEFAULT_STATE_PATH = os.path.join("data", ".state", "circuit_breaker.json")


# ====================================================================== #
# 枚举与写死路由表
# ====================================================================== #
class Tier(Enum):
    """四档模型层级。"""
    L0_LOCAL_STAT = "L0_LOCAL_STAT"        # 无 LLM，本地统计兜底
    L1_LOCAL_OLLAMA = "L1_LOCAL_OLLAMA"    # 本地小模型
    L2_FREE_CHANNEL = "L2_FREE_CHANNEL"    # 免费云端通道
    L3_BIG_MODEL = "L3_BIG_MODEL"         # 大模型（付费强推理）


class TaskColor(Enum):
    """四色任务分级。"""
    GREEN = "GREEN"    # 简单闲聊/分类
    YELLOW = "YELLOW"  # 中等推理/摘要
    RED = "RED"        # 复杂推理/对抗/侧写
    BLACK = "BLACK"    # 关键决策/高风险


class ProviderState(Enum):
    """熔断状态。"""
    OK = "OK"               # 正常
    DEGRADED = "DEGRADED"   # >5%：降一档（同档内优先其他健康 provider）
    BACKUP_ONLY = "BACKUP"  # >20%：备用通道（同档内最后兜底）
    BLOCKED = "BLOCKED"     # >50%：拉黑 24h


# 每色任务允许的有序候选 tier 列表（熔断时按序降级）
COLOR_TIER_MAP: Dict[TaskColor, List[Tier]] = {
    TaskColor.GREEN:  [Tier.L0_LOCAL_STAT, Tier.L1_LOCAL_OLLAMA],
    TaskColor.YELLOW: [Tier.L1_LOCAL_OLLAMA, Tier.L2_FREE_CHANNEL],
    TaskColor.RED:    [Tier.L2_FREE_CHANNEL, Tier.L3_BIG_MODEL],
    TaskColor.BLACK:  [Tier.L3_BIG_MODEL],
}

# 每 tier 下的有序 provider 列表（优先级从高到低）
# provider 命名与 free_router_kernel.PROVIDER_BASE_PRIORITY 对齐；
# L0 无 provider（None 表示纯统计，不发请求）；L3 给一个占位名，实际 provider 由上层决定。
TIER_PROVIDERS: Dict[Tier, List[Optional[str]]] = {
    Tier.L0_LOCAL_STAT:    [None],
    Tier.L1_LOCAL_OLLAMA: ["ollama", "local_bridge", "local_scan"],
    Tier.L2_FREE_CHANNEL:  ["openrouter", "nvidia", "pollinations", "llm7"],
    Tier.L3_BIG_MODEL:    ["big_model"],
}

# 熔断阈值（NYSE 式三档）
THRESHOLD_DEGRADED = 0.05    # >5% 降一档
THRESHOLD_BACKUP = 0.20      # >20% 切备用通道
THRESHOLD_BLOCKED = 0.50      # >50% 拉黑 24h

# 最小样本数：窗口内事件数低于此值时不触发任何降级（避免 1/1=100% 误判）
MIN_SAMPLES = 10

# 滑动窗口长度（秒）：在此窗口内统计错误率
SLIDING_WINDOW_SEC = 60.0
# 拉黑时长（秒）：24h
BLOCK_SECONDS = 86400.0


# ====================================================================== #
# 单 provider 熔断器
# ====================================================================== #
class CircuitBreaker:
    """按 provider 独立计数的滑动窗口熔断器。

    用法::

        cb = CircuitBreaker("openrouter")
        cb.record(success=False)   # 一次失败
        cb.record(success=True)    # 一次成功
        state = cb.state           # 当前状态
        cb.is_blocked(now)         # 当前是否被拉黑
    """

    def __init__(
        self,
        provider: str,
        window_sec: float = SLIDING_WINDOW_SEC,
        block_sec: float = BLOCK_SECONDS,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.provider = provider
        self.window_sec = float(window_sec)
        self.block_sec = float(block_sec)
        self._clock = clock or time.time
        # 事件队列：deque of (timestamp, is_success)
        self._events: Deque[Tuple[float, bool]] = deque()
        # 拉黑到期时间戳（None 表示未拉黑）
        self._blocked_until: Optional[float] = None

    # ------------------------------------------------------------------ #
    # 事件记录
    # ------------------------------------------------------------------ #
    def record(self, success: bool, now: Optional[float] = None) -> None:
        """记录一次请求结果。状态评估延迟到 state()/route() 调用时（懒求值）。"""
        t = float(now if now is not None else self._clock())
        self._events.append((t, bool(success)))
        # 顺手清理过期事件（避免队列无限增长）
        self._evict_old(t)
        # 如果刚拉黑到期，清除 blocked 标记
        if self._blocked_until is not None and t >= self._blocked_until:
            logger.info("provider=%s 拉黑到期，自动恢复", self.provider)
            self._blocked_until = None
            self._events.clear()  # 清空窗口，重新计数

    def _evict_old(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    # ------------------------------------------------------------------ #
    # 状态查询
    # ------------------------------------------------------------------ #
    def error_rate(self, now: Optional[float] = None) -> float:
        """返回滑动窗口内的错误率（0.0~1.0）。窗口内无请求返回 0.0。"""
        t = float(now if now is not None else self._clock())
        self._evict_old(t)
        total = len(self._events)
        if total == 0:
            return 0.0
        failures = sum(1 for _, ok in self._events if not ok)
        return failures / total

    def is_blocked(self, now: Optional[float] = None) -> bool:
        """是否处于 24h 拉黑中。"""
        t = float(now if now is not None else self._clock())
        # 到期自动清除
        if self._blocked_until is not None and t >= self._blocked_until:
            self._blocked_until = None
            self._events.clear()
        return self._blocked_until is not None

    def state(self, now: Optional[float] = None) -> ProviderState:
        """当前熔断状态。按 NYSE 三档判定。样本数 < MIN_SAMPLES 时一律 OK。"""
        t = float(now if now is not None else self._clock())
        if self.is_blocked(t):
            return ProviderState.BLOCKED
        rate = self.error_rate(t)
        # 样本量不足时不触发任何降级
        if len(self._events) < MIN_SAMPLES:
            return ProviderState.OK
        # 注意：必须先判 BLOCKED（最高档），再判 BACKUP，最后 DEGRADED
        if rate > THRESHOLD_BLOCKED:
            # 触发拉黑
            self._blocked_until = t + self.block_sec
            logger.warning(
                "provider=%s 错误率 %.2f%% > 50%%，拉黑 %.0fh",
                self.provider, rate * 100, self.block_sec / 3600,
            )
            return ProviderState.BLOCKED
        if rate > THRESHOLD_BACKUP:
            return ProviderState.BACKUP_ONLY
        if rate > THRESHOLD_DEGRADED:
            return ProviderState.DEGRADED
        return ProviderState.OK

    def snapshot(self) -> dict:
        """可观测性快照。"""
        now = self._clock()
        return {
            "provider": self.provider,
            "state": self.state(now).value,
            "error_rate": round(self.error_rate(now), 4),
            "blocked_until": self._blocked_until,
            "window_events": len(self._events),
        }

    # ------------------------------------------------------------------ #
    # 持久化（JSON 序列化/反序列化）
    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict:
        """序列化为可 JSON 化的 dict（只保留窗口期内有效事件 + blocked_until）。"""
        now = self._clock()
        self._evict_old(now)
        return {
            "provider": self.provider,
            "events": [[t, bool(ok)] for t, ok in self._events],
            "blocked_until": self._blocked_until,
        }

    @classmethod
    def from_dict(cls, data: dict,
                  window_sec: float = SLIDING_WINDOW_SEC,
                  block_sec: float = BLOCK_SECONDS,
                  clock: Optional[Callable[[], float]] = None) -> "CircuitBreaker":
        """从 dict 恢复；坏数据静默跳过，绝不抛错。"""
        provider = str(data.get("provider") or "")
        cb = cls(provider, window_sec=window_sec, block_sec=block_sec, clock=clock)
        events = data.get("events") or []
        if isinstance(events, list):
            for ev in events:
                try:
                    t = float(ev[0])
                    ok = bool(ev[1])
                    cb._events.append((t, ok))
                except (TypeError, ValueError, IndexError):
                    continue
        bu = data.get("blocked_until")
        try:
            cb._blocked_until = float(bu) if bu is not None else None
        except (TypeError, ValueError):
            cb._blocked_until = None
        return cb


# ====================================================================== #
# 路由矩阵主类
# ====================================================================== #
# 状态排序权重：OK 最优先，DEGRADED 次之，BACKUP_ONLY 再次，BLOCKED 被过滤掉
_STATE_RANK: Dict[ProviderState, int] = {
    ProviderState.OK: 0,
    ProviderState.DEGRADED: 1,
    ProviderState.BACKUP_ONLY: 2,
    ProviderState.BLOCKED: 3,
}


class ModelRoutingMatrix:
    """L0-L3 × 绿黄红黑四色路由表 + NYSE 三级熔断。

    用法::

        m = ModelRoutingMatrix()
        m.record("openrouter", success=False)  # 上层在请求后回写结果
        tier, provider = m.route(TaskColor.RED)
        # -> (Tier.L2_FREE_CHANNEL, "nvidia")  如果 openrouter 已被熔断
    """

    def __init__(
        self,
        clock: Optional[Callable[[], float]] = None,
        window_sec: float = SLIDING_WINDOW_SEC,
        block_sec: float = BLOCK_SECONDS,
        state_path: Optional[str] = DEFAULT_STATE_PATH,
    ) -> None:
        self._clock = clock or time.time
        self._window_sec = float(window_sec)
        self._block_sec = float(block_sec)
        # 持久化路径：默认走工程内 data/.state/circuit_breaker.json；
        # 传 None = 纯内存态（测试用）；传 "" 也视为纯内存。
        self._state_path = state_path or None
        self._lock = threading.RLock()
        # provider -> CircuitBreaker（懒初始化）
        self._breakers: Dict[str, CircuitBreaker] = {}
        # 启动时从磁盘恢复
        if self._state_path:
            self._load()

    # ------------------------------------------------------------------ #
    # 持久化：JSON 落盘 / 加载
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """从 state_path 加载熔断状态。坏文件静默回退到空表，绝不炸启动。"""
        if not self._state_path or not os.path.exists(self._state_path):
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            logger.warning("熔断状态文件读失败，回退空表: %s (%s)",
                           self._state_path, e)
            return
        if not isinstance(data, dict):
            return
        providers = data.get("providers")
        if not isinstance(providers, dict):
            return
        now = self._clock()
        for pname, pdata in providers.items():
            if not isinstance(pdata, dict):
                continue
            try:
                cb = CircuitBreaker.from_dict(
                    pdata,
                    window_sec=self._window_sec,
                    block_sec=self._block_sec,
                    clock=self._clock,
                )
                # 顺手清理过期事件 + 自动解除到期拉黑
                cb._evict_old(now)
                if cb._blocked_until is not None and now >= cb._blocked_until:
                    cb._blocked_until = None
                    cb._events.clear()
                self._breakers[pname] = cb
            except Exception as e:  # noqa: BLE001
                logger.warning("恢复 provider=%s 熔断状态失败: %s", pname, e)

    def _save(self) -> None:
        """把当前所有 breaker 状态写盘。写失败静默降级（不影响路由主流程）。"""
        if not self._state_path:
            return
        try:
            d = os.path.dirname(self._state_path)
            if d:
                os.makedirs(d, exist_ok=True)
            payload = {
                "providers": {p: cb.to_dict() for p, cb in self._breakers.items()},
            }
            # 原子写：先写临时文件再替换，避免半截 json
            tmp = self._state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, self._state_path)
        except OSError as e:
            logger.warning("熔断状态写盘失败（已静默降级内存态）: %s", e)

    # ------------------------------------------------------------------ #
    # 熔断记录
    # ------------------------------------------------------------------ #
    def record(self, provider: Optional[str], success: bool,
               now: Optional[float] = None) -> None:
        """上层请求完成后回写结果。provider 为 None（L0 统计层）不计数。"""
        if not provider:
            return
        with self._lock:
            cb = self._breakers.get(provider)
            if cb is None:
                cb = CircuitBreaker(
                    provider,
                    window_sec=self._window_sec,
                    block_sec=self._block_sec,
                    clock=self._clock,
                )
                self._breakers[provider] = cb
            cb.record(success, now=now)
            # 触发 BLOCKED 状态切换时立即落盘；普通事件按节流（每个 provider 一次
            # record 都落盘）——滑动窗口事件量小（60s 内通常 <100 条），写盘成本可接受
            self._save()

    def _breaker_state(self, provider: Optional[str],
                      now: float) -> ProviderState:
        if provider is None:
            return ProviderState.OK  # L0 统计层不熔断
        cb = self._breakers.get(provider)
        if cb is None:
            return ProviderState.OK
        prev_blocked = cb._blocked_until is not None
        state = cb.state(now)
        # 如果刚进入 BLOCKED，立即落盘（否则进程崩溃会丢拉黑状态）
        if not prev_blocked and state is ProviderState.BLOCKED:
            self._save()
        return state

    # ------------------------------------------------------------------ #
    # 路由主入口
    # ------------------------------------------------------------------ #
    def route(self, color: TaskColor) -> Tuple[Tier, Optional[str]]:
        """根据任务颜色返回 (tier, provider_pref)。

        规则：
          1. 按 COLOR_TIER_MAP[color] 的顺序遍历候选 tier；
          2. 每个 tier 下的 provider 按健康度排序（OK > DEGRADED > BACKUP_ONLY）；
          3. 跳过 BLOCKED 的 provider；
          4. 第一个有可用 provider 的 tier 立即返回；
          5. 如果所有 tier 都无可用 provider，BLACK 强制 fail-open 返回 L3；
             其他颜色返回最低 tier（L0 或 L1）的第一个 provider（含 None）。
        """
        now = self._clock()
        candidates = COLOR_TIER_MAP.get(color, COLOR_TIER_MAP[TaskColor.GREEN])

        last_tier = candidates[0]
        for tier in candidates:
            providers = TIER_PROVIDERS[tier]
            # 按健康度排序（BLOCKED 最后，但会被过滤）
            ranked = sorted(
                providers,
                key=lambda p: _STATE_RANK[self._breaker_state(p, now)],
            )
            for p in ranked:
                state = self._breaker_state(p, now)
                if state is not ProviderState.BLOCKED:
                    return (tier, p)
            last_tier = tier

        # 兜底：全 blocked
        if color is TaskColor.BLACK:
            # 高风险任务 fail-open：返回 L3 + 主 provider（上层可再做最后一次降级）
            return (Tier.L3_BIG_MODEL, TIER_PROVIDERS[Tier.L3_BIG_MODEL][0])
        # 其他颜色：返回第一个候选 tier 的第一个 provider（哪怕 blocked，也返回让上层决定）
        return (candidates[0], TIER_PROVIDERS[candidates[0]][0])

    # ------------------------------------------------------------------ #
    # 可观测性
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        """返回当前所有 provider 的熔断快照。"""
        now = self._clock()
        return {
            "providers": {
                p: cb.snapshot() for p, cb in self._breakers.items()
            },
            "window_sec": self._window_sec,
            "block_sec": self._block_sec,
        }


# ====================================================================== #
# 模块级便捷入口（未来 free_model_router 接线时可直接 import 使用）
# ====================================================================== #
_default_matrix: Optional[ModelRoutingMatrix] = None


def route(color: TaskColor) -> Tuple[Tier, Optional[str]]:
    """模块级纯函数入口：使用全局默认 matrix。

    注意：全局 matrix 是惰性单例；测试时请直接实例化 ModelRoutingMatrix 并
    注入自定义 clock，避免污染全局状态。
    """
    global _default_matrix
    if _default_matrix is None:
        _default_matrix = ModelRoutingMatrix()
    return _default_matrix.route(color)


def reset_default_matrix() -> None:
    """测试用：清空全局默认 matrix。"""
    global _default_matrix
    _default_matrix = None
