"""
LoveAI v2.0 - OCR服务
截屏识别（微信聊天区域/全屏/指定区域）、图片文字提取、RapidOCR懒加载。
服务层：不import PySide6，RapidOCR/PIL全部try/except懒加载降级。
"""
import os
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# 项目根目录（services/ 的上一级）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class OCRService:
    """OCR识别服务：截屏 + 图片文字提取，重依赖全部懒加载，缺库时优雅降级。"""

    def __init__(self, db=None, config=None, signals=None):
        """
        依赖注入初始化。
        :param db: Database实例（可选，预留写库用）
        :param config: ConfigService或dict（可选，控制截图保留等）
        :param signals: AppSignals实例（可选，预留事件通知）
        """
        self.db = db
        self.config = config
        self.signals = signals

        self.ocr = None                # RapidOCR实例，懒加载
        self.ocr_available = False     # OCR模型是否就绪
        self._pil_available = False    # PIL.ImageGrab是否可用（首次使用时探测）

        # 截图保存目录：项目根/data/screenshots/
        self.screenshots_dir = os.path.join(PROJECT_ROOT, "data", "screenshots")
        os.makedirs(self.screenshots_dir, exist_ok=True)
        logger.info("OCR服务初始化完成（RapidOCR/PIL均未加载，按需懒加载）")

    # ---------------- 配置辅助 ----------------
    def _get_config(self, key: str, default: Any = None) -> Any:
        """兼容ConfigService对象与普通dict的配置读取，读取失败返回default。"""
        if self.config is None:
            return default
        try:
            if hasattr(self.config, "get"):
                return self.config.get(key, default)
            if isinstance(self.config, dict):
                value: Any = self.config
                for k in key.split("."):
                    if isinstance(value, dict) and k in value:
                        value = value[k]
                    else:
                        return default
                return value
        except Exception as e:
            logger.debug(f"读取配置 {key} 失败: {e}")
        return default

    def _check_pil(self) -> bool:
        """探测PIL.ImageGrab是否可用，结果缓存。"""
        if self._pil_available:
            return True
        try:
            from PIL import ImageGrab  # noqa: F401
            self._pil_available = True
        except ImportError:
            self._pil_available = False
        return self._pil_available

    # ---------------- OCR懒加载 ----------------
    def _ensure_ocr(self) -> bool:
        """
        懒加载RapidOCR。优先 rapidocr_onnxruntime，缺失时尝试新版包名 rapidocr。
        缺库或初始化失败时返回False，不抛异常。
        """
        if self.ocr_available and self.ocr is not None:
            return True
        try:
            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError:
                # 新版包名
                from rapidocr import RapidOCR  # type: ignore
            self.ocr = RapidOCR()
            self.ocr_available = True
            logger.info("RapidOCR模型加载成功")
            return True
        except ImportError as e:
            logger.warning(f"RapidOCR未安装，OCR不可用: {e}")
        except Exception as e:
            logger.error(f"RapidOCR初始化失败: {e}")
        self.ocr = None
        self.ocr_available = False
        return False

    # ---------------- 截屏 ----------------
    def capture_screen(self, region: tuple = None) -> str | None:
        """
        截屏（全屏或指定区域）。
        :param region: (left, top, right, bottom)，None为全屏
        :return: 保存的图片路径，失败返回None
        """
        # 优先PIL.ImageGrab
        if self._check_pil():
            try:
                from PIL import ImageGrab
                if region is not None:
                    img = ImageGrab.grab(bbox=tuple(int(v) for v in region))
                else:
                    img = ImageGrab.grab()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                path = os.path.join(self.screenshots_dir, f"{timestamp}.png")
                img.save(path, "PNG")
                logger.debug(f"截图已保存: {path}")
                return path
            except Exception as e:
                logger.error(f"PIL截图失败，降级ctypes: {e}")

        # PIL不可用或失败时尝试ctypes兜底
        return self._capture_with_ctypes(region)

    def _capture_with_ctypes(self, region: tuple = None) -> str | None:
        """
        ctypes BitBlt兜底截屏（无PIL时），保存为BMP。
        最佳努力实现，任何异常都返回None不崩溃。
        """
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            gdi32 = ctypes.windll.gdi32

            # 设置句柄类型签名，避免64位下指针截断/OverflowError
            user32.GetDC.restype = wintypes.HDC
            user32.GetDC.argtypes = [wintypes.HWND]
            gdi32.CreateCompatibleDC.restype = wintypes.HDC
            gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
            gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
            gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
            gdi32.SelectObject.restype = ctypes.c_void_p
            gdi32.SelectObject.argtypes = [wintypes.HDC, ctypes.c_void_p]
            gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                     wintypes.DWORD]
            gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
            gdi32.DeleteDC.argtypes = [wintypes.HDC]
            user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
            gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT,
                                        wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                        wintypes.UINT]

            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)

            if region and len(region) == 4:
                left, top, right, bottom = (int(v) for v in region)
                w, h = right - left, bottom - top
            else:
                left, top, w, h = 0, 0, screen_w, screen_h

            if w <= 0 or h <= 0:
                logger.error("截屏区域无效")
                return None

            hdc_screen = user32.GetDC(0)
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            hbm = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
            old = gdi32.SelectObject(hdc_mem, hbm)

            SRCCOPY = 0x00CC0020
            gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, left, top, SRCCOPY)

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD),
                    ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD),
                ]

            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h  # 自上而下存储
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0  # BI_RGB

            img_size = w * h * 4
            buf = (ctypes.c_ubyte * img_size)()
            gdi32.GetDIBits(hdc_mem, hbm, 0, h, buf, ctypes.byref(bmi), 0)

            # 清理GDI资源
            gdi32.SelectObject(hdc_mem, old)
            gdi32.DeleteObject(hbm)
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(0, hdc_screen)

            # 写BMP文件（14字节文件头 + 40字节信息头 + BGRA像素数据）
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(self.screenshots_dir, f"{timestamp}.bmp")
            row_size = w * 4  # 32bpp天然4字节对齐
            pixel_data_size = row_size * h
            file_header_size = 14
            info_header_size = 40
            file_size = file_header_size + info_header_size + pixel_data_size

            with open(path, "wb") as f:
                f.write(b"BM")
                f.write(file_size.to_bytes(4, "little"))
                f.write((0).to_bytes(4, "little"))
                f.write((file_header_size + info_header_size).to_bytes(4, "little"))
                f.write(info_header_size.to_bytes(4, "little"))
                f.write(w.to_bytes(4, "little", signed=True))
                f.write((-h).to_bytes(4, "little", signed=True))
                f.write((1).to_bytes(2, "little"))
                f.write((32).to_bytes(2, "little"))
                f.write((0).to_bytes(4, "little"))
                f.write(pixel_data_size.to_bytes(4, "little"))
                f.write((2835).to_bytes(4, "little"))
                f.write((2835).to_bytes(4, "little"))
                f.write((0).to_bytes(4, "little"))
                f.write((0).to_bytes(4, "little"))
                f.write(bytes(buf))

            logger.debug(f"ctypes兜底截图已保存: {path}")
            return path
        except Exception as e:
            logger.error(f"ctypes兜底截屏失败: {e}")
            return None

    # ---------------- 微信区域 ----------------
    def capture_wechat_area(self, window_info: dict = None) -> str | None:
        """
        截取微信聊天区域。
        :param window_info: 含 left/top/right/bottom（来自WeChatDetector），None时全屏
        :return: 截图路径
        """
        if not window_info:
            return self.capture_screen(None)

        try:
            left = int(window_info.get("left", 0))
            top = int(window_info.get("top", 0))
            right = int(window_info.get("right", 0))
            bottom = int(window_info.get("bottom", 0))
        except (TypeError, ValueError, AttributeError) as e:
            logger.warning(f"窗口信息无效，降级全屏截图: {e}")
            return self.capture_screen(None)

        # 估算聊天区域：排除左侧联系人栏、顶部标题栏、底部输入框
        sidebar_w = 220   # 左侧导航/联系人栏宽度
        title_h = 60      # 顶部标题栏高度
        input_h = 180     # 底部输入框+工具栏高度
        chat_left = left + sidebar_w
        chat_top = top + title_h
        chat_right = right
        chat_bottom = bottom - input_h

        # 边界保护：区域过小时退回完整窗口
        if chat_right - chat_left < 100 or chat_bottom - chat_top < 100:
            logger.warning("估算聊天区域过小，使用完整窗口区域")
            chat_left, chat_top, chat_right, chat_bottom = left, top, right, bottom

        return self.capture_screen((chat_left, chat_top, chat_right, chat_bottom))

    # ---------------- 识别核心 ----------------
    def recognize_image(self, image_path: str) -> dict:
        """
        识别图片中的文字（核心方法）。
        :return: {"text": 全文, "lines": [{text, score, box}], "elapsed": 耗时}
                 OCR不可用时返回 {"text": "", "error": "OCR未就绪"}
        """
        if not image_path or not os.path.exists(image_path):
            return {"text": "", "lines": [], "elapsed": 0, "error": f"图片不存在: {image_path}"}

        if not self._ensure_ocr():
            return {"text": "", "lines": [], "elapsed": 0, "error": "OCR未就绪"}

        try:
            result, elapse = self.ocr(image_path)
        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return {"text": "", "lines": [], "elapsed": 0, "error": str(e)}

        lines = []
        texts = []
        if result:
            for item in result:
                # RapidOCR结果格式: [box, text, score]
                box = item[0] if len(item) > 0 else None
                text = item[1] if len(item) > 1 else ""
                score = item[2] if len(item) > 2 else 0.0
                lines.append({"text": text, "score": score, "box": box})
                texts.append(text)

        full_text = "\n".join(texts)
        return {"text": full_text, "lines": lines, "elapsed": elapse}

    def capture_and_recognize(self, region: tuple = None) -> dict:
        """截屏并识别（一步完成）。截图默认保留，是否清理由上层config控制。"""
        screenshot = self.capture_screen(region)
        if not screenshot:
            return {"text": "", "lines": [], "elapsed": 0,
                    "screenshot": None, "error": "截图失败"}
        result = self.recognize_image(screenshot)
        result["screenshot"] = screenshot
        return result

    def capture_wechat_and_recognize(self, window_info: dict = None) -> dict:
        """截取微信聊天区域并识别。"""
        screenshot = self.capture_wechat_area(window_info)
        if not screenshot:
            return {"text": "", "lines": [], "elapsed": 0,
                    "screenshot": None, "error": "微信区域截图失败"}
        result = self.recognize_image(screenshot)
        result["screenshot"] = screenshot
        return result

    def recognize_clipboard_image(self) -> dict:
        """识别剪贴板中的图片。无图片时返回error，不崩溃。"""
        if not self._check_pil():
            return {"text": "", "error": "PIL未安装，无法读取剪贴板"}

        try:
            from PIL import ImageGrab
            img = ImageGrab.grabclipboard()
        except Exception as e:
            logger.error(f"读取剪贴板失败: {e}")
            return {"text": "", "error": f"剪贴板读取失败: {e}"}

        if img is None:
            return {"text": "", "error": "剪贴板无图片"}

        # grabclipboard可能返回Image对象或文件路径列表，仅处理Image
        if not hasattr(img, "save"):
            return {"text": "", "error": "剪贴板内容不是图片"}

        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(self.screenshots_dir, f"clipboard_{timestamp}.png")
            img.save(path, "PNG")
        except Exception as e:
            logger.error(f"剪贴板图片保存失败: {e}")
            return {"text": "", "error": str(e)}

        return self.recognize_image(path)

    # ---------------- 状态 ----------------
    def get_status(self) -> dict:
        """返回OCR服务状态。"""
        return {
            "available": self.ocr_available,
            "pil_available": self._check_pil(),
            "screenshots_dir": self.screenshots_dir,
        }
