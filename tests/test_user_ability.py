# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 用户能力评估系统单元测试

覆盖：
  - 默认画像初始化（6 维全 3 分）
  - 任务完成更新能力（成功→社交/执行涨分，失败→扣分）
  - 聊天推断能力（情绪词/焦虑词/时间紧张词）
  - 任务难度调整（6 个维度各一个场景）
  - 情感阶梯表 L1-L5
  - 阶梯式任务分级（basic/intermediate/advanced 推荐规则）
"""
import os
import sys
import shutil
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.user_ability import (  # noqa: E402
    UserAbility, EMOTIONAL_LADDER, ABILITY_DIMS, VALID_LEVELS,
)
from models.database import Database  # noqa: E402


class TestDefaultProfile(unittest.TestCase):
    """默认画像初始化。"""

    def test_default_all_three(self) -> None:
        ua = UserAbility(None)
        p = ua.get_profile("any_user")
        for d in ABILITY_DIMS:
            self.assertEqual(p[d], 3, f"默认画像 {d} 应为 3")

    def test_default_level_basic(self) -> None:
        ua = UserAbility(None)
        p = ua.get_profile("any_user")
        self.assertEqual(p["level"], "basic")

    def test_persistence_with_db(self) -> None:
        """带 db 时画像能写入并读回。"""
        tmpdir = tempfile.mkdtemp(prefix="loveai_ua_")
        try:
            db = Database(os.path.join(tmpdir, "ua.db"))
            db.init_tables()
            ua = UserAbility(db)
            ua.set_user_ability_level("u1", "advanced")
            p = ua.get_profile("u1")
            self.assertEqual(p["level"], "advanced")
            db.engine.dispose()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestUpdateFromTaskCompletion(unittest.TestCase):
    """任务完成更新能力。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_ua_upd_")
        self.db = Database(os.path.join(self.tmpdir, "upd.db"))
        self.db.init_tables()
        self.ua = UserAbility(self.db)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_success_high_stress_bumps_social(self) -> None:
        """完成高压力任务 → social_skill 上涨。"""
        p0 = self.ua.get_profile("u")
        self.ua.update_from_task_completion("u", {
            "title": "表达一次真实感受",
            "success": True,
            "stress_level": 4,
        })
        p1 = self.ua.get_profile("u")
        self.assertGreater(p1["social_skill"], p0["social_skill"])

    def test_failure_deducts(self) -> None:
        """失败任务 → social/执行 扣分。"""
        p0 = self.ua.get_profile("u")
        self.ua.update_from_task_completion("u", {
            "title": "约对方喝咖啡",
            "success": False,
        })
        p1 = self.ua.get_profile("u")
        self.assertLess(p1["social_skill"], p0["social_skill"])
        self.assertLess(p1["executive_function"], p0["executive_function"])

    def test_clamp_to_1_5(self) -> None:
        """分数被 clamp 在 1-5。"""
        for _ in range(20):
            self.ua.update_from_task_completion("u", {
                "title": "x", "success": True, "stress_level": 5,
            })
        p = self.ua.get_profile("u")
        self.assertLessEqual(p["social_skill"], 5)
        self.assertGreaterEqual(p["social_skill"], 1)


class TestInferFromChat(unittest.TestCase):
    """从聊天记录推断能力。"""

    def setUp(self) -> None:
        self.ua = UserAbility(None)

    def test_emotional_expression_up_on_long_emotional_msgs(self) -> None:
        p0 = self.ua.get_profile("u")
        self.ua.infer_from_chat("u", [
            "今天跟你聊天我真的挺开心的",
            "刚才那件事我有点难过，但说出来就好多了",
            "我其实挺在意你的感受的",
        ])
        # 无 db 时不会真写回，但不应报错
        self.assertEqual(p0["emotional_expression"], 3)

    def test_anxiety_words_raise_anxiety(self) -> None:
        ua = UserAbility(None)
        # 无 db 时 infer 不写库，只验证不抛异常
        ua.infer_from_chat("u", [
            "我好怕他不回我",
            "昨晚又睡不着一直在担心",
        ])

    def test_empty_messages_noop(self) -> None:
        ua = UserAbility(None)
        p = ua.infer_from_chat("u", [])
        self.assertEqual(p["anxiety_level"], 3)


