# -*- coding: utf-8 -*-
"""BoostGlidePlanner（长期助推-滑翔规划器）单测。

覆盖：
  (a) 长期值：短期诱饵 vs 长期更优 —— 短视(gamma=0)选诱饵，长期规划器选蓄势；
  (b) 四模式：boost / glide / dive 按 P/M/A 条件切换；
  (c) CMDP 硬闸：mainline_impact<0 一律否决，Q 再高也不选；
  (d) 接线：generate_recommended_tasks 真返回含 mode/q/rationale 等新字段。
"""
import os
import shutil
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.boost_glide_planner import (  # noqa: E402
    BoostGlidePlanner, build_decision_context, raw_to_task_cand,
)
from services.decision_contract import (  # noqa: E402
    MODE_BOOST, MODE_DIVE, MODE_GLIDE,
    DecisionContext, RelationState, TaskCandidate,
)


def _myopic_order(cands, state):
    """短视基线：gamma=0（只看即时期望奖励）。返回 [(id, immediate)] 按分降序。"""
    p = BoostGlidePlanner(config={"gamma": 0.0})
    ctx = DecisionContext(person_id="t", state=state, candidates=cands, horizon=12)
    feats = [f for f in p._score_all(ctx) if f["gate"].passed]
    feats.sort(key=lambda f: f["q"], reverse=True)
    return [(f["cand"].id, f["q"]) for f in feats]


class TestLongTermValue(unittest.TestCase):
    """(a) 长期值函数：SMDP Q/V rollout 必须真实多步计算。"""

    def setUp(self):
        self.state = RelationState(P=0.25, M=0.25, A=0.6)
        # A：即时大、耗时长（吞视野）的"短期诱饵"
        self.A = TaskCandidate(id="A_decoy", title="速推",
                               reward={"affection": 0.8, "trust": 0.0},
                               success_prob=0.9, duration=4)
        # B：即时小、短时长、持续建 P/M 的"蓄势"
        self.B = TaskCandidate(id="B_build", title="建信任",
                              reward={"affection": 0.06, "trust": 0.35},
                              success_prob=0.95, duration=1)
        # C：高价值主线收口（P 高时才划算）
        self.C = TaskCandidate(id="C_dive", title="定关系",
                              reward={"affection": 0.3, "mainline": 0.8},
                              success_prob=0.7, duration=2)
        self.cands = [self.A, self.B, self.C]

    def test_myopic_picks_decoy(self):
        order = _myopic_order(self.cands, self.state)
        self.assertEqual(order[0][0], "A_decoy",
                         f"短视基线应选诱饵 A，实际 {order}")

    def test_long_term_picks_build(self):
        p = BoostGlidePlanner()
        ctx = DecisionContext(person_id="t", state=self.state,
                              candidates=self.cands, horizon=12)
        pa = p.decide(ctx)
        self.assertEqual(pa.candidate_id, "B_build",
                         f"长期规划器应选蓄势 B，实际选了 {pa.candidate_id}")

    def test_q_is_longer_than_immediate(self):
        """Q 必须含折扣未来项：Q > 纯即时奖励（rollout 末端价值为正）。"""
        p = BoostGlidePlanner()
        ctx = DecisionContext(person_id="t", state=self.state,
                              candidates=self.cands, horizon=12)
        feats = {f["cand"].id: f for f in p._score_all(ctx)}
        # 蓄势动作的 v_after（未来值）必须显著为正，而非 0
        self.assertGreater(feats["B_build"]["v_after"], 0.3)
        # 长期 Q 远大于即时（对比 gamma=0 的即时分）
        self.assertGreater(feats["B_build"]["q"], 0.5)


