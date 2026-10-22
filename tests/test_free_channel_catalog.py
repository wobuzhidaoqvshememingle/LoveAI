# -*- coding: utf-8 -*-
"""R3.3-43 free_channel_catalog 结构化清单单测。"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.free_channel_catalog import (  # noqa: E402
    FREE_CHANNEL_CATALOG, as_dict, zero_config_only, free_but_need_key, reachable_now,
)


class TestFreeChannelCatalog(unittest.TestCase):
    def test_has_six_channels(self) -> None:
        ids = [c["id"] for c in FREE_CHANNEL_CATALOG]
        self.assertIn("ollama_local", ids)
        self.assertIn("openrouter_free", ids)
        self.assertIn("nvidia_nim", ids)
        self.assertIn("browser_ai_hub", ids)
        self.assertIn("desktop_ai_connector", ids)
        self.assertIn("hf_ssh", ids)

    def test_zero_config_remote_present(self) -> None:
        """pollinations/llm7：免 Key 远端零配置，已进入免费通道清单。"""
        ids = [c["id"] for c in FREE_CHANNEL_CATALOG]
        self.assertIn("pollinations", ids)
        self.assertIn("llm7", ids)
        for cid in ("pollinations", "llm7"):
            c = next(c for c in FREE_CHANNEL_CATALOG if c["id"] == cid)
            self.assertTrue(c["zero_config"])
            self.assertFalse(c["need_key"])
            self.assertTrue(c["reachable"])
            self.assertIn("free_model_router", c["code_path"])

    def test_each_channel_has_required_fields(self) -> None:
        required = ("id", "name", "need_login", "need_key", "zero_config",
                    "reachable", "latency_ms", "priority", "code_path", "note")
        for c in FREE_CHANNEL_CATALOG:
            for k in required:
                self.assertIn(k, c, f"channel {c.get('id')} missing {k}")

    def test_zero_config_only_contains_ollama(self) -> None:
        zc = zero_config_only()
        ids = [c["id"] for c in zc]
        self.assertIn("ollama_local", ids)
        # openrouter/nvidia 都要 key，不应在零配置组
        self.assertNotIn("openrouter_free", ids)
        self.assertNotIn("nvidia_nim", ids)

    def test_free_but_need_key_explicitly_flagged(self) -> None:
        fk = free_but_need_key()
        ids = [c["id"] for c in fk]
        self.assertIn("openrouter_free", ids)
        self.assertIn("nvidia_nim", ids)

    def test_hf_ssh_endpoint_answers_but_channel_disabled(self) -> None:
        """2026-10-17 r2 复核推翻原断言：chat.hf.co:22 有 SSHD 应答
        （Permission denied (publickey,keyboard-interactive)，即需凭据、非网络不通），
        故 reachable 为 True；但本项目 hf_ssh_channel 仍是 disabled 占位，priority 保持 99。"""
        hf = next(c for c in FREE_CHANNEL_CATALOG if c["id"] == "hf_ssh")
        self.assertTrue(hf["reachable"])
        self.assertEqual(hf["priority"], 99)
        self.assertIn("disabled", hf["note"])
        self.assertIn("Permission denied", hf["note"])

    def test_http_responded_channels_are_reachable(self) -> None:
        """r2 口径：拿到了服务端/边缘 HTTP 响应（含 401/403/405）= reachable True，
        「匿名被拒」不得写成「本机网络不通」。"""
        for cid in ("groq", "cerebras", "cohere", "github_models",
                    "cloudflare_workers_ai", "sambanova", "zhipu"):
            c = next(x for x in FREE_CHANNEL_CATALOG if x["id"] == cid)
            self.assertIs(True, c["reachable"], f"{cid} 拿到了 HTTP 应答，应为 True")

    def test_reachable_false_only_when_no_http_response(self) -> None:
        """r2 口径：False 只允许出现在「拿不到任何 HTTP 响应」的通道上
        （连接超时 / DNS 失败 / 连接被拒）。"""
        for cid in ("google_gemini", "mistral", "huggingface"):
            c = next(x for x in FREE_CHANNEL_CATALOG if x["id"] == cid)
            self.assertIs(False, c["reachable"])
            self.assertIn("(28)", c["probe"])   # curl 连接超时
            self.assertIsNone(c["latency_ms"])  # 无应答 => 无延迟可记

    def test_as_dict_summary_consistent(self) -> None:
        d = as_dict()
        self.assertEqual(d["summary"]["total"], len(FREE_CHANNEL_CATALOG))
        self.assertEqual(d["summary"]["zero_config_count"], len(zero_config_only()))
        self.assertEqual(d["summary"]["reachable_count"], len(reachable_now()))
        self.assertIn("免费≠无 key", "".join(d["notes"]))


if __name__ == "__main__":
    unittest.main()
