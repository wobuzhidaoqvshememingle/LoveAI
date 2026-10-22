# -*- coding: utf-8 -*-
"""
R3.3-30 三层故障分级路由（FaultRouter）

把运行期异常按「能不能自愈」分成三级，与 L1/L2/L3 网关的降级链配合：

- P0_IMMEDIATE_RECOVER（瞬时故障）：timeout / connection reset / 5xx
  → 不换层，同层立即重试一次；第二次还挂才上抛交给上层决策。
- P1_DEGRADE_SWITCH（可降级故障）：401/403/429/模型名不存在(404)/模型本体崩溃
  → 当前层放弃，沿 fallback_chain 切下一层。
- P2_MANUAL（人工介入）：磁盘满 / 数据库锁死 / 关键配置缺失 / 连续 N 次 P1
  → 不再自动重试，发信号 + 写日志 + 回调 notifier（UI 标红由上层接）。

设计原则：
- classify_fault 是纯函数，不依赖任何外部状态，方便单测逐条覆盖；
- FaultRouter.handle 只做「按级执行」，具体怎么调 action_fn / fallback_chain
  由调用方传入，本模块不认识 openai / PySide6 等重依赖；
- 所有兜底分支都不吞错：自愈失败会把最后一个异常原样抛回，由网关层决定
  最终是再降级还是报错给用户。
"""
from __future__ import annotations

import errno
import logging
import os
import socket
from enum import Enum
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# P1 默认连续失败阈值：同一条降级链连续 N 次 P1 失败 → 升级 P2 人工介入
DEFAULT_P2_THRESHOLD = 3


class FaultLevel(Enum):
    """故障分级。value 仅用于日志/展示。"""

    P0_IMMEDIATE_RECOVER = "P0"    # 网络瞬时错误：同层立即重试一次
    P1_DEGRADE_SWITCH = "P1"       # 认证/限流/模型问题：降级切下一层
    P2_MANUAL = "P2"               # 磁盘/DB/配置/连续失败：人工介入