class TestModes(unittest.TestCase):
    """(b) 四模式状态机：boost / glide / dive 切换。"""

    def _mode(self, state, cand):
        p = BoostGlidePlanner()
        return p._mode_for(state, cand, p._gate(cand, state))

    def test_boost_when_low_p(self):
        state = RelationState(P=0.3, M=0.3, A=0.6)
        cand = TaskCandidate(id="x", title="x", mainline_impact=0.0, success_prob=0.7)
        self.assertEqual(self._mode(state, cand), MODE_BOOST)

    def test_glide_when_ready(self):
        # P 足、M 足、A 足，且非主线高确定性动作 → glide
        state = RelationState(P=0.6, M=0.5, A=0.7)
        cand = TaskCandidate(id="x", title="x", mainline_impact=0.0, success_prob=0.7)
        self.assertEqual(self._mode(state, cand), MODE_GLIDE)

    def test_dive_on_mature_window(self):
        # P/M 双高 + 主线高确定性动作 → dive
        state = RelationState(P=0.7, M=0.6, A=0.7)
        cand = TaskCandidate(id="x", title="x", mainline_impact=0.4, success_prob=0.8)
        self.assertEqual(self._mode(state, cand), MODE_DIVE)


class TestCMDPGate(unittest.TestCase):
    """(c) 主线硬约束：伤主线一律否决。"""

    def test_bad_mainline_rejected_even_if_high_reward(self):
        state = RelationState(P=0.6, M=0.6, A=0.7)
        good = TaskCandidate(id="good", title="好", reward={"affection": 0.2},
                             mainline_impact=0.1, success_prob=0.8)
        bad = TaskCandidate(id="bad", title="伤主线",
                            reward={"affection": 0.9, "mainline": 0.9},  # 极高即时收益
                            mainline_impact=-0.6, success_prob=0.9)
        p = BoostGlidePlanner()
        ctx = DecisionContext(person_id="t", state=state,
                              candidates=[good, bad], horizon=12)
        # 硬闸：bad 不通过
        gate = p._gate(bad, state)
        self.assertFalse(gate.passed)
        # recommend 从不返回伤主线候选
        out = p.recommend(ctx, top_n=5)
        ids = [pa.candidate_id for pa in out]
        self.assertNotIn("bad", ids)
        self.assertIn("good", ids)

    def test_all_gated_returns_empty(self):
        state = RelationState(P=0.5, M=0.5, A=0.5)
        bad = TaskCandidate(id="b1", title="x", mainline_impact=-0.3)
        p = BoostGlidePlanner()
        ctx = DecisionContext(person_id="t", state=state, candidates=[bad], horizon=12)
        self.assertEqual(p.recommend(ctx, top_n=3), [])


class TestWiring(unittest.TestCase):
    """(d) 真实接线进 TaskEngine。"""

    def setUp(self):
        from models.database import Database
        from services.affection_engine import AffectionEngine
        from services.task_engine import TaskEngine
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_bgp_")
        self.db = Database(os.path.join(self.tmpdir, "w.db"))
        self.db.init_tables()
        self.affection = AffectionEngine(self.db)
        self.engine = TaskEngine(self.db, self.affection, guide_engine=None,
                                 twin=None, signals=None)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generate_returns_new_fields(self):
        out = self.engine.generate_recommended_tasks("p1", top_n=3)
        self.assertGreater(len(out), 0)
        first = out[0]
        # 新规划器字段必须存在
        for field in ("score", "mode", "rationale", "q", "v_after",
                      "expected_reward", "gate_passed"):
            self.assertIn(field, first, f"缺字段 {field}: {list(first.keys())}")
        self.assertIn(first["mode"], (MODE_BOOST, MODE_GLIDE, MODE_DIVE, "diverge"))


class TestContextBuilder(unittest.TestCase):
    """桥接函数：raw task → DecisionContext 稳健降级。"""

    def test_raw_to_cand_defaults(self):
        raw = {"title": "测试", "category": "关系维护", "difficulty": "普通", "base": 1.0}
        c = raw_to_task_cand(raw, 0)
        self.assertEqual(c.title, "测试")
        self.assertEqual(c.reward["affection"], 0.10)
        self.assertGreaterEqual(c.success_prob, 0.0)

    def test_build_context_no_engine(self):
        ctx = build_decision_context("p", [{"title": "t"}], affection=None, horizon=8)
        self.assertEqual(len(ctx.candidates), 1)
        self.assertEqual(ctx.horizon, 8)
        # P/M/A 中性默认，不抛
        self.assertTrue(0.0 <= ctx.state.P <= 1.0)