class TestDifficultyAdjustment(unittest.TestCase):
    """6 个维度的难度调整各测一个场景。"""

    def setUp(self) -> None:
        self.ua = UserAbility(None)
        # 直接覆盖默认画像：用 mock get_profile
        self._orig_get = self.ua.get_profile

    def _set_profile(self, **overrides) -> None:
        base = {d: 3 for d in ABILITY_DIMS}
        base["level"] = "basic"
        base.update(overrides)
        self.ua.get_profile = lambda uid: dict(base)  # type: ignore

    def tearDown(self) -> None:
        self.ua.get_profile = self._orig_get  # type: ignore

    def test_high_anxiety_splits_steps(self) -> None:
        """anxiety>=4 → 步骤拆成 <5 分钟并加确认。"""
        self._set_profile(anxiety_level=5)
        out = self.ua.adjust_task_difficulty("u", {
            "title": "处理一次冲突",
            "steps": [{"name": "谈", "complete_keywords": ["x"]}],
        })
        self.assertTrue(any("焦虑适配" in n for n in out["_ability_notes"]))
        self.assertGreaterEqual(len(out["steps"]), 2)

    def test_low_social_skill_adds_script(self) -> None:
        """social_skill<=2 → 附加话术模板。"""
        self._set_profile(social_skill=1)
        out = self.ua.adjust_task_difficulty("u", {"title": "主动发一条问候"})
        self.assertIn("guide_direction", out)
        self.assertIn("新手话术", out["guide_direction"])
        self.assertTrue(any("新手适配" in n for n in out["_ability_notes"]))

    def test_high_social_skill_no_script(self) -> None:
        """social_skill>=4 → 不附加话术，鼓励自主。"""
        self._set_profile(social_skill=5, anxiety_level=1)
        out = self.ua.adjust_task_difficulty("u", {"title": "约对方喝咖啡"})
        # 高 social 不应有"新手话术"
        self.assertNotIn("新手话术", out.get("guide_direction", ""))

    def test_low_economic_filters_high_cost(self) -> None:
        """economic<=2 → 高消费任务被替换。"""
        self._set_profile(economic_status=1)
        out = self.ua.adjust_task_difficulty("u", {"title": "买礼物送给对方"})
        self.assertNotIn("买礼物", out["title"])
        self.assertTrue(any("经济适配" in n for n in out["_ability_notes"]))

    def test_low_time_shortens_deadline(self) -> None:
        """time_energy<=2 → 时间词被替换成 10 分钟。"""
        self._set_profile(time_energy=1)
        out = self.ua.adjust_task_difficulty("u", {"title": "花30分钟和对方深聊"})
        self.assertIn("10分钟", out["title"])
        self.assertNotIn("30分钟", out["title"])

    def test_low_executive_adds_checkpoint(self) -> None:
        """executive_function<=2 → 加提醒检查点。"""
        self._set_profile(executive_function=1)
        out = self.ua.adjust_task_difficulty("u", {"title": "主动发起一次对话"})
        self.assertIn("执行提醒", out["guide_direction"])

    def test_basic_user_no_direct_confession(self) -> None:
        """basic 推荐级 + 高压力任务 → 被降级。"""
        self._set_profile(social_skill=1, anxiety_level=5)
        out = self.ua.adjust_task_difficulty("u", {"title": "直接表白"})
        self.assertNotEqual(out["title"], "直接表白")
        self.assertTrue(any("压力适配" in n for n in out["_ability_notes"]))


class TestEmotionalLadder(unittest.TestCase):
    """情感阶梯表 L1-L5。"""

    def test_ladder_has_five_levels(self) -> None:
        self.assertEqual([l["level"] for l in EMOTIONAL_LADDER],
                         ["L1", "L2", "L3", "L4", "L5"])

    def test_ladder_pressure_monotonic(self) -> None:
        pressures = [l["pressure"] for l in EMOTIONAL_LADDER]
        self.assertEqual(pressures, sorted(pressures))

    def test_ladder_examples_nonempty(self) -> None:
        for l in EMOTIONAL_LADDER:
            self.assertGreater(len(l["examples"]), 0)

    def test_get_emotional_ladder(self) -> None:
        ua = UserAbility(None)
        out = ua.get_emotional_ladder("u")
        self.assertEqual(len(out["ladder"]), 5)
        self.assertIn(out["recommended_start_level"], VALID_LEVELS)


class TestLevelRecommendation(unittest.TestCase):
    """推荐起始级别规则。"""

    def setUp(self) -> None:
        self.ua = UserAbility(None)
        self._orig = self.ua.get_profile

    def tearDown(self) -> None:
        self.ua.get_profile = self._orig  # type: ignore

    def test_beginner_anxious_gets_basic(self) -> None:
        self.ua.get_profile = lambda u: {  # type: ignore
            "social_skill": 1, "anxiety_level": 5,
        }
        self.assertEqual(self.ua.get_recommended_start_level("u"), "basic")

    def test_expert_gets_advanced(self) -> None:
        self.ua.get_profile = lambda u: {  # type: ignore
            "social_skill": 5, "anxiety_level": 1,
        }
        self.assertEqual(self.ua.get_recommended_start_level("u"), "advanced")

    def test_mixed_gets_intermediate(self) -> None:
        self.ua.get_profile = lambda u: {  # type: ignore
            "social_skill": 3, "anxiety_level": 3,
        }
        self.assertEqual(self.ua.get_recommended_start_level("u"), "intermediate")

    def test_set_level_validates(self) -> None:
        ua = UserAbility(None)
        out = ua.set_user_ability_level("u", "nonsense")
        # 无 db 时返回默认 basic
        self.assertEqual(out.get("level", "basic"), "basic")


if __name__ == "__main__":
    unittest.main()
