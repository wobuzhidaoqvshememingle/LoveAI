# -*- coding: utf-8 -*-
"""
POST /v1/chat/completions — OpenAI 兼容对话入口。

- 非流式: 直接返回 engine.complete() 的结果
- 流式: 包成 SSE (text/event-stream), 逐 chunk yield, 末尾发 [DONE]
- 鉴权: cfg.security.gateway_api_key 非空时校验 Bearer Token
- 错误: ProviderError 被捕获后包成 GatewayError 返回, tried_providers 记录试过的厂商
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import GatewayConfig
from app.core.engine import Engine
from app.models import (
    ChatCompletionRequest,
    GatewayError,
)
from app.providers.base import ProviderError

router = APIRouter(prefix="/v1", tags=["chat"])


# --------------------------------------------------------------------------- #
# 依赖
# --------------------------------------------------------------------------- #
def get_engine(request: Request) -> Engine:
    return request.app.state.engine  # type: ignore[no-any-return]


def get_cfg(request: Request) -> GatewayConfig:
    return request.app.state.cfg  # type: ignore[no-any-return]


async def require_gateway_key(
    request: Request,
    cfg: GatewayConfig = Depends(get_cfg),
) -> None:
    """如果配置了 gateway_api_key, 校验 Authorization: Bearer <key>。"""
    expected = (cfg.security.gateway_api_key or "").strip()
    if not expected:
        return  # 未开启鉴权 (仅本机 self-hosted)
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise _unauthorized("missing or invalid Authorization header")
    token = auth[7:].strip()
    if token != expected:
        raise _unauthorized("invalid gateway api key")


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=GatewayError(
            message=detail, type="authentication_error", code="invalid_request"
        ).model_dump(),
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_tried(exc: ProviderError) -> list[dict[str, Any]]:
    """从 ProviderError 上提取已尝试过的厂商列表。

    core 层在 fallback 链耗尽时可能把 tried_providers 挂在异常上;
    没有就退化成单条记录。
    """
    tried = getattr(exc, "tried_providers", None)
    if isinstance(tried, list) and tried:
        return tried  # type: ignore[return-value]
    return [{
        "provider": getattr(exc, "provider", "") or "unknown",
        "error": str(exc),
    }]


def _build_gateway_error(exc: ProviderError) -> GatewayError:
    return GatewayError(
        message=exc.message or "upstream provider error",
        type=type(exc).__name__,
        code=str(getattr(exc, "http_status", 502)),
        tried_providers=_extract_tried(exc),
    )


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
@router.post("/chat/completions", response_model=None)
async def chat_completions(
    req: ChatCompletionRequest,
    request: Request,
    engine: Engine = Depends(get_engine),
    cfg: GatewayConfig = Depends(get_cfg),
    _: None = Depends(require_gateway_key),
):
    # -- 流式 ---------------------------------------------------------------- #
    if req.stream:
        async def sse_generator():
            try:
                async for chunk in engine.stream(req):
                    payload = chunk.to_sse_json()
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except ProviderError as e:
                err = _build_gateway_error(e)
                yield f"data: {json.dumps(err.model_dump(), ensure_ascii=False)}\n\n"
            except Exception as e:  # noqa: BLE001
                err = GatewayError(
                    message=f"internal gateway error: {e}",
                    type="internal_error",
                    code="internal",
                    tried_providers=[],
                )
                yield f"data: {json.dumps(err.model_dump(), ensure_ascii=False)}\n\n"
            finally:
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # -- 非流式 --------------------------------------------------------------- #
    try:
        resp = await engine.complete(req)
        return JSONResponse(status_code=200, content=resp.model_dump(exclude_none=True))
    except ProviderError as e:
        err = _build_gateway_error(e)
        return JSONResponse(
            status_code=getattr(e, "http_status", 502),
            content=err.model_dump(),
        )
    except Exception as e:  # noqa: BLE001
        err = GatewayError(
            message=f"internal gateway error: {e}",
            type="internal_error",
            code="internal",
            tried_providers=[],
        )
        return JSONResponse(status_code=500, content=err.model_dump())
