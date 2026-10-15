"""
心情状态服务
类似微信状态，支持：实时心情标签、惯例情绪模式、从聊天记录自动捕获、时间趋势分析
"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)


class MoodService:
    """心情状态管理服务"""

    # 预设心情状态标签
    MOOD_TAGS = {
        # 积极情绪
        "happy": {"name": "开心", "emoji": "😊", "category": "positive", "color": "#22c55e"},
        "excited": {"name": "兴奋", "emoji": "🤩", "category": "positive", "color": "#f59e0b"},
        "relaxed": {"name": "放松", "emoji": "😌", "category": "positive", "color": "#06b6d4"},
        "confident": {"name": "自信", "emoji": "😎", "category": "positive", "color": "#3b82f6"},
        "grateful": {"name": "感恩", "emoji": "🥰", "category": "positive", "color": "#ec4899"},

        # 中性情绪
        "calm": {"name": "平静", "emoji": "😐", "category": "neutral", "color": "#6b7280"},
        "tired": {"name": "疲劳", "emoji": "😴", "category": "neutral", "color": "#78716c"},
        "busy": {"name": "忙碌", "emoji": "🏃", "category": "neutral", "color": "#a3a3a3"},
        "hungry": {"name": "饿了", "emoji": "🍔", "category": "neutral", "color": "#f97316"},
        "sleepy": {"name": "困了", "emoji": "🥱", "category": "neutral", "color": "#94a3b8"},

        # 消极情绪
        "sad": {"name": "沮丧", "emoji": "😢", "category": "negative", "color": "#3b82f6"},
        "emo": {"name": "emo", "emoji": "🌧️", "category": "negative", "color": "#6366f1"},
        "anxious": {"name": "焦虑", "emoji": "😰", "category": "negative", "color": "#f59e0b"},
        "insecure": {"name": "没安全感", "emoji": "🫂", "category": "negative", "color": "#8b5cf6"},
        "overthinking": {"name": "胡思乱想", "emoji": "🤔", "category": "negative", "color": "#7c3aed"},
        "angry": {"name": "生气", "emoji": "😠", "category": "negative", "color": "#ef4444"},
        "lonely": {"name": "孤独", "emoji": "🥺", "category": "negative", "color": "#64748b"},
        "stressed": {"name": "压力大", "emoji": "😫", "category": "negative", "color": "#dc2626"},
        "disappointed": {"name": "失望", "emoji": "😞", "category": "negative", "color": "#475569"},

        # 关系相关
        "missing_you": {"name": "想你了", "emoji": "💭", "category": "relationship", "color": "#ec4899"},
        "clingy": {"name": "黏人", "emoji": "🥺", "category": "relationship", "color": "#f472b6"},
        "cold": {"name": "冷淡", "emoji": "🧊", "category": "relationship", "color": "#0ea5e9"},
        "jealous": {"name": "吃醋", "emoji": "🍋", "category": "relationship", "color": "#84cc16"},
        "coquettish": {"name": "撒娇", "emoji": "😽", "category": "relationship", "color": "#f472b6"},

        # 身体/健康相关
        "physical_discomfort": {"name": "身体不适", "emoji": "🤒", "category": "health", "color": "#f97316"},
    }

    # 生理周期症状关键词（对方提到时，自动判定为"身体不适"并触发周期记录）
    PERIOD_SYMPTOM_KEYWORDS = [
        "姨妈", "痛经", "肚子疼", "肚子痛", "不舒服", "流血",
        "经期", "大姨妈", "生理期", "来事了", "腰酸",
    ]

    # 关键词到心情标签的映射（用于从聊天记录自动识别）
    KEYWORD_TO_MOOD = {
        "happy": ["开心", "高兴", "哈哈", "嘻嘻", "快乐", "好棒", "太好了", "耶", "哇"],
        "excited": ["激动", "兴奋", "期待", "好期待", "超开心"],
        "tired": ["累", "好累", "疲惫", "疲劳", "累死", "没力气", "困"],
        "sleepy": ["困", "好困", "想睡觉", "睡觉了", "晚安"],
        "sad": ["难过", "伤心", "不开心", "郁闷", "失落", "想哭", "哭"],
        "emo": ["emo", "网抑云", "抑郁", "低落", "丧"],
        "anxious": ["焦虑", "紧张", "担心", "害怕", "不安", "慌"],
        "insecure": ["没安全感", "不安全", "怕失去", "自卑", "不配", "觉得自己"],
        "overthinking": ["胡思乱想", "想太多", "纠结", "反复想", "钻牛角尖"],
        "angry": ["生气", "气死", "愤怒", "火大", "烦", "讨厌"],
        "lonely": ["孤独", "寂寞", "一个人", "没人陪", "无聊"],
        "stressed": ["压力", "好累", "撑不住", "崩溃", "喘不过气"],
        "missing_you": ["想你", "想念", "好想你", "想你了"],
        "clingy": ["陪我", "不要走", "别离开", "黏着", "要抱抱"],
        "cold": ["哦", "嗯", "随便", "都行", "无所谓", "呵呵"],
        "jealous": ["吃醋", "嫉妒", "酸", "那个女的", "那个男的"],
        "coquettish": ["哼", "不理你了", "你坏", "讨厌啦", "嘤嘤"],
        "hungry": ["饿", "好饿", "想吃", "吃饭", "干饭"],
        "busy": ["忙", "在忙", "没空", "等一下", "稍后"],
    }

    def __init__(self, db, config=None):
        self.db = db
        self.config = config or {}
        self._ensure_tables()

    def _ensure_tables(self):
        """确保心情状态相关表存在"""
        try:
            # 心情状态记录表
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS mood_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    mood_tag TEXT NOT NULL,
                    source TEXT DEFAULT 'manual',
                    confidence REAL DEFAULT 1.0,
                    context TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    expires_at TEXT,
                    FOREIGN KEY (person_id) REFERENCES persons(id)
                )
            """)
            self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_mood_person_time
                ON mood_records(person_id, created_at)
            """)

            # 惯例情绪模式表（比如每周三晚上容易emo）
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS mood_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    mood_tag TEXT NOT NULL,
                    pattern_type TEXT,
                    time_start TEXT,
                    time_end TEXT,
                    weekday INTEGER,
                    frequency INTEGER DEFAULT 0,
                    last_occurred TEXT,
                    note TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (person_id) REFERENCES persons(id)
                )
            """)
        except Exception as e:
            logger.warning("创建心情状态表失败: %s", e)

    def set_mood(self, person_id: str, mood_tag: str,
                 source: str = "manual", duration_hours: int = 4,
                 context: str = "", confidence: float = 1.0) -> Dict:
        """设置当前心情状态"""
        try:
            if mood_tag not in self.MOOD_TAGS:
                return {"ok": False, "error": f"未知心情标签: {mood_tag}"}

            expires_at = (datetime.now() + timedelta(hours=duration_hours)).strftime("%Y-%m-%d %H:%M:%S")

            self.db.execute("""
                INSERT INTO mood_records
                (person_id, mood_tag, source, confidence, context, expires_at)
                VALUES (:pid, :tag, :source, :conf, :ctx, :exp)
            """, {
                "pid": person_id,
                "tag": mood_tag,
                "source": source,
                "conf": confidence,
                "ctx": context,
                "exp": expires_at,
            })

            # 更新惯例模式
            self._update_pattern(person_id, mood_tag)

            mood_info = self.MOOD_TAGS[mood_tag]
            return {
                "ok": True,
                "mood": mood_tag,
                "name": mood_info["name"],
                "emoji": mood_info["emoji"],
                "expires_at": expires_at,
            }
        except Exception as e:
            logger.error("设置心情失败: %s", e)
            return {"ok": False, "error": str(e)}

    def get_current_mood(self, person_id: str) -> Optional[Dict]:
        """获取当前有效的心情状态"""
        try:
            row = self.db.execute("""
                SELECT mood_tag, source, confidence, context, created_at, expires_at
                FROM mood_records
                WHERE person_id=:pid AND (expires_at IS NULL OR expires_at > datetime('now'))
                ORDER BY created_at DESC
                LIMIT 1
            """, {"pid": person_id}).fetchone()

            if not row:
                return None

            mood_tag = row[0]
            mood_info = self.MOOD_TAGS.get(mood_tag, {"name": mood_tag, "emoji": "❓", "color": "#666"})

            return {
                "mood": mood_tag,
                "name": mood_info["name"],
                "emoji": mood_info["emoji"],
                "color": mood_info.get("color", "#666"),
                "category": mood_info.get("category", "unknown"),
                "source": row[1],
                "confidence": row[2],
                "context": row[3],
                "created_at": row[4],
                "expires_at": row[5],
            }
        except Exception as e:
            logger.error("获取当前心情失败: %s", e)
            return None

    def get_mood_history(self, person_id: str, days: int = 7) -> List[Dict]:
        """获取最近N天的心情历史"""
        try:
            rows = self.db.execute("""
                SELECT mood_tag, source, context, created_at
                FROM mood_records
                WHERE person_id=:pid AND created_at >= datetime('now', '-' || :days || ' days')
                ORDER BY created_at DESC
            """, {"pid": person_id, "days": days}).fetchall()

            result = []
            for row in rows:
                mood_info = self.MOOD_TAGS.get(row[0], {"name": row[0], "emoji": "❓"})
                result.append({
                    "mood": row[0],
                    "name": mood_info["name"],
                    "emoji": mood_info["emoji"],
                    "source": row[1],
                    "context": row[2],
                    "created_at": row[3],
                })
            return result
        except Exception as e:
            logger.error("获取心情历史失败: %s", e)
            return []

    def detect_mood_from_text(self, text: str) -> List[Dict]:
        """从文本中检测心情状态（关键词匹配）"""
        if not text:
            return []

        detected = []
        text_lower = text.lower()

        for mood_tag, keywords in self.KEYWORD_TO_MOOD.items():
            matches = [kw for kw in keywords if kw in text_lower]
            if matches:
                mood_info = self.MOOD_TAGS.get(mood_tag, {})
                detected.append({
                    "mood": mood_tag,
                    "name": mood_info.get("name", mood_tag),
                    "emoji": mood_info.get("emoji", "❓"),
                    "color": mood_info.get("color", "#666"),
                    "matched_keywords": matches,
                    "confidence": min(0.3 + len(matches) * 0.2, 0.9),
                })

        # 按置信度排序
        detected.sort(key=lambda x: x["confidence"], reverse=True)
        return detected

    def auto_detect_and_set(self, person_id: str, text: str,
                            min_confidence: float = 0.5) -> Optional[Dict]:
        """从聊天文本自动检测并设置心情状态"""
        detected = self.detect_mood_from_text(text)
        if not detected or detected[0]["confidence"] < min_confidence:
            return None

        top_mood = detected[0]
        result = self.set_mood(
            person_id=person_id,
            mood_tag=top_mood["mood"],
            source="auto",
            duration_hours=2,
            context=f"自动检测: {text[:50]}",
            confidence=top_mood["confidence"],
        )
        return result if result.get("ok") else None

    def detect_period_symptoms(self, text: str) -> List[str]:
        """检测文本中是否包含生理周期症状关键词。

        Returns:
            命中的关键词列表（去重），未命中返回空列表。
        """
        if not text:
            return []
        hits = []
        for kw in self.PERIOD_SYMPTOM_KEYWORDS:
            if kw in text and kw not in hits:
                hits.append(kw)
        return hits

    def apply_period_state(self, person_id: str, text: str) -> Optional[Dict]:
        """检测到生理周期症状时，自动把对方心情置为"身体不适"。

        - 命中关键词即设置（不经过 min_confidence 门槛，生理信号本身置信度高）；
        - source='period'，有效期 8 小时，context 记录命中的症状词；
        - 只负责心情侧；是否写入 menstrual_cycles 由调用方（service_facade）
          结合 CycleService 决定，避免 MoodService 反向依赖。

        Returns:
            set_mood 的结果 dict；未命中关键词返回 None。
        """
        hits = self.detect_period_symptoms(text)
        if not hits:
            return None
        return self.set_mood(
            person_id=person_id,
            mood_tag="physical_discomfort",
            source="period",
            duration_hours=8,
            context=f"生理症状: {'、'.join(hits)} | {text[:50]}",
            confidence=0.85,
        )

    def get_mood_analysis_context(self, person_id: str) -> str:
        """获取心情状态分析上下文（供LLM使用）"""
        current = self.get_current_mood(person_id)
        patterns = self.get_patterns(person_id)

        context_parts = []

        if current:
            context_parts.append(f"""
【对方当前心情状态】
当前状态：{current['emoji']} {current['name']}
来源：{'手动设置' if current['source'] == 'manual' else '自动检测'}
置信度：{int(current['confidence'] * 100)}%
上下文：{current['context'] or '无'}
""")

        if patterns:
            pattern_text = "\n".join([
                f"- {p['mood_name']}：{p['pattern_desc']}（出现{p['frequency']}次）"
                for p in patterns[:5]
            ])
            context_parts.append(f"""
【对方惯例情绪模式】
{pattern_text}
注意：如果当前时间符合某个惯例模式，对方的情绪可能是习惯性的，不要过度解读。
""")

        return "\n".join(context_parts).strip()

    def _update_pattern(self, person_id: str, mood_tag: str):
        """更新惯例情绪模式（记录什么时间容易出现什么情绪）"""
        try:
            now = datetime.now()
            weekday = now.weekday()
            time_str = now.strftime("%H:%M")

            # 查找是否已有相似模式
            row = self.db.execute("""
                SELECT id, frequency FROM mood_patterns
                WHERE person_id=:pid AND mood_tag=:tag AND weekday=:wd
                LIMIT 1
            """, {"pid": person_id, "tag": mood_tag, "wd": weekday}).fetchone()

            if row:
                self.db.execute("""
                    UPDATE mood_patterns
                    SET frequency = frequency + 1, last_occurred = datetime('now')
                    WHERE id=:id
                """, {"id": row[0]})
            else:
                self.db.execute("""
                    INSERT INTO mood_patterns
                    (person_id, mood_tag, pattern_type, time_start, weekday, frequency, last_occurred)
                    VALUES (:pid, :tag, 'weekly', :time, :wd, 1, datetime('now'))
                """, {"pid": person_id, "tag": mood_tag, "time": time_str, "wd": weekday})
        except Exception as e:
            logger.warning("更新惯例模式失败: %s", e)

    def get_patterns(self, person_id: str) -> List[Dict]:
        """获取惯例情绪模式"""
        try:
            rows = self.db.execute("""
                SELECT mood_tag, pattern_type, time_start, weekday, frequency, note
                FROM mood_patterns
                WHERE person_id=:pid
                ORDER BY frequency DESC
                LIMIT 10
            """, {"pid": person_id}).fetchall()

            result = []
            for row in rows:
                mood_info = self.MOOD_TAGS.get(row[0], {"name": row[0], "emoji": "❓"})
                weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                weekday_str = weekday_names[row[3]] if row[3] is not None else "每天"
                pattern_desc = f"{weekday_str} {row[2]}左右"

                result.append({
                    "mood": row[0],
                    "mood_name": mood_info["name"],
                    "emoji": mood_info["emoji"],
                    "pattern_desc": pattern_desc,
                    "frequency": row[4],
                    "note": row[5],
                })
            return result
        except Exception as e:
            logger.error("获取惯例模式失败: %s", e)
            return []

    def get_all_mood_tags(self) -> List[Dict]:
        """获取所有可用心情标签"""
        return [
            {"tag": k, "name": v["name"], "emoji": v["emoji"],
             "color": v["color"], "category": v["category"]}
            for k, v in self.MOOD_TAGS.items()
        ]

    def get_mood_statistics(self, person_id: str, days: int = 30) -> Dict:
        """获取心情统计"""
        try:
            rows = self.db.execute("""
                SELECT mood_tag, COUNT(*) as cnt
                FROM mood_records
                WHERE person_id=:pid AND created_at >= datetime('now', '-' || :days || ' days')
                GROUP BY mood_tag
                ORDER BY cnt DESC
            """, {"pid": person_id, "days": days}).fetchall()

            total = sum(r[1] for r in rows)
            stats = []
            for row in rows:
                mood_info = self.MOOD_TAGS.get(row[0], {"name": row[0], "emoji": "❓"})
                stats.append({
                    "mood": row[0],
                    "name": mood_info["name"],
                    "emoji": mood_info["emoji"],
                    "color": mood_info.get("color", "#666"),
                    "count": row[1],
                    "percentage": int(row[1] / total * 100) if total > 0 else 0,
                })

            return {"total": total, "stats": stats, "days": days}
        except Exception as e:
            logger.error("获取心情统计失败: %s", e)
            return {"total": 0, "stats": [], "days": days}
