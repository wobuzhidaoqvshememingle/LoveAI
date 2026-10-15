# -*- coding: utf-8 -*-
"""
LoveAI R3.3-78 双轨情感指标：loyalty 维度 + 路线分流 单元测试。

覆盖冻结契约：
  - get_current 返回 dict 含 "loyalty" 键（与 18 维好感并列、互不污染）
  - get_loyalty / add_loyalty_bonus / get_route / set_route
  - 忠诚度独立日衰减（独立比例，不跟好感共用 0.005）
  - dual_track.resolve_route（counter / guide 两分支）
  - dual_track.main_metric
  - dual_track.extend_reward（七维键齐全 / 不改入参 / 按路线加权不同）
  - dual_track.ROUTE_WEIGHTS（counter / guide 两套七维）
"""
import os
import sys
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from services.dual_track import (  # noqa: E402
    ROUTE_WEIGHTS,
    REWARD_KEYS,
    resolve_route,
    main_metric,
    extend_reward,
)


def _make_person(db, pid):
    db.execute("INSERT INTO persons(id, nickname) VALUES(:p, :n)",
               {"p": pid, "n": pid})


class TestLoyaltyStorage(unittest.TestCase):
    """loyalty 独立存储：与 18 维好感互不混淆。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_loy_")
        self.db = Database(os.path.join(self.tmpdir, "test.db"))
        self.db.init_tables()
        self.engine = AffectionEngine(self.db)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_default_loyalty_50(self):
        """新对象缺省忠诚度 = 50。"""
        _make_person(self.db, "p1")
        self.assertAlmostEqual(self.engine.get_loyalty("p1"), 50.0)
        cur = self.engine.get_current("p1")
        self.assertIn("loyalty", cur)
        self.assertAlmostEqual(cur["loyalty"], 50.0)

    def test_02_loyalty_not_in_weights(self):
        """loyalty 绝不在 18 维权重表内，不污染加权总分。"""
        self.assertNotIn("loyalty", self.engine.WEIGHTS)
        self.assertNotIn("loyalty", self.engine.DIMENSION_NAMES)

    def test_03_loyalty_does_not_affect_total(self):
        """把 loyalty 拉到 99，好感总分仍应是 50（18 维全 50）。"""
        _make_person(self.db, "p2")
        scores = {d: 50.0 for d in self.engine.WEIGHTS}
        scores["loyalty"] = 99.0
        self.engine._save_current("p2", scores)
        cur = self.engine.get_current("p2")
        self.assertAlmostEqual(cur["loyalty"], 99.0)
        self.assertAlmostEqual(self.engine.get_total(cur), 50.0)

    def test_04_old_json_without_loyalty_defaults_50(self):
        """旧 scores_json 无 loyalty 键 → 读出来补 50，不报错。"""
        old_json = '{"trust": 70.0, "intimacy": 60.0}'
        self.db.execute(
            "INSERT INTO current_affection(person_id, scores_json, total_score) "
            "VALUES(:p, :j, 65.0)",
            {"p": "legacy", "j": old_json},
        )
        cur = self.engine.get_current("legacy")
        self.assertAlmostEqual(cur["trust"], 70.0)
        self.assertAlmostEqual(cur["loyalty"], 50.0)

    def test_05_comply_keyword_raises_loyalty(self):
        """对方发来顺从类关键词 → 忠诚度上升。"""
        _make_person(self.db, "p3")
        before = self.engine.get_loyalty("p3")
        r = self.engine.update_from_message(
            "p3",
            {"content": "好好好，我都听你的，这次是我错了", "direction": "received"},
        )
        self.assertGreater(r["loyalty"], before)
        # 结果持久化
        self.assertGreater(self.engine.get_loyalty("p3"), before)

    def test_06_defy_keyword_lowers_loyalty(self):
        """对方发来违抗类关键词 → 忠诚度下降。"""
        _make_person(self.db, "p4")
        # 先拉到 60 基线，避免从 50 下降不明显时误判
        self.engine.add_loyalty_bonus("p4", 10.0, "基线")
        base = self.engine.get_loyalty("p4")
        r = self.engine.update_from_message(
            "p4",
            {"content": "凭什么要我改？你管不着", "direction": "received"},
        )
        self.assertLess(r["loyalty"], base)

    def test_07_loyalty_independent_from_affection(self):
        """顺从消息主要动忠诚，不应把好感总分推高（关键词不碰好感维）。"""
        _make_person(self.db, "p5")
        aff_before = self.engine.get_total(self.engine.get_current("p5"))
        self.engine.update_from_message(
            "p5",
            {"content": "好好好我听你的", "direction": "received"},
            emotion={"label": "neutral", "intensity": 0.0},
        )
        aff_after = self.engine.get_total(self.engine.get_current("p5"))
        # 顺从话术不承载好感关键词，总分应基本不动（±0.5 内）
        self.assertLess(abs(aff_after - aff_before), 0.5)

    def test_08_add_loyalty_bonus_only_touches_loyalty(self):
        """反制线任务奖励只加忠诚，不动好感总分。"""
        _make_person(self.db, "p6")
        aff_before = self.engine.get_total(self.engine.get_current("p6"))
        self.engine.add_loyalty_bonus("p6", 12.0, reason="反制线任务完成")
        self.assertAlmostEqual(self.engine.get_loyalty("p6"), 62.0)
        aff_after = self.engine.get_total(self.engine.get_current("p6"))
        self.assertAlmostEqual(aff_after, aff_before)  # 好感纹丝不动
        # 事件落库
        ev = self.engine.get_recent_events("p6")
        self.assertTrue(any("反制线" in e["reason"] for e in ev))

    def test_09_add_loyalty_bonus_clamped(self):
        """忠诚奖励封顶 100。"""
        _make_person(self.db, "p7")
        self.engine.add_loyalty_bonus("p7", 200.0)
        self.assertLessEqual(self.engine.get_loyalty("p7"), 100.0)

    def test_10_loyalty_independent_decay(self):
        """忠诚度走独立衰减率（0.012），不是好感的 0.005。"""
        _make_person(self.db, "p8")
        scores = {d: 50.0 for d in self.engine.WEIGHTS}
        scores["loyalty"] = 80.0
        self.engine._save_current("p8", scores)
        # 插入 30 天前的消息触发沉默衰减
        self.db.execute(
            "INSERT INTO chat_messages(person_id, direction, content, created_at) "
            "VALUES(:p, 'received', 'hi', datetime('now','-30 days'))",
            {"p": "p8"},
        )
        self.engine.apply_daily_decay("p8")
        after = self.engine.get_loyalty("p8")
        # 向 50 回归：80 + (50-80)*0.012 = 79.64
        self.assertGreater(after, 50.0)
        self.assertLess(after, 80.0)
        # 若误用好感的 0.005，只降 0.15；用独立的 0.012 应降约 0.36
        self.assertGreater(80.0 - after, 0.25,
                           f"忠诚度衰减比例应为独立值，实际下降 {80.0-after:.3f}")

    def test_11_low_loyalty_no_auto_recover(self):
        """低忠诚度不会因衰减自动回升。"""
        _make_person(self.db, "p9")
        scores = {d: 50.0 for d in self.engine.WEIGHTS}
        scores["loyalty"] = 20.0
        self.engine._save_current("p9", scores)
        self.db.execute(
            "INSERT INTO chat_messages(person_id, direction, content, created_at) "
            "VALUES(:p, 'received', 'hi', datetime('now','-30 days'))",
            {"p": "p9"},
        )
        self.engine.apply_daily_decay("p9")
        self.assertAlmostEqual(self.engine.get_loyalty("p9"), 20.0, places=1)

    def test_12_reset_resets_loyalty(self):
        """reset 把忠诚度一并归 50。"""
        _make_person(self.db, "p10")
        self.engine.add_loyalty_bonus("p10", 20.0)
        self.engine.reset("p10")
        self.assertAlmostEqual(self.engine.get_loyalty("p10"), 50.0)
        for v in self.engine.get_current("p10").values():
            self.assertEqual(v, 50.0)

    def test_13_route_default_guide(self):
        """未设置路线时缺省 guide。"""
        self.assertEqual(self.engine.get_route("anyone"), "guide")

    def test_14_set_route_counter(self):
        """set_route 后 get_route 返回 counter；非法值被拒。"""
        self.engine.set_route("p11", "counter")
        self.assertEqual(self.engine.get_route("p11"), "counter")
        self.engine.set_route("p11", "bogus")  # 非法值不覆盖
        self.assertEqual(self.engine.get_route("p11"), "counter")


class TestDualTrack(unittest.TestCase):
    """services.dual_track 纯函数契约。"""

    def test_01_route_weights_two_sets_seven_dims(self):
        self.assertIn("counter", ROUTE_WEIGHTS)
        self.assertIn("guide", ROUTE_WEIGHTS)
        for route in ("counter", "guide"):
            for k in REWARD_KEYS:
                self.assertIn(k, ROUTE_WEIGHTS[route],
                              f"{route} 缺权重键 {k}")

    def test_02_counter_weights_loyalty_heavy(self):
        """反制线忠诚权重大、引导线好感权重大。"""
        self.assertGreater(ROUTE_WEIGHTS["counter"]["loyalty"],
                           ROUTE_WEIGHTS["guide"]["loyalty"])
        self.assertGreater(ROUTE_WEIGHTS["guide"]["affection"],
                           ROUTE_WEIGHTS["counter"]["affection"])

    def test_03_resolve_route_counter_from_manipulation(self):
        mo = {"triggered": True,
              "manipulation_patterns": [{"key": "gaslighting"}],
              "counter_strategies": [{"principle": "x"}]}
        self.assertEqual(resolve_route(None, mo), "counter")

    def test_04_resolve_route_counter_from_persona(self):
        persona = {"psychology": {"dark_triad_tendencies": {"操纵": True}}}
        self.assertEqual(resolve_route(persona, None), "counter")

    def test_05_resolve_route_counter_from_goal(self):
        mo = {"triggered": False, "user_goal": "反制驯服"}
        self.assertEqual(resolve_route(None, mo), "counter")

    def test_06_resolve_route_guide_default(self):
        self.assertEqual(resolve_route(None, None), "guide")
        self.assertEqual(resolve_route({}, None), "guide")
        self.assertEqual(resolve_route(None, {"triggered": False}), "guide")
        # 正常画像（无操纵标签）→ guide
        persona = {"psychology": {"dark_triad_tendencies": {"操纵": False}}}
        self.assertEqual(resolve_route(persona, None), "guide")

    def test_07_main_metric(self):
        self.assertEqual(main_metric("counter"), "loyalty")
        self.assertEqual(main_metric("guide"), "affection")
        self.assertEqual(main_metric("unknown"), "affection")

    def test_08_extend_reward_has_all_keys(self):
        out = extend_reward({}, "guide")
        for k in REWARD_KEYS:
            self.assertIn(k, out)

    def test_09_extend_reward_does_not_mutate_input(self):
        reward = {"total": 10.0}
        extend_reward(reward, "counter")
        # 入参不被污染
        self.assertNotIn("loyalty", reward)
        self.assertNotIn("trust", reward)
        self.assertEqual(reward, {"total": 10.0})

    def test_10_extend_reward_route_difference(self):
        """同一笔奖励，反制线给忠诚的份额明显大于引导线。"""
        c = extend_reward({"total": 10.0}, "counter")
        g = extend_reward({"total": 10.0}, "guide")
        self.assertGreater(c["loyalty"], g["loyalty"])
        self.assertGreater(g["affection"], c["affection"])

    def test_11_extend_reward_explicit_key_passthrough(self):
        """显式给出的维度原样透传，其余维度按路线权重扩展。"""
        out = extend_reward({"affection": 7.0, "total": 10.0}, "counter")
        self.assertAlmostEqual(out["affection"], 7.0)
        # loyalty 未显式给 → 10 * counter 权重 0.40 = 4.0
        self.assertAlmostEqual(out["loyalty"], 4.0)

    def test_12_extend_reward_no_base_zero(self):
        """无标量基数、无显式维度 → 缺键补 0.0。"""
        out = extend_reward({}, "guide")
        self.assertEqual(sum(abs(v) for v in out.values()), 0.0)

    def test_13_extend_reward_none_input(self):
        self.assertEqual(extend_reward(None, "counter"),
                         {k: 0.0 for k in REWARD_KEYS})


if __name__ == "__main__":
    unittest.main(verbosity=2)
