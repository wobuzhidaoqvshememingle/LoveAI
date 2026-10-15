# -*- coding: utf-8 -*-
"""话术库 v2 新功能单测：跨会话去重 / 人格透镜 / 阶段透镜 / 连接风味。"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.script_library import (  # noqa: E402
    _dedupe_recent, record_usage, reset_person_usage,
    pick_scripts_smart, reset_usage,
)


class TestScriptLibraryV2(unittest.TestCase):

    def setUp(self):
        reset_usage()
        reset_person_usage()

    # ------------------------------------------------------------------ #
    # 跨会话去重
    # ------------------------------------------------------------------ #
    def test_dedupe_recent_excludes_used(self):
        cands = ["a", "b", "c", "d"]
        record_usage("p1", "邀约", ["a", "b"])
        fresh = _dedupe_recent("p1", "邀约", cands)
        self.assertNotIn("a", fresh)
        self.assertNotIn("b", fresh)
        self.assertIn("c", fresh)

    def test_dedupe_recent_no_person_returns_all(self):
        cands = ["a", "b"]
        record_usage(None, "邀约", ["a"])
        fresh = _dedupe_recent(None, "邀约", cands)
        self.assertEqual(fresh, cands)

    def test_dedupe_recent_empty_fallback(self):
        # 全用过也别返回空
        record_usage("p1", "邀约", ["a", "b", "c"])
        fresh = _dedupe_recent("p1", "邀约", ["a", "b"])
        self.assertEqual(len(fresh), 2)

    def test_reset_person_usage(self):
        record_usage("p1", "邀约", ["x"])
        reset_person_usage("p1")
        fresh = _dedupe_recent("p1", "邀约", ["x"])
        self.assertIn("x", fresh)

    # ------------------------------------------------------------------ #
    # 智能推荐主入口
    # ------------------------------------------------------------------ #
    def test_smart_returns_k(self):
        out = pick_scripts_smart("邀约", k=3)
        self.assertEqual(len(out), 3)

    def test_smart_unknown_scene_empty(self):
        self.assertEqual(pick_scripts_smart("不存在的场景", k=3), [])

    def test_smart_consecutive_no_repeat_same_person(self):
        a = pick_scripts_smart("升温", k=3, person_id="p9")
        b = pick_scripts_smart("升温", k=3, person_id="p9")
        joined = a + b
        # 6 条里至少 4 条不同
        self.assertGreaterEqual(len(set(joined)), 4)

    # ------------------------------------------------------------------ #
    # 人格透镜：回避型要软化"逼问"
    # ------------------------------------------------------------------ #
    def test_persona_lens_avoid_softens_pushy(self):
        out = pick_scripts_smart(
            "邀约", k=5, persona_type="回避型",
            person_id="p_av",
        )
        joined = " ".join(out)
        # 不应该出现"你来不来给个话"这种逼问
        self.assertNotIn("你来不来给个话", joined)

    def test_persona_lens_narcissistic_removes_humble(self):
        out = pick_scripts_smart(
            "幽默", k=8, persona_type="自恋型",
            person_id="p_np",
        )
        joined = " ".join(out)
        # 讨好尾巴被换掉
        self.assertNotIn("一般人我不理", joined)

    # ------------------------------------------------------------------ #
    # 阶段透镜：meet 软化深情
    # ------------------------------------------------------------------ #
    def test_stage_lens_meet_softens_deep(self):
        # 直接调内部逻辑验证：那条"我想和你成为彼此最重要的人"在 meet 会被换
        from services.script_library import _apply_stage_lens
        out = _apply_stage_lens("我想和你成为彼此最重要的人", "meet")
        self.assertNotEqual(out, "我想和你成为彼此最重要的人")
        self.assertIn("有意思", out)

    def test_stage_lens_other_stages_unchanged(self):
        from services.script_library import _apply_stage_lens
        out = _apply_stage_lens("跟你聊天还挺舒服的", "deepen")
        self.assertEqual(out, "跟你聊天还挺舒服的")

    # ------------------------------------------------------------------ #
    # 连接风味
    # ------------------------------------------------------------------ #
    def test_connection_flavor_mirror(self):
        out = pick_scripts_smart(
            "关心", k=3, connection_type="镜像",
            person_id="p_mirror",
        )
        joined = " ".join(out)
        self.assertTrue(
            ("我也是这么想的" in joined) or
            ("想到一块去" in joined) or
            ("我心里也" in joined),
            f"镜像型应注入同频共振句: {joined}",
        )

    def test_connection_flavor_complement(self):
        out = pick_scripts_smart(
            "升温", k=3, connection_type="互补",
            person_id="p_comp",
        )
        joined = " ".join(out)
        # 互补型三句尾都算合法注入；原断言只认"羡慕"一句，k=3 独立抽奖下
        # 有 (2/3)^3≈30% 概率三句都没抽中"羡慕"而 flaky，故对齐镜像的多句 OR。
        self.assertTrue(
            ("羡慕" in joined) or ("跟你聊我踏实" in joined)
            or ("一句话就捋顺" in joined),
            f"互补型应注入欣赏对方特质句: {joined}",
        )

    def test_connection_flavor_growth(self):
        out = pick_scripts_smart(
            "关心", k=3, connection_type="成长",
            person_id="p_grow",
        )
        joined = " ".join(out)
        # 成长型三句尾都算合法正向反馈；原断言只认"学到/琢磨"两句，
        # k=3 独立抽奖下三句都抽中中间那句("你这么一说…")时 flaky，
        # 故把第三句也纳入合法期望。
        self.assertTrue(
            ("学到" in joined) or ("琢磨" in joined)
            or ("死脑筋好像转过来" in joined),
            f"成长型应注入正向反馈句: {joined}",
        )

    # ------------------------------------------------------------------ #
    # 情绪透传
    # ------------------------------------------------------------------ #
    def test_mood_passthrough(self):
        out = pick_scripts_smart(
            "关心", k=2, mood="开心", person_id="p_mood",
        )
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
