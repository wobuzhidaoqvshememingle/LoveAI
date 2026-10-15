"""
LoveAI v2.0 - 数据库连接与初始化
SQLite + SQLAlchemy，单文件数据库，支持10万条聊天记录。
所有表在首次启动时自动创建。
"""
import os
import logging
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)

# 所有表的DDL，按依赖顺序排列
TABLE_DDLS = [
    # 人物档案
    """
    CREATE TABLE IF NOT EXISTS persons (
        id TEXT PRIMARY KEY,
        nickname TEXT,
        wechat_nickname TEXT,
        wxid TEXT,
        wxids TEXT DEFAULT '[]',
        aliases TEXT DEFAULT '[]',
        gender TEXT,
        birthday TEXT,
        occupation TEXT,
        relationship_stage INTEGER DEFAULT 0,
        soul_path TEXT,
        portrait_path TEXT,
        user_perspective_gender TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        is_active INTEGER DEFAULT 1
    )
    """,
    # 喜恶清单
    """
    CREATE TABLE IF NOT EXISTS person_preferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        category TEXT,
        content TEXT,
        is_like INTEGER DEFAULT 1,
        source TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 聊天记录
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        direction TEXT,
        content TEXT,
        msg_type TEXT DEFAULT 'text',
        emotion TEXT,
        emotion_score REAL,
        wechat_msg_id TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        is_withdrawn INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_chat_person_time ON chat_messages(person_id, created_at)",
    # 当前好感度
    """
    CREATE TABLE IF NOT EXISTS current_affection (
        person_id TEXT PRIMARY KEY,
        scores_json TEXT,
        total_score REAL DEFAULT 50.0,
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 好感度每日快照
    """
    CREATE TABLE IF NOT EXISTS affection_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        date TEXT,
        scores_json TEXT,
        total_score REAL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 好感度变化事件（每次变化都记录原因，做游戏化反馈的"变化历史"）
    """
    CREATE TABLE IF NOT EXISTS affection_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        changed_at TEXT DEFAULT (datetime('now')),
        dim TEXT,
        delta REAL,
        reason TEXT,
        total_before REAL,
        total_after REAL,
        stage_before INTEGER,
        stage_after INTEGER,
        event_type TEXT DEFAULT 'message',
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 好感度阶段跃迁记录（每次跨过区间边界时记一条，用于勋章/任务/UI高亮）
    """
    CREATE TABLE IF NOT EXISTS affection_stage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        stage_from INTEGER,
        stage_to INTEGER,
        score_at REAL,
        changed_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 任务
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        person_id TEXT,
        title TEXT,
        description TEXT,
        task_type TEXT,
        difficulty TEXT DEFAULT 'normal',
        estimated_time TEXT,
        reward_desc TEXT,
        affection_reward REAL DEFAULT 0,
        deadline TEXT,
        completion_condition TEXT,
        auto_keywords TEXT,
        guide_direction TEXT,
        steps_json TEXT,
        current_step INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT,
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 攻略/教程库（TOP二私教咨询的产物）
    """
    CREATE TABLE IF NOT EXISTS tutorials (
        id TEXT PRIMARY KEY,
        title TEXT,
        category TEXT,
        content_json TEXT,
        source TEXT DEFAULT 'ai',
        person_id TEXT,
        related_task_id TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # 财务分析报告
    """
    CREATE TABLE IF NOT EXISTS financial_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        income_estimate TEXT,
        disposable_income TEXT,
        consumption_level TEXT DEFAULT 'mid',
        large_purchase_capacity TEXT,
        stress_index INTEGER DEFAULT 50,
        suggested_amount_limit REAL DEFAULT 0,
        raw_signals TEXT,
        report_json TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 对方付出记录
    """
    CREATE TABLE IF NOT EXISTS giving_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        task_id TEXT,
        item TEXT,
        estimated_value REAL,
        context TEXT,
        affection_after REAL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 勋章定义
    """
    CREATE TABLE IF NOT EXISTS medals (
        id TEXT PRIMARY KEY,
        code TEXT UNIQUE,
        name TEXT,
        category TEXT,
        description TEXT,
        icon_path TEXT
    )
    """,
    # 用户获得的勋章
    """
    CREATE TABLE IF NOT EXISTS user_medals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medal_id TEXT,
        unlocked_at TEXT DEFAULT (datetime('now')),
        source_person_id TEXT,
        FOREIGN KEY (medal_id) REFERENCES medals(id)
    )
    """,
    # 长期记忆
    """
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        memory_type TEXT,
        content TEXT,
        source TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # 分析记录
    """
    CREATE TABLE IF NOT EXISTS analysis_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        input_content TEXT,
        result_json TEXT,
        model_used TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # 设置
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # 用户档案
    """
    CREATE TABLE IF NOT EXISTS user_profile (
        id INTEGER PRIMARY KEY DEFAULT 1,
        age INTEGER,
        gender TEXT,
        occupation TEXT,
        personality TEXT,
        strengths TEXT,
        pitfalls TEXT,
        relationship_history TEXT
    )
    """,
    # 关系事件
    """
    CREATE TABLE IF NOT EXISTS relationship_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        event_type TEXT,
        description TEXT,
        event_date TEXT,
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 分身预测反馈
    """
    CREATE TABLE IF NOT EXISTS twin_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        prediction TEXT,
        score TEXT,
        note TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (person_id) REFERENCES persons(id)
    )
    """,
    # 用户本人说话风格档案（本地学习，单行 id=1）
    """
    CREATE TABLE IF NOT EXISTS user_speech_style (
        id INTEGER PRIMARY KEY DEFAULT 1,
        profile_json TEXT,
        sample_count INTEGER DEFAULT 0,
        avg_length REAL DEFAULT 0,
        cloud_summary TEXT,
        source TEXT DEFAULT 'local',
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    # 分身预测记录（复盘回流：每轮预测持久化，下一轮拿到对方实际反应后结算命中）
    """
    CREATE TABLE IF NOT EXISTS prediction_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id TEXT,
        turn_ref TEXT,
        trigger_input TEXT,
        predicted_json TEXT,
        actual_json TEXT,
        status TEXT DEFAULT 'pending',
        emotion_hit INTEGER,
        direction_hit INTEGER,
        action_hit INTEGER,
        validity_score REAL,
        created_at TEXT DEFAULT (datetime('now')),
        settled_at TEXT
    )
    """,
]

# 25个初始勋章
INITIAL_MEDALS = [
    ('medal_001', 'first_contact', '初出茅庐', '新手', '完成新手引导', None),
    ('medal_002', 'first_chat', '破冰者', '沟通', '完成第一次聊天分析', None),
    ('medal_003', 'first_reply', '神回复', '沟通', '第一次使用回复建议填入微信', None),
    ('medal_004', 'chat_master', '话痨', '沟通', '累计分析100条消息', None),
    ('medal_005', 'first_date', '约会达人', '关系', '完成第一次约会任务', None),
    ('medal_006', 'stage_up', '进阶者', '关系', '关系阶段升级', None),
    ('medal_007', 'affection_70', '心动信号', '关系', '好感度总分达到70', None),
    ('medal_008', 'affection_90', '灵魂伴侣', '关系', '好感度总分达到90', None),
    ('medal_009', 'task_10', '任务达人', '任务', '完成10个任务', None),
    ('medal_010', 'task_50', '攻略大师', '任务', '完成50个任务', None),
    ('medal_011', 'limited_task', '限时猎手', '任务', '完成一个限时任务', None),
    ('medal_012', 'daily_streak_7', '七日坚持', '任务', '连续7天完成日常任务', None),
    ('medal_013', 'first_twin', '分身初体验', '成长', '第一次使用数字分身预测', None),
    ('medal_014', 'twin_accurate', '读心者', '成长', '数字分身预测准确率达到70%', None),
    ('medal_015', 'self_growth', '自我提升', '成长', '完成一个自我成长咨询', None),
    ('medal_016', 'first_medal', '收藏家', '收藏', '收藏第一条话术', None),
    ('medal_017', 'collector_10', '话术库', '收藏', '收藏10条话术', None),
    ('medal_018', 'data_analyst', '数据控', '数据', '查看数据统计10次', None),
    ('medal_019', 'night_owl', '夜猫子', '特殊', '凌晨1-5点使用', None),
    ('medal_020', 'early_bird', '早起鸟', '特殊', '早上5-7点使用', None),
    ('medal_021', 'multi_person', '广结善缘', '特殊', '创建3个以上对象', None),
    ('medal_022', 'offline_mode', '离线战士', '特殊', '使用离线模式完成分析', None),
    ('medal_023', 'voice_first', '语音达人', '特殊', '第一次使用语音输入', None),
    ('medal_024', 'crisis_saver', '救火队员', '特殊', '完成一个危机修复任务', None),
    ('medal_025', 'year_anniversary', '周年纪念', '特殊', '使用满一年', None),
]


class Database:
    """数据库管理器"""

    def __init__(self, db_path: str = "data/loveai.db"):
        self.db_path = db_path
        self._ensure_dir()
        self.engine = create_engine(f"sqlite:///{db_path}", echo=False)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self._initialized = False
        self._install_sqlite_pragmas()

    def _install_sqlite_pragmas(self):
        """为每条新连接设置 SQLite PRAGMA。

        - journal_mode=WAL：WAL 模式持久生效在数据库文件上，写入不再独占锁，
          单条 INSERT 提交从 ~65ms（rollback journal 每次 fsync）降到几 ms；
        - synchronous=NORMAL：WAL 下安全的同步级别，崩溃不会损坏数据库，
          但不必每次 commit 都 fsync，显著提升写入吞吐。
        """
        @event.listens_for(self.engine, "connect")
        def _set_pragmas(dbapi_conn, _connection_record):
            try:
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
            except Exception as e:
                logger.warning("设置 SQLite PRAGMA 失败: %s", e)

    def _ensure_dir(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    def init_tables(self):
        """创建所有表和初始数据"""
        with self.engine.connect() as conn:
            for ddl in TABLE_DDLS:
                conn.execute(text(ddl))
            conn.commit()

            # 迁移：给已存在的 persons 表加新列（兼容旧库）
            columns = [row[1] for row in conn.execute(text("PRAGMA table_info(persons)")).fetchall()]
            if "wxid" not in columns:
                conn.execute(text("ALTER TABLE persons ADD COLUMN wxid TEXT"))
            if "wxids" not in columns:
                conn.execute(text("ALTER TABLE persons ADD COLUMN wxids TEXT DEFAULT '[]'"))
            if "aliases" not in columns:
                conn.execute(text("ALTER TABLE persons ADD COLUMN aliases TEXT DEFAULT '[]'"))

            # R3.3-78 双轨情感指标：给 tasks 表加路线标签与忠诚奖励列（兼容旧库）。
            # route: counter/guide，NULL 视为引导线旧行为；loyalty_reward 默认 0。
            tcols = [row[1] for row in conn.execute(text("PRAGMA table_info(tasks)")).fetchall()]
            if "route" not in tcols:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN route TEXT"))
            if "loyalty_reward" not in tcols:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN loyalty_reward REAL DEFAULT 0"))

            # 撤回消息标记（P1）：chat_messages.is_withdrawn。
            # 1=该条为微信系统撤回通知（<sysmsg type="revokemsg">），不参与情感分析，
            # 仅作为"撤回事件"计入 withdrawn_count。旧库缺列时幂等补列，默认 0。
            mcols = [row[1] for row in conn.execute(text("PRAGMA table_info(chat_messages)")).fetchall()]
            if "is_withdrawn" not in mcols:
                conn.execute(text("ALTER TABLE chat_messages ADD COLUMN is_withdrawn INTEGER NOT NULL DEFAULT 0"))
            conn.commit()

            # 插入初始勋章（如果不存在）
            for medal in INITIAL_MEDALS:
                conn.execute(
                    text("INSERT OR IGNORE INTO medals (id, code, name, category, description, icon_path) "
                         "VALUES (:id, :code, :name, :category, :description, :icon_path)"),
                    {"id": medal[0], "code": medal[1], "name": medal[2],
                     "category": medal[3], "description": medal[4], "icon_path": medal[5]}
                )
            conn.commit()
        self._initialized = True
        logger.info(f"数据库初始化完成: {self.db_path}")

    def get_session(self) -> Session:
        """获取数据库会话"""
        if not self._initialized:
            self.init_tables()
        return self.SessionLocal()

    def _ensure_init(self) -> None:
        """懒触发建表/迁移（幂等）。

        病灶③修复：历史上迁移只挂在 get_session() 闸门后；而 TaskEngine 全部走
        execute/fetch_all/fetch_one 直连 engine，绕过了该闸门，导致真实库 tasks 表
        缺 route/loyalty_reward 列、带列 INSERT 报 "no column named route"。
        这里在每条直连入口与 get_database() 单例创建后自愈合触发；init_tables 本身
        幂等（CREATE IF NOT EXISTS + ALTER 前查列 + INSERT OR IGNORE），可安全重复。
        """
        if not self._initialized:
            self.init_tables()

    def execute(self, sql: str, params: dict = None):
        """执行SQL并返回结果（便捷方法）"""
        self._ensure_init()
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            conn.commit()
            return result

    def fetch_all(self, sql: str, params: dict = None) -> list:
        """查询所有行，返回字典列表"""
        self._ensure_init()
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            columns = result.keys()
            return [dict(zip(columns, row)) for row in result.fetchall()]

    def fetch_one(self, sql: str, params: dict = None) -> dict | None:
        """查询单行，返回字典或None"""
        self._ensure_init()
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            columns = result.keys()
            row = result.fetchone()
            return dict(zip(columns, row)) if row else None

    def find_person_by_wechat(self, nickname: str = "", wxid: str = "") -> dict | None:
        """通过微信昵称或wxid查找人物，支持别名和多微信号匹配。

        匹配优先级：wxid > wxids数组（双微信）> wechat_nickname > aliases（历史昵称）
        """
        if not nickname and not wxid:
            return None
        # 1. wxid 精确匹配
        if wxid:
            person = self.fetch_one("SELECT * FROM persons WHERE wxid=:wxid", {"wxid": wxid})
            if person:
                return person
            # 2. wxids 数组匹配（双微信场景：一个人两个微信号）
            all_persons = self.fetch_all("SELECT * FROM persons WHERE wxids IS NOT NULL AND wxids != '[]'")
            import json
            for p in all_persons:
                try:
                    wxids = json.loads(p.get("wxids", "[]"))
                    if wxid in wxids:
                        return p
                except Exception:
                    continue
        # 3. wechat_nickname 精确匹配
        if nickname:
            person = self.fetch_one(
                "SELECT * FROM persons WHERE wechat_nickname=:name", {"name": nickname}
            )
            if person:
                return person
        # 4. aliases 模糊匹配（JSON数组中包含该昵称）
        if nickname:
            all_persons = self.fetch_all("SELECT * FROM persons WHERE aliases IS NOT NULL AND aliases != '[]'")
            import json
            for p in all_persons:
                try:
                    aliases = json.loads(p.get("aliases", "[]"))
                    if nickname in aliases:
                        return p
                except Exception:
                    continue
        return None

    def add_person_wxid(self, person_id: str, wxid: str) -> bool:
        """给人物添加微信号（支持双微信场景，一个人多个微信号）。"""
        if not wxid:
            return False
        person = self.fetch_one("SELECT wxid, wxids FROM persons WHERE id=:id", {"id": person_id})
        if not person:
            return False
        import json
        try:
            wxids = json.loads(person.get("wxids", "[]") or "[]")
        except Exception:
            wxids = []
        # 如果主 wxid 为空，设为主 wxid
        if not person.get("wxid"):
            self.execute("UPDATE persons SET wxid=:wxid WHERE id=:id", {"wxid": wxid, "id": person_id})
            return True
        # 否则加入 wxids 数组
        if wxid not in wxids and wxid != person.get("wxid"):
            wxids.append(wxid)
            self.execute(
                "UPDATE persons SET wxids=:wxids, updated_at=datetime('now') WHERE id=:id",
                {"wxids": json.dumps(wxids, ensure_ascii=False), "id": person_id}
            )
            return True
        return False

    def add_person_alias(self, person_id: str, alias: str) -> bool:
        """给人物添加历史昵称别名（对方改昵称时记录）。"""
        if not alias:
            return False
        person = self.fetch_one("SELECT aliases FROM persons WHERE id=:id", {"id": person_id})
        if not person:
            return False
        import json
        try:
            aliases = json.loads(person.get("aliases", "[]") or "[]")
        except Exception:
            aliases = []
        if alias not in aliases and alias != person.get("wechat_nickname"):
            aliases.append(alias)
            self.execute(
                "UPDATE persons SET aliases=:aliases, updated_at=datetime('now') WHERE id=:id",
                {"aliases": json.dumps(aliases, ensure_ascii=False), "id": person_id}
            )
            return True
        return False


_db_instance = None


def get_database(db_path: str = None) -> Database:
    """获取数据库单例"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path or "data/loveai.db")
        # 病灶③：单例创建即迁移（不经 get_session 也能拿到含 route/loyalty_reward 列的库）。
        _db_instance._ensure_init()
    return _db_instance


# ===== dexter 借鉴：5 张新表（try/except 隔离，不影响原有表） =====
def init_dexter_tables(conn) -> None:
    """创建 knowledge_base / knowledge_graph / user_phrase / training_log / media_analysis。"""
    try:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT, category TEXT, title TEXT, content TEXT,
            source TEXT, confidence REAL, created_at TEXT, updated_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS knowledge_graph (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_a_id INTEGER, knowledge_b_id INTEGER,
            relation_type TEXT, weight REAL, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS user_phrase (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT, scene TEXT, usage_count INTEGER,
            success_rate REAL, created_at TEXT, updated_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS training_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene TEXT, user_reply TEXT, system_suggestion TEXT,
            result TEXT, feedback TEXT, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS media_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type TEXT, file_path TEXT, analysis_result TEXT,
            person_id TEXT, created_at TEXT)""")
        conn.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("init_dexter_tables失败: %s", e)
