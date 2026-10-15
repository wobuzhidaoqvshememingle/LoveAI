# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - 人格倾向识别与策略/任务适配测试（14 种）

覆盖：
  - 14 种人格倾向的自动识别（基于画像特征，不硬编码标签）
  - strategy_advisor 接入：专属策略/禁忌注入 + reasoning 说明
  - task_engine 接入：专属任务生成
  - 偏执型(PPD) / 依赖型(DPD) 小规模验证
  - 4 种人格（PPD/DPD/NPD/BPD）策略输出明显不同
  - 与既有 NPD/BPD 路径不冲突；健康画像不误报
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services import personality_strategy as ps  # noqa: E402
from services.strategy_advisor import StrategyAdvisor  # noqa: E402


# ---------------------------------------------------------------------- #
# 测试画像构造（模拟真实 21 维嵌套画像）
# ---------------------------------------------------------------------- #
def _ppd_persona():
    return {
        "attachment": {"avoidance_score": 55, "anxiety_score": 65, "primary_type": "焦虑型"},
        "psychology": {
            "cbt_distortions": {"读心术": 2, "非黑即白": 1},
            "defensive_communication": {"否定现实": 1},
            "dark_triad_tendencies": {},
        },
        "trustworthiness": {"level": "low"},
        "emotion_pattern": {"volatility": "中", "pattern": "偏积极"},
        "conflict_style": {"primary_key": "competing"},
    }


def _dpd_persona():
    return {
        "attachment": {"avoidance_score": 30, "anxiety_score": 70, "primary_type": "焦虑型"},
        "psychology": {
            "cbt_distortions": {"个人化": 2},
            "defensive_communication": {},
            "dark_triad_tendencies": {},
        },
        "boundaries": {"personal_boundary": "模糊"},
        "self_differentiation": {"level": "低"},
        "conflict_style": {"primary_key": "avoiding"},
        "emotion_pattern": {"volatility": "低", "pattern": "中性平稳"},
    }


def _npd_persona():
    return {
        "attachment": {"avoidance_score": 65, "anxiety_score": 35, "primary_type": "回避型"},
        "psychology": {
            "cbt_distortions": {},
            "defensive_communication": {"贬低感受": 2},
            "dark_triad_tendencies": {"自恋": 3, "操纵": 1},
        },
        "risk_warning": {"pua_score": 6},
    }


def _bpd_persona():
    return {
        "attachment": {"avoidance_score": 65, "anxiety_score": 65, "primary_type": "恐惧型"},
        "psychology": {
            "cbt_distortions": {"非黑即白": 2, "灾难化": 1},
            "defensive_communication": {},
            "dark_triad_tendencies": {},
        },
        "emotion_pattern": {"volatility": "高", "pattern": "消极倾向"},
        "conflict_style": {"primary_key": "emotional_outburst"},
    }


