# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 用户能力评估系统（个性化任务难度）

设计目标：
  1. 不再给所有用户同一套任务——新手拿到"直接表白"会直接崩，
     熟练用户拿到"发个问候"会觉得幼稚；
  2. 6 维能力画像（社交/表达/执行/时间/经济/焦虑）动态学习，
     从默认中等起步，随任务完成率和聊天内容自然更新；
  3. 情感阶梯表 L1-L5 按压力分级，用户从"扛得住"的级别开始；
  4. 3 层任务分级（basic/intermediate/advanced），80% 掌握度才进阶，
     用户也可手动设定级别，系统不强迫越级；
  5. 服务层纯 Python，不依赖 PySide6；单模块失败不影响主流程。

数据库表（幂等创建）：
  user_ability(user_id TEXT PRIMARY KEY, profile_json TEXT, updated_at TEXT)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from models.database import Database

logger = logging.getLogger(__name__)


# ====================================================================== #
# 常量：6 维能力 / 默认画像 / 情感阶梯 / 任务分级
# ====================================================================== #
ABILITY_DIMS: tuple[str, ...] = (
    "social_skill",       # 社交技能 1-5（1=新手，5=熟练）
    "emotional_expression",  # 情感表达 1-5（1=多喝热水，5=自然表达感受）
    "executive_function",    # 执行功能 1-5（1=经常拖延，5=按时完成）
    "time_energy",        # 时间精力 1-5（1=<30min/天，5=>2h/天）
    "economic_status",    # 经济状况 1-5（1=紧张，5=宽裕）
    "anxiety_level",      # 焦虑程度 1-5（1=低焦虑，5=高焦虑）
)

DIM_LABELS: dict[str, str] = {
    "social_skill": "社交技能",
    "emotional_expression": "情感表达",
    "executive_function": "执行功能",
    "time_energy": "时间精力",
    "economic_status": "经济状况",
    "anxiety_level": "焦虑程度",
}

# 默认画像：中等水平，不偏不倚
DEFAULT_PROFILE: dict[str, int] = {d: 3 for d in ABILITY_DIMS}

# 用户可手动设定的任务级别
VALID_LEVELS = ("basic", "intermediate", "advanced")
LEVEL_LABELS: dict[str, str] = {
    "basic": "基础层（社交新手，8-12分钟微任务+话术模板）",
    "intermediate": "进阶层（有经验，主动沟通+方向提示）",
    "advanced": "高阶层（熟练，复杂场景+开放发挥）",
}

# 情感阶梯表 L1-L5（按压力程度分级）
EMOTIONAL_LADDER: list[dict[str, Any]] = [
    {
        "level": "L1",
        "name": "微压力",
        "pressure": 1,
        "examples": [
            "接受15分钟咖啡邀约，提前说好到点就走",
            "发一条不带压力的问候",
        ],
    },
    {
        "level": "L2",
        "name": "低压力",
        "pressure": 2,
        "examples": [
            "在安全环境做3分钟限时深度倾诉，到点可以停",
            "观察对方情绪并记录",
        ],
    },
    {
        "level": "L3",
        "name": "中压力",
        "pressure": 3,
        "examples": [
            "主动发起一次话题并维持5分钟",
            "表达一次真实感受",
        ],
    },
    {
        "level": "L4",
        "name": "较高压力",
        "pressure": 4,
        "examples": [
            "表达一次不同意见",
            "邀请一次简短约会",
        ],
    },
    {
        "level": "L5",
        "name": "高压力",
        "pressure": 5,
        "examples": [
            "处理一次冲突",
            "进行一次完整深度对话",
            "直接表白",
        ],
    },
]

# 高消费任务关键词（经济紧张用户需要过滤/替换）
HIGH_COST_KEYWORDS = (
    "买礼物", "高档餐厅", "奢侈品", "五星级", "出国旅行", "异地",
    "送花", "转账", "红包", "大餐", "高端酒店", "spa", "奢侈品店",
)

# 高压力任务关键词（新手/高焦虑用户需要降级）
HIGH_PRESSURE_KEYWORDS = (
    "表白", "直接说", "深度对话", "谈关系定位", "见家长",
    "处理冲突", "摊牌", "挑明", "正式确定", "分手", "复合",
)

