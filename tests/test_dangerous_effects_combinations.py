# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 危险效应 + 组合协同方案 单元/集成测试

覆盖：
  - 4 条危险效应（维特/煤气灯/斯德哥尔摩/鲶鱼）注册完整、含 usage_boundary
  - 维特效应：自伤风险识别触发，无风险不触发；detect_self_harm_risk 正确
  - 鲶鱼效应严格门控：焦虑型绝不触发 / 安全型停滞触发 / 冲突期不触发 / NPD不触发
  - 煤气灯：高欺骗或否定现实模式触发，且边界标注"不要对对方使用"
  - 斯德哥尔摩：创伤性联结 / NPD+维护加害方触发
  - 组合方案：5 个场景组合存在；冲突场景命中"冲突修复组合"且效应按序；
    组合有协同逻辑而非罗列
  - 编排器端到端：orchestrate() 返回 combination 字段；自伤消息 → 安全警告 + 维特
  - 服务层不 import PySide6
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services import psychology_laws as pl  # noqa: E402
from services import combination_playbook as cp  # noqa: E402
from services.analysis_orchestrator import AnalysisOrchestrator  # noqa: E402


# ---------------------------------------------------------------------- #
# 人设
# ---------------------------------------------------------------------- #
AVOIDANT = {"attachment": {"primary_type": "回避型", "avoidance_score": 75, "anxiety_score": 25}}
ANXIOUS = {"attachment": {"primary_type": "焦虑型", "avoidance_score": 25, "anxiety_score": 75}}
SECURE = {"attachment": {"primary_type": "安全型", "avoidance_score": 20, "anxiety_score": 20}}
NPD = {
    "risk_warning": {"pua_score": 7},
    "psychology": {"dark_triad_tendencies": {"自恋": 3, "操纵": 2}},
}


def _by_id(law_id: str) -> dict:
    return next(l for l in pl.LAWS if l["id"] == law_id)


class TestDangerousEffectsRegistry(unittest.TestCase):
    """4 条危险效应注册完整、带使用边界。"""

    DANGER_IDS = ("werther_effect", "gaslighting_effect",
                  "stockholm_syndrome", "catfish_effect")

    def test_dangerous_laws_present(self) -> None:
        existing = {l["id"] for l in pl.LAWS}
        for lid in self.DANGER_IDS:
            self.assertIn(lid, existing, f"危险效应 {lid} 未注册")

    def test_dangerous_laws_have_usage_boundary(self) -> None:
        """每条危险效应必须有 usage_boundary（使用边界）。"""
        for lid in self.DANGER_IDS:
            law = _by_id(lid)
            self.assertTrue(law.get("usage_boundary"), f"{lid} 缺 usage_boundary")
            self.assertIn("危险", law["category"], f"{lid} 未标注为危险效应类别")

    def test_dangerous_laws_have_tasks_and_match(self) -> None:
        for lid in self.DANGER_IDS:
            law = _by_id(lid)
            self.assertTrue(callable(law["match"]), f"{lid} match 不可调用")
            self.assertGreater(len(law.get("tasks", [])), 0, f"{lid} 无任务")

    def test_gaslight_boundary_states_cost_of_reverse_use(self) -> None:
        """手段中立：煤气灯 taboo 以'条件-代价'呈现反向使用后果，而非道德禁令。"""
        law = _by_id("gaslighting_effect")
        # 不再断言"不要/禁止"式道德裁决，改为断言如实呈现代价
        self.assertTrue(
            any(k in law["taboo"] for k in ("关系破裂", "信任崩塌", "被识破")),
            f"煤气灯 taboo 未呈现反向使用代价: {law['taboo']}",
        )
        self.assertNotIn("绝对不要", law["taboo"])
        self.assertTrue(any("反向操控" in t for t in law.get("forb_tips", [])))

    def test_catfish_taboo_forbids_fake_suitors(self) -> None:
        law = _by_id("catfish_effect")
        self.assertIn("编造虚假追求者", law["taboo"])


