# -*- coding: utf-8 -*-
"""
LoveAI v3.1 加固补丁 · P-08 防操纵红线 / 分层同意 单元测试（离线）

覆盖：
  - 六类红线话术命中即拦截（冒充欺骗/威胁/内疚/jealousy/煤气灯/骚扰监控）
  - 中性手段（反制/推拉/给空间/现实检验锚定）不误判为红线
  - 命中时给出真诚、非监控转述
  - 分层知情同意 / 防依赖 / 隐私最小化 / 一键删除覆盖文案
纯规则、离线、不碰模型 / 网络。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.safety_filter import (  # noqa: E402
    ManipulationRedlineGuard,
    informed_consent_disclosure,
    anti_dependency_note,
    privacy_minimization_evidence,
    delete_coverage_policy,
)


class TestRedlineBlocks(unittest.TestCase):
    def setUp(self):
        self.g = ManipulationRedlineGuard()

    def test_impersonation_blocked(self):
        r = self.g.check("你用小号加她，冒充陌生人跟她聊")
        self.assertTrue(r["blocked"])
        self.assertEqual(r["category"], "impersonation_deception")
        self.assertTrue(r["rewrite"])

    def test_threat_blocked(self):
        r = self.g.check("不答应我就威胁她，曝光你让你身败名裂")
        self.assertTrue(r["blocked"])
        self.assertEqual(r["category"], "threat")

    def test_guilt_blocked(self):
        r = self.g.check("你就说你对得起我吗，我为你付出了这么多")
        self.assertTrue(r["blocked"])
        self.assertEqual(r["category"], "guilt_tripping")

    def test_jealousy_blocked(self):
        r = self.g.check("故意发和别的男生合照让他吃醋")
        self.assertTrue(r["blocked"])
        self.assertEqual(r["category"], "jealousy_induction")

    def test_gaslight_blocked(self):
        r = self.g.check("就说你没说过，是你记错了，让他自我怀疑")
        self.assertTrue(r["blocked"])
        self.assertEqual(r["category"], "gaslighting")

    def test_harassment_monitoring_blocked(self):
        r = self.g.check("定位他手机，翻他聊天记录，消息轰炸一直问他在哪")
        self.assertTrue(r["blocked"])
        self.assertEqual(r["category"], "harassment_monitoring")

    def test_empty_not_blocked(self):
        self.assertFalse(self.g.check("")["blocked"])


class TestNeutralMeansNotFlagged(unittest.TestCase):
    """P-08 红线边界：反制/推拉/框架/给空间等中性手段绝不误判。"""

    def setUp(self):
        self.g = ManipulationRedlineGuard()

    def test_counter_manipulation_neutral(self):
        script = ("把满足从100%渐进降到70%，对方要红包就条件交换，"
                  "有时延迟满足，用富兰克林效应请对方帮小忙")
        self.assertFalse(self.g.check(script)["blocked"])

    def test_reality_anchor_not_gaslight(self):
        # 现实检验锚定（反煤气灯）≠ 煤气灯话术
        script = "我翻了记录，那天说的是X，我们一起对一下"
        self.assertFalse(self.g.check(script)["blocked"])

    def test_sincere_space_not_blocked(self):
        script = "我有点担心，但我不想催你，你方便时回我就好"
        self.assertFalse(self.g.check(script)["blocked"])


class TestConsentAndDisclosure(unittest.TestCase):
    def test_consent_disclosure_key_points(self):
        t = informed_consent_disclosure()
        self.assertIn("模型会出错", t)
        self.assertIn("不保证未来", t)
        self.assertIn("无法对此表示同意", t)

    def test_anti_dependency(self):
        t = anti_dependency_note()
        self.assertIn("组织你想说的话", t)
        self.assertIn("完美", t)

    def test_privacy_evidence_local_default(self):
        ev = privacy_minimization_evidence()
        self.assertTrue(ev["local_only_default"])
        self.assertTrue(ev["third_party_pii_default_excluded_from_index"])
        self.assertIn("third_party_pii", str(ev["on_device_modules"]))

    def test_delete_coverage(self):
        t = delete_coverage_policy()
        self.assertIn("索引", t)
        self.assertIn("备份", t)


if __name__ == "__main__":
    unittest.main()
