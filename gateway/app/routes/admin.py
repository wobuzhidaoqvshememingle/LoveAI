# -*- coding: utf-8 -*-
"""
管理 API — /admin/*

- GET  /admin/providers           所有 provider 健康状态
- GET  /admin/usage               全局用量 + 最近 50 条日志
- POST /admin/keys                粘贴 Key -> 自动识别厂商 -> 密文落库
- GET  /admin/catalog             模型目录 + 免费厂商注册引导 (含 signup_url / free_tier_note)
- POST /admin/providers/{name}/disable  禁用某 provider
- POST /admin/providers/{name}/enable   启用某 provider
- GET  /admin/stats/summary       今日总请求数 / 总 token / 成功率
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core.engine import Engine
from app.db import Database
from app.providers.key_detect import (
    GENERIC_OPENAI_COMPATIBLE_CANDIDATES,
    build_provider_config,
    detect_by_prefix,
    looks_like_generic_sk,
)
from app.providers.registry import build_provider
from app.providers.cherry_loader import match_cherry_for_key
from app.security import encrypt

router = APIRouter(prefix="/admin", tags=["admin"])


# --------------------------------------------------------------------------- #
# 免费厂商目录 (硬编码 — 网关本身不发 Key, 用户自己去注册)
# 每项: name, type, signup_url, free_tier_note, default_base_url
# --------------------------------------------------------------------------- #
FREE_PROVIDER_CATALOG: list[dict] = [
    # ---- 零配置 (开箱即用, 无需注册/Key) ----
    {"name": "free-llm.com",         "type": "openai_compatible",    "signup_url": "https://free-llm.com/",                               "free_tier_note": "公共免费 OpenAI 兼容端点, 网关内置直连, 无需注册",   "default_base_url": "https://free-llm.com/v1",          "is_zero_config": True},
    {"name": "OVHcloud AI Endpoints","type": "openai_compatible",    "signup_url": "https://endpoints.ai.cloud.ovh.net/",                 "free_tier_note": "欧洲云厂商免费推理端点, 无需信用卡",                  "default_base_url": "https://endpoints.ai.cloud.ovh.net", "is_zero_config": True},
    {"name": "HuggingChat",          "type": "openai_compatible",    "signup_url": "https://huggingface.co/chat/",                        "free_tier_note": "HuggingFace 官方聊天, 网页即开即用",                "default_base_url": "https://huggingface.co/chat/",     "is_zero_config": True},
    {"name": "HuggingFace Inference", "type": "openai_compatible",    "signup_url": "https://huggingface.co/inference-endpoints/serverless", "free_tier_note": "Serverless 免费推理, 公共速率内免费用",            "default_base_url": "https://api-inference.huggingface.co/v1", "is_zero_config": True},

    # ---- 需注册 Key ----
    # 海外
    {"name": "Google Gemini",        "type": "gemini",               "signup_url": "https://aistudio.google.com/apikey",                  "free_tier_note": "AI Studio 永久免费额度, 约每天百万级 token",          "default_base_url": "https://generativelanguage.googleapis.com", "is_zero_config": False},
    {"name": "Groq",                "type": "groq",                 "signup_url": "https://console.groq.com/keys",                       "free_tier_note": "免费极速推理, 限速 RPM 较高, 适合日常",              "default_base_url": "https://api.groq.com/openai/v1", "is_zero_config": False},
    {"name": "Cerebras",            "type": "openai_compatible",   "signup_url": "https://cloud.cerebras.ai/",                          "free_tier_note": "免费层, 极快 LLaMA/Qwen 推理",                       "default_base_url": "https://api.cerebras.ai/v1", "is_zero_config": False},
    {"name": "NVIDIA NIM",          "type": "openai_compatible",   "signup_url": "https://build.nvidia.com/",                           "free_tier_note": "免费 credits, 托管 LLaMA/Mistral 等",               "default_base_url": "https://integrate.api.nvidia.com/v1", "is_zero_config": False},
    {"name": "Mistral",             "type": "openai_compatible",    "signup_url": "https://console.mistral.ai/",                         "free_tier_note": "新用户免费试用额度, La Plateforme 免费层",           "default_base_url": "https://api.mistral.ai/v1", "is_zero_config": False},
    {"name": "OpenRouter",          "type": "openai_compatible",    "signup_url": "https://openrouter.ai/keys",                          "free_tier_note": "部分模型免费 (:free 后缀), 一个 Key 调百家",         "default_base_url": "https://openrouter.ai/api/v1", "is_zero_config": False},
    {"name": "Cohere",              "type": "cohere",               "signup_url": "https://dashboard.cohere.com/api-keys",              "free_tier_note": "Trials 免费层, 约每月 1000 次调用",                  "default_base_url": "https://api.cohere.ai/compose/v1", "is_zero_config": False},
    {"name": "Cloudflare Workers AI","type": "cloudflare",           "signup_url": "https://dash.cloudflare.com/profile/api-tokens",     "free_tier_note": "每天 10000 次 Neurons 免费",                        "default_base_url": "https://api.cloudflare.com/client/v4", "is_zero_config": False},
    {"name": "Z.ai",                "type": "openai_compatible",    "signup_url": "https://z.ai/",                                       "free_tier_note": "GLM 系列海外版, 免费额度",                           "default_base_url": "https://api.z.ai/api/paas/v4", "is_zero_config": False},
    {"name": "HuggingFace",         "type": "openai_compatible",    "signup_url": "https://huggingface.co/settings/tokens",              "free_tier_note": "Inference API 免费层, 社区模型多",                   "default_base_url": "https://api-inference.huggingface.co/v1", "is_zero_config": False},
    {"name": "SambaNova",           "type": "openai_compatible",    "signup_url": "https://cloud.sambanova.ai/",                        "free_tier_note": "免费 credits, 高速 LLaMA 推理",                       "default_base_url": "https://api.sambanova.ai/v1", "is_zero_config": False},
    {"name": "GitHub Models",       "type": "openai_compatible",    "signup_url": "https://github.com/settings/personal-access-tokens", "free_tier_note": "GitHub 账号免费, 限率内免费用 GPT/Claude/Llama",     "default_base_url": "https://models.inference.ai.azure.com", "is_zero_config": False},
    {"name": "xAI",                 "type": "xai",                  "signup_url": "https://console.x.ai/",                               "free_tier_note": "新用户少量试用 credits",                             "default_base_url": "https://api.x.ai/v1", "is_zero_config": False},
    {"name": "Together AI",          "type": "openai_compatible",    "signup_url": "https://api.together.xyz/settings/api-keys",         "free_tier_note": "新用户 $5 免费额度",                                  "default_base_url": "https://api.together.xyz/v1", "is_zero_config": False},
    {"name": "Fireworks",           "type": "openai_compatible",    "signup_url": "https://fireworks.ai/account/api-keys",              "free_tier_note": "新用户 $1 免费额度, 速度快",                         "default_base_url": "https://api.fireworks.ai/inference/v1", "is_zero_config": False},
    {"name": "Perplexity",          "type": "openai_compatible",    "signup_url": "https://www.perplexity.ai/settings/api",             "free_tier_note": "Sonar 系列, 按量计费, 有少量免费额度",               "default_base_url": "https://api.perplexity.ai", "is_zero_config": False},
    {"name": "OpenAI",              "type": "openai_compatible",    "signup_url": "https://platform.openai.com/api-keys",               "free_tier_note": "官方平台, 需充值, 兼容所有 SDK",                       "default_base_url": "https://api.openai.com/v1", "is_zero_config": False},
    {"name": "Anthropic Claude",    "type": "anthropic",            "signup_url": "https://console.anthropic.com/",                     "free_tier_note": "新用户试用额度, Claude 系列质量高",                   "default_base_url": "https://api.anthropic.com", "is_zero_config": False},
    {"name": "Replicate",           "type": "openai_compatible",    "signup_url": "https://replicate.com/",                             "free_tier_note": "按量计费, 新用户少量免费额度",                         "default_base_url": "https://api.replicate.com/v1", "is_zero_config": False},
    {"name": "Anyscale",            "type": "openai_compatible",    "signup_url": "https://console.anyscale.com/",                      "free_tier_note": "Endpoints 托管, 有免费试用",                          "default_base_url": "https://api.endpoints.anyscale.com/v1", "is_zero_config": False},
    # ---- 国内 ----
    {"name": "硅基流动 SiliconFlow", "type": "openai_compatible",   "signup_url": "https://cloud.siliconflow.cn/account/ak",            "free_tier_note": "注册送 1400 万 token, 含 Qwen/DeepSeek 免费模型",   "default_base_url": "https://api.siliconflow.cn/v1", "is_zero_config": False},
    {"name": "智谱 GLM",            "type": "openai_compatible",    "signup_url": "https://open.bigmodel.cn/usercenter/apikeys",        "free_tier_note": "新用户送 tokens, GLM-4-Flash 永久免费",               "default_base_url": "https://open.bigmodel.cn/api/paas/v4", "is_zero_config": False},
    {"name": "腾讯混元",            "type": "openai_compatible",    "signup_url": "https://console.cloud.tencent.com/hunyuan/api-key",  "free_tier_note": "新用户免费额度, 混元系列",                             "default_base_url": "https://hunyuan.tencentcloudapi.com/v1", "is_zero_config": False},
    {"name": "百度文心",            "type": "openai_compatible",    "signup_url": "https://console.bce.baidu.com/qianfan/",             "free_tier_note": "新用户免费 tokens, ERNIE 系列",                        "default_base_url": "https://qianfan.baidubce.com/v2", "is_zero_config": False},
    {"name": "通义千问 DashScope",  "type": "openai_compatible",    "signup_url": "https://dashscope.console.aliyun.com/apiKey",        "free_tier_note": "新用户免费额度, qwen-turbo 长期免费",                 "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "is_zero_config": False},
    {"name": "DeepSeek",            "type": "openai_compatible",    "signup_url": "https://platform.deepseek.com/api_keys",             "free_tier_note": "官方直连, 价格极低, 新用户送额度",                     "default_base_url": "https://api.deepseek.com/v1", "is_zero_config": False},
    {"name": "Moonshot Kimi",       "type": "openai_compatible",    "signup_url": "https://platform.moonshot.cn/console/api-keys",      "free_tier_note": "新用户 15 元额度, 长上下文",                           "default_base_url": "https://api.moonshot.cn/v1", "is_zero_config": False},
    {"name": "字节火山方舟",        "type": "openai_compatible",    "signup_url": "https://console.volcengine.com/ark",                  "free_tier_note": "新用户送 tokens, 豆包/DeepSeek 托管",                  "default_base_url": "https://ark.cn-beijing.volces.com/api/v3", "is_zero_config": False},
    {"name": "零一万物 01.ai",      "type": "openai_compatible",    "signup_url": "https://platform.lingyiwanwu.com/",                  "free_tier_note": "新用户免费额度, Yi 系列",                              "default_base_url": "https://api.lingyiwanwu.com/v1", "is_zero_config": False},
    {"name": "MiniMax",             "type": "openai_compatible",    "signup_url": "https://platform.minimaxi.com/",                      "free_tier_note": "新用户 tokens, abab/MiniMax-Text",                    "default_base_url": "https://api.minimax.chat/v1", "is_zero_config": False},
    {"name": "讯飞星火",            "type": "openai_compatible",    "signup_url": "https://xinghuo.xfyun.cn/",                          "free_tier_note": "新用户免费额度, 星火系列",                             "default_base_url": "https://spark-api-open.xf-yun.com/v1", "is_zero_config": False},
    {"name": "昆仑万维天工",        "type": "openai_compatible",    "signup_url": "https://www.tiangong.cn/",                            "free_tier_note": "新用户免费额度, Sky 系列",                             "default_base_url": "https://api.tiangong.cn/v1", "is_zero_config": False},
    {"name": "百川智能",            "type": "openai_compatible",    "signup_url": "https://platform.baichuan-ai.com/",                   "free_tier_note": "新用户 tokens, Baichuan 系列",                        "default_base_url": "https://api.baichuan-ai.com/v1", "is_zero_config": False},
    {"name": "Ollama (本地)",       "type": "ollama",               "signup_url": "https://ollama.com/download",                        "free_tier_note": "完全本地, 无需 Key, 需自己下载模型",                   "default_base_url": "http://localhost:11434/v1", "is_zero_config": False},
]


# --------------------------------------------------------------------------- #
# 依赖
# --------------------------------------------------------------------------- #
def get_engine(request: Request) -> Engine:
    return request.app.state.engine  # type: ignore[no-any-return]


def get_db(request: Request) -> Database:
    return request.app.state.db  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #
class AddKeyRequest(BaseModel):
    api_key: str


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
@router.get("/providers")
async def list_providers(engine: Engine = Depends(get_engine)) -> list[dict]:
    """所有 provider 健康状态, 并补上 is_zero_config / signup_url 等引导字段。"""
    report = await engine.health_report()
    zero_map: dict[str, bool] = {
        p["name"]: bool(p.get("is_zero_config", False)) for p in FREE_PROVIDER_CATALOG
    }
    signup_map: dict[str, str] = {p["name"]: p["signup_url"] for p in FREE_PROVIDER_CATALOG}
    note_map: dict[str, str] = {p["name"]: p["free_tier_note"] for p in FREE_PROVIDER_CATALOG}
    for item in report:
        name = item.get("provider") or item.get("name") or ""
        item["is_zero_config"] = zero_map.get(name, False)
        item["signup_url"] = signup_map.get(name, "")
        item["free_tier_note"] = note_map.get(name, "")
    return report


@router.get("/usage")
async def usage(
    engine: Engine = Depends(get_engine),
    db: Database = Depends(get_db),
) -> dict:
    """全局用量 + 最近 50 条日志。"""
    return {
        "usage": await engine.usage_report(),
        "recent_logs": db.recent_logs(50),
    }


@router.post("/keys")
async def add_key(
    body: AddKeyRequest,
    engine: Engine = Depends(get_engine),
    db: Database = Depends(get_db),
) -> dict:
    """粘贴一个 Key, 自动识别厂商 -> 动态注册进路由表 -> 密文落库。

    注册策略:
      1. 前缀精确命中 (anthropic/gemini/groq/xai/openrouter/zhipu) -> 用指纹 base_url
      2. 模糊 sk- -> 并发探测候选 base_url 的 GET /models, 200 即命中
      3. 命中后: 若已有同名 provider (含 cherry_ 预注册条目) 则更新 api_key 并 enable;
         否则新建 ProviderConfig + build_provider + engine.register_provider
      4. 探测一次 /models 把模型列表填进 config.models (路由层据此匹配)
    """
    raw = (body.api_key or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="api_key 不能为空")

    provider_type = "openai_compatible"
    provider_name = ""
    base_url = ""
    note = ""
    matched_by = "unknown"
    probed_models: list[str] = []

    # 1) 前缀精确匹配
    fp = detect_by_prefix(raw)
    if fp is not None:
        provider_type = fp.provider_type
        provider_name = fp.provider_name
        base_url = fp.default_base_url
        note = fp.note
        matched_by = "prefix"
        probed_models = await _probe_model_list(base_url, raw)
    elif looks_like_generic_sk(raw):
        # 2) 模糊 sk- 并发探测
        probed = await _probe_generic_candidates(raw)
        if probed is not None:
            provider_name, base_url = probed
            matched_by = "probe"
            note = "通过 /models 探测命中"
            probed_models = await _probe_model_list(base_url, raw)
        else:
            note = "无法自动识别, 请手动在 config.yaml 配置 base_url"
            matched_by = "unresolved"
    else:
        note = "Key 格式无法识别"
        matched_by = "unknown"

    # 3) 密文落库 (无论是否识别成功都存一份)
    cipher = encrypt(raw, data_dir=engine.cfg.data_dir,
                     master_key_env=engine.cfg.security.master_key_env)
    now = time.time()
    with db.cursor() as cur:
        cur.execute(
            """INSERT OR REPLACE INTO discovered_keys
               (api_key_cipher, provider_type, provider_name, base_url,
                models, detected_at, last_health_ok)
               VALUES (?,?,?,?,?,?,?)""",
            (cipher, provider_type, provider_name or "unknown", base_url,
             json.dumps(probed_models, ensure_ascii=False), now,
             1 if matched_by in ("prefix", "probe") else 0),
        )

    # 4) 动态注册到运行时路由表
    registered_name = ""
    routing_status = "not_registered"
    if matched_by in ("prefix", "probe") and base_url:
        existing = engine.get_provider(provider_name)
        if existing is not None:
            # 已有同名 provider -> 更新 key + enable
            existing.config.api_key = raw
            existing.config.base_url = base_url.rstrip("/")
            if probed_models:
                existing.config.models = probed_models
            existing.config.enabled = True
            registered_name = provider_name
            routing_status = "updated_existing"
        else:
            # 看是否命中预注册的 cherry_ 条目
            cherry_pcfg = match_cherry_for_key(provider_name, base_url)
            if cherry_pcfg is not None and engine.get_provider(cherry_pcfg.name) is not None:
                inst = engine.get_provider(cherry_pcfg.name)
                inst.config.api_key = raw
                inst.config.base_url = base_url.rstrip("/")
                if probed_models:
                    inst.config.models = probed_models
                inst.config.enabled = True
                registered_name = cherry_pcfg.name
                routing_status = "enabled_cherry"
            else:
                # 全新注册
                pcfg = build_provider_config(
                    provider_type=provider_type,
                    provider_name=provider_name,
                    base_url=base_url,
                    api_key=raw,
                    models=probed_models,
                )
                try:
                    prov = build_provider(pcfg)
                    engine.register_provider(pcfg.name, prov)
                    engine.cfg.providers.append(pcfg)
                    registered_name = pcfg.name
                    routing_status = "registered_new"
                except Exception as e:  # noqa: BLE001
                    routing_status = f"register_failed: {e}"

    return {
        "ok": True,
        "detected": {
            "provider_type": provider_type,
            "provider_name": provider_name,
            "base_url": base_url,
            "note": note,
            "matched_by": matched_by,
            "models": probed_models,
        },
        "routing": {
            "registered_name": registered_name,
            "status": routing_status,
        },
        "hint": ("Key 已加密保存并动态注册到路由表, 立即生效。"
                 if routing_status.startswith(("registered", "updated", "enabled"))
                 else "Key 已保存, 但未能自动路由, 请检查 base_url。"),
    }


async def _probe_generic_candidates(api_key: str) -> Optional[tuple[str, str]]:
    """并发试候选 base_url 的 GET /models, 第一个 200 即返回 (name, base_url)。"""
    async with httpx.AsyncClient(timeout=6.0) as client:
        async def _try(name: str, url: str):
            try:
                r = await client.get(
                    f"{url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if r.status_code == 200:
                    return (name, url)
            except Exception:
                return None
            return None

        tasks = [_try(n, u) for n, u in GENERIC_OPENAI_COMPATIBLE_CANDIDATES]
        for coro in asyncio.as_completed(tasks):
            res = await coro
            if res is not None:
                return res
    return None


async def _probe_model_list(base_url: str, api_key: str) -> list[str]:
    """GET {base_url}/models, 返回模型 id 列表 (失败返回空)。"""
    url = base_url.rstrip("/") + "/models"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            if r.status_code != 200:
                return []
            data = r.json()
    except Exception:
        return []
    items = data.get("data") if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out: list[str] = []
    for it in items or []:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("name")
            if mid:
                out.append(str(mid))
    return out[:50]  # 最多存 50 个, 够路由用


@router.get("/catalog")
async def catalog(db: Database = Depends(get_db)) -> dict:
    """模型目录 (从 models_catalog 表) + 免费厂商注册引导清单。"""
    with db.cursor() as cur:
        cur.execute(
            """SELECT provider, model_name, context_window, cost_per_1k_in,
                      cost_per_1k_out, rpm_limit, tpm_limit, is_free, updated_at
               FROM models_catalog ORDER BY provider, model_name"""
        )
        models = [dict(r) for r in cur.fetchall()]

    return {
        "models": models,
        "free_providers": FREE_PROVIDER_CATALOG,
    }


@router.post("/providers/{name}/disable")
async def disable_provider(
    name: str,
    engine: Engine = Depends(get_engine),
) -> dict:
    prov = engine.get_provider(name)
    if prov is None:
        raise HTTPException(status_code=404, detail=f"provider {name} not found")
    prov.config.enabled = False
    return {"ok": True, "name": name, "enabled": False}


@router.post("/providers/{name}/enable")
async def enable_provider(
    name: str,
    engine: Engine = Depends(get_engine),
) -> dict:
    prov = engine.get_provider(name)
    if prov is None:
        raise HTTPException(status_code=404, detail=f"provider {name} not found")
    prov.config.enabled = True
    return {"ok": True, "name": name, "enabled": True}


@router.get("/stats/summary")
async def stats_summary(db: Database = Depends(get_db)) -> dict:
    """今日总请求数 / 总 token / 成功率。"""
    day_start = time.mktime(
        time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")
    )
    with db.cursor() as cur:
        cur.execute(
            """SELECT
                 COUNT(*)                                              AS reqs,
                 SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)     AS oks,
                 SUM(prompt_tokens + completion_tokens)                AS toks,
                 SUM(prompt_tokens)                                    AS ptoks,
                 SUM(completion_tokens)                                AS ctoks
               FROM request_logs WHERE ts >= ?""",
            (day_start,),
        )
        row = cur.fetchone()
    reqs = int(row["reqs"] or 0)
    oks = int(row["oks"] or 0)
    return {
        "day": time.strftime("%Y-%m-%d"),
        "total_requests": reqs,
        "total_tokens": int(row["toks"] or 0),
        "prompt_tokens": int(row["ptoks"] or 0),
        "completion_tokens": int(row["ctoks"] or 0),
        "success_rate": (oks / reqs) if reqs else 0.0,
    }


@router.get("/desktop-apps")
async def desktop_apps() -> dict:
    """检测本机安装了哪些 AI 桌面端软件 (Windows only)。"""
    try:
        from app.providers.desktop_detect import detect_installed_apps, KNOWN_DESKTOP_APPS
        installed = detect_installed_apps()
        return {
            "installed": [{"name": a.name, "exe_path": a.exe_path} for a in installed],
            "known": [{"name": a["name"], "exe_hint": a["exe_hint"]} for a in KNOWN_DESKTOP_APPS],
            "platform": "windows",
        }
    except Exception as e:
        return {"installed": [], "known": [], "error": str(e)}
