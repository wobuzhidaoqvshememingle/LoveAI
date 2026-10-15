"""
微信4.1+适配器 - 截图+OCR+坐标操作

适用于微信4.1及以上版本，UI完全自绘（MMUIRenderSubWindowHW），
UIAutomation找不到任何标准控件。改用以下策略：
1. Win32 API检测窗口位置和大小
2. 截图聊天区域
3. RapidOCR识别文字
4. 坐标点击切换聊天对象
5. 剪贴板+Ctrl+V发送消息

注意：此适配器不滑动微信界面，只读取当前可见区域，避免封号风险。
"""
import ctypes
import logging
import time
from typing import Any, Dict, List, Optional

from .base import BaseWeChatAdapter

logger = logging.getLogger(__name__)

# Win32 API
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# 微信4.x窗口类名
_WECHAT_4X_CLASSES = [
    "Qt51514QWindowIcon",
    "WeChatMainWndForPC",
]


class WeChatAdapter41(BaseWeChatAdapter):
    """微信4.1+适配器，基于截图+OCR+坐标。"""

    VERSION_RANGE = "4.1+"
    NAME = "screenshot-ocr"

    def __init__(self, signals=None, config=None):
        super().__init__(signals, config)
        self._hwnd = None
        self._window_rect = None
        self._ocr_engine = None
        self._last_screenshot = None
        self._last_messages_hash = set()

    def _init_ocr(self):
        """懒加载OCR引擎。"""
        if self._ocr_engine is not None:
            return True
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr_engine = RapidOCR()
            return True
        except Exception as e:
            logger.warning(f"OCR引擎初始化失败: {e}")
            return False

    def detect_version(self) -> Optional[str]:
        """检测微信版本（从exe文件属性）。"""
        try:
            # 先找微信进程
            hwnd = self._find_wechat_window()
            if not hwnd:
                return None
            # 获取进程ID
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            # 从进程获取exe路径
            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid.value
            )
            if handle:
                buf = ctypes.create_unicode_buffer(260)
                size = ctypes.c_ulong(260)
                ctypes.windll.psapi.GetModuleFileNameExW(handle, None, buf, size)
                ctypes.windll.kernel32.CloseHandle(handle)
                exe_path = buf.value
                # 从文件版本信息获取版本号
                try:
                    version_size = ctypes.windll.version.GetFileVersionInfoSizeW(exe_path, None)
                    if version_size:
                        res = ctypes.create_string_buffer(version_size)
                        ctypes.windll.version.GetFileVersionInfoW(exe_path, 0, version_size, res)
                        # 简化：返回4.1+标识
                        return "4.1+"
                except Exception:
                    pass
            return "4.1+"
        except Exception as e:
            logger.debug(f"41适配器检测版本失败: {e}")
            return None

    def _find_wechat_window(self) -> Optional[int]:
        """查找微信主窗口。"""
        for cls in _WECHAT_4X_CLASSES:
            hwnd = user32.FindWindowW(cls, None)
            if hwnd:
                return hwnd
        # 按标题找
        hwnd = user32.FindWindowW(None, "微信")
        return hwnd if hwnd else None

    def _get_window_rect(self) -> Optional[tuple]:
        """获取窗口位置和大小。"""
        if not self._hwnd:
            return None
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        return (rect.left, rect.top, rect.right, rect.bottom)

    def connect(self) -> bool:
        """连接微信（只检测窗口，不注入不Hook）。"""
        try:
            self._hwnd = self._find_wechat_window()
            if not self._hwnd:
                logger.warning("41适配器: 未找到微信窗口")
                return False
            self._window_rect = self._get_window_rect()
            self._connected = True
            logger.info(f"41适配器连接成功，窗口: {self._window_rect}")
            return True
        except Exception as e:
            logger.warning(f"41适配器连接失败: {e}")
            return False

    def get_current_chat_person(self) -> str:
        """从窗口标题获取当前聊天对象。"""
        if not self._hwnd:
            return self._current_person
        try:
            length = user32.GetWindowTextLengthW(self._hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(self._hwnd, buf, length + 1)
            title = buf.value
            # 微信4.x标题格式: "微信" 或 "昵称 - 微信"
            if "微信" in title and "-" in title:
                person = title.split("-")[0].strip()
                if person and person != "微信":
                    self._current_person = person
                    return person
            return self._current_person
        except Exception as e:
            logger.debug(f"41获取当前对象失败: {e}")
            return self._current_person

    def _screenshot_chat_area(self):
        """截图聊天区域。"""
        if not self._hwnd or not self._window_rect:
            return None
        try:
            left, top, right, bottom = self._window_rect
            width = right - left
            height = bottom - top
            # 使用PrintWindow截图（对自绘UI可能需要PW_RENDERFULLCONTENT）
            hwnd_dc = user32.GetWindowDC(self._hwnd)
            mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
            bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
            gdi32.SelectObject(mem_dc, bmp)
            # PW_RENDERFULLCONTENT = 0x00000002
            result = user32.PrintWindow(self._hwnd, mem_dc, 0x00000002)
            if result == 0:
                # 退化为普通PrintWindow
                user32.PrintWindow(self._hwnd, mem_dc, 0)
            # 转换为PIL Image
            try:
                from PIL import Image
                import struct as _struct
                bmp_info = ctypes.create_string_buffer(40)
                # BITMAPINFOHEADER：40, width, -height(top-down), planes=1, bitcount=32
                _hdr = _struct.pack('<IiiHH', 40, width, -height, 1, 32)
                ctypes.memmove(bmp_info, _hdr, len(_hdr))
                img_data = ctypes.create_string_buffer(width * height * 4)
                gdi32.GetDIBits(mem_dc, bmp, 0, height, img_data, bmp_info, 0)
                img = Image.frombuffer('RGB', (width, height), img_data.raw, 'raw', 'BGRX', 0, 1)
                self._last_screenshot = img
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem_dc)
                user32.ReleaseDC(self._hwnd, hwnd_dc)
                return img
            except ImportError:
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(mem_dc)
                user32.ReleaseDC(self._hwnd, hwnd_dc)
                return None
        except Exception as e:
            logger.debug(f"41截图失败: {e}")
            return None

    def get_new_messages(self) -> List[Dict[str, Any]]:
        """通过截图+OCR获取当前可见聊天消息。"""
        if not self._connected:
            return []
        try:
            # 刷新窗口位置
            self._window_rect = self._get_window_rect()
            # 截图
            img = self._screenshot_chat_area()
            if img is None:
                return []
            # OCR识别（RapidOCR 1.2.3 只接受 ndarray/路径/bytes，不接受 PIL）
            if not self._init_ocr():
                return []
            import numpy as _np
            result, _ = self._ocr_engine(_np.asarray(img))
            if not result:
                return []
            # 解析OCR结果为消息
            messages = []
            for item in result:
                text = item[1] if len(item) > 1 else ""
                confidence = item[2] if len(item) > 2 else 0
                if not text or confidence < 0.5:
                    continue
                # 根据文本位置判断方向（左侧=对方，右侧=自己）
                bbox = item[0] if len(item) > 0 else []
                direction = "received"  # 默认对方
                if bbox and len(bbox) >= 2:
                    x_center = (bbox[0][0] + bbox[2][0]) / 2 if len(bbox) >= 4 else 0
                    img_width = img.width
                    if x_center > img_width * 0.6:
                        direction = "sent"
                # 去重
                msg_hash = hash(f"{direction}:{text}")
                if msg_hash in self._last_messages_hash:
                    continue
                self._last_messages_hash.add(msg_hash)
                if len(self._last_messages_hash) > 200:
                    self._last_messages_hash = set(list(self._last_messages_hash)[-100:])
                messages.append({
                    "direction": direction,
                    "content": text,
                    "timestamp": time.time(),
                    "msg_type": 1,
                    "source": "ocr",
                })
            return messages
        except Exception as e:
            logger.debug(f"41获取消息失败: {e}")
            return []

    def send_message(self, text: str) -> bool:
        """通过剪贴板+Ctrl+V发送消息。"""
        if not self._connected:
            return False
        try:
            # 激活微信窗口
            user32.SetForegroundWindow(self._hwnd)
            time.sleep(0.1)
            # 复制到剪贴板
            import pyperclip
            pyperclip.copy(text)
            time.sleep(0.1)
            # 模拟Ctrl+V
            VK_CONTROL = 0x11
            VK_V = 0x56
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            user32.keybd_event(VK_V, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.1)
            # 模拟Enter发送
            VK_RETURN = 0x0D
            user32.keybd_event(VK_RETURN, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(VK_RETURN, 0, KEYEVENTF_KEYUP, 0)
            return True
        except Exception as e:
            logger.warning(f"41发送消息失败: {e}")
            return False

    def disconnect(self):
        """断开连接。"""
        self._connected = False
        self._hwnd = None
        self._ocr_engine = None