# --------------------------------------------------------------------------- #
# 工具：从异常里抠出 HTTP status / 错误文本
# --------------------------------------------------------------------------- #
def _http_status(exc: BaseException) -> Optional[int]:
    """openai v3 APIStatusError 带 .status_code；兼容 .response.status_code。"""
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = getattr(resp, "status_code", None)
    if status is None:
        return None
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _err_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# 故障分类纯函数
# --------------------------------------------------------------------------- #
def classify_fault(error: BaseException,
                   ctx: Optional[dict] = None) -> FaultLevel:
    """把异常分到 P0 / P1 / P2。

    ctx（可选）：
      - "consecutive_p1_failures": int，同层连续 P1 失败次数，达阈值即 P2；
      - "p2_threshold": int，覆盖默认阈值；
      - "missing_config_key": str，关键配置缺失时传入该 key 名 → P2。

    判定顺序：先看「硬故障 P2」（磁盘/DB锁/配置缺失/连续失败），
    再看「可降级 P1」（401/403/404/429/模型崩溃），最后剩下的网络/5xx 归 P0。
    """
    ctx = ctx or {}

    # ---- P2：人工介入硬故障 ----
    # 磁盘满（Windows: errno 28 ENOSPC / 112 EDQUOT；Linux: 28）
    if isinstance(error, OSError) and getattr(error, "errno", None) in (
            errno.ENOSPC, 112):
        return FaultLevel.P2_MANUAL
    # 数据库锁死（sqlite3.OperationalError: database is locked）
    etype_name = type(error).__name__
    if etype_name == "OperationalError" and "locked" in _err_text(error).lower():
        return FaultLevel.P2_MANUAL
    # 关键配置缺失（调用方显式标注 / KeyError 直接缺配置键）
    if ctx.get("missing_config_key"):
        return FaultLevel.P2_MANUAL
    if isinstance(error, KeyError) and str(error).strip("'\"") in (
            ctx.get("required_config_keys") or []):
        return FaultLevel.P2_MANUAL
    # 连续 N 次 P1 失败 → 升级人工介入
    # 容错：ctx 传入非数字字符串（脏配置/脏调用）时不崩溃，按默认 0 / 阈值处理
    streak_raw = ctx.get("consecutive_p1_failures", 0)
    if not streak_raw:
        streak = 0
    else:
        try:
            streak = int(streak_raw)
        except (TypeError, ValueError):
            streak = 0
    thr_raw = ctx.get("p2_threshold", DEFAULT_P2_THRESHOLD)
    if not thr_raw:
        threshold = DEFAULT_P2_THRESHOLD
    else:
        try:
            threshold = int(thr_raw)
        except (TypeError, ValueError):
            threshold = DEFAULT_P2_THRESHOLD
    if streak >= threshold:
        return FaultLevel.P2_MANUAL

    # ---- P1：可降级切层 ----
    status = _http_status(error)
    if status in (401, 403, 404, 429):
        return FaultLevel.P1_DEGRADE_SWITCH
    text = _err_text(error).lower()
    if any(k in text for k in ("model_not_found", "model not found",
                               "does not exist", "model not exist",
                               "context window exceeded", "context_length")):
        return FaultLevel.P1_DEGRADE_SWITCH
    # 模型本体崩溃（进程内推理崩了，换层不换模型通常无用，但仍属「切下一层」范畴）
    if etype_name in ("ModelError", "InferenceError", "OnnxRuntimeError",
                      "GGUFError"):
        return FaultLevel.P1_DEGRADE_SWITCH

    # ---- P0：网络瞬时错误（timeout / connection reset / 5xx）----
    if status is not None and 500 <= status < 600:
        return FaultLevel.P0_IMMEDIATE_RECOVER
    if isinstance(error, (TimeoutError, socket.timeout, ConnectionResetError,
                          ConnectionAbortedError, ConnectionRefusedError)):
        return FaultLevel.P0_IMMEDIATE_RECOVER
    # urllib3 / requests / openai 的传输层异常按类名兜底识别
    if any(k in etype_name for k in ("Timeout", "Connection", "Network",
                                     "502", "503", "504")):
        return FaultLevel.P0_IMMEDIATE_RECOVER

    # 未识别异常默认按 P0 处理（安全侧：先自愈一次，再上抛）
    return FaultLevel.P0_IMMEDIATE_RECOVER


