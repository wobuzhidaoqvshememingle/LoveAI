# -*- coding: utf-8 -*-
"""第八批第二批：Token统计/会话恢复/分析断点/分析历史/分身管理接口。纯函数，向后兼容。"""
from __future__ import annotations
import logging, time
logger = logging.getLogger(__name__)

# ============ 6. Token消耗统计+成本 ============
class TokenTracker:
    def __init__(self): self.records = []
    def log(self, module: str, in_tok: int, out_tok: int):
        self.records.append({"module":module,"in":in_tok,"out":out_tok,"ts":time.time()})
    def summary(self) -> dict:
        tot_in = sum(r["in"] for r in self.records)
        tot_out = sum(r["out"] for r in self.records)
        return {"total_in":tot_in, "total_out":tot_out,
                "cost_usd": round((tot_in*0.000001)+(tot_out*0.000002),4)}

# ============ 7. 会话恢复 ============
class SessionManager:
    def __init__(self): self._sessions = {}
    def save(self, pid: str, data: dict): self._sessions[pid] = {"data":data,"ts":time.time()}
    def load(self, pid: str) -> dict | None:
        s = self._sessions.get(pid)
        return s["data"] if s else None
    def has_resume(self, pid: str) -> bool: return pid in self._sessions

# ============ 8. 分析断点 ============
class AnalysisCheckpoint:
    def __init__(self): self.done = set()
    def mark(self, step: str): self.done.add(step)
    def skip(self, step: str) -> bool: return step in self.done
    def reset(self): self.done.clear()

# ============ 9. 分析历史 ============
class AnalysisHistory:
    def __init__(self): self.items = []
    def add(self, pid: str, summary: str):
        self.items.append({"pid":pid,"summary":summary,"ts":time.time()})
    def list_for(self, pid: str) -> list:
        return [i for i in self.items if i["pid"] == pid]

# ============ 10. 分身管理接口 ============
def get_twin_status(mode: str = "partner") -> dict:
    return {"mode": mode, "persona_loaded": True, "current_emotion": "未知"}
def switch_twin_mode(mode: str) -> dict:
    return {"mode": mode, "switched": True}
