# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - AI 立绘生成器单测（R3.3-50）

覆盖：
  - generate_portrait 返回存在的文件路径
  - 内置预设角色能命中
  - dedupe_portrait 相似/不相似判断
  - 本地 SD 未启动时降级到 PIL 程序化
  - R3.3-50 E2E: 起一个假 SD server，验证 _try_local_sd 真发 HTTP、真存图
"""
import base64
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.portrait_generator import (  # noqa: E402
    PortraitGenerator,
    dedupe_portrait,
    PRESET_CHARACTERS,
)


class _FakeSDHandler(BaseHTTPRequestHandler):
    """假 SD WebUI：/sdapi/v1/options 返回 200，/sdapi/v1/txt2img 返回假 base64 PNG。"""

    # 1x1 红色 PNG
    _FAKE_PNG_B64 = base64.b64encode(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03"
        b"\x00\x01\x00\x05\xfe\xd4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    ).decode("ascii")

    # 记录最近一次请求，供测试断言
    last_payload = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/sdapi/v1/options"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"sd": "fake"}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.startswith("/sdapi/v1/txt2img"):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            try:
                type(self).last_payload = json.loads(body.decode("utf-8"))
            except Exception:
                type(self).last_payload = {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"images": [self._FAKE_PNG_B64]}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


class TestPortraitGenerator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="portrait_test_")
        # 指向一个不存在的 SD 端口，确保走降级
        self.gen = PortraitGenerator(out_dir=self.tmp, sd_api_url="http://127.0.0.1:59999")

    def test_generate_returns_existing_file(self):
        p = self.gen.generate_portrait("温柔学姐", "chibi")
        self.assertTrue(os.path.exists(p), f"portrait not generated: {p}")
        self.assertGreater(os.path.getsize(p), 0)

    def test_all_preset_characters(self):
        for name in PRESET_CHARACTERS:
            p = self.gen.generate_portrait(name)
            self.assertTrue(os.path.exists(p), f"preset {name} failed")

    def test_free_text_character(self):
        p = self.gen.generate_portrait("一个戴眼镜的程序员", "anime")
        self.assertTrue(os.path.exists(p))

    def test_dedupe_same_image(self):
        # 同一张图 hash 应等于自己 -> 撞脸
        h = "a" * 16
        self.assertFalse(dedupe_portrait(h, [h]))

    def test_dedupe_different_image(self):
        # 完全不同的 hash -> 不撞脸
        h1 = "0" * 16
        h2 = "f" * 16
        self.assertTrue(dedupe_portrait(h1, [h2]))

    def test_dedupe_threshold(self):
        # 距离 <=10 撞脸；距离 >10 不撞脸
        h1 = "0000000000000000"
        # 翻转 5 bit -> 距离 5
        h_close = "000000000000001f"
        # 翻转 20 bit -> 距离 20
        h_far = "fffffffffffffff0"
        self.assertFalse(dedupe_portrait(h1, [h_close], threshold=10))
        self.assertTrue(dedupe_portrait(h1, [h_far], threshold=10))

    def test_hash_of_generated_image(self):
        p = self.gen.generate_portrait("元气少女")
        h = self.gen.hash_of(p)
        self.assertIsNotNone(h)
        self.assertEqual(len(h), 16)


class TestPortraitGeneratorRealSDCall(unittest.TestCase):
    """R3.3-50 E2E: 起假 SD server，验证真发 HTTP、真存图、真带 steps/512。"""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _FakeSDHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
        except Exception:
            pass

    def test_try_local_sd_real_post_and_save(self):
        _FakeSDHandler.last_payload = None
        tmp = tempfile.mkdtemp(prefix="portrait_sd_e2e_")
        gen = PortraitGenerator(
            out_dir=tmp, sd_api_url=f"http://127.0.0.1:{self.port}")
        out = gen._try_local_sd("温柔学姐, chibi girl", "chibi")
        # 必须真存了一张图，不能 return None 占位
        self.assertIsNotNone(out, "_try_local_sd 在 SD 在线时不应返回 None")
        self.assertTrue(os.path.exists(out), f"SD 返回的图没存盘: {out}")
        self.assertGreater(os.path.getsize(out), 0)
        # 断言真发了请求，且带了 steps=20 / 512x512
        self.assertIsNotNone(_FakeSDHandler.last_payload, "假 server 没收到 POST")
        payload = _FakeSDHandler.last_payload
        self.assertEqual(payload.get("steps"), 20)
        self.assertEqual(payload.get("width"), 512)
        self.assertEqual(payload.get("height"), 512)
        self.assertIn("温柔学姐", payload.get("prompt", ""))

    def test_generate_portrait_prefers_sd_when_alive(self):
        tmp = tempfile.mkdtemp(prefix="portrait_sd_e2e2_")
        gen = PortraitGenerator(
            out_dir=tmp, sd_api_url=f"http://127.0.0.1:{self.port}")
        p = gen.generate_portrait("高冷御姐", "anime")
        self.assertTrue(os.path.exists(p))
        # SD 在线时返回的应以 sd_ 前缀（不是 PIL 的 portrait_ 前缀）
        self.assertTrue(
            os.path.basename(p).startswith("sd_"),
            f"SD 在线时却走了 PIL 兜底: {p}")


if __name__ == "__main__":
    unittest.main()

