# -*- coding: utf-8 -*-
"""批量实测 keyless 端点的 chat completions, 真出文本才算数。"""
import asyncio
import json
import httpx

# (name, base_url, test_model)
CANDIDATES = [
    ("pollinations",       "https://text.pollinations.ai/openai", "openai"),
    ("llm7",              "https://api.llm7.io/v1",             "DeepSeek-V4-Flash-0731"),
    ("keylessai",         "https://keylessai.thryx.workers.dev/v1", "gpt-4o-mini"),
    ("kilo_gateway",      "https://api.kilo.ai/api/gateway",    "z-ai/glm-4.5-air"),
    ("opencode",          "https://console.opencode.ai/inference/openai/v1", "gpt-4o-mini"),
    ("glhf",              "https://glhf.chat/api/openai/v1",    "hf-moon/DeepSeek-V3-0324"),
    ("ovh_kepler",        "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "Qwen/Qwen2.5-72B-Instruct"),
    ("gonka",             "https://proxy.gonkabroker.com/v1",  "gpt-4o-mini"),
    ("airforce",          "https://api.airforce/v1",           "gpt-4o-mini"),
    ("bazaarlink",        "https://bazaarlink.ai/api/v1",      "gpt-4o-mini"),
    ("literouter",        "https://api.literouter.com/v1",     "gpt-4o-mini:free"),
    ("fastrouter",        "https://api.fastrouter.ai/api/v1",   "gpt-4o-mini:free"),
    ("naga",              "https://api.naga.ac/v1",            "gpt-4o-mini"),
    ("poixe",             "https://api.poixe.com/v1",          "gpt-4o-mini"),
    ("electronhub",       "https://api.electronhub.ai/v1",     "gpt-4o-mini:free"),
    ("helyxai",           "https://helyxai.space/v1",          "gpt-4o-mini"),
    ("freeinference",     "https://freeinference.org/v1",      "gpt-4o-mini"),
    ("intern_ai",         "https://chat.intern-ai.org.cn/api/v1", "gpt-4o-mini"),
    ("antling",           "https://api.ant-ling.com/v1",       "gpt-4o-mini"),
    ("anyapi",            "https://api.anyapi.ai/v1",          "gpt-4o-mini:free"),
    ("routeway",          "https://api.routeway.ai/v1",        "gpt-4o-mini:free"),
    ("morph",             "https://api.morphllm.com/v1",       "gpt-4o-mini"),
    ("publicai",          "https://api.publicai.co/v1",        "gpt-4o-mini"),
    ("llm_kiwi",          "https://api.llm.kiwi/v1",           "gpt-4o-mini"),
    ("nscale",            "https://inference.api.nscale.com/v1", "gpt-4o-mini"),
    ("poolside",          "https://inference.poolside.ai/v1",  "gpt-4o-mini"),
    ("mixlayer",          "https://models.mixlayer.ai/v1",     "qwen"),
    ("yolo_auto",         "https://yolo-auto.com/v1",          "qwen"),
    ("mnn",               "https://api.mnnai.ru/v1",           "gpt-4o-mini"),
    ("speka",             "https://speka.me/v1",               "gpt-4o-mini"),
    ("auriko",            "https://api.auriko.ai/v1",          "glm-4-flash"),
    ("helixmind",         "https://helixmind.online/v1",       "gpt-4o-mini"),
    ("tokenreply",        "https://api.tokenreply.com/v1",     "gpt-4o-mini"),
    ("longcat",           "https://api.longcat.chat/openai/v1", "gpt-4o-mini"),
    ("zylo",              "https://api.zyloai.net/v1",         "gpt-4o-mini"),
    ("voidai",            "https://api.voidai.app/v1",         "gpt-4o-mini"),
    ("vercel_gateway",    "https://ai-gateway.vercel.sh/v1",   "openai/gpt-4o-mini"),
]

PAYLOAD = {
    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    "max_tokens": 20,
    "temperature": 0.1,
}

async def test_one(client, name, base, model):
    url = base.rstrip("/") + "/chat/completions"
    body = dict(PAYLOAD, model=model)
    try:
        r = await client.post(url, json=body, headers={"Authorization": "Bearer none"})
        if r.status_code == 200:
            data = r.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            if content and len(content.strip()) > 0:
                return name, "OK", content.strip()[:60], r.status_code
            return name, "EMPTY", "200 but no content", r.status_code
        return name, f"HTTP_{r.status_code}", r.text[:120], r.status_code
    except Exception as e:
        return name, "ERR", f"{type(e).__name__}: {str(e)[:80]}", 0

async def main():
    async with httpx.AsyncClient(timeout=20.0) as client:
        tasks = [test_one(client, n, b, m) for n, b, m in CANDIDATES]
        results = await asyncio.gather(*tasks)
    ok = []
    bad = []
    for name, status, detail, code in results:
        if status == "OK":
            ok.append((name, detail))
            print(f"  ✅ {name:18s} {detail}")
        else:
            bad.append((name, status, detail))
            print(f"  ❌ {name:18s} {status:12s} {detail[:70]}")
    print(f"\n=== 真能用: {len(ok)} 个 ===")
    for n, d in ok:
        print(f"  {n}: {d}")

asyncio.run(main())
