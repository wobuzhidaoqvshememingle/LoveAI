# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - AuthHelper 单元测试

覆盖：
  - __init__ 不崩溃 / get_status 默认值
  - attach_ocr 注入 OCR 服务
  - save_token / get_valid_token（存活 / 过期 / 临期刷新）
  - invalidate_token
  - classify_captcha_type（滑块/数字/文字/图形/未知）
  - recognize_captcha（mock OCR / 无 OCR 离线兜底 / 图片不存在）
  - check_session_alive
  - monitor_loop_once
  - set_relogin_callback / set_verification_code_callback / provide_verification_code
"""
import os
import sys
import time
import json
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.auth_helper import AuthHelper, CaptchaType  # noqa: E402


class TestAuthHelper(unittest.TestCase):
    """AuthHelper 测试：auth_dir 指向临时目录，避免污染真实 data/auth/。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_auth_")
        self.auth_dir = os.path.join(self.tmpdir, "auth")
        self.h = AuthHelper(ocr_service=None, auth_dir=self.auth_dir, refresh_margin=60)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 初始化 / 状态
    # ------------------------------------------------------------------ #
    def test_init_no_crash(self) -> None:
        """__init__ 不应崩溃，auth_dir 自动创建。"""
        self.assertTrue(os.path.isdir(self.auth_dir))
        self.assertEqual(self.h.ocr, None)
        status = self.h.get_status()
        self.assertFalse(status["ocr_attached"])
        self.assertIn("platforms", status)
        self.assertEqual(status["refresh_margin"], 60)

    def test_attach_ocr(self) -> None:
        """attach_ocr 应把 OCR 服务挂到 self.ocr。"""
        fake_ocr = MagicMock()
        self.h.attach_ocr(fake_ocr)
        self.assertEqual(self.h.ocr, fake_ocr)
        self.assertTrue(self.h.get_status()["ocr_attached"])

    # ------------------------------------------------------------------ #
    # token 持久化
    # ------------------------------------------------------------------ #
    def test_save_and_get_token(self) -> None:
        """save_token 后 get_valid_token 应返回原 token。"""
        ok = self.h.save_token("wechat_web", "tok_abc", expires_in=3600)
        self.assertTrue(ok)
        self.assertEqual(self.h.get_valid_token("wechat_web"), "tok_abc")
        # auth.json 应落盘
        self.assertTrue(os.path.exists(self.h.auth_file))
        with open(self.h.auth_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("wechat_web", data)
        self.assertEqual(data["wechat_web"]["token"], "tok_abc")

    def test_get_expired_token_triggers_relogin(self) -> None:
        """已过期 token 应触发重新登录回调，返回 None。"""
        relogin_called = {"flag": False}

        def fake_relogin(platform):
            relogin_called["flag"] = True
            return True

        self.h.set_relogin_callback(fake_relogin)
        # 直接塞一个已过期的 session
        self.h._sessions["wechat_pc"] = {
            "token": "old",
            "issued_at": time.time() - 10000,
            "expires_at": time.time() - 100,
            "extra": {},
        }
        token = self.h.get_valid_token("wechat_pc")
        self.assertIsNone(token)
        self.assertTrue(relogin_called["flag"])

    def test_get_near_expiry_token_tries_refresh(self) -> None:
        """临期 token 应尝试刷新；无 refresh_token 时触发重新登录。"""
        self.h._sessions["wx"] = {
            "token": "near",
            "issued_at": time.time() - 1000,
            "expires_at": time.time() + 30,  # 距过期 30s < margin 60s
            "extra": {},  # 无 refresh_token
        }
        # 无回调时不崩溃
        token = self.h.get_valid_token("wx")
        # 刷新失败但原 token 还没完全过期 → 返回原 token
        self.assertEqual(token, "near")

    def test_invalidate_token(self) -> None:
        """invalidate_token 应从内存和文件中删除。"""
        self.h.save_token("wx", "tok", expires_in=3600)
        self.assertTrue(self.h.invalidate_token("wx"))
        self.assertIsNone(self.h.get_valid_token("wx"))

    def test_check_session_alive(self) -> None:
        """check_session_alive 对存在/过期/不存在三种情况返回正确。"""
        self.assertFalse(self.h.check_session_alive("nonexist"))
        self.h.save_token("wx", "tok", expires_in=3600)
        self.assertTrue(self.h.check_session_alive("wx"))
        self.h._sessions["wx"]["expires_at"] = time.time() - 10
        self.assertFalse(self.h.check_session_alive("wx"))

    # ------------------------------------------------------------------ #
    # 验证码类型识别
    # ------------------------------------------------------------------ #
    def test_classify_slider(self) -> None:
        self.assertEqual(AuthHelper.classify_captcha_type("请拖动滑块完成拼图"),
                         CaptchaType.SLIDER)

    def test_classify_number(self) -> None:
        self.assertEqual(AuthHelper.classify_captcha_type("验证码：1234"),
                         CaptchaType.NUMBER)

    def test_classify_text(self) -> None:
        self.assertEqual(AuthHelper.classify_captcha_type("输入 abcd 完成登录"),
                         CaptchaType.TEXT)

    def test_classify_graphic(self) -> None:
        self.assertEqual(AuthHelper.classify_captcha_type("请计算 3+5 等于几"),
                         CaptchaType.GRAPHIC)

    def test_classify_unknown_on_empty(self) -> None:
        self.assertEqual(AuthHelper.classify_captcha_type(""), CaptchaType.UNKNOWN)

    # ------------------------------------------------------------------ #
    # recognize_captcha
    # ------------------------------------------------------------------ #
    def test_recognize_captcha_missing_image(self) -> None:
        """图片不存在时返回 error。"""
        result = self.h.recognize_captcha("/no/such/file.png")
        self.assertIn("error", result)
        self.assertEqual(result["code"], "")

    def test_recognize_captcha_with_mock_ocr_number(self) -> None:
        """mock OCR 返回数字串，应识别为 NUMBER 并提取数字。"""
        tmpimg = os.path.join(self.tmpdir, "cap.png")
        with open(tmpimg, "wb") as f:
            f.write(b"fake")
        fake_ocr = MagicMock()
        fake_ocr.recognize_image.return_value = {"text": "验证码：8523", "lines": []}
        self.h.attach_ocr(fake_ocr)
        result = self.h.recognize_captcha(tmpimg)
        self.assertEqual(result["type"], CaptchaType.NUMBER.value)
        self.assertEqual(result["code"], "8523")
        self.assertEqual(result["strategy"], "ocr")

    def test_recognize_captcha_graphic_arithmetic(self) -> None:
        """图形算术验证码应算出结果。"""
        tmpimg = os.path.join(self.tmpdir, "calc.png")
        with open(tmpimg, "wb") as f:
            f.write(b"fake")
        fake_ocr = MagicMock()
        fake_ocr.recognize_image.return_value = {"text": "请计算 3+5 等于几", "lines": []}
        self.h.attach_ocr(fake_ocr)
        result = self.h.recognize_captcha(tmpimg)
        self.assertEqual(result["type"], CaptchaType.GRAPHIC.value)
        self.assertEqual(result["code"], "8")

    def test_recognize_captcha_no_ocr_offline_fallback(self) -> None:
        """无 OCR 服务时走离线兜底，不崩溃，返回空 code。"""
        tmpimg = os.path.join(self.tmpdir, "off.png")
        with open(tmpimg, "wb") as f:
            f.write(b"fake")
        # 不 attach_ocr
        result = self.h.recognize_captcha(tmpimg)
        # 无 OCR 时 ocr_text 为空 → type=UNKNOWN，code 空
        self.assertEqual(result["code"], "")
        self.assertNotIn("error", result)  # 正常兜底不算错误

    def test_recognize_captcha_ocr_exception_degrades(self) -> None:
        """OCR 抛异常时应降级，不向上抛。"""
        tmpimg = os.path.join(self.tmpdir, "err.png")
        with open(tmpimg, "wb") as f:
            f.write(b"fake")
        fake_ocr = MagicMock()
        fake_ocr.recognize_image.side_effect = RuntimeError("ocr boom")
        self.h.attach_ocr(fake_ocr)
        result = self.h.recognize_captcha(tmpimg)
        self.assertEqual(result["code"], "")

    # ------------------------------------------------------------------ #
    # 会话监控
    # ------------------------------------------------------------------ #
    def test_monitor_loop_once(self) -> None:
        """monitor_loop_once 应遍历所有平台并报告过期项。"""
        # 一个活的，一个过期的
        self.h.save_token("alive", "tok", expires_in=3600)
        self.h._sessions["dead"] = {
            "token": "old",
            "issued_at": time.time() - 10000,
            "expires_at": time.time() - 100,
            "extra": {},
        }
        report = self.h.monitor_loop_once()
        self.assertEqual(report["checked"], 2)
        self.assertIn("dead", report["expired"])
        self.assertIn("alive", self.h._sessions)

    # ------------------------------------------------------------------ #
    # 回调
    # ------------------------------------------------------------------ #
    def test_set_relogin_callback(self) -> None:
        relogin_called = {"platform": None}

        def cb(platform):
            relogin_called["platform"] = platform
            return True

        self.h.set_relogin_callback(cb)
        self.h._trigger_relogin("wx_test")
        self.assertEqual(relogin_called["platform"], "wx_test")

    def test_set_verification_code_callback(self) -> None:
        """provide_verification_code 应优先走注册的自定义回调。"""
        def cb(path):
            return "custom_123"
        self.h.set_verification_code_callback(cb)
        self.assertEqual(self.h.provide_verification_code("any.png"), "custom_123")

    def test_provide_verification_code_default(self) -> None:
        """未注册自定义回调时，走默认 recognize_captcha。"""
        # 图片不存在 → 返回 None
        self.assertIsNone(self.h.provide_verification_code("/no/such.png"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
