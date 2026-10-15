# -*- coding: utf-8 -*-
"""
LoveAI Gateway — FastAPI 应用入口。

启动流程 (lifespan):
  1. load_config("config.yaml")
  2. 建 Database(data_dir/gateway.db)
  3. 建 Engine(cfg, db)
  4. 遍历 cfg.providers, build_provider + engine.register_provider
  5. 启动 catalog 自动采集后台任务 (每 24h)
  6. 挂 /v1/* 路由 (OpenAI 兼容) + /admin/* 路由 + / 静态仪表盘

self-hosted 场景: CORS 全开, 方便本机前端跨域访问。
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import GatewayConfig, load_config, ProviderConfig
from app.core.engine import Engine
from app.db import Database
from app.providers.registry import build_provider, FREE_PROVIDER_CATALOG, catalog_to_provider_config
from app.providers.cherry_loader import build_provider_configs as build_cherry_provider_configs
from app.routes import admin as admin_routes
from app.routes import chat as chat_routes
from app.routes import models as models_routes

logger = logging.getLogger("loveai.gateway")

BASE_DIR = Path(__file__).resolve().parent
DASHBOARD_HTML = BASE_DIR / "web" / "dashboard.html"


# --------------------------------------------------------------------------- #
# Catalog 自动采集后台任务 (每 24h 拉一次各 provider 的 /models)
# --------------------------------------------------------------------------- #
async def refresh_catalog_once(cfg: GatewayConfig, db: Database, engine: Engine) -> None:
    """遍历已注册 provider, 尝试 GET {base_url}/models, 结果写回 models_catalog 表。"""
    now = __import__("time").time()
    for prov_name, prov in engine.all_providers().items():
        pcfg = getattr(prov, "config", None)
        if pcfg is None or not getattr(pcfg, "base_url", ""):
            continue
        key = pcfg.resolved_api_key() if hasattr(pcfg, "resolved_api_key") else ""
        url = pcfg.base_url.rstrip("/") + "/models"
        try:
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url, headers=headers)
            if r.status_code != 200:
                continue
            data = r.json()
            items = data.get("data", data if isinstance(data, list) else [])
            with db.cursor() as cur:
                for it in items:
                    mid = it.get("id") or it.get("name")
                    if not mid:
                        continue
                    cur.execute(
                        """INSERT INTO models_catalog
                           (provider, model_name, context_window, cost_per_1k_in,
                            cost_per_1k_out, rpm_limit, tpm_limit, is_free,
                            raw_meta, updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(provider, model_name) DO UPDATE SET
                             updated_at=excluded.updated_at,
                             raw_meta=excluded.raw_meta""",
                        (
                            prov_name, mid,
                            it.get("context_window"),
                            float(it.get("pricing", {}).get("prompt", 0) or 0),
                            float(it.get("pricing", {}).get("completion", 0) or 0),
                            None, None,
                            1 if "free" in mid.lower() else 0,
                            __import__("json").dumps(it, ensure_ascii=False),
                            now,
                        ),
                    )
            logger.info("[catalog] refreshed %s: %d models", prov_name, len(items))
        except Exception as e:  # noqa: BLE001
            logger.warning("[catalog] %s failed: %s", prov_name, e)


async def catalog_refresh_loop(cfg: GatewayConfig, db: Database, engine: Engine) -> None:
    """每 24h 跑一次 catalog 采集。"""
    # 启动后先跑一次 (非阻塞, 5s 后)
    await asyncio.sleep(5.0)
    while True:
        try:
            await refresh_catalog_once(cfg, db, engine)
        except Exception as e:  # noqa: BLE001
            logger.warning("[catalog] refresh error: %s", e)
        await asyncio.sleep(24 * 3600)


# --------------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    cfg = load_config("config.yaml")
    db_path = Path(cfg.data_dir) / "gateway.db"
    db = Database(db_path)
    engine = Engine(cfg, db)

    # 注册所有 yaml 里声明的 provider
    for p in cfg.providers:
        try:
            prov = build_provider(p)
            engine.register_provider(p.name, prov)
            logger.info("[startup] provider registered: %s (%s)", p.name, p.type)
        except Exception as e:  # noqa: BLE001
            logger.warning("[startup] failed to build provider %s: %s", p.name, e)

    # 自动加载零配置免费 provider（已验证可用的）
    # 不用用户手动写 config.yaml，启动就能直接用
    auto_registered = 0
    for entry in FREE_PROVIDER_CATALOG:
        # 只自动注册：零配置 + 已验证可用 + 不在yaml里重复
        if not entry.get("is_zero_config"):
            continue
        if entry.get("type") == "unsupported":
            continue
        if entry.get("verified_status") not in ("verified_ok",):
            continue
        name = entry["name"]
        if cfg.provider_by_name(name) is not None:
            continue  # yaml里已经有了，不重复
        try:
            pcfg = catalog_to_provider_config(entry)
            prov = build_provider(pcfg)
            engine.register_provider(name, prov)
            cfg.providers.append(pcfg)  # 关键：加到cfg.providers里，router才能找到
            auto_registered += 1
            logger.info("[startup] auto-registered zero-config provider: %s (%s)", name, entry.get("verified_note", ""))
        except Exception as e:  # noqa: BLE001
            logger.warning("[startup] failed to auto-register %s: %s", name, e)
    if auto_registered > 0:
        logger.info("[startup] %d zero-config providers auto-loaded", auto_registered)

    # 注册 Cherry Studio 提取的 61 家厂商 base_url (默认 disabled, 待用户贴 Key)
    # 这些 provider 进路由表但不参与实际路由 (enabled=False), 用户通过 /admin/keys
    # 贴 Key 识别后, 由 admin 路由把对应条目 enable 并写入 api_key。
    cherry_registered = 0
    for pcfg in build_cherry_provider_configs():
        if cfg.provider_by_name(pcfg.name) is not None:
            continue  # yaml 或零配置自动注册里已有同名
        try:
            prov = build_provider(pcfg)
            engine.register_provider(pcfg.name, prov)
            cfg.providers.append(pcfg)
            cherry_registered += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("[startup] failed to register cherry provider %s: %s", pcfg.name, e)
    logger.info("[startup] %d cherry-studio providers registered (disabled, awaiting key)",
                cherry_registered)

    app.state.cfg = cfg
    app.state.db = db
    app.state.engine = engine

    # 后台 catalog 采集
    catalog_task = asyncio.create_task(catalog_refresh_loop(cfg, db, engine))
    logger.info("[startup] gateway ready on %s:%d", cfg.host, cfg.port)
    try:
        yield
    finally:
        catalog_task.cancel()
        try:
            await catalog_task
        except (asyncio.CancelledError, Exception):
            pass


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
app = FastAPI(
    title="LoveAI Gateway",
    version="0.1.0",
    description="多模型聚合网关 (OpenAI 兼容)",
    lifespan=lifespan,
)

# CORS 全开 (self-hosted 方便)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
app.include_router(chat_routes.router)
app.include_router(models_routes.router)
app.include_router(admin_routes.router)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    """仪表盘首页。"""
    return FileResponse(DASHBOARD_HTML)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True}
