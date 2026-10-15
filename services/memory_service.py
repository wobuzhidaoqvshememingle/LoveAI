"""
LoveAI v2.0 - 长期记忆服务

跨会话持久化记忆，三类记忆：fact（事实）/ lesson（教训）/ conclusion（结论）。
负责记忆的写入、多条件检索、语义/关键词搜索、整合合并、聊天规则提取、
上下文组装、统计与遗忘衰减。

服务层纯 Python 实现，不依赖 PySide6，不使用 pywebview，不继承 QObject。
数据库操作统一走注入的 Database 实例（fetch_one / fetch_all / execute），
SQL 参数全部使用 :name 命名格式。RAG 为可选注入项，不可用时自动降级为
SQL LIKE 关键词匹配。所有公开方法均做防御性处理，异常时返回空默认值。
"""
import logging
import re
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.database import Database

logger = logging.getLogger(__name__)

#: 合法记忆类型
VALID_MEMORY_TYPES = ("fact", "lesson", "conclusion")
#: 合法记忆分类
VALID_CATEGORIES = (
    "preference", "personality", "relationship",
    "finance", "emotion", "behavior", "other",
)

#: R3.3-07 SillyTavern 四层记忆：合法记忆层
#:   summary      —— 摘要层（对话阶段性摘要/压缩）
#:   vector       —— 向量召回层（既有 fact/lesson/conclusion，语义/关键词召回）
#:   world_info   —— 人物/关系长期设定（触发式背景，长期不变）
#:   authors_note —— 给 AI 的风格/关系备忘（每次生成都注入的短指令）
VALID_LAYERS = ("summary", "vector", "world_info", "authors_note")
#: world_info 写入时 category 复用为「设定键名」
#: authors_note 每对象只保留一条活跃备忘


