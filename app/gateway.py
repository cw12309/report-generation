"""Public API Gateway.

This process deliberately does not import the Worker application, model clients,
or vector-database code. It only authenticates public calls and proxies them to
the private Worker service.
"""

from __future__ import annotations

import ipaddress
import os
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask


load_dotenv()

WORKER_BASE_URL = os.getenv(
    "WORKER_BASE_URL",
    "http://127.0.0.1:8001",
).rstrip("/")
WORKER_REQUEST_TIMEOUT = float(os.getenv("WORKER_REQUEST_TIMEOUT", "600"))

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
_DEPLOYMENT_KEY_HEADER = "X-Deployment-API-Key"
deployment_auth = HTTPBearer()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    timeout = httpx.Timeout(
        connect=5.0,
        read=WORKER_REQUEST_TIMEOUT,
        write=WORKER_REQUEST_TIMEOUT,
        pool=5.0,
    )
    app.state.worker_client = httpx.AsyncClient(
        base_url=WORKER_BASE_URL,
        timeout=timeout,
        trust_env=False,
    )
    try:
        yield
    finally:
        await app.state.worker_client.aclose()


app = FastAPI(
    title="影像报告生成 API Gateway",
    version="1.0.0",
    lifespan=lifespan,
)


class DeploymentChatPayload(BaseModel):
    """Stable public contract for a stateful Deployment Agent."""

    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    selected_diagnosis: Optional[str] = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


def _forward_request_headers(request: Request) -> dict[str, str]:
    return {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _HOP_BY_HOP_HEADERS
        and name.lower() not in {
            "host",
            "content-length",
            "authorization",
            _DEPLOYMENT_KEY_HEADER.lower(),
        }
    }


def _forward_response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in _HOP_BY_HOP_HEADERS
    }


async def _proxy(
    request: Request,
    worker_path: str,
    *,
    internal_headers: Optional[dict[str, str]] = None,
) -> StreamingResponse:
    client: httpx.AsyncClient = request.app.state.worker_client
    headers = _forward_request_headers(request)
    if internal_headers:
        headers.update(internal_headers)
    upstream_request = client.build_request(
        method=request.method,
        url=worker_path,
        params=request.query_params.multi_items(),
        headers=headers,
        content=request.stream(),
    )
    try:
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Worker 请求超时") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Worker 服务不可用") from exc

    return StreamingResponse(
        upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=_forward_response_headers(upstream_response),
        background=BackgroundTask(upstream_response.aclose),
    )


@app.post(
    "/api/v1/deployments/{deployment_id}/chat",
    summary="Stateful Deployment Agent Chat",
)
async def deployment_chat(
    deployment_id: str,
    request: Request,
    payload: DeploymentChatPayload,
    credentials: Annotated[HTTPAuthorizationCredentials, Security(deployment_auth)],
):
    del payload
    return await _proxy(
        request,
        f"/internal/deployments/{deployment_id}/chat",
        internal_headers={_DEPLOYMENT_KEY_HEADER: credentials.credentials},
    )


def _verify_legacy_source(request: Request) -> None:
    """Keep the existing browser UI working without making legacy APIs public.

    Forwarded IP headers are intentionally ignored: trusting them without a
    configured reverse-proxy trust boundary would allow trivial spoofing.
    """
    if os.getenv("ALLOW_LEGACY_PUBLIC_PROXY", "false").lower() == "true":
        return
    if request.client is None:
        raise HTTPException(status_code=403, detail="无法验证旧版前端来源")
    try:
        address = ipaddress.ip_address(request.client.host)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="旧版前端来源无效") from exc
    if not (address.is_loopback or address.is_private or address.is_link_local):
        raise HTTPException(status_code=403, detail="旧版接口仅允许内网前端访问")


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def legacy_frontend_proxy(path: str, request: Request):
    _verify_legacy_source(request)
    return await _proxy(request, f"/{path}")