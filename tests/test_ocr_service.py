# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - OCRService 单元测试

覆盖：
  - __init__ 不崩溃 / get_status 默认 available=False
  - _ensure_ocr 缺 RapidOCR 返回 False
  - capture_screen（ctypes 兜底或降级，不崩溃）
  - recognize_image OCR 不可用返回 error
  - capture_and_recognize / capture_wechat_area / recognize_clipboard_image 降级
  - mock OCR 时 recognize_image 解析 lines
  - 微信区域估算 / 截图目录创建
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import services.ocr_service as ocr_mod  # noqa: E402
from services.ocr_service import OCRService  # noqa: E402


class TestOCRService(unittest.TestCase):
    """OCRService 测试类（把 PROJECT_ROOT 指向临时目录，避免污染真实 data/）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_ocr_")
        # 重定向模块级 PROJECT_ROOT，使截图目录落在临时目录
        self._proj_patch = patch.object(ocr_mod, "PROJECT_ROOT", self.tmpdir)
        self._proj_patch.start()
        self.svc = OCRService(db=None, config=None, signals=None)

    def tearDown(self) -> None:
        self._proj_patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化 / 状态
    # ------------------------------------------------------------------ #
    def test_init_no_crash(self) -> None:
        """__init__ 不应崩溃，OCR 默认未加载。"""
        self.assertIsNone(self.svc.ocr)
        self.assertFalse(self.svc.ocr_available)
        self.assertTrue(self.svc.screenshots_dir.startswith(self.tmpdir))

    def test_screenshots_dir_created(self) -> None:
        """截图目录应自动创建。"""
        self.assertTrue(os.path.isdir(self.svc.screenshots_dir))

    def test_get_status_unavailable(self) -> None:
        """get_status 默认 available=False。"""
        status = self.svc.get_status()
        self.assertFalse(status["available"])
        self.assertIn("screenshots_dir", status)

    # ------------------------------------------------------------------ #
    # OCR 懒加载
    # ------------------------------------------------------------------ #
    def test_ensure_ocr_without_lib(self) -> None:
        """屏蔽 RapidOCR 导入时 _ensure_ocr 返回 False。"""
        with patch.dict(sys.modules, {"rapidocr_onnxruntime": None,
                                     "rapidocr": None}):
            result = self.svc._ensure_ocr()
        self.assertFalse(result)
        self.assertFalse(self.svc.ocr_available)

    # ------------------------------------------------------------------ #
    # 截屏
    # ------------------------------------------------------------------ #
    def test_capture_screen_does_not_crash(self) -> None:
        """capture_screen 在 PIL 缺失时走 ctypes 兜底，任何结果都不应崩溃。"""
        result = self.svc.capture_screen(None)
        # 成功则返回路径，失败（无显示环境）返回 None，二者皆可
        self.assertTrue(result is None or isinstance(result, str))

    def test_capture_screen_region_invalid(self) -> None:
        """无效区域（宽高<=0）ctypes 兜底应返回 None 不崩溃。"""
        result = self.svc._capture_with_ctypes((100, 100, 100, 50))  # 宽=0
        self.assertIsNone(result)

    # ------------------------------------------------------------------ #
    # 微信区域
    # ------------------------------------------------------------------ #
    def test_capture_wechat_area_no_window(self) -> None:
        """window_info=None 时应全屏截图。"""
        with patch.object(self.svc, "capture_screen", return_value="full.png") as m:
            path = self.svc.capture_wechat_area(None)
        self.assertEqual(path, "full.png")
        m.assert_called_once_with(None)

    def test_capture_wechat_area_estimates_chat_region(self) -> None:
        """有窗口信息时应估算聊天区域（扣除侧栏/标题栏/输入框）。"""
        win = {"left": 0, "top": 0, "right": 800, "bottom": 900}
        with patch.object(self.svc, "capture_screen", return_value="chat.png") as m:
            path = self.svc.capture_wechat_area(win)
        self.assertEqual(path, "chat.png")
        # chat_left=0+220, chat_top=0+60, right=800, bottom=900-180=720
        m.assert_called_once_with((220, 60, 800, 720))

    def test_capture_wechat_area_small_falls_back(self) -> None:
        """估算区域过小时回退到完整窗口区域。"""
        win = {"left": 0, "top": 0, "right": 50, "bottom": 50}
        with patch.object(self.svc, "capture_screen", return_value="win.png") as m:
            self.svc.capture_wechat_area(win)
        m.assert_called_once_with((0, 0, 50, 50))

    def test_capture_wechat_area_bad_window_info(self) -> None:
        """window_info 字段非法时降级全屏。"""
        with patch.object(self.svc, "capture_screen", return_value="full.png") as m:
            self.svc.capture_wechat_area({"left": "abc"})
        m.assert_called_once_with(None)

    # ------------------------------------------------------------------ #
    # 识别
    # ------------------------------------------------------------------ #
    def test_recognize_image_missing_file(self) -> None:
        """图片不存在时返回 error。"""
        result = self.svc.recognize_image(os.path.join(self.tmpdir, "x.png"))
        self.assertIn("error", result)
        self.assertEqual(result["text"], "")

    def test_recognize_image_ocr_unavailable(self) -> None:
        """图片存在但 OCR 引擎不可用时返回 OCR 未就绪。"""
        img = os.path.join(self.tmpdir, "img.png")
        with open(img, "wb") as f:
            f.write(b"not a real png but path exists")
        with patch.object(self.svc, "_ensure_ocr", return_value=False):
            result = self.svc.recognize_image(img)
        self.assertEqual(result["text"], "")
        self.assertIn("OCR未就绪", result["error"])

    def test_capture_and_recognize_degrades_on_capture_fail(self) -> None:
        """截图失败时 capture_and_recognize 返回 error。"""
        with patch.object(self.svc, "capture_screen", return_value=None):
            result = self.svc.capture_and_recognize()
        self.assertIn("error", result)
        self.assertIsNone(result["screenshot"])

    def test_recognize_clipboard_no_pil(self) -> None:
        """PIL 缺失时剪贴板识别返回 error 不崩溃。"""
        self.svc._pil_available = False
        result = self.svc.recognize_clipboard_image()
        self.assertIn("error", result)

    def test_mock_ocr_parses_lines(self) -> None:
        """mock OCR 引擎时应把 [box, text, score] 解析为 lines 并拼接全文。"""
        img = os.path.join(self.tmpdir, "ok.png")
        with open(img, "wb") as f:
            f.write(b"x")
        fake_ocr = MagicMock()
        fake_ocr.return_value = (
            [[[[0, 0], [1, 0], [1, 1], [0, 1]], "你好", 0.99],
             [[[0, 2], [1, 2], [1, 3], [0, 3]], "世界", 0.88]],
            0.5,
        )
        self.svc.ocr = fake_ocr
        self.svc.ocr_available = True

        result = self.svc.recognize_image(img)
        self.assertEqual(result["text"], "你好\n世界")
        self.assertEqual(len(result["lines"]), 2)
        self.assertEqual(result["lines"][0]["text"], "你好")
        self.assertEqual(result["lines"][1]["score"], 0.88)
        self.assertIn("elapsed", result)

    def test_mock_ocr_exception_degrades(self) -> None:
        """mock OCR 抛异常时返回 error，不向上抛。"""
        img = os.path.join(self.tmpdir, "err.png")
        with open(img, "wb") as f:
            f.write(b"x")
        fake_ocr = MagicMock(side_effect=RuntimeError("ocr boom"))
        self.svc.ocr = fake_ocr
        self.svc.ocr_available = True
        result = self.svc.recognize_image(img)
        self.assertEqual(result["text"], "")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
