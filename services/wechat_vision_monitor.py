# -*- coding: utf-8 -*-
"""视觉监控+省token5层策略（本地OCR/L1预处理/增量/批量/本地规则优先）。"""
from __future__ import annotations
import hashlib, logging, threading, time
from collections import OrderedDict

logger = logging.getLogger(__name__)

_SIMPLE_PATTERNS = {"嗯", "哦", "好的", "在", "哈哈", "好"}


def _find_wechat_window() -> tuple[int, int, int, int] | None:
    """ctypes FindWindow + GetWindowRect 获取微信窗口位置大小 (x, y, w, h)。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "微信")
        if not hwnd:
            hwnd = user32.FindWindowW(None, "Weixin")
        if not hwnd:
            return None

        class _R(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        r = _R()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception as e:
        logger.debug("FindWindow失败: %s", e)
        return None


class WechatVisionMonitor:
    def __init__(self) -> None:
        self._running = False
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = 500
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start_monitoring(self, interval_seconds: int = 30) -> None:
        """后台线程定时截图→OCR→预处理→增量分析。"""
        self._stop_event.clear()
        self._running = True

        def _loop():
            while not self._stop_event.is_set():
                try:
                    img = self.capture_wechat_window()
                    if img:
                        txt = self.ocr_image(img)
                        if txt:
                            self.incremental_analysis([txt])
                except Exception as e:
                    logger.debug("monitor循环异常: %s", e)
                self._stop_event.wait(interval_seconds)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop_monitoring(self) -> None:
        self._running = False
        self._stop_event.set()

    def is_running(self) -> bool:
        return self._running

    def capture_wechat_window(self) -> bytes | None:
        """截取微信聊天区域（排除侧栏/标题/输入框），返回PNG bytes。"""
        geo = _find_wechat_window()
        if not geo:
            return None
        x, y, w, h = geo
        # 估算聊天区域：左侧侧栏 ~20%，顶部标题 40px，底部输入框 100px
        chat_left = x + int(w * 0.2)
        chat_top = y + 40
        chat_w = int(w * 0.8)
        chat_h = max(1, h - 40 - 100)
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab(bbox=(chat_left, chat_top,
                                       chat_left + chat_w, chat_top + chat_h))
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception as e:
            logger.debug("PIL截图失败: %s", e)
            return None

    def ocr_image(self, image: bytes) -> str:
        """调用项目已有 services/ocr_service.py（RapidOCR），不消耗云端 token。"""
        try:
            from services.ocr_service import ocr_image as _rapid_ocr
            return _rapid_ocr(image) or ""
        except Exception:
            return ""

    def local_rule_first(self, message: str) -> dict | None:
        if not message:
            return None
        if message.strip() in _SIMPLE_PATTERNS:
            return {"scene": "simple", "reply_hint": "简单回应即可", "need_cloud": False}
        return None

    def preprocess_with_l1(self, ocr_result: str) -> dict:
        return {"keywords": ocr_result.split()[:5], "complexity": "medium",
                "summary": ocr_result[:50]}

    def dedupe_key(self, content: str) -> str:
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def incremental_analysis(self, new_messages: list) -> list:
        out = []
        for m in new_messages:
            k = self.dedupe_key(str(m))
            if k in self._cache:
                continue
            self._cache[k] = True
            out.append(m)
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return out

    def batch_analysis(self, messages: list, batch_size: int = 10) -> list:
        return [messages[i:i+batch_size] for i in range(0, len(messages), batch_size)]

    def availability_check(self) -> dict:
        geo = _find_wechat_window()
        if geo:
            return {"available": True, "reason": "微信窗口已找到"}
        return {"available": False, "reason": "未找到微信窗口，请确保微信已打开"}

    def import_chats(self) -> list:
        img = self.capture_wechat_window()
        if not img:
            return []
        txt = self.ocr_image(img)
        return [{"type": "ocr", "text": txt}] if txt else []


class TokenSaver:
    LAYERS = ["本地OCR", "L1预处理", "增量分析", "批量处理", "本地规则优先"]

    def estimate_savings(self, raw_chars: int) -> dict:
        return {"raw_chars": raw_chars, "estimated_saved": int(raw_chars * 0.75),
                "ratio": "75%", "layers": self.LAYERS}


# ===== B类扩展：视觉读取其他方式 =====
class WechatVisionExtra:
    def availability_check(self) -> dict:
        return {"available": False, "reason": "需要UI Automation或截图权限"}
    def record_screen(self, seconds=30): return b""
    def read_ui_automation_tree(self): return {}
    def read_narrator_text(self): return ""
    def grab_notifications(self): return []
    def read_cef_debug(self, port=9222): return {}
    def magnify_and_ocr(self, region): return ""
    def import_chats(self) -> list:
        return self.grab_notifications()
