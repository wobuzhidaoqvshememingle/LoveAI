"""
LoveAI v2.0 - 微信窗口检测器
用 ctypes 调用 Windows user32 API 检测微信主窗口位置，用于 overlay 对齐。
阶段一只做窗口检测，不做微信消息读写（pywechat127 在阶段二引入）。
"""
import ctypes
import logging
from ctypes import wintypes
from typing import Callable, Dict, Optional

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)

# user32 函数原型设置（64 位兼容）
_user32 = ctypes.windll.user32

_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND

_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL

_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND

_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL

_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int


class WeChatDetector(QObject):
    """微信窗口检测器。

    用法:
        detector = WeChatDetector()
        rect = detector.get_wechat_window()
        detector.start_monitoring(callback, interval_ms=500)
    """

    #: 微信主窗口可能的类名列表（按优先级）
    WECHAT_CLASS_NAMES = [
        "WeChatMainWndForPC",
        "Qt51514QWindowIcon",
        "WeChatMainWnd",
    ]

    def __init__(self, parent: QObject = None):
        super().__init__(parent)
        self._timer: Optional[QTimer] = None
        self._callback: Optional[Callable[[dict], None]] = None
        self._last_state: Optional[dict] = None

    # ==================================================================
    # 单次检测
    # ==================================================================

    def get_wechat_window(self) -> Optional[Dict]:
        """获取微信主窗口信息。

        Returns:
            dict: {hwnd, x, y, width, height, is_focused, is_visible, title}
            如果微信未运行返回 None。
        """
        hwnd = self._find_wechat_hwnd()
        if not hwnd:
            return None

        try:
            rect = wintypes.RECT()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None

            is_visible = bool(_user32.IsWindowVisible(hwnd))
            foreground = _user32.GetForegroundWindow()
            is_focused = (foreground == hwnd)

            # 窗口标题
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, buf, 256)

            return {
                "hwnd": int(hwnd),
                "x": rect.left,
                "y": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
                "is_focused": is_focused,
                "is_visible": is_visible,
                "title": buf.value,
            }
        except Exception as e:
            logger.warning(f"获取微信窗口信息失败: {e}")
            return None

    def _find_wechat_hwnd(self) -> wintypes.HWND:
        """按类名列表依次查找微信主窗口句柄。"""
        for class_name in self.WECHAT_CLASS_NAMES:
            try:
                hwnd = _user32.FindWindowW(class_name, None)
                if hwnd:
                    logger.debug(f"找到微信窗口: class={class_name}, hwnd={hwnd}")
                    return hwnd
            except Exception as e:
                logger.warning(f"FindWindowW({class_name}) 失败: {e}")
        return None

    @staticmethod
    def get_chat_area(wechat_rect: Dict) -> Dict:
        """根据微信窗口矩形估算聊天区域。

        左侧约 200px 是会话列表，底部约 80px 是输入框，
        中间区域是聊天消息列表。

        Args:
            wechat_rect: get_wechat_window() 返回的 dict

        Returns:
            dict: {x, y, width, height} 聊天消息区域
        """
        list_width = 200
        input_height = 80
        return {
            "x": wechat_rect["x"] + list_width,
            "y": wechat_rect["y"],
            "width": wechat_rect["width"] - list_width,
            "height": wechat_rect["height"] - input_height,
        }

    # ==================================================================
    # 定时监控
    # ==================================================================

    def start_monitoring(self, callback: Callable[[Dict], None], interval_ms: int = 500):
        """启动定时监控。状态变化时回调。

        Args:
            callback: 状态变化时的回调函数，参数为最新的窗口 dict（或 None）
            interval_ms: 检测间隔，默认 500ms
        """
        self.stop_monitoring()
        self._callback = callback
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        logger.info(f"微信窗口监控已启动，间隔 {interval_ms}ms")

    def stop_monitoring(self):
        """停止定时监控。"""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._callback = None
        self._last_state = None
        logger.info("微信窗口监控已停止")

    def _poll(self):
        """定时轮询：检测微信窗口状态，有变化则回调。"""
        try:
            current = self.get_wechat_window()
            if self._has_changed(self._last_state, current):
                self._last_state = current
                if self._callback is not None:
                    self._callback(current)
        except Exception as e:
            logger.warning(f"微信窗口轮询异常: {e}")

    @staticmethod
    def _has_changed(old: Optional[Dict], new: Optional[Dict]) -> bool:
        """比较两次检测结果是否发生变化。"""
        if old is None and new is None:
            return False
        if old is None or new is None:
            return True
        keys = ("x", "y", "width", "height", "is_focused", "is_visible")
        return any(old.get(k) != new.get(k) for k in keys)
