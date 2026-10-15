# -*- coding: utf-8 -*-
"""
桌面端软件转 API 基类 (RPA 自动化)。

原理: 用 pyautogui/uiautomation 模拟鼠标键盘操作本机的免费 AI 桌面客户端,
把输入输出转成 OpenAI 兼容接口。

注意:
  - Windows only (依赖 uiautomation / pywinauto)
  - 懒加载: 没装 pyautogui/uiautomation 时, import 不崩, 实例化时报清晰错误
  - 失败重试 3 次, 30 秒超时
  - 每个桌面软件一个子类, 只填窗口类名/控件定位策略
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from abc import abstractmethod
from typing import Any, AsyncIterator, Optional

from app.config import ProviderConfig
from app.models import ChatCompletionRequest, ChatCompletionResponse, StreamChunk, Usage
from app.providers.base import BaseProvider, ProviderError

logger = logging.getLogger(__name__)


class DesktopProviderError(ProviderError):
    pass


class BaseDesktopProvider(BaseProvider):
    """桌面端 RPA 适配器基类。"""

    # 子类填这些元数据
    desktop_app_name: str = "unknown"          # 如 "Cherry Studio"
    desktop_exe_hint: str = ""                 # 如 "Cherry Studio.exe"
    install_paths: list[str] = []             # 常见安装路径
    retry_max: int = 3
    timeout_sec: float = 30.0

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._automation = None
        self._process: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------ #
    # 子类要实现的
    # ------------------------------------------------------------------ #
    @abstractmethod
    def _locate_chat_window(self) -> Any:
        """定位聊天主窗口, 返回窗口对象。"""

    @abstractmethod
    def _send_message(self, window: Any, text: str) -> None:
        """在输入框输入文本并发送。"""

    @abstractmethod
    def _read_reply(self, window: Any, since_ts: float) -> str:
        """从窗口读取最新回复。"""

    # ------------------------------------------------------------------ #
    # 懒加载桌面自动化库
    # ------------------------------------------------------------------ #
    def _ensure_automation(self) -> Any:
        if self._automation is not None:
            return self._automation
        try:
            import uiautomation as auto  # type: ignore
            self._automation = auto
            return auto
        except ImportError as e:
            raise DesktopProviderError(
                f"uiautomation 未安装, 无法驱动 {self.desktop_app_name}. "
                f"pip install uiautomation pyautogui",
                provider=self.name,
            ) from e

    # ------------------------------------------------------------------ #
    # 检测软件是否安装
    # ------------------------------------------------------------------ #
    def is_installed(self) -> bool:
        """检查常见路径下有没有这个桌面软件的 exe。"""
        import os
        for p in self.install_paths:
            if os.path.exists(p):
                return True
        # 开始菜单快捷方式粗检
        try:
            auto_dir = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")
            for root, _dirs, files in os.walk(auto_dir):
                for f in files:
                    if self.desktop_exe_hint.lower().replace(".exe", "") in f.lower():
                        return True
        except Exception:
            pass
        return False

    def launch(self) -> bool:
        """启动桌面软件。返回是否启动成功。"""
        try:
            for p in self.install_paths:
                import os
                if os.path.exists(p):
                    self._process = subprocess.Popen([p])
                    time.sleep(3)  # 等窗口起来
                    return True
        except Exception as e:
            logger.warning("launch %s failed: %s", self.desktop_app_name, e)
        return False

    # ------------------------------------------------------------------ #
    # 主接口: chat completion
    # ------------------------------------------------------------------ #
    async def chat_completion(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        # 把 messages 拼成一个用户输入文本
        user_text = "\n".join(
            m.content if isinstance(m.content, str) else str(m.content)
            for m in req.messages if m.role == "user"
        )
        last_err: Optional[Exception] = None
        for attempt in range(1, self.retry_max + 1):
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._do_chat, user_text),
                    timeout=self.timeout_sec,
                )
            except asyncio.TimeoutError as e:
                last_err = e
                logger.warning("%s chat timeout (attempt %d/%d)",
                               self.desktop_app_name, attempt, self.retry_max)
            except Exception as e:
                last_err = e
                logger.warning("%s chat error (attempt %d/%d): %s",
                               self.desktop_app_name, attempt, self.retry_max, e)
        raise DesktopProviderError(
            f"{self.desktop_app_name} RPA failed after {self.retry_max} retries: {last_err}",
            provider=self.name,
        )

    def _do_chat(self, user_text: str) -> ChatCompletionResponse:
        """同步执行 RPA 操作 (在线程里跑)。"""
        auto = self._ensure_automation()
        window = self._locate_chat_window()
        if window is None:
            # 尝试启动
            if not self.launch():
                raise DesktopProviderError(
                    f"{self.desktop_app_name} 未运行且启动失败, 请手动打开",
                    provider=self.name,
                )
            window = self._locate_chat_window()
            if window is None:
                raise DesktopProviderError(
                    f"找不到 {self.desktop_app_name} 聊天窗口", provider=self.name,
                )
        self._send_message(window, user_text)
        time.sleep(2)  # 等回复开始渲染
        reply = self._read_reply(window, time.time())
        return ChatCompletionResponse(
            model=req_model_name(user_text),
            choices=[{
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }],
            usage=Usage(prompt_tokens=len(user_text) // 4,
                        completion_tokens=len(reply) // 4,
                        total_tokens=(len(user_text) + len(reply)) // 4),
            provider=self.name,
        )

    async def stream_chat_completion(self, req: ChatCompletionRequest) -> AsyncIterator[StreamChunk]:
        """RPA 不好做真流式, 一次性返回整段。"""
        resp = await self.chat_completion(req)
        chunk = StreamChunk(model=resp.model, provider=self.name)
        chunk.choices = [{
            "index": 0,
            "delta": {"content": resp.choices[0].message.content, "role": "assistant"},
            "finish_reason": None,
        }]
        yield chunk


def req_model_name(fallback: str) -> str:
    return "desktop-rpa"
