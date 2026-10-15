# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - StrategyAdvisor 单元测试（策略适配矩阵）

覆盖：
  - 四种依恋类型（回避/焦虑/安全/恐惧）的推荐与禁忌
  - 冲突处理风格匹配
  - 爱的五种语言匹配
  - 多维度交叉匹配（回避型+逃避型冲突）
  - 不同关系阶段的策略差异
  - 缺字段时的防御性默认值
  - 策略变更检测（因时制宜）
  - reasoning 非空且含人格特征描述
  - 与真实 21 维嵌套画像结构兼容
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

from models.database import Database  # noqa: E402
from services.strategy_advisor import StrategyAdvisor  # noqa: E402


class TestStrategyAdvisor(unittest.TestCase):
    """StrategyAdvisor 测试类。"""

    def setUp(self) -> None:
        self.adv = StrategyAdvisor(None)

    # ------------------------------------------------------------------ #
    # 四种依恋类型
    # ------------------------------------------------------------------ #
    def test_attachment_avoidant(self) -> None:
        """回避型：推荐给空间/低压/间接关心，禁忌逼问/冷战/情感施压。"""
        r = self.adv.analyze({"attachment": "avoidant"}, 4)
        for tip in ["给对方空间", "低压沟通", "间接表达关心"]:
            self.assertIn(tip, r["recommended_raw"])
        for forb in ["逼问", "情感施压"]:
            self.assertIn(forb, r["forbidden_raw"])
        # 推荐技巧应带攻略目的说明
        self.assertIn("目的：", r["recommended"][0])

    def test_attachment_anxious(self) -> None:
        """焦虑型：推荐及时回应/明确表达/持续确认，禁忌忽冷忽热/玩消失/欲擒故纵/冷战。"""
        r = self.adv.analyze({"attachment": "anxious"}, 4)
        for tip in ["及时回应", "明确表达爱意", "持续确认"]:
            self.assertIn(tip, r["recommended_raw"])
        for forb in ["忽冷忽热", "玩消失", "欲擒故纵", "冷战"]:
            self.assertIn(forb, r["forbidden_raw"])
        # 禁忌技巧应带禁忌原因说明
        self.assertIn("禁忌原因：", r["forbidden"][0])

    def test_attachment_secure(self) -> None:
        """安全型：推荐自然互动/真诚沟通。"""
        r = self.adv.analyze({"attachment": "secure"}, 4)
        self.assertIn("自然互动", r["recommended_raw"])
        self.assertIn("真诚沟通", r["recommended_raw"])

    def test_attachment_fearful(self) -> None:
        """恐惧型：推荐温和稳定/逐步建立信任，禁忌突然亲密/逼迫承诺。"""
        r = self.adv.analyze({"attachment": "fearful"}, 4)
        self.assertIn("温和稳定", r["recommended_raw"])
        self.assertIn("逐步建立信任", r["recommended_raw"])
        self.assertIn("突然亲密", r["forbidden_raw"])
        self.assertIn("逼迫承诺", r["forbidden_raw"])

    # ------------------------------------------------------------------ #
    # 冲突处理风格
    # ------------------------------------------------------------------ #
    def test_conflict_avoiding(self) -> None:
        """逃避型冲突：推荐安全氛围/非暴力沟通，禁忌当场对峙/逼表态。"""
        r = self.adv.analyze({"conflict_style": "avoiding"}, 4)
        self.assertIn("创造安全氛围", r["recommended_raw"])
        self.assertIn("当场对峙", r["forbidden_raw"])
        self.assertIn("逼表态", r["forbidden_raw"])

    def test_conflict_competing(self) -> None:
        """攻击型冲突：推荐理性锚点，禁忌情绪化对抗/人身攻击。"""
        r = self.adv.analyze({"conflict_style": "competing"}, 4)
        self.assertIn("就事论事", r["recommended_raw"])
        self.assertIn("人身攻击", r["forbidden_raw"])

    # ------------------------------------------------------------------ #
    # 爱的五种语言
    # ------------------------------------------------------------------ #
    def test_love_language_acts_of_service(self) -> None:
        """服务行动爱语：推荐帮忙做事，禁忌只说不做。"""
        r = self.adv.analyze({"love_language": "acts_of_service"}, 4)
        self.assertIn("帮忙做事", r["recommended_raw"])
        self.assertIn("只说不做", r["forbidden_raw"])

    def test_love_language_words_of_affirmation(self) -> None:
        """肯定言辞爱语：推荐口头赞美，禁忌贬低。"""
        r = self.adv.analyze({"love_language": "words_of_affirmation"}, 4)
        self.assertIn("口头赞美", r["recommended_raw"])
        self.assertIn("贬低", r["forbidden_raw"])

    # ------------------------------------------------------------------ #
    # 多维度交叉匹配
    # ------------------------------------------------------------------ #
    def test_cross_dimension_avoidant_plus_avoiding(self) -> None:
        """回避型依恋+逃避型冲突：两维度的推荐都应出现，禁忌合并。"""
        persona = {"attachment": "avoidant", "conflict_style": "avoiding",
                   "love_language": "acts_of_service", "boundary": "high",
                   "differentiation": "high", "emotional_stability": "medium"}
        r = self.adv.analyze(persona, 4)
        # 依恋维度推荐
        self.assertIn("给对方空间", r["recommended_raw"])
        # 冲突维度推荐
        self.assertIn("创造安全氛围", r["recommended_raw"])
        # 两维度禁忌合并
        self.assertIn("逼问", r["forbidden_raw"])
        self.assertIn("当场对峙", r["forbidden_raw"])

    def test_different_personas_yield_different_strategy(self) -> None:
        """同一场景下三种人设推荐策略必须不同（非同一模板）。"""
        r1 = self.adv.analyze({"attachment": "avoidant", "conflict_style": "avoiding"}, 4)
        r2 = self.adv.analyze({"attachment": "anxious", "conflict_style": "compromising"}, 4)
        r3 = self.adv.analyze({"attachment": "secure", "conflict_style": "collaborating"}, 4)
        self.assertNotEqual(r1["recommended"], r2["recommended"])
        self.assertNotEqual(r1["recommended"], r3["recommended"])
        self.assertNotEqual(r2["recommended"], r3["recommended"])

    # ------------------------------------------------------------------ #
    # 关系阶段差异（因时制宜）
    # ------------------------------------------------------------------ #
    def test_stage_guidance_differs(self) -> None:
        """初识期与热恋期的 reasoning 节奏说明应不同。"""
        r_early = self.adv.analyze({"attachment": "secure"}, 1)
        r_hot = self.adv.analyze({"attachment": "secure"}, 6)
        self.assertIn("初识期", r_early["reasoning"])
        self.assertIn("热恋期", r_hot["reasoning"])
        self.assertNotEqual(r_early["reasoning"], r_hot["reasoning"])

    # ------------------------------------------------------------------ #
    # 缺字段防御性默认值
    # ------------------------------------------------------------------ #
    def test_empty_persona_defaults(self) -> None:
        """空画像不应崩溃，且有合理默认推荐。"""
        r = self.adv.analyze({}, 5)
        self.assertIsInstance(r["recommended"], list)
        self.assertGreater(len(r["recommended"]), 0)
        self.assertIsInstance(r["forbidden"], list)
        # 缺省依恋应为 secure
        self.assertIn("自然互动", r["recommended_raw"])

    def test_none_persona(self) -> None:
        """None 画像不崩溃。"""
        r = self.adv.analyze(None, 5)
        self.assertIn("recommended", r)

    # ------------------------------------------------------------------ #
    # reasoning 内容
    # ------------------------------------------------------------------ #
    def test_reasoning_nonempty_and_mentions_traits(self) -> None:
        """reasoning 非空且包含对方人格特征描述。"""
        r = self.adv.analyze({"attachment": "avoidant", "conflict_style": "avoiding"}, 4)
        self.assertTrue(len(r["reasoning"]) > 20)
        self.assertIn("依恋", r["reasoning"])
        self.assertIn("推荐技巧", r["reasoning"])
        self.assertTrue(r["style_tips"])
        self.assertTrue(r["conflict_approach"])

    # ------------------------------------------------------------------ #
    # 真实 21 维嵌套画像兼容
    # ------------------------------------------------------------------ #
    def test_real_nested_persona(self) -> None:
        """兼容 PersonaAnalyzer 真实输出（嵌套 dict + 中文取值）。"""
        real = {
            "attachment": {"type": "回避型"},
            "conflict_style": {"primary_key": "cold_war", "primary": "冷战逃避"},
            "love_languages": {"primary_key": "acts_of_service", "primary": "服务行动"},
            "boundaries": {"personal_boundary": "清晰"},
            "self_differentiation": {"level": "自我分化高"},
            "emotion_pattern": {"volatility": "低", "pattern": "积极主导"},
        }
        r = self.adv.analyze(real, 4)
        # 中文"回避型"应归一到 avoidant
        self.assertIn("给对方空间", r["recommended_raw"])
        self.assertIn("逼问", r["forbidden_raw"])

    # ------------------------------------------------------------------ #
    # 因时制宜：记录 + 变更检测（需要 db）
    # ------------------------------------------------------------------ #
    def test_strategy_change_detection(self) -> None:
        """记录回避型→画像改安全型→检测到策略变更。"""
        tmpdir = tempfile.mkdtemp(prefix="loveai_strat_test_")
        try:
            db = Database(os.path.join(tmpdir, "t.db"))
            db.init_tables()
            adv = StrategyAdvisor(db)
            avoidant = {"attachment": "avoidant", "conflict_style": "avoiding"}
            secure = {"attachment": "secure", "conflict_style": "collaborating"}

            r1 = adv.analyze(avoidant, 4)
            adv.record_strategy("p1", avoidant, r1)

            # 首次历史：无更早记录，changed=False
            first = adv.detect_strategy_change("p1", r1)
            self.assertFalse(first["changed"])

            # 画像变为安全型
            r2 = adv.analyze(secure, 4)
            change = adv.detect_strategy_change("p1", r2)
            self.assertTrue(change["changed"])
            self.assertIn("依恋", change["reason"])

            # 记录新策略后再检，应不再报变更
            adv.record_strategy("p1", secure, r2)
            change2 = adv.detect_strategy_change("p1", r2)
            self.assertFalse(change2["changed"])

            history = adv.get_strategy_history("p1")
            self.assertEqual(len(history), 2)
            db.engine.dispose()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
