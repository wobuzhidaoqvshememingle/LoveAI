"""
LoveAI v2.0 - RAG 检索服务
FTS5 全文检索 + Chroma 向量语义检索双引擎融合，为聊天分析提供恋爱知识库上下文。
服务层，不依赖 PySide6 / QObject。
"""
import hashlib
import logging
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Chroma 整体延迟到首次向量检索才导入，避免空闲时占用大量内存。
# 通过 _import_chromadb() 获取 (chromadb, available)。
_chromadb_module = None
_chromadb_checked = False

def _import_chromadb():
    """惰性导入 chromadb，返回 (module, available)；结果缓存。"""
    global _chromadb_module, _chromadb_checked
    if _chromadb_checked:
        return _chromadb_module, _chromadb_module is not None
    _chromadb_checked = True
    try:
        import chromadb as _cdb  # type: ignore
        _chromadb_module = _cdb
    except Exception as _e:  # pragma: no cover - 环境降级路径
        _chromadb_module = None
        logger.warning("chromadb 未安装，向量检索将不可用: %s", _e)
    return _chromadb_module, _chromadb_module is not None


class RAGService:
    """RAG 双引擎检索服务。

    - FTS5：基于 SQLite 虚表的关键词全文检索，精准召回。
    - Chroma：本地向量库（默认 all-MiniLM embedding），语义召回。
    - 融合：FTS 权重 0.6，Chroma 权重 0.4，按内容前缀去重合并。
    """

    #: 融合权重：全文 / 向量
    FTS_WEIGHT = 0.6
    CHROMA_WEIGHT = 0.4

    #: Chroma collection 名称与度量
    CHROMA_COLLECTION = "loveai_knowledge"

    #: 学科分类映射：路径关键词 → 学科名
    DISCIPLINE_MAP = {
        "disciplines/psychology": "psychology",
        "disciplines/law": "law",
        "disciplines/economics": "economics",
        "disciplines/sociology": "sociology",
        "disciplines/communication": "communication",
        "disciplines/biology": "biology",
        "disciplines/game_theory": "game_theory",
        "theory/psychology": "psychology",
        "亲密关系咨询": "psychology",
        "两性科普写作": "biology",
    }

    @classmethod
    def _extract_discipline(cls, file_path: str) -> str:
        """根据文件路径提取学科分类。

        Args:
            file_path: 知识库文件的绝对或相对路径。

        Returns:
            str: 学科名（psychology/law/economics/sociology/communication/biology/game_theory），
            未匹配时返回 "general"。
        """
        norm = file_path.replace("\\", "/").lower()
        for keyword, disc in cls.DISCIPLINE_MAP.items():
            if keyword.lower() in norm:
                return disc
        return "general"

    def __init__(self, config: Any = None):
        """
        初始化 RAG 服务。

        Args:
            config: ConfigService 实例（预留，用于读取可选配置；路径以项目根为准）。
        """
        self.config = config

        # 项目根目录：services/rag_service.py → 上一级
        # （冻结态 = _internal，用于读取 knowledge/ 等只读包内资源）
        self.project_root: str = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )

        # === 随包运行时接入（新增）：预置知识库落点冻结感知 ===
        # 实际发货走「精简核心（LOVEAI_BUNDLE_* 全关）+ Inno 勾选 knowledge 释放到
        # {安装目录}\data」；此时冻结态 project_root=_internal 去 _internal\data 找库会落空。
        # 解析顺序：安装目录\data\<name>（Inno 释放、可写可扩展）存在即用；
        # 否则 app_root()\data\<name>（LOVEAI_BUNDLE_KNOWLEDGE=1 打入 _internal 时）；
        # 都不存在则落安装目录\data（供首次自动索引创建）。
        # 开发态 install_dir()==app_root()==仓库根，行为与旧逻辑完全一致。
        self.fts_db_path: str = self._resolve_knowledge_path("unified_search.db")
        self.chroma_path: str = self._resolve_knowledge_path("chroma_db")

        # Chroma 懒加载
        self.chroma_client: Optional[Any] = None
        self.chroma_collection: Optional[Any] = None
        self.embedding_function: Optional[Any] = None
        self.chroma_available: bool = False
        # 嵌入模型就绪探测（会话内只做一次），避免隐式下载阻塞分析流水线
        self._emb_probed: bool = False
        self._emb_ok: bool = False

        # 自动索引状态
        self._indexed_files: dict = {}
        self._indexing: bool = False
        self._indexed: bool = False
        self._total_chunks: int = 0
        # 可写索引状态落可写数据目录（冻结态=安装目录\data，不写只读 _internal）
        try:
            from services import runtime_paths as _rp
            self._index_state_file: str = os.path.join(
                _rp.writable_data_dir(), "rag_index_state.json"
            )
        except Exception:
            self._index_state_file = os.path.join(
                self.project_root, "data", "rag_index_state.json"
            )
        self._load_index_state()
        # 后台自动索引（不阻塞启动）
        threading.Thread(target=self._auto_index, daemon=True).start()

    def _resolve_knowledge_path(self, name: str) -> str:
        """冻结感知的预置知识库落点解析（unified_search.db / chroma_db）。

        顺序：
          1) 安装目录\\data\\<name>（Inno 释放、可写可扩展）——存在即用；
          2) 包内 app_root()\\data\\<name>（LOVEAI_BUNDLE_KNOWLEDGE=1 打入 _internal 时）；
          3) 都不存在则落安装目录\\data\\<name>（供首次自动索引创建，可写）。

        开发态 install_dir()==app_root()==仓库根，三者等价旧 project_root/data/<name>。
        任何异常回退到旧 __file__ 推导，保证不崩。
        """
        try:
            from services import runtime_paths as _rp
            install_cand = os.path.join(_rp.install_dir(), "data", name)
            if os.path.exists(install_cand):
                return install_cand
            bundled_cand = os.path.join(_rp.app_root(), "data", name)
            if os.path.exists(bundled_cand):
                return bundled_cand
            return install_cand  # 兜底：落可写安装目录，供首次索引创建
        except Exception:
            return os.path.join(self.project_root, "data", name)

    def _load_index_state(self) -> None:
        try:
            import json
            if os.path.exists(self._index_state_file):
                with open(self._index_state_file, "r", encoding="utf-8") as f:
                    self._indexed_files = json.load(f)
        except Exception:
            self._indexed_files = {}

    def _save_index_state(self) -> None:
        try:
            import json
            os.makedirs(os.path.dirname(self._index_state_file), exist_ok=True)
            with open(self._index_state_file, "w", encoding="utf-8") as f:
                json.dump(self._indexed_files, f, ensure_ascii=False)
        except Exception as e:
            logger.debug("保存索引状态失败: %s", e)

    def _auto_index(self) -> None:
        """后台自动索引：FTS 必做（兜底），Chroma 可用时再做。"""
        try:
            self._indexing = True
            # FTS 全文索引（始终执行，作为兜底）
            fts_count = self._ensure_fts_indexed()
            # Chroma 向量索引（可用时执行）
            chroma_count = 0
            try:
                chroma_count = self.index_knowledge()
            except Exception as e:
                logger.warning("Chroma 向量索引失败，降级到 FTS: %s", e)
            self._total_chunks = fts_count + chroma_count
            self._indexed = True
            self._save_index_state()
            logger.info(
                f"知识库索引完成，共 {self._total_chunks} 个段落 "
                f"(FTS={fts_count}, Chroma={chroma_count})"
            )
        except Exception as e:
            logger.warning(f"自动索引异常: {e}")
            self._indexed = True
        finally:
            self._indexing = False

    def _ensure_fts_indexed(self) -> int:
        """确保 FTS 表存在并批量导入 knowledge/ 目录所有 .md/.txt 文件。"""
        knowledge_dir = os.path.join(self.project_root, "knowledge")
        if not os.path.isdir(knowledge_dir):
            return 0
        conn = None
        try:
            conn = sqlite3.connect(self.fts_db_path)
            cur = conn.cursor()
            cur.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "title TEXT, content TEXT, source TEXT)"
            )
            # FTS5 外部内容表
            cur.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5("
                "title, content, content='chunks', content_rowid='id')"
            )

            # === 幂等根因修复（P0）===
            # 安装释放的是已建好的 unified_search.db，但 rag_index_state.json 首次运行
            # 全新生成 → 旧逻辑会对已存在 chunks 重复 INSERT、重复召回。
            # 先载入库中已存在的全部正文集合，只插入缺失段落；按正文去重，与 source
            # 路径形态无关（建库机/运行机路径不同也能正确判重）。
            existing_contents: set = set()
            try:
                cur.execute("SELECT content FROM chunks")
                existing_contents = {
                    row[0] for row in cur.fetchall() if row and row[0]
                }
            except Exception:
                existing_contents = set()

            # 增量：只索引修改时间变了的文件；文件内按正文去重
            indexed = 0
            for root, _dirs, files in os.walk(knowledge_dir):
                for fname in sorted(files):
                    if not fname.lower().endswith((".md", ".txt")):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        mtime = os.path.getmtime(fpath)
                    except Exception:
                        continue
                    prev = self._indexed_files.get(fpath)
                    if prev and prev.get("mtime") == mtime:
                        continue  # 未修改
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            text = f.read()
                    except Exception:
                        continue
                    sections = self._split_markdown_sections(text)
                    file_new = 0
                    for sec in sections:
                        body = (sec.get("body") or "").strip()
                        if not body:
                            continue
                        if body in existing_contents:
                            continue  # 已在库中（预建库/本批已插），跳过，不重复插入
                        cur.execute(
                            "INSERT INTO chunks(title, content, source) VALUES (?, ?, ?)",
                            (fname, body, fpath),
                        )
                        rowid = cur.lastrowid
                        cur.execute(
                            "INSERT INTO fts(rowid, title, content) VALUES (?, ?, ?)",
                            (rowid, fname, body),
                        )
                        existing_contents.add(body)
                        indexed += 1
                        file_new += 1
                    # 无论是否新增段落，都据当前文件校正状态（对接已建库时状态为空）
                    self._indexed_files[fpath] = {
                        "mtime": mtime,
                        "sections": len(sections),
                        "new": file_new,
                    }
            conn.commit()
            return indexed
        except Exception as e:
            logger.warning(f"FTS 索引失败: {e}")
            return 0
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------ #
    # 通用硬超时：任何子步骤都不得阻塞超过预算
    # ------------------------------------------------------------------ #
    @staticmethod
    def _run_with_timeout(fn, seconds: float):
        """在守护子线程中执行 fn，最多等待 seconds 秒。

        Returns:
            (done, value)：done=True 时 value 为 fn 返回值；
            done=False 表示超时或异常（value 为异常对象或 None）。
        """
        box: dict = {}

        def _work():
            try:
                box["v"] = fn()
            except Exception as e:  # noqa: BLE001 - 转为结果返回
                box["e"] = e

        t = threading.Thread(target=_work, daemon=True)
        t.start()
        t.join(seconds)
        if "v" in box:
            return True, box["v"]
        return False, box.get("e")

    @staticmethod
    def _embedding_files_present() -> bool:
        """仅检查嵌入模型文件是否存在（不导入 chromadb，零成本）。

        用于在融合检索前快速决定是否值得走 Chroma：
        已解压（model.onnx + tokenizer.json）或存在 onnx.tar.gz 即视为可能可用。
        """
        try:
            base = (
                Path.home() / ".cache" / "chroma" / "onnx_models"
                / "all-MiniLM-L6-v2"
            )
            extracted = base / "onnx"
            if (extracted / "model.onnx").exists() and (
                extracted / "tokenizer.json"
            ).exists():
                return True
            return (base / "onnx.tar.gz").exists()
        except Exception:
            return False

    def _embedding_model_ready(self) -> bool:
        """检测默认 ONNX 嵌入模型是否在本地就绪，绝不隐式触发长时间下载。

        chromadb 默认嵌入（all-MiniLM-L6-v2）缓存于
        ``~/.cache/chroma/onnx_models/all-MiniLM-L6-v2/``。
        - 已解压（model.onnx + tokenizer.json）：限时实例化确认；
        - 仅有 onnx.tar.gz：限时实例化（内部校验并解压）；
        - 两者皆无：直接判定不可用，不发起下载。
        """
        if self._emb_probed:
            return self._emb_ok
        self._emb_probed = True
        cdb, available = _import_chromadb()
        if not available:
            self._emb_ok = False
            return False
        try:
            base = (
                Path.home() / ".cache" / "chroma" / "onnx_models"
                / "all-MiniLM-L6-v2"
            )
            extracted = base / "onnx"
            ready_files = (extracted / "model.onnx").exists() and (
                extracted / "tokenizer.json"
            ).exists()
            archive = base / "onnx.tar.gz"
            if not ready_files and not archive.exists():
                logger.info("Chroma 嵌入模型缺失且无压缩包，向量检索本次不可用（不下载）")
                self._emb_ok = False
                return False

            def _probe():
                ef = cdb.utils.embedding_functions.ONNXMiniLM_L6_V2()
                return ef(["测试"])

            # 已解压给 6s，仅压缩包（需解压）给 12s
            done, err = self._run_with_timeout(_probe, 6.0 if ready_files else 12.0)
            if not done and err is not None:
                logger.warning("Chroma 嵌入模型探测异常: %s", err)
            self._emb_ok = bool(done)
            return self._emb_ok
        except Exception as e:  # noqa: BLE001
            logger.warning("Chroma 嵌入模型就绪检测失败: %s", e)
            self._emb_ok = False
            return False

    # ------------------------------------------------------------------ #
    # Chroma 懒加载
    # ------------------------------------------------------------------ #
    def _ensure_chroma(self) -> bool:
        """懒加载 Chroma 客户端与 collection。

        Returns:
            True 表示 Chroma 可用；False 表示不可用（已置标志位）。
        """
        if self.chroma_client is not None and self.chroma_collection is not None:
            return True

        cdb, available = _import_chromadb()
        if not available:
            self.chroma_available = False
            return False

        try:
            def _build():
                # 关闭遥测：打包态可能缺少 posthog 子模块，关闭后不触发该导入路径，
                # 保证客户端创建快速且确定
                try:
                    chroma_settings = cdb.Settings(anonymized_telemetry=False)
                except Exception:
                    chroma_settings = None
                client = cdb.PersistentClient(
                    path=self.chroma_path, settings=chroma_settings
                )
                collection = client.get_or_create_collection(
                    name=self.CHROMA_COLLECTION,
                    metadata={"hnsw:space": "cosine"},
                )
                return client, collection

            done, value = self._run_with_timeout(_build, 8.0)
            if not done:
                logger.warning("Chroma 初始化超时，向量检索不可用: %s", value)
                self.chroma_client = None
                self.chroma_collection = None
                self.chroma_available = False
                return False
            self.chroma_client, self.chroma_collection = value
            self.chroma_available = True
            logger.info(
                f"Chroma 加载完成: {self.chroma_path}, "
                f"collection={self.CHROMA_COLLECTION}"
            )
            return True
        except Exception as e:
            logger.warning(f"Chroma 初始化失败，向量检索不可用: {e}")
            self.chroma_client = None
            self.chroma_collection = None
            self.chroma_available = False
            return False

    # ------------------------------------------------------------------ #
    # FTS5 查询预处理
    # ------------------------------------------------------------------ #
    @staticmethod
    def _preprocess_fts_query(query: str) -> str:
        """将原始 query 转换为安全且对中文友好的 FTS5 MATCH 表达式。

        实际库用默认 unicode61 分词器，CJK 按**单字**切分。若把整句中文包成
        一个引号短语，会要求整串精确相邻、几乎无法命中。因此：
        - 每个 CJK 段：整段短语（精确命中、排名最高）+ 滑动二元组（语义主力），
          单字段直接用单字；
        - 拉丁/数字：按词；
        - 全部以 OR 连接（FTS 按相关度排序），去重并限量，特殊字符安全。
        """
        if not query:
            return ""

        terms: list = []
        for run in re.findall(r"[一-鿿㐀-䶿]+", query):
            if len(run) == 1:
                terms.append(run)
            else:
                terms.append(f'"{run}"')  # 整段短语
                for i in range(len(run) - 1):  # 滑动二元组
                    terms.append(f'"{run[i:i+2]}"')
        for word in re.findall(r"[A-Za-z0-9]+", query):
            terms.append(word)

        # 去重保序 + 限量（整段短语天然排在前）
        seen: set = set()
        final: list = []
        for t in terms:
            if t not in seen:
                seen.add(t)
                final.append(t)
        return " OR ".join(final[:24])

    # ------------------------------------------------------------------ #
    # FTS5 全文检索
    # ------------------------------------------------------------------ #
    def search_fts(self, query: str, top_k: int = 5) -> list:
        """FTS5 全文检索。

        实际库结构：`fts` 为 fts5 外部内容虚表（仅索引 content/title），
        元数据（source）存于 `chunks`，通过 rowid=id 关联。

        Args:
            query: 查询语句。
            top_k: 返回条数。

        Returns:
            list[dict]，每项含 title/content/source/score；异常或无表时返回 []。
        """
        match_expr = self._preprocess_fts_query(query)
        if not match_expr:
            return []

        # 外部内容 FTS5：fts 虚表 JOIN chunks 取 source
        sql = (
            "SELECT chunks.title, chunks.content, chunks.source, fts.rank "
            "FROM fts JOIN chunks ON chunks.id = fts.rowid "
            "WHERE fts MATCH ? ORDER BY rank LIMIT ?"
        )

        results: list = []
        conn = None
        try:
            conn = sqlite3.connect(self.fts_db_path)
            cur = conn.cursor()
            cur.execute(sql, (match_expr, top_k))
            rows = cur.fetchall()
            for title, content, source, rank in rows:
                results.append(
                    {
                        "title": title or "",
                        "content": content or "",
                        "source": source or "",
                        # FTS rank 为负数（越小越相关），转正数便于融合排序
                        "score": float(rank) if rank is not None else 0.0,
                    }
                )
        except sqlite3.OperationalError as e:
            # 表不存在或 MATCH 语法问题：静默降级为空结果
            logger.warning(f"FTS5 查询失败（可能表不存在）: {e}")
        except Exception as e:
            logger.warning(f"FTS5 检索异常: {e}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

        return results

    # ------------------------------------------------------------------ #
    # Chroma 向量语义检索
    # ------------------------------------------------------------------ #
    def search_chroma(self, query: str, top_k: int = 5) -> list:
        """Chroma 向量语义检索。

        Args:
            query: 查询语句。
            top_k: 返回条数。

        Returns:
            list[dict]，每项含 title/content/source/score(=1-distance)；
            Chroma 不可用或 collection 为空时返回 []。
        """
        if not self._ensure_chroma() or self.chroma_collection is None:
            return []

        # 嵌入模型未就绪（缺失/解压慢）则不发起 query，避免隐式下载阻塞
        if not self._embedding_model_ready():
            return []

        if not query:
            return []

        done, value = self._run_with_timeout(
            lambda: self.chroma_collection.query(
                query_texts=[query], n_results=top_k
            ),
            5.0,
        )
        if not done:
            logger.warning("Chroma 检索超时（%s），本次跳过向量结果", value)
            return []
        results = value

        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        distances = results.get("distances") or []

        if not documents or not documents[0]:
            # collection 为空或无召回
            return []

        docs = documents[0]
        metas = metadatas[0] if metadatas else [{}] * len(docs)
        dists = distances[0] if distances else [0.0] * len(docs)

        out: list = []
        for doc, meta, dist in zip(docs, metas, dists):
            meta = meta or {}
            dist = float(dist) if dist is not None else 0.0
            out.append(
                {
                    "title": meta.get("title", ""),
                    "content": doc or "",
                    "source": meta.get("source", ""),
                    # cosine 距离越小越相似，转为 [0,1] 相似度
                    "score": max(0.0, 1.0 - dist),
                }
            )
        return out

    # ------------------------------------------------------------------ #
    # 双引擎融合检索（主接口）
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        top_k: int = 5,
        use_chroma: bool = True,
        disciplines: Optional[list] = None,
    ) -> list:
        """双引擎融合检索主接口。

        流程：
        1. FTS5 关键词召回；
        2. 可选 Chroma 语义召回；
        3. 以 content 前 50 字为 key 去重合并；
        4. 融合排序：FTS 权重 0.6，Chroma 权重 0.4；
        5. 截取 top_k，标注 engine 来源。

        Args:
            query: 查询语句。
            top_k: 最终返回条数。
            use_chroma: 是否启用向量引擎。
            disciplines: 可选学科过滤列表，如 ["psychology","law"]；
                        None 表示不限制学科。

        Returns:
            list[dict]：[{"title","content","source","score","engine","discipline"}]，
            engine ∈ {"fts", "chroma", "both"}。
        """
        fts_results = self.search_fts(query, top_k * 2)
        # 先用零成本文件探测决定是否走 Chroma：无嵌入模型文件则根本不导入 chromadb，
        # 保持首次分析快速、不增加内存
        chroma_results = (
            self.search_chroma(query, top_k * 2)
            if use_chroma and self._embedding_files_present()
            else []
        )

        # 学科过滤：FTS 按 source 路径判断，Chroma 按 metadata 判断
        if disciplines:
            disc_set = set(d.lower() for d in disciplines)
            fts_results = [
                r for r in fts_results
                if self._extract_discipline(r.get("source", "")) in disc_set
            ]
            chroma_results = [
                r for r in chroma_results
                if r.get("discipline", "general") in disc_set
            ]

        # ---- 归一化各引擎内部分数到 [0,1] ----
        fts_norm = self._normalize_fts(fts_results)
        chroma_norm = self._normalize_chroma(chroma_results)

        # ---- 以 content 前 50 字为 key 合并 ----
        merged: dict[str, dict] = {}

        for r in fts_norm:
            key = r["content"][:50]
            if not key:
                continue
            item = {
                "title": r["title"],
                "content": r["content"],
                "source": r["source"],
                "fused": self.FTS_WEIGHT * r["_norm"],
                "engine": "fts",
            }
            merged[key] = item

        for r in chroma_norm:
            key = r["content"][:50]
            if not key:
                continue
            weighted = self.CHROMA_WEIGHT * r["_norm"]
            if key in merged:
                # 双引擎都命中：加权融合
                merged[key]["fused"] += weighted
                merged[key]["engine"] = "both"
                # 保留更完整的 title/source（若现有为空则补）
                if not merged[key]["title"] and r["title"]:
                    merged[key]["title"] = r["title"]
                if not merged[key]["source"] and r["source"]:
                    merged[key]["source"] = r["source"]
            else:
                merged[key] = {
                    "title": r["title"],
                    "content": r["content"],
                    "source": r["source"],
                    "fused": weighted,
                    "engine": "chroma",
                }

        # ---- 按融合分排序，截取 top_k ----
        ranked = sorted(
            merged.values(), key=lambda x: x["fused"], reverse=True
        )[:top_k]

        out: list = []
        for item in ranked:
            out.append(
                {
                    "title": item["title"],
                    "content": item["content"],
                    "source": item["source"],
                    "score": round(item["fused"], 6),
                    "engine": item["engine"],
                    "discipline": self._extract_discipline(item.get("source", "")),
                }
            )
        return out

    @staticmethod
    def _normalize_fts(results: list) -> list:
        """将 FTS rank（负数，越小越相关）归一化为 [0,1]。

        策略：raw = -rank（越大越相关），除以集合内最大值。
        """
        if not results:
            return []
        raws = [abs(float(r["score"])) for r in results]
        mx = max(raws) if raws else 0.0
        out = []
        for r, raw in zip(results, raws):
            norm = (raw / mx) if mx > 0 else 0.0
            out.append({**r, "_norm": norm})
        return out

    @staticmethod
    def _normalize_chroma(results: list) -> list:
        """Chroma score 已是 [0,1] 相似度，直接作为归一化值。"""
        if not results:
            return []
        out = []
        for r in results:
            norm = max(0.0, min(1.0, float(r["score"])))
            out.append({**r, "_norm": norm})
        return out

    # ------------------------------------------------------------------ #
    # Prompt 上下文构建
    # ------------------------------------------------------------------ #
    def build_context(
        self, query: str, max_chars: int = 2000, disciplines: Optional[list] = None
    ) -> str:
        """为大模型构建知识库 Prompt 上下文。

        Args:
            query: 用户查询。
            max_chars: 上下文最大字符数。
            disciplines: 可选学科过滤列表，如 ["psychology","law"]；
                        None 表示不限制学科。

        Returns:
            格式化后的上下文字符串；无结果时返回提示语。
        """
        results = self.search(query, top_k=5, disciplines=disciplines)
        if not results:
            return "（无相关知识库内容）"

        chunks: list[str] = []
        for r in results:
            title = r.get("title", "") or "未命名"
            content = (r.get("content", "") or "")[:300]
            source = r.get("source", "") or "未知来源"
            disc = r.get("discipline", "general")
            chunks.append(f"【{title}·{disc}】{content}\n来源：{source}\n")

        text = "\n".join(chunks)
        if len(text) > max_chars:
            text = text[:max_chars]
        return text

    # ------------------------------------------------------------------ #
    # 知识库索引
    # ------------------------------------------------------------------ #
    def index_knowledge(self, knowledge_dir: Optional[str] = None) -> int:
        """将 knowledge/ 目录下的 Markdown 文件增量索引到 Chroma。

        按 `##` / `###` 标题切分段落，每段作为一个 document；
        使用 upsert 写入（id = 文件路径 + 段索引 的 md5），可重复执行。

        Args:
            knowledge_dir: 知识库目录；默认为 项目根/knowledge/。

        Returns:
            索引的段落数；Chroma 不可用时返回 0。
        """
        if knowledge_dir is None:
            knowledge_dir = os.path.join(self.project_root, "knowledge")

        if not self._ensure_chroma() or self.chroma_collection is None:
            logger.warning("Chroma 不可用，跳过知识库索引")
            return 0

        if not os.path.isdir(knowledge_dir):
            logger.warning(f"知识库目录不存在: {knowledge_dir}")
            return 0

        indexed = 0
        for root, _dirs, files in os.walk(knowledge_dir):
            for fname in sorted(files):
                if not fname.lower().endswith(".md"):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        text = f.read()
                except Exception as e:
                    logger.warning(f"读取知识库文件失败 {fpath}: {e}")
                    continue

                sections = self._split_markdown_sections(text)
                for idx, sec in enumerate(sections):
                    heading = sec.get("heading", "")
                    body = sec.get("body", "").strip()
                    if not body:
                        continue

                    doc_id = hashlib.md5(
                        f"{fpath}::{idx}".encode("utf-8")
                    ).hexdigest()
                    discipline = self._extract_discipline(fpath)
                    metadata = {
                        "title": fname,
                        "source": fpath,
                        "section": heading,
                        "discipline": discipline,
                    }
                    try:
                        self.chroma_collection.upsert(
                            ids=[doc_id],
                            documents=[body],
                            metadatas=[metadata],
                        )
                        indexed += 1
                    except Exception as e:
                        logger.warning(
                            f"Chroma upsert 失败 ({fpath}#{idx}): {e}"
                        )

        logger.info(f"知识库索引完成，共写入 {indexed} 个段落")
        return indexed

    @staticmethod
    def _split_markdown_sections(text: str) -> list:
        """按 Markdown 二级/三级标题切分文本。

        首个标题之前的内容归入"前言"段。

        Args:
            text: 原始 Markdown 文本。

        Returns:
            list[dict]：[{"heading": str, "body": str}, ...]
        """
        lines = text.splitlines()
        sections: list = []
        cur_heading = ""
        buf: list[str] = []

        def flush():
            body = "\n".join(buf).strip()
            if body:
                sections.append({"heading": cur_heading, "body": body})

        for line in lines:
            if re.match(r"^#{2,3}\s+", line):
                # 遇到新标题：先收尾上一段
                flush()
                cur_heading = line.lstrip("#").strip()
                buf = [line]
            else:
                buf.append(line)
        flush()

        # 全文无标题时，整体作为一段
        if not sections and text.strip():
            sections.append({"heading": "", "body": text.strip()})
        return sections

    # ------------------------------------------------------------------ #
    # 统计信息
    # ------------------------------------------------------------------ #
    def get_stats(self) -> dict:
        """返回 RAG 服务统计信息。

        Returns:
            dict：fts_db_exists / fts_db_size_mb / chroma_available /
                  chroma_doc_count / knowledge_file_count。
        """
        fts_db_exists = os.path.exists(self.fts_db_path)
        fts_db_size_mb = 0.0
        if fts_db_exists:
            try:
                fts_db_size_mb = round(
                    os.path.getsize(self.fts_db_path) / (1024 * 1024), 2
                )
            except Exception:
                pass

        chroma_available = False
        chroma_doc_count = 0
        if self._ensure_chroma() and self.chroma_collection is not None:
            chroma_available = True
            try:
                chroma_doc_count = self.chroma_collection.count()
            except Exception as e:
                logger.warning(f"获取 Chroma 文档数失败: {e}")
                chroma_available = False

        knowledge_dir = os.path.join(self.project_root, "knowledge")
        knowledge_file_count = 0
        if os.path.isdir(knowledge_dir):
            for root, _dirs, files in os.walk(knowledge_dir):
                knowledge_file_count += sum(
                    1 for f in files if f.lower().endswith(".md")
                )

        return {
            "fts_db_exists": fts_db_exists,
            "fts_db_size_mb": fts_db_size_mb,
            "chroma_available": chroma_available,
            "chroma_doc_count": chroma_doc_count,
            "knowledge_file_count": knowledge_file_count,
        }
