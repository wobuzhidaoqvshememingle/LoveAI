"""
生理周期服务
支持：
- 女性：姨妈期/卵泡期/排卵期/黄体期记录与预测，情绪波动分析，各阶段攻略建议
- 男性：情绪周期（精力充沛期/平稳期/低落期/恢复期），俗称"大姨夫"
"""
import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CycleService:
    """生理周期管理服务（支持男女）"""

    # 女性周期阶段定义
    CYCLE_PHASES = {
        "menstrual": {
            "name": "姨妈期",
            "duration": 5,  # 平均天数
            "emotion_traits": ["易怒", "情绪低落", "敏感", "疲惫", "渴望安慰"],
            "advice": "多关心少讲道理，主动提供温暖和照顾，避免争执",
            "strategy": "温柔陪伴型，多说暖心话，主动帮忙做事",
            "color": "#ef4444",
        },
        "follicular": {
            "name": "卵泡期",
            "duration": 7,
            "emotion_traits": ["精力充沛", "情绪稳定", "自信", "社交活跃"],
            "advice": "适合约会和推进关系，对方状态好容易接受新事物",
            "strategy": "积极进攻型，可以安排约会、送礼物、推进关系",
            "color": "#22c55e",
        },
        "ovulation": {
            "name": "排卵期",
            "duration": 3,
            "emotion_traits": ["性欲增强", "情绪高涨", "更有魅力", "渴望亲密"],
            "advice": "最佳升温期，适合制造浪漫和亲密接触",
            "strategy": "浪漫升温型，制造惊喜，肢体接触升级",
            "color": "#a855f7",
        },
        "luteal": {
            "name": "黄体期",
            "duration": 10,
            "emotion_traits": ["焦虑", "情绪波动", "易怒", "食欲增加", "需要安全感"],
            "advice": "PMS高发期，多包容少较真，提供安全感",
            "strategy": "稳定陪伴型，多倾听少反驳，避免重大决策",
            "color": "#f59e0b",
        },
    }

    # 男性情绪周期阶段定义（俗称"大姨夫"，与睾酮水平波动相关）
    MALE_CYCLE_PHASES = {
        "energetic": {
            "name": "精力充沛期",
            "duration": 8,
            "emotion_traits": ["自信", "精力旺盛", "性欲强", "社交活跃", "有干劲"],
            "advice": "对方状态好，适合一起做有挑战性的事，给予认可和崇拜",
            "strategy": "崇拜鼓励型，多夸他能力强，一起参与活动",
            "color": "#22c55e",
        },
        "stable": {
            "name": "平稳期",
            "duration": 12,
            "emotion_traits": ["情绪稳定", "理性", "专注", "日常状态"],
            "advice": "正常相处即可，可以推进日常计划和沟通",
            "strategy": "日常陪伴型，自然相处，适度关心",
            "color": "#3b82f6",
        },
        "low": {
            "name": "低落期（大姨夫）",
            "duration": 6,
            "emotion_traits": ["易怒", "疲惫", "冷漠", "需要独处", "性欲下降", "敏感"],
            "advice": "他需要空间而非追问，别逼他说话，默默陪伴，给台阶下",
            "strategy": "空间给予型，减少情绪索取，做自己的事，等他自己恢复",
            "color": "#ef4444",
        },
        "recovery": {
            "name": "恢复期",
            "duration": 4,
            "emotion_traits": ["逐渐回暖", "需要被需要", "渴望亲密", "补偿心理"],
            "advice": "他恢复后会有补偿心理，适合适度撒娇和提小要求",
            "strategy": "温柔接纳型，不翻旧账，自然恢复亲密",
            "color": "#f59e0b",
        },
    }

    def __init__(self, db, config=None):
        self.db = db
        self.config = config or {}
        self._ensure_table()

    def _ensure_table(self):
        """确保生理周期表存在（女性+男性）"""
        try:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS menstrual_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    start_date TEXT NOT NULL,
                    end_date TEXT,
                    cycle_length INTEGER DEFAULT 28,
                    period_length INTEGER DEFAULT 5,
                    symptoms TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (person_id) REFERENCES persons(id)
                )
            """)
            self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_cycle_person_date
                ON menstrual_cycles(person_id, start_date)
            """)
            # 男性情绪周期表（记录低落期开始，用于推算周期）
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS male_mood_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    low_phase_start TEXT NOT NULL,
                    low_phase_end TEXT,
                    cycle_length INTEGER DEFAULT 30,
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (person_id) REFERENCES persons(id)
                )
            """)
            self.db.execute("""
                CREATE INDEX IF NOT EXISTS idx_male_cycle_person_date
                ON male_mood_cycles(person_id, low_phase_start)
            """)
        except Exception as e:
            logger.warning("创建生理周期表失败: %s", e)

    def add_cycle(self, person_id: str, start_date: str,
                  cycle_length: int = 28, period_length: int = 5,
                  symptoms: str = "", notes: str = "") -> Dict:
        """添加一次姨妈期记录"""
        try:
            end_date = (datetime.strptime(start_date, "%Y-%m-%d") +
                       timedelta(days=period_length - 1)).strftime("%Y-%m-%d")
            self.db.execute("""
                INSERT INTO menstrual_cycles
                (person_id, start_date, end_date, cycle_length, period_length, symptoms, notes)
                VALUES (:pid, :start, :end, :cycle, :period, :symptoms, :notes)
            """, {
                "pid": person_id,
                "start": start_date,
                "end": end_date,
                "cycle": cycle_length,
                "period": period_length,
                "symptoms": symptoms,
                "notes": notes,
            })
            return {"ok": True, "message": "记录成功", "end_date": end_date}
        except Exception as e:
            logger.error("添加生理周期失败: %s", e)
            return {"ok": False, "error": str(e)}

    def get_cycles(self, person_id: str, limit: int = 12) -> List[Dict]:
        """获取历史周期记录"""
        try:
            rows = self.db.execute("""
                SELECT id, start_date, end_date, cycle_length, period_length, symptoms, notes
                FROM menstrual_cycles
                WHERE person_id=:pid
                ORDER BY start_date DESC
                LIMIT :limit
            """, {"pid": person_id, "limit": limit}).fetchall()
            return [dict(zip(["id", "start_date", "end_date", "cycle_length",
                             "period_length", "symptoms", "notes"], row)) for row in rows]
        except Exception as e:
            logger.error("获取周期记录失败: %s", e)
            return []

    def get_current_phase(self, person_id: str) -> Optional[Dict]:
        """计算当前所处的周期阶段"""
        try:
            cycles = self.get_cycles(person_id, limit=1)
            if not cycles:
                return None

            last_cycle = cycles[0]
            last_start = datetime.strptime(last_cycle["start_date"], "%Y-%m-%d")
            cycle_length = last_cycle.get("cycle_length", 28)
            period_length = last_cycle.get("period_length", 5)

            today = datetime.now()
            days_since_start = (today - last_start).days

            # 计算当前阶段
            if days_since_start < 0:
                # 还没到上次姨妈期（可能记录的是未来日期）
                return None
            elif days_since_start < period_length:
                phase_key = "menstrual"
                day_in_phase = days_since_start + 1
            elif days_since_start < period_length + 7:
                phase_key = "follicular"
                day_in_phase = days_since_start - period_length + 1
            elif days_since_start < period_length + 10:
                phase_key = "ovulation"
                day_in_phase = days_since_start - period_length - 7 + 1
            else:
                phase_key = "luteal"
                day_in_phase = days_since_start - period_length - 10 + 1

            phase = self.CYCLE_PHASES[phase_key]
            days_until_next = cycle_length - days_since_start

            return {
                "phase": phase_key,
                "phase_name": phase["name"],
                "day_in_phase": day_in_phase,
                "days_since_period": days_since_start,
                "days_until_next_period": max(0, days_until_next),
                "emotion_traits": phase["emotion_traits"],
                "advice": phase["advice"],
                "strategy": phase["strategy"],
                "color": phase["color"],
                "next_period_date": (last_start + timedelta(days=cycle_length)).strftime("%Y-%m-%d"),
            }
        except Exception as e:
            logger.error("计算当前阶段失败: %s", e)
            return None

    def predict_next_cycles(self, person_id: str, count: int = 3) -> List[Dict]:
        """预测未来的姨妈期"""
        try:
            cycles = self.get_cycles(person_id, limit=3)
            if not cycles:
                return []

            # 计算平均周期长度
            lengths = [c.get("cycle_length", 28) for c in cycles]
            avg_length = sum(lengths) // len(lengths)

            last_start = datetime.strptime(cycles[0]["start_date"], "%Y-%m-%d")
            predictions = []
            for i in range(1, count + 1):
                next_start = last_start + timedelta(days=avg_length * i)
                predictions.append({
                    "cycle_number": i,
                    "start_date": next_start.strftime("%Y-%m-%d"),
                    "end_date": (next_start + timedelta(days=4)).strftime("%Y-%m-%d"),
                    "ovulation_date": (next_start + timedelta(days=avg_length - 14)).strftime("%Y-%m-%d"),
                })
            return predictions
        except Exception as e:
            logger.error("预测周期失败: %s", e)
            return []

    def get_emotion_analysis_context(self, person_id: str) -> str:
        """获取生理周期对情绪的影响上下文（供LLM分析使用）"""
        phase = self.get_current_phase(person_id)
        if not phase:
            return ""

        context = f"""
