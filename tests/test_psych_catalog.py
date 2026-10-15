# -*- coding: utf-8 -*-
"""R3.3-44 心理学参考体系筛选清单 单测。

校验：
  - KEEP 每条都有 module 和 code_file 字段；
  - KEEP 每条都能映射到一个主线 tag；
  - catalog() 返回结构完整、stats 与列表长度一致；
  - 13 条核心理论全部在 KEEP 里。
"""
from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from services import psych_catalog as pc  # noqa: E402


CORE_THEORY_IDS = {
    "attachment_theory", "big_five", "dsm5_pd", "gottman_method",
    "sternberg_triangle", "rusbult_investment", "cognitive_dissonance",
    "social_penetration", "interdependence", "nvc", "gaslighting",
    "pua_schools", "path_dependence",
}

REQUIRED_FIELDS = ("id", "name", "origin", "why_keep",
                   "loveai_module", "code_file", "tags")


class TestPsychCatalog(unittest.TestCase):

    def test_keep_has_required_fields(self):
        for item in pc.KEEP:
            for f in REQUIRED_FIELDS:
                self.assertIn(f, item, f"KEEP[{item.get('id')}] 缺字段 {f}")
            self.assertTrue(item["loveai_module"],
                            f"KEEP[{item['id']}] loveai_module 为空")
            # code_file 允许为空（理论没有专属文件时），但必须是 str
            self.assertIsInstance(item["code_file"], str)

    def test_keep_each_has_mainline_tag(self):
        mainline_tags = {"love", "communication", "attachment",
                         "manipulation", "personality"}
        for item in pc.KEEP:
            self.assertTrue(set(item["tags"]) & mainline_tags,
                            f"KEEP[{item['id']}] 没有任何主线 tag")

    def test_core_theories_all_present(self):
        keep_ids = {x["id"] for x in pc.KEEP}
        missing = CORE_THEORY_IDS - keep_ids
        self.assertFalse(missing, f"核心理论缺失：{missing}")

    def test_reference_has_why_defer(self):
        for item in pc.REFERENCE:
            self.assertIn("why_defer", item,
                          f"REFERENCE[{item.get('id')}] 缺 why_defer")
            self.assertTrue(item["why_defer"])

    def test_catalog_structure(self):
        cat = pc.catalog()
        self.assertEqual(cat["stats"]["keep_count"], len(pc.KEEP))
        self.assertEqual(cat["stats"]["reference_count"], len(pc.REFERENCE))
        self.assertEqual(cat["keep"], pc.KEEP)
        self.assertEqual(cat["reference"], pc.REFERENCE)
        self.assertIn("version", cat)
        self.assertIn("mainline", cat)

    def test_keep_and_reference_disjoint(self):
        keep_ids = {x["id"] for x in pc.KEEP}
        ref_ids = {x["id"] for x in pc.REFERENCE}
        self.assertFalse(keep_ids & ref_ids,
                         f"重复 id：{keep_ids & ref_ids}")


if __name__ == "__main__":
    unittest.main()
