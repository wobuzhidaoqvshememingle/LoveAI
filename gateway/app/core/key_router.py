# -*- coding: utf-8 -*-
"""
Key 自动识别与探测。

流程:
  1. detect_by_prefix(raw_key) 精确命中 -> 直接返回指纹
  2. 没命中但 looks_like_generic_sk(raw_key) 为真:
     并发请求 GENERIC_OPENAI_COMPATIBLE_CANDIDATES 中每个 base_url 的
     GET /models (带 Authorization: Bearer <key>), 哪个 200 即命中
  3. 识别结果写 discovered_keys 表 (api_key 用 security.encrypt 密文存)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.config import GatewayConfig
from app.db import Database
from app.providers.key_detect import (
    GENERIC_OPENAI_COMPATIBLE_CANDIDATES,
    detect_by_prefix,
    looks_like_generic_sk,
)
from app.security import encrypt

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredKey:
    provider_type: str
    provider_name: str
    base_url: str
    models: List[str]
    raw_key: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_type": self.provider_type,
            "provider_name": self.provider_name,
            "base_url": self.base_url,
            "models": self.models,
        }


class KeyAutoDetector:
    def __init__(self, cfg: GatewayConfig, db: Database) -> None:
        self.cfg = cfg
        self.db = db

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    async def detect(self, raw_key: str) -> Optional[DiscoveredKey]:
        raw_key = (raw_key or "").strip()
        if not raw_key:
            return None

        # 1) 前缀精确匹配
        fp = detect_by_prefix(raw_key)
        if fp:
            discovered = DiscoveredKey(
                provider_type=fp.provider_type,
                provider_name=fp.provider_name,
                base_url=fp.default_base_url,
                models=[],
                raw_key=raw_key,
            )
            # 可选: 拉一次 models 列表
            discovered.models = await self._probe_models(discovered.base_url, raw_key)
            self._persist(discovered)
            return discovered

        # 2) 模糊 sk- 并发探测
        if looks_like_generic_sk(raw_key):
            discovered = await self._probe_generic(raw_key)
            if discovered:
                self._persist(discovered)
                return discovered

        return None

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    async def _probe_generic(self, raw_key: str) -> Optional[DiscoveredKey]:
        tasks = [
            self._probe_one(name, base_url, raw_key)
            for name, base_url in GENERIC_OPENAI_COMPATIBLE_CANDIDATES
        ]
        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
            except Exception:
                continue
            if result is not None:
                # 取消其余
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return result
        return None

    async def _probe_one(
        self, name: str, base_url: str, raw_key: str
    ) -> Optional[DiscoveredKey]:
        models = await self._probe_models(base_url, raw_key)
        if models is None:
            return None
        return DiscoveredKey(
            provider_type="openai_compatible",
            provider_name=name,
            base_url=base_url,
            models=models,
            raw_key=raw_key,
        )

    async def _probe_models(self, base_url: str, raw_key: str) -> Optional[List[str]]:
        url = base_url.rstrip("/") + "/models"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url,
                    headers={"Authorization": f"Bearer {raw_key}"},
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
        except Exception as e:
            logger.debug("probe %s failed: %s", url, e)
            return None

        items = []
        if isinstance(data, dict):
            items = list(data.get("data") or data.get("models") or [])
        elif isinstance(data, list):
            items = list(data)
        out: List[str] = []
        for it in items:
            if isinstance(it, dict):
                mid = it.get("id") or it.get("model") or it.get("name")
                if mid:
                    out.append(str(mid))
            elif isinstance(it, str):
                out.append(it)
        return out

    # ------------------------------------------------------------------ #
    # 落库
    # ------------------------------------------------------------------ #
    def _persist(self, dk: DiscoveredKey) -> None:
        try:
            cipher = encrypt(dk.raw_key, data_dir=self.cfg.data_dir)
        except Exception as e:
            logger.warning("encrypt key failed: %s", e)
            return
        now = time.time()
        with self.db.cursor() as cur:
            cur.execute(
                """INSERT INTO discovered_keys
                     (api_key_cipher, provider_type, provider_name, base_url,
                      models, detected_at, last_tested_at, last_health_ok)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(api_key_cipher) DO UPDATE SET
                     provider_type=excluded.provider_type,
                     provider_name=excluded.provider_name,
                     base_url=excluded.base_url,
                     models=excluded.models,
                     last_tested_at=excluded.last_tested_at,
                     last_health_ok=excluded.last_health_ok""",
                (
                    cipher,
                    dk.provider_type,
                    dk.provider_name,
                    dk.base_url,
                    json.dumps(dk.models, ensure_ascii=False),
                    now, now, 1,
                ),
            )

    def list_discovered(self) -> List[Dict[str, Any]]:
        """列出已识别的 key (不返回明文)。"""
        with self.db.cursor() as cur:
            cur.execute(
                """SELECT provider_type, provider_name, base_url, models,
                          detected_at, last_tested_at, last_health_ok
                   FROM discovered_keys ORDER BY detected_at DESC"""
            )
            return [dict(r) for r in cur.fetchall()]
