# -*- coding: utf-8 -*-
"""R3.3-23 数字诚实规范单测：
- cap_score 在不同证据数下永不返回 1.0 / 100%；
- cap_answer_confidence 压平绝对化措辞；
- UI 源码（app/ 层）不出现「成功率」字样。
"""
import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.confidence_cap import (  # noqa: E402
    cap_score, cap_answer_confidence, HONEST_SCORE_CEILING,
)


class TestCapScoreHonesty(unittest.TestCase):
    def test_never_returns_one_across_evidence_counts(self):
        """证据数 0..200 全覆盖：cap_score 永不返回 1.0。"""
        for n in (0, 1, 4, 5, 9, 10, 29, 30, 99, 100, 200, 1000):
            v = cap_score(1.0, n)
            self.assertLess(v, 1.0, f"evidence_n={n} 返回了 1.0")
            self.assertLessEqual(v, HONEST_SCORE_CEILING)

    def test_percent_input_also_capped(self):
        """0-100 制输入（99.9）同样被压。"""
        self.assertLess(cap_score(99.9, 0), 1.0)
        self.assertLessEqual(cap_score(100, 500), HONEST_SCORE_CEILING)

    def test_evidence_scales_ceiling(self):
        """证据越少封顶越低：n=0 应 ≤ n=200。"""
        self.assertLessEqual(cap_score(0.99, 0), cap_score(0.99, 200))

    def test_bad_input_safe(self):
        """坏输入不抛异常，保守返回 0。"""
        self.assertEqual(cap_score(None, 0), 0.0)
        self.assertEqual(cap_score("abc", 0), 0.0)


class TestCapAnswerConfidence(unittest.TestCase):
    def test_absolute_words_softened(self):
        out = cap_answer_confidence("他一定会答应，肯定成功")
        self.assertIn("大概率", out)
        self.assertNotIn("一定会", out)

    def test_empty_safe(self):
        self.assertEqual(cap_answer_confidence(None), "")
        self.assertEqual(cap_answer_confidence(""), "")


class TestUiWording(unittest.TestCase):
    """UI 文案层（app/ 下 .py）不得出现「成功率」字样。"""

    def test_no_success_rate_in_ui_sources(self):
        app_dir = os.path.join(_PROJECT_ROOT, "app")
        offenders = []
        for root, _dirs, files in os.walk(app_dir):
            if ".venv" in root or "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if "成功率" in line:
                            offenders.append(f"{path}:{i}:{line.strip()}")
        self.assertEqual(offenders, [], "UI 源码中出现「成功率」:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
