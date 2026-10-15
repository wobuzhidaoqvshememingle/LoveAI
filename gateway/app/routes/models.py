# -*- coding: utf-8 -*-
"""
GET /v1/models — 汇总所有 provider 支持的模型, 去重后返回 OpenAI 标准列表。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.engine import Engine
from app.models import ModelInfo

router = APIRouter(prefix="/v1", tags=["models"])


def get_engine(request: Request) -> Engine:
    return request.app.state.engine  # type: ignore[no-any-return]


@router.get("/models")
async def list_models(engine: Engine = Depends(get_engine)) -> dict:
    """遍历所有已注册 provider, 收集 supported_models, 去重返回。"""
    seen: set[str] = set()
    data: list[dict] = []
    for prov_name, prov in engine.all_providers().items():
        for m in getattr(prov, "supported_models", []) or []:
            if m in seen:
                continue
            seen.add(m)
            data.append(
                ModelInfo(id=m, owned_by=prov_name).model_dump(exclude_none=True)
            )
    return {"object": "list", "data": data}
