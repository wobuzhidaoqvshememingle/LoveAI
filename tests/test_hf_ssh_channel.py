# -*- coding: utf-8 -*-
"""R3.3-42 HF SSH 通道单测：验证 disabled 占位 + 不可达时走 fallback。"""
import os
import sys
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.hf_ssh_channel import (  # noqa: E402
    HFSSHChannel, HFSSHChannelDisabled, SSH_PROBE_RESULTS, describe_for_chain,
)


class _Cfg:
    """假 config：默认全空。"""
    def __init__(self, d: dict):
        self._d = d

    def get(self, key, default=None):
        v = self._d
        for p in key.split("."):
            if isinstance(v, dict) and p in v:
                v = v[p]
            else:
                return default
        return v


class TestHFSSHChannel(unittest.TestCase):
    def test_probe_results_record_unreachable(self) -> None:
        # 本轮实测 chat.hf.co / benchmark-hf.co 都 exit=255
        self.assertFalse(SSH_PROBE_RESULTS["chat.hf.co"]["reachable"])
        self.assertFalse(SSH_PROBE_RESULTS["benchmark-hf.co"]["reachable"])

    def test_default_disabled(self) -> None:
        ch = HFSSHChannel(config=_Cfg({}))
        self.assertFalse(ch.is_enabled())
        with self.assertRaises(HFSSHChannelDisabled):
            ch.chat("sys", "你好")

    def test_status_disabled_reason(self) -> None:
        ch = HFSSHChannel(config=_Cfg({}))
        st = ch.status()
        self.assertFalse(st["enabled"])
        self.assertIn("不可达", st["disabled_reason"])

    def test_forced_on_but_no_host_still_disabled(self) -> None:
        # 用户开了 enabled 但没填 host/user，仍 disabled
        ch = HFSSHChannel(config=_Cfg({"llm": {"free": {"hf_ssh": {"enabled": True}}}}))
        self.assertFalse(ch.is_enabled())
        with self.assertRaises(HFSSHChannelDisabled):
            ch.chat("sys", "hi")

    def test_forced_on_with_host_but_still_raises_stub(self) -> None:
        # 用户配了 host/user，is_enabled() 返回 True，但 chat() 仍 raise（stub 未接 paramiko）
        cfg = _Cfg({"llm": {"free": {"hf_ssh": {
            "enabled": True, "host": "bastion.internal", "user": "me",
            "key_path": "C:/Users/me/.ssh/id_rsa", "remote_model": "Qwen/Qwen2.5-0.5B",
        }}}})
        ch = HFSSHChannel(config=cfg)
        self.assertTrue(ch.is_enabled())
        with self.assertRaises(HFSSHChannelDisabled):
            ch.chat("sys", "hi")

    def test_describe_for_chain_excludes_from_auto_chain(self) -> None:
        d = describe_for_chain()
        self.assertEqual(d["layer"], "hf_ssh")
        self.assertFalse(d["enabled"])
        self.assertIn("l2_ollama", d["fallback_to"])
        self.assertIn("rules_template", d["fallback_to"])

    def test_paramiko_not_required_when_disabled(self) -> None:
        # mock paramiko 不存在，验证 disabled 分支不会因为 import paramiko 而崩
        with mock.patch.dict(sys.modules, {"paramiko": None}):
            ch = HFSSHChannel(config=_Cfg({}))
            with self.assertRaises(HFSSHChannelDisabled):
                ch.chat("sys", "hi")


if __name__ == "__main__":
    unittest.main()
