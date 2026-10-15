# -*- coding: utf-8 -*-
"""第六批第三批：性能监控/话术个性化/攻略风格/资料优化。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.batch6_personalize import (performance_monitor, analyze_style_phrases,
    apply_play_style, profile_optimizer)


class TestBatch6C(unittest.TestCase):
    def test_performance_flags_slow(self):
        r = performance_monitor([{"module":"twin","ms":1200},{"module":"emotion","ms":50}])
        self.assertIn("twin", r["slow_modules"])

    def test_style_catchphrase(self):
        r = analyze_style_phrases(["哈哈","哈哈","哈哈","今天好开心"])
        self.assertIn("哈哈", r["catchphrase"])

    def test_play_style_weights(self):
        r = apply_play_style({"direction":"推进"}, "霸道型")
        self.assertEqual(r["style_profile"], "霸道型")
        self.assertGreater(r["weights"]["direct"], 0.6)

    def test_profile_optimizer_tips(self):
        r = profile_optimizer("照片", {"喜欢":"真诚"})
        self.assertTrue(r["suggestions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