class TestPersonalityRecognition(unittest.TestCase):
    """人格倾向识别：14 种关键人格能被正确识别为主导倾向。"""

    def _primary(self, persona):
        t = ps.primary_tendency(persona)
        return t["key"] if t else None

    # ---- 第一步要求验证的两种 ----
    def test_recognize_ppd(self):
        self.assertEqual(self._primary(_ppd_persona()), "ppd")

    def test_recognize_dpd(self):
        self.assertEqual(self._primary(_dpd_persona()), "dpd")

    # ---- 既有 NPD/BPD 仍正确 ----
    def test_recognize_npd(self):
        self.assertEqual(self._primary(_npd_persona()), "npd")

    def test_recognize_bpd(self):
        self.assertEqual(self._primary(_bpd_persona()), "bpd")

    # ---- 其余人格 ----
    def test_recognize_aspd(self):
        p = {
            "psychology": {
                "cbt_distortions": {},
                "defensive_communication": {"推卸责任": 2},
                "dark_triad_tendencies": {"冲动无情": 3, "操纵": 2},
            },
            "conflict_style": {"primary_key": "destructive"},
        }
        self.assertEqual(self._primary(p), "aspd")

    def test_recognize_hpd(self):
        p = {
            "emotion_pattern": {"volatility": "高", "pattern": "偏积极"},
            "boundaries": {"personal_boundary": "模糊"},
            "self_differentiation": {"level": "低"},
            "psychology": {"cbt_distortions": {"应该陈述": 2},
                           "defensive_communication": {}, "dark_triad_tendencies": {}},
        }
        self.assertEqual(self._primary(p), "hpd")

    def test_recognize_ocpd(self):
        p = {
            "psychology": {"cbt_distortions": {"应该陈述": 3},
                           "defensive_communication": {}, "dark_triad_tendencies": {}},
            "self_differentiation": {"level": "高"},
        }
        self.assertEqual(self._primary(p), "ocpd")

    def test_recognize_avpd(self):
        p = {
            "attachment": {"avoidance_score": 60, "anxiety_score": 60, "primary_type": "恐惧型"},
            "psychology": {"cbt_distortions": {"灾难化": 2, "个人化": 1},
                           "defensive_communication": {}, "dark_triad_tendencies": {}},
            "self_differentiation": {"level": "低"},
        }
        self.assertEqual(self._primary(p), "avpd")

    def test_recognize_depressive(self):
        p = {
            "emotion_pattern": {"volatility": "低", "pattern": "消极倾向"},
            "psychology": {"cbt_distortions": {"个人化": 2, "灾难化": 1},
                           "defensive_communication": {}, "dark_triad_tendencies": {}},
        }
        self.assertEqual(self._primary(p), "depressive")

    def test_recognize_passive_aggressive(self):
        p = {
            "conflict_style": {"primary_key": "cold_war"},
            "psychology": {"cbt_distortions": {}, "defensive_communication": {"推卸责任": 2},
                           "dark_triad_tendencies": {}},
        }
        self.assertEqual(self._primary(p), "passive_aggressive")

    def test_recognize_schizoid(self):
        p = {
            "attachment": {"avoidance_score": 80, "anxiety_score": 30, "primary_type": "回避型"},
            "boundaries": {"personal_boundary": "清晰"},
            "self_differentiation": {"level": "高"},
            "emotion_pattern": {"volatility": "低", "pattern": "中性平稳"},
        }
        self.assertEqual(self._primary(p), "schizoid")

    def test_recognize_alexithymia(self):
        p = {
            "attachment": {"avoidance_score": 40, "anxiety_score": 30, "primary_type": "安全型"},
            "boundaries": {"personal_boundary": "清晰"},
            "self_differentiation": {"level": "高"},
            "emotion_pattern": {"volatility": "低", "pattern": "中性平稳"},
        }
        self.assertEqual(self._primary(p), "alexithymia")

    def test_healthy_persona_no_false_positive(self):
        p = {
            "attachment": {"avoidance_score": 30, "anxiety_score": 30, "primary_type": "安全型"},
            "psychology": {"cbt_distortions": {}, "defensive_communication": {},
                           "dark_triad_tendencies": {}},
            "emotion_pattern": {"volatility": "低", "pattern": "积极主导"},
        }
        self.assertIsNone(ps.primary_tendency(p))