class TestWholeSetCompletion(unittest.TestCase):
    """整集全任务达成（HTN 主链解锁 + 依赖顺序）。"""

    def _chain(self):
        return [
            TaskCandidate(id="s1", title="完善档案", level="small", deps=[],
                          duration=1, reward={"affection": 0.1, "trust": 0.2},
                          success_prob=0.9, mainline_impact=0.0),
            TaskCandidate(id="s2", title="日常关心", level="small", deps=[],
                          duration=1, reward={"affection": 0.1, "trust": 0.15},
                          success_prob=0.9, mainline_impact=0.0),
            TaskCandidate(id="m1", title="约见面", level="mid", deps=["s1"],
                          duration=2, reward={"affection": 0.2, "mainline": 0.4},
                          success_prob=0.75, mainline_impact=0.25),
            TaskCandidate(id="main", title="确定关系", level="main", deps=["m1", "s2"],
                          duration=2, reward={"affection": 0.2, "mainline": 0.7},
                          success_prob=0.7, mainline_impact=0.5),
        ]

    def _simulate(self, planner, steps=12):
        remaining = {c.id: c for c in self._chain()}
        done, failures = [], 0
        for _ in range(steps):
            if not remaining:
                break
            ctx = DecisionContext(person_id="t",
                                  state=RelationState(P=0.4, M=0.4, A=0.6),
                                  candidates=list(remaining.values()),
                                  horizon=20, now_index=len(done))
            pa = planner.decide(ctx)
            unmet = [d for d in pa.candidate.deps if d in remaining]
            if unmet:
                failures += 1            # 前置未完成就选了 → 白做
            else:
                done.append(pa.candidate_id)
                del remaining[pa.candidate_id]
        return done, failures

    def test_whole_set_100pct_completion(self):
        done, failures = self._simulate(BoostGlidePlanner())
        self.assertEqual(len(done), 4, f"整集应 100% 达成，实际只完成 {done}")
        self.assertEqual(failures, 0, f"不应有前置未满足的白做步，失败 {failures}")

    def test_htn_order_respected(self):
        done, _ = self._simulate(BoostGlidePlanner())
        # s1 先于 m1；m1 与 s2 先于 main
        self.assertLess(done.index("s1"), done.index("m1"))
        self.assertLess(done.index("m1"), done.index("main"))
        self.assertLess(done.index("s2"), done.index("main"))

    def test_no_htn_shaping_loops(self):
        """对照：关闭依赖就绪塑形后会卡在未就绪主线（复现'整集0%达成'）。"""
        class NoShape(BoostGlidePlanner):
            def _htn_shaping(self, cand, cands):  # type: ignore
                return (0.0, True, [])
        done, failures = self._simulate(NoShape())
        # 无塑形时 main 永远因 m1 未就绪而被反复选中 → 达成不全
        self.assertNotEqual(len(done), 4, "无塑形对照应无法整集达成")


class TestDetourReform(unittest.TestCase):
    """ALG-27 第四类迂回：reform 重构。"""

    def test_both_mainline_conflict_triggers_reform(self):
        c1 = TaskCandidate(id="a", title="A", conflict_tags=["周六晚"],
                           mainline_impact=0.4, success_prob=0.7)
        c2 = TaskCandidate(id="b", title="B", conflict_tags=["周六晚"],
                           mainline_impact=0.35, success_prob=0.7)
        p = BoostGlidePlanner()
        pa = p.recommend(DecisionContext(
            person_id="t", state=RelationState(P=0.6, M=0.5, A=0.7),
            candidates=[c1, c2], horizon=12), top_n=1)[0]
        self.assertEqual(pa.mode, "diverge")
        self.assertEqual(pa.detour["type"], "reform")
        self.assertTrue(pa.detour["heading_kept"])
        self.assertTrue(pa.detour["momentum_kept"])

    def test_one_mainline_conflict_is_stagger(self):
        c1 = TaskCandidate(id="a", title="A", conflict_tags=["周六晚"],
                           mainline_impact=0.4, success_prob=0.7)
        c2 = TaskCandidate(id="c", title="C", conflict_tags=["周六晚"],
                           mainline_impact=0.0, success_prob=0.8)
        p = BoostGlidePlanner()
        pa = p.recommend(DecisionContext(
            person_id="t", state=RelationState(P=0.6, M=0.5, A=0.7),
            candidates=[c1, c2], horizon=12), top_n=1)[0]
        self.assertEqual(pa.detour["type"], "stagger")


