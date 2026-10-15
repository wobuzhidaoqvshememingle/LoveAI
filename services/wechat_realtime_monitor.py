# -*- coding: utf-8 -*-
"""
微信实时消息监听器

三种模式（默认轮询，最稳定）：
- polling: 每 N 秒重新读增量消息，对比上次最大时间戳
- watchdog: 监控 .db 文件 mtime，变化时触发读取
- sse: 连接 wechat-decrypt 的 SSE 端点（外部工具，可选）

后台线程运行，不阻塞 UI；新消息通过回调/事件总线发布。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class WeChatRealtimeMonitor:
    """实时消息监听器。

    Usage:
        monitor = WeChatRealtimeMonitor(reader, on_new_messages=lambda msgs: ...)
        monitor.start(mode="polling", interval=5)
        ...
        monitor.stop()
    """

    def __init__(self, reader=None,
                 on_new_messages: Optional[Callable[[List[Dict]], None]] = None,
                 event_bus=None) -> None:
        self.reader = reader
        self._on_new = on_new_messages
        self._bus = event_bus
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._mode: str = "polling"
        self._interval: float = 5.0
        self._last_ts: int = 0
        self._watch_files: List[str] = []
        self._sse_url: Optional[str] = None
        self._sse_client = None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------
    def start(self, mode: str = "polling", interval: float = 5.0,
              sse_url: Optional[str] = None,
              watch_files: Optional[List[str]] = None) -> None:
        """启动监听。

        Args:
            mode: "polling" / "watchdog" / "sse"
            interval: polling 模式间隔秒
            sse_url: sse 模式端点
            watch_files: watchdog 模式监控的 .db 路径列表
        """
        if self.is_running():
            logger.warning("监听器已在运行，先停止再重启")
            return
        self._mode = mode
        self._interval = interval
        self._sse_url = sse_url
        self._watch_files = watch_files or []
        self._stop.clear()

        if mode == "sse":
            target = self._sse_loop
        elif mode == "watchdog":
            target = self._watchdog_loop
        else:
            target = self._polling_loop

        self._thread = threading.Thread(target=target, name="wechat-monitor",
                                         daemon=True)
        self._thread.start()
        logger.info("微信实时监听已启动（mode=%s, interval=%.1fs）", mode, interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("微信实时监听已停止")

    # ------------------------------------------------------------------
    # 内部：分发
    # ------------------------------------------------------------------
    def _dispatch(self, messages: List[Dict]) -> None:
        if not messages:
            return
        if self._on_new is not None:
            try:
                self._on_new(messages)
            except Exception as e:  # noqa: BLE001
                logger.warning("新消息回调异常: %s", e)
        if self._bus is not None:
            try:
                self._bus.publish("wechat_new_message",
                                  {"count": len(messages), "messages": messages})
            except Exception as e:  # noqa: BLE001
                logger.warning("事件总线发布异常: %s", e)

    # ------------------------------------------------------------------
    # 模式1：轮询
    # ------------------------------------------------------------------
    def _polling_loop(self) -> None:
        # 先记录当前最大时间戳作为基线（避免启动时拉历史）
        if self.reader is not None:
            try:
                for msg in self.reader.read_messages():
                    self._last_ts = max(self._last_ts, msg.get("create_time", 0))
                    break  # 只读一条最新的
            except Exception as e:  # noqa: BLE001
                logger.debug("轮询基线初始化失败: %s", e)
        logger.info("轮询基线时间戳: %d", self._last_ts)

        while not self._stop.is_set():
            try:
                if self.reader is None:
                    time.sleep(self._interval)
                    continue
                new_msgs = []
                for msg in self.reader.read_messages(start_time=self._last_ts + 1):
                    new_msgs.append(msg)
                    self._last_ts = max(self._last_ts, msg.get("create_time", 0))
                if new_msgs:
                    self._dispatch(new_msgs)
            except Exception as e:  # noqa: BLE001
                logger.warning("轮询异常（将自动重试）: %s", e)
            self._stop.wait(self._interval)

    # ------------------------------------------------------------------
    # 模式2：watchdog 文件监控
    # ------------------------------------------------------------------
    def _watchdog_loop(self) -> None:
        last_mtime = {fp: os.path.getmtime(fp) if os.path.exists(fp) else 0
                      for fp in self._watch_files}
        while not self._stop.is_set():
            changed = []
            for fp in self._watch_files:
                try:
                    mt = os.path.getmtime(fp)
                    if mt != last_mtime.get(fp, 0):
                        changed.append(fp)
                        last_mtime[fp] = mt
                except OSError:
                    continue
            if changed and self.reader is not None:
                logger.info("检测到 .db 变化: %s", changed)
                try:
                    msgs = list(self.reader.read_messages(start_time=self._last_ts + 1))
                    for m in msgs:
                        self._last_ts = max(self._last_ts, m.get("create_time", 0))
                    self._dispatch(msgs)
                except Exception as e:  # noqa: BLE001
                    logger.warning("watchdog 读取异常: %s", e)
            self._stop.wait(self._interval)

    # ------------------------------------------------------------------
    # 模式3：SSE
    # ------------------------------------------------------------------
    def _sse_loop(self) -> None:
        """连接外部 wechat-decrypt 的 SSE 端点。"""
        if not self._sse_url:
            logger.warning("SSE 模式但未提供 url")
            return
        try:
            import requests  # type: ignore
        except ImportError:
            logger.error("requests 未安装，无法使用 SSE 模式")
            return
        while not self._stop.is_set():
            try:
                resp = requests.get(self._sse_url, stream=True, timeout=60)
                for line in resp.iter_lines():
                    if self._stop.is_set():
                        break
                    if not line:
                        continue
                    text = line.decode("utf-8", "ignore") if isinstance(line, bytes) else line
                    if text.startswith("data:"):
                        import json
                        try:
                            payload = json.loads(text[5:].strip())
                            self._dispatch([payload] if isinstance(payload, dict)
                                           else payload.get("messages", []))
                        except Exception:  # noqa: BLE001
                            continue
            except Exception as e:  # noqa: BLE001
                logger.warning("SSE 连接异常（5s 后重连）: %s", e)
                self._stop.wait(5.0)


__all__ = ["WeChatRealtimeMonitor"]