class TestPersonalityStrategyContent(unittest.TestCase):
    """策略与任务：每种人格有专属内容，且不雷同。"""

    def test_ppd_strategy_content(self):
        recs, forb, note, key = ps.build_strategy(_ppd_persona())
        self.assertEqual(key, "ppd")
        self.assertTrue(any("事实" in r for r in recs))       # 用事实回应
        self.assertTrue(any("消失" in f for f in forb))       # 禁忌突然消失
        self.assertIn("表现出偏执倾向", note)                 # 措辞是"倾向"
        self.assertIn("不是医学诊断", note)                   # 非诊断声明

    def test_dpd_strategy_content(self):
        recs, forb, note, key = ps.build_strategy(_dpd_persona())
        self.assertEqual(key, "dpd")
        self.assertTrue(any("决定权" in r or "决定" in r for r in recs))  # 把决定权还给他
        self.assertTrue(any("替对方做决定" in f for f in forb))          # 禁忌替做决定

    def test_four_personalities_differ(self):
        """4 种人格策略输出明显不同（不是套模板）。"""
        pps = {"ppd": _ppd_persona(), "dpd": _dpd_persona(),
               "npd": _npd_persona(), "bpd": _bpd_persona()}
        notes = {}
        for k, p in pps.items():
            _, _, note, key = ps.build_strategy(p)
            self.assertEqual(k, key)
            notes[k] = note
        # 四份 note 两两不同
        self.assertNotEqual(notes["ppd"], notes["dpd"])
        self.assertNotEqual(notes["ppd"], notes["npd"])
        self.assertNotEqual(notes["dpd"], notes["bpd"])
        # PPD 强调事实/信任，DPD 强调独立/决定，NPD 强调煤气灯/自我价值，BPD 强调稳定锚点
        self.assertTrue(any(w in notes["ppd"] for w in ["事实", "信任", "行程"]))
        self.assertTrue(any(w in notes["dpd"] for w in ["决定", "独立", "依附"]))
        self.assertTrue(any(w in notes["npd"] for w in ["煤气灯", "自我价值"]))
        self.assertTrue(any(w in notes["bpd"] for w in ["稳定", "锚点", "一致"]))

    def test_combination_adjustment(self):
        """人格×依恋组合：NPD+回避型 应出现'疏离型自恋'组合说明。"""
        _, _, note, key = ps.build_strategy(_npd_persona())  # NPD + 回避型
        self.assertEqual(key, "npd")
        self.assertIn("疏离型自恋", note)

    def test_dpd_tasks_generated(self):
        note, tasks = ps.build_tasks(_dpd_persona())
        self.assertTrue(note)
        self.assertTrue(len(tasks) >= 1)
        self.assertTrue(any("决定" in t for t in tasks))

    def test_ppd_tasks_generated(self):
        note, tasks = ps.build_tasks(_ppd_persona())
        self.assertTrue(len(tasks) >= 1)
        self.assertTrue(any("事实" in t or "触发点" in t for t in tasks))

    def test_tasks_for_at_least_six_personalities(self):
        """至少 6 种人格能生成专属任务。"""
        cnt = 0
        for k in ps.all_keys():
            prof = ps.get_profile(k)
            if prof.get("tasks"):
                cnt += 1
        self.assertGreaterEqual(cnt, 6)


class TestStrategyAdvisorIntegration(unittest.TestCase):
    """strategy_advisor._apply_personality_strategy 接入。"""

    def setUp(self):
        self.adv = StrategyAdvisor(None)

    def test_ppd_injected_into_recommendation(self):
        r = self.adv.analyze(_ppd_persona(), 5)
        # 人格策略应被注入推荐
        joined = " ".join(r["recommended_raw"])
        self.assertIn("用事实和时间线回应质问，不情绪化", joined)
        # reasoning 应说明基于偏执倾向
        self.assertIn("人格倾向审计", r["reasoning"])
        self.assertIn("偏执倾向", r["reasoning"])

    def test_dpd_injected_into_recommendation(self):
        r = self.adv.analyze(_dpd_persona(), 5)
        joined = " ".join(r["recommended_raw"])
        self.assertTrue(any("决定权" in t for t in r["recommended_raw"]))
        self.assertIn("依赖倾向", r["reasoning"])

    def test_safe_persona_no_personality_note(self):
        p = {"attachment": {"avoidance_score": 30, "anxiety_score": 30, "primary_type": "安全型"},
             "psychology": {"cbt_distortions": {}, "defensive_communication": {},
                            "dark_triad_tendencies": {}}}
        r = self.adv.analyze(p, 5)
        self.assertNotIn("人格倾向审计", r["reasoning"])

    def test_npd_path_still_works(self):
        """既有 NPD 煤气灯防御路径不被破坏。"""
        r = self.adv.analyze(_npd_persona(), 5)
        self.assertIn("人格倾向审计", r["reasoning"])
        self.assertIn("自恋倾向", r["reasoning"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
