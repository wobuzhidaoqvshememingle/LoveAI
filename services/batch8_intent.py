# -*- coding: utf-8 -*-
"""第八批第三批：带意图截图/上下文仪表盘/多模块并行。纯函数，向后兼容。"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

# ============ 11. 带意图截图分析 ============
def classify_screenshot(text: str) -> str:
    t = text or ""
    if any(k in t for k in ("转账","￥","红包","收款")): return "转账"
    if any(k in t for k in ("朋友圈","赞","分享")): return "朋友圈"
    if ":" in t or "：" in t: return "聊天记录"
    return "其他"

def intent_screenshot(text: str) -> dict:
    kind = classify_screenshot(text)
    logic = {"转账":"财务/付出平衡","朋友圈":"展示面分析","聊天记录":"常规情感分析","其他":"通用"}
    return {"type": kind, "analysis_logic": logic.get(kind, "通用")}

# ============ 12. 上下文仪表盘 ============
def context_dashboard(parts: dict, limit: int = 4000) -> dict:
    total = sum(parts.values())
    compressed = False
    if total > limit:
        ratio = limit / total
        parts = {k: int(v*ratio) for k, v in parts.items()}
        compressed = True
    return {"parts": parts, "total_tokens": sum(parts.values()), "compressed": compressed}

# ============ 13. 多模块并行（同步隔离执行） ============
def parallel_analyzer(tasks: dict) -> dict:
    out = {}
    for name, fn in tasks.items():
        try: out[name] = fn()
        except Exception as e:
            logger.warning("并行模块 %s 失败: %s", name, e); out[name] = None
    return out