# 话术模板库（social_skill<=2 时附加到任务）
SCRIPT_TEMPLATES: dict[str, list[str]] = {
    "greeting": [
        "你可以这样说：'今天路过一家店，突然想起你上次说过喜欢XX，就顺手拍了一张'",
        "你可以这样说：'在干嘛？今天这边天气挺好的，你那边呢'",
    ],
    "invite": [
        "你可以这样说：'周六下午我正好在你公司附近，要不要一起喝杯咖啡？就15分钟，到点你就去忙'",
        "你可以这样说：'发现一家新开的咖啡馆，听说不错，有空一起试试？'",
    ],
    "feeling": [
        "你可以这样说：'刚才那件事，我其实有点XX（开心/难过/在意）'",
        "你可以这样说：'跟你聊天的时候，我觉得挺放松的'",
    ],
    "boundary": [
        "你可以这样说：'这件事我有点不舒服，我们能不能换个方式'",
        "你可以这样说：'我理解你的意思，但我的感受是XX'",
    ],
    "conflict": [
        "你可以这样说：'我现在有点上头，我们10分钟后再聊好不好'",
        "你可以这样说：'我不是要跟你吵，我是想把这件事说清楚'",
    ],
    "default": [
        "你可以这样说：先从一句轻松的话开始，比如'在干嘛？'，等对方回了再往下接",
    ],
}


