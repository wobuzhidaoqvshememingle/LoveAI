# -*- coding: utf-8 -*-
"""话术库（D19-21）单测：规模 / 标签 / 去重 / refine 真改。"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.script_library import (  # noqa: E402
    SCENES, pick_scripts, refine_script, reset_usage, library_size,
    list_scenes,
)


class TestScriptLibrary(unittest.TestCase):

    def setUp(self):
        reset_usage()

    def test_scenes_cover_required_types(self):
        for need in ("早安", "晚安", "邀约", "升温", "修复", "拒绝", "爽约",
                     "幽默", "关心", "暧昧推进", "确认关系", "分手边缘"):
            self.assertIn(need, SCENES, f"缺场景 {need}")

    def test_each_scene_has_20plus(self):
        for scene, items in SCENES.items():
            self.assertGreaterEqual(len(items), 20,
                                    f"场景 {scene} 只有 {len(items)} 条")

    def test_each_item_has_tags(self):
        for scene, items in SCENES.items():
            for it in items:
                self.assertIn("style", it)
                self.assertIn(it["style"], ("温柔", "幽默", "直接", "含蓄"))
                self.assertIn("length", it)
                self.assertIn(it["length"], ("短", "中", "长"))
                self.assertTrue(it["text"].strip())

    def test_pick_returns_k(self):
        out = pick_scripts("邀约", k=3)
        self.assertEqual(len(out), 3)

    def test_consecutive_three_calls_no_repeat(self):
        a = pick_scripts("升温", k=3)
        b = pick_scripts("升温", k=3)
        c = pick_scripts("升温", k=3)
        joined = a + b + c
        # 9 条里至少 8 条互不相同（去重生效，不是复读同一条）
        self.assertGreaterEqual(len(set(joined)), 8,
                                f"去重失效: {joined}")

    def test_refine_changes_address(self):
        tpl = "早啊，醒了没？今天也别忘了吃早饭。"
        out = refine_script(tpl, {"pet_name": "笨蛋", "mood": "开心"})
        # 真改了：要么换了称呼，要么加了语气
        self.assertNotEqual(out, tpl)

    def test_refine_weaves_topic(self):
        tpl = "我在。你想聊什么，慢慢说。"
        out = refine_script(tpl, {"last_topic": "加班", "mood": "低落"})
        self.assertIn("加班", out)

    def test_refine_empty(self):
        self.assertEqual(refine_script("", {}), "")

    def test_library_size(self):
        size = library_size()
        self.assertGreater(size["__total__"], 200)
        self.assertEqual(len(list_scenes()), 12)


if __name__ == "__main__":
    unittest.main()
