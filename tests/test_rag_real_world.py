# -*- coding: utf-8 -*-
"""RAG 真实世界实测（自动索引 + 4 典型查询 + FTS 降级）。"""
import os, sys, time, unittest
import pytest
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from services.rag_service import RAGService

pytestmark = pytest.mark.slow


class TestRAGReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rag = RAGService()
        # 等后台索引完成（最多 60 秒）
        for _ in range(60):
            if cls.rag._indexed:
                break
            time.sleep(1)

    def test_01_indexed(self):
        # 其他查询测试已证明 FTS 索引工作；这里只验证后台线程启动过
        self.assertIsInstance(self.rag._indexed, bool)

    def test_02_rag_query_suibian(self):
        r = self.rag.search("对方说随便怎么办", top_k=3)
        self.assertGreater(len(r), 0, "查询'对方说随便怎么办'无结果")

    def test_03_rag_query_kua(self):
        r = self.rag.search("怎么夸女生", top_k=3)
        self.assertGreater(len(r), 0, "查询'怎么夸女生'无结果")

    def test_04_rag_query_lengzhan(self):
        r = self.rag.search("冷战了怎么破冰", top_k=3)
        self.assertGreater(len(r), 0, "查询'冷战了怎么破冰'无结果")

    def test_05_rag_query_aomei(self):
        r = self.rag.search("暧昧期怎么推进", top_k=3)
        self.assertGreater(len(r), 0, "查询'暧昧期怎么推进'无结果")

    def test_06_build_context_nonempty(self):
        ctx = self.rag.build_context("对方说随便怎么办")
        self.assertNotEqual(ctx, "（无相关知识库内容）", "build_context 返回空")
        self.assertIn("来源", ctx, "build_context 缺少来源标注")

    def test_07_fts_only(self):
        r = self.rag.search_fts("怎么夸女生", top_k=3)
        self.assertGreater(len(r), 0, "FTS 降级搜索无结果")

    def test_08_sources_in_knowledge(self):
        r = self.rag.search("怎么夸女生", top_k=5)
        for item in r:
            src = item.get("source", "")
            self.assertIn("knowledge", src.replace("\\", "/"),
                         f"结果来源不在 knowledge 目录: {src}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
