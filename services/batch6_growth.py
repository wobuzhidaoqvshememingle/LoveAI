# -*- coding: utf-8 -*-
"""
LoveAI v3.0 - 第六批第一批：用户成长系统/复盘/情景模拟/XP+Streak

全部为新建独立模块，纯函数+轻SQLite，向后兼容。服务层纯Python。
"""
from __future__ import annotations
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ============ 1+2. 用户成长系统（含复盘）============
class UserGrowth:
    """四维能力(沟通/共情/策略/执行)，XP升级，等级解锁高级策略。"""
    DIMS = ("沟通力", "共情力", "策略力", "执行力")
    UNLOCK = {3: "深度情感投标", 5: "高级效应组合", 10: "Contract Net详投"}

    def __init__(self, db: Any = None, user_id: str = "me"):
        self._db = db; self._uid = user_id
        self.dims = {d: 10 for d in self.DIMS}
        self.xp = 0
        self._ensure_table()

    def _ensure_table(self):
        try:
            if self._db and hasattr(self._db, "execute"):
                self._db.execute("CREATE TABLE IF NOT EXISTS user_growth("
                    "user_id TEXT, dim TEXT, xp REAL, level REAL, updated_at REAL)")
        except Exception: pass

    def gain_xp(self, amount: float, dim: str = "执行力") -> dict:
        self.xp += amount
        if dim in self.dims:
            self.dims[dim] = min(100, self.dims[dim] + amount * 0.5)
        lvl = self.level()
        unlocked = [v for lv, v in self.UNLOCK.items() if lvl >= lv]
        try:
            if self._db:
                self._db.execute("INSERT INTO user_growth(user_id,dim,xp,level,updated_at) "
                    "VALUES(:u,:d,:x,:l,:t)", {"u":self._uid,"d":dim,"x":self.xp,
                    "l":lvl,"t":time.time()})
        except Exception: pass
        return {"xp": self.xp, "level": lvl, "unlocked": unlocked}

    def level(self) -> int:
        return 1 + self.xp // 100

    def review(self, good: str, improve: str, next_note: str, signal: str) -> dict:
        """复盘：自动总结，质量影响XP。"""
        points = 5 + (5 if good and improve else 0)
        self.gain_xp(points, "共情力")
        return {"good": good, "improve": improve, "next": next_note,
                "key_signal": signal, "xp_gained": points}


# ============ 3. 情景模拟练习 ============
class ScenarioSimulator:
    """数字分身扮演对方；练习回复→评分→建议→XP。"""
    SCENES = ["第一次约会", "吵架和好", "表白", "被拒绝", "冷淡", "质问", "异地"]

    def score_reply(self, reply: str, strategy: str) -> dict:
        length = len(reply or "")
        empathy = 1 if any(k in reply for k in ("我理解", "别急", "在", "你")) else 0
        push = 1 if any(k in reply for k in ("周末", "见面", "一起", "下次")) else 0
        s = min(100, 30 + empathy*25 + push*25 + (10 if 5 < length < 60 else 0))
        return {"score": s, "empathy": empathy, "push": push,
                "suggestion": "可加一句共情" if not empathy else "可适度推进"}


# ============ 4. XP + Streak ============
class XPStreak:
    """连续使用天数Streak奖励；等级解锁功能。"""
    def __init__(self):
        self.xp = 0; self.streak = 0; self._last = None

    def login(self) -> dict:
        today = time.strftime("%Y-%m-%d")
        if self._last is None:
            self.streak = 1
        elif self._last == today:
            pass
        else:
            self.streak += 1
        self._last = today
        bonus = {3: 1.2, 7: 1.5, 30: 2.0}.get(self.streak, 1.0)
        return {"streak": self.streak, "bonus_mult": bonus}

    def add(self, amount: float) -> float:
        mult = {3: 1.2, 7: 1.5, 30: 2.0}.get(self.streak, 1.0)
        gained = amount * mult
        self.xp += gained
        return gained

    def unlocked(self) -> list:
        lvl = 1 + self.xp // 100
        feats = {1: "基础策略", 5: "高级组合", 10: "Contract Net"}
        return [v for lv, v in feats.items() if lvl >= lv]
