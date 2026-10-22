"""
LoveAI v2.0 - RAG 检索服务
FTS5 全文检索 + Chroma 向量语义检索双引擎融合，为聊天分析提供恋爱知识库上下文。
服务层，不依赖 PySide6 / QObject。
"""
import hashlib
import io
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


# ---------------------------------------------------------------------- #
# 知识库索引：准入与分块（2026-10-17「检索库重建」）
#
# 上一轮「资产融入索引风险加固」给两条扫描路径加了两道闸：
#   1) MAX_INDEX_FILE_BYTES = 8MB  → 单文件超限即**整文件跳过**；
#   2) EXCLUDED_KNOWLEDGE_DIRS = ("泡妞分类语料",) → **整目录跳过**。
# 副作用是：knowledge/泡妞分类语料 的 383 个 .txt（150.37 MB）与
# knowledge/泡妞全本蒸馏/references/m3-model.md（120.06 MB）等大批有效语料
# 被**永久踢出检索**——库体积好看了，产品却再也检索不到这些内容。
#
# 本轮改为**从根因修分块**，而不是靠排除把语料藏起来：
#   - 读文件不再整读：改流式逐行（_iter_chunk_records + _index_one_file），
#     单文件多大都不会把内存打爆，因此不需要按体积跳过；
#   - 分块不再"无标题就整文 1 段"：改「按 Markdown 标题切 + 超长段落按
#     段落/长度边界二次切」，单块有硬上限 CHUNK_MAX_CHARS，故
#     "整篇当成 1 段入库"这一立项理由不再成立；
#   - 去重不再把全表正文拉进内存：改用 content_hash 唯一索引 + INSERT OR IGNORE。
#
# 两个常量保留为**可配置项**（机制不删），但默认值改为「不拦」：
#   - EXCLUDED_KNOWLEDGE_DIRS 默认 ()：默认让全部语料都可被检索到；
#   - MAX_INDEX_FILE_BYTES 默认 None（不限）：仅作可选保险丝，需要临时封顶时
#     设为字节数即可（例如 512 * 1024 * 1024）。
# ---------------------------------------------------------------------- #

#: 单个知识库文件的体积上限（字节）。None = 不限（默认值）。
#: 分块已改为流式，单文件体积不再影响内存峰值，故不再是提交门的理由。
MAX_INDEX_FILE_BYTES = None

#: 相对 knowledge/ 的目录路径前缀（不区分大小写、按路径段前缀匹配），整目录跳过。
#: 默认为空 = 不做整目录排除（所有语料都必须可被检索到）。
EXCLUDED_KNOWLEDGE_DIRS = ()

#: 分块：单块正文硬上限（字符）。取值论证见 _inventory/检索库重建报告.md：
#: - build_context 只渲染 content[:300]，段过大对 prompt 无增益；
#: - FTS5 的 bm25/rank 按行打分，一个 2.3 MB 的段命中任意一个词就以同一名次
#:   压过所有正常段（实测旧库最长单段 2,306,943 字符，前 10 命中里混着 17 字符
#:   的纯标题碎块与 36~48 KB 的巨型段，排序毫无意义）；
#: - 太小则上下文断裂、行数开销放大。折中取 2000 字符（≈中文 1.5~2 个自然段，
#:   落在任务建议的 1,500~4,000 量级内，偏"可读"一侧）。
CHUNK_MAX_CHARS = 2000

#: 分块：单块软目标（字符）。累积达到该长度后，遇到下一个段落边界即落一块，
#: 避免自然段被切碎成大量单行块。
CHUNK_TARGET_CHARS = 1200

#: 每写入 N 个块提交一次事务（分批提交，避免单事务在低内存机上膨胀）。
COMMIT_EVERY_CHUNKS = 2000


def _limit_desc() -> str:
    """把 MAX_INDEX_FILE_BYTES 渲染成可写日志的说明文本（None 表示不限）。"""
    if MAX_INDEX_FILE_BYTES is None:
        return "不限"
    return "%.2f MB" % (MAX_INDEX_FILE_BYTES / 1048576.0)