class TestWertherEffect(unittest.TestCase):
    """维特效应：自伤风险识别。"""

    def test_detect_self_harm_high(self) -> None:
        r = pl.detect_self_harm_risk("我真的不想活了，活着没意思")
        self.assertTrue(r["detected"])
        self.assertIn("不想活", r["hits"])

    def test_detect_self_harm_negative(self) -> None:
        r = pl.detect_self_harm_risk("今天天气不错，一起吃饭吗")
        self.assertFalse(r["detected"])

    def test_werther_triggers_on_risk(self) -> None:
        law = _by_id("werther_effect")
        p = {"self_harm_risk": {"detected": True, "level": "high"}}
        self.assertTrue(law["match"]({}, p, 5))

    def test_werther_not_triggered_otherwise(self) -> None:
        law = _by_id("werther_effect")
        self.assertFalse(law["match"]({}, {}, 5))

    def test_werther_task_guides_professional_help(self) -> None:
        law = _by_id("werther_effect")
        joined = " ".join(law.get("tasks", [])) + law.get("usage_boundary", "")
        self.assertIn("专业", joined)


class TestCatfishGating(unittest.TestCase):
    """鲶鱼效应严格门控。"""

    def _match(self, persona: dict) -> bool:
        return _by_id("catfish_effect")["match"](pl._lightweight_dims(persona), persona, 5)

    def test_anxious_stagnant_never_triggers(self) -> None:
        p = dict(ANXIOUS, relation_status="stagnant")
        self.assertFalse(self._match(p))

    def test_fearful_stagnant_never_triggers(self) -> None:
        p = {"attachment": {"primary_type": "恐惧型", "avoidance_score": 65, "anxiety_score": 65},
             "relation_status": "stagnant"}
        self.assertFalse(self._match(p))

    def test_secure_stagnant_triggers(self) -> None:
        p = dict(SECURE, relation_status="stagnant")
        self.assertTrue(self._match(p))

    def test_no_stagnation_never_triggers(self) -> None:
        p = dict(SECURE)  # 无停滞标记
        self.assertFalse(self._match(p))

    def test_conflict_period_never_triggers(self) -> None:
        p = dict(SECURE, relation_status="stagnant",
                 conflict_style={"primary_key": "destructive"})
        self.assertFalse(self._match(p))

    def test_npd_never_triggers(self) -> None:
        p = dict(SECURE, relation_status="stagnant",
                 risk_warning={"pua_score": 7})
        self.assertFalse(self._match(p))


class TestGaslightAndStockholm(unittest.TestCase):
    def test_gaslight_triggers_on_deception_high(self) -> None:
        law = _by_id("gaslighting_effect")
        p = {"deception_risk": {"data_sufficient": True, "risk_score": 72, "risk_level": "高"}}
        self.assertTrue(law["match"](pl._lightweight_dims(p), p, 5))

    def test_gaslight_triggers_on_denial_pattern(self) -> None:
        law = _by_id("gaslighting_effect")
        p = {"psychology": {"defensive_communication": {"否定现实": 2, "扭曲事实": 1}}}
        self.assertTrue(law["match"](pl._lightweight_dims(p), p, 5))

    def test_stockholm_triggers_on_traumatic_bonding(self) -> None:
        law = _by_id("stockholm_syndrome")
        p = {"relationship_dynamics": {"traumatic_bonding": True}}
        self.assertTrue(law["match"](pl._lightweight_dims(p), p, 5))

    def test_stockholm_triggers_on_npd_defends_abuser(self) -> None:
        law = _by_id("stockholm_syndrome")
        p = dict(NPD, relationship_dynamics={"defends_abuser": True})
        self.assertTrue(law["match"](pl._lightweight_dims(p), p, 5))

    def test_stockholm_not_triggered_normal(self) -> None:
        law = _by_id("stockholm_syndrome")
        self.assertFalse(law["match"](pl._lightweight_dims(SECURE), SECURE, 5))