# --------------------------------------------------------------------------- #
# FaultRouter：按级执行
# --------------------------------------------------------------------------- #
class FaultRouter:
    """按 FaultLevel 执行 action_fn / fallback_chain 的小状态机。

    参数：
      - p2_threshold: 连续 P1 失败几次后升级 P2（默认 3）；
      - notifier: P2 人工介入回调 callable(report_dict)，默认写 error 日志；
        UI 标红 / 发系统托盘信号由上层把这个回调换成自己的实现。
    """

    def __init__(self,
                 p2_threshold: int = DEFAULT_P2_THRESHOLD,
                 notifier: Optional[Callable[[dict], None]] = None) -> None:
        self.p2_threshold = p2_threshold
        self._notifier = notifier or self._default_notifier
        # 跨调用累计：同一条链连续 P1 失败次数（成功/降级成功即清零）
        self.consecutive_p1_failures = 0
        # P2 事件计数（供 UI 角标）
        self.p2_alerts = 0

    @staticmethod
    def _default_notifier(report: dict) -> None:
        """默认人工介入通知：只写日志；上层可注入 UI 标红/弹窗。"""
        logger.error("【需人工介入 P2】%s", report)

    def reset(self) -> None:
        """一次成功调用后清零 P1 连续失败计数。"""
        self.consecutive_p1_failures = 0

    # ------------------------------------------------------------------ #
    def handle(self,
               level: FaultLevel,
               action_fn: Callable[[], Any],
               fallback_chain: Optional[Iterable[Callable[[], Any]]] = None
               ) -> tuple[str, Any]:
        """按级执行，返回 (outcome, result)。

        outcome:
          - "recovered"      P0 同层重试成功；
          - "degraded"       P1 沿 fallback_chain 切层成功；
          - "manual_required" P2 已通知人工介入（不再自动重试）；
        自愈/降级都失败时把最后一个异常原样 raise，由上层收尾。
        """
        chain = list(fallback_chain or [])

        if level is FaultLevel.P0_IMMEDIATE_RECOVER:
            return self._handle_p0(action_fn)

        if level is FaultLevel.P1_DEGRADE_SWITCH:
            return self._handle_p1(action_fn, chain)

        # P2_MANUAL
        return self._handle_p2(action_fn, chain)

    # ------------------------------------------------------------------ #
    def _handle_p0(self, action_fn: Callable[[], Any]) -> tuple[str, Any]:
        """P0：先试一次；失败立即同层再试一次（不换层、不等待）。"""
        last_err: Optional[BaseException] = None
        for attempt in range(2):
            try:
                result = action_fn()
                if attempt > 0:
                    logger.warning("P0 瞬时故障第 %d 次重试后恢复", attempt + 1)
                self.reset()
                return "recovered", result
            except BaseException as e:  # 重试要抓 BaseException 里的同类瞬时错
                last_err = e
                logger.warning("P0 第 %d 次尝试失败: %s", attempt + 1, _err_text(e))
        assert last_err is not None
        raise last_err

    def _handle_p1(self,
                   action_fn: Callable[[], Any],
                   chain: list) -> tuple[str, Any]:
        """P1：当前层 action_fn 失败 → 依次试 fallback_chain 下一层。"""
        try:
            result = action_fn()
            self.reset()
            return "degraded", result
        except BaseException as e:
            last_err = e
            self.consecutive_p1_failures += 1
            logger.warning("P1 当前层失败(%d/%d)，尝试降级链: %s",
                           self.consecutive_p1_failures, self.p2_threshold,
                           _err_text(e))

            if self.consecutive_p1_failures >= self.p2_threshold:
                return self._escalate_p2(last_err, chain)

            for i, fb in enumerate(chain):
                try:
                    result = fb()
                    logger.warning("P1 降级链第 %d 层成功", i + 1)
                    self.reset()
                    return "degraded", result
                except BaseException as e2:  # 这层也挂，继续下一层
                    last_err = e2
                    logger.info("P1 降级链第 %d 层失败: %s", i + 1, _err_text(e2))

            # 降级链全挂
            if self.consecutive_p1_failures >= self.p2_threshold:
                return self._escalate_p2(last_err, chain)
            raise last_err

    def _handle_p2(self,
                    action_fn: Callable[[], Any],
                    chain: list) -> tuple[str, Any]:
        """P2：不自动重试，只通知人工介入。action_fn 只跑一次做诊断。"""
        try:
            result = action_fn()
            self.reset()
            return "recovered", result
        except BaseException as e:
            return self._escalate_p2(e, chain)

    # ------------------------------------------------------------------ #
    def _escalate_p2(self, error: BaseException,
                     chain: list) -> tuple[str, None]:
        """升级人工介入：计数 + 发通知（日志/UI 标红/信号由 notifier 接）。"""
        self.p2_alerts += 1
        report = {
            "level": FaultLevel.P2_MANUAL.value,
            "error_type": type(error).__name__,
            "error": _err_text(error),
            "consecutive_p1_failures": self.consecutive_p1_failures,
            "fallback_chain_depth": len(chain),
            "hint": "自动降级已用尽，请检查磁盘/数据库/关键配置后手动重启",
        }
        try:
            self._notifier(report)
        except Exception as e:  # 通知本身失败不能再炸启动链
            logger.error("P2 notifier 回调失败: %s", e)
        return "manual_required", None