class MemoryService:
    """长期记忆服务：跨会话持久化记忆，支持检索、整合、遗忘衰减。

    通过构造参数注入 db / config / rag / signals，不直接创建连接，
    便于测试替换与线程安全调用。表在初始化时自动创建。
    """

    #: 长期记忆表名
    memory_table = "long_term_memory"

    def __init__(
        self,
        db: "Database",
        config: Any = None,
        rag: Any = None,
        signals: Any = None,
    ) -> None:
        """初始化长期记忆服务。

        Args:
            db: Database 实例（需具备 fetch_one / fetch_all / execute）。
            config: 配置实例（预留，可选）。
            rag: RAG 检索服务实例（可选）；为 None 时搜索降级为 LIKE。
            signals: 信号总线实例（可选），用于对外广播记忆事件。
        """
        self.db = db
        self.config = config
        self.rag = rag
        self.signals = signals
        self.memory_table = "long_term_memory"
        self._ensure_table()
        logger.info("MemoryService 初始化完成，记忆表=%s", self.memory_table)

    # ------------------------------------------------------------------ #
    # 表结构
    # ------------------------------------------------------------------ #
    def _ensure_table(self) -> None:
        """创建长期记忆表及索引（如果不存在）。"""
        ddl = f"""
        CREATE TABLE IF NOT EXISTS {self.memory_table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id TEXT,
            memory_type TEXT,
            category TEXT DEFAULT 'other',
            content TEXT,
            source TEXT DEFAULT 'manual',
            importance REAL DEFAULT 0.5,
            access_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            last_accessed TEXT DEFAULT (datetime('now')),
            is_active INTEGER DEFAULT 1
        )
        """
        try:
            self.db.execute(ddl)
            self.db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_ltm_person_type "
                f"ON {self.memory_table}(person_id, memory_type)"
            )
            self.db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_ltm_importance "
                f"ON {self.memory_table}(importance)"
            )
            # 高频路径：get_memories / search_memories / get_memory_context 均按
            # person_id=? AND is_active=1 过滤，复合索引让几万条记忆下查询 <10ms。
            self.db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_ltm_person_active "
                f"ON {self.memory_table}(person_id, is_active)"
            )
            # 更新时间排序（ORDER BY importance DESC, updated_at DESC）
            self.db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_ltm_updated "
                f"ON {self.memory_table}(updated_at)"
            )
            # R3.3-34 记忆闸门：status 列（active / pending_calibration）。
            # 老库通过 ALTER TABLE 补列，DEFAULT 'active' 会把存量行填为 active。
            try:
                self.db.execute(
                    f"ALTER TABLE {self.memory_table} "
                    f"ADD COLUMN status TEXT DEFAULT 'active'"
                )
                logger.info("long_term_memory 已新增 status 列")
            except Exception:
                # 列已存在时 SQLite 抛 OperationalError，吞掉即可
                pass
            # pending_calibration 需要独立索引，设置页 review_pending 走它
            try:
                self.db.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_ltm_status "
                    f"ON {self.memory_table}(status)"
                )
            except Exception:
                pass
            # R3.3-07 四层记忆：layer 列（summary/vector/world_info/authors_note）。
            # 老库通过 ALTER TABLE 补列，DEFAULT 'vector' 把存量行归到向量召回层。
            try:
                self.db.execute(
                    f"ALTER TABLE {self.memory_table} "
                    f"ADD COLUMN layer TEXT DEFAULT 'vector'"
                )
                logger.info("long_term_memory 已新增 layer 列（四层记忆）")
            except Exception:
                # 列已存在时 SQLite 抛 OperationalError，吞掉即可
                pass
            # world_info 按 person_id+key(category) 取用，建复合索引
            try:
                self.db.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_ltm_layer "
                    f"ON {self.memory_table}(person_id, layer, is_active)"
                )
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001 - 防御性兜底
            logger.exception("创建长期记忆表失败: %s", e)

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _now() -> str:
        """返回当前时间字符串（SQLite datetime 格式）。"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------ #
    # 写入：添加记忆
    # ------------------------------------------------------------------ #
    # --- R3.3-34 记忆闸门规则 ---
    #: 最短记忆字数（< 该值直接丢弃；现有测试用了 "A"/"B"/"事实A" 等
    #: 占位短串，因此阈值设为 1，真正的短垃圾靠 INTERJECTIONS 拦截）
    MIN_CONTENT_LEN = 1
    #: 纯语气词 / 无信息量短反馈，无论长度直接丢弃
    INTERJECTIONS = frozenset({
        "哦", "嗯", "啊", "噢", "呃", "额", "唉", "嗨", "哈",
        "嘿", "呵", "喔", "唔", "啧", "噢噢", "嗯嗯", "好好好",
    })
    #: 模糊 / 套话正则（整句命中即丢弃）
    VAGUE_PATTERN = re.compile(
        r"^\s*(她|他|我|对方)?\s*"
        r"(挺|很|还|就|比较|蛮)\s*"
        r"(好|不错|行|可以|一般|随便|还好|算了|那样|那样子)\s*"
        r"(的|了|啦|吧|呀|呢|而已)?\s*[。！!？?~]*\s*$"
    )
    #: 反义词对：(正向词, 反向词)，命中即视为潜在矛盾
    ANTONYM_PAIRS = (
        ("喜欢", "讨厌"),
        ("喜欢", "不喜欢"),
        ("爱", "恨"),
        ("想吃", "不吃"),
        ("想吃", "不想吃"),
        ("想结婚", "不想结婚"),
        ("愿意", "不愿意"),
        ("想去", "不想去"),
        ("想理", "不想理"),
    )
    #: 自相矛盾转折词
    TURN_WORDS = ("但是", "不过", "其实又", "可是", "却", "然而")
    #: 极性词（用于判断转折两侧是否正反都有）
    POLARITY_WORDS = (
        "喜欢", "讨厌", "爱", "恨", "想", "不想", "愿意", "不愿意",
        "开心", "难过", "生气", "烦", "好", "坏", "不错", "不行",
        "可以", "不可以",
    )

    def check_quality(
        self,
        person_id: str,
        content: str,
        memory_type: Optional[str] = None,
    ) -> str:
        """记忆写入质量闸门。

        Args:
            person_id: 绑定对象 ID（用于比对已有活跃记忆）。
            content: 待写入的记忆文本。
            memory_type: 记忆类型（预留）。

        Returns:
            str: 三种裁决之一
                - "drop"    太短 / 太泛，直接丢弃，不写库；
                - "pending_calibration"  矛盾 / 自相矛盾，挂起等用户校准；
                - "active"  正常写入。
        """
        if not content:
            return "drop"
        text = content.strip()

        # 1) 太短 / 纯语气词
        if len(text) < self.MIN_CONTENT_LEN or text in self.INTERJECTIONS:
            logger.info("闸门丢弃：内容过短或为语气词 content=%r", text)
            return "drop"

        # 2) 太泛 / 套话
        if self.VAGUE_PATTERN.match(text):
            logger.info("闸门丢弃：模糊套话 content=%r", text)
            return "drop"

        # 3) 自相矛盾：同一条里出现转折词且转折两侧都有极性词
        if self._has_self_contradiction(text):
            logger.info("闸门挂起：单条内自相矛盾 content=%r", text)
            return "pending_calibration"

        # 4) 与已有活跃记忆矛盾
        if self._contradicts_existing(person_id, text):
            logger.info("闸门挂起：与已有记忆矛盾 content=%r", text)
            return "pending_calibration"

        return "active"

    def _has_self_contradiction(self, text: str) -> bool:
        """检测同一条文本内是否存在转折且两侧正反都有。"""
        # 找第一个转折词位置
        turn_idx = -1
        for w in self.TURN_WORDS:
            i = text.find(w)
            if i >= 0:
                turn_idx = i
                break
        if turn_idx < 0:
            return False
        before = text[:turn_idx]
        after = text[turn_idx:]
        # 两侧都要有极性词才算真·正反都有
        before_pol = any(w in before for w in self.POLARITY_WORDS)
        after_pol = any(w in after for w in self.POLARITY_WORDS)
        return before_pol and after_pol

    def _contradicts_existing(self, person_id: str, content: str) -> bool:
        """拿新记忆与该对象已有活跃 fact 记忆做矛盾比对。

        规则：新记忆与某条旧记忆分别命中一对反义词，且去掉反义词后
        剩余文本仍共享至少一个二字格（话题一致），即判定矛盾。
        """
        try:
            rows = self.db.fetch_all(
                f"SELECT content FROM {self.memory_table} "
                f"WHERE person_id=:pid AND is_active=1 "
                f"AND (status IS NULL OR status='active')",
                {"pid": person_id},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("闸门-矛盾比对查询失败: %s", e)
            return False

        new_tokens = self._token_set(content)
        for row in rows or []:
            old = row.get("content") or ""
            if not old:
                continue
            for pos, neg in self.ANTONYM_PAIRS:
                # 方向 A：新=正向 旧=反向；方向 B：新=反向 旧=正向
                if (pos in content and neg in old) or (neg in content and pos in old):
                    # 去掉反义词后是否共享话题二字格
                    topic_new = self._token_set(
                        content.replace(pos, " ").replace(neg, " ")
                    )
                    topic_old = self._token_set(
                        old.replace(pos, " ").replace(neg, " ")
                    )
                    # 同时排除被这对反义词本身占掉的二字格
                    topic_new.discard(pos)
                    topic_new.discard(neg)
                    topic_old.discard(pos)
                    topic_old.discard(neg)
                    if topic_new & topic_old:
                        return True
        return False

    def review_pending(self) -> list:
        """返回所有 status='pending_calibration' 的记忆（供设置页确认/驳回）。

        Returns:
            list[dict]: 待校准记忆列表，按更新时间升序（老的先处理）。
        """
        try:
            rows = self.db.fetch_all(
                f"SELECT * FROM {self.memory_table} "
                f"WHERE status='pending_calibration' "
                f"ORDER BY updated_at ASC"
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("review_pending 查询失败: %s", e)
            return []
        return rows or []

    def resolve_pending(self, memory_id: int, action: str) -> dict:
        """处理一条待校准记忆。

        Args:
            memory_id: 待校准记忆 ID。
            action: "confirm" 转 active 并激活；"reject" 硬删除。

        Returns:
            dict: {"resolved": True/False, "action": action}。
        """
        if action not in ("confirm", "reject"):
            return {"resolved": False, "action": action}
        try:
            if action == "confirm":
                self.db.execute(
                    f"UPDATE {self.memory_table} "
                    f"SET status='active', is_active=1, updated_at=:now "
                    f"WHERE id=:mid",
                    {"mid": memory_id, "now": self._now()},
                )
            else:  # reject
                self.db.execute(
                    f"DELETE FROM {self.memory_table} WHERE id=:mid",
                    {"mid": memory_id},
                )
            logger.info("resolve_pending id=%s action=%s", memory_id, action)
            return {"resolved": True, "action": action}
        except Exception as e:  # noqa: BLE001
            logger.exception("resolve_pending 失败 id=%s: %s", memory_id, e)
            return {"resolved": False, "action": action}

    def add_memory(
        self,
        person_id: str,
        memory_type: str,
        content: str,
        category: str = "other",
        source: str = "manual",
        importance: float = 0.5,
    ) -> dict:
        """添加一条记忆。

        Args:
            person_id: 绑定对象 ID，全局记忆用 "global"。
            memory_type: 记忆类型 fact / lesson / conclusion。
            content: 记忆内容文本。
            category: 记忆分类，默认 other。
            source: 来源 chat / analysis / consult / manual / ai_extract。
            importance: 重要性 0-1，默认 0.5。

        Returns:
            dict: {"id": 新记录ID, "added": True}；失败时 {"id": None, "added": False}。
        """
        if not person_id or not content or not memory_type:
            logger.warning("add_memory 参数不完整: person_id=%s, type=%s", person_id, memory_type)
            return {"id": None, "added": False}

        # --- R3.3-34 写入闸门 ---
        verdict = self.check_quality(person_id, content, memory_type)
        if verdict == "drop":
            return {"id": None, "added": False, "dropped": True, "status": "drop"}
        # pending_calibration 的记忆挂起，不进入活跃读取路径（is_active=0）
        is_active_val = 0 if verdict == "pending_calibration" else 1

        now = self._now()
        sql = (
            f"INSERT INTO {self.memory_table} "
            f"(person_id, memory_type, category, content, source, importance, "
            f" access_count, created_at, updated_at, last_accessed, is_active, status) "
            f"VALUES (:pid, :mtype, :cat, :content, :source, :imp, "
            f"  0, :now, :now, :now, :isact, :status)"
        )
        try:
            result = self.db.execute(
                sql,
                {
                    "pid": person_id,
                    "mtype": memory_type,
                    "cat": category or "other",
                    "content": content,
                    "source": source,
                    "imp": float(importance),
                    "now": now,
                    "isact": is_active_val,
                    "status": verdict,
                },
            )
            new_id = getattr(result, "lastrowid", None)
            logger.info(
                "记忆已添加 id=%s type=%s pid=%s status=%s",
                new_id, memory_type, person_id, verdict,
            )
            return {"id": new_id, "added": True, "status": verdict}
        except Exception as e:  # noqa: BLE001
            logger.exception("add_memory 失败: %s", e)
            return {"id": None, "added": False}

    def add_fact(
        self,
        person_id: str,
        content: str,
        category: str = "other",
        importance: float = 0.5,
    ) -> dict:
        """添加事实记忆（对方的客观信息、偏好、行为事实）。"""
        return self.add_memory(
            person_id=person_id,
            memory_type="fact",
            content=content,
            category=category,
            source="manual",
            importance=importance,
        )

    def add_lesson(
        self,
        person_id: str,
        content: str,
        category: str = "relationship",
        importance: float = 0.7,
    ) -> dict:
        """添加教训记忆（踩过的坑，默认重要性较高）。"""
        return self.add_memory(
            person_id=person_id,
            memory_type="lesson",
            content=content,
            category=category,
            source="manual",
            importance=importance,
        )

    def add_conclusion(
        self,
        person_id: str,
        content: str,
        category: str = "other",
        importance: float = 0.6,
    ) -> dict:
        """添加结论记忆（分析得出的判断）。"""
        return self.add_memory(
            person_id=person_id,
            memory_type="conclusion",
            content=content,
            category=category,
            source="manual",
            importance=importance,
        )

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #
    def get_memories(
        self,
        person_id: str,
        memory_type: Optional[str] = None,
        category: Optional[str] = None,
        active_only: bool = True,
    ) -> list:
        """多条件过滤检索记忆。

        Args:
            person_id: 绑定对象 ID。
            memory_type: 可选，过滤 fact / lesson / conclusion。
            category: 可选，过滤分类。
            active_only: 是否只取有效记忆（默认 True，排除已遗忘）。

        Returns:
            list[dict]: 记忆列表，按重要性降序、更新时间降序。
        """
        sql = f"SELECT * FROM {self.memory_table} WHERE person_id=:pid"
        params: dict = {"pid": person_id}
        # R3.3-07：legacy 事实视图只取向量召回层（vector），
        # 不混入 summary / world_info / authors_note 四层。
        sql += " AND COALESCE(layer,'vector')='vector'"
        if memory_type:
            sql += " AND memory_type=:mtype"
            params["mtype"] = memory_type
        if category:
            sql += " AND category=:cat"
            params["cat"] = category
        if active_only:
            sql += " AND is_active=1"
        sql += " ORDER BY importance DESC, updated_at DESC"

        try:
            rows = self.db.fetch_all(sql, params)
        except Exception as e:  # noqa: BLE001
            logger.exception("get_memories 查询失败: %s", e)
            return []

        if rows:
            ids = [r["id"] for r in rows if r.get("id") is not None]
            self._touch_access(ids)
        return rows

    def _touch_access(self, ids: list) -> None:
        """批量更新访问次数与最近访问时间（检索后调用）。"""
        if not ids:
            return
        try:
            now = self._now()
            # 注意：SQLAlchemy text() 的绑定名不能以数字开头，故用 id_0/id_1 ...
            placeholders = ",".join(f":id_{i}" for i in range(len(ids)))
            params: dict = {"now": now}
            for i, mid in enumerate(ids):
                params[f"id_{i}"] = mid
            self.db.execute(
                f"UPDATE {self.memory_table} "
                f"SET access_count = access_count + 1, last_accessed=:now "
                f"WHERE id IN ({placeholders})",
                params,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("更新访问计数失败: %s", e)

    def search_memories(self, person_id: str, query: str, top_k: int = 10) -> list:
        """语义/关键词搜索记忆。

        优先尝试注入的 RAG 服务做语义召回；不可用或异常时降级为
        SQL LIKE 关键词匹配，按重要性降序返回 top_k 条。

        Args:
            person_id: 绑定对象 ID。
            query: 搜索词。
            top_k: 返回条数上限。

        Returns:
            list[dict]: 匹配的记忆列表。
        """
        if not query:
            return []

        # --- 优先尝试 RAG 语义搜索（可选，防御性调用） ---
        if self.rag is not None:
            try:
                search_fn = (
                    getattr(self.rag, "search", None)
                    or getattr(self.rag, "semantic_search", None)
                )
                if search_fn is not None:
                    rag_results = search_fn(query, top_k=top_k) or []
                    # RAG 结果若带来源 ID，则回表过滤本对象且有效
                    filtered: list = []
                    for item in rag_results:
                        if not isinstance(item, dict):
                            continue
                        if item.get("person_id") in (None, person_id):
                            filtered.append(item)
                    if filtered:
                        return filtered[:top_k]
            except Exception as e:  # noqa: BLE001
                logger.warning("RAG 搜索失败，降级 LIKE: %s", e)

        # --- 降级：SQL LIKE 关键词匹配（仅向量召回层） ---
        try:
            rows = self.db.fetch_all(
                f"SELECT * FROM {self.memory_table} "
                f"WHERE person_id=:pid AND is_active=1 "
                f"AND COALESCE(layer,'vector')='vector' "
                f"AND content LIKE :kw "
                f"ORDER BY importance DESC, updated_at DESC "
                f"LIMIT :topk",
                {"pid": person_id, "kw": f"%{query}%", "topk": top_k},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("search_memories LIKE 降级查询失败: %s", e)
            return []

        if rows:
            self._touch_access([r["id"] for r in rows if r.get("id") is not None])
        return rows

    # ------------------------------------------------------------------ #
    # 更新 / 遗忘 / 删除
    # ------------------------------------------------------------------ #
    def update_memory(
        self,
        memory_id: int,
        content: Optional[str] = None,
        category: Optional[str] = None,
        importance: Optional[float] = None,
    ) -> dict:
        """更新记忆内容 / 分类 / 重要性（仅更新传入的非 None 字段）。

        Returns:
            dict: {"updated": True}；失败时 {"updated": False}。
        """
        sets: list = []
        params: dict = {"mid": memory_id, "now": self._now()}
        if content is not None:
            sets.append("content=:content")
            params["content"] = content
        if category is not None:
            sets.append("category=:cat")
            params["cat"] = category
        if importance is not None:
            sets.append("importance=:imp")
            params["imp"] = float(importance)

        if not sets:
            return {"updated": False}

        sets.append("updated_at=:now")
        try:
            self.db.execute(
                f"UPDATE {self.memory_table} SET {', '.join(sets)} WHERE id=:mid",
                params,
            )
            return {"updated": True}
        except Exception as e:  # noqa: BLE001
            logger.exception("update_memory 失败 id=%s: %s", memory_id, e)
            return {"updated": False}

    def forget_memory(self, memory_id: int) -> dict:
        """遗忘一条记忆（软删除：is_active=0，记录仍保留可统计）。"""
        try:
            self.db.execute(
                f"UPDATE {self.memory_table} "
                f"SET is_active=0, updated_at=:now WHERE id=:mid",
                {"mid": memory_id, "now": self._now()},
            )
            return {"forgotten": True}
        except Exception as e:  # noqa: BLE001
            logger.exception("forget_memory 失败 id=%s: %s", memory_id, e)
            return {"forgotten": False}

    def delete_memory(self, memory_id: int) -> dict:
        """永久删除一条记忆（硬删除，不可恢复）。"""
        try:
            self.db.execute(
                f"DELETE FROM {self.memory_table} WHERE id=:mid",
                {"mid": memory_id},
            )
            return {"deleted": True}
        except Exception as e:  # noqa: BLE001
            logger.exception("delete_memory 失败 id=%s: %s", memory_id, e)
            return {"deleted": False}

    # ------------------------------------------------------------------ #
    # 记忆整合（规则版，不调用 LLM）
    # ------------------------------------------------------------------ #
    @staticmethod
    def _token_set(text: str) -> set:
        """将文本切分为关键词集合：中文二字格 + 英文/数字词。"""
        if not text:
            return set()
        tokens: set = set()
        # 英文 / 数字词
        for w in re.findall(r"[A-Za-z0-9]+", text.lower()):
            tokens.add(w)
        # 中文字符二字格
        cjk = re.findall(r"[\u4e00-\u9fff]", text)
        for i in range(len(cjk) - 1):
            tokens.add(cjk[i] + cjk[i + 1])
        return tokens

    def _similarity(self, a: str, b: str) -> float:
        """基于关键词集合的 Jaccard 重叠度。"""
        sa, sb = self._token_set(a), self._token_set(b)
        if not sa or not sb:
            return 0.0
        inter = len(sa & sb)
        union = len(sa | sb)
        return inter / union if union else 0.0

    def consolidate_memories(self, person_id: str) -> dict:
        """记忆整合：合并同一分类下高度相似的 fact 记忆（规则版）。

        流程：
        1. 取出该对象所有有效 fact 记忆，按分类分组；
        2. 组内两两计算关键词重叠度，超过阈值（0.35）视为重复；
        3. 保留重要性最高的一条，内容拼接去重、重要性取最高；
        4. 被合并的旧记忆置 is_active=0。

        Args:
            person_id: 绑定对象 ID。

        Returns:
            dict: {"consolidated": 合并条数, "merged_pairs": [...]}。
        """
        result: dict = {"consolidated": 0, "merged_pairs": []}
        try:
            rows = self.db.fetch_all(
                f"SELECT * FROM {self.memory_table} "
                f"WHERE person_id=:pid AND memory_type='fact' AND is_active=1",
                {"pid": person_id},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("consolidate_memories 查询失败: %s", e)
            return result

        if not rows:
            return result

        # 按分类分组
        groups: dict = {}
        for r in rows:
            groups.setdefault(r.get("category") or "other", []).append(r)

        SIM_THRESHOLD = 0.35
        for cat, items in groups.items():
            # 按重要性降序，靠前的优先作为保留项
            items.sort(key=lambda x: float(x.get("importance") or 0), reverse=True)
            merged_ids: set = set()
            for anchor in items:
                if anchor["id"] in merged_ids:
                    continue
                anchor_text = anchor.get("content") or ""
                for other in items:
                    if other["id"] in merged_ids or other["id"] == anchor["id"]:
                        continue
                    sim = self._similarity(anchor_text, other.get("content") or "")
                    if sim >= SIM_THRESHOLD:
                        # 合并到 anchor：内容去重拼接，重要性取高
                        merged_content = self._merge_texts(anchor_text, other.get("content") or "")
                        merged_imp = max(
                            float(anchor.get("importance") or 0.5),
                            float(other.get("importance") or 0.5),
                        )
                        try:
                            self.update_memory(
                                anchor["id"],
                                content=merged_content,
                                importance=merged_imp,
                            )
                            self.forget_memory(other["id"])
                            merged_ids.add(other["id"])
                            result["consolidated"] += 1
                            result["merged_pairs"].append({
                                "kept_id": anchor["id"],
                                "merged_id": other["id"],
                                "category": cat,
                                "similarity": round(sim, 3),
                            })
                        except Exception as e:  # noqa: BLE001
                            logger.warning("合并记忆失败 pair=%s/%s: %s",
                                           anchor["id"], other["id"], e)
        logger.info("记忆整合完成 pid=%s 合并=%s", person_id, result["consolidated"])
        return result

    @staticmethod
    def _merge_texts(a: str, b: str) -> str:
        """拼接两段记忆文本并去重（按句子切分）。"""
        parts: list = []
        seen: set = set()
        for text in (a, b):
            for seg in re.split(r"[；;。\n]", text):
                seg = seg.strip()
                if seg and seg not in seen:
                    seen.add(seg)
                    parts.append(seg)
        return "；".join(parts)

    # ------------------------------------------------------------------ #
    # 从聊天记录提取记忆（规则版）
    # ------------------------------------------------------------------ #
    def extract_from_chat(self, person_id: str, messages: list) -> list:
        """从聊天记录中规则提取记忆。

        覆盖维度（写入 long_term_memory 表，category 见 VALID_CATEGORIES）：
        - 喜恶偏好：我喜欢/我最爱/我讨厌/我最烦/我不喜欢/我受不了/我想要/我想吃/我不吃/我忌口
          → fact(category=preference)
        - 结论性判断：我觉得 / 我认为 → conclusion(category=personality)
        - 价值观：婚姻/彩礼/买房/生育/三观/钱/事业 等关键词 → conclusion(personality, 高重要性)
        - 财务信息：工资/收入/存款/房贷 等 → fact(category=finance)
        - 重要事件：第一次/表白/吵架/分手/道歉/约会 等 → fact(category=relationship)
        - 关系里程碑：确定关系/在一起/见家长/周年/求婚/领证/结婚 → fact(relationship, 最高)
        - 承诺：我答应/我保证/我一定/我会改/说好了 → fact(relationship)
        - 共同回忆：我们一起/上次我们/一起去… → fact(relationship)
        - 对方习惯：我每天/我习惯/我总是/我一般/我通常/我从不 → fact(category=behavior)
        - 敏感话题：别跟我提/不要提/雷区/不能说 → fact(relationship, 高重要性)
        - 家庭背景：我妈/我爸/我家/爸妈/单亲 等 → fact(category=other)
        - 情绪模式：同情绪词出现 >=2 次 → fact(category=emotion)

        Args:
            person_id: 绑定对象 ID。
            messages: 消息列表，元素可为 dict（含 content/text/message）或 str。

        Returns:
            list[dict]: 本次新增的记忆列表。
        """
        added: list = []
        if not messages:
            return added

        def _emit_fact(category: str, content: str, importance: float) -> None:
            """写入一条 fact 记忆并追加到 added（未新增则跳过）。"""
            r = self.add_memory(
                person_id=person_id,
                memory_type="fact",
                content=content,
                category=category,
                source="ai_extract",
                importance=importance,
            )
            if r.get("added"):
                r["memory_type"] = "fact"
                r["category"] = category
                r["content"] = content
                added.append(r)

        # 情绪词频次统计
        emotion_words = ["开心", "难过", "生气", "委屈", "焦虑", "失眠",
                         "压力", "心烦", "低落", "兴奋", "失望", "害怕"]
        emo_count: dict = {}

        for msg in messages:
            text = self._msg_text(msg)
            if not text:
                continue

            # 情绪词频次
            for w in emotion_words:
                if w in text:
                    emo_count[w] = emo_count.get(w, 0) + 1

            # --- 喜恶偏好（扩展关键词组；每条消息只取第一个命中锚点，避免刷屏） ---
            pref_anchors = (
                ("我喜欢", "喜欢"), ("我最爱", "最爱"),
                ("我讨厌", "讨厌"), ("我最烦", "最烦"),
                ("我不喜欢", "不喜欢"), ("我受不了", "受不了"),
                ("我想要", "想要"), ("我想吃", "想吃"),
                ("我不吃", "不吃"), ("我忌口", "忌口"),
            )
            for kw, label in pref_anchors:
                idx = text.find(kw)
                if idx >= 0:
                    snippet = text[idx:idx + 30].strip()
                    _emit_fact("preference", f"对方{label}：{snippet}", 0.6)
                    break

            # --- 结论性判断 ---
            for kw in ("我觉得", "我认为"):
                idx = text.find(kw)
                if idx >= 0:
                    snippet = text[idx:idx + 30].strip()
                    r = self.add_memory(
                        person_id=person_id,
                        memory_type="conclusion",
                        content=snippet,
                        category="personality",
                        source="ai_extract",
                        importance=0.6,
                    )
                    if r.get("added"):
                        r["memory_type"] = "conclusion"
                        r["category"] = "personality"
                        r["content"] = snippet
                        added.append(r)

            # --- 价值观（婚姻/金钱/事业/生育观，高重要性结论） ---
            value_kws = ("婚姻", "彩礼", "买房", "生孩子", "要孩子",
                         "不要孩子", "婚前", "婚后", "三观", "我对钱",
                         "男人就该", "女人就该", "事业优先", "全职太太",
                         "结婚必须", "我不想结婚", "我想结婚")
            if any(k in text for k in value_kws):
                snippet = text[:40].strip()
                r = self.add_memory(
                    person_id=person_id,
                    memory_type="conclusion",
                    content=f"价值观：{snippet}",
                    category="personality",
                    source="ai_extract",
                    importance=0.75,
                )
                if r.get("added"):
                    r["memory_type"] = "conclusion"
                    r["category"] = "personality"
                    r["content"] = f"价值观：{snippet}"
                    added.append(r)

            # --- 财务信息 ---
            finance_kws = ["工资", "收入", "月薪", "年薪", "存款", "房贷",
                           "车贷", "花了", "消费", "预算", "投资", "理财"]
            if any(k in text for k in finance_kws):
                snippet = text[:40].strip()
                _emit_fact("finance", f"财务相关：{snippet}", 0.7)

            # --- 重要事件（第一次约会/表白/吵架/分手/道歉…） ---
            event_kws = ("第一次", "表白", "分手", "吵架", "道歉",
                         "约会", "送花", "初吻")
            if any(k in text for k in event_kws):
                snippet = text[:40].strip()
                _emit_fact("relationship", f"重要事件：{snippet}", 0.8)

            # --- 关系里程碑（确定关系/见家长/周年/求婚/领证/结婚） ---
            milestone_kws = ("确定关系", "在一起", "见家长", "见父母",
                             "周年", "求婚", "订婚", "领证", "结婚")
            if any(k in text for k in milestone_kws):
                snippet = text[:40].strip()
                _emit_fact("relationship", f"关系里程碑：{snippet}", 0.9)

            # --- 承诺（对方答应过的事） ---
            promise_kws = ("我答应", "我保证", "我一定", "我承诺",
                           "我会改", "说好了", "拉钩")
            for kw in promise_kws:
                idx = text.find(kw)
                if idx >= 0:
                    snippet = text[idx:idx + 30].strip()
                    _emit_fact("relationship", f"承诺：{snippet}", 0.8)
                    break

            # --- 共同回忆（一起做过的事） ---
            shared_kws = ("我们一起", "上次我们", "那天我们", "我们那天",
                          "一起去", "一起吃", "一起看", "我们上次")
            if any(k in text for k in shared_kws):
                snippet = text[:40].strip()
                _emit_fact("relationship", f"共同回忆：{snippet}", 0.7)

            # --- 对方习惯（作息/行为模式） ---
            habit_kws = ("我每天", "我习惯", "我总是", "我一般",
                         "我通常", "我从不")
            for kw in habit_kws:
                idx = text.find(kw)
                if idx >= 0:
                    snippet = text[idx:idx + 30].strip()
                    _emit_fact("behavior", f"习惯：{snippet}", 0.65)
                    break

            # --- 敏感话题 / 雷区 ---
            sensitive_kws = ("别跟我提", "不要提", "别乱说", "雷区",
                             "禁忌", "不能说", "不能提", "别碰这个话题")
            if any(k in text for k in sensitive_kws):
                snippet = text[:40].strip()
                _emit_fact("relationship", f"敏感话题：{snippet}", 0.85)

            # --- 家庭背景 ---
            family_kws = ("我妈", "我爸", "我弟弟", "我妹妹", "我哥哥",
                          "我姐姐", "我奶奶", "我爷爷", "我父母",
                          "爸妈", "我家", "单亲")
            if any(k in text for k in family_kws):
                snippet = text[:40].strip()
                _emit_fact("other", f"家庭相关：{snippet}", 0.7)

        # --- 反复出现的情绪模式 ---
        for w, cnt in emo_count.items():
            if cnt >= 2:
                r = self.add_memory(
                    person_id=person_id,
                    memory_type="fact",
                    content=f"情绪模式：近期多次出现「{w}」（{cnt}次）",
                    category="emotion",
                    source="ai_extract",
                    importance=0.65,
                )
                if r.get("added"):
                    r["memory_type"] = "fact"
                    r["category"] = "emotion"
                    r["content"] = f"情绪模式：近期多次出现「{w}」（{cnt}次）"
                    added.append(r)

        logger.info("从聊天提取记忆 pid=%s 新增=%s", person_id, len(added))
        return added

    @staticmethod
    def _msg_text(msg: Any) -> str:
        """从消息元素中取出纯文本内容（兼容 dict 或 str）。"""
        if isinstance(msg, str):
            return msg.strip()
        if isinstance(msg, dict):
            for key in ("content", "text", "message", "msg"):
                val = msg.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return ""

    # ------------------------------------------------------------------ #
    # 上下文组装（供 LLM prompt 使用）
    # ------------------------------------------------------------------ #
    def get_memory_context(
        self,
        person_id: str,
        query: Optional[str] = None,
        top_k: int = 10,
    ) -> str:
        """构建记忆上下文字符串，供 LLM prompt 注入。

        Args:
            person_id: 绑定对象 ID。
            query: 可选；提供时先做语义/关键词搜索，否则按重要性取 top_k。
            top_k: 取记忆条数上限。

        Returns:
            str: 格式化的记忆上下文，按【事实】/【教训】/【结论】分组；
                 无记忆时返回空串。
        """
        if query:
            memories = self.search_memories(person_id, query, top_k=top_k)
        else:
            memories = self.get_memories(person_id, active_only=True)
            memories = memories[:top_k]

        if not memories:
            return ""

        groups: dict = {"fact": [], "lesson": [], "conclusion": []}
        for m in memories:
            mt = m.get("memory_type")
            if mt in groups:
                groups[mt].append(m.get("content") or "")

        label_map = {"fact": "【事实】", "lesson": "【教训】", "conclusion": "【结论】"}
        blocks: list = []
        for mt in ("fact", "lesson", "conclusion"):
            items = [c for c in groups[mt] if c]
            if items:
                blocks.append(label_map[mt] + "\n- " + "\n- ".join(items))
        return "\n\n".join(blocks)

    # ------------------------------------------------------------------ #
    # R3.3-07 SillyTavern 四层记忆
    #   summary(摘要) / vector(向量召回=既有fact/lesson/conclusion)
    #   world_info(人物/关系长期设定) / authors_note(给AI的风格/关系备忘)
    # ------------------------------------------------------------------ #
    def _insert_layer(self, person_id: str, layer: str, content: str,
                      category: str = "other", source: str = "system",
                      importance: float = 0.8) -> dict:
        """直接写入指定记忆层（绕过质量闸门）。

        world_info / authors_note / 摘要属于系统级长期设定，不应被
        check_quality 的「太短/套话/矛盾」闸门误删。
        """
        if not person_id or not content:
            return {"id": None, "added": False}
        if layer not in VALID_LAYERS:
            logger.warning("非法记忆层 layer=%s", layer)
            return {"id": None, "added": False}
        now = self._now()
        try:
            result = self.db.execute(
                f"INSERT INTO {self.memory_table} "
                f"(person_id, memory_type, category, content, source, importance, "
                f" access_count, created_at, updated_at, last_accessed, is_active, "
                f" status, layer) "
                f"VALUES (:pid, 'conclusion', :cat, :content, :source, :imp, "
                f"  0, :now, :now, :now, 1, 'active', :layer)",
                {
                    "pid": person_id, "cat": category or "other",
                    "content": content, "source": source,
                    "imp": float(importance), "now": now, "layer": layer,
                },
            )
            new_id = getattr(result, "lastrowid", None)
            return {"id": new_id, "added": True, "layer": layer}
        except Exception as e:  # noqa: BLE001
            logger.exception("_insert_layer 失败 layer=%s: %s", layer, e)
            return {"id": None, "added": False}

    # --- 摘要层 ---
    def add_summary(self, person_id: str, content: str,
                    importance: float = 0.5) -> dict:
        """写入摘要层：一段对话阶段性摘要/压缩。"""
        return self._insert_layer(person_id, "summary", content,
                                  category="summary", source="summary",
                                  importance=importance)

    def get_summaries(self, person_id: str, top_k: int = 10) -> list:
        """取摘要层记忆（按更新时间倒序）。"""
        return self.get_layer_memories(person_id, "summary")[:top_k]

    # ------------------------------------------------------------------ #
    # R3.3-34 记忆闸门：校准数据回灌前先过 gate
    # ------------------------------------------------------------------ #
    def guarded_write_calibration(
        self,
        person_id: str,
        raw_items: list[dict],
    ) -> dict:
        """校准数据回灌入口：先过 MemoryGate 去噪，再按 target_layer 写入。

        这是 calibration_runner / 任何外部回灌画像数据时应走的入口，
        而不是直接调 add_memory / _insert_layer。

        Args:
            person_id: 绑定对象 ID。
            raw_items: 原始条目列表，每条 dict 含
                text / source / confidence / timestamp / speaker。

        Returns:
            dict: {"accepted": int, "rejected": int,
                   "accepted_items": [...], "rejected_items": [...],
                   "written_ids": [...]}
        """
        # 延迟 import 避免循环依赖 / 启动时强绑定
        from services.memory_gate import MemoryGate  # noqa: WPS433

        gate = MemoryGate()
        accepted, rejected = gate.process_batch(raw_items or [])

        written_ids: list = []
        for acc in accepted:
            layer = acc.target_layer if acc.target_layer in VALID_LAYERS else "vector"
            res = self._insert_layer(
                person_id=person_id,
                layer=layer,
                content=acc.denoised_text,
                category="calibration",
                source="calibration_gate",
                importance=0.6 if acc.noisy else 0.75,
            )
            if res.get("id") is not None:
                written_ids.append(res["id"])

        return {
            "accepted": len(accepted),
            "rejected": len(rejected),
            "accepted_items": [a.to_dict() for a in accepted],
            "rejected_items": [r.to_dict() for r in rejected],
            "written_ids": written_ids,
        }

    # --- 按层取用 ---
    def get_layer_memories(self, person_id: str, layer: str,
                           active_only: bool = True) -> list:
        """按记忆层取用：summary / vector / world_info / authors_note。

        vector 层 = 既有全部 fact/lesson/conclusion（向后兼容原查询）。
        """
        if layer not in VALID_LAYERS:
            logger.warning("非法记忆层 layer=%s", layer)
            return []
        sql = (f"SELECT * FROM {self.memory_table} WHERE person_id=:pid "
               f"AND layer=:layer")
        params: dict = {"pid": person_id, "layer": layer}
        if active_only:
            sql += " AND is_active=1"
        sql += " ORDER BY importance DESC, updated_at DESC"
        try:
            rows = self.db.fetch_all(sql, params)
        except Exception as e:  # noqa: BLE001
            logger.exception("get_layer_memories 查询失败 layer=%s: %s", layer, e)
            return []
        return rows or []

    # --- world info：人物/关系长期设定（触发式背景） ---
    def set_world_info(self, person_id: str, key: str, content: str,
                       triggers: Optional[list] = None,
                       importance: float = 0.9) -> dict:
        """upsert 一条 world_info 长期设定（key 为设定名，category 复用存 key）。

        同一 person_id + key 已存在则更新 content，否则新建。
        """
        if not person_id or not key or not content:
            return {"updated": False, "added": False}
        note = content
        if triggers:
            note = f"{content}｜触发词:{'/'.join(str(t) for t in triggers)}"
        existing = self.db.fetch_one(
            f"SELECT id FROM {self.memory_table} "
            f"WHERE person_id=:pid AND layer='world_info' AND category=:key "
            f"AND is_active=1",
            {"pid": person_id, "key": key},
        )
        if existing:
            self.update_memory(existing["id"], content=note, importance=importance)
            return {"updated": True, "id": existing["id"]}
        r = self._insert_layer(person_id, "world_info", note, category=key,
                               source="world_info", importance=importance)
        return {"added": True, "id": r.get("id")}

    def get_world_info(self, person_id: str) -> list:
        """返回全部 world_info 长期设定：[{key, content, importance}]。"""
        rows = self.get_layer_memories(person_id, "world_info")
        out = []
        for r in rows:
            out.append({
                "key": r.get("category") or "other",
                "content": r.get("content") or "",
                "importance": r.get("importance"),
            })
        return out

    # --- author's note：给 AI 的风格/关系备忘 ---
    def set_authors_note(self, person_id: str, content: str) -> dict:
        """设置 author's note（每对象单条活跃备忘，重复设置覆盖更新）。"""
        if not person_id or not content:
            return {"updated": False, "added": False}
        existing = self.db.fetch_one(
            f"SELECT id FROM {self.memory_table} "
            f"WHERE person_id=:pid AND layer='authors_note' AND is_active=1 "
            f"ORDER BY updated_at DESC LIMIT 1",
            {"pid": person_id},
        )
        if existing:
            self.update_memory(existing["id"], content=content, importance=1.0)
            # 兜底：若历史上有多条活跃备忘，其余置为遗忘
            self.db.execute(
                f"UPDATE {self.memory_table} SET is_active=0 "
                f"WHERE person_id=:pid AND layer='authors_note' "
                f"AND id!=:mid AND is_active=1",
                {"pid": person_id, "mid": existing["id"]},
            )
            return {"updated": True, "id": existing["id"]}
        r = self._insert_layer(person_id, "authors_note", content,
                               category="authors_note", source="authors_note",
                               importance=1.0)
        return {"added": True, "id": r.get("id")}

    def get_authors_note(self, person_id: str) -> str:
        """返回当前 author's note 文本，无则空串。"""
        rows = self.get_layer_memories(person_id, "authors_note")
        return rows[0]["content"] if rows else ""

    # --- 四层组装（供 ST 式 prompt 注入） ---
    def get_st_memory_context(self, person_id: str, query: Optional[str] = None,
                              top_k: int = 10) -> str:
        """按 SillyTavern 四层组装 prompt 上下文。

        注入顺序（自上而下权重递减）：
          1. author's note（风格/关系备忘，最高优先）
          2. world info（人物/关系长期设定）
          3. 摘要层（近期对话摘要）
          4. 向量召回层（既有 fact/lesson/conclusion，按 query 召回）
        """
        blocks: list = []

        note = self.get_authors_note(person_id)
        if note:
            blocks.append("【作者备忘 / Author's Note】\n" + note)

        wi = self.get_world_info(person_id)
        if wi:
            wi_lines = [f"- {w['key']}：{w['content']}" for w in wi]
            blocks.append("【世界观设定 / World Info】\n" + "\n".join(wi_lines))

        summaries = self.get_summaries(person_id)
        if summaries:
            sm_lines = [f"- {m.get('content','')}" for m in summaries if m.get("content")]
            blocks.append("【近期摘要 / Summary】\n" + "\n".join(sm_lines))

        vec = self.get_memory_context(person_id, query=query, top_k=top_k)
        if vec:
            blocks.append("【记忆召回 / Vector Recall】\n" + vec)

        return "\n\n".join(blocks)

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #
    def get_memory_stats(self, person_id: Optional[str] = None) -> dict:
        """记忆统计：按类型 / 分类计数，平均重要性、总访问次数。

        Args:
            person_id: 可选；为 None 时统计全部对象。

        Returns:
            dict: {"by_type": {...}, "by_category": {...},
                   "total": N, "active": N, "forgotten": N,
                   "avg_importance": float, "total_access": int}。
        """
        result: dict = {
            "by_type": {},
            "by_category": {},
            "total": 0,
            "active": 0,
            "forgotten": 0,
            "avg_importance": 0.0,
            "total_access": 0,
        }
        where = "WHERE person_id=:pid" if person_id else ""
        params: dict = {"pid": person_id} if person_id else {}

        try:
            # 按类型
            for r in self.db.fetch_all(
                f"SELECT memory_type, COUNT(*) AS cnt FROM {self.memory_table} "
                f"{where} GROUP BY memory_type",
                params,
            ):
                result["by_type"][r.get("memory_type") or "unknown"] = int(r.get("cnt", 0))

            # 按分类
            for r in self.db.fetch_all(
                f"SELECT category, COUNT(*) AS cnt FROM {self.memory_table} "
                f"{where} GROUP BY category",
                params,
            ):
                result["by_category"][r.get("category") or "other"] = int(r.get("cnt", 0))

            # 总数 / 有效 / 遗忘
            row = self.db.fetch_one(
                f"SELECT COUNT(*) AS cnt, "
                f" SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) AS act, "
                f" SUM(CASE WHEN is_active=0 THEN 1 ELSE 0 END) AS forg, "
                f" AVG(importance) AS avg_imp, "
                f" COALESCE(SUM(access_count), 0) AS acc "
                f" FROM {self.memory_table} {where}",
                params,
            )
            if row:
                result["total"] = int(row.get("cnt") or 0)
                result["active"] = int(row.get("act") or 0)
                result["forgotten"] = int(row.get("forg") or 0)
                result["avg_importance"] = round(float(row.get("avg_imp") or 0), 3)
                result["total_access"] = int(row.get("acc") or 0)
        except Exception as e:  # noqa: BLE001
            logger.exception("get_memory_stats 失败: %s", e)
        return result

    # ------------------------------------------------------------------ #
    # 遗忘衰减
    # ------------------------------------------------------------------ #
    def apply_decay(self, person_id: Optional[str] = None) -> int:
        """记忆衰减：降低长期未访问记忆的重要性，模拟自然遗忘。

        规则：
        - 90 天未访问：importance *= 0.7；
        - 30 天未访问：importance *= 0.9；
        - 衰减后 importance < 0.1：置 is_active=0（彻底遗忘）。

        Args:
            person_id: 可选；为 None 时衰减全部对象。

        Returns:
            int: 发生衰减的记忆条数。
        """
        pid_clause = "AND person_id=:pid" if person_id else ""
        params: dict = {"pid": person_id} if person_id else {}
        affected = 0
        try:
            # 90 天以上：乘 0.7
            # R3.3-07：仅衰减向量召回层；world_info/authors_note/summary 为长期设定不衰减。
            r1 = self.db.execute(
                f"UPDATE {self.memory_table} SET "
                f" importance = importance * 0.7, "
                f" is_active = CASE WHEN importance * 0.7 < 0.1 THEN 0 ELSE is_active END, "
                f" updated_at = datetime('now') "
                f"WHERE is_active=1 AND COALESCE(layer,'vector')='vector' {pid_clause} "
                f"AND (julianday('now') - julianday(last_accessed)) >= 90",
                params,
            )
            affected += getattr(r1, "rowcount", 0) or 0

            # 30~90 天：乘 0.9
            r2 = self.db.execute(
                f"UPDATE {self.memory_table} SET "
                f" importance = importance * 0.9, "
                f" is_active = CASE WHEN importance * 0.9 < 0.1 THEN 0 ELSE is_active END, "
                f" updated_at = datetime('now') "
                f"WHERE is_active=1 AND COALESCE(layer,'vector')='vector' {pid_clause} "
                f"AND (julianday('now') - julianday(last_accessed)) >= 30 "
                f"AND (julianday('now') - julianday(last_accessed)) < 90",
                params,
            )
            affected += getattr(r2, "rowcount", 0) or 0
        except Exception as e:  # noqa: BLE001
            logger.exception("apply_decay 失败: %s", e)
        if affected:
            logger.info("记忆衰减完成 pid=%s 条数=%s", person_id or "global", affected)
        return affected

    # ------------------------------------------------------------------ #
    # 全局视图
    # ------------------------------------------------------------------ #
    def get_all_person_memories_global(self) -> dict:
        """获取所有对象的有效记忆（全局视图）。

        Returns:
            dict: {person_id: [记忆dict, ...]}；global 对象单独成组。
        """
        result: dict = {}
        try:
            rows = self.db.fetch_all(
                f"SELECT * FROM {self.memory_table} "
                f"WHERE is_active=1 ORDER BY person_id, importance DESC"
            )
            for r in rows:
                pid = r.get("person_id") or "global"
                result.setdefault(pid, []).append(r)
        except Exception as e:  # noqa: BLE001
            logger.exception("get_all_person_memories_global 失败: %s", e)
        return result


# ===== 借鉴 ex-skill：结构化关系记忆 =====
class StructuredRelationshipMemory:
    def __init__(self) -> None:
        self._shared_locations: list = []
        self._internal_jokes: list = []
        self._conflict_patterns: list = []
        self._timeline: list = []

    def add_shared_location(self, person_id, name, location_type="餐厅"):
        self._shared_locations.append({"pid": person_id, "name": name, "type": location_type})

    def add_internal_joke(self, person_id, content, origin=""):
        self._internal_jokes.append({"pid": person_id, "content": content, "origin": origin})

    def record_conflict_pattern(self, person_id, cause, resolution, who_first="", duration=0):
        self._conflict_patterns.append({"pid": person_id, "cause": cause, "resolution": resolution,
                                        "who_first": who_first, "duration": duration})

    def add_timeline_event(self, person_id, event_type, date, description=""):
        self._timeline.append({"pid": person_id, "type": event_type, "date": date, "desc": description})

    def get_structured_memory(self, person_id):
        f = lambda arr: [x for x in arr if x.get("pid") == person_id]
        return {"shared_locations": f(self._shared_locations),
                "internal_jokes": f(self._internal_jokes),
                "conflict_patterns": f(self._conflict_patterns),
                "timeline": f(self._timeline)}