class TestPsychAndOpponent(unittest.TestCase):
    """ALG-22 心理适配 + ALG-25 对手建模。"""

    def test_avoidant_reduces_pushy_success(self):
        push = TaskCandidate(id="push", title="冒进",
                             reward={"affection": 0.5, "trust": -0.2},
                             success_prob=0.8)
        p = BoostGlidePlanner()
        st = RelationState(P=0.6, M=0.5, A=0.7)
        secure = p._reaction_factor(st, push, {"other_attachment": "secure",
                                               "self_efficacy": 0.8})
        avoidant = p._reaction_factor(st, push, {"other_attachment": "avoidant",
                                                "self_efficacy": 0.8})
        self.assertLess(avoidant, secure)

    def test_low_efficacy_discounts_long_task(self):
        long_t = TaskCandidate(id="L", title="长任务", duration=3,
                               reward={"affection": 0.2}, success_prob=0.8)
        p = BoostGlidePlanner()
        st = RelationState(P=0.6, M=0.5, A=0.7)
        high = p._reaction_factor(st, long_t, {"other_attachment": "secure",
                                              "self_efficacy": 0.9})
        low = p._reaction_factor(st, long_t, {"other_attachment": "secure",
                                             "self_efficacy": 0.3})
        self.assertLess(low, high)


class TestCreditAssignment(unittest.TestCase):
    """ALG-36 延迟信用分配。"""

    def test_assign_credit_to_unlocking_actions(self):
        p = BoostGlidePlanner()
        cands = [
            TaskCandidate(id="s1", title="前置", deps=[], duration=1,
                          reward={"trust": 0.2}, success_prob=0.9, mainline_impact=0.0),
            TaskCandidate(id="main", title="主线", deps=["s1"], duration=2,
                          reward={"mainline": 0.7}, success_prob=0.7, mainline_impact=0.5),
        ]
        p.recommend(DecisionContext(person_id="t",
                                   state=RelationState(P=0.4, M=0.4, A=0.6),
                                   candidates=cands, horizon=12), top_n=2)
        weights = p.assign_credit({"target": "main", "mainline": True})
        self.assertTrue(any(w["candidate_id"] == "s1" for w in weights),
                        "解锁主线的前置动作应分到信用")
        tot = sum(w["credit"] for w in weights)
        self.assertAlmostEqual(tot, 1.0, places=2)


class TestHorizonAdaptation(unittest.TestCase):
    """视野自适应：迂回吃时间后仍够解锁主链。"""

    def test_effective_horizon_covers_chain(self):
        cands = [TaskCandidate(id=f"t{i}", title="t", duration=3,
                               reward={"affection": 0.1}) for i in range(6)]
        p = BoostGlidePlanner()
        ctx = DecisionContext(person_id="t", state=RelationState(P=0.5, M=0.5, A=0.5),
                              candidates=cands, horizon=8)
        feats = p._score_all(ctx)
        # chain_dur=18 > horizon=8 → 有效视野被抬到覆盖任务链
        self.assertGreater(len(feats), 0)
        # 末端完成奖励应存在（rollout 内 left_main 逻辑不抛）
        self.assertTrue(all("q" in f for f in feats))


