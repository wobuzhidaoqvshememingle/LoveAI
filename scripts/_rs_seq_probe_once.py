# -*- coding: utf-8 -*-
"""一次性串行探针：绕开 pollinations 30s/IP 限流，拿到 3 块+1 校验的真实输出。"""
from __future__ import annotations
import os, sys, time, tempfile
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
for s in (sys.stdout, sys.stderr):
    try: s.reconfigure(encoding="utf-8")
    except Exception: pass

from services.free_model_router import FreeModelRouter
from services.rs_consensus import RSConsensusK3M1

cfg = {
    "llm.free.zero_config.enabled": "true",
    "llm.free.include_ollama": "false",
    "llm.free.auto_catalog": "false",
    "llm.free.nvidia_catalog_md": "false",
    "llm.free.auto_discover_bridge": "false",
    "llm.free.local_scan.enabled": "false",
    "llm.free.desktop_ai.enabled": "false",
    "llm.free.keychannels.enabled": "false",
    "llm.free.state_dir": tempfile.mkdtemp(prefix="rs_seq_probe_"),
}
router = FreeModelRouter(cfg)

PROMPT = "用一句话解释什么是里德所罗门（Reed-Solomon）纠错码。"

# 用 pollinations 上当前还能用的两个模型；串行 + 30s sleep 绕开限流。
def block(model, sleep_after=0.0):
    def fn(prompt):
        cands = router.candidates()
        cand = next(c for c in cands if c.model == model)
        out = RSConsensusK3M1._call_candidate(
            cand,
            "你是 RS 共识的数据块生成器。简洁、准确、事实性回答。不要客套。",
            prompt,
            temperature=0.3, max_tokens=200, timeout=45.0,
        )
        if sleep_after:
            time.sleep(sleep_after)
        return out
    return fn

def judge_fn(prompt, blocks):
    lines = [f"用户问题：{prompt}", ""]
    for i, b in enumerate(blocks):
        if isinstance(b, str):
            lines.append(f"[候选 {i}]\n{b}\n")
        else:
            lines.append(f"[候选 {i}] ERROR: {b}\n")
    user_msg = "\n".join(lines)
    cands = router.candidates()
    cand = next(c for c in cands if c.model == "openai")
    raw = RSConsensusK3M1._call_candidate(
        cand,
        "你是 RS 独立校验方。只输出一行：INDEX=<0|1|2> REASON=<一句话理由>",
        user_msg, temperature=0.0, max_tokens=200, timeout=45.0,
    )
    import re
    m = re.search(r"INDEX\s*=\s*([012])", raw)
    idx = int(m.group(1)) if m else 0
    r = re.search(r"REASON\s*=\s*(.+?)(?:\n|$)", raw, re.DOTALL)
    reason = r.group(1).strip() if r else raw[:200]
    return idx, reason

# 串行：每块之间 sleep 30s 绕开 pollinations 1 req/30s/IP 限流
rs = RSConsensusK3M1(
    block("openai-fast", sleep_after=30),
    block("openai",       sleep_after=30),
    block("openai-fast",  sleep_after=30),
    judge_fn,
    block_timeout=300.0,
    max_workers=1,   # 串行，避免并发触发限流
)
print("串行 RS 真跑（每块后 sleep 30s 绕限流，预计 ~2 分钟）...")
t0 = time.time()
out = rs.run(PROMPT)
print(f"\n耗时 {time.time()-t0:.1f}s")
print("-" * 78)
for i, b in enumerate(out["blocks"]):
    tag = ["openai-fast", "openai", "openai-fast"][i]
    if isinstance(b, str):
        print(f"[block {i}] ({tag})")
        print(f"  {b}")
    else:
        print(f"[block {i}] ({tag}) ERROR: {b}")
print()
print(f"chosen_index = {out['chosen_index']}")
print(f"agreed       = {out['agreed']}")
print(f"judge_reason = {out['judge_reason']}")
