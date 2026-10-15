# -*- coding: utf-8 -*-
"""
LoveAI v2.0 - FTS 索引幂等性测试（离线确定性）

覆盖 P0 根因修复（services/rag_service.py::_ensure_fts_indexed）：
  对同一已建库连跑两次，chunks 计数不增长、召回不重复。
  模拟"安装释放已建好的 unified_search.db + 首次运行全新 rag_index_state.json"。
"""
import os
import sys
import sqlite3

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from services.rag_service import RAGService  # noqa: E402


def _make_svc(tmp_path, body_text):
    svc = RAGService.__new__(RAGService)
    svc.project_root = str(tmp_path)
    os.makedirs(os.path.join(str(tmp_path), "knowledge"), exist_ok=True)
    with open(os.path.join(str(tmp_path), "knowledge", "intro.md"), "w",
              encoding="utf-8") as f:
        f.write(body_text)
    svc.fts_db_path = str(tmp_path / "unified_search.db")
    svc._indexed_files = {}
    return svc


def _count_chunks(db_path):
    return sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM chunks").fetchone()[0]


def test_fts_index_idempotent_no_duplicate(tmp_path):
    body = "## Intro\n亲密关系里的沟通要点第一条是倾听与回应。\n"
    svc = _make_svc(tmp_path, body)

    # 第一次：空库 + 空状态 → 建库并写入段落
    n1 = svc._ensure_fts_indexed()
    c1 = _count_chunks(svc.fts_db_path)
    assert n1 >= 1
    assert c1 == n1

    # 模拟新装机：已建库存在，但 rag_index_state.json 全新为空
    svc._indexed_files = {}
    n2 = svc._ensure_fts_indexed()
    c2 = _count_chunks(svc.fts_db_path)
    assert n2 == 0     # 不新增段落
    assert c2 == c1    # chunks 数不增长、不重复

    # 召回不得出现重复行
    rows = svc.search_fts("沟通", top_k=10)
    contents = [r["content"] for r in rows]
    assert len(contents) == len(set(contents))


def test_fts_index_third_run_stable(tmp_path):
    body = "## Intro\n耐心\n## Second\n一致性与边界感。\n"
    svc = _make_svc(tmp_path, body)
    svc._ensure_fts_indexed()
    c1 = _count_chunks(svc.fts_db_path)
    # 状态文件已写入 → 第二次命中 mtime 快路径，直接跳过
    n2 = svc._ensure_fts_indexed()
    c2 = _count_chunks(svc.fts_db_path)
    assert n2 == 0
    assert c2 == c1
