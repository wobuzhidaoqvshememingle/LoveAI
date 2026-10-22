# -*- coding: utf-8 -*-
r"""零配置免费 provider 真实可用性实测（真联网，逐条打印原始响应片段）。

三件事：
  A. 直接请求 pollinations / llm7 的 OpenAI 兼容端点各一次 —— 用与
     ``FreeModelRouter._ZERO_CONFIG_API`` 完全一致的 base_url / 占位 key / 默认模型，
     证明这两个「零配置免 Key」渠道本身真的能出文本；
  B. 把候选池**隔离到只剩这两个零配置 provider**，经 FreeModelRouter 真发一次
     chat，打印 routed_via / tried / 回复片段，证明接入后免费池端到端可用；
  C. 故意把 pollinations 的模型名覆盖成不存在的名字 → 它整条链路失败后，验证
     路由器自动 Fallback 到 llm7（「智能路由自动切换」的活证据）。

不伪造：任何一步失败都原样打印异常类型与消息。

用法（仓库根目录、项目 venv）::

    .\.venv\Scripts\python.exe scripts\free_router_live_probe.py
    .\.venv\Scripts\python.exe scripts\free_router_live_probe.py --skip-c
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

for _stream in (sys.stdout, sys.stderr):   # Windows 控制台/管道下保证中文不乱码
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

PROMPT = "请只回复两个字：收到"


def _snip(text, n: int = 200) -> str:
    return ((text or "").replace("\n", "\\n"))[:n]


def _isolated_cfg(extra: dict = None) -> dict:
    """隔离配置：只留两个零配置 provider，绝不碰本机 ollama/桥接/动态目录/OpenRouter。"""
    cfg = {
        "llm.free.zero_config.enabled": "true",
        "llm.free.include_ollama": "false",
        "llm.free.auto_catalog": "false",
        "llm.free.nvidia_catalog_md": "false",
        "llm.free.auto_discover_bridge": "false",
        "llm.free.local_scan.enabled": "false",
        "llm.free.desktop_ai.enabled": "false",
        "llm.free.state_dir": tempfile.mkdtemp(prefix="free_live_probe_"),
    }
    cfg.update(extra or {})
    return cfg


def part_a() -> None:
    from services.free_model_router import _ZERO_CONFIG_API
    import openai

    print("=" * 78)
    print("A. 直接实测零配置 provider（base_url + 占位 key + 默认模型，与路由完全一致）")
    print("=" * 78)
    for entry in _ZERO_CONFIG_API:
        model = entry["models"][0]
        print(f"\n[A] provider={entry['provider']}  model={model}")
        print(f"[A] base_url={entry['base_url']}  prompt={PROMPT!r}")
        t0 = time.time()
        try:
            client = openai.OpenAI(api_key="zero-config",
                                   base_url=entry["base_url"], timeout=45.0)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROMPT}],
                max_tokens=32,
            )
            dt = (time.time() - t0) * 1000
            text = (resp.choices[0].message.content or "").strip()
            print(f"[A] 成功 {dt:.0f}ms  原始 text = {_snip(text)!r}")
            print(f"[A] usage = {getattr(resp, 'usage', None)}")
        except Exception as e:  # noqa: BLE001 - 失败要原样报出来
            dt = (time.time() - t0) * 1000
            print(f"[A] 失败 {dt:.0f}ms  {type(e).__name__}: {e}")


def part_b() -> None:
    from services.free_model_router import FreeModelRouter

    print("\n" + "=" * 78)
    print("B. 隔离候选池后经 FreeModelRouter 真发一次 chat（端到端 + routed_via）")
    print("=" * 78)
    r = FreeModelRouter(_isolated_cfg())
    print("[B] 候选池 =", [f"{c.provider}:{c.model}" for c in r.candidates()])
    t0 = time.time()
    try:
        out = r.chat("你是实测助手。", PROMPT, max_tokens=32, timeout=45.0)
        print(f"[B] 命中 routed_via = {out.get('routed_via')}  "
              f"(provider={out.get('provider')} model={out.get('model')})")
        print(f"[B] tried = {out.get('tried')}")
        print(f"[B] 回复片段 = {_snip(out.get('text'))!r}")
        print(f"[B] 全程耗时 = {(time.time() - t0) * 1000:.0f}ms")
        snap = r.routing_snapshot()
        hit = [ch for ch in snap["channels"] if ch["key"] == out.get("routed_via")]
        print(f"[B] routing_snapshot.last_routed_via = {snap['last_routed_via']!r}")
        for ch in hit:
            print(f"[B] 命中通道快照: base={ch['base_priority']} penalty={ch['penalty']} "
                  f"score={ch['effective_score']} ok={ch['ok']} fail={ch['fail']} "
                  f"latency_ms={ch['latency_ms']} limits={ch['limits']}")
        print(f"[B] persistence = {snap['persistence']}")
    except Exception as e:  # noqa: BLE001
        print(f"[B] 失败（fail-closed，不会改道付费模型）: {type(e).__name__}: {e}")


def part_c() -> None:
    from services.free_model_router import FreeModelRouter

    print("\n" + "=" * 78)
    print("C. 把 pollinations 模型名覆盖成不存在的值 → 验证自动 Fallback 到 llm7")
    print("=" * 78)
    r = FreeModelRouter(_isolated_cfg({"llm.free.pollinations_models": "no-such-model-xyz"}))
    print("[C] 候选池 =", [f"{c.provider}:{c.model}" for c in r.candidates()])
    t0 = time.time()
    try:
        out = r.chat("你是实测助手。", PROMPT, max_tokens=32, timeout=45.0)
        print(f"[C] 自动切换结果 routed_via = {out.get('routed_via')}  "
              f"tried = {out.get('tried')}  ({(time.time() - t0) * 1000:.0f}ms)")
        print(f"[C] 回复片段 = {_snip(out.get('text'))!r}")
    except Exception as e:  # noqa: BLE001
        print(f"[C] 失败: {type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description="零配置免费 provider 真实可用性实测")
    ap.add_argument("--skip-a", action="store_true", help="跳过逐 provider 直连实测")
    ap.add_argument("--skip-b", action="store_true", help="跳过路由端到端实测")
    ap.add_argument("--skip-c", action="store_true", help="跳过自动 Fallback 实测")
    args = ap.parse_args()

    if not args.skip_a:
        part_a()
    if not args.skip_b:
        part_b()
    if not args.skip_c:
        part_c()
    print("\n[probe] 实测结束（以上全部为真实网络结果，未做任何修饰）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
