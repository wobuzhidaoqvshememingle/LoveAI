# -*- coding: utf-8 -*-
"""
LoveAI v3.1 加固补丁 · P-07 拒绝信号自动降级 单元测试（离线）

覆盖：
  - 明确拒绝（强停止 / 要空间）→ hard/soft
  - 暗示性拒绝：连续单字冷敷衍、话题反复转移、长冷（复用 NoReplyDetector 结论）
  - 正常对话不误触发
  - 只拦推进类任务，反制/给空间/引导对等投入主线被保护（不误伤）
  - build_boundary_prompt_addendum 行为
全部纯函数、离线、不碰 DB / 模型 / 网络。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.refusal_downgrade import (  # noqa: E402
    detect_refusal,
    is_push_task,
    filter_push_titles,
    build_boundary_prompt_addendum,
    is_explicit_refusal,
)


class TestExplicitRefusal(unittest.TestCase):
    def test_strong_stop_is_hard(self):
        msgs = [{"direction": "sent", "content": "周末一起吃饭吗"},
                {"direction": "received", "content": "别说了，我不想聊"}]
        d = detect_refusal(msgs)
        self.assertTrue(d["triggered"])
        self.assertEqual(d["level"], "hard")
        self.assertEqual(d["downgrade"], "hold_boundary")
        self.assertTrue(any("强停止" in r for r in d["reasons"]))

    def test_space_request_is_soft(self):
        msgs = [{"direction": "received", "content": "我想静静，给我点空间"}]
        d = detect_refusal(msgs)
        self.assertTrue(d["triggered"])
        self.assertEqual(d["level"], "soft")

    def test_is_explicit_refusal_helper(self):
        self.assertTrue(is_explicit_refusal("别发了"))
        self.assertTrue(is_explicit_refusal("让我一个人静静"))
        self.assertFalse(is_explicit_refusal("今天天气不错"))


class TestImplicitRefusal(unittest.TestCase):
    def test_trailing_cold_single_chars(self):
        msgs = [{"direction": "sent", "content": "在干嘛"},
                {"direction": "received", "content": "哦"},
                {"direction": "received", "content": "嗯"},
                {"direction": "received", "content": "额"}]
        d = detect_refusal(msgs)
        self.assertTrue(d["triggered"])
        self.assertTrue(any("单字" in r for r in d["reasons"]))

    def test_topic_shift_repeated(self):
        msgs = [{"direction": "received", "content": "对了"},
                {"direction": "received", "content": "以后再说吧"},
                {"direction": "received", "content": "随便吧"}]
        d = detect_refusal(msgs)
        self.assertTrue(d["triggered"])
        self.assertTrue(any("转移" in r for r in d["reasons"]))

    def test_long_cold_via_no_reply(self):
        no_reply = {"is_no_reply": True, "waiting_on_partner": True,
                    "severity": "重度", "waiting_hours": 48.0}
        d = detect_refusal([], no_reply=no_reply)
        self.assertTrue(d["triggered"])
        self.assertTrue(any("长冷" in r for r in d["reasons"]))

    def test_light_no_reply_not_triggered(self):
        # 轻度不算长冷，避免误伤正常慢回
        no_reply = {"is_no_reply": True, "waiting_on_partner": True,
                    "severity": "轻度", "waiting_hours": 2.0}
        d = detect_refusal([], no_reply=no_reply)
        self.assertFalse(d["triggered"])

    def test_normal_chat_not_triggered(self):
        msgs = [{"direction": "sent", "content": "最近忙吗"},
                {"direction": "received", "content": "挺忙的，刚开完会，你呢？"},
                {"direction": "sent", "content": "我还行，注意休息"}]
        d = detect_refusal(msgs)
        self.assertFalse(d["triggered"])


class TestPushTaskProtection(unittest.TestCase):
    def test_push_tasks_flagged(self):
        self.assertTrue(is_push_task("约她周末见面"))
        self.assertTrue(is_push_task("表白确认关系"))
        self.assertTrue(is_push_task("升温升级亲密感"))

    def test_neutral_counter_means_not_flagged(self):
        # 反制/给空间/引导对等投入主线必须被保护，绝不软暂停
        self.assertFalse(is_push_task("渐进式减量到70%（不突然断供）"))
        self.assertFalse(is_push_task("条件交换替代无条件给予"))
        self.assertFalse(is_push_task("轻量接触给空间（不追问、不逼表态）"))
        self.assertFalse(is_push_task("引导对方投入（富兰克林效应）"))
        self.assertFalse(is_push_task("我信息设边界"))
        self.assertFalse(is_push_task("主动温和改约一次（给台阶，不追问）"))

    def test_filter_push_titles(self):
        titles = ["约她周末见面", "条件交换替代无条件给予", "表白确认关系",
                  "轻量接触给空间"]
        pushed = filter_push_titles(titles)
        self.assertEqual(set(pushed), {"约她周末见面", "表白确认关系"})


class TestBoundaryAddendum(unittest.TestCase):
    def test_addendum_empty_when_not_triggered(self):
        d = {"triggered": False, "level": "none"}
        self.assertEqual(build_boundary_prompt_addendum(d), "")

    def test_addendum_present_when_hard(self):
        d = {"triggered": True, "level": "hard", "reasons": ["x"]}
        txt = build_boundary_prompt_addendum(d)
        self.assertIn("边界降级", txt)
        self.assertIn("严禁", txt)

    def test_addendum_soft_mentions_counter_protected(self):
        d = {"triggered": True, "level": "soft", "reasons": ["x"]}
        txt = build_boundary_prompt_addendum(d)
        self.assertIn("反制", txt)


if __name__ == "__main__":
    unittest.main()
