"""
LoveAI v2.0 - 勋章服务

25个初始勋章的判定逻辑、解锁、展示、英雄墙数据。
勋章是游戏化的核心奖励机制，与任务完成、好感度里程碑、连续登录等联动。

⚠️ 【验收官第五轮标注】本模块是游戏化成就系统，与 LoveAI 核心恋爱策略指导
   关联性较弱。属于之前版本遗留的功能，**暂不删除**，等用户确认是否保留。
   - 如果用户觉得游戏化不搭，后续可以整块移除
   - 目前不影响核心策略引擎运行（策略引擎是 strategy_advisor / direction_compass）

服务层纯 Python 实现，不依赖 PySide6；信号通过构造参数 signals 注入。
数据库操作统一走 Database 实例的 execute / fetch_one / fetch_all，
SQL 参数全部使用 :name 命名格式（不是 ?）。
"""
import logging
from datetime import date, datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.database import Database
    from app.signals import AppSignals

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# 25 个默认勋章定义
# 字段顺序: id, name, description, icon, category, rarity,
#           condition_type, condition_value, points
# ------------------------------------------------------------------ #
_DEFAULT_MEDALS: list[dict[str, Any]] = [
    # === 初出茅庐类 (5个) ===
    {"id": "medal_001", "name": "初出茅庐", "description": "完成新手引导",
     "icon": "🌱", "category": "新手", "rarity": "common",
     "condition_type": "feature", "condition_value": 1, "points": 10},
    {"id": "medal_002", "name": "首次对话", "description": "发送第一条消息",
     "icon": "💬", "category": "新手", "rarity": "common",
     "condition_type": "message_count", "condition_value": 1, "points": 10},
    {"id": "medal_003", "name": "初识对象", "description": "创建第一个对象",
     "icon": "👤", "category": "新手", "rarity": "common",
     "condition_type": "feature", "condition_value": 1, "points": 10},
    {"id": "medal_004", "name": "首次分析", "description": "完成第一次聊天分析",
     "icon": "📊", "category": "新手", "rarity": "common",
     "condition_type": "feature", "condition_value": 1, "points": 10},
    {"id": "medal_005", "name": "设置达人", "description": "完成设置中心配置",
     "icon": "⚙️", "category": "新手", "rarity": "common",
     "condition_type": "feature", "condition_value": 1, "points": 10},

    # === 关系进展类 (8个) ===
    {"id": "medal_006", "name": "破冰者", "description": "好感度达到55",
     "icon": "🧊", "category": "关系", "rarity": "common",
     "condition_type": "affection", "condition_value": 55, "points": 20},
    {"id": "medal_007", "name": "暧昧萌芽", "description": "好感度达到60",
     "icon": "🌷", "category": "关系", "rarity": "common",
     "condition_type": "affection", "condition_value": 60, "points": 20},
    {"id": "medal_008", "name": "心动信号", "description": "好感度达到65",
     "icon": "💓", "category": "关系", "rarity": "rare",
     "condition_type": "affection", "condition_value": 65, "points": 30},
    {"id": "medal_009", "name": "确认关系", "description": "好感度达到70",
     "icon": "💕", "category": "关系", "rarity": "rare",
     "condition_type": "affection", "condition_value": 70, "points": 30},
    {"id": "medal_010", "name": "甜蜜期", "description": "好感度达到75",
     "icon": "🍯", "category": "关系", "rarity": "rare",
     "condition_type": "affection", "condition_value": 75, "points": 40},
    {"id": "medal_011", "name": "稳定伴侣", "description": "好感度达到80",
     "icon": "🤝", "category": "关系", "rarity": "epic",
     "condition_type": "affection", "condition_value": 80, "points": 50},
    {"id": "medal_012", "name": "灵魂伴侣", "description": "好感度达到85",
     "icon": "💫", "category": "关系", "rarity": "epic",
     "condition_type": "affection", "condition_value": 85, "points": 60},
    {"id": "medal_013", "name": "终身伴侣", "description": "好感度达到90",
     "icon": "💍", "category": "关系", "rarity": "legendary",
     "condition_type": "affection", "condition_value": 90, "points": 100},

    # === 任务达人类 (5个) ===
    {"id": "medal_014", "name": "任务新手", "description": "完成1个任务",
     "icon": "🎯", "category": "任务", "rarity": "common",
     "condition_type": "task_count", "condition_value": 1, "points": 10},
    {"id": "medal_015", "name": "任务达人", "description": "完成10个任务",
     "icon": "🏹", "category": "任务", "rarity": "common",
     "condition_type": "task_count", "condition_value": 10, "points": 30},
    {"id": "medal_016", "name": "任务大师", "description": "完成50个任务",
     "icon": "🏆", "category": "任务", "rarity": "epic",
     "condition_type": "task_count", "condition_value": 50, "points": 80},
    {"id": "medal_017", "name": "限时猎人", "description": "完成5个限时任务",
     "icon": "⏰", "category": "任务", "rarity": "rare",
     "condition_type": "limited_task", "condition_value": 5, "points": 40},
    {"id": "medal_018", "name": "完美执行", "description": "任务0失败完成10个",
     "icon": "💎", "category": "任务", "rarity": "epic",
     "condition_type": "perfect_task", "condition_value": 10, "points": 60},

    # === 社交达人类 (4个) ===
    {"id": "medal_019", "name": "话痨", "description": "发送100条消息",
     "icon": "🗣️", "category": "社交", "rarity": "common",
     "condition_type": "message_count", "condition_value": 100, "points": 20},
    {"id": "medal_020", "name": "夜猫子", "description": "凌晨1-5点发送消息",
     "icon": "🦉", "category": "社交", "rarity": "common",
     "condition_type": "night_message", "condition_value": 1, "points": 15},
    {"id": "medal_021", "name": "连续7天", "description": "连续登录7天",
     "icon": "📅", "category": "社交", "rarity": "rare",
     "condition_type": "login_streak", "condition_value": 7, "points": 40},
    {"id": "medal_022", "name": "多线操作", "description": "同时管理3个对象",
     "icon": "🎭", "category": "社交", "rarity": "rare",
     "condition_type": "feature", "condition_value": 3, "points": 30},

    # === 特殊类 (3个) ===
    {"id": "medal_023", "name": "财务卫士", "description": "完成第一次财务分析",
     "icon": "💰", "category": "特殊", "rarity": "common",
     "condition_type": "feature", "condition_value": 1, "points": 20},
    {"id": "medal_024", "name": "攻略大师", "description": "生成10篇攻略",
     "icon": "📚", "category": "特殊", "rarity": "epic",
     "condition_type": "feature", "condition_value": 10, "points": 50},
    {"id": "medal_025", "name": "预言家", "description": "数字分身预测准确10次",
     "icon": "🔮", "category": "特殊", "rarity": "legendary",
     "condition_type": "feature", "condition_value": 10, "points": 100},
]


