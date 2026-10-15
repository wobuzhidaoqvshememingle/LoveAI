# -*- coding: utf-8 -*-
"""
LoveAI v3.2 - local_model_manager 离线确定性单测（不连真机 Ollama、不联网）。

覆盖：
  1. build_modelfile：FROM 绝对路径 + ChatML TEMPLATE + num_ctx + stop；
  2. ensure_registered 幂等：已存在跳过、缺失则 create、文件缺失标 missing_file、
     Ollama 不可用返回 OLLAMA_UNAVAILABLE；全程不删除任何模型；
  3. model_for / division_of_labor 分工固化（1.5B 主力 / 0.5B 兜底 / smollm2 保留）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import local_model_manager as lmm  # noqa: E402


class TestBuildModelfile(unittest.TestCase):
    def test_modelfile_contains_required_parts(self):
        mf = lmm.build_modelfile("loveai-qwen15b", r"C:\models\qwen15b.gguf")
        self.assertIn(r"FROM C:\models\qwen15b.gguf", mf)
        self.assertIn("TEMPLATE", mf)
        self.assertIn("<|im_start|>", mf)
        self.assertIn("PARAMETER num_ctx 8192", mf)
        self.assertIn("PARAMETER stop <|im_end|>", mf)
        self.assertIn("PARAMETER temperature", mf)

    def test_unknown_tag_raises(self):
        with self.assertRaises(KeyError):
            lmm.build_modelfile("nope", "x.gguf")


class TestEnsureRegistered(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        # 物理放置两个随包 gguf（空文件即可，仅校验存在性）
        for rel in ("qwen2.5-1.5b-instruct-q4_k_m.gguf",
                    "qwen2.5-0.5b-instruct-q4_k_m.gguf"):
            with open(os.path.join(self.root, rel), "wb") as f:
                f.write(b"GGUF")

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_present_skips_create(self):
        created = []
        status = lmm.ensure_registered(
            self.root,
            create_runner=lambda tag, mf: created.append(tag) or (0, "ok"),
            tags_lister=lambda: {"loveai-qwen15b", "loveai-qwen05b"},
        )
        self.assertEqual(status["status"], "OK")
        self.assertEqual(created, [])  # 已存在 -> 绝不重复 create
        for tag, info in status["models"].items():
            self.assertEqual(info["status"], "present")

    def test_missing_tags_created_idempotently(self):
        created = []

        def runner(tag, mf):
            created.append(tag)
            self.assertTrue(os.path.isfile(mf))  # modelfile 已落盘
            return 0, "success"

        status = lmm.ensure_registered(
            self.root, create_runner=runner, tags_lister=lambda: set())
        self.assertEqual(status["status"], "OK")
        self.assertIn("loveai-qwen15b", created)
        self.assertIn("loveai-qwen05b", created)
        for tag, info in status["models"].items():
            self.assertEqual(info["status"], "created", tag)

    def test_create_failure_marked_failed_not_raise(self):
        status = lmm.ensure_registered(
            self.root,
            create_runner=lambda tag, mf: (1, "boom"),
            tags_lister=lambda: set(),
        )
        self.assertEqual(status["status"], "OK")
        for tag, info in status["models"].items():
            self.assertEqual(info["status"], "failed")
            self.assertEqual(info["exit_code"], 1)

    def test_missing_gguf_marked_missing_file(self):
        os.remove(os.path.join(self.root, "qwen2.5-1.5b-instruct-q4_k_m.gguf"))
        status = lmm.ensure_registered(
            self.root,
            create_runner=lambda tag, mf: (0, "ok"),
            tags_lister=lambda: set(),
        )
        self.assertEqual(status["models"]["loveai-qwen15b"]["status"],
                         "missing_file")
        self.assertEqual(status["models"]["loveai-qwen05b"]["status"],
                         "created")

    def test_ollama_unavailable_returns_flag(self):
        def boom():
            raise OSError("refused")
        status = lmm.ensure_registered(
            self.root, create_runner=lambda tag, mf: (0, "ok"),
            tags_lister=boom)
        self.assertEqual(status["status"], "OLLAMA_UNAVAILABLE")
        self.assertEqual(status["models"], {})


class TestDivisionOfLabor(unittest.TestCase):
    def test_model_for_mapping(self):
        self.assertEqual(lmm.model_for("dialogue"), "loveai-qwen15b")
        self.assertEqual(lmm.model_for("analysis"), "loveai-qwen15b")
        self.assertEqual(lmm.model_for("draft"), "loveai-qwen15b")
        self.assertEqual(lmm.model_for("intent"), "loveai-qwen05b")
        self.assertEqual(lmm.model_for("quick"), "loveai-qwen05b")
        # 未知/复杂任务默认离线主力
        self.assertEqual(lmm.model_for("whatever"), "loveai-qwen15b")

    def test_registry_file_mapping(self):
        self.assertIn("1.5b", lmm.REGISTRY["loveai-qwen15b"]["file"])
        self.assertIn("0.5b", lmm.REGISTRY["loveai-qwen05b"]["file"])

    def test_division_of_labor_content(self):
        d = lmm.division_of_labor()
        self.assertIn("loveai-qwen15b", d)
        self.assertIn("loveai-qwen05b", d)
        self.assertIn("smollm2-135m", d)
        self.assertIn("cloud", d)
        self.assertIn("保留", d["smollm2-135m"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
