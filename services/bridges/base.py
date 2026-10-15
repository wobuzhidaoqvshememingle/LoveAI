# -*- coding: utf-8 -*-
"""R3.3-77 桥基类（Bridge）。

每座桥 = 一组"语义转换 + 约束通道 + 双向反馈 + 仲裁钩子 + 指标"：
  - 订阅 trigger_events（来自总线）；
  - on_event(data) 拿到上游模块原始事件；
      translate_in(data)  -> 把 A 的输出翻译成 B 听得懂的输入（语义转换）；
      constrain(...)       -> 范围钳制 / 格式校验 / 去噪（约束通道，R3.3-71 口径）；
      _act(translated)     -> 调用 B 模块真实函数落地（真写/真读）；
      如需向下游再发事件，translate_out + bus.publish 触发下一跳（多跳）。
  - 软写入冲突时走 arbiter.claim 裁决（一致性）。
  - 全程计时记指标，单桥异常被隔离，不影响总线与其他桥。

服务层纯 Python：不 import PySide6。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class Bridge:
    """桥基类。子类需实现 name / subscribe_events / handle。"""

    name: str = "bridge"

    def __init__(self, bus, metrics=None, arbiter=None) -> None:
        self.bus = bus
        self.metrics = metrics or (bus.metrics if hasattr(bus, "metrics") else None)
        self.arbiter = arbiter
        self.enabled = True

    # 子类声明要订阅的事件类型
    def subscribe_events(self) -> list[str]:
        return []

    def setup(self) -> None:
        """向总线订阅本桥关心的事件。"""
        for et in self.subscribe_events():
            # 闭包绑定事件类型：dispatch 时注入 _event_type，供 translate_in 分流
            def _make(et_name: str):
                def _cb(data: dict):
                    enriched = dict(data or {})
                    enriched.setdefault("_event_type", et_name)
                    self._dispatch(enriched)
                return _cb
            self.bus.subscribe(et, _make(et_name=et), consumer=self.name)

    # ------------------------------------------------------------------ #
    # 语义转换（子类覆盖）：上游事件 -> 本桥内部动作载荷
    # ------------------------------------------------------------------ #
    def translate_in(self, data: dict) -> Any | None:
        return data

    # 约束通道（子类可扩展）：默认对 dict 做数值范围钳制钩子
    def constrain(self, translated: Any) -> Any:
        return translated

    # 子类核心：执行真实落地（调模块函数），返回 True 表示成功转换一次
    def handle(self, translated: Any, raw: dict) -> bool:  # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    def _dispatch(self, data: dict) -> None:
        if not self.enabled:
            return
        if self.metrics is not None:
            self.metrics.record_event_in(self.name)
        t0 = time.perf_counter()
        try:
            translated = self.translate_in(data)
            if translated is None:
                return  # 语义上无需联动（去噪）
            translated = self.constrain(translated)
            ok = self.handle(translated, data)
            if ok:
                dt_ms = (time.perf_counter() - t0) * 1000.0
                if self.metrics is not None:
                    self.metrics.record_conversion(self.name, dt_ms)
        except Exception as e:  # 单桥隔离：不影响总线与其他桥
            if self.metrics is not None:
                self.metrics.record_failure(self.name)
            logger.warning("桥 %s 处理事件失败: %s", self.name, e)

    # ------------------------------------------------------------------ #
    def claim(self, target: str, key: str, value, priority: int,
              meta: dict | None = None) -> dict:
        """软写入冲突仲裁（无 arbiter 时直通，不阻塞业务）。"""
        if self.arbiter is None:
            return {"target": target, "key": key, "value": value,
                    "winner_bridge": self.name, "is_winner": True,
                    "contenders": [{"bridge": self.name, "value": value}]}
        return self.arbiter.claim(target, key, value, priority,
                                  source_bridge=self.name, meta=meta)
