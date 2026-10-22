# -*- coding: utf-8 -*-
"""
R3.7 价格门两个纯函数的**边界矩阵**：_strictly_free_pricing / _proven_paid_pricing。

任务书要求三种边界：
  ① 已证实免费（五维全 0）→ _strictly_free_pricing=True
  ② 已证实付费（任一项解析出非 0）→ _proven_paid_pricing=True
  ③ 无证据（非 dict / 字段缺失 / None / 空串 / 解析不出来）→ fail-closed：
        _strictly_free_pricing=False（不收进免费池）
        _proven_paid_pricing=False（不判付费，但也不放行 —— 放行靠 price_gate_verdict）

注意：本文件只锁**纯函数当前行为**，不改算法；empty dict → True 是 R3.7 文档化口径
（字段缺失按 "0" 处理，与改动前 `str(p.get(k,"0"))=="0"` 逐字节一致）。
"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.free_model_router import (  # noqa: E402
    STRICT_FREE_COST_KEYS,
    _proven_paid_pricing,
    _strictly_free_pricing,
)


class TestStrictlyFreePricing(unittest.TestCase):
    """① 五维全 0 → True；任何一项非 0 / 不可解析 → False。"""

    def test_non_dict_inputs_are_not_free(self):
        for bad in (None, 123, "0", [0, 0], object()):
            self.assertFalse(_strictly_free_pricing(bad),
                             msg=f"非 dict {bad!r} 不应判免费")

    def test_all_zero_variants_are_free(self):
        # 字符串 "0" / "0.0" / "0E0"，数字 0，都算免费
        for variant in (
            {"prompt": "0", "completion": "0.0", "request": "0",
             "input_cache_read": "0", "input_cache_write": "0"},
            {"prompt": 0, "completion": 0, "request": 0,
             "input_cache_read": 0, "input_cache_write": 0},
            {"prompt": "0E0", "completion": 0},  # 缺省维度按 0
        ):
            self.assertTrue(_strictly_free_pricing(variant),
                            msg=f"全 0 变体 {variant!r} 应判免费")

    def test_any_non_zero_dimension_rejects(self):
        for key in STRICT_FREE_COST_KEYS:
            p = {k: 0 for k in STRICT_FREE_COST_KEYS}
            p[key] = 0.000001
            self.assertFalse(_strictly_free_pricing(p),
                             msg=f"{key} 非 0 应拒")

    def test_none_or_empty_or_garbage_value_rejects(self):
        for val in (None, "", "abc", float("nan")):
            p = {"prompt": val, "completion": 0}
            self.assertFalse(_strictly_free_pricing(p),
                             msg=f"不可解析值 {val!r} 应拒（fail-closed）")

    def test_empty_dict_treated_as_all_zero(self):
        # R3.7 文档化口径：字段缺失按 "0" 处理；这是**当前行为**，锁回归
        self.assertTrue(_strictly_free_pricing({}))


class TestProvenPaidPricing(unittest.TestCase):
    """② 任一项解析出非 0 → True；其余情况 False（无付费证据）。"""

    def test_non_dict_inputs_not_proven_paid(self):
        for bad in (None, 123, "0", [1], object()):
            self.assertFalse(_proven_paid_pricing(bad),
                             msg=f"非 dict {bad!r} 不应判付费")

    def test_any_non_zero_value_is_paid_evidence(self):
        for key in STRICT_FREE_COST_KEYS:
            p = {k: 0 for k in STRICT_FREE_COST_KEYS}
            p[key] = "0.0000025"
            self.assertTrue(_proven_paid_pricing(p),
                            msg=f"{key}=非 0 应算付费证据")

    def test_none_or_garbage_value_is_not_paid_evidence(self):
        # None / 空串 / 解析不出 → 证据不足，不算付费
        # 注意：bool 不是垃圾值 —— float(True)=1.0 是合法非 0 数，会算付费证据
        for val in (None, "", "abc", "free"):
            p = {"prompt": val, "completion": 0}
            self.assertFalse(_proven_paid_pricing(p),
                             msg=f"不可解析值 {val!r} 不算付费证据")

    def test_all_zero_is_not_proven_paid(self):
        self.assertFalse(_proven_paid_pricing(
            {"prompt": 0, "completion": "0", "request": 0}))
        self.assertFalse(_proven_paid_pricing({}))


class TestThreeWayBoundary(unittest.TestCase):
    """③ 任务书：免费必过 / 付费必拒 / 无证据 fail-closed。"""

    def test_free_evidence_side(self):
        # 已证实免费：五维全 0 → strictly_free=True，proven_paid=False
        p = {"prompt": "0", "completion": "0", "request": "0",
             "input_cache_read": "0", "input_cache_write": "0"}
        self.assertTrue(_strictly_free_pricing(p))
        self.assertFalse(_proven_paid_pricing(p))

    def test_paid_evidence_side(self):
        # 已证实付费：任一项非 0 → proven_paid=True，strictly_free=False
        p = {"prompt": "0.0000025", "completion": "0.00001", "request": 0}
        self.assertFalse(_strictly_free_pricing(p))
        self.assertTrue(_proven_paid_pricing(p))

    def test_no_evidence_side_is_fail_closed(self):
        # 无证据（pricing 缺失/不可解析）：strictly_free=False，proven_paid=False
        # —— 既不免费也不判付费；放行要靠 price_gate_verdict 的 (A)(B)(C)
        # 注意：{} 是 R3.7 文档化口径（字段缺失按 "0"），见 test_empty_dict_treated_as_all_zero
        for p in (None, {"prompt": None}, {"prompt": "abc"}, []):
            self.assertFalse(_strictly_free_pricing(p),
                             msg=f"{p!r} 无免费证据，不应进免费池")
            self.assertFalse(_proven_paid_pricing(p),
                             msg=f"{p!r} 无付费证据，不应判付费")


if __name__ == "__main__":
    unittest.main()