【生理周期状态】
当前阶段：{phase['phase_name']}（第{phase['day_in_phase']}天）
距离下次姨妈：{phase['days_until_next_period']}天
该阶段典型情绪：{', '.join(phase['emotion_traits'])}
该阶段相处建议：{phase['advice']}
该阶段攻略策略：{phase['strategy']}

注意：如果对方情绪异常，需考虑是否受生理周期影响。
比如姨妈期和黄体期容易情绪波动，不要把生理原因当成感情问题。
"""
        return context.strip()

    def get_phase_advice(self, phase_key: str) -> Optional[Dict]:
        """获取指定阶段的详细攻略"""
        return self.CYCLE_PHASES.get(phase_key)

    def delete_cycle(self, cycle_id: int) -> Dict:
        """删除周期记录"""
        try:
            self.db.execute("DELETE FROM menstrual_cycles WHERE id=:id", {"id": cycle_id})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ==================== 男性情绪周期（大姨夫）====================

    def _get_person_gender(self, person_id: str) -> str:
        """获取人物性别（female/male/unknown）"""
        try:
            row = self.db.execute(
                "SELECT gender FROM persons WHERE id=:pid", {"pid": person_id}
            ).fetchone()
            if row and row[0]:
                g = str(row[0]).lower()
                if g in ("女", "female", "f", "woman"):
                    return "female"
                if g in ("男", "male", "m", "man"):
                    return "male"
            return "unknown"
        except Exception:
            return "unknown"

    def record_male_low_phase(self, person_id: str, start_date: str,
                              cycle_length: int = 30, notes: str = "") -> Dict:
        """记录男性低落期（大姨夫）开始，用于推算情绪周期。

        男性没有明显生理标记，通过观察到的情绪低落期来记录。
        """
        try:
            end_date = (datetime.strptime(start_date, "%Y-%m-%d") +
                       timedelta(days=5)).strftime("%Y-%m-%d")
            self.db.execute("""
                INSERT INTO male_mood_cycles
                (person_id, low_phase_start, low_phase_end, cycle_length, notes)
                VALUES (:pid, :start, :end, :cycle, :notes)
            """, {
                "pid": person_id,
                "start": start_date,
                "end": end_date,
                "cycle": cycle_length,
                "notes": notes,
            })
            return {"ok": True, "message": "男性低落期记录成功", "end_date": end_date}
        except Exception as e:
            logger.error("记录男性低落期失败: %s", e)
            return {"ok": False, "error": str(e)}

    def get_male_cycles(self, person_id: str, limit: int = 6) -> List[Dict]:
        """获取男性情绪周期记录"""
        try:
            rows = self.db.execute("""
                SELECT id, low_phase_start, low_phase_end, cycle_length, notes
                FROM male_mood_cycles
                WHERE person_id=:pid
                ORDER BY low_phase_start DESC
                LIMIT :limit
            """, {"pid": person_id, "limit": limit}).fetchall()
            return [dict(zip(["id", "low_phase_start", "low_phase_end",
                             "cycle_length", "notes"], row)) for row in rows]
        except Exception as e:
            logger.error("获取男性周期失败: %s", e)
            return []

    def get_male_current_phase(self, person_id: str) -> Optional[Dict]:
        """计算男性当前情绪周期阶段。

        以最近一次低落期开始为基准，按周期长度推算当前阶段。
        阶段顺序：低落期(6天) → 恢复期(4天) → 精力充沛期(8天) → 平稳期(12天)
        """
        try:
            cycles = self.get_male_cycles(person_id, limit=1)
            if not cycles:
                return None

            last = cycles[0]
            last_low_start = datetime.strptime(last["low_phase_start"], "%Y-%m-%d")
            cycle_length = last.get("cycle_length", 30)

            today = datetime.now()
            days_since_low = (today - last_low_start).days

            # 归一化到当前周期内
            day_in_cycle = days_since_low % cycle_length

            # 阶段划分（总30天）
            if day_in_cycle < 6:
                phase_key = "low"
                day_in_phase = day_in_cycle + 1
            elif day_in_cycle < 10:
                phase_key = "recovery"
                day_in_phase = day_in_cycle - 6 + 1
            elif day_in_cycle < 18:
                phase_key = "energetic"
                day_in_phase = day_in_cycle - 10 + 1
            else:
                phase_key = "stable"
                day_in_phase = day_in_cycle - 18 + 1

            phase = self.MALE_CYCLE_PHASES[phase_key]
            days_until_next_low = cycle_length - day_in_cycle

            return {
                "phase": phase_key,
                "phase_name": phase["name"],
                "day_in_phase": day_in_phase,
                "day_in_cycle": day_in_cycle,
                "days_until_next_low": max(0, days_until_next_low),
                "emotion_traits": phase["emotion_traits"],
                "advice": phase["advice"],
                "strategy": phase["strategy"],
                "color": phase["color"],
                "next_low_date": (last_low_start + timedelta(days=cycle_length)).strftime("%Y-%m-%d"),
                "gender": "male",
            }
        except Exception as e:
            logger.error("计算男性当前阶段失败: %s", e)
            return None

    def get_current_phase_auto(self, person_id: str) -> Optional[Dict]:
        """自动根据人物性别选择女性或男性周期阶段。"""
        gender = self._get_person_gender(person_id)
        if gender == "male":
            return self.get_male_current_phase(person_id)
        # 默认女性周期
        return self.get_current_phase(person_id)

    def get_male_emotion_context(self, person_id: str) -> str:
        """获取男性情绪周期上下文（供LLM分析使用）"""
        phase = self.get_male_current_phase(person_id)
        if not phase:
            return ""
        context = f"""
【男性情绪周期状态】
当前阶段：{phase['phase_name']}（第{phase['day_in_phase']}天）
距离下次低落期：{phase['days_until_next_low']}天
该阶段典型情绪：{', '.join(phase['emotion_traits'])}
该阶段相处建议：{phase['advice']}
该阶段攻略策略：{phase['strategy']}

注意：男性也有情绪周期（俗称"大姨夫"），低落期需要空间而非追问，
不要把他的情绪低落当成感情变淡或不爱了。
"""
        return context.strip()
