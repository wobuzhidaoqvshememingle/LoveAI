# -*- coding: utf-8 -*-
"""Probe the 13 anonymous endpoints."""
import asyncio
import httpx

ENDPOINTS = [
    ("blockrun", "https://blockrun.ai/api/v1/models"),
    ("llm7", "https://api.llm7.io/v1/models"),
    ("ovhcloud_kepler", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1/models"),
    ("kilo_gateway", "https://api.kilo.ai/api/gateway/models"),
    ("gonka_broker", "https://proxy.gonkabroker.com/v1/models"),
    ("api_airforce", "https://api.airforce/v1/models"),
    ("bazaarlink", "https://bazaarlink.ai/api/v1/models"),
    ("fastrouter", "https://api.fastrouter.ai/api/v1/models"),
    ("nagaai", "https://api.naga.ac/v1/models"),
    ("poixe", "https://api.poixe.com/v1/models"),
    ("electronhub", "https://api.electronhub.ai/v1/models"),
    ("freeinference", "https://freeinference.org/v1/models"),
    ("chutes", "https://llm.chutes.ai/v1/models"),
    ("modelscope", "https://api-inference.modelscope.cn/v1/models"),
    ("sambanova", "https://api.sambanova.ai/v1/models"),
    ("novita", "https://api.novita.ai/openai/models"),
    ("openrouter", "https://openrouter.ai/api/v1/models"),
    ("nvidia_nim", "https://integrate.api.nvidia.com/v1/models"),
    ("voidai", "https://api.voidai.app/v1/models"),
    ("zyloai", "https://api.zyloai.net/v1/models"),
]

async def probe(name, url):
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as c:
            r = await c.get(url, headers={"Authorization": "Bearer none"})
            return name, r.status_code, len(r.text)
    except Exception as e:
        return name, 0, type(e).__name__

async def main():
    results = await asyncio.gather(*[probe(n, u) for n, u in ENDPOINTS])
    for name, code, ln in sorted(results, key=lambda x: (x[1] == 200, -x[1])):
        flag = "✓" if code == 200 else ("·" if 300 <= code < 500 else "✗")
        print(f"  {flag} {name:20s} http={code} len={ln}")

asyncio.run(main())
