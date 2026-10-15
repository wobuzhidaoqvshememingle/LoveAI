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

    def test_hf_ssh_marked_unreachable(self) -> None:
        hf = next(c for c in FREE_CHANNEL_CATALOG if c["id"] == "hf_ssh")
        self.assertFalse(hf["reachable"])
        self.assertEqual(hf["priority"], 99)

    def test_as_dict_summary_consistent(self) -> None:
        d = as_dict()
        self.assertEqual(d["summary"]["total"], len(FREE_CHANNEL_CATALOG))
        self.assertEqual(d["summary"]["zero_config_count"], len(zero_config_only()))
        self.assertEqual(d["summary"]["reachable_count"], len(reachable_now()))
        self.assertIn("免费≠无 key", "".join(d["notes"]))


if __name__ == "__main__":
    unittest.main()
