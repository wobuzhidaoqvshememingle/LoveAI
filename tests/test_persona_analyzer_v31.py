# -*- coding: utf-8 -*-
"""
LoveAI v3.1 - PersonaAnalyzer 新增能力单元测试

覆盖：
  - classify_connection_type：五种连接类型判定
  - _weight_by_event_type：事件权重分级
  - apply_user_correction + _apply_corrections_to_profile：用户修正覆盖
  - dealbreaker 硬过滤不被自动分析覆盖
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

from services.persona_analyzer import PersonaAnalyzer  # noqa: E402


def _make_profile(attachment_type="安全型", value_score=70,
                  emotion_pattern="中性平稳", volatility="低",
                  risk_level="低") -> dict:
    return {
        "attachment": {"primary_type": attachment_type, "type": attachment_type},
        "values_match": {"score": value_score},
        "emotion_pattern": {"pattern": emotion_pattern, "volatility": volatility},
        "risk_warning": {"overall_level": risk_level},
        "investment_balance": {},
    }


class TestConnectionType(unittest.TestCase):
    def setUp(self):
        self.an = PersonaAnalyzer(db=None)

    def test_mirror_type(self):
        """安全型 + 价值观不冲突 → 镜像型。"""
        r = self.an.classify_connection_type(_make_profile("安全型", 70))
        self.assertEqual(r["type"], "镜像型")
        self.assertIn("很像", r["label"])

    def test_opposing_type(self):
        """价值观差异大 + 消极情绪 → 对立型。"""
        r = self.an.classify_connection_type(
            _make_profile("恐惧型", 30, emotion_pattern="消极倾向"))
        self.assertEqual(r["type"], "对立型")

    def test_shared_future_type(self):
        """价值观高分 + 共同未来信号 → 共同旅程型。"""
        p = _make_profile("安全型", 80)
        p["investment_balance"] = {"共同": True}
        r = self.an.classify_connection_type(p)
        self.assertEqual(r["type"], "共同旅程型")

    def test_growth_type(self):
        """积极主导 + 成长信号 → 成长型。"""
        r = self.an.classify_connection_type(
            _make_profile("安全型", 60, emotion_pattern="积极主导"))
        self.assertIn(r["type"], ("成长型", "镜像型"))

    def test_complementary_type(self):
        """回避型 + 价值观不冲突 → 互补型。"""
        r = self.an.classify_connection_type(
            _make_profile("疏离型回避", 55))
        self.assertEqual(r["type"], "互补型")

    def test_returns_breakdown(self):
        r = self.an.classify_connection_type(_make_profile())
        self.assertIn("score_breakdown", r)
        self.assertIn("attachment_type", r["score_breakdown"])


class TestEventWeight(unittest.TestCase):
    def setUp(self):
        self.an = PersonaAnalyzer(db=None)

    def test_key_event_weight(self):
        self.assertEqual(self.an._weight_by_event_type("我喜欢你，我们在一起吧"), 3.0)
        self.assertEqual(self.an._weight_by_event_type("我们分手吧"), 3.0)
        self.assertEqual(self.an._weight_by_event_type("我答应你周末陪你"), 3.0)

    def test_deep_topic_weight(self):
        self.assertEqual(self.an._weight_by_event_type("我小时候原生家庭不太好"), 2.0)
        self.assertEqual(self.an._weight_by_event_type("我们未来规划一下吧"), 2.0)

    def test_shallow_topic_weight(self):
        self.assertEqual(self.an._weight_by_event_type("晚安啦~"), 0.3)
        self.assertEqual(self.an._weight_by_event_type("哈哈嗯"), 0.3)

    def test_medium_topic_weight(self):
        self.assertEqual(self.an._weight_by_event_type("今天上班有点忙"), 1.0)

    def test_empty_text(self):
        self.assertEqual(self.an._weight_by_event_type(""), 1.0)


class TestUserCorrection(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_corr_")
        self.an = PersonaAnalyzer(db=None)
        # 把修正目录重定向到临时目录
        self._patch = patch.object(
            PersonaAnalyzer, "_corrections_dir",
            return_value=self.tmpdir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_apply_correction_overrides_field(self):
        rec = self.an.apply_user_correction(
            "p1", "attachment.primary_type", "回避型", "安全型",
            reason="他其实只是忙")
        self.assertEqual(rec["new_value"], "安全型")
        # 加载并叠加到画像
        profile = _make_profile("回避型")
        out = self.an._apply_corrections_to_profile(profile, "p1")
        self.assertEqual(out["attachment"]["primary_type"], "安全型")
        self.assertIn("user_corrections_applied", out)

    def test_dealbreaker_never_overwritten(self):
        """dealbreaker 标记后，再次分析也会被覆盖回用户值。"""
        self.an.apply_user_correction(
            "p2", "psychology.dark_triad", "自恋", "正常",
            reason="他绝对不是自恋的人", is_dealbreaker=True)
        profile = {"psychology": {"dark_triad": "自恋"}}
        out = self.an._apply_corrections_to_profile(profile, "p2")
        self.assertEqual(out["psychology"]["dark_triad"], "正常")
        self.assertIn("dealbreakers", out)
        # 再次叠加（模拟下次 analyze），仍然是用户值
        out2 = self.an._apply_corrections_to_profile(
            {"psychology": {"dark_triad": "自恋"}}, "p2")
        self.assertEqual(out2["psychology"]["dark_triad"], "正常")

    def test_no_corrections_returns_unchanged(self):
        profile = _make_profile("安全型")
        out = self.an._apply_corrections_to_profile(profile, "nobody")
        self.assertNotIn("user_corrections_applied", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
