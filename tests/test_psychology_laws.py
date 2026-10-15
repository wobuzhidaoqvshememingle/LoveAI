# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 心理学定律策略化单元测试

覆盖：
  - 39 条定律注册表完整性（字段齐全、≥30 条）
  - 三种人设的定律触发差异（回避型/焦虑型/NPD）
  - 关键定律的条件触发（首因/近因/晕轮/贝勃/破窗/四骑士/沉没成本...）
  - StrategyAdvisor 真实输出：reasoning 引用定律名、禁忌含误用警告
  - TaskEngine 真实输出：生成「基于XX定律」的任务标题
  - 修复引导导向扫描：定律推荐技巧不得输出操控对方的话术
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services import psychology_laws as pl  # noqa: E402
from services.strategy_advisor import StrategyAdvisor  # noqa: E402
from services.task_engine import TaskEngine  # noqa: E402
from services.affection_engine import AffectionEngine  # noqa: E402
from models.database import Database  # noqa: E402


# ---------------------------------------------------------------------- #
# 三人设画像（与需求文档对齐）
# ---------------------------------------------------------------------- #
AVOIDANT = {"attachment": {"primary_type": "回避型", "avoidance_score": 75, "anxiety_score": 25}}
ANXIOUS = {"attachment": {"primary_type": "焦虑型", "avoidance_score": 25, "anxiety_score": 75}}
NPD = {
    "risk_warning": {"pua_score": 7},
    "psychology": {"dark_triad_tendencies": {"自恋": 3, "操纵": 2}},
}
SECURE = {"attachment": {"primary_type": "安全型", "avoidance_score": 20, "anxiety_score": 20}}


def _law_names(hits: list[dict]) -> list[str]:
    return [h["name"] for h in hits]


class TestLawRegistry(unittest.TestCase):
    """定律注册表结构完整性。"""

    def test_total_laws_at_least_30(self) -> None:
        """至少 30 条定律（实际 39 条）。"""
        self.assertGreaterEqual(len(pl.LAWS), 30)

    def test_every_law_has_required_fields(self) -> None:
        """每条定律必须有：定义/场景/策略/禁忌/人设关系/触发谓词。"""
        required = ("id", "name", "definition", "scenario", "strategy",
                    "taboo", "persona_effectiveness", "match")
        for law in pl.LAWS:
            for f in required:
                self.assertIn(f, law, f"定律 {law.get('id')} 缺字段 {f}")
            self.assertTrue(callable(law["match"]), f"定律 {law['id']} 的 match 不可调用")

    def test_unique_law_ids(self) -> None:
        """定律 id 不得重复。"""
        ids = [law["id"] for law in pl.LAWS]
        self.assertEqual(len(ids), len(set(ids)))


class TestPersonaTriggerDifferences(unittest.TestCase):
    """三种人设触发不同的定律集（需求第六步验证基线）。"""

    def test_avoidant_triggers_exposure_and_suspension_bridge(self) -> None:
        """回避型 → 曝光效应 + 吊桥效应。"""
        hits = _law_names(pl.match_laws(AVOIDANT, stage=4))
        self.assertIn("曝光效应", hits)
        self.assertIn("吊桥效应", hits)

    def test_anxious_triggers_intermittent_reinforcement(self) -> None:
        """焦虑型 → 间歇性强化识别。"""
        hits = _law_names(pl.match_laws(ANXIOUS, stage=4))
        self.assertIn("间歇性强化", hits)

    def test_npd_triggers_cognitive_dissonance_and_commitment(self) -> None:
        """NPD → 认知失调 + 承诺一致性 + 登门槛。"""
        hits = _law_names(pl.match_laws(NPD, stage=5))
        self.assertIn("认知失调", hits)
        self.assertIn("承诺一致性", hits)
        self.assertIn("登门槛效应", hits)

    def test_secure_no_npd_laws(self) -> None:
        """安全型画像（无 pua）不应触发 NPD 类定律。"""
        hits = _law_names(pl.match_laws(SECURE, stage=5))
        self.assertNotIn("登门槛效应", hits)
        self.assertNotIn("认知失调", hits)
        self.assertNotIn("稀缺原理", hits)