class TestPsychWiring(unittest.TestCase):
    """ALG-22 心理适配真实路径：persona → ctx.extras → reaction_factor → 推荐结果。"""

    def setUp(self):
        from models.database import Database
        from services.affection_engine import AffectionEngine
        from services.task_engine import TaskEngine
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_psych_")
        self.db = Database(os.path.join(self.tmpdir, "w.db"))
        self.db.init_tables()
        self.affection = AffectionEngine(self.db)
        self.engine = TaskEngine(self.db, self.affection,
                                 guide_engine=None, twin=None, signals=None)

    def tearDown(self):
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run(self, persona):
        # 固定一个冒进任务（升温试探：高 affection / 负 trust），隔离心理适配效果
        from services.task_engine import TaskEngine as _TE
        self._saved_goals = _TE.STAGE_GOALS
        _TE.STAGE_GOALS = {}   # 去掉主线目标干扰，只测冒进任务
        self.engine._load_persona = lambda pid: persona  # type: ignore
        self.engine.generate_tasks_by_direction = lambda pid, direction: [  # type: ignore
            {"title": "制造独处并试探未来规划", "category": "升温试探",
             "difficulty": "困难", "base": 1.0, "direction": direction, "stage_tags": []},
        ]
        try:
            return self.engine.generate_recommended_tasks("p1", top_n=1)
        finally:
            _TE.STAGE_GOALS = self._saved_goals

    def test_avoidant_lowers_pushy_success(self):
        avoid = self._run({"attachment": {"primary_type": "回避型"}})
        sec = self._run({"attachment": {"primary_type": "安全型"}})
        self.assertTrue(avoid and sec)
        # 回避型对冒进动作成功率打折，应可观测地低于安全型
        self.assertLess(avoid[0]["success_prob"], sec[0]["success_prob"])
        # 且两者都走了新规划器路径（含 mode/rationale 字段）
        self.assertIn("mode", avoid[0])

    def test_no_persona_neutral_default(self):
        self.engine._load_persona = lambda pid: None  # type: ignore
        self.engine.generate_tasks_by_direction = lambda pid, direction: [  # type: ignore
            {"title": "日常关心", "category": "关系维护", "difficulty": "简单",
             "base": 1.0, "direction": direction, "stage_tags": []},
        ]
        out = self.engine.generate_recommended_tasks("p1", top_n=1)
        self.assertTrue(out)
        self.assertIn("success_prob", out[0])
        # 中性结果不报错、字段完整
        self.assertIn("mode", out[0])

    def test_persona_mapping_direct(self):
        """build_decision_context 直接映射 persona → extras。"""
        from services.boost_glide_planner import build_decision_context
        ctx = build_decision_context(
            "p", [{"title": "t", "category": "升温试探"}],
            affection=None, persona={"attachment": {"primary_type": "回避型"}})
        self.assertEqual(ctx.extras["other_profile"]["attachment"], "avoidant")
        ctx2 = build_decision_context(
            "p", [{"title": "t"}], affection=None, persona=None)
        self.assertEqual(ctx2.extras["other_profile"]["attachment"], "secure")
        self.assertEqual(ctx2.extras["self_profile"]["efficacy"], 0.5)


DETOUR_COST = {"equivalent": 0.0, "stagger": 1.0, "buffer": 2.0, "reform": 1.0}


def _simulate(cands, state, horizon=20, max_steps=20):
    """逐步决策仿真：选 done 即移出，返回 (done_ids, detour_types, fails)。"""
    p = BoostGlidePlanner()
    remaining = {c.id: c for c in cands}
    done, detours, fails = [], [], 0
    s = state
    for _ in range(max_steps):
        if not remaining:
            break
        ctx = DecisionContext(person_id="t", state=s,
                              candidates=list(remaining.values()),
                              horizon=horizon, now_index=len(done))
        pa = p.decide(ctx)
        # 硬闸否决的动作绝不执行（忠实于 CMDP：伤主线不做）
        if not pa.gate.passed:
            break
        unmet = [d for d in pa.candidate.deps if d in remaining]
        if unmet:
            ready = [c for c in remaining.values()
                     if not [d for d in c.deps if d in remaining]]
            if not ready:
                break
            pa.candidate = ready[0]
            fails += 1
        c = pa.candidate
        done.append(c.id)
        if pa.detour:
            detours.append(pa.detour["type"])
        del remaining[c.id]
    return done, detours, fails


