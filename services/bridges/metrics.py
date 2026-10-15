# -*- coding: utf-8 -*-
"""R3.3-77 桥接层指标收集器（每桥可观测）。

每座桥记录：
  - conversions : 成功完成的"语义转换 + 落地写入"次数；
  - failures    : 转换/写入失败次数（异常被桥基类隔离后计此）；
  - latency_ms  : 单次处理耗时累加与计数，对外给 avg / max。

另有总线级：被去噪（schema 不合法丢弃）的事件数、被仲裁压制的写入数。
全部内存态、纯 Python，无第三方依赖；reset() 供单测隔离。
"""
from __future__ import annotations

import threading
from collections import defaultdict


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # bridge_name -> dict
        self._bridges: dict[str, dict] = defaultdict(
            lambda: {"conversions": 0, "failures": 0,
                     "latency_sum_ms": 0.0, "latency_max_ms": 0.0,
                     "events_in": 0, "events_out": 0})
        self.denoised_events = 0     # 载荷 schema 不合格被丢弃
        self.arbitrated_writes = 0   # 仲裁器压制的低优先级写入

    # ------------------------------------------------------------------ #
    def record_event_in(self, bridge: str) -> None:
        with self._lock:
            self._bridges[bridge]["events_in"] += 1

    def record_event_out(self, bridge: str) -> None:
        with self._lock:
            self._bridges[bridge]["events_out"] += 1

    def record_conversion(self, bridge: str, latency_ms: float) -> None:
        with self._lock:
            b = self._bridges[bridge]
            b["conversions"] += 1
            b["latency_sum_ms"] += latency_ms
            if latency_ms > b["latency_max_ms"]:
                b["latency_max_ms"] = latency_ms

    def record_failure(self, bridge: str) -> None:
        with self._lock:
            self._bridges[bridge]["failures"] += 1

    def record_denoised(self, n: int = 1) -> None:
        with self._lock:
            self.denoised_events += n

    def record_arbitrated(self, n: int = 1) -> None:
        with self._lock:
            self.arbitrated_writes += n

    # ------------------------------------------------------------------ #
    def bridge_report(self, bridge: str) -> dict:
        with self._lock:
            b = dict(self._bridges.get(bridge) or {})
        conv = b.get("conversions", 0)
        fail = b.get("failures", 0)
        total = conv + fail
        b["failure_rate"] = round((fail / total), 4) if total else 0.0
        b["avg_latency_ms"] = round(
            b["latency_sum_ms"] / conv, 3) if conv else 0.0
        return b

    def summary(self) -> dict:
        """全桥指标汇总（便于排查'挂了没连通/连通没联动'）。"""
        with self._lock:
            names = sorted(self._bridges.keys())
            per = {n: self.bridge_report(n) for n in names}
            denoised = self.denoised_events
            arbitrated = self.arbitrated_writes
        total_in = sum(v["events_in"] for v in per.values())
        total_conv = sum(v["conversions"] for v in per.values())
        return {
            "bridges": per,
            "total_bridges": len(per),
            "total_events_in": total_in,
            "total_conversions": total_conv,
            "denoised_events": denoised,
            "arbitrated_writes": arbitrated,
        }

    def reset(self) -> None:
        with self._lock:
            self._bridges.clear()
            self.denoised_events = 0
            self.arbitrated_writes = 0