class TestConditionTriggers(unittest.TestCase):
    """关键定律的条件触发。"""

    def test_primacy_effect_stage_le_2(self) -> None:
        """首因效应在初识期（stage≤2）触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=1))
        self.assertIn("首因效应", hits)

    def test_primacy_effect_not_triggered_stage5(self) -> None:
        """首因效应在暧昧期不触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=5))
        self.assertNotIn("首因效应", hits)

    def test_recency_effect_stage_3_5(self) -> None:
        """近因效应在矛盾/修复期触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=4))
        self.assertIn("近因效应", hits)

    def test_halo_effect_stage_le_5(self) -> None:
        """晕轮效应在 stage≤5 触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=3))
        self.assertIn("晕轮效应", hits)

    def test_beibao_law_when_stage_ge_6(self) -> None:
        """贝勃定律在稳定期（stage≥6）触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=7))
        self.assertIn("贝勃定律", hits)

    def test_broken_window_when_conflict_avoiding(self) -> None:
        """逃避型冲突 → 破窗效应。"""
        p = {"conflict_style": {"primary_key": "cold_war"}}
        hits = _law_names(pl.match_laws(p, stage=4))
        self.assertIn("破窗效应（关系版）", hits)

    def test_gottman_four_horsemen_when_competing_low_stability(self) -> None:
        """竞争型冲突+低情绪稳定 → Gottman 四骑士。"""
        p = {
            "conflict_style": {"primary_key": "destructive"},
            "emotion_pattern": {"volatility": "高"},
        }
        hits = _law_names(pl.match_laws(p, stage=5))
        self.assertIn("Gottman婚姻四骑士", hits)

    def test_sunk_cost_when_user_overinvests(self) -> None:
        """用户超额投入 → 沉没成本 + 过度理由 + 富兰克林效应。"""
        p = {"investment_balance": {"status": "user_overinvest", "overall_ratio": 1.6}}
        hits = _law_names(pl.match_laws(p, stage=5))
        self.assertIn("沉没成本效应", hits)
        self.assertIn("过度理由效应", hits)
        self.assertIn("富兰克林效应", hits)

    def test_horn_effect_when_deception_high(self) -> None:
        """高欺骗风险 → 恶魔效应。"""
        p = {"deception_risk": {"data_sufficient": True, "risk_score": 72, "risk_level": "高"}}
        hits = _law_names(pl.match_laws(p, stage=3))
        self.assertIn("恶魔效应", hits)


class TestAdvisorIntegration(unittest.TestCase):
    """StrategyAdvisor 真实接入验证。"""

    def setUp(self) -> None:
        self.adv = StrategyAdvisor(None)

    def test_reasoning_cites_law_name(self) -> None:
        """焦虑型 analyze() 的 reasoning 必须引用「间歇性强化」。"""
        r = self.adv.analyze(ANXIOUS, 4)
        self.assertIn("间歇性强化", r["reasoning"])
        self.assertIn("心理学定律引用", r["reasoning"])

    def test_reasoning_cites_bridge_for_avoidant(self) -> None:
        """回避型 analyze() 的 reasoning 必须引用「吊桥效应」和「曝光效应」。"""
        r = self.adv.analyze(AVOIDANT, 4)
        self.assertIn("吊桥效应", r["reasoning"])
        self.assertIn("曝光效应", r["reasoning"])

    def test_forbidden_contains_misuse_warning(self) -> None:
        """禁忌列表必须含「定律误用警告」。"""
        r = self.adv.analyze(ANXIOUS, 4)
        joined = "；".join(r["forbidden"])
        self.assertIn("定律误用警告", joined)

    def test_recommended_includes_law_tips(self) -> None:
        """推荐列表必须包含来自定律的技巧（带「目的：」注解）。"""
        r = self.adv.analyze(AVOIDANT, 4)
        joined = "；".join(r["recommended"])
        self.assertIn("曝光效应", joined)  # 目的注解里会提到
        # 至少有一条推荐技巧带定律来源
        self.assertTrue(any("目的：" in x for x in r["recommended"]))


class TestRepairOrientation(unittest.TestCase):
    """修复引导导向扫描：定律推荐技巧不得是操控话术。"""

    # 这些词一旦出现在 rec_tips 里，就是操控导向，必须报错
    FORBIDDEN_MANIP_WORDS = ("你用这个去操控", "故意吊着对方", "故意不回", "驯化对方",
                             "逼对方爱上你", "让他离不开你靠手段")

    def test_no_manipulation_tips_in_rec_tips(self) -> None:
        """所有定律的 rec_tips 不得包含操控对方的话术。"""
        for law in pl.LAWS:
            for tip in law.get("rec_tips", []):
                for bad in self.FORBIDDEN_MANIP_WORDS:
                    self.assertNotIn(
                        bad, tip,
                        f"定律 {law['id']} 的推荐技巧疑似操控导向: {tip}",
                    )

    def test_intermittent_reinforcement_is_identification_only(self) -> None:
        """间歇性强化的策略表述必须是「识别/保持节奏」，不能是「你去玩冷热交替」。"""
        ir = next(l for l in pl.LAWS if l["id"] == "intermittent_reinforcement")
        self.assertIn("识别", ir["strategy"])
        for tip in ir["rec_tips"]:
            self.assertIn("识别", tip)

    def test_misuse_warning_covers_key_dangerous_laws(self) -> None:
        """易误用定律必须有误用警告。"""
        for law_id in ("intermittent_reinforcement", "suspension_bridge",
                       "scarcity_principle", "zeigarnik_effect", "foot_in_door"):
            law = next(l for l in pl.LAWS if l["id"] == law_id)
            # 要么 law 自己有 forb_tips，要么全局警告表有覆盖
            has_local = bool(law.get("forb_tips"))
            has_global = any(w in pl.LAW_MISUSE_WARNINGS for w in law.get("forb_tips", []))
            self.assertTrue(has_local or has_global,
                            f"定律 {law_id} 缺少误用警告")


class TestTaskEngineIntegration(unittest.TestCase):
    """TaskEngine 真实生成「基于XX定律」的任务。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_test_laws_")
        self.db_path = os.path.join(self.tmpdir, "test_laws.db")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.affection = AffectionEngine(self.db)
        self.engine = TaskEngine(self.db, self.affection, guide_engine=None)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_build_law_tasks_outputs_law_labels(self) -> None:
        """build_law_tasks 生成的任务标题必须含「基于XX定律」。"""
        note, titles = pl.build_law_tasks(ANXIOUS, stage=4)
        self.assertTrue(note.startswith("[心理学定律]"))
        self.assertGreater(len(titles), 0)
        for t in titles:
            self.assertIn("（基于", t)
            self.assertTrue(t.rstrip().endswith("）"), f"任务标题未以定律括注结尾: {t}")

    def test_engine_compute_law_tasks_with_persona(self) -> None:
        """TaskEngine._compute_psychology_law_tasks 在画像存在时返回非空任务。"""
        with patch(
            "services.strategy_advisor.load_persona_from_disk",
            return_value=ANXIOUS,
        ):
            note, titles = self.engine._compute_psychology_law_tasks("p_test", stage=4)
        self.assertIn("心理学定律", note)
        self.assertGreater(len(titles), 0)
        # 至少一条任务明确提到间歇性强化
        self.assertTrue(any("间歇性强化" in t for t in titles))

    def test_engine_compute_law_tasks_no_persona_returns_empty(self) -> None:
        """无画像时返回空（向后兼容）。"""
        with patch(
            "services.strategy_advisor.load_persona_from_disk",
            return_value=None,
        ):
            note, titles = self.engine._compute_psychology_law_tasks("p_none", stage=4)
        self.assertEqual(note, "")
        self.assertEqual(titles, [])


