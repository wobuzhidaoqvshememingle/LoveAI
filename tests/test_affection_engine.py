# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - AffectionEngine 游戏化重构单元测试

覆盖：
  - 18 维向后兼容（旧 scores_json 可读）
  - 10 个分数区间定义与边界
  - 阶段映射（0-9，每档 10 分）
  - 消息驱动更新 + 变化原因落库
  - 阶段跃迁日志
  - 进度可视化接口
  - 单日单维上限
  - 衰减机制（只衰减 >50 的维度）
  - 重大事件（表白/吵架/分手）
  - 双向奔赴指标（主动发起率 / 投入比）
  - 攻略成功综合判定（80+ 稳定 + 主动>40% + 平衡 + 冲突可修复）
  - 推荐动作 / 重置 / 奖励
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


def _make_person(db, pid):
    db.execute("INSERT INTO persons(id, nickname) VALUES(:p, :n)",
               {"p": pid, "n": pid})


class TestAffectionEngineV3(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_aff_v3_")
        self.db = Database(os.path.join(self.tmpdir, "test.db"))
        self.db.init_tables()
        self.engine = AffectionEngine(self.db)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 维度与向后兼容
    # ------------------------------------------------------------------ #
    def test_01_weights_18_dimensions(self):
        """WEIGHTS 仍为 18 维（向后兼容，不能删维度）。"""
        self.assertEqual(len(AffectionEngine.WEIGHTS), 18)
        self.assertEqual(len(AffectionEngine.DIMENSION_NAMES), 18)

    def test_02_backward_compat_old_json(self):
        """旧 scores_json 缺新维度 / 多旧维度都能正常读取。"""
        # 模拟旧版数据：只存了 18 维里的一部分，且加了一个已废弃维度
        old_json = '{"trust": 70.0, "intimacy": 60.0, "obsolete_old_dim": 99.0}'
        self.db.execute(
            "INSERT INTO current_affection(person_id, scores_json, total_score) "
            "VALUES(:p, :j, 65.0)",
            {"p": "legacy_p", "j": old_json},
        )
        scores = self.engine.get_current("legacy_p")
        self.assertAlmostEqual(scores["trust"], 70.0)
        self.assertAlmostEqual(scores["intimacy"], 60.0)
        # 缺的维度补 50
        self.assertAlmostEqual(scores["chat_resonance"], 50.0)
        # 多的维度被忽略
        self.assertNotIn("obsolete_old_dim", scores)

    def test_03_default_total_50(self):
        """全 50 时总分恰为 50。"""
        scores = {d: 50.0 for d in AffectionEngine.WEIGHTS}
        self.assertEqual(self.engine.get_total(scores), 50.0)

    def test_04_total_range(self):
        """总分始终落在 [0,100]。"""
        low = {d: 0.0 for d in AffectionEngine.WEIGHTS}
        high = {d: 100.0 for d in AffectionEngine.WEIGHTS}
        self.assertGreaterEqual(self.engine.get_total(low), 0.0)
        self.assertLessEqual(self.engine.get_total(high), 100.0)

    # ------------------------------------------------------------------ #
    # 区间定义（10 个区间，每档 10 分）
    # ------------------------------------------------------------------ #
    def test_05_intervals_count(self):
        """应有 10 个区间。"""
        self.assertEqual(len(AffectionEngine.INTERVALS), 10)

    def test_06_interval_cover_full_range(self):
        """区间应完整覆盖 0-100 且每档 10 分。"""
        for i, iv in enumerate(AffectionEngine.INTERVALS):
            self.assertEqual(iv["stage"], i)
            self.assertAlmostEqual(iv["min"], i * 10.0)
            self.assertAlmostEqual(iv["max"], (i + 1) * 10.0)

    def test_07_interval_names(self):
        """10 个区间名称正确。"""
        names = [iv["name"] for iv in AffectionEngine.INTERVALS]
        self.assertEqual(names, [
            "陌生人", "认识", "有点印象", "不反感", "普通朋友",
            "有好感", "暧昧中", "热恋", "深度绑定", "灵魂伴侣",
        ])

    def test_08_interval_each_has_metadata(self):
        """每个区间都要有 含义/关系状态/解锁/对方表现/用户动作/风险。"""
        for iv in AffectionEngine.INTERVALS:
            for k in ("meaning", "relation_state", "unlock",
                      "typical_behavior", "user_action", "risk"):
                self.assertIn(k, iv, f"区间 {iv['name']} 缺字段 {k}")
                self.assertTrue(iv[k], f"区间 {iv['name']} 字段 {k} 为空")

    def test_09_get_interval_boundaries(self):
        """区间边界正确（上界归右）。"""
        self.assertEqual(AffectionEngine.get_interval(5)["name"], "陌生人")
        self.assertEqual(AffectionEngine.get_interval(10)["name"], "认识")
        self.assertEqual(AffectionEngine.get_interval(55)["name"], "有好感")
        self.assertEqual(AffectionEngine.get_interval(80)["name"], "深度绑定")
        self.assertEqual(AffectionEngine.get_interval(99)["name"], "灵魂伴侣")

    # ------------------------------------------------------------------ #
    # 阶段映射 0-9
    # ------------------------------------------------------------------ #
    def test_10_stage_mapping(self):
        """get_stage 应按 10 分一档返回 0-9。"""
        self.assertEqual(AffectionEngine.get_stage(5), 0)
        self.assertEqual(AffectionEngine.get_stage(50), 5)
        self.assertEqual(AffectionEngine.get_stage(75), 7)
        self.assertEqual(AffectionEngine.get_stage(95), 9)

    # ------------------------------------------------------------------ #
    # 消息驱动 + 变化原因
    # ------------------------------------------------------------------ #
    def test_11_positive_message_raises_score(self):
        """收到积极消息总分应上升。"""
        _make_person(self.db, "p_pos")
        before = self.engine.get_total(self.engine.get_current("p_pos"))
        r = self.engine.update_from_message(
            "p_pos",
            {"content": "想你了宝贝，今天给你点了外卖", "direction": "received"},
            emotion={"label": "love", "intensity": 0.9},
        )
        self.assertGreater(r["total_score"], before)
        self.assertTrue(r["reasons"], "应返回变化原因列表")

    def test_12_negative_message_lowers_score(self):
        """收到消极消息总分应下降。"""
        _make_person(self.db, "p_neg")
        before = self.engine.get_total(self.engine.get_current("p_neg"))
        r = self.engine.update_from_message(
            "p_neg",
            {"content": "别烦我，关你什么事", "direction": "received"},
            emotion={"label": "angry", "intensity": 0.9},
        )
        self.assertLess(r["total_score"], before)

    def test_13_change_reasons_persisted(self):
        """变化原因应写入 affection_events 表。"""
        _make_person(self.db, "p_evt")
        self.engine.update_from_message(
            "p_evt",
            {"content": "想你了", "direction": "received"},
            emotion={"label": "love", "intensity": 0.8},
        )
        events = self.engine.get_recent_events("p_evt")
        self.assertGreaterEqual(len(events), 1)
        e = events[0]
        self.assertTrue(e["reason"])
        self.assertIn(e["dim"], ("__total__",))
        # reason 应包含具体触发词
        joined = " ".join(str(x) for x in events)
        self.assertTrue(("想你" in joined) or ("love" in joined),
                        f"变化原因应记录具体触发词: {joined}")

    def test_14_stage_up_logged(self):
        """跨过区间边界时应写 affection_stage_log。"""
        _make_person(self.db, "p_up")
        # 连续发大量积极消息，把分数从 50 推过 60
        for _ in range(15):
            self.engine.update_from_message(
                "p_up",
                {"content": "我爱你宝贝，今天陪你吃饭，给你点了奶茶，注意身体别太累",
                 "direction": "received"},
                emotion={"label": "love", "intensity": 1.0},
            )
        rows = self.db.fetch_all(
            "SELECT stage_from, stage_to FROM affection_stage_log WHERE person_id=:p",
            {"p": "p_up"},
        )
        self.assertGreaterEqual(len(rows), 1, "应记录至少一次阶段跃迁")

    # ------------------------------------------------------------------ #
    # 进度可视化
    # ------------------------------------------------------------------ #
    def test_15_progress_interface(self):
        """get_progress 应返回 UI 所需全部字段。"""
        _make_person(self.db, "p_prog")
        p = self.engine.get_progress("p_prog")
        for k in ("total_score", "stage", "interval_name", "points_to_next",
                  "next_interval_name", "progress_in_interval", "recommended_actions",
                  "recent_events", "trend_7d", "trend_30d", "risk", "unlock"):
            self.assertIn(k, p, f"get_progress 缺字段 {k}")
        # 距下一阶段应为正数（非最高区间）
        self.assertGreater(p["points_to_next"], 0)

    def test_16_recommended_actions_by_interval(self):
        """不同区间推荐动作不同。"""
        a = self.engine.get_recommended_actions(5)   # 陌生人
        b = self.engine.get_recommended_actions(75)  # 热恋
        c = self.engine.get_recommended_actions(85)  # 深度绑定
        self.assertNotEqual(a[0], b[0])
        self.assertNotEqual(b[0], c[0])

    # ------------------------------------------------------------------ #
    # 单日上限 / 衰减
    # ------------------------------------------------------------------ #
    def test_17_daily_cap(self):
        """单日单维变化不应超过 ±10。"""
        _make_person(self.db, "p_cap")
        r = self.engine.update_from_message(
            "p_cap",
            {"content": "随便聊聊", "direction": "received"},
            emotion={"label": "neutral", "intensity": 0.1},
            analysis={"affection_hints": {"emotional_value": 100.0}},
        )
        self.assertLessEqual(abs(r["applied"]["emotional_value"]), 10.0)

    def test_18_decay_only_above_50(self):
        """衰减只让 >50 的维度向 50 回归，<50 的维度不动。"""
        _make_person(self.db, "p_decay")
        # 手动把某个维度拉高、某个维度打低
        scores = {d: 50.0 for d in AffectionEngine.WEIGHTS}
        scores["intimacy"] = 80.0
        scores["trust"] = 20.0
        self.engine._save_current("p_decay", scores)
        # 插入一条 30 天前的消息，模拟长期沉默
        self.db.execute(
            "INSERT INTO chat_messages(person_id, direction, content, created_at) "
            "VALUES(:p, 'received', 'hi', datetime('now','-30 days'))",
            {"p": "p_decay"},
        )
        self.engine.apply_daily_decay("p_decay")
        after = self.engine.get_current("p_decay")
        # intimacy 应被拉低（向 50 回归 0.5%）
        self.assertLess(after["intimacy"], 80.0)
        self.assertGreater(after["intimacy"], 50.0)
        # trust 应保持 20（不自动回升）
        self.assertAlmostEqual(after["trust"], 20.0, places=1)

    # ------------------------------------------------------------------ #
    # 重大事件
    # ------------------------------------------------------------------ #
    def test_19_milestone_quarrel(self):
        """吵架应显著拉低信任/安全感/情绪价值。"""
        _make_person(self.db, "p_mil")
        before = self.engine.get_total_with_bonus("p_mil", self.engine.get_current("p_mil"))
        m = self.engine.record_milestone("p_mil", "quarrel", "测试吵架")
        self.assertTrue(m["ok"])
        self.assertLess(m["total_after"], before)

    def test_20_milestone_confess(self):
        """表白应显著拉高亲密/浪漫。"""
        _make_person(self.db, "p_conf")
        before = self.engine.get_total_with_bonus("p_conf", self.engine.get_current("p_conf"))
        m = self.engine.record_milestone("p_conf", "confess_love", "我喜欢你")
        self.assertGreater(m["total_after"], before)

    # ------------------------------------------------------------------ #
    # 双向奔赴指标
    # ------------------------------------------------------------------ #
    def test_21_proactive_ratio(self):
        """对方主动发起对话的会话占比（按 30 分钟间隔切会话）。"""
        _make_person(self.db, "p_act")
        # 每小时间隔一条，交替方向 → 每条都是独立会话，对方开 3 场
        base = datetime(2026, 9, 1, 9, 0, 0)
        msgs = [
            ("sent", "在吗"), ("received", "在的刚下班"),
            ("sent", "忙吗"), ("received", "不忙你呢"),
            ("sent", "想你了"), ("received", "我也想你"),
        ]
        for i, (d, c) in enumerate(msgs):
            ts = (base + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
            self.db.execute(
                "INSERT INTO chat_messages(person_id, direction, content, created_at) "
                "VALUES(:p, :d, :c, :ts)",
                {"p": "p_act", "d": d, "c": c, "ts": ts},
            )
        ratio = self.engine.get_proactive_ratio("p_act")
        # 6 场会话，对方开了后 3 场（received 开头的）
        self.assertAlmostEqual(ratio, 3/6, places=2)

    def test_22_investment_balance(self):
        """投入比 = received/sent。"""
        _make_person(self.db, "p_inv")
        for _ in range(30):
            self.db.execute(
                "INSERT INTO chat_messages(person_id, direction, content) VALUES(:p,'sent','x')",
                {"p": "p_inv"},
            )
        for _ in range(30):
            self.db.execute(
                "INSERT INTO chat_messages(person_id, direction, content) VALUES(:p,'received','y')",
                {"p": "p_inv"},
            )
        bal = self.engine.get_investment_balance("p_inv")
        self.assertAlmostEqual(bal, 1.0, places=1)

    # ------------------------------------------------------------------ #
    # 攻略成功判定
    # ------------------------------------------------------------------ #
    def test_23_success_not_100_required(self):
        """成功判定不应要求 100 分；80+ 且稳定 + 双向奔赴才算成功。"""
        # 构造一个"健康关系"：高分 + 对方主动 + 平衡 + 无未修复冲突
        _make_person(self.db, "p_ok")
        # 手工把分数拉到 82
        scores = {d: 82.0 for d in AffectionEngine.WEIGHTS}
        self.engine._save_current("p_ok", scores)
        # 构造 14 天稳定快照（都在 80-84 之间波动）
        for i in range(14):
            self.db.execute(
                "DELETE FROM affection_snapshots WHERE person_id=:p AND date=:d",
                {"p": "p_ok", "d": f"2026-09-{15+i:02d}"},
            )
            self.db.execute(
                "INSERT INTO affection_snapshots(person_id, date, scores_json, total_score) "
                "VALUES(:p, :d, '{}', :t)",
                {"p": "p_ok", "d": f"2026-09-{15+i:02d}", "t": 81.0 + (i % 3)},
            )
        # 对方主动 > 40%：4 场会话里对方开 2 场（每小时间隔切新会话）
        base = datetime(2026, 9, 1, 10, 0, 0)
        msgs = [
            ("sent", "a"), ("received", "b"),     # 会话1：我开
            ("received", "c"), ("sent", "d"),     # 会话2：对方开
            ("sent", "e"), ("received", "f"),     # 会话3：我开
            ("received", "g"), ("sent", "h"),     # 会话4：对方开
        ]
        for i, (d, c) in enumerate(msgs):
            # 每两条消息后跨 1 小时切新会话；同会话内间隔 5 分钟
            hour_block = i // 2
            minute_off = (i % 2) * 5
            ts = (base + timedelta(hours=hour_block, minutes=minute_off)).strftime("%Y-%m-%d %H:%M:%S")
            self.db.execute(
                "INSERT INTO chat_messages(person_id, direction, content, created_at) "
                "VALUES(:p,:d,:c,:ts)",
                {"p": "p_ok", "d": d, "c": c, "ts": ts},
            )
        res = self.engine.evaluate_courtship_success("p_ok")
        self.assertIn("success", res)
        self.assertEqual(res["checks"]["score_ge_80"], True)
        self.assertEqual(res["checks"]["stable_14d"], True)
        self.assertGreaterEqual(res["checks"]["proactive_gt_40"], True)
        self.assertEqual(res["checks"]["in_balance"], True)
        self.assertEqual(res["checks"]["conflict_repairable"], True)
        self.assertTrue(res["success"], f"健康关系应判定成功: {res}")

    def test_24_success_fail_for_low_score(self):
        """低分关系不应判定成功。"""
        _make_person(self.db, "p_low")
        res = self.engine.evaluate_courtship_success("p_low")
        self.assertFalse(res["success"])
        self.assertFalse(res["checks"]["score_ge_80"])

    def test_25_success_fails_one_sided(self):
        """单方面舔（对方主动率 <40%）即使分高也不算成功。"""
        _make_person(self.db, "p_one")
        scores = {d: 85.0 for d in AffectionEngine.WEIGHTS}
        self.engine._save_current("p_one", scores)
        # 14 天稳定快照
        for i in range(14):
            self.db.execute(
                "INSERT INTO affection_snapshots(person_id, date, scores_json, total_score) "
                "VALUES(:p, :d, '{}', :t)",
                {"p": "p_one", "d": f"2026-09-{15+i:02d}", "t": 84.0 + (i % 2)},
            )
        # 我发 100 条，对方只回 5 条 → 主动率低、投入比失衡
        for _ in range(100):
            self.db.execute(
                "INSERT INTO chat_messages(person_id, direction, content) VALUES(:p,'sent','x')",
                {"p": "p_one"},
            )
        for _ in range(5):
            self.db.execute(
                "INSERT INTO chat_messages(person_id, direction, content) VALUES(:p,'received','y')",
                {"p": "p_one"},
            )
        res = self.engine.evaluate_courtship_success("p_one")
        self.assertFalse(res["success"], "单方面舔不应判定成功")
        self.assertFalse(res["checks"]["proactive_gt_40"])

    # ------------------------------------------------------------------ #
    # 重置 / 奖励 / 快照
    # ------------------------------------------------------------------ #
    def test_26_reset(self):
        _make_person(self.db, "p_rst")
        self.engine.update_from_message(
            "p_rst", {"content": "想你了宝贝", "direction": "received"},
            emotion={"label": "love", "intensity": 0.9},
        )
        self.engine.reset("p_rst")
        for v in self.engine.get_current("p_rst").values():
            self.assertEqual(v, 50.0)

    def test_27_add_bonus_logs(self):
        _make_person(self.db, "p_bonus")
        before = self.engine.get_total(self.engine.get_current("p_bonus"))
        self.engine.add_bonus("p_bonus", 10.0, reason="完成测试任务")
        after = self.engine.get_total(self.engine.get_current("p_bonus"))
        self.assertGreater(after, before)
        events = self.engine.get_recent_events("p_bonus")
        self.assertTrue(any("任务" in e["reason"] for e in events))

    def test_28_snapshot_history(self):
        _make_person(self.db, "p_snap")
        self.engine.update_from_message(
            "p_snap", {"content": "想你了", "direction": "received"},
            emotion={"label": "love", "intensity": 0.9},
        )
        self.engine.take_daily_snapshot("p_snap")
        hist = self.engine.get_history("p_snap")
        self.assertGreaterEqual(len(hist), 1)
        self.assertIn("date", hist[0])
        self.assertIn("total_score", hist[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
