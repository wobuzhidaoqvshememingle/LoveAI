# -*- coding: utf-8 -*-
"""R3.3-22 单轮分析输出面板单测：六字段全渲染 / 缺字段显示「—」/ 把握度封顶 / 措辞诚实。"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from app.widgets.analysis_output_card import AnalysisOutputCard  # noqa: E402


MOCK_RESULT = {
    "intent": "对方在试探投入度",
    "confidence": 0.92,
    "evidence_n": 3,
    "strategy": "渐进升温，不主动加码",
    "reply_candidates": ["候选A：刚路过一家店拍给你看",
                         "候选B：不着急回，你忙完再说",
                         "候选C：今天天气不错，想起你说的咖啡馆"],
    "risk": "需警惕过度投入暴露需求感",
    "prediction": "她大概率会轻松接话，不会冷场",
}


class TestAnalysisOutputCard(unittest.TestCase):
    def setUp(self):
        self.card = AnalysisOutputCard()

    def test_all_fields_rendered(self):
        """mock dict 渲染后六个字段全部出现。"""
        self.card.set_result(MOCK_RESULT)
        self.assertIn("试探投入度", self.card.field_text("intent"))
        self.assertIn("渐进升温", self.card.field_text("strategy"))
        self.assertIn("候选A", self.card.field_text("candidates"))
        self.assertIn("候选B", self.card.field_text("candidates"))
        self.assertIn("候选C", self.card.field_text("candidates"))
        self.assertIn("过度投入", self.card.field_text("risk"))
        self.assertIn("轻松接话", self.card.field_text("prediction"))

    def test_missing_fields_show_dash(self):
        """空 dict 渲染：六字段全部显示「—」而非空白。"""
        self.card.set_result({})
        for key in ("intent", "confidence", "strategy", "candidates", "risk", "prediction"):
            self.assertEqual(self.card.field_text(key), "—", key)

    def test_confidence_capped_by_evidence(self):
        """把握度走 cap_score：证据仅 3 条时，原始 0.92 被压到 ≤30%。"""
        self.card.set_result({"confidence": 0.92, "evidence_n": 3})
        txt = self.card.field_text("confidence")
        self.assertNotEqual(txt, "—")
        self.assertIn("%", txt)
        percent = float(txt.split("%")[0])
        self.assertLessEqual(percent, 30.0)

    def test_no_success_rate_wording(self):
        """措辞规范：把握度字段显示「预测有效度」语义，不出现「成功率」。"""
        self.card.set_result(MOCK_RESULT)
        for key in ("intent", "confidence", "strategy", "candidates", "risk", "prediction"):
            self.assertNotIn("成功率", self.card.field_text(key), key)


if __name__ == "__main__":
    unittest.main()
