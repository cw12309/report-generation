"""Public API Gateway.

This process deliberately does not import the Worker application, model clients,
or vector-database code. It only authenticates public calls and proxies them to
the private Worker service.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
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


class RAGSearchPayload(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    top_k: Optional[int] = Field(default=None, ge=1, le=100)
    rerank_top_k: Optional[int] = Field(default=None, ge=1, le=100)
    selected_kbs: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class ReportGeneratePayload(RAGSearchPayload):
    modality: str = Field(default="", max_length=100)
    body_part: str = Field(default="", max_length=200)
    clinical_history: str = Field(default="", max_length=4000)
    diagnosis: List[str] = Field(default_factory=list)
    selected_diagnosis: Optional[str] = Field(default=None, max_length=500)


class DeploymentChatPayload(BaseModel):
    """Stable public contract for a stateful Deployment Agent."""

    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    selected_diagnosis: Optional[str] = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


async def verify_api_key(
    authorization: Annotated[Optional[str], Header()] = None,
) -> None:
    """Validate ``Authorization: Bearer <token>`` in constant time."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Bearer Token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, separator, token = authorization.partition(" ")
    expected = os.getenv("API_SECRET_KEY", "sk-my-default-secret")
    valid = (
        bool(separator)
        and scheme.lower() == "bearer"
        and bool(token)
        and secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def extract_deployment_api_key(
    authorization: Annotated[Optional[str], Header()] = None,
) -> str:
    """Require a Bearer token without coupling Gateway to business storage."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 Bearer Token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 Bearer Token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


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
    "/api/v1/rag/search",
    dependencies=[Depends(verify_api_key)],
    summary="RAG 检索",
)
async def rag_search(request: Request, payload: RAGSearchPayload):
    del payload  # Validation/OpenAPI only; the original bytes are forwarded.
    return await _proxy(request, "/internal/rag/search")


@app.post(
    "/api/v1/report/generate",
    dependencies=[Depends(verify_api_key)],
    summary="报告生成",
)
async def report_generate(request: Request, payload: ReportGeneratePayload):
    del payload  # Validation/OpenAPI only; the original bytes are forwarded.
    return await _proxy(request, "/internal/report/generate")


@app.post(
    "/api/v1/deployments/{deployment_id}/chat",
    summary="Stateful Deployment Agent Chat",
)
async def deployment_chat(
    deployment_id: str,
    request: Request,
    payload: DeploymentChatPayload,
    deployment_api_key: str = Depends(extract_deployment_api_key),
):
    del payload  # Validation/OpenAPI only; the original bytes are forwarded.
    return await _proxy(
        request,
        f"/internal/deployments/{deployment_id}/chat",
        internal_headers={_DEPLOYMENT_KEY_HEADER: deployment_api_key},
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