class TestV3NewLaws(unittest.TestCase):
    """v3.0 新增 22 条定律的字段完整性与触发覆盖。"""

    NEW_LAW_IDS = (
        # 情绪管理
        "wild_horse_effect", "kick_cat_effect", "emotion_contagion",
        # 关系距离
        "hedgehog_dilemma", "self_disclosure_law", "dark_effect",
        # 互动策略
        "hercules_effect", "south_wind_law", "north_wind_effect",
        "mutual_affinity", "vulnerability_strength", "conversion_effect",
        "in_group_effect", "non_zero_sum",
        # 认知偏差
        "projection_bias", "spotlight_effect", "illusion_of_control",
        # 其他
        "flywheel_effect", "praise_specific", "powells_law",
        "trust_integrity", "similarity_attraction",
    )

    def test_total_laws_around_60(self) -> None:
        """v3.0 四轮补全后定律总数应为 89 条（原82 + 危险效应/重审收录7条）。"""
        self.assertGreaterEqual(len(pl.LAWS), 85)
        self.assertLessEqual(len(pl.LAWS), 95)

    def test_new_laws_all_present(self) -> None:
        """22 条新定律都注册在 LAWS 里。"""
        existing = {l["id"] for l in pl.LAWS}
        for lid in self.NEW_LAW_IDS:
            self.assertIn(lid, existing, f"新定律 {lid} 未注册")

    def test_new_laws_have_required_fields(self) -> None:
        """每条新定律字段齐全。"""
        required = ("id", "name", "category", "definition", "scenario",
                    "strategy", "taboo", "persona_effectiveness",
                    "rec_tips", "forb_tips", "tasks", "match")
        by_id = {l["id"]: l for l in pl.LAWS}
        for lid in self.NEW_LAW_IDS:
            law = by_id[lid]
            for f in required:
                self.assertIn(f, law, f"新定律 {lid} 缺字段 {f}")

    def test_new_law_ids_no_conflict_with_old(self) -> None:
        """新定律 id 不与旧定律冲突（已在 unique 测试覆盖，这里再核一遍数量）。"""
        self.assertEqual(len(pl.LAWS), len({l["id"] for l in pl.LAWS}))

    # ---- 触发场景测试：每条新定律至少一个触发场景 ----
    def test_wild_horse_and_kick_cat_trigger_on_low_stability(self) -> None:
        """低情绪稳定 → 野马效应 + 踢猫效应。"""
        p = {"emotional_stability": "低"}
        hits = _law_names(pl.match_laws(p, stage=5))
        self.assertIn("野马效应", hits)
        self.assertIn("踢猫效应", hits)

    def test_emotion_contagion_always(self) -> None:
        """情绪转移定律普适触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=5))
        self.assertIn("情绪转移定律", hits)

    def test_hedgehog_triggers_on_avoidant(self) -> None:
        """回避型 → 刺猬法则。"""
        hits = _law_names(pl.match_laws(AVOIDANT, stage=4))
        self.assertIn("刺猬法则", hits)

    def test_self_disclosure_stage_2_5(self) -> None:
        """自我暴露定律在 stage 2-5 触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=3))
        self.assertIn("自我暴露定律", hits)
        hits2 = _law_names(pl.match_laws(SECURE, stage=8))
        self.assertNotIn("自我暴露定律", hits2)

    def test_dark_effect_stage_3_5(self) -> None:
        """黑暗效应在约会期 stage 3-5 触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=4))
        self.assertIn("黑暗效应", hits)

    def test_hercules_on_competing_conflict(self) -> None:
        """竞争型冲突 → 海格力斯效应 + 非零和博弈 + 波特定律。"""
        p = {"conflict_style": {"primary_key": "destructive"}}
        hits = _law_names(pl.match_laws(p, stage=5))
        self.assertIn("海格力斯效应", hits)
        self.assertIn("非零和博弈", hits)
        self.assertIn("波特定律", hits)

    def test_south_wind_and_mutual_affinity_always(self) -> None:
        """南风法则/互悦机制/赞美定律/诚信定律/投射效应 普适触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=5))
        for name in ("南风法则", "互悦机制", "赞美定律", "诚信定律", "投射效应"):
            self.assertIn(name, hits, f"{name} 应普适触发")

    def test_north_wind_triggers_on_npd(self) -> None:
        """NPD 画像 → 北风效应（识别强硬话术）。"""
        hits = _law_names(pl.match_laws(NPD, stage=5))
        self.assertIn("北风效应", hits)

    def test_vulnerability_stage_3_6(self) -> None:
        """示弱效应在关系推进期 stage 3-6 触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=4))
        self.assertIn("示弱效应", hits)

    def test_in_group_stage_4_7(self) -> None:
        """同体效应在巩固期 stage 4-7 触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=5))
        self.assertIn("同体效应", hits)

    def test_spotlight_stage_le_3(self) -> None:
        """聚光灯效应在初识期 stage≤3 触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=2))
        self.assertIn("聚光灯效应", hits)

    def test_illusion_of_control_on_anxious(self) -> None:
        """焦虑型 → 控制错觉。"""
        hits = _law_names(pl.match_laws(ANXIOUS, stage=5))
        self.assertIn("控制错觉", hits)

    def test_flywheel_stage_1_3(self) -> None:
        """飞轮效应在关系前期 stage 1-3 触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=2))
        self.assertIn("飞轮效应", hits)

    def test_similarity_stage_le_3(self) -> None:
        """相似吸引效应在 stage≤3 触发。"""
        hits = _law_names(pl.match_laws(SECURE, stage=1))
        self.assertIn("相似吸引效应", hits)

    def test_new_laws_all_generate_tasks(self) -> None:
        """每条新定律都至少有一个任务标题。"""
        by_id = {l["id"]: l for l in pl.LAWS}
        for lid in self.NEW_LAW_IDS:
            tasks = by_id[lid].get("tasks") or []
            self.assertGreater(len(tasks), 0, f"新定律 {lid} 没有任务")
            # 任务标题以"（基于XX）"结尾
            for t in tasks:
                self.assertTrue(t.endswith("）"), f"新定律 {lid} 任务未以定律括注结尾: {t}")

    def test_build_law_tasks_includes_new_law(self) -> None:
        """build_law_tasks 在合适画像下能产出新定律任务。"""
        # 低情绪稳定画像 → 野马效应应命中
        p = {"emotional_stability": "低"}
        note, titles = pl.build_law_tasks(p, stage=5)
        self.assertIn("心理学定律", note)
        self.assertTrue(any("野马效应" in t for t in titles),
                        f"低情绪稳定画像应生成野马效应任务，实际: {titles}")


if __name__ == "__main__":
    unittest.main()
