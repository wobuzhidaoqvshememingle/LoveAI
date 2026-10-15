# -*- coding: utf-8 -*-
"""第六批第四批：关系时间线/隐私模式/多目标隔离/A-B回复学习。纯函数向后兼容。"""
from __future__ import annotations
import logging, time, hashlib
logger = logging.getLogger(__name__)

# ============ 13. 关系时间线 ============
def relationship_timeline(state_changes: list[dict]) -> dict:
    """从好感度变化推导节点+曲线。"""
    nodes = []
    curve = []
    for c in sorted(state_changes, key=lambda x: x.get("ts", 0)):
        curve.append({"ts": c.get("ts"), "v": c.get("new_value")})
        if c.get("event") in ("stage_up", "stage_down"):
            nodes.append({"ts": c.get("ts"), "label": c.get("event")})
    return {"nodes": nodes, "affection_curve": curve, "node_count": len(nodes)}


# ============ 14. 隐私模式 ============
class PrivacyMode:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def redact(self, text: str) -> str:
        if not self.enabled:
            return text
        return hashlib.md5((text or "").encode("utf-8")).hexdigest()[:8]

    def allow_cloud(self) -> bool:
        return not self.enabled


# ============ 15. 多目标管理 ============
class MultiTarget:
    def __init__(self):
        self.persons: dict[str, dict] = {}

    def register(self, person_id: str, name: str):
        self.persons[person_id] = {"name": name}

    def switch(self, person_id: str) -> dict | None:
        return self.persons.get(person_id)

    def merge_same_person(self, a: str, b: str) -> bool:
        """双微信同一人按姓名匹配合并。"""
        if a in self.persons and b in self.persons:
            return self.persons[a]["name"] == self.persons[b]["name"]
        return False


# ============ 16. A/B 回复学习 ============
class ABLearning:
    def __init__(self):
        self.feedback: dict[str, float] = {}

    def record(self, reply_style: str, score: float):
        self.feedback[reply_style] = self.feedback.get(reply_style, 0.5) * 0.7 + score * 0.3

    def best_style(self) -> str | None:
        return max(self.feedback, key=self.feedback.get) if self.feedback else None

    def utility_weights(self) -> dict:
        return dict(self.feedback)
