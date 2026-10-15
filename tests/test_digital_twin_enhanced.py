# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - DigitalTwin 增强能力单元测试

覆盖新增 4 个方法：
  - predict_reaction_multi_scene（多场景预测）
  - adversarial_debate（对抗性辩驳）
  - analyze_event_impact（事件影响分析）
  - multi_dimension_analysis（三维情感分析）

每个方法至少 2 个用例：
  - LLM 路径（mock llm.chat 返回合法 JSON）
  - 规则兜底路径（llm.chat 抛异常 / 返回无法解析内容）
"""
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from services.digital_twin import DigitalTwin  # noqa: E402


class TestDigitalTwinEnhanced(unittest.TestCase):
    """DigitalTwin 增强能力测试类（临时 SQLite + 临时 data_dir）。"""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="loveai_twin_enh_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.data_dir = os.path.join(self.tmpdir, "data")
        self.db = Database(self.db_path)
        self.db.init_tables()
        self.db.execute(
            "INSERT INTO persons (id, nickname, gender, occupation) "
            "VALUES (:id, :n, :g, :o)",
            {"id": "p1", "n": "苏晚", "g": "女", "o": "设计师"},
        )
        self.llm = MagicMock()
        self.rag = MagicMock()
        self.twin = DigitalTwin("p1", self.db, self.llm, self.rag,
                               data_dir=self.data_dir)

    def tearDown(self) -> None:
        try:
            self.db.engine.dispose()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 1. predict_reaction_multi_scene
    # ------------------------------------------------------------------ #
    def test_multi_scene_llm_path(self) -> None:
        """LLM 路径：应返回 5 个场景的预测，source=llm。"""
        self.llm.chat.return_value = (
            '{"scenes": {'
            '"暧昧期": {"emotion":"期待","inner_thought":"他在试探我",'
            '"possible_replies":["哈哈你说呢","我在听"],"confidence":0.8},'
            '"冷战期": {"emotion":"冷淡","inner_thought":"不想理他",'
            '"possible_replies":["嗯"],"confidence":0.7},'
            '"热恋期": {"emotion":"开心","inner_thought":"宝贝找我了",'
            '"possible_replies":["怎么啦宝贝"],"confidence":0.9},'
            '"分手期": {"emotion":"防备","inner_thought":"想干嘛",'
            '"possible_replies":["有事吗"],"confidence":0.6},'
            '"朋友区": {"emotion":"平静","inner_thought":"正常聊天",'
            '"possible_replies":["哈哈"],"confidence":0.75}'
            '}}'
        )
        out = self.twin.predict_reaction_multi_scene("在干嘛呢")
        self.assertEqual(out["source"], "llm")
        scenes = out["scenes"]
        for sc in ("暧昧期", "冷战期", "热恋期", "分手期", "朋友区"):
            self.assertIn(sc, scenes)
            self.assertIn("emotion", scenes[sc])
            self.assertIn("possible_replies", scenes[sc])
            self.assertIn("confidence", scenes[sc])
            self.assertIsInstance(scenes[sc]["possible_replies"], list)

    def test_multi_scene_rule_fallback(self) -> None:
        """规则兜底：LLM 抛异常时，应返回 5 个场景模板，source=rule_fallback。"""
        self.llm.chat.side_effect = RuntimeError("LLM 不可用")
        out = self.twin.predict_reaction_multi_scene("在干嘛呢")
        self.assertEqual(out["source"], "rule_fallback")
        scenes = out["scenes"]
        self.assertEqual(len(scenes), 5)
        for sc, node in scenes.items():
            self.assertEqual(node["confidence"], 0.5)
            self.assertGreater(len(node["possible_replies"]), 0)
            self.assertTrue(node["inner_thought"])

    def test_multi_scene_custom_scenes(self) -> None:
        """自定义场景列表：只预测指定场景。"""
        self.llm.chat.side_effect = RuntimeError("离线")
        out = self.twin.predict_reaction_multi_scene(
            "周末有空吗", scenes=["暧昧期", "热恋期"])
        self.assertEqual(out["source"], "rule_fallback")
        self.assertIn("暧昧期", out["scenes"])
        self.assertIn("热恋期", out["scenes"])
        self.assertNotIn("分手期", out["scenes"])

    # ------------------------------------------------------------------ #
    # 2. adversarial_debate
    # ------------------------------------------------------------------ #
    def test_adversarial_debate_llm_path(self) -> None:
        """LLM 路径：返回辩论记录 + 最终裁决。"""
        self.llm.chat.return_value = (
            '{"debate": ['
            '{"round":1,"bull":"这是积极信号","bear":"也可能是客套"},'
            '{"round":2,"bull":"她主动找话题了","bear":"只是礼貌回复"},'
            '{"round":3,"bull":"综合偏积极","bear":"仍需观察"}'
            '],'
            '"final_verdict":"整体偏积极，但需持续观察",'
            '"leaning":"bull","confidence":0.65}'
        )
        out = self.twin.adversarial_debate("她主动问我吃饭了没", rounds=3)
        self.assertEqual(out["source"], "llm")
        self.assertEqual(len(out["debate"]), 3)
        self.assertEqual(out["leaning"], "bull")
        self.assertIn("final_verdict", out)
        self.assertTrue(out["final_verdict"])

    def test_adversarial_debate_rule_fallback_positive(self) -> None:
        """规则兜底：正面消息应 leaning=bull。"""
        self.llm.chat.side_effect = RuntimeError("离线")
        out = self.twin.adversarial_debate("我好想你呀，今天好开心", rounds=2)
        self.assertEqual(out["source"], "rule_fallback")
        self.assertEqual(out["leaning"], "bull")
        self.assertEqual(len(out["debate"]), 2)
        self.assertEqual(out["confidence"], 0.5)

    def test_adversarial_debate_rule_fallback_negative(self) -> None:
        """规则兜底：负面消息应 leaning=bear。"""
        self.llm.chat.side_effect = RuntimeError("离线")
        out = self.twin.adversarial_debate("我们冷战吧，我累了不想理你",
                                          rounds=1)
        self.assertEqual(out["leaning"], "bear")
        self.assertEqual(len(out["debate"]), 1)

    # ------------------------------------------------------------------ #
    # 3. analyze_event_impact
    # ------------------------------------------------------------------ #
    def test_analyze_event_impact_llm_path(self) -> None:
        """LLM 路径：返回三维影响分 + 维度偏移 + 恢复周期。"""
        self.llm.chat.return_value = (
            '{"short_term":25,"mid_term":20,"long_term":30,'
            '"dimensions":{"好感度":20,"信任度":10,"亲密度":15,"期待值":25},'
            '"recovery_days":0,"advice":"继续推进关系"}'
        )
        out = self.twin.analyze_event_impact("表白", "我在餐厅向她表白了")
        self.assertEqual(out["source"], "llm")
        self.assertEqual(out["short_term"], 25)
        self.assertEqual(out["mid_term"], 20)
        self.assertEqual(out["long_term"], 30)
        self.assertIn("好感度", out["dimensions"])
        self.assertGreaterEqual(out["recovery_days"], 0)

    def test_analyze_event_impact_rule_fallback_吵架(self) -> None:
        """规则兜底：吵架事件应负向、恢复周期合理。"""
        self.llm.chat.side_effect = RuntimeError("离线")
        out = self.twin.analyze_event_impact("吵架", "因为小事吵了一架")
        self.assertEqual(out["source"], "rule_fallback")
        self.assertLess(out["short_term"], 0)
        self.assertLess(out["mid_term"], 0)
        self.assertGreater(out["recovery_days"], 0)
        self.assertEqual(out["confidence"], 0.5)

    def test_analyze_event_impact_rule_fallback_第三方(self) -> None:
        """规则兜底：第三方介入应重度负向、恢复周期长。"""
        self.llm.chat.side_effect = RuntimeError("离线")
        out = self.twin.analyze_event_impact("第三方介入",
                                            "发现她和别的男生暧昧")
        self.assertLess(out["short_term"], -30)
        self.assertGreater(out["recovery_days"], 10)

    # ------------------------------------------------------------------ #
    # 4. multi_dimension_analysis
    # ------------------------------------------------------------------ #
    def test_multi_dimension_analysis_llm_path(self) -> None:
        """LLM 路径：返回三大维度 + 综合判断。"""
        self.llm.chat.return_value = (
            '{"fundamental":{"投入度":70,"承诺度":60,"匹配度":65},'
            '"technical":{"回复速度":80,"字数比":55,"主动率":70,"话题延续":60},'
            '"emotional":{"正负情绪比":75,"情绪波动":40,"情绪深度":65},'
            '"comprehensive":"关系整体向好"}'
        )
        out = self.twin.multi_dimension_analysis(
            "今天想你了，周末一起吃饭吧")
        self.assertEqual(out["source"], "llm")
        self.assertIn("fundamental", out)
        self.assertIn("technical", out)
        self.assertIn("emotional", out)
        for dim in ("投入度", "承诺度", "匹配度"):
            self.assertIn(dim, out["fundamental"])
        self.assertTrue(out["comprehensive"])

    def test_multi_dimension_analysis_rule_fallback_positive(self) -> None:
        """规则兜底：正面文本应正负情绪比 > 50。"""
        self.llm.chat.side_effect = RuntimeError("离线")
        out = self.twin.multi_dimension_analysis(
            "我好喜欢你，今天真的好开心，想你了抱抱")
        self.assertEqual(out["source"], "rule_fallback")
        self.assertGreater(out["emotional"]["正负情绪比"], 50)
        self.assertIn("fundamental", out)
        self.assertIn("technical", out)

    def test_multi_dimension_analysis_rule_fallback_negative(self) -> None:
        """规则兜底：负面文本应正负情绪比 < 50。"""
        self.llm.chat.side_effect = RuntimeError("离线")
        out = self.twin.multi_dimension_analysis(
            "我累了，失望透顶，不想理你，冷战吧")
        self.assertEqual(out["source"], "rule_fallback")
        self.assertLess(out["emotional"]["正负情绪比"], 50)

    def test_multi_dimension_analysis_empty_text(self) -> None:
        """规则兜底：空文本不应抛异常，返回中性默认。"""
        self.llm.chat.side_effect = RuntimeError("离线")
        out = self.twin.multi_dimension_analysis("")
        self.assertEqual(out["source"], "rule_fallback")
        self.assertIn("fundamental", out)


if __name__ == "__main__":
    unittest.main()