def knowledge_scan_skip_reason(fpath: str, knowledge_dir: str):
    """判断某个知识库文件是否应跳过索引（两条索引路径共用的唯一准入口径）。

    刻意做成模块级函数（而非类内私有方法），便于测试/离线脚本直接 import 复用，
    保证「验证时跑的过滤逻辑」与「运行时真正生效的过滤逻辑」是同一份实现。

    Args:
        fpath: 待判定的文件绝对路径。
        knowledge_dir: 知识库根目录，用于把 fpath 换算成相对路径做目录前缀匹配。

    Returns:
        None 表示可索引；否则返回 ``(code, detail)`` 二元组，
        ``code`` ∈ {"excluded", "oversize"}，``detail`` 为可直接写日志的中文说明。

    默认配置下（EXCLUDED_KNOWLEDGE_DIRS 为空且 MAX_INDEX_FILE_BYTES 为 None）
    本函数恒返回 None——即**不做任何整文件/整目录排除**，这是本轮重建的要求。
    """
    if not EXCLUDED_KNOWLEDGE_DIRS and MAX_INDEX_FILE_BYTES is None:
        # 默认路径：不拦任何文件（也省掉每个文件一次 getsize 系统调用）
        return None
    try:
        rel = os.path.relpath(fpath, knowledge_dir)
    except Exception:
        rel = fpath
    rel_norm = rel.replace("\\", "/").strip("/").lower()
    for prefix in EXCLUDED_KNOWLEDGE_DIRS:
        pref = str(prefix).replace("\\", "/").strip("/").lower()
        if pref and (rel_norm == pref or rel_norm.startswith(pref + "/")):
            return ("excluded", "目录在 EXCLUDED_KNOWLEDGE_DIRS 内: %s" % prefix)
    if MAX_INDEX_FILE_BYTES is not None:
        try:
            size = os.path.getsize(fpath)
        except OSError as e:
            return ("oversize", "无法读取文件体积: %s" % e)
        if size > MAX_INDEX_FILE_BYTES:
            return (
                "oversize",
                "单文件 %.2f MB 超过 MAX_INDEX_FILE_BYTES=%s"
                % (size / 1048576.0, _limit_desc()),
            )
    return None


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
                    raw = json.load(f)
                if isinstance(raw, dict):
                    # === 键口径迁移（2026-10-17 写侧 source 相对化）===
                    # 旧状态文件以**开发机绝对路径**为键（实测 832 条，形如
                    # ``C:\Users\<dev>\...\LoveAI-v2\knowledge\x.md``）。写侧改用相对键
                    # 后二者对不上；若放任不管，首启会把 832 个文件全部当"未索引"重扫
                    # 一遍。这里在载入时一次性归一，并立即回写使迁移持久化。
                    self._indexed_files, migrated = self._normalize_state_keys(raw)
                    if migrated:
                        logger.info(
                            "rag_index_state.json 键口径迁移：%d 条绝对路径键已归一为"
                            "相对口径（relpath → project_root）", migrated)
                        self._save_index_state()
                else:
                    self._indexed_files = {}
        except Exception:
            self._indexed_files = {}

    def _normalize_state_keys(self, raw: dict):
        """把索引状态字典里的**绝对路径键**归一为写库同口径的相对键。

        Returns:
            ``(normalized: dict, migrated: int)``

        - 键已是相对形态 → 原样保留（幂等）；
        - 键为绝对路径且可换算到 project_root 之内 → 换成 :meth:`_source_key` 相对键；
        - 归一后若撞车（同一文件同时存在旧绝对键与新相对键）→ 保留**先出现**那条，
          其值只含 mtime/sections/new，取哪条都不改变"该文件已索引"的语义。
        """
        out: dict = {}
        migrated = 0
        for k, v in raw.items():
            nk = k
            if isinstance(k, str) and os.path.isabs(k):
                cand = self._source_key(k)
                if cand != k:
                    nk = cand
                    migrated += 1
            if nk not in out:
                out[nk] = v
        return out, migrated

    def _source_key(self, fpath: str) -> str:
        """``chunks.source`` 的**相对口径**键（与现存 598 个 source 逐字符同形）。

        形态实测（改造前真实库）：``knowledge\\auto-expansion.md`` —— 即
        **相对 project_root、反斜杠分隔、保留 ``knowledge\\`` 前缀**。因此这里用
        ``os.path.relpath(fpath, self.project_root)``；**不能**用相对
        ``knowledge_dir`` 的 relpath，那会丢掉 ``knowledge\\`` 前缀、与现存库不符。

        越界/异常一律退回原始 ``fpath``（``relpath`` 抛 ``ValueError``，或结果以
        ``..`` 开头说明不在 project_root 之内）——保证口径换算失败时不崩，也绝不
        写出畸形相对路径。
        """
        try:
            rel = os.path.relpath(fpath, self.project_root)
        except ValueError:
            return fpath
        if rel == os.curdir or rel.startswith(".."):
            return fpath
        return rel

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

    # ------------------------------------------------------------------ #
    # 流式分块（2026-10-17 重建核心）
    # ------------------------------------------------------------------ #
    #: Markdown 1~6 级标题（旧实现只认 ##/###，导致一级标题文件被视为"无标题"）
    _HEADING_RE = re.compile(r"^\s*#{1,6}[ \t]+(.*\S)\s*$")

    #: 标题行长度上限。**超过此长度的行一定是正文，不是标题**。
    #: 实测：``knowledge/引导恋爱学/引导恋爱学-最终成品-融合优化版.md`` 本身是合法
    #: UTF-8、但正文已乱码的副本，其中一条「1.4.1 闂傚倸鍊搁……」的**单行**长达
    #: 74,274 字符；它被 ``^#{1,6}`` 规则误判成小标题后，该节下每一块的
    #: ``title`` / ``section`` 都被复制进这 74 K 字符，实测使 chunks 表从
    #: 353 MB 膨胀到 908 MB（+504 MB，占全库 43%）。真实小标题远短于此。
    _HEADING_MAX_CHARS = 120

    #: 写库时 title / section 的兜底截断，防止任何未来的怪输入再次撑爆行宽。
    _META_MAX_CHARS = 160

    @classmethod
    def _clip_meta(cls, text: str) -> str:
        """把 title / section 这类元信息截断到 ``_META_MAX_CHARS``，附省略号。"""
        s = (text or "").strip()
        if len(s) <= cls._META_MAX_CHARS:
            return s
        return s[: cls._META_MAX_CHARS] + "…"

    @staticmethod
    def _hard_split_text(text: str, max_chars: int) -> list:
        """把超长文本硬切成若干长度 <= max_chars 的片段。

        优先在换行处断开（保住行语义），找不到合适换行才在字符处硬切。
        返回片段均已 strip，空片段丢弃。
        """
        n = len(text)
        if n <= max_chars:
            return [text]
        out: list = []
        start = 0
        while start < n:
            end = min(start + max_chars, n)
            if end < n:
                nl = text.rfind("\n", start + (max_chars // 2), end)
                if nl > start:
                    end = nl
            piece = text[start:end].strip()
            if piece:
                out.append(piece)
            start = end
        return out

    @classmethod
    def _iter_chunk_records(cls, fh, max_chars: Optional[int] = None,
                            target_chars: Optional[int] = None):
        """流式把文本切块，逐块产出 ``{"heading": str, "body": str}``。

        两级切分：
          1) 先按 Markdown 1~6 级标题切段；标题行**保留在该段正文首行**（与旧
             `_split_markdown_sections` 一致，便于命中后自解释"这是哪一节"）；
          2) 段内以空行为段落边界累积，达到软目标 CHUNK_TARGET_CHARS 且落在段落
             边界上即落一块；任何段落都不得把单块撑过硬上限 CHUNK_MAX_CHARS，
             超长段落按 换行→字符 硬切。

        **无标题文件同样成立**：没有标题就退化为"按段落/长度切"，不再整文 1 段。

        内存上界 ~O(max_chars)：任何时刻只缓存「当前段落」与「当前待落块」，
        不缓存整个文件，故 120 MB 单文件也能安全处理（见报告的资源评估）。

        Yields:
            dict: ``{"heading", "body"}``；``body`` 非空且长度 <= max_chars。
        """
        mx = int(max_chars or CHUNK_MAX_CHARS)
        tg = int(target_chars or CHUNK_TARGET_CHARS)
        st = {"heading": "", "parts": [], "para": []}

        def _para_text():
            if not st["para"]:
                return ""
            return "\n".join(st["para"]).strip()

        def _push(text):
            """把一个段落并入当前块，返回『因满而落下的块』列表。"""
            done = []
            if not text:
                return done
            for piece in cls._hard_split_text(text, mx):
                if st["parts"] and len("\n\n".join(st["parts"] + [piece])) > mx:
                    done.append({"heading": st["heading"],
                                 "body": "\n\n".join(st["parts"])})
                    st["parts"] = []
                st["parts"].append(piece)
                if len("\n\n".join(st["parts"])) >= tg:
                    done.append({"heading": st["heading"],
                                 "body": "\n\n".join(st["parts"])})
                    st["parts"] = []
            return done

        for raw in fh:
            line = raw.rstrip("\r\n")
            # 超长行不可能是标题（见 _HEADING_MAX_CHARS 的实测说明），只当正文
            m = (cls._HEADING_RE.match(line)
                 if len(line) <= cls._HEADING_MAX_CHARS else None)
            if m:
                for rec in _push(_para_text()):
                    yield rec
                st["para"] = []
                if st["parts"]:
                    yield {"heading": st["heading"],
                           "body": "\n\n".join(st["parts"])}
                    st["parts"] = []
                st["heading"] = m.group(1).strip()
                st["para"] = [line]      # 标题行留在正文首行（保持旧行为）
                continue
            if not line.strip():
                for rec in _push(_para_text()):
                    yield rec
                st["para"] = []
                continue
            st["para"].append(line)
            if sum(len(x) + 1 for x in st["para"]) > mx:
                # 无空行的巨型单段：先落盘，防止缓存无界增长
                for rec in _push(_para_text()):
                    yield rec
                st["para"] = []
        for rec in _push(_para_text()):
            yield rec
        if st["parts"]:
            yield {"heading": st["heading"], "body": "\n\n".join(st["parts"])}

    @staticmethod
    def _iter_knowledge_files(knowledge_dir: str) -> list:
        """收集待索引文件（.md/.txt），按「路径浅者优先 + 字母序」排序。

        排序是**确定性去重的前提**：knowledge/ 下存在多份互为镜像的同文文件
        （实测 playbooks.md 同时存在于 6 处），content_hash 唯一索引只会保留先写入
        的那一份；排序保证保留的永远是路径最浅、最"正统"的那份，而不是随
        os.walk 顺序漂移。
        """
        out: list = []
        for root, _dirs, files in os.walk(knowledge_dir):
            for fname in files:
                if fname.lower().endswith((".md", ".txt")):
                    out.append(os.path.join(root, fname))

        def _depth_key(p: str):
            rel = os.path.relpath(p, knowledge_dir).replace("\\", "/")
            return (rel.count("/"), rel.lower())

        out.sort(key=_depth_key)
        return out

    def _index_one_file(self, cur, conn, fpath: str, knowledge_dir: str,
                        max_chars: int, target_chars: int):
        """流式索引单个文件，返回 ``(新增块数, 生成块数, 实际编码)``。

        - **不整读文件**：按行迭代，内存上界 ~O(max_chars)；
        - **不把全表正文拉进内存**：以 content_hash 唯一索引 + INSERT OR IGNORE 判重；
        - 编码：UTF-8 优先；解码失败则按 GBK 重试，最后 utf-8/replace 兜底。
          重试是安全的——同正文哈希相同，已写入的块会被 OR IGNORE 吞掉。
        - ``title`` = ``"<文件名> · <所在小标题>"``（无小标题时就是文件名），
          ``section`` = 小标题，``discipline`` = 学科映射，保证命中后可读性不坍缩。
        """
        fname = os.path.basename(fpath)
        disc = self._extract_discipline(fpath)
        # 写库统一用**相对口径** source（见 _source_key）：让"去掉开发机绝对路径
        # 前缀"的成果沉淀进写侧——解压改 mtime 后重扫、或在终端用户机器上重建，
        # 都不会再把机器绝对路径写回 chunks.source。
        src_key = self._source_key(fpath)
        last = (0, 0, "utf-8")
        for encoding, errors in (("utf-8", "strict"), ("gbk", "strict"),
                                 ("utf-8", "replace")):
            inserted = 0
            seen = 0
            try:
                with open(fpath, "r", encoding=encoding, errors=errors) as fh:
                    for rec in self._iter_chunk_records(fh, max_chars, target_chars):
                        body = (rec.get("body") or "").strip()
                        if not body:
                            continue
                        seen += 1
                        heading = self._clip_meta(rec.get("heading") or "")
                        title = ("%s · %s" % (fname, heading)) if heading else fname
                        title = self._clip_meta(title)
                        digest = hashlib.md5(body.encode("utf-8")).hexdigest()
                        before = conn.total_changes
                        cur.execute(
                            "INSERT OR IGNORE INTO chunks("
                            "title, content, source, section, discipline, content_hash)"
                            " VALUES (?, ?, ?, ?, ?, ?)",
                            (title, body, src_key, heading, disc, digest),
                        )
                        if conn.total_changes == before:
                            continue     # 该正文已在库（镜像文件 / 重跑），不重复入库
                        cur.execute(
                            "INSERT INTO fts(rowid, title, content) VALUES (?, ?, ?)",
                            (cur.lastrowid, title, body),
                        )
                        inserted += 1
            except UnicodeDecodeError:
                last = (inserted, seen, encoding)
                logger.debug("文件 %s 用 %s 解码失败，回退下一编码重试", fpath, encoding)
                continue
            return inserted, seen, encoding
        return last

    #: fts 建表语句：**external content**（索引本体只存倒排表，正文按 rowid 回查
    #: chunks），不再产生逐字复制全部正文的影子表 fts_content。
    FTS_DDL = ("CREATE VIRTUAL TABLE fts USING fts5("
               "title, content, content='chunks', content_rowid='id')")

    def _ensure_fts_schema(self, conn) -> bool:
        """建表 / 迁移到 external-content FTS 结构。返回 True 表示需要全量重建。

        实测真实库结构（2026-10-17 透视；**不要**再用代码里的 IF NOT EXISTS 反推）：
          - ``chunks(id INTEGER PRIMARY KEY, title, content, source, section, discipline)``
            148,070 行；旧的建表语句却写着 ``id INTEGER PRIMARY KEY AUTOINCREMENT``
            且只有 3 列——因 ``IF NOT EXISTS`` 对已存在的库不生效，错配才没炸。
          - ``fts`` 实际是 **5 列 standalone** ``fts5(title, content, source,
            section, discipline)``（脚本另写的 3 列 ``content='chunks'`` 建表语句同样
            因 IF NOT EXISTS 从未生效）。standalone 结构会多出一张影子表
            ``fts_content``，把 title/正文/source/section/discipline 逐字复制一遍，
            实测占用 336.70 MB（全库 38.5%）——纯冗余，本次改造即回收这一块。
          - ``search_fts()`` 的 ``chunks.id = fts.rowid`` JOIN 口径在两种结构下都自洽，
            所以旧代码"能跑"；真正的问题是 5 列 standalone 下代码只写 3 列，
            ``source/section/discipline`` 三列恒为空。

        迁移策略（旧结构无法安全原地增量，只能重建）：
          - ``chunks`` 缺 ``content_hash`` → 补列；历史行哈希为空、唯一索引形同虚设，
            判重失效 → 标记全量重建；
          - ``fts`` 非 external content → DROP 后按 FTS_DDL 建新表 → 标记全量重建；
          - 重建 = 清空 chunks/fts + 丢弃状态文件记忆，由 knowledge/ 文件重新写入。
        """
        cur = conn.cursor()
        need_rebuild = False

        row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks'"
        ).fetchone()
        if row is None:
            cur.execute(
                "CREATE TABLE chunks ("
                "id INTEGER PRIMARY KEY, title TEXT, content TEXT, source TEXT, "
                "section TEXT, discipline TEXT, content_hash TEXT)"
            )
        else:
            cols = {r[1] for r in cur.execute("PRAGMA table_info(chunks)")}
            # 逐列补齐。注意：**旧代码自己的建表语句**是
            # `chunks(id INTEGER PRIMARY KEY AUTOINCREMENT, title, content, source)`
            # ——4 列、没有 section/discipline。也就是说"由旧代码在空目录上新建的库"
            # 会缺 section/discipline，只有本工程预建的那张库才恰好带这两列。
            for col in ("title", "content", "source", "section", "discipline",
                        "content_hash"):
                if col not in cols:
                    cur.execute("ALTER TABLE chunks ADD COLUMN %s TEXT" % col)
                    if col == "content_hash":
                        # 历史行哈希为空 → 唯一索引形同虚设、判重失效 → 必须全量重建
                        need_rebuild = True

        fts_row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='fts'"
        ).fetchone()
        if fts_row is None:
            cur.execute(self.FTS_DDL)
        elif "content='chunks'" not in (fts_row[0] or "").replace('"', "'"):
            cur.execute("DROP TABLE IF EXISTS fts")
            cur.execute(self.FTS_DDL)
            need_rebuild = True

        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(content_hash)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source)")

        if need_rebuild:
            logger.warning(
                "unified_search.db 为旧结构（缺 content_hash 或 fts 非 external "
                "content），清空索引表后按当前分块策略全量重建")
            cur.execute("DELETE FROM chunks")
            try:
                cur.execute("INSERT INTO fts(fts) VALUES('delete-all')")
            except sqlite3.OperationalError:
                pass
            self._indexed_files = {}
            conn.commit()
        return need_rebuild

    def _ensure_fts_indexed(self) -> int:
        """确保 FTS 表存在，并**流式**索引 knowledge/ 下所有 .md/.txt 文件。

        与旧实现的差异（本次重建的核心）：
          - 不再 ``SELECT content FROM chunks`` 把全表正文拉进内存（旧实现每次启动
            都物化 148,070 行 / 131,177,631 字符，实测峰值 RSS 因此多出数百 MB）；
          - 不再整文件 ``f.read()``：改流式逐行读，120 MB 单文件也能处理；
          - 不再整目录/整文件跳过（见文件头常量说明）；
          - 写库 ``source`` 与状态文件键统一**相对 project_root** 口径
            （``knowledge\\...``，见 ``_source_key``），与现存库 598 个 source
            逐字符一致；不再把开发机/终端机器绝对路径写进 ``chunks.source``；
          - 分批提交（COMMIT_EVERY_CHUNKS），避免单事务在低内存机上膨胀。
        幂等：content_hash 唯一索引 + INSERT OR IGNORE，连跑两次行数不增长。
        """
        knowledge_dir = os.path.join(self.project_root, "knowledge")
        if not os.path.isdir(knowledge_dir):
            return 0
        conn = None
        try:
            conn = sqlite3.connect(self.fts_db_path)
            # 低内存机 + 大库：限制页缓存、临时表落盘，避免把内存压满
            try:
                conn.execute("PRAGMA cache_size = -16000")   # ~16 MB 页缓存
                conn.execute("PRAGMA temp_store = FILE")
            except Exception:
                pass

            self._ensure_fts_schema(conn)
            cur = conn.cursor()

            indexed = 0
            pending = 0
            skipped_excluded = 0
            skipped_oversize = 0

            for fpath in self._iter_knowledge_files(knowledge_dir):
                # 准入过滤（默认配置下恒为 None，即不拦任何文件）
                skip = knowledge_scan_skip_reason(fpath, knowledge_dir)
                if skip is not None:
                    if skip[0] == "oversize":
                        skipped_oversize += 1
                    else:
                        skipped_excluded += 1
                    logger.debug("跳过知识库文件（%s）: %s", skip[1], fpath)
                    continue
                try:
                    mtime = os.path.getmtime(fpath)
                except Exception:
                    continue
                # 状态文件键与写库 source 同口径（相对 project_root）。若仍用绝对
                # 路径作键，将与改写后的相对键对不上，首启把全部文件当"未索引"
                # 重扫一遍（详见 _source_key / _normalize_state_keys）。
                src_key = self._source_key(fpath)
                prev = self._indexed_files.get(src_key)
                if prev and prev.get("mtime") == mtime:
                    continue  # 未修改
                try:
                    n_new, n_seen, _enc = self._index_one_file(
                        cur, conn, fpath, knowledge_dir,
                        CHUNK_MAX_CHARS, CHUNK_TARGET_CHARS)
                except Exception as e:
                    logger.warning("索引知识库文件失败 %s: %s", fpath, e)
                    continue
                indexed += n_new
                pending += n_new
                # 无论是否新增段落，都据当前文件校正状态（对接已建库时状态为空）
                self._indexed_files[src_key] = {
                    "mtime": mtime,
                    "sections": n_seen,
                    "new": n_new,
                }
                if pending >= COMMIT_EVERY_CHUNKS:
                    conn.commit()
                    pending = 0

            conn.commit()
            if skipped_oversize or skipped_excluded:
                logger.info(
                    "FTS 准入过滤：跳过 %d 个超限单文件(>%s) 与 %d 个被排除目录文件；"
                    "调整 MAX_INDEX_FILE_BYTES / EXCLUDED_KNOWLEDGE_DIRS 可改变此行为",
                    skipped_oversize, _limit_desc(), skipped_excluded,
                )
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

        **分词器实测事实（2026-10-17 权威探针，纠正旧注释的错误认知）**：
        真实库用默认 unicode61 分词器，它**不按单字切分 CJK**，而是把一段连续的
        CJK 当作 **1 个 token**——实测 ``开场白类型`` / ``假性时间限制`` /
        ``废物测试识别与应对`` 各为单个 token，因此
        ``MATCH "开场白"`` **匹配不到** ``开场白类型``（0 命中），而
        ``MATCH 开场白*`` 前缀查询能命中。

        据此本函数的构造规则：
        - 每个 CJK 段：整段短语（精确命中、排名最高）+ 整段前缀（补住"查询词只是
          某个长 token 的开头"的漏召回，见下）+ 滑动二元组；
        - 拉丁/数字：按词；
        - 全部 OR 连接、去重、限量（24 项），特殊字符安全。

        **已知召回上限（未解决，如实记录）**：短语、整段前缀、二元组都只能命中
        "token 与之整串相等、或 token 以之开头"的情形；当查询词落在某个更长 CJK
        token 的**中间**时无法召回——例如 token ``假性时间限制`` 不会被查询
        ``时间限制`` 命中（实测 0 命中）。要解决需换分词器（如 trigram）或双写一份
        CJK 二元组倒排，都会显著改变索引体积与排序语义，本轮不动。
        详见 _inventory/检索库重建报告.md「残余风险」。
        """
        if not query:
            return ""

        terms: list = []
        for run in re.findall(r"[一-鿿㐀-䶿]+", query):
            if len(run) == 1:
                terms.append(run)
            else:
                terms.append(f'"{run}"')  # 整段短语（精确命中，排名最高）
                # 整段前缀：unicode61 把连续 CJK 当作 1 个 token，若查询词只是某个
                # 长 token 的开头（查询"红丸米格道" vs token"红丸米格道语料内容"），
                # 只有前缀查询能命中。严格增量：只会多召回、不会少召回
                # （实测 柯李思 2→8、米格道 23→32、依恋类型 1928→2083 命中）。
                terms.append(f'"{run}"*')
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

        实际库结构（2026-10-17 实测 + 本次重建统一）：``fts`` 为 **external
        content** 虚表 ``fts5(title, content, content='chunks', content_rowid='id')``，
        索引本体只含 title/content 两列的倒排表；source/section/discipline 等元数据
        存于 ``chunks``，通过 ``fts.rowid == chunks.id`` 关联。
        （旧库曾是 5 列 standalone 结构、库内多出一张冗余影子表 fts_content；
        本次重建已改造为 external content，见 ``_ensure_fts_schema``。）

        注：``section`` 未单独进 FTS 索引——小标题已写在 ``chunks.title``
        与该段正文首行里，再索引一遍只增体积、不改召回。

        Args:
            query: 查询语句。
            top_k: 返回条数。

        Returns:
            list[dict]，每项含 title/content/source/score；异常或无表时返回 []。
        """
        match_expr = self._preprocess_fts_query(query)
        if not match_expr:
            return []

        # external content FTS5：fts 虚表 JOIN chunks 取元数据
        sql = (
            "SELECT chunks.title, chunks.content, chunks.source, fts.rank "
            "FROM fts JOIN chunks ON chunks.id = fts.rowid "
            "WHERE fts MATCH ? ORDER BY fts.rank LIMIT ?"
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
        skipped_excluded = 0
        skipped_oversize = 0
        for fpath in self._iter_knowledge_files(knowledge_dir):
            if not fpath.lower().endswith(".md"):
                continue
            # 准入过滤：与 _ensure_fts_indexed 共用同一个判定函数，保证两条索引
            # 路径口径一致。默认配置下恒为 None（不拦任何文件）——上一轮"整目录
            # 排除 + 单文件上限"已按重建要求取消，见文件头常量说明。
            skip = knowledge_scan_skip_reason(fpath, knowledge_dir)
            if skip is not None:
                if skip[0] == "oversize":
                    skipped_oversize += 1
                else:
                    skipped_excluded += 1
                logger.debug("跳过知识库文件（%s）: %s", skip[1], fpath)
                continue

            fname = os.path.basename(fpath)
            discipline = self._extract_discipline(fpath)
            # 流式：不整读文件，逐块 upsert
            try:
                fh = open(fpath, "r", encoding="utf-8", errors="replace")
            except Exception as e:
                logger.warning(f"读取知识库文件失败 {fpath}: {e}")
                continue
            try:
                for idx, sec in enumerate(self._iter_chunk_records(fh)):
                    heading = self._clip_meta(sec.get("heading") or "")
                    body = (sec.get("body") or "").strip()
                    if not body:
                        continue

                    doc_id = hashlib.md5(
                        f"{fpath}::{idx}".encode("utf-8")
                    ).hexdigest()
                    title = ("%s · %s" % (fname, heading)) if heading else fname
                    metadata = {
                        "title": self._clip_meta(title),
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
            finally:
                try:
                    fh.close()
                except Exception:
                    pass

        if skipped_oversize or skipped_excluded:
            logger.info(
                "Chroma 准入过滤：跳过 %d 个超限单文件(>%s) 与 %d 个被排除目录文件",
                skipped_oversize,
                _limit_desc(),
                skipped_excluded,
            )
        logger.info(f"知识库索引完成，共写入 {indexed} 个段落")
        return indexed

    @classmethod
    def _split_markdown_sections(cls, text: str) -> list:
        """按 Markdown 标题 + 长度边界切分文本（兼容保留入口）。

        **行为已变更（2026-10-17 检索库重建）**：旧实现只认 ``##``/``###``，
        且"全文无标题时整篇作为 1 段"——这正是旧库出现 2,306,943 字符单段的成因。
        现在本方法只是流式分块 ``_iter_chunk_records`` 的兼容封装：
        - 标题识别放宽到 1~6 级；
        - 无标题文本按 段落/长度 边界切分，不再整文 1 段；
        - 单段 body 长度有硬上限 CHUNK_MAX_CHARS。

        Args:
            text: 原始 Markdown 文本。

        Returns:
            list[dict]：[{"heading": str, "body": str}, ...]
        """
        return list(cls._iter_chunk_records(io.StringIO(text)))

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
