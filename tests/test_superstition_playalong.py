# -*- coding: utf-8 -*-
"""R3.3-46 迷信借力打力 单测。"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services.superstition_playalong import (  # noqa: E402
    detect_superstition, generate_playalong_response, maybe_playalong,
    _PLAYALONG_TEMPLATES,
)


class TestDetectSuperstition(unittest.TestCase):

    def test_detect_scorpio(self):
        r = detect_superstition("我是天蝎座的，你呢？")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "zodiac")
        self.assertIn("天蝎", r["quote"])

    def test_detect_zodiac_pairing(self):
        r = detect_superstition("我们星座很配诶")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "zodiac")

    def test_detect_bazi(self):
        r = detect_superstition("我妈说我们八字很合，让我们早点定下来")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "bazi")
        self.assertIn("八字", r["quote"])

    def test_detect_mbti(self):
        r = detect_superstition("我最近测了 MBTI，是 INFJ")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "mbti")

    def test_detect_tarot(self):
        r = detect_superstition("昨天抽塔罗，抽了张恋人牌")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "tarot")

    def test_detect_fengshui(self):
        r = detect_superstition("你家这个摆件放在财位上挺好的")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "fengshui")

    def test_detect_zodiac_cn(self):
        r = detect_superstition("我本命年犯太岁，今年特别倒霉")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "zodiac_cn")

    def test_detect_mercury(self):
        r = detect_superstition("最近水逆，老吵架")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "mercury")

    def test_no_superstition(self):
        self.assertIsNone(detect_superstition("今天吃了吗？晚上想吃什么？"))
        self.assertIsNone(detect_superstition(""))
        self.assertIsNone(detect_superstition(None))  # type: ignore[arg-type]


class TestPlayalongResponse(unittest.TestCase):

    def test_every_type_has_three_templates(self):
        for stype, tpls in _PLAYALONG_TEMPLATES.items():
            self.assertGreaterEqual(len(tpls), 3,
                                    f"{stype} 模板不足 3 条")

    def test_scorpio_generates_three_candidates(self):
        det = detect_superstition("我是天蝎座的")
        cands = generate_playalong_response(det)
        self.assertGreaterEqual(len(cands), 3)
        # 不能否定迷信
        for c in cands:
            self.assertNotIn("假的", c)
            self.assertNotIn("迷信", c)
            self.assertNotIn("别信", c)

    def test_bazi_generates_candidates(self):
        det = detect_superstition("我们八字很合")
        cands = generate_playalong_response(det)
        self.assertGreaterEqual(len(cands), 3)
        # 应该把话题引向关系/行动
        joined = " ".join(cands)
        self.assertTrue(("我们" in joined) or ("关系" in joined) or
                        ("接下来" in joined))

    def test_user_intent_appends_extra(self):
        det = detect_superstition("我们星座很配")
        cands = generate_playalong_response(det, user_intent="约见面")
        self.assertGreaterEqual(len(cands), 4)
        self.assertIn("约见面", cands[-1])

    def test_empty_detected_returns_empty(self):
        self.assertEqual(generate_playalong_response(None), [])  # type: ignore[arg-type]
        self.assertEqual(generate_playalong_response({}), [])

    def test_maybe_playalong_end_to_end(self):
        hit = maybe_playalong("我最近水逆", user_intent="别吵架")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["detected"]["type"], "mercury")
        self.assertGreaterEqual(len(hit["candidates"]), 3)

        miss = maybe_playalong("今晚吃啥")
        self.assertIsNone(miss)


if __name__ == "__main__":
    unittest.main()
