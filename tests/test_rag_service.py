# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - RAGService 单元测试

测试 RAG 双引擎检索服务：
  - 初始化不崩溃
  - get_stats 返回字段
  - search_fts / search_chroma / search 返回列表
  - build_context 返回字符串
  - 结果字段格式（title/content/source/score）

注意：Chroma 向量检索需要下载 ONNX embedding 模型（约 80MB），
测试中统一把 _ensure_chroma patch 为返回 False，避免触发网络下载，
保证测试不依赖网络且快速运行。
"""
import os
import sys
import unittest
from unittest.mock import patch

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.rag_service import RAGService  # noqa: E402


class TestRAGService(unittest.TestCase):
    """RAGService 测试类。"""

    def setUp(self) -> None:
        """初始化 RAG 服务，并禁用 Chroma 懒加载（避免下载模型）。"""
        self.rag = RAGService(config=None)
        # patch 掉 _ensure_chroma，使 chroma_available=False，
        # 避免真实环境下触发 ONNX 模型下载（约 80MB）
        self._chroma_patcher = patch.object(
            RAGService, "_ensure_chroma", return_value=False
        )
        self._chroma_patcher.start()

    def tearDown(self) -> None:
        """停止 patch。"""
        self._chroma_patcher.stop()

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def test_init(self) -> None:
        """RAGService(config) 应正常初始化不崩溃。"""
        svc = RAGService(config=None)
        self.assertIsNotNone(svc)
        self.assertIsNotNone(svc.fts_db_path)
        self.assertIsNotNone(svc.chroma_path)

    def test_get_stats(self) -> None:
        """get_stats 应返回 fts_db_exists / chroma_available 等字段。"""
        stats = self.rag.get_stats()
        expected_keys = {
            "fts_db_exists", "fts_db_size_mb",
            "chroma_available", "chroma_doc_count",
            "knowledge_file_count",
        }
        self.assertEqual(set(stats.keys()), expected_keys)
        # chroma 已被 patch 为不可用
        self.assertFalse(stats["chroma_available"])

    # ------------------------------------------------------------------ #
    # 各搜索接口返回类型
    # ------------------------------------------------------------------ #
    def test_search_fts_returns_list(self) -> None:
        """search_fts 应返回列表（可能为空，但不报错）。"""
        result = self.rag.search_fts("约会怎么开口")
        self.assertIsInstance(result, list)

    def test_search_chroma_returns_list(self) -> None:
        """search_chroma 应返回列表（chroma 被禁用，应为空列表）。"""
        result = self.rag.search_chroma("约会怎么开口")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    def test_search_returns_list(self) -> None:
        """search 融合接口应返回列表。"""
        result = self.rag.search("约会怎么开口")
        self.assertIsInstance(result, list)

    def test_build_context_returns_string(self) -> None:
        """build_context 应返回字符串。"""
        ctx = self.rag.build_context("如何挽回对方")
        self.assertIsInstance(ctx, str)
        self.assertGreater(len(ctx), 0)

    # ------------------------------------------------------------------ #
    # FTS 库存在性
    # ------------------------------------------------------------------ #
    def test_fts_db_exists(self) -> None:
        """data/unified_search.db 存在时 get_stats 中 fts_db_exists 应为 True。

        这里做自洽断言：stats 中的 fts_db_exists 应与文件系统实际状态一致。
        """
        stats = self.rag.get_stats()
        actual = os.path.exists(self.rag.fts_db_path)
        self.assertEqual(stats["fts_db_exists"], actual)

    # ------------------------------------------------------------------ #
    # 结果格式
    # ------------------------------------------------------------------ #
    def test_search_format(self) -> None:
        """每条搜索结果应包含 title / content / source / score 字段。

        通过 mock search_fts / search_chroma 返回固定样本，
        验证 search() 融合后的输出字段结构。
        """
        fake_fts = [
            {"title": "约会技巧", "content": "第一次约会建议去咖啡馆",
             "source": "knowledge/date.md", "score": -1.5},
        ]
        fake_chroma = [
            {"title": "挽回指南", "content": "挽回对方需要真诚",
             "source": "knowledge/recover.md", "score": 0.8},
        ]
        with patch.object(self.rag, "search_fts", return_value=fake_fts), \
             patch.object(self.rag, "search_chroma", return_value=fake_chroma):
            results = self.rag.search("约会", top_k=5)

        self.assertIsInstance(results, list)
        self.assertGreaterEqual(len(results), 1)
        for item in results:
            self.assertIn("title", item)
            self.assertIn("content", item)
            self.assertIn("source", item)
            self.assertIn("score", item)


if __name__ == "__main__":
    unittest.main(verbosity=2)
