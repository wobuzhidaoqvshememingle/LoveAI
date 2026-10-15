# -*- coding: utf-8 -*-
"""
模型目录自动采集。

启动时 + 每 24h, 对每个 enabled provider 调 GET {base_url}/models,
把返回的模型列表 upsert 到 models_catalog 表。失败不阻塞启动。

使用 httpx.AsyncClient, 超时从 ProviderConfig.timeout_sec 读。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import GatewayConfig, ProviderConfig
from app.db import Database

logger = logging.getLogger(__name__)


class ModelCatalog:
    REFRESH_INTERVAL_SEC = 24 * 3600

    def __init__(self, cfg: GatewayConfig, db: Database) -> None:
        self.cfg = cfg
        self.db = db
        self._last_refresh = 0.0
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ #
    # 单次刷新
    # ------------------------------------------------------------------ #
    async def refresh_provider(self, pcfg: ProviderConfig) -> int:
        """拉单个 provider 的 /models, 写库。返回写入条数。"""
        if not pcfg.base_url:
            return 0
        url = pcfg.base_url.rstrip("/") + "/models"
        key = pcfg.resolved_api_key()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with httpx.AsyncClient(timeout=pcfg.timeout_sec) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.warning("catalog %s GET %s -> %s", pcfg.name, url, resp.status_code)
                    return 0
                data = resp.json()
        except Exception as e:
            logger.warning("catalog %s fetch failed: %s", pcfg.name, e)
            return 0

        items: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            items = list(data.get("data") or data.get("models") or [])
        elif isinstance(data, list):
            items = list(data)

        now = time.time()
        written = 0
        with self.db.cursor() as cur:
            for it in items:
                if not isinstance(it, dict):
                    continue
                model_id = it.get("id") or it.get("model") or it.get("name")
                if not model_id:
                    continue
                context = it.get("context_window") or it.get("max_context")
                rpm = it.get("rpm_limit")
                tpm = it.get("tpm_limit")
                cost_in = it.get("cost_per_1k_in") or it.get("pricing", {}).get("prompt") or 0
                cost_out = it.get("cost_per_1k_out") or it.get("pricing", {}).get("completion") or 0
                is_free = 1 if (float(cost_in or 0) == 0 and float(cost_out or 0) == 0) else 0
                cur.execute(
                    """INSERT INTO models_catalog
                         (provider, model_name, context_window, cost_per_1k_in,
                          cost_per_1k_out, rpm_limit, tpm_limit, is_free, raw_meta, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(provider, model_name) DO UPDATE SET
                         context_window=excluded.context_window,
                         cost_per_1k_in=excluded.cost_per_1k_in,
                         cost_per_1k_out=excluded.cost_per_1k_out,
                         rpm_limit=excluded.rpm_limit,
                         tpm_limit=excluded.tpm_limit,
                         is_free=excluded.is_free,
                         raw_meta=excluded.raw_meta,
                         updated_at=excluded.updated_at""",
                    (
                        pcfg.name, model_id, context,
                        float(cost_in or 0), float(cost_out or 0),
                        rpm, tpm, is_free,
                        json.dumps(it, ensure_ascii=False), now,
                    ),
                )
                written += 1
        self._last_refresh = now
        return written

    async def refresh_all(self) -> Dict[str, int]:
        """并发刷新所有 enabled provider。失败不抛。"""
        enabled = [p for p in self.cfg.providers if p.enabled and p.base_url]
        results = await asyncio.gather(
            *[self.refresh_provider(p) for p in enabled],
            return_exceptions=True,
        )
        out: Dict[str, int] = {}
        for p, r in zip(enabled, results):
            out[p.name] = 0 if isinstance(r, Exception) else int(r)
        return out

    # ------------------------------------------------------------------ #
    # 后台循环
    # ------------------------------------------------------------------ #
    async def _loop(self) -> None:
        try:
            await self.refresh_all()
        except Exception as e:
            logger.warning("catalog initial refresh failed: %s", e)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.REFRESH_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass
            if self._stop.is_set():
                return
            try:
                await self.refresh_all()
            except Exception as e:
                logger.warning("catalog periodic refresh failed: %s", e)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list_models(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.db.cursor() as cur:
            if provider:
                cur.execute(
                    "SELECT * FROM models_catalog WHERE provider=? ORDER BY model_name",
                    (provider,),
                )
            else:
                cur.execute("SELECT * FROM models_catalog ORDER BY provider, model_name")
            return [dict(r) for r in cur.fetchall()]
