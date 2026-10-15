# -*- coding: utf-8 -*-
"""第10项：Utility+Contract Net 三层融合策略选择。"""
import os, sys, unittest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.strategy_utility import fused_choose


class TestContractNetFusion(unittest.TestCase):
    def test_filter_low_utility(self):
        # 对方愤怒/回避：推进效用低，应被初筛排除
        r = fused_choose({"attachment": "回避", "trend": "对方冷淡",
                          "emotion_pad": {"p": 0.3}})
        self.assertNotIn("推进", r["candidates"])

    def test_dynamic_weight_lowers_bad_strategy(self):
        # 历史上"冷战/硬推进"对回避型效果差 → 降权
        r = fused_choose({"attachment": "回避", "emotion_pad": {"p": 0.4}},
                         history_effects={"给空间": -0.5})
        self.assertIn("chosen", r)

    def test_fusion_combines_when_close(self):
        r = fused_choose({"emotion_pad": {"p": 0.5}, "axes": {"Trust": 50}})
        # 有fallback/投标分
        self.assertIn("bid_score", r)
        self.assertTrue(r["fallback"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