# ====================================================================== #
# 主类
# ====================================================================== #
class UserAbility:
    """用户能力画像：6 维评估 + 情感阶梯 + 任务难度调整。

    典型用法：
        ua = UserAbility(db)
        profile = ua.get_profile(user_id)
        adjusted = ua.adjust_task_difficulty(user_id, {"title": "约对方喝咖啡", ...})
        ua.update_from_task_completion(user_id, {"task": "...", "success": True})
    """

    def __init__(self, db: Optional["Database"] = None) -> None:
        self.db = db
        self._ensure_table()
        logger.info("UserAbility 初始化完成 db=%s", "on" if db else "off")

    # ------------------------------------------------------------------ #
    # 表初始化（幂等）
    # ------------------------------------------------------------------ #
    def _ensure_table(self) -> None:
        if self.db is None:
            return
        try:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS user_ability ("
                " user_id TEXT PRIMARY KEY,"
                " profile_json TEXT NOT NULL,"
                " level TEXT DEFAULT 'basic',"
                " updated_at TEXT NOT NULL"
                ")"
            )
        except Exception as e:  # noqa: BLE001 - 表已存在/无 db 时静默
            logger.debug("user_ability 表初始化失败: %s", e)

    # ------------------------------------------------------------------ #
    # 读取 / 写入
    # ------------------------------------------------------------------ #
    def get_profile(self, user_id: str) -> dict:
        """获取用户能力画像；不存在则返回默认中等画像。

        Returns:
            dict: {social_skill, emotional_expression, executive_function,
                   time_energy, economic_status, anxiety_level,
                   level, updated_at}
        """
        base = dict(DEFAULT_PROFILE)
        base["level"] = "basic"
        base["updated_at"] = ""
        if self.db is None or not user_id:
            return base
        try:
            row = self.db.fetch_one(
                "SELECT profile_json, level, updated_at FROM user_ability WHERE user_id=:uid",
                {"uid": user_id},
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("读取 user_ability 失败 uid=%s: %s", user_id, e)
            return base
        if not row:
            return base
        try:
            data = json.loads(row["profile_json"]) if row.get("profile_json") else {}
        except (json.JSONDecodeError, TypeError):
            data = {}
        for k in ABILITY_DIMS:
            if k in data:
                try:
                    base[k] = max(1, min(5, int(data[k])))
                except (TypeError, ValueError):
                    pass
        base["level"] = row.get("level") or "basic"
        base["updated_at"] = row.get("updated_at") or ""
        return base

    def _save_profile(self, user_id: str, profile: dict) -> None:
        if self.db is None or not user_id:
            return
        dims = {k: profile.get(k, 3) for k in ABILITY_DIMS}
        level = profile.get("level", "basic")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            self.db.execute(
                "INSERT INTO user_ability (user_id, profile_json, level, updated_at) "
                "VALUES (:uid, :pj, :lv, :ts) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                " profile_json=excluded.profile_json, "
                " level=excluded.level, "
                " updated_at=excluded.updated_at",
                {
                    "uid": user_id,
                    "pj": json.dumps(dims, ensure_ascii=False),
                    "lv": level,
                    "ts": now,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("写入 user_ability 失败 uid=%s: %s", user_id, e)

    # ------------------------------------------------------------------ #
    # 从任务完成情况更新能力
    # ------------------------------------------------------------------ #
    def update_from_task_completion(self, user_id: str, task_result: dict) -> dict:
        """根据任务完成情况更新能力评估。

        Args:
            user_id: 用户 ID。
            task_result: {
                "title": 任务标题,
                "success": True/False,
                "task_type": "main"/"limited:xxx"/...,
                "stress_level": 1-5 (可选，对应情感阶梯),
                "used_script": True/False (是否借助话术模板),
            }

        Returns:
            dict: 更新后的完整画像。
        """
        profile = self.get_profile(user_id)
        success = bool(task_result.get("success", True))
        stress = task_result.get("stress_level")
        used_script = bool(task_result.get("used_script", False))
        title = str(task_result.get("title", ""))

        # social_skill：完成一次带压力的互动任务 → +；经常失败 → -
        if success:
            if stress and int(stress) >= 3:
                profile["social_skill"] = self._bump(profile["social_skill"], +1)
                if not used_script:
                    # 不借助模板完成高压力任务，表达能力也涨
                    profile["emotional_expression"] = self._bump(
                        profile["emotional_expression"], +1)
            elif "问候" in title or "聊天" in title:
                profile["social_skill"] = self._bump(profile["social_skill"], 0)  # 微任务不涨
            # executive_function：按时完成 +
            profile["executive_function"] = self._bump(
                profile["executive_function"], +1 if success else 0)
        else:
            profile["social_skill"] = self._bump(profile["social_skill"], -1)
            profile["executive_function"] = self._bump(
                profile["executive_function"], -1)

        self._save_profile(user_id, profile)
        logger.info(
            "UserAbility 更新 uid=%s success=%s social=%d exec=%d level=%s",
            user_id, success, profile["social_skill"],
            profile["executive_function"], profile.get("level"),
        )
        return profile

    # ------------------------------------------------------------------ #
    # 从聊天记录推断能力水平（轻量启发式）
    # ------------------------------------------------------------------ #
    def infer_from_chat(self, user_id: str, messages: list) -> dict:
        """从聊天记录推断能力水平（表达能力/社交技巧等）。

        启发式规则（不调 LLM，纯关键词+长度统计）：
          - 消息平均长度 > 30字 且含情绪词 → emotional_expression +1
          - 消息平均长度 < 10字 且高频"嗯/哦/好" → emotional_expression -1
          - 主动发起话题次数 > 被动回复 → social_skill +1
          - 出现"焦虑/怕/担心/睡不着" → anxiety_level +1
          - 出现"加班/忙/累/没时间" → time_energy -1

        Args:
            messages: list[str] 或 list[{role, content}]。

        Returns:
            dict: 更新后的完整画像。
        """
        profile = self.get_profile(user_id)
        if not messages:
            return profile

        # 归一化消息文本
        texts: list[str] = []
        for m in messages:
            if isinstance(m, str):
                texts.append(m)
            elif isinstance(m, dict):
                c = m.get("content") or m.get("text") or ""
                if c:
                    texts.append(str(c))
        if not texts:
            return profile

        n = len(texts)
        avg_len = sum(len(t) for t in texts) / max(1, n)

        # 情绪词
        emotion_words = ("开心", "难过", "委屈", "生气", "害怕", "担心", "在意", "感动", "委屈")
        emo_hits = sum(1 for t in texts if any(w in t for w in emotion_words))

        # 敷衍词
        perfunctory = sum(1 for t in texts if t.strip() in ("嗯", "哦", "好", "哈哈", "。", "..."))

        # 焦虑词
        anxiety_words = ("焦虑", "怕", "担心", "睡不着", "紧张", "慌", "不安")
        anx_hits = sum(1 for t in texts if any(w in t for w in anxiety_words))

        # 时间紧张词
        time_words = ("加班", "忙", "没时间", "累", "好困", "赶")
        time_hits = sum(1 for t in texts if any(w in t for w in time_words))

        # 表达能力
        if avg_len >= 30 and emo_hits >= max(1, n // 5):
            profile["emotional_expression"] = self._bump(profile["emotional_expression"], +1)
        elif perfunctory >= n // 2 and avg_len < 10:
            profile["emotional_expression"] = self._bump(profile["emotional_expression"], -1)

        # 焦虑
        if anx_hits >= max(1, n // 5):
            profile["anxiety_level"] = self._bump(profile["anxiety_level"], +1)

        # 时间精力
        if time_hits >= max(1, n // 4):
            profile["time_energy"] = self._bump(profile["time_energy"], -1)

        self._save_profile(user_id, profile)
        return profile

    # ------------------------------------------------------------------ #
    # 推荐起始级别
    # ------------------------------------------------------------------ #
    def get_recommended_start_level(self, user_id: str) -> str:
        """根据画像推荐任务起始级别：basic / intermediate / advanced。

        规则：
          - social_skill>=4 且 anxiety_level<=2 → advanced
          - social_skill<=2 或 anxiety_level>=4 → basic
          - 其余 → intermediate
        用户手动设定的 level 优先级低于此推荐（系统给建议，用户拍板）。
        """
        p = self.get_profile(user_id)
        social = p.get("social_skill", 3)
        anxiety = p.get("anxiety_level", 3)
        if social >= 4 and anxiety <= 2:
            return "advanced"
        if social <= 2 or anxiety >= 4:
            return "basic"
        return "intermediate"

    def set_user_ability_level(self, user_id: str, level: str) -> dict:
        """用户手动设定能力级别（basic/intermediate/advanced）。"""
        if level not in VALID_LEVELS:
            level = "basic"
        profile = self.get_profile(user_id)
        profile["level"] = level
        self._save_profile(user_id, profile)
        return profile

    # ------------------------------------------------------------------ #
    # 情感阶梯表
    # ------------------------------------------------------------------ #
    def get_emotional_ladder(self, user_id: str) -> dict:
        """返回情感阶梯表 + 用户当前推荐级别。

        Returns:
            dict: {
                "ladder": [L1..L5],
                "current_level": "L1".."L5"（根据用户 level 映射）,
                "recommended_start_level": basic/intermediate/advanced,
                "profile": {...},
            }
        """
        p = self.get_profile(user_id)
        rec = self.get_recommended_start_level(user_id)
        # 把 basic/intermediate/advanced 映射到阶梯起点
        level_map = {"basic": "L1", "intermediate": "L2", "advanced": "L3"}
        return {
            "ladder": EMOTIONAL_LADDER,
            "current_level": level_map.get(p.get("level", "basic"), "L1"),
            "recommended_start_level": rec,
            "profile": p,
        }

    # ------------------------------------------------------------------ #
    # 核心：根据用户能力调整任务难度
    # ------------------------------------------------------------------ #
    def adjust_task_difficulty(self, user_id: str, task: dict) -> dict:
        """根据用户能力画像调整任务难度。

        调整维度：
          - high anxiety (>=4)：拆小步骤、加确认、减开放
          - social_skill<=2：附加话术模板
          - social_skill>=4：去话术模板、开放发挥
          - economic<=2：过滤高消费任务，替换为低成本版
          - time_energy<=2：缩短时间限制、减少任务数量
          - executive_function<=2：加检查点、步骤更清晰

        Args:
            user_id: 用户 ID。
            task: 任务 dict，至少含 title；可含 steps / guide_direction / deadline。

        Returns:
            dict: 调整后的任务 dict（复制后修改，不污染原对象）。
        """
        p = self.get_profile(user_id)
        adjusted = dict(task) if isinstance(task, dict) else {"title": str(task)}
        title = str(adjusted.get("title", ""))
        notes: list[str] = []

        # ---- 1. 经济紧张：过滤高消费任务 ----
        if p.get("economic_status", 3) <= 2:
            if any(kw in title for kw in HIGH_COST_KEYWORDS):
                adjusted["title"] = self._downgrade_to_low_cost(title)
                notes.append("[经济适配] 已替换为低成本/免费版本任务")

        # ---- 2. 时间少：缩短时间要求 ----
        if p.get("time_energy", 3) <= 2:
            # 把"30分钟/1小时/半天"类时间词替换成"10分钟"
            adjusted["title"] = re.sub(
                r"\d+\s*(分钟|小时|个小时)", "10分钟", adjusted["title"])
            notes.append("[时间适配] 已缩短任务时间要求至10分钟")

        # ---- 3. 社交新手：附加话术模板 ----
        if p.get("social_skill", 3) <= 2:
            scripts = self._pick_scripts(title)
            if scripts:
                existing_dir = adjusted.get("guide_direction", "") or ""
                script_block = "｜".join(scripts)
                adjusted["guide_direction"] = (
                    f"{existing_dir}｜[新手话术] {script_block}" if existing_dir
                    else f"[新手话术] {script_block}"
                )
                notes.append(f"[新手适配] 已附加 {len(scripts)} 条话术模板")

        # ---- 4. 熟练用户：去掉话术，鼓励开放发挥 ----
        if p.get("social_skill", 3) >= 4:
            existing_dir = adjusted.get("guide_direction", "") or ""
            if "[新手话术]" in existing_dir:
                adjusted["guide_direction"] = existing_dir.replace(
                    "[新手话术]", "[熟练用户] 请自主发挥，不必照本宣科：")
            notes.append("[熟练适配] 任务已开放，鼓励自主发挥")

        # ---- 5. 高焦虑：拆小步骤、加确认 ----
        if p.get("anxiety_level", 3) >= 4:
            steps = adjusted.get("steps")
            if isinstance(steps, list) and steps:
                # 把现有步骤名改成 <5 分钟的小步，末尾加一个"确认"步
                new_steps = []
                for s in steps:
                    if isinstance(s, dict):
                        ns = dict(s)
                        nm = str(ns.get("name", ""))
                        if nm:
                            ns["name"] = f"（≤5分钟）{nm}"
                        new_steps.append(ns)
                new_steps.append({
                    "name": "（≤2分钟）完成后自我确认：我做到了吗？",
                    "complete_keywords": ["做到了", "完成", "OK"],
                })
                adjusted["steps"] = new_steps
            notes.append("[焦虑适配] 任务已拆成<5分钟小步并加确认环节")

        # ---- 6. 执行功能差：加检查点 ----
        if p.get("executive_function", 3) <= 2:
            existing_dir = adjusted.get("guide_direction", "") or ""
            checkpoint = "[执行提醒] 请把本任务加入手机日历提醒，完成后回来打卡"
            adjusted["guide_direction"] = (
                f"{existing_dir}｜{checkpoint}" if existing_dir else checkpoint
            )
            notes.append("[执行适配] 已加入提醒检查点")

        # ---- 7. 高压力任务降级：新手/高焦虑用户不应拿到 L5 ----
        rec_level = self.get_recommended_start_level(user_id)
        if rec_level == "basic" and any(kw in title for kw in HIGH_PRESSURE_KEYWORDS):
            adjusted["title"] = self._downgrade_pressure(title)
            notes.append("[压力适配] 新手/高焦虑：高压力任务已降级为低压力版本")

        adjusted["_ability_notes"] = notes
        return adjusted

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _bump(v: int, delta: int) -> int:
        return max(1, min(5, int(v) + delta))

    @staticmethod
    def _pick_scripts(title: str) -> list[str]:
        """根据任务标题挑最相关的话术模板。"""
        if any(k in title for k in ("问候", "聊天", "打招呼", "开场白")):
            return SCRIPT_TEMPLATES["greeting"]
        if any(k in title for k in ("约", "邀约", "约会", "见面", "咖啡")):
            return SCRIPT_TEMPLATES["invite"]
        if any(k in title for k in ("感受", "表达", "脆弱", "真心话")):
            return SCRIPT_TEMPLATES["feeling"]
        if any(k in title for k in ("边界", "拒绝", "不同意见")):
            return SCRIPT_TEMPLATES["boundary"]
        if any(k in title for k in ("冲突", "吵架", "矛盾", "冷战")):
            return SCRIPT_TEMPLATES["conflict"]
        return SCRIPT_TEMPLATES["default"]

    @staticmethod
    def _downgrade_to_low_cost(title: str) -> str:
        """把高消费任务替换成低成本版本。"""
        replacements = [
            ("买礼物", "分享一首你喜欢的歌/一张你拍的照片"),
            ("高档餐厅", "在家/街边小店吃顿便饭"),
            ("送花", "路过花店时顺手拍一张发给TA"),
            ("大餐", "一起做顿饭"),
            ("出国旅行", "周末去公园/免费展览走走"),
            ("转账", "手写一段真诚的文字"),
            ("红包", "手写一段真诚的文字"),
            ("五星级", "一家平价但有特色的小店"),
            ("奢侈品", "无消费的陪伴"),
        ]
        new = title
        for old, repl in replacements:
            new = new.replace(old, repl)
        if new == title:
            # 没匹配到具体词，加一个前缀说明
            new = f"[低成本版]{title}"
        return new

    @staticmethod
    def _downgrade_pressure(title: str) -> str:
        """把高压力任务降级为低压力版本。"""
        replacements = [
            ("直接表白", "先表达一句具体的欣赏，不要求对方回应"),
            ("表白", "先表达一句具体的欣赏，不要求对方回应"),
            ("处理冲突", "先观察记录冲突触发点，暂不正面沟通"),
            ("谈关系定位", "先自然聊一次对未来的期待，不逼对方表态"),
            ("见家长", "先介绍TA给一个好朋友认识"),
            ("摊牌", "先温和地表达一次感受，不要求解决全部问题"),
            ("分手", "先给自己3天冷静期，不做任何决定"),
            ("复合", "先发一条不带压力的问候，观察回应"),
        ]
        new = title
        for old, repl in replacements:
            new = new.replace(old, repl)
        if new == title:
            new = f"[低压力版]{title}"
        return new
