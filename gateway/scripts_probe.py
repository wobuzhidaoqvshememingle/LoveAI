# -*- coding: utf-8 -*-
"""实测每个零配置 provider 的可达性。结果写回 registry.py 的 catalog。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from app.config import ProviderConfig
from app.providers.registry import FREE_PROVIDER_CATALOG, build_provider


async def probe_one(entry: dict[str, Any]) -> dict[str, Any]:
    name = entry.get("name", "?")
    base_url = entry.get("default_base_url", "")
    transport = entry.get("transport", "api")
    is_zero = entry.get("is_zero_config", False)
    if not is_zero:
        return {"name": name, "status": "skip_requires_key"}
    if transport in ("web", "browser", "ssh"):
        return {"name": name, "status": f"skip_{transport}", "detail": "非 HTTP 端点, 需浏览器/SSH"}
    if not base_url:
        return {"name": name, "status": "skip_no_url", "detail": "无 base_url"}
    # 试 GET {base_url}/models (无 key)
    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            headers = {"Authorization": "Bearer none"}
            r = await client.get(url, headers=headers)
            return {
                "name": name,
                "status": "ok" if r.status_code < 400 else f"http_{r.status_code}",
                "url": url,
                "latency_ms": int(r.elapsed.total_seconds() * 1000),
                "detail": (r.text or "")[:120].replace("\n", " "),
            }
    except Exception as e:
        return {
            "name": name,
            "status": "error",
            "url": url,
            "detail": f"{type(e).__name__}: {e}",
        }


async def main():
    print(f"=== 测试 {len(FREE_PROVIDER_CATALOG)} 条 catalog ===")
    results = []
    for entry in FREE_PROVIDER_CATALOG:
        r = await probe_one(entry)
        results.append(r)
        flag = {"ok": "✓", "error": "✗"}.get(r["status"], "·")
        print(f"  {flag} {r['name']:24s} {r['status']:15s} {r.get('detail','')[:80]}")
    ok = [r for r in results if r["status"] == "ok"]
    bad = [r for r in results if r["status"].startswith("http_") or r["status"] == "error"]
    skip = [r for r in results if r["status"].startswith("skip")]
    print(f"\n汇总: ✓ 可用 {len(ok)} | ✗ 不可用 {len(bad)} | · 跳过 {len(skip)}")
    return results


if __name__ == "__main__":
    asyncio.run(main())
