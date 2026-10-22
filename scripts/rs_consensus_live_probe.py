# -*- coding: utf-8 -*-
r"""P0 卡11：RS K=3+M=1 多模型竞速共识 真跑探针（真联网，不 mock）。

目标：在真实免费通道（pollinations 零配置免 Key）上跑一次完整的
RS 共识：3 个数据块模型并发答同一道题 → 第 4 个模型做 cross-check 裁决。

不发真网络请求的场景：如果候选池为空 / 所有候选都连不上，脚本会清晰
打印"无可用通道，跳过真跑"，退出码 0（不视为失败）。

用法（工程根、venv python）：
    .\.venv\Scripts\python.exe scripts\rs_consensus_live_probe.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


PROMPT = "用一句话解释什么是里德所罗门（Reed-Solomon）纠错码。"

# 选 3 个免 Key 的不同模型做数据块；校验方用第 4 个。
# 实测（2026-10-22）pollinations Classic API 已下架 llama/mistral，且 1 req/30s/IP
# 限流；llm7 零配置池有 4 个真模型，更稳。
BLOCK_MODELS = ["DeepSeek-V4-Flash-0731", "gpt-4o-mini", "llama-3.3-70b"]
JUDGE_MODEL = "claude-3-5-sonnet"   # 用第 4 个做独立校验方


def _isolated_cfg() -> dict:
    """隔离配置：只留 pollinations 零配置通道，不碰 ollama/桥接/OpenRouter。"""
    return {
        "llm.free.zero_config.enabled": "true",
        "llm.free.include_ollama": "false",
        "llm.free.auto_catalog": "false",
        "llm.free.nvidia_catalog_md": "false",
        "llm.free.auto_discover_bridge": "false",
        "llm.free.local_scan.enabled": "false",
        "llm.free.desktop_ai.enabled": "false",
        "llm.free.keychannels.enabled": "false",
        "llm.free.state_dir": tempfile.mkdtemp(prefix="rs_live_probe_"),
    }


class _ShimGateway:
    """薄壳：只暴露 from_llm_gateway 需要的 get_free_router()。

    直接用 FreeModelRouter(cfg_dict)，不绕 LLMGateway（避免 L1/L2/本地模型
    等无关依赖被加载）。from_llm_gateway 是鸭子类型，只调 get_free_router()。
    """
    def __init__(self, router):
        self._router = router

    def get_free_router(self):
        return self._router


def main() -> int:
    from services.free_model_router import FreeModelRouter
    from services.rs_consensus import RSConsensusK3M1

    print("=" * 78)
    print("RS K=3+M=1 多模型竞速共识 · 真跑探针")
    print("=" * 78)
    cfg = _isolated_cfg()
    router = FreeModelRouter(cfg)
    try:
        router.probe_all(budget_sec=3.0)
    except Exception as e:  # noqa: BLE001
        print(f"[probe] 候选池探活异常（忽略，继续）: {e}")

    cands = router.candidates()
    print(f"[probe] 当前候选池 = {[f'{c.provider}:{c.model}' for c in cands]}")

    # 检查我们要的 4 个模型是否都在候选池里
    wanted = list(BLOCK_MODELS) + [JUDGE_MODEL]
    missing = [m for m in wanted
               if not any(m == c.model or m in c.model for c in cands)]
    if missing:
        print(f"[probe] 缺少可用候选模型: {missing}")
        print("[probe] 无可用通道，跳过真跑（不视为失败）。")
        return 0
    if len(cands) < 4:
        print(f"[probe] 候选池只有 {len(cands)} 个，不够 3 块+1 校验，跳过真跑。")
        return 0

    gw = _ShimGateway(router)
    rs = RSConsensusK3M1.from_llm_gateway(
        gw,
        block_models=BLOCK_MODELS,
        judge_model=JUDGE_MODEL,
        block_timeout=45.0,
        max_tokens=300,
    )

    print(f"\n[probe] prompt = {PROMPT!r}")
    print(f"[probe] 数据块模型 = {BLOCK_MODELS}")
    print(f"[probe] 校验模型   = {JUDGE_MODEL}")
    t0 = time.time()
    out = rs.run(PROMPT)
    dt = time.time() - t0

    print("\n" + "-" * 78)
    print("RS 共识输出")
    print("-" * 78)
    for i, b in enumerate(out["blocks"]):
        if isinstance(b, str):
            print(f"[block {i}] ({BLOCK_MODELS[i]})")
            print(f"  {b}")
        else:
            print(f"[block {i}] ({BLOCK_MODELS[i]}) ERROR: {b}")
    print()
    print(f"chosen_index = {out['chosen_index']}  "
          f"({BLOCK_MODELS[out['chosen_index']] if out['chosen_index'] < 3 else '?'})")
    print(f"agreed       = {out['agreed']}")
    print(f"judge_reason = {out['judge_reason']}")
    print(f"耗时         = {dt:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
