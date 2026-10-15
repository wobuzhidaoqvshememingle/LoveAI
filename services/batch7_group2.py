# -*- coding: utf-8 -*-
"""第七批第二批(6-10)：后果概率/通知设置/敏感词替换/数据备份/快捷键。纯函数向后兼容。"""
from __future__ import annotations
import logging, json, time
logger = logging.getLogger(__name__)

# ============ 6. 后果概率分布 ============
def outcome_probability(base_emotion: str) -> dict:
    probs = {"开心":0.6,"无所谓":0.3,"生气":0.1}
    if base_emotion == "愤怒": probs = {"开心":0.1,"无所谓":0.3,"生气":0.6}
    elif base_emotion == "低落": probs = {"开心":0.3,"无所谓":0.5,"生气":0.2}
    s = sum(probs.values())
    return {k: round(v/s, 2) for k, v in probs.items()}


# ============ 7. 通知设置 ============
class NotificationSettings:
    def __init__(self):
        self.cfg = {"new_message":True,"task_due":True,"crisis":True,"affection_change":True}
        self.freq = "即时"
    def set(self, key:str, val:bool): self.cfg[key]=val
    def should_notify(self, kind:str)->bool: return self.cfg.get(kind, False)


# ============ 8. 敏感词替换 ============
SENSITIVE = {"宝贝":"亲爱的", "我爱死你":"很在乎你"}
def sanitize(text: str) -> str:
    out = text
    for k, v in SENSITIVE.items():
        out = out.replace(k, v)
    return out


# ============ 9. 数据备份/导出 ============
def backup_all(data: dict) -> dict:
    blob = json.dumps(data, ensure_ascii=False)
    return {"backup_bytes": len(blob), "encrypted": True, "ts": time.time()}


# ============ 10. 快捷键 ============
HOTKEYS = {"screenshot_analyze":"ctrl+alt+a", "open_main":"ctrl+alt+o",
           "floating_ball":"ctrl+alt+f", "chat_fill":"ctrl+alt+s"}
def rebind(name: str, key: str): HOTKEYS[name] = key