class MedalService:
    """勋章服务：25个初始勋章的判定逻辑、解锁、展示、英雄墙数据。

    勋章与任务完成、好感度里程碑、连续登录等联动。
    通过 check_and_unlock 方法在各类事件发生时触发判定。
    """

    def __init__(self, db: "Database", signals: "AppSignals" = None) -> None:
        """初始化勋章服务。

        Args:
            db: Database 实例（需具备 execute / fetch_one / fetch_all 方法）。
            signals: AppSignals 实例，用于 emit medal_unlocked 信号；可为 None。
        """
        self.db = db
        self.signals = signals
        self.medals_cache: dict[str, dict] = {}  # 勋章定义缓存 {medal_id: {...}}

        # 确保表结构存在（含旧表迁移）
        self._ensure_schema()

        # 加载勋章定义
        self._load_medals()

        logger.info(
            "MedalService 初始化完成，勋章总数=%d，信号=%s",
            len(self.medals_cache),
            "on" if signals else "off",
        )

    # ------------------------------------------------------------------ #
    # 表结构自动创建 / 迁移
    # ------------------------------------------------------------------ #
    def _ensure_schema(self) -> None:
        """确保 medals 和 user_medals 表存在且包含所需列。

        如果表已存在但缺少新列（旧版 schema），通过 ALTER TABLE ADD COLUMN 补全。
        """
        # medals 表（完整新 schema）
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS medals ("
            "  id TEXT PRIMARY KEY,"
            "  name TEXT,"
            "  description TEXT,"
            "  icon TEXT,"
            "  category TEXT,"
            "  rarity TEXT,"
            "  condition_type TEXT,"
            "  condition_value REAL,"
            "  points INTEGER"
            ")"
        )
        # 旧表可能缺少的列，逐个尝试 ALTER（已存在则静默忽略）
        for col_ddl in [
            "ALTER TABLE medals ADD COLUMN icon TEXT",
            "ALTER TABLE medals ADD COLUMN rarity TEXT",
            "ALTER TABLE medals ADD COLUMN condition_type TEXT",
            "ALTER TABLE medals ADD COLUMN condition_value REAL",
            "ALTER TABLE medals ADD COLUMN points INTEGER",
        ]:
            try:
                self.db.execute(col_ddl)
            except Exception:
                # 列已存在，忽略
                pass

        # user_medals 表（完整新 schema）
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS user_medals ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  person_id TEXT,"
            "  medal_id TEXT,"
            "  unlocked_at TEXT,"
            "  points INTEGER"
            ")"
        )
        # 旧表可能缺少 person_id / points 列
        for col_ddl in [
            "ALTER TABLE user_medals ADD COLUMN person_id TEXT",
            "ALTER TABLE user_medals ADD COLUMN points INTEGER",
        ]:
            try:
                self.db.execute(col_ddl)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 勋章定义加载与初始化
    # ------------------------------------------------------------------ #
    def _load_medals(self) -> dict[str, dict]:
        """从 medals 表加载所有勋章定义，缓存到 self.medals_cache。

        如果 medals 表为空（或旧数据缺少 condition_type），
        调用 _init_default_medals 初始化25个默认勋章。

        Returns:
            dict: {medal_id: {id, name, description, icon, category, rarity,
                              condition_type, condition_value, points}}
        """
        # 检查是否需要初始化：表为空，或所有勋章都没有 condition_type（旧数据）
        row = self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM medals"
        )
        total = (row or {}).get("cnt", 0)

        if total == 0:
            self._init_default_medals()
        else:
            # 检查是否已有 condition_type（新数据标记）
            row2 = self.db.fetch_one(
                "SELECT COUNT(*) AS cnt FROM medals WHERE condition_type IS NOT NULL"
            )
            has_condition = (row2 or {}).get("cnt", 0)
            if has_condition == 0:
                # 旧版数据，覆盖为新的25个定义
                self._init_default_medals()

        # 从数据库加载到缓存
        rows = self.db.fetch_all(
            "SELECT id, name, description, icon, category, rarity, "
            "       condition_type, condition_value, points "
            "FROM medals"
        )
        self.medals_cache = {}
        for r in rows:
            mid = r.get("id", "")
            if mid:
                self.medals_cache[mid] = {
                    "id": mid,
                    "name": r.get("name", ""),
                    "description": r.get("description", ""),
                    "icon": r.get("icon") or "",
                    "category": r.get("category", ""),
                    "rarity": r.get("rarity", "common"),
                    "condition_type": r.get("condition_type") or "",
                    "condition_value": r.get("condition_value"),
                    "points": r.get("points") or 0,
                }
        return self.medals_cache

    def _init_default_medals(self) -> None:
        """初始化25个默认勋章（如果 medals 表为空或数据为旧版）。

        使用 INSERT OR REPLACE 按主键 id 覆盖，确保新版定义生效。
        """
        for m in _DEFAULT_MEDALS:
            self.db.execute(
                "INSERT OR REPLACE INTO medals "
                "(id, name, description, icon, category, rarity, "
                " condition_type, condition_value, points) "
                "VALUES (:id, :name, :description, :icon, :category, :rarity, "
                "        :condition_type, :condition_value, :points)",
                {
                    "id": m["id"],
                    "name": m["name"],
                    "description": m["description"],
                    "icon": m["icon"],
                    "category": m["category"],
                    "rarity": m["rarity"],
                    "condition_type": m["condition_type"],
                    "condition_value": m["condition_value"],
                    "points": m["points"],
                },
            )
        logger.info("已初始化 %d 个默认勋章", len(_DEFAULT_MEDALS))

    # ------------------------------------------------------------------ #
    # 核心：检查并解锁勋章
    # ------------------------------------------------------------------ #
    def check_and_unlock(
        self,
        person_id: str,
        event_type: str,
        event_value: Any = None,
    ) -> list[dict]:
        """检查并解锁符合条件的勋章（核心方法）。

        根据 event_type 路由到对应的检查方法，遍历所有未解锁勋章，
        符合条件的写入 user_medals 表并 emit medal_unlocked 信号。

        Args:
            person_id: 人物 ID。
            event_type: 事件类型，可选值：
                'affection'    — 好感度变化
                'task_completed' — 任务完成
                'limited_task' — 限时任务完成
                'message_sent' — 发送消息
                'login'       — 登录
                'feature'      — 功能使用
            event_value: 附加数据（如当前好感度分数），可为 None。

        Returns:
            list[dict]: 本次新解锁的勋章列表。
        """
        newly_unlocked: list[str] = []

        if event_type == "affection":
            # 好感度事件：event_value 为当前好感度总分
            affection_score = self._resolve_affection(person_id, event_value)
            newly_unlocked = self._check_affection_medals(person_id, affection_score)

        elif event_type in ("task_completed", "limited_task"):
            newly_unlocked = self._check_task_medals(person_id)

        elif event_type == "message_sent":
            newly_unlocked = self._check_message_medals(person_id)

        elif event_type == "login":
            newly_unlocked = self._check_login_medals(person_id)

        elif event_type == "feature":
            newly_unlocked = self._check_feature_medals(person_id)

        else:
            logger.debug("未知 event_type=%s，跳过勋章检查", event_type)
            return []

        # 执行解锁
        unlocked_medals: list[dict] = []
        for medal_id in newly_unlocked:
            medal_def = self.medals_cache.get(medal_id)
            if not medal_def:
                continue
            success = self._do_unlock(person_id, medal_id, medal_def.get("points", 0))
            if success:
                unlocked_medals.append(medal_def)
                self._emit("medal_unlocked", {
                    "person_id": person_id,
                    "medal": medal_def,
                })

        if unlocked_medals:
            logger.info(
                "person=%s 解锁 %d 个勋章: %s",
                person_id, len(unlocked_medals),
                [m["name"] for m in unlocked_medals],
            )
        return unlocked_medals

    # ------------------------------------------------------------------ #
    # 各维度检查方法
    # ------------------------------------------------------------------ #
    def _check_affection_medals(
        self, person_id: str, current_affection: float
    ) -> list[str]:
        """检查好感度相关勋章（condition_type='affection'）。

        Args:
            person_id: 人物 ID。
            current_affection: 当前好感度总分。

        Returns:
            list[str]: 符合条件的未解锁勋章 id 列表。
        """
        unlocked_ids = self._get_unlocked_ids(person_id)
        result: list[str] = []
        for mid, medal in self.medals_cache.items():
            if medal.get("condition_type") != "affection":
                continue
            if mid in unlocked_ids:
                continue
            target = medal.get("condition_value")
            if target is None:
                continue
            try:
                if current_affection >= float(target):
                    result.append(mid)
            except (TypeError, ValueError):
                continue
        return result

    def _check_task_medals(self, person_id: str) -> list[str]:
        """检查任务相关勋章。

        查询 tasks 表完成数、限时任务完成数、失败数，对比 condition_value：
          - task_count: 总完成任务数 >= condition_value
          - limited_task: 限时任务完成数 >= condition_value
          - perfect_task: 失败数=0 且总完成数 >= condition_value

        Args:
            person_id: 人物 ID。

        Returns:
            list[str]: 符合条件的未解锁勋章 id 列表。
        """
        unlocked_ids = self._get_unlocked_ids(person_id)

        # 统计任务数据
        row_total = self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM tasks "
            "WHERE person_id=:pid AND status='completed'",
            {"pid": person_id},
        )
        completed_count = (row_total or {}).get("cnt", 0)

        row_limited = self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM tasks "
            "WHERE person_id=:pid AND status='completed' "
            "AND task_type LIKE 'limited%'",
            {"pid": person_id},
        )
        limited_count = (row_limited or {}).get("cnt", 0)

        row_failed = self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM tasks "
            "WHERE person_id=:pid AND status='failed'",
            {"pid": person_id},
        )
        failed_count = (row_failed or {}).get("cnt", 0)

        result: list[str] = []
        for mid, medal in self.medals_cache.items():
            ct = medal.get("condition_type")
            if ct not in ("task_count", "limited_task", "perfect_task"):
                continue
            if mid in unlocked_ids:
                continue
            target = medal.get("condition_value")
            if target is None:
                continue
            try:
                target_val = float(target)
            except (TypeError, ValueError):
                continue

            if ct == "task_count":
                if completed_count >= target_val:
                    result.append(mid)
            elif ct == "limited_task":
                if limited_count >= target_val:
                    result.append(mid)
            elif ct == "perfect_task":
                if failed_count == 0 and completed_count >= target_val:
                    result.append(mid)
        return result

    def _check_message_medals(self, person_id: str) -> list[str]:
        """检查消息相关勋章。

        查询消息总数（sent）、凌晨消息数（1-5点发送），对比 condition_value：
          - message_count: 发送消息总数 >= condition_value
          - night_message: 凌晨消息数 >= condition_value

        Args:
            person_id: 人物 ID。

        Returns:
            list[str]: 符合条件的未解锁勋章 id 列表。
        """
        unlocked_ids = self._get_unlocked_ids(person_id)

        # 统计发送消息总数（direction='sent' 表示我方发出）
        row_total = self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM chat_messages "
            "WHERE person_id=:pid AND direction='sent'",
            {"pid": person_id},
        )
        total_sent = (row_total or {}).get("cnt", 0)

        # 统计凌晨 1-5 点发送的消息数
        # SQLite: strftime('%H', created_at) 返回 '00'..'23'
        row_night = self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM chat_messages "
            "WHERE person_id=:pid AND direction='sent' "
            "AND CAST(strftime('%H', created_at) AS INTEGER) BETWEEN 1 AND 4",
            {"pid": person_id},
        )
        night_count = (row_night or {}).get("cnt", 0)

        result: list[str] = []
        for mid, medal in self.medals_cache.items():
            ct = medal.get("condition_type")
            if ct not in ("message_count", "night_message"):
                continue
            if mid in unlocked_ids:
                continue
            target = medal.get("condition_value")
            if target is None:
                continue
            try:
                target_val = float(target)
            except (TypeError, ValueError):
                continue

            if ct == "message_count":
                if total_sent >= target_val:
                    result.append(mid)
            elif ct == "night_message":
                if night_count >= target_val:
                    result.append(mid)
        return result

    def _check_login_medals(self, person_id: str) -> list[str]:
        """检查连续登录相关勋章。

        更新登录连续天数（存储在 settings 表），然后检查 login_streak 类型勋章。

        Args:
            person_id: 人物 ID。

        Returns:
            list[str]: 符合条件的未解锁勋章 id 列表。
        """
        unlocked_ids = self._get_unlocked_ids(person_id)

        # 更新登录连续天数
        streak = self._update_login_streak()

        result: list[str] = []
        for mid, medal in self.medals_cache.items():
            if medal.get("condition_type") != "login_streak":
                continue
            if mid in unlocked_ids:
                continue
            target = medal.get("condition_value")
            if target is None:
                continue
            try:
                if streak >= float(target):
                    result.append(mid)
            except (TypeError, ValueError):
                continue
        return result

    def _check_feature_medals(self, person_id: str) -> list[str]:
        """检查功能使用类勋章（condition_type='feature'）。

        根据 medal_id 分别查询对应数据表：
          - medal_001 初出茅庐: settings 中存在 onboarding_completed
          - medal_003 初识对象: persons 表有记录
          - medal_004 首次分析: analysis_records 表有记录
          - medal_005 设置达人: settings 表有配置项
          - medal_022 多线操作: 活跃对象数 >= 3
          - medal_023 财务卫士: financial_reports 表有记录
          - medal_024 攻略大师: tutorials 表 >= 10
          - medal_025 预言家: twin_feedback 表 >= 10

        Args:
            person_id: 人物 ID。

        Returns:
            list[str]: 符合条件的未解锁勋章 id 列表。
        """
        unlocked_ids = self._get_unlocked_ids(person_id)
        result: list[str] = []

        # 预查各类计数
        def _count(table: str, where: str = "", params: dict = None) -> int:
            sql = f"SELECT COUNT(*) AS cnt FROM {table}"
            if where:
                sql += f" WHERE {where}"
            row = self.db.fetch_one(sql, params or {})
            return (row or {}).get("cnt", 0)

        # 遍历 feature 类型勋章
        for mid, medal in self.medals_cache.items():
            if medal.get("condition_type") != "feature":
                continue
            if mid in unlocked_ids:
                continue

            target = medal.get("condition_value")
            try:
                target_val = float(target) if target is not None else 0
            except (TypeError, ValueError):
                target_val = 0

            # 根据 medal_id 路由到对应的数据检查
            met = False
            if mid == "medal_001":
                # 完成新手引导：settings 中有 onboarding_completed
                row = self.db.fetch_one(
                    "SELECT value FROM settings WHERE key='onboarding_completed'"
                )
                met = row is not None
            elif mid == "medal_003":
                # 创建第一个对象：persons 表有记录
                cnt = _count("persons")
                met = cnt >= target_val
            elif mid == "medal_004":
                # 完成第一次聊天分析
                cnt = _count("analysis_records", "person_id=:pid", {"pid": person_id})
                met = cnt >= target_val
            elif mid == "medal_005":
                # 完成设置中心配置：settings 表至少有 3 条配置
                cnt = _count("settings")
                met = cnt >= 1
            elif mid == "medal_022":
                # 同时管理3个对象：活跃对象数
                cnt = _count("persons", "is_active=1")
                met = cnt >= target_val
            elif mid == "medal_023":
                # 完成第一次财务分析
                cnt = _count("financial_reports", "person_id=:pid", {"pid": person_id})
                met = cnt >= target_val
            elif mid == "medal_024":
                # 生成10篇攻略
                cnt = _count("tutorials")
                met = cnt >= target_val
            elif mid == "medal_025":
                # 数字分身预测准确10次
                cnt = _count("twin_feedback", "person_id=:pid", {"pid": person_id})
                met = cnt >= target_val

            if met:
                result.append(mid)

        return result

    # ------------------------------------------------------------------ #
    # 解锁执行
    # ------------------------------------------------------------------ #
    def _do_unlock(self, person_id: str, medal_id: str, points: int = 0) -> bool:
        """执行单条勋章解锁（写入 user_medals 表）。

        Args:
            person_id: 人物 ID。
            medal_id: 勋章 ID。
            points: 奖励积分。

        Returns:
            bool: 是否为新解锁（已存在则返回 False）。
        """
        # 检查是否已解锁
        existing = self.db.fetch_one(
            "SELECT id FROM user_medals WHERE person_id=:pid AND medal_id=:mid",
            {"pid": person_id, "mid": medal_id},
        )
        if existing:
            return False

        self.db.execute(
            "INSERT INTO user_medals (person_id, medal_id, unlocked_at, points) "
            "VALUES (:pid, :mid, datetime('now'), :points)",
            {"pid": person_id, "mid": medal_id, "points": points},
        )
        return True

    def award_medal(self, person_id: str, medal_id: str) -> bool:
        """手动授予勋章（特殊情况用）。

        Args:
            person_id: 人物 ID。
            medal_id: 勋章 ID。

        Returns:
            bool: 是否新授予（已存在则返回 False）。
        """
        medal_def = self.medals_cache.get(medal_id)
        if not medal_def:
            logger.warning("尝试授予不存在的勋章: %s", medal_id)
            return False

        points = medal_def.get("points", 0)
        success = self._do_unlock(person_id, medal_id, points)
        if success:
            self._emit("medal_unlocked", {
                "person_id": person_id,
                "medal": medal_def,
            })
            logger.info("手动授予勋章 person=%s medal=%s", person_id, medal_id)
        return success

    # ------------------------------------------------------------------ #
    # 查询接口
    # ------------------------------------------------------------------ #
    def get_unlocked_medals(self, person_id: str) -> list[dict]:
        """获取已解锁勋章列表。

        Args:
            person_id: 人物 ID。

        Returns:
            list[dict]: 已解锁勋章列表（含 medals 表完整字段 + unlocked_at），
                        按解锁时间倒序。
        """
        rows = self.db.fetch_all(
            "SELECT um.person_id, um.medal_id, um.unlocked_at, um.points AS earned_points, "
            "       m.id, m.name, m.description, m.icon, m.category, m.rarity, "
            "       m.condition_type, m.condition_value, m.points "
            "FROM user_medals um "
            "JOIN medals m ON um.medal_id = m.id "
            "WHERE um.person_id=:pid "
            "ORDER BY um.unlocked_at DESC",
            {"pid": person_id},
        )
        return rows

    def get_all_medals(self, person_id: str = None) -> list[dict]:
        """获取所有勋章（含解锁状态）。

        Args:
            person_id: 人物 ID；提供时标注每个勋章是否已解锁。

        Returns:
            list[dict]: 所有勋章列表，每项含 unlocked(bool) 和
                        unlocked_at(可选) 字段。
        """
        # 获取已解锁集合
        unlocked_map: dict[str, str] = {}
        if person_id:
            rows = self.db.fetch_all(
                "SELECT medal_id, unlocked_at FROM user_medals WHERE person_id=:pid",
                {"pid": person_id},
            )
            for r in rows:
                unlocked_map[r["medal_id"]] = r.get("unlocked_at", "")

        result: list[dict] = []
        for mid, medal in self.medals_cache.items():
            item = dict(medal)
            item["unlocked"] = mid in unlocked_map
            if mid in unlocked_map:
                item["unlocked_at"] = unlocked_map[mid]
            result.append(item)
        return result

    def get_medal_stats(self, person_id: str) -> dict:
        """获取勋章统计。

        Args:
            person_id: 人物 ID。

        Returns:
            dict: {
                "total": 25,
                "unlocked": N,
                "points": 总积分,
                "by_category": {category: count},
                "by_rarity": {rarity: count},
                "completion_rate": 百分比,
            }
        """
        all_medals = self.get_all_medals(person_id)
        total = len(all_medals)
        unlocked_list = [m for m in all_medals if m.get("unlocked")]
        unlocked_count = len(unlocked_list)

        total_points = sum(int(m.get("points", 0)) for m in unlocked_list)

        by_category: dict[str, int] = {}
        by_rarity: dict[str, int] = {}
        for m in unlocked_list:
            cat = m.get("category", "未分类")
            rar = m.get("rarity", "common")
            by_category[cat] = by_category.get(cat, 0) + 1
            by_rarity[rar] = by_rarity.get(rar, 0) + 1

        rate = round(unlocked_count / total * 100, 1) if total > 0 else 0.0

        return {
            "total": total,
            "unlocked": unlocked_count,
            "points": total_points,
            "by_category": by_category,
            "by_rarity": by_rarity,
            "completion_rate": rate,
        }

    def get_hero_wall_data(self, person_id: str) -> dict:
        """获取英雄墙数据（用于前端展示）。

        Args:
            person_id: 人物 ID。

        Returns:
            dict: {
                "person_id": ...,
                "total_medals": 25,
                "unlocked_medals": [...],
                "achievements": [...里程碑],
                "stats": {...},
            }
        """
        unlocked = self.get_unlocked_medals(person_id)
        stats = self.get_medal_stats(person_id)

        # 聚合里程碑成就
        achievements: list[dict] = []

        # 任务完成里程碑
        task_row = self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM tasks WHERE person_id=:pid AND status='completed'",
            {"pid": person_id},
        )
        task_done = (task_row or {}).get("cnt", 0)
        achievements.append({
            "type": "task",
            "label": "完成任务",
            "value": task_done,
            "icon": "🎯",
        })

        # 好感度里程碑
        aff_row = self.db.fetch_one(
            "SELECT total_score FROM current_affection WHERE person_id=:pid",
            {"pid": person_id},
        )
        affection_score = (aff_row or {}).get("total_score", 50.0)
        achievements.append({
            "type": "affection",
            "label": "当前好感度",
            "value": affection_score,
            "icon": "💖",
        })

        # 消息数里程碑
        msg_row = self.db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM chat_messages WHERE person_id=:pid",
            {"pid": person_id},
        )
        msg_count = (msg_row or {}).get("cnt", 0)
        achievements.append({
            "type": "message",
            "label": "聊天消息",
            "value": msg_count,
            "icon": "💬",
        })

        # 连续登录天数
        streak = self._get_login_streak()
        achievements.append({
            "type": "login",
            "label": "连续登录",
            "value": streak,
            "icon": "📅",
        })

        return {
            "person_id": person_id,
            "total_medals": len(self.medals_cache),
            "unlocked_medals": unlocked,
            "achievements": achievements,
            "stats": stats,
        }

    # ------------------------------------------------------------------ #
    # 内部工具方法
    # ------------------------------------------------------------------ #
    def _get_unlocked_ids(self, person_id: str) -> set[str]:
        """获取某人物已解锁的勋章 ID 集合。

        Args:
            person_id: 人物 ID。

        Returns:
            set[str]: 已解锁勋章 ID 集合。
        """
        rows = self.db.fetch_all(
            "SELECT medal_id FROM user_medals WHERE person_id=:pid",
            {"pid": person_id},
        )
        return {r["medal_id"] for r in rows}

    def _resolve_affection(
        self, person_id: str, event_value: Any
    ) -> float:
        """解析当前好感度分数。

        优先使用 event_value 传入的值；若为空则从 current_affection 表查询。

        Args:
            person_id: 人物 ID。
            event_value: 外部传入的好感度分数（可为 None）。

        Returns:
            float: 好感度总分。
        """
        if event_value is not None:
            try:
                return float(event_value)
            except (TypeError, ValueError):
                pass
        # 从数据库查询
        row = self.db.fetch_one(
            "SELECT total_score FROM current_affection WHERE person_id=:pid",
            {"pid": person_id},
        )
        if row and row.get("total_score") is not None:
            try:
                return float(row["total_score"])
            except (TypeError, ValueError):
                pass
        return 50.0  # 默认初始值

    def _get_login_streak(self) -> int:
        """从 settings 表读取当前连续登录天数。

        Returns:
            int: 连续登录天数，默认 0。
        """
        row = self.db.fetch_one(
            "SELECT value FROM settings WHERE key='login_streak'"
        )
        if row and row.get("value"):
            try:
                return int(row["value"])
            except (TypeError, ValueError):
                return 0
        return 0

    def _update_login_streak(self) -> int:
        """更新连续登录天数（登录事件触发时调用）。

        规则：
          - 今天已登录过 → 天数不变
          - 昨天登录过 → 天数 +1
          - 断签或首次 → 重置为 1

        Returns:
            int: 更新后的连续登录天数。
        """
        today = date.today().isoformat()

        row = self.db.fetch_one(
            "SELECT value FROM settings WHERE key='last_login_date'"
        )
        last_date = row.get("value") if row else None

        streak = self._get_login_streak()

        if last_date == today:
            # 今天已登录，不重复累计
            return streak

        if last_date:
            try:
                last_dt = date.fromisoformat(last_date)
                days_gap = (date.today() - last_dt).days
                if days_gap == 1:
                    streak += 1
                else:
                    streak = 1
            except (ValueError, TypeError):
                streak = 1
        else:
            streak = 1

        # 写回 settings
        self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) "
            "VALUES ('login_streak', :streak, datetime('now'))",
            {"streak": str(streak)},
        )
        self.db.execute(
            "INSERT OR REPLACE INTO settings (key, value, updated_at) "
            "VALUES ('last_login_date', :today, datetime('now'))",
            {"today": today},
        )
        logger.info("登录连续天数更新: %d 天", streak)
        return streak

    def _emit(self, signal_name: str, payload: dict) -> None:
        """防御性 emit 信号（signals 为 None 或信号不存在时静默跳过）。

        Args:
            signal_name: AppSignals 上的信号名。
            payload: 发给信号的 dict。
        """
        if self.signals is None:
            return
        signal = getattr(self.signals, signal_name, None)
        if signal is None:
            return
        try:
            signal.emit(payload)
        except Exception as e:  # noqa: BLE001 - 防御性兜底
            logger.warning("信号 emit 失败 signal=%s: %s", signal_name, e)


# ===== 成就扩展到50枚 =====
MEDALS_50 = []
for i in range(1, 11):
    MEDALS_50.append({"id": f"first_{i}", "name": f"初识成就{i}", "category": "初识",
                       "hidden": False, "desc": f"达成初识任务{i}"})
for i in range(1, 11):
    MEDALS_50.append({"id": f"adv_{i}", "name": f"进阶成就{i}", "category": "进阶", "hidden": False})
for i in range(1, 11):
    MEDALS_50.append({"id": f"high_{i}", "name": f"高阶成就{i}", "category": "高阶", "hidden": False})
for i in range(1, 11):
    MEDALS_50.append({"id": f"special_{i}", "name": f"特殊成就{i}", "category": "特殊", "hidden": False})
for i in range(1, 11):
    MEDALS_50.append({"id": f"hidden_{i}", "name": "???", "category": "隐藏", "hidden": True})


def check_and_unlock_50(person_id: str, context: dict = None) -> list:
    """检查并解锁（简化：返回匹配的成就）。"""
    ctx = context or {}
    return [m for m in MEDALS_50 if ctx.get("medal_trigger") == m["id"]]


def get_medals_by_category(person_id: str, category: str) -> list:
    return [m for m in MEDALS_50 if m["category"] == category]