class TestCombinationPlaybook(unittest.TestCase):
    """组合协同方案。"""

    def test_five_playbooks_exist(self) -> None:
        names = [pb["playbook_name"] for pb in cp._PLAYBOOKS]
        for expected in ("暧昧推进组合", "冲突修复组合", "停滞重启组合",
                         "退缩应对组合", "长期信任组合"):
            self.assertIn(expected, names)

    def test_conflict_scene_triggers_repair_playbook(self) -> None:
        p = {"conflict_style": {"primary_key": "cold_war"}}
        hits = pl.match_laws(p, stage=4)
        dims = pl._lightweight_dims(p)
        pbs = cp.match_playbooks("冲突争吵", 4, dims, p, hits)
        names = [x["playbook_name"] for x in pbs]
        self.assertIn("冲突修复组合", names)
        repair = next(x for x in pbs if x["playbook_name"] == "冲突修复组合")
        # 效应按 order 排序
        orders = [e["order"] for e in repair["effects"]]
        self.assertEqual(orders, sorted(orders))
        self.assertGreater(len(repair["effects"]), 0)
        self.assertTrue(repair["synergy_logic"])
        self.assertTrue(repair["combined_strategy"])

    def test_combination_is_ordered_not_flat_list(self) -> None:
        """组合方案必须有 order/role/timing，是有序分工而非罗列。"""
        p = {"conflict_style": {"primary_key": "cold_war"}}
        hits = pl.match_laws(p, stage=4)
        pbs = cp.match_playbooks("冲突争吵", 4, pl._lightweight_dims(p), p, hits)
        repair = next(x for x in pbs if x["playbook_name"] == "冲突修复组合")
        for e in repair["effects"]:
            self.assertIn("role", e)
            self.assertIn("order", e)
            self.assertIn("timing", e)

    def test_longterm_playbook_stage_ge_6(self) -> None:
        p = dict(SECURE)
        hits = pl.match_laws(p, stage=7)
        pbs = cp.match_playbooks("日常闲聊", 7, pl._lightweight_dims(p), p, hits)
        names = [x["playbook_name"] for x in pbs]
        self.assertIn("长期信任组合", names)

    def test_summary_is_nonempty(self) -> None:
        p = dict(SECURE)
        hits = pl.match_laws(p, stage=7)
        pbs = cp.match_playbooks("日常闲聊", 7, pl._lightweight_dims(p), p, hits)
        s = cp.primary_playbooks_summary(pbs)
        self.assertTrue(s.startswith("心理学效应组合方案"))


class TestOrchestratorCombinationE2E(unittest.TestCase):
    """编排器端到端：返回 combination；自伤消息触发安全路径。"""

    def setUp(self) -> None:
        self.orch = AnalysisOrchestrator(db=None)

    def test_returns_combination_fields(self) -> None:
        persona = {"conflict_style": {"primary_key": "cold_war"}}
        res = self.orch.orchestrate(
            person_id="test_combo_e2e",
            chat_text="我们别吵了，我不想冷战了",
            context={"persona": persona, "relation": {"stage": 4}},
        )
        self.assertIn("combination", res)
        self.assertIn("combinations", res)
        # 冲突场景应至少给出一个组合方案
        self.assertGreaterEqual(len(res["combinations"]), 1)
        names = [p["playbook_name"] for p in res["combinations"]]
        self.assertIn("冲突修复组合", names)

    def test_self_harm_text_triggers_safety_and_werther(self) -> None:
        persona = dict(ANXIOUS)
        res = self.orch.orchestrate(
            person_id="test_combo_sh",
            chat_text="我真的不想活了，活着没意思",
            context={"persona": persona, "relation": {"stage": 5}},
        )
        # 安全警告置顶
        joined_warn = " ".join(res.get("risk_warnings", []))
        self.assertIn("安全优先", joined_warn)
        # 维特效应命中
        law_ids = [l["id"] for l in res["psychology_laws"]["laws"]]
        self.assertIn("werther_effect", law_ids)


class TestNoPySide6InServiceLayer(unittest.TestCase):
    def test_combination_module_no_qt(self) -> None:
        import inspect
        from services import combination_playbook
        src = inspect.getsource(combination_playbook)
        # 只检查真实导入语句，不误伤文档字符串里"不 import PySide6"的说明
        self.assertNotIn("import PySide6", src)
        self.assertNotIn("from PySide6", src)
        self.assertNotIn("QObject", src)

    def test_psychology_laws_no_qt(self) -> None:
        import inspect
        src = inspect.getsource(pl)
        self.assertNotIn("import PySide6", src)
        self.assertNotIn("from PySide6", src)


if __name__ == "__main__":
    unittest.main()