class TestLevelCompletion(unittest.TestCase):
    """分级场景达成率与迂回成本。"""

    def test_easy_full_completion_zero_discount(self):
        cands = [
            TaskCandidate(id="s1", title="关心", level="small", deps=[], duration=1,
                          reward={"affection": 0.2, "trust": 0.1}, success_prob=0.9),
            TaskCandidate(id="s2", title="接情绪", level="small", deps=[], duration=1,
                          reward={"affection": 0.1, "trust": 0.2}, success_prob=0.9),
            TaskCandidate(id="m1", title="约见面", level="mid", deps=["s1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.8,
                          mainline_impact=0.25),
            TaskCandidate(id="main", title="定关系", level="main", deps=["m1", "s2"],
                          duration=2, reward={"affection": 0.3, "mainline": 0.7},
                          success_prob=0.8, mainline_impact=0.5),
        ]
        done, detours, fails = _simulate(cands, RelationState(P=0.5, M=0.4, A=0.6))
        self.assertEqual(len(done), 4, f"easy 应 100% 达成，实际 {done}")
        self.assertEqual(sum(DETOUR_COST[d] for d in detours), 0.0,
                         f"easy 应零折扣，实际迂回 {detours}")
        self.assertEqual(fails, 0)

    def test_medium_equivalent_detour_zero_cost(self):
        cands = [
            TaskCandidate(id="s1", title="关心", level="small", deps=[], duration=1,
                          reward={"affection": 0.2}, success_prob=0.9),
            TaskCandidate(id="other", title="朋友局", level="small", deps=["s1"], duration=1,
                          reward={"affection": 0.1}, success_prob=0.9,
                          conflict_tags=["周六晚"]),
            TaskCandidate(id="c1", title="周六约饭", level="mid", deps=["s1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.8,
                          mainline_impact=0.25, conflict_tags=["周六晚"],
                          equivalents=["c1b"]),
            TaskCandidate(id="main", title="定关系", level="main", deps=["c1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.7}, success_prob=0.8,
                          mainline_impact=0.5),
        ]
        done, detours, fails = _simulate(cands, RelationState(P=0.5, M=0.4, A=0.6))
        self.assertIn("main", done)
        # 有等价后继 → 应走 equivalent（0 成本），而非 stagger/buffer
        self.assertIn("equivalent", detours, f"应选等价迂回，实际 {detours}")
        self.assertNotIn("buffer", detours)
        self.assertEqual(sum(DETOUR_COST[d] for d in detours), 0.0)

    def test_mainline_under_pressure_rejects_temptation(self):
        """重压/诱惑下：高即时奖励但伤主线的捷径被否，主线仍达成。"""
        cands = [
            TaskCandidate(id="s1", title="关心", level="small", deps=[], duration=1,
                          reward={"affection": 0.2}, success_prob=0.9),
            TaskCandidate(id="temp", title="快速讨好换承诺", level="small", deps=[],
                          duration=1, reward={"affection": 0.9, "mainline": 0.2},
                          success_prob=0.9, mainline_impact=-0.4),  # 伤主线
            TaskCandidate(id="m1", title="约见面", level="mid", deps=["s1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.8,
                          mainline_impact=0.25),
            TaskCandidate(id="main", title="定关系", level="main", deps=["m1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.7}, success_prob=0.8,
                          mainline_impact=0.5),
        ]
        done, detours, fails = _simulate(cands, RelationState(P=0.5, M=0.4, A=0.6))
        self.assertNotIn("temp", done, "伤主线的诱惑捷径绝不能被选中")
        self.assertIn("main", done, "主线任务仍应达成")

    def test_opposing_pair_both_completed(self):
        """相背两主线：经 reform/stagger 两者都达成，不二选一。"""
        cands = [
            TaskCandidate(id="s1", title="关心", level="small", deps=[], duration=1,
                          reward={"affection": 0.2}, success_prob=0.9),
            TaskCandidate(id="a", title="闺蜜局", level="main", deps=["s1"], duration=2,
                          reward={"affection": 0.2, "mainline": 0.4}, success_prob=0.7,
                          mainline_impact=0.35, conflict_tags=["周六晚"]),
            TaskCandidate(id="b", title="见家长", level="main", deps=["s1"], duration=2,
                          reward={"affection": 0.2, "mainline": 0.5}, success_prob=0.7,
                          mainline_impact=0.4, conflict_tags=["周六晚"]),
        ]
        done, detours, fails = _simulate(cands, RelationState(P=0.5, M=0.4, A=0.6))
        self.assertIn("a", done)
        self.assertIn("b", done, "相背两任务应都达成，不二选一")
        self.assertGreaterEqual(len(detours), 1)

    def test_detour_prefers_equivalent_over_buffer(self):
        """冲突消解主动选等价(0成本)而非缓冲(2成本)。"""
        cands = [
            TaskCandidate(id="s1", title="关心", level="small", deps=[], duration=1,
                          reward={"affection": 0.2}, success_prob=0.9),
            TaskCandidate(id="p1", title="朋友局", level="small", deps=["s1"], duration=1,
                          reward={"affection": 0.1}, success_prob=0.9,
                          conflict_tags=["周六晚"]),
            TaskCandidate(id="eq", title="有等价", level="mid", deps=["s1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.8,
                          mainline_impact=0.25, conflict_tags=["周六晚"],
                          equivalents=["eq_alt"]),
            TaskCandidate(id="p2", title="同事局", level="small", deps=["s1"], duration=1,
                          reward={"affection": 0.1}, success_prob=0.9,
                          conflict_tags=["周日晚"]),
            TaskCandidate(id="buf", title="需缓冲", level="mid", deps=["s1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.8,
                          mainline_impact=0.25, conflict_tags=["周日晚"]),
        ]
        done, detours, fails = _simulate(cands, RelationState(P=0.5, M=0.4, A=0.6))
        self.assertIn("eq", done)
        self.assertIn("equivalent", detours)
        self.assertNotIn("buffer", detours)


class TestEasyBatch(unittest.TestCase):
    """easy 档批量变体：高成功率(≥0.9)、无冲突 → 每个都 100% 全达成、折扣 0、零误迂回。"""

    def _variants(self):
        return [
            [TaskCandidate(id="s", title="关心", level="small", deps=[], duration=1,
                           reward={"affection": 0.2}, success_prob=0.92),
             TaskCandidate(id="m", title="约会", level="main", deps=["s"], duration=2,
                           reward={"affection": 0.4, "mainline": 0.6}, success_prob=0.9,
                           mainline_impact=0.5)],
            [TaskCandidate(id="s1", title="关心", level="small", deps=[], duration=1,
                           reward={"affection": 0.2}, success_prob=0.95),
             TaskCandidate(id="s2", title="倾听", level="small", deps=[], duration=1,
                           reward={"trust": 0.2}, success_prob=0.95),
             TaskCandidate(id="m1", title="约见面", level="mid", deps=["s1"], duration=2,
                           reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.9,
                           mainline_impact=0.25),
             TaskCandidate(id="main", title="定关系", level="main", deps=["m1", "s2"],
                           duration=2, reward={"affection": 0.4, "mainline": 0.6},
                           success_prob=0.9, mainline_impact=0.5)],
            [TaskCandidate(id="a", title="关心", level="small", deps=[], duration=1,
                           reward={"affection": 0.2}, success_prob=0.95),
             TaskCandidate(id="b", title="倾听", level="small", deps=[], duration=1,
                           reward={"trust": 0.2}, success_prob=0.95),
             TaskCandidate(id="c", title="约咖啡", level="mid", deps=["a"], duration=1,
                           reward={"affection": 0.3}, success_prob=0.9, mainline_impact=0.2),
             TaskCandidate(id="d", title="周末约会", level="mid", deps=["c", "b"], duration=2,
                           reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.9,
                           mainline_impact=0.3),
             TaskCandidate(id="main", title="定关系", level="main", deps=["d"], duration=2,
                           reward={"affection": 0.4, "mainline": 0.6}, success_prob=0.9,
                           mainline_impact=0.5)],
            [TaskCandidate(id="s1", title="日常关心", level="small", deps=[], duration=1,
                           reward={"affection": 0.2}, success_prob=0.95),
             TaskCandidate(id="online", title="线上升温", level="mid", deps=["s1"], duration=1,
                           reward={"affection": 0.3}, success_prob=0.9, mainline_impact=0.2),
             TaskCandidate(id="offline", title="线下见面", level="mid", deps=["online"],
                           duration=2, reward={"affection": 0.3, "mainline": 0.3},
                           success_prob=0.9, mainline_impact=0.3),
             TaskCandidate(id="main", title="定关系", level="main", deps=["offline"],
                           duration=2, reward={"affection": 0.4, "mainline": 0.6},
                           success_prob=0.9, mainline_impact=0.5)],
        ]

    def test_easy_batch_zero_discount(self):
        states = [RelationState(P=0.6, M=0.5, A=0.6),
                  RelationState(P=0.35, M=0.3, A=0.5),   # 低P起
                  RelationState(P=0.75, M=0.6, A=0.7),   # 高P高M直接dive
                  RelationState(P=0.5, M=0.4, A=0.6)]
        for cands, st in zip(self._variants(), states):
            done, detours, fails = _simulate(cands, st)
            self.assertEqual(len(done), len(cands),
                             f"easy 应全达成: {done}")
            self.assertEqual(sum(DETOUR_COST[d] for d in detours), 0.0,
                             f"平坦路径不应有迂回折扣: {detours}")
            self.assertEqual(detours, [], "无冲突不应误触发 diverge/detour")


    def test_stagger_preferred_over_buffer(self):
        """同一标签冲突下 stagger(1) 可行即不选 buffer(2)。"""
        cands = [
            TaskCandidate(id="s1", title="关心", level="small", deps=[], duration=1,
                          reward={"affection": 0.2}, success_prob=0.9),
            TaskCandidate(id="p1", title="朋友局", level="small", deps=["s1"], duration=1,
                          reward={"affection": 0.1}, success_prob=0.9,
                          conflict_tags=["周六晚"]),
            TaskCandidate(id="c1", title="约饭", level="mid", deps=["s1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.8,
                          mainline_impact=0.25, conflict_tags=["周六晚"]),
            TaskCandidate(id="main", title="定关系", level="main", deps=["c1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.7}, success_prob=0.8,
                          mainline_impact=0.5),
        ]
        done, detours, fails = _simulate(cands, RelationState(P=0.5, M=0.4, A=0.6))
        self.assertIn("main", done)
        self.assertNotIn("buffer", detours, f"应优先 stagger，实际 {detours}")
        self.assertIn("stagger", detours)

    def test_single_conflict_paid_once_not_repeated(self):
        """一次冲突只消解一次，后续步骤不延续 diverge（不连环 +成本）。"""
        cands = [
            TaskCandidate(id="s1", title="关心", level="small", deps=[], duration=1,
                          reward={"affection": 0.2}, success_prob=0.9),
            TaskCandidate(id="p1", title="朋友局", level="small", deps=["s1"], duration=1,
                          reward={"affection": 0.1}, success_prob=0.9,
                          conflict_tags=["周六晚"]),
            TaskCandidate(id="c1", title="约饭", level="mid", deps=["s1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.3}, success_prob=0.8,
                          mainline_impact=0.25, conflict_tags=["周六晚"]),
            TaskCandidate(id="main", title="定关系", level="main", deps=["c1"], duration=2,
                          reward={"affection": 0.3, "mainline": 0.7}, success_prob=0.8,
                          mainline_impact=0.5),
        ]
        done, detours, fails = _simulate(cands, RelationState(P=0.5, M=0.4, A=0.6))
        # 只有 c1/p1 共享一个标签 → 只应产生 1 次 detour（非 +4/+6）
        self.assertLessEqual(len(detours), 1, f"单冲突只消解一次，实际 {detours}")
        self.assertIn("main", done)


if __name__ == "__main__":
    unittest.main()
