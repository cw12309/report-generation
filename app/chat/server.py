"""FastAPI Web 服务层"""

import asyncio
import hashlib
import json
import logging
import os
import queue
import shutil
import sys
import time
import uuid
from typing import Any, List, Optional
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import yaml
import httpx
import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from memory.entity_tracker import EntityTracker
from memory.short_term import ShortTermMemory
from memory.session_store import SessionStore
from config import (
    get_db_path, get_collection_name, get_markdown_dir, get_milvus_token,
    get_llm_model, get_llm_api_key,
    get_embed_model, get_embed_api_key,
    get_rerank_api_key,
    get_max_rounds, reload_config,
    create_llm_client, create_embed_client,
    normalize_openai_model_urls, _normalize_base_url,
    sync_milvus_uri,
    get_breast_mri_base_url,
)
from data_pipeline.build_vector_db import (
    build_db,
    ensure_collection_schema,
    ensure_catalog_collection,
    upsert_catalog_entry,
    delete_catalog_entry,
    list_catalog_entries,
    list_distinct_kb_ids,
    find_catalog_by_hash,
)
from data_pipeline.extract_metadata import extract_all
from data_pipeline.process_reports import process_uploaded_excels
from milvus_compat import create_milvus_client
from observability import (
    bind_observability_context,
    current_request_id,
    install_structured_log_handler,
    new_request_id,
    normalize_request_id,
    observability_store,
)
from eval.generate_test_cases import stream_generate_cases
from eval.run_retrieval_eval import (
    DEFAULT_DATASET,
    add_retrieval_case,
    clear_retrieval_dataset,
    delete_retrieval_case,
    get_evaluation_status,
    load_dataset,
    request_evaluation_cancel,
    stream_run_evaluation,
)

from .schemas import (
    ChatRequest, ConfigSaveRequest, TestModelRequest,
    KBBuildRequest, ClearSessionRequest, KBUploadResponse, KBBuildResponse,
    DuplicateKBResponse, KBUploadAction,
    EvalCaseRequest, EvalAutoGenerateRequest,
    DeploymentChatRequest, DeploymentCreateRequest, DeploymentUpdateRequest,
)
from .deployments import (
    DEPLOYMENT_KEY_HEADER,
    ConversationLockRegistry,
    generate_api_key,
    generate_conversation_id,
    generate_deployment_id,
    generate_internal_session_id,
    map_public_event,
    mask_api_key_prefix,
    normalize_selected_kbs,
    verify_api_key as verify_deployment_api_key,
)
from .llm_client import chat_stream
from .pipeline import run_pipeline, search_reports
from .report_schema import ReportSchemaError, parse_report_output, serialize_report
from tools.rag_tool import create_rag_search_handler

# 全局歧义缓存，跨请求存活（按 session_id 索引）
_ambiguity_cache = {}

logger = logging.getLogger(__name__)

# 从配置加载常量
COLLECTION_NAME = get_collection_name()
CHAT_MODEL = get_llm_model()
EMBED_MODEL = get_embed_model()


def _compute_file_sha256(file_path: Path) -> str:
    """计算文件二进制内容的 SHA-256 哈希值。"""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


class RAGSearchRequest(BaseModel):
    """Stateless retrieval request used by the API Gateway."""

    query: str = Field(..., min_length=1, max_length=4000)
    top_k: Optional[int] = Field(default=None, ge=1, le=100)
    rerank_top_k: Optional[int] = Field(default=None, ge=1, le=100)
    selected_kbs: List[str] = Field(default_factory=list)


class ReportGenerateRequest(RAGSearchRequest):
    """Stateless retrieval-and-generation request used by the API Gateway."""

    modality: str = Field(default="", max_length=100)
    body_part: str = Field(default="", max_length=200)
    clinical_history: str = Field(default="", max_length=4000)
    diagnosis: List[str] = Field(default_factory=list)
    selected_diagnosis: Optional[str] = Field(default=None, max_length=500)


def _new_milvus_client():
    return create_milvus_client(get_db_path(), get_milvus_token())


def create_app(*, session_store: Optional[SessionStore] = None) -> FastAPI:
    # 确保日志配置正确
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )
    install_structured_log_handler()

    app = FastAPI(title="影像报告生成 Agent Worker")
    store = session_store or SessionStore(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data",
            "sessions.db",
        )
    )
    evaluation_lock = asyncio.Lock()
    auto_generation_lock = asyncio.Lock()
    conversation_locks = ConversationLockRegistry()
    evaluation_tasks: set[asyncio.Task] = set()

    @app.middleware("http")
    async def observe_http_request(request: Request, call_next):
        """Record bounded request metrics/traces without inspecting request bodies."""
        if request.url.path.startswith(("/static", "/api/observability")):
            return await call_next(request)

        request_id = normalize_request_id(request.headers.get("X-Request-ID", ""))
        session_id = str(request.query_params.get("session_id") or "")[:128]
        started_at = time.perf_counter()
        observability_store.start_request(
            request_id,
            method=request.method,
            path=request.url.path,
            session_id=session_id,
        )
        with bind_observability_context(
            request_id=request_id,
            session_id=session_id,
        ):
            try:
                response = await call_next(request)
            except Exception:
                observability_store.finish_request(
                    request_id,
                    status_code=500,
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                )
                logger.exception("HTTP request failed: path=%s", request.url.path)
                raise
        response.headers["X-Request-ID"] = request_id
        observability_store.finish_request(
            request_id,
            status_code=response.status_code,
            duration_ms=(time.perf_counter() - started_at) * 1000,
        )
        return response

    async def _persist_evaluation_event(event: dict) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "done":
            result = {
                key: event.get(key)
                for key in ("metrics", "details", "bad_cases", "config")
            }
            await asyncio.to_thread(store.save_evaluation_result, result)
            return
        status_map = {
            "start": "running",
            "progress": "running",
            "cancelled": "cancelled",
            "error": "failed",
        }
        status = status_map.get(event_type)
        if status:
            await asyncio.to_thread(
                store.save_evaluation_runtime_state,
                status=status,
                current=int(event.get("current", 0) or 0),
                total=int(event.get("total", 0) or 0),
                progress=int(event.get("progress", 0) or 0),
                current_query=str(event.get("query") or ""),
                message=str(event.get("message") or ""),
            )

    @app.on_event("startup")
    async def validate_milvus_schema_on_startup():
        """Fail fast on an incompatible legacy Collection; create if absent."""
        client = _new_milvus_client()
        try:
            result = ensure_collection_schema(client)
            logger.info(
                "Milvus Schema 检查完成: collection=%s, created=%s",
                COLLECTION_NAME, result.get("created", False),
            )
        finally:
            client.close()

    @app.on_event("startup")
    async def consistency_check_on_startup():
        """Compare SQLite, Milvus Catalog, and Milvus Chunks for consistency."""
        client = _new_milvus_client()
        try:
            ensure_catalog_collection(client)

            sqlite_kbs = {kb["kb_id"]: kb for kb in store.list_knowledge_bases()}
            catalog_entries = list_catalog_entries(client)
            catalog_ids = {entry["kb_id"]: entry for entry in catalog_entries}
            chunk_ids = set(list_distinct_kb_ids(client))

            recovered: list[str] = []
            degraded: list[str] = []
            orphan: list[str] = []

            all_ids = set(sqlite_kbs.keys()) | set(catalog_ids.keys()) | chunk_ids

            for kb_id in sorted(all_ids):
                in_sqlite = kb_id in sqlite_kbs
                in_catalog = kb_id in catalog_ids
                in_chunks = kb_id in chunk_ids

                if in_sqlite and in_catalog and in_chunks:
                    continue
                elif not in_sqlite and in_catalog and in_chunks:
                    entry = catalog_ids[kb_id]
                    store.upsert_knowledge_base_from_catalog(
                        kb_id,
                        str(entry["kb_name"]),
                        str(entry["source_name"]),
                        str(entry["source_hash"]),
                    )
                    recovered.append(kb_id)
                    logger.info("一致性恢复: kb_id=%s 从 Catalog 恢复到 SQLite", kb_id)
                elif in_sqlite and in_catalog and not in_chunks:
                    store.update_knowledge_base(kb_id, status="degraded")
                    degraded.append(kb_id)
                    logger.warning("一致性警告: kb_id=%s 缺少 Milvus chunks，已标记 degraded", kb_id)
                elif in_sqlite and not in_catalog and not in_chunks:
                    pass
                elif not in_sqlite and not in_catalog and in_chunks:
                    orphan.append(kb_id)
                    logger.warning("一致性警告: kb_id=%s 仅存在于 Milvus chunks，无 Catalog/SQLite 记录", kb_id)
                elif not in_sqlite and in_catalog and not in_chunks:
                    degraded.append(kb_id)
                    logger.warning("一致性警告: kb_id=%s 在 Catalog 中但缺少 chunks，已标记 degraded", kb_id)

            logger.info(
                "启动一致性检查完成: SQLite=%d, Catalog=%d, Chunks(kb_id)=%d | "
                "recovered=%d, degraded=%d, orphan=%d",
                len(sqlite_kbs), len(catalog_ids), len(chunk_ids),
                len(recovered), len(degraded), len(orphan),
            )
            if recovered:
                logger.info("恢复列表: %s", recovered)
            if degraded:
                logger.warning("降级列表: %s", degraded)
            if orphan:
                logger.warning("孤儿数据列表: %s", orphan)
        finally:
            client.close()

    _breast_mri_launcher = None

    @app.on_event("startup")
    async def start_breast_mri_service():
        """自动启动乳腺 MRI 报告工作台 Node 服务，失败不阻塞主项目。"""
        global _breast_mri_launcher
        try:
            await consistency_check_on_startup()
        except Exception as exc:
            logger.warning("启动一致性检查失败（不影响主项目其他功能）: %s", exc)
        try:
            import importlib.util
            launcher_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "services", "breast-mri-report", "launcher.py"
            )
            spec = importlib.util.spec_from_file_location("breast_mri_launcher", launcher_path)
            launcher_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(launcher_module)
            _breast_mri_launcher = launcher_module.BreastMRILauncher()
            _breast_mri_launcher.start()
            logger.info("乳腺 MRI 报告工作台已启动: %s", _breast_mri_launcher.base_url)
        except Exception as exc:
            logger.warning("乳腺 MRI 报告工作台启动失败（不影响主项目其他功能）: %s", exc)
            _breast_mri_launcher = None

    @app.on_event("shutdown")
    async def stop_breast_mri_service():
        """主项目退出时自动停止乳腺 MRI 工作台 Node 进程。"""
        if _breast_mri_launcher is not None:
            _breast_mri_launcher.stop()

    front_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "front")
    if os.path.isdir(front_dir):
        app.mount("/static", StaticFiles(directory=front_dir), name="static")

    @app.get("/")
    async def index():
        index_path = os.path.join(front_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"message": "影像报告生成Agent v2", "docs": "/docs"}

    @app.get("/health", tags=["internal"])
    async def health():
        """Only becomes reachable after the Worker startup hook has completed."""
        return {"status": "ok"}

    @app.get("/api/observability/overview", summary="Monitor 运行概览")
    async def observability_overview():
        """Combine bounded request metrics with the existing session metrics."""
        overview = observability_store.overview()
        sessions = await asyncio.to_thread(store.list_sessions)
        overview["sessions"] = {
            "total": len(sessions),
            "turns": sum(int(item.get("turn_count", 0) or 0) for item in sessions),
        }
        overview["services"] = {
            "worker": "healthy",
            "observability": "healthy",
        }
        return overview

    @app.get("/api/observability/traces", summary="Monitor 请求 Trace 列表")
    async def observability_traces(
        limit: int = Query(default=100, ge=1, le=500),
        session_id: str = Query(default="", max_length=128),
        request_id: str = Query(default="", max_length=128),
    ):
        scan_limit = observability_store.max_traces if (session_id or request_id) else limit
        items = observability_store.list_traces(limit=scan_limit)
        if session_id:
            items = [item for item in items if item.get("session_id") == session_id]
        if request_id:
            items = [item for item in items if item.get("request_id") == request_id]
        return {"items": items[:limit], "total": len(items)}

    @app.get(
        "/api/observability/traces/{request_id}",
        summary="Monitor 单次请求完整 Trace",
    )
    async def observability_trace_detail(request_id: str):
        trace = observability_store.get_trace(request_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="Trace 不存在或已过期")
        return trace

    @app.get("/api/observability/logs/stream", summary="Monitor 结构化实时日志 SSE")
    async def observability_log_stream(
        request: Request,
        level: str = Query(default="", max_length=16),
        module: str = Query(default="", max_length=160),
        session_id: str = Query(default="", max_length=128),
        request_id: str = Query(default="", max_length=128),
        after_id: int = Query(default=0, ge=0),
    ):
        normalized_level = level.upper()
        if normalized_level and normalized_level not in {
            "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
        }:
            raise HTTPException(status_code=422, detail="不支持的日志级别")

        try:
            reconnect_cursor = max(
                after_id,
                int(request.headers.get("Last-Event-ID", "0") or 0),
            )
        except ValueError:
            reconnect_cursor = after_id

        async def event_stream():
            cursor = reconnect_cursor
            try:
                while True:
                    events = observability_store.list_logs(
                        after_id=cursor,
                        level=normalized_level,
                        module=module,
                        session_id=session_id,
                        request_id=request_id,
                        limit=200,
                    )
                    if events:
                        for event in events:
                            cursor = max(cursor, int(event["id"]))
                            yield (
                                f"id: {event['id']}\n"
                                "event: log\n"
                                f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                            )
                    else:
                        yield ": heartbeat\n\n"
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                return

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    def _resolve_knowledge_bases(selected_kbs: List[str]):
        requested = list(dict.fromkeys(selected_kbs))
        active = {
            kb["kb_id"]: kb
            for kb in store.list_knowledge_bases(status="active")
        }
        if not requested:
            return None, list(active.values())

        invalid = [kb_id for kb_id in requested if kb_id not in active]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"知识库不存在或不可用: {', '.join(invalid)}",
            )
        return requested, [active[kb_id] for kb_id in requested]

    async def _stateless_search(request: RAGSearchRequest):
        trace_request_id = current_request_id() or new_request_id()
        query = request.query.strip()
        if not query:
            raise HTTPException(status_code=422, detail="query 不能为空")
        selected_kbs, knowledge_bases = _resolve_knowledge_bases(request.selected_kbs)
        if (
            request.top_k is not None
            and request.rerank_top_k is not None
            and request.rerank_top_k > request.top_k
        ):
            raise HTTPException(
                status_code=422,
                detail="rerank_top_k 不能大于 top_k",
            )

        kwargs: dict[str, Any] = {
            "selected_kbs": selected_kbs,
            "knowledge_bases": knowledge_bases,
        }
        if request.top_k is not None:
            kwargs["top_k"] = request.top_k
        if request.rerank_top_k is not None:
            kwargs["rerank_top_k"] = request.rerank_top_k

        def run_search():
            with bind_observability_context(request_id=trace_request_id):
                client = _new_milvus_client()
                try:
                    return search_reports(
                        query,
                        client=client,
                        _emit=lambda event_type, data: (
                            observability_store.record_pipeline_event(
                                trace_request_id,
                                event_type,
                                data,
                            )
                        ),
                        **kwargs,
                    )
                finally:
                    client.close()

        return await asyncio.to_thread(run_search)

    @app.post("/internal/rag/search", tags=["internal"])
    async def internal_rag_search(request: RAGSearchRequest):
        """Run retrieval without creating or mutating a chat session."""
        search_result, results = await _stateless_search(request)
        return {
            "query": request.query,
            "content": search_result,
            "results": results,
        }

    @app.post("/internal/report/generate", tags=["internal"])
    async def internal_report_generate(request: ReportGenerateRequest):
        """Retrieve references and generate a report without session state."""
        search_result, reranked_entities = await _stateless_search(request)

        entity_tracker = EntityTracker(llm_chat_fn=None)
        entity_tracker.slots.update({
            "modality": request.modality or None,
            "body_part": [request.body_part] if request.body_part else [],
            "clinical_history": request.clinical_history,
            "diagnosis": request.diagnosis,
            "intent": "new_session",
        })

        # Reuse the existing report-generation tool. Retrieval is precomputed
        # above in a worker thread so synchronous embedding/vector calls do not
        # block FastAPI's event loop.
        def use_precomputed_search(_query, _emit=None):
            return search_result, reranked_entities

        async def chat_fn(messages, max_tokens=2048, temperature=0.3,
                          _emit=None, debug=False, caller="internal_report_generate"):
            return await chat_stream(
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                _emit=_emit,
                debug=debug,
                caller=caller,
            )

        handler = create_rag_search_handler(
            chat_fn=chat_fn,
            entity_tracker=entity_tracker,
            get_embedding_fn=lambda _text: None,
            search_reports_fn=use_precomputed_search,
            last_report=[""],
            selected_diagnosis=request.selected_diagnosis,
        )
        raw_result = await handler({
            "query": request.query.strip(),
            "modality": request.modality,
            "body_part": request.body_part,
        })
        try:
            payload = json.loads(raw_result)
        except json.JSONDecodeError:
            payload = {"content": raw_result}
        if isinstance(payload, dict):
            payload.pop("_is_final", None)
        return payload

    @app.post("/api/eval/run", summary="运行检索评测")
    async def run_retrieval_evaluation():
        """Start a durable background evaluation and stream its live events."""
        if auto_generation_lock.locked():
            raise HTTPException(status_code=409, detail="测试用例正在自动生成，请完成后再评测")
        if evaluation_lock.locked():
            raise HTTPException(status_code=409, detail="评测任务正在运行，请稍候")

        await evaluation_lock.acquire()
        event_queue: asyncio.Queue = asyncio.Queue()

        async def evaluation_worker():
            try:
                async for event in stream_run_evaluation():
                    await _persist_evaluation_event(event)
                    await event_queue.put(event)
            except asyncio.CancelledError:
                cancelled_event = {
                    "type": "cancelled",
                    "message": "评测任务已中断",
                }
                await _persist_evaluation_event(cancelled_event)
                await event_queue.put(cancelled_event)
                raise
            except Exception as exc:
                logger.exception("Retrieval Eval 执行失败")
                error_event = {
                    "type": "error",
                    "message": f"评测执行失败: {exc}",
                }
                await _persist_evaluation_event(error_event)
                await event_queue.put(error_event)
            finally:
                await event_queue.put(None)
                if evaluation_lock.locked():
                    evaluation_lock.release()

        worker_task = asyncio.create_task(evaluation_worker())
        evaluation_tasks.add(worker_task)
        worker_task.add_done_callback(evaluation_tasks.discard)

        async def event_stream():
            try:
                while True:
                    event = await event_queue.get()
                    if event is None:
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                # The evaluation worker is intentionally independent from this
                # browser connection, so refresh/navigation does not stop it.
                logger.info("Retrieval Eval SSE 客户端已断开，后台任务继续执行")
                raise

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/eval/status", summary="恢复最后一次评测现场")
    async def get_retrieval_evaluation_status():
        runtime = get_evaluation_status()
        if runtime.get("running") or evaluation_lock.locked():
            return {**runtime, "status": "running"}

        persisted = await asyncio.to_thread(store.get_evaluation_state)
        if persisted and persisted.get("result"):
            return {
                "status": "completed",
                "result": persisted["result"],
                "completed_at": persisted.get("completed_at"),
                "last_task_status": persisted.get("status"),
            }
        if persisted:
            status = str(persisted.get("status") or "idle")
            if status == "running":
                status = "interrupted"
            return {
                "status": status,
                "current": persisted.get("current", 0),
                "total": persisted.get("total", 0),
                "progress": persisted.get("progress", 0),
                "query": persisted.get("current_query", ""),
                "message": persisted.get("message", ""),
            }
        return {"status": "idle"}

    @app.post("/api/eval/cancel", summary="取消正在运行的检索评测")
    async def cancel_retrieval_evaluation():
        result = request_evaluation_cancel()
        return {
            "status": "success",
            "message": (
                "已请求取消评测，将在当前 Query 完成后停止"
                if result["running"]
                else "当前没有正在运行的评测任务"
            ),
            **result,
        }

    @app.post("/api/eval/add_case", summary="新增人工检索评测用例")
    async def add_retrieval_evaluation_case(request: EvalCaseRequest):
        """Validate and append one manual case without blocking the event loop."""
        try:
            result = await asyncio.to_thread(
                add_retrieval_case,
                request.query,
                request.expected_chunk_ids,
            )
            await asyncio.to_thread(store.invalidate_evaluation_result)
            logger.info(
                "新增 Retrieval Eval 用例成功: query=%s, total=%s",
                request.query.strip(),
                result["total"],
            )
            return {"code": 0, **result}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("新增 Retrieval Eval 用例失败")
            raise HTTPException(
                status_code=500,
                detail=f"新增评测用例失败: {exc}",
            ) from exc

    @app.get("/api/eval/dataset", summary="获取检索评测测试集")
    async def get_retrieval_evaluation_dataset():
        """Return cases without running Embedding, retrieval, or Rerank."""
        try:
            cases = await asyncio.to_thread(load_dataset, DEFAULT_DATASET)
            return {
                "status": "success",
                "total": len(cases),
                "items": cases,
            }
        except Exception as exc:
            logger.exception("读取 Retrieval Eval 测试集失败")
            raise HTTPException(
                status_code=500,
                detail=f"读取测试集失败: {exc}",
            ) from exc

    def _ensure_eval_dataset_mutable():
        if evaluation_lock.locked() or auto_generation_lock.locked():
            raise HTTPException(
                status_code=409,
                detail="评测或自动生成任务正在运行，请完成后再修改测试集",
            )

    @app.delete("/api/eval/dataset", summary="清空检索评测测试集")
    async def clear_retrieval_evaluation_dataset():
        _ensure_eval_dataset_mutable()
        try:
            result = await asyncio.to_thread(clear_retrieval_dataset)
            await asyncio.to_thread(store.invalidate_evaluation_result)
            return {
                "status": "success",
                "message": "测试集已清空",
                **result,
            }
        except Exception as exc:
            logger.exception("清空 Retrieval Eval 测试集失败")
            raise HTTPException(
                status_code=500,
                detail=f"清空测试集失败: {exc}",
            ) from exc

    @app.delete("/api/eval/dataset/{chunk_id}", summary="删除单条检索评测用例")
    async def delete_retrieval_evaluation_case(chunk_id: str):
        _ensure_eval_dataset_mutable()
        try:
            result = await asyncio.to_thread(delete_retrieval_case, chunk_id)
            await asyncio.to_thread(store.invalidate_evaluation_result)
            return {
                "status": "success",
                "message": "测试用例已删除",
                **result,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="未找到对应测试用例") from exc
        except Exception as exc:
            logger.exception("删除 Retrieval Eval 测试用例失败")
            raise HTTPException(
                status_code=500,
                detail=f"删除测试用例失败: {exc}",
            ) from exc

    @app.post("/api/eval/auto_generate", summary="流式自动生成检索评测用例")
    async def auto_generate_retrieval_cases(request: EvalAutoGenerateRequest):
        """Stream persisted LLM-generated cases to the browser as SSE events."""
        if evaluation_lock.locked():
            raise HTTPException(status_code=409, detail="评测任务正在运行，请完成后再生成用例")
        if auto_generation_lock.locked():
            raise HTTPException(status_code=409, detail="已有自动生成任务正在运行，请稍候")

        await auto_generation_lock.acquire()

        async def event_stream():
            dataset_invalidated = False
            try:
                async for event in stream_generate_cases(
                    count=request.count,
                    min_length=request.min_length,
                ):
                    if event.get("type") == "progress" and not dataset_invalidated:
                        await asyncio.to_thread(store.invalidate_evaluation_result)
                        dataset_invalidated = True
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                logger.info("Retrieval Eval 自动生成连接已取消")
                raise
            except Exception as exc:
                logger.exception("Retrieval Eval 自动生成失败")
                error_event = {
                    "type": "error",
                    "message": str(exc),
                    "current": 0,
                    "total": request.count,
                }
                yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"
            finally:
                auto_generation_lock.release()

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    def _get_or_create_session(session_id):
        """从 SQLite 加载或创建会话，返回内存对象字典"""
        if store.session_exists(session_id):
            # ── 恢复已有会话 ──
            session_data = store.load_session(session_id)
            logger.info(f"恢复会话: {session_id}, 标题={session_data['title']}, 轮次={len(session_data['turns'])}")
            stm = ShortTermMemory(max_rounds=get_max_rounds())
            entity_tracker = EntityTracker(llm_chat_fn=chat_stream)
            client = _new_milvus_client()
            client.load_collection(COLLECTION_NAME)

            # 恢复对话历史到 STM
            for turn in session_data["turns"]:
                stm.add_turn(session_id, turn["user_input"], turn["assistant_output"])

            # 恢复实体槽位（合并默认值，防止缺失键导致 KeyError）
            if session_data["state"]["entity_slots"]:
                entity_tracker.slots.update(session_data["state"]["entity_slots"])

            # 恢复 last_report：旧 Session 仅在此边界转换为 Canonical Schema。
            stored_last_report = session_data["state"]["last_report"]
            if stored_last_report:
                try:
                    stored_last_report = serialize_report(
                        parse_report_output(stored_last_report)
                    )
                except ReportSchemaError as exc:
                    logger.warning("忽略无法规范化的历史 last_report: %s", exc)
                    stored_last_report = ""
            last_report = [stored_last_report]

            # 优先进程缓存，重启后可从 SQLite state 恢复。
            cached = _ambiguity_cache.get(
                session_id,
                session_data["state"].get("last_ambiguity") or None,
            )
            last_ambiguity = [cached] if cached else [None]

            return {
                "stm": stm,
                "entity_tracker": entity_tracker,
                "client": client,
                "last_report": last_report,
                "last_ambiguity": last_ambiguity,
                "selected_kbs": session_data["state"].get("selected_kbs", []),
            }
        else:
            # ── 创建新会话 ──
            logger.info(f"创建新会话: {session_id}")
            stm = ShortTermMemory(max_rounds=get_max_rounds())
            entity_tracker = EntityTracker(llm_chat_fn=chat_stream)
            client = _new_milvus_client()
            client.load_collection(COLLECTION_NAME)

            store.create_session(session_id)

            return {
                "stm": stm,
                "entity_tracker": entity_tracker,
                "client": client,
                "last_report": [""],
                "last_ambiguity": [None],
                "selected_kbs": [],
            }

    def _validate_deployment_kbs(selected_kbs: List[str]) -> tuple[list[str], list[dict]]:
        active = {
            kb["kb_id"]: kb
            for kb in store.list_knowledge_bases(status="active")
        }
        try:
            return normalize_selected_kbs(selected_kbs, active)
        except ValueError as exc:
            message = str(exc)
            raise HTTPException(
                status_code=422 if message.startswith("至少") else 400,
                detail=message,
            ) from exc

    def _deployment_response(deployment: dict, *, api_key: Optional[str] = None) -> dict:
        response = {
            key: value
            for key, value in deployment.items()
            if key not in {"api_key_hash", "deleted_at"}
        }
        response["api_key_prefix"] = mask_api_key_prefix(
            str(deployment.get("api_key_prefix") or "")
        )
        response["endpoint"] = (
            f"/api/v1/deployments/{deployment['deployment_id']}/chat"
        )
        if api_key is not None:
            response["api_key"] = api_key
        return response

    async def _stateful_chat_events(
        *,
        query: str,
        session_id: str,
        selected_diagnosis: Optional[str],
        selected_kbs: list[str],
        active_kbs: list[dict],
        execution_guard=None,
        request_id: str = "",
    ):
        """Run the existing stateful pipeline and yield internal event dictionaries."""
        event_queue: asyncio.Queue = asyncio.Queue()
        stream_finished = object()
        trace_request_id = request_id or current_request_id() or new_request_id()

        def request_event(event_type: str, data: Optional[dict] = None) -> dict:
            payload = dict(data or {})
            # These routing fields are owned by the SSE aggregation layer and
            # cannot be overridden by an individual pipeline emitter.
            payload["type"] = event_type
            payload["request_id"] = trace_request_id
            return payload

        def queue_event(event_type: str, data: dict) -> None:
            observability_store.record_pipeline_event(
                trace_request_id,
                event_type,
                data,
                session_id=session_id,
            )
            event_queue.put_nowait(request_event(event_type, data))

        async def _execute_bound() -> None:
            from langsmith.run_helpers import get_current_run_tree

            session = None
            thinking_events: list[dict] = []
            start_time = time.time()

            def _emit_sync(event_type, data):
                try:
                    event_data = dict(data or {})
                    if event_type == "report" and session is not None:
                        try:
                            structured = json.loads(session["last_report"][0] or "{}")
                        except (json.JSONDecodeError, TypeError):
                            structured = None
                        if isinstance(structured, dict):
                            event_data["data"] = structured
                    payload = request_event(event_type, event_data)
                    observability_store.record_pipeline_event(
                        trace_request_id,
                        event_type,
                        event_data,
                        session_id=session_id,
                    )
                    logger.info(
                        "── _emit_sse: type=%s, len=%s",
                        event_type,
                        len(json.dumps(payload, ensure_ascii=False)),
                    )
                    event_queue.put_nowait(payload)
                    if event_type != "prompt_trace":
                        thinking_events.append({"type": event_type, "data": event_data})
                except Exception:
                    logger.warning("SSE事件入队失败", exc_info=True)

            try:
                session = _get_or_create_session(session_id)
                stm = session["stm"]
                entity_tracker = session["entity_tracker"]
                last_report = session["last_report"]
                last_ambiguity = session["last_ambiguity"]

                run_tree = get_current_run_tree()
                if run_tree is not None:
                    metadata = {
                        "session_id": session_id,
                        "modality": entity_tracker.slots.get("modality", ""),
                        "body_part": entity_tracker.slots.get("body_part", ""),
                    }
                    run_tree.add_metadata(metadata)

                info_before = stm.session_info(session_id)
                turn_index = info_before.get("total_turns", 0)
                result = await run_pipeline(
                    query,
                    session_id,
                    stm,
                    entity_tracker,
                    session["client"],
                    last_report,
                    _emit_sync,
                    selected_diagnosis=selected_diagnosis,
                    last_ambiguity=last_ambiguity,
                    selected_kbs=selected_kbs,
                    knowledge_bases=active_kbs,
                )

                if last_ambiguity[0] is not None:
                    _ambiguity_cache[session_id] = last_ambiguity[0]

                store.save_turn(session_id, turn_index, query, result or "")
                store.save_state(
                    session_id,
                    entity_tracker.slots,
                    last_report[0],
                    last_ambiguity=last_ambiguity[0],
                    selected_kbs=selected_kbs,
                )
                if thinking_events:
                    store.save_thinking(session_id, turn_index, thinking_events)
                if turn_index == 0:
                    store.update_title(session_id, query[:20])
            except Exception as exc:
                logger.error("run_pipeline 执行异常", exc_info=True)
                msg = str(exc)
                try:
                    body = getattr(exc, "body", None) or getattr(exc, "response", None)
                    if body is not None:
                        if hasattr(body, "json"):
                            body = body.json()
                        if isinstance(body, dict):
                            msg = body.get("error", {}).get("message", msg)
                except Exception:
                    pass
                queue_event("error", {"code": "AGENT_ERROR", "message": msg})
            finally:
                if session is not None:
                    try:
                        session["client"].close()
                    except Exception:
                        logger.warning("关闭 Milvus Client 失败", exc_info=True)
                queue_event("done", {"total_time": round(time.time() - start_time, 1)})
                event_queue.put_nowait(stream_finished)

        async def execute() -> None:
            with bind_observability_context(
                request_id=trace_request_id,
                session_id=session_id,
            ):
                await _execute_bound()

        async def run() -> None:
            if execution_guard is None:
                await execute()
                return
            async with execution_guard():
                await execute()

        asyncio.create_task(run())
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=120)
            except asyncio.TimeoutError:
                timeout_event = request_event("error", {
                    "code": "CHAT_TIMEOUT",
                    "message": "等待 Agent 响应超时",
                })
                done_event = request_event("done", {"total_time": 120.0})
                observability_store.record_pipeline_event(
                    trace_request_id,
                    "error",
                    timeout_event,
                    session_id=session_id,
                )
                observability_store.record_pipeline_event(
                    trace_request_id,
                    "done",
                    done_event,
                    session_id=session_id,
                )
                yield timeout_event
                yield done_event
                break
            if event is stream_finished:
                break
            yield event

    def _sse_response(event_stream) -> StreamingResponse:
        return StreamingResponse(
            event_stream,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/deployments", summary="列出 API Deployments")
    async def list_deployments():
        return {
            "deployments": [
                _deployment_response(item)
                for item in store.list_api_deployments()
            ]
        }

    @app.post("/api/deployments", summary="创建 API Deployment")
    async def create_deployment(request: DeploymentCreateRequest):
        name = request.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="name 不能为空")
        selected_kbs, _ = _validate_deployment_kbs(request.selected_kbs)
        api_key, api_key_hash, api_key_prefix = generate_api_key()
        deployment = store.create_api_deployment(
            deployment_id=generate_deployment_id(),
            name=name,
            description=request.description.strip(),
            selected_kbs=selected_kbs,
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
        )
        return _deployment_response(deployment, api_key=api_key)

    @app.get("/api/deployments/{deployment_id}", summary="获取 API Deployment")
    async def get_deployment(deployment_id: str):
        deployment = store.get_api_deployment(deployment_id)
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment 不存在")
        return _deployment_response(deployment)

    @app.patch("/api/deployments/{deployment_id}", summary="更新 API Deployment")
    async def update_deployment(deployment_id: str, request: DeploymentUpdateRequest):
        if request.name is not None and not request.name.strip():
            raise HTTPException(status_code=422, detail="name 不能为空")
        selected_kbs = None
        if request.selected_kbs is not None:
            selected_kbs, _ = _validate_deployment_kbs(request.selected_kbs)
        deployment = store.update_api_deployment(
            deployment_id,
            name=request.name.strip() if request.name is not None else None,
            description=(
                request.description.strip() if request.description is not None else None
            ),
            selected_kbs=selected_kbs,
        )
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment 不存在")
        return _deployment_response(deployment)

    @app.post("/api/deployments/{deployment_id}/rotate-key", summary="轮换 Deployment Key")
    async def rotate_deployment_key(deployment_id: str):
        api_key, api_key_hash, api_key_prefix = generate_api_key()
        deployment = store.rotate_api_deployment_key(
            deployment_id,
            api_key_hash=api_key_hash,
            api_key_prefix=api_key_prefix,
        )
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment 不存在")
        return _deployment_response(deployment, api_key=api_key)

    @app.post("/api/deployments/{deployment_id}/enable", summary="启用 API Deployment")
    async def enable_deployment(deployment_id: str):
        deployment = store.set_api_deployment_status(deployment_id, "active")
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment 不存在")
        return _deployment_response(deployment)

    @app.post("/api/deployments/{deployment_id}/disable", summary="停用 API Deployment")
    async def disable_deployment(deployment_id: str):
        deployment = store.set_api_deployment_status(deployment_id, "disabled")
        if deployment is None:
            raise HTTPException(status_code=404, detail="Deployment 不存在")
        return _deployment_response(deployment)

    @app.delete("/api/deployments/{deployment_id}", summary="删除 API Deployment")
    async def delete_deployment(deployment_id: str):
        if not store.soft_delete_api_deployment(deployment_id):
            raise HTTPException(status_code=404, detail="Deployment 不存在")
        return {"status": "ok", "message": "Deployment 已删除，历史会话已保留"}

    @app.post(
        "/internal/deployments/{deployment_id}/chat",
        tags=["internal"],
        summary="Deployment Stateful Agent Chat",
    )
    async def internal_deployment_chat(
        deployment_id: str,
        request: DeploymentChatRequest,
        deployment_api_key: Optional[str] = Header(
            default=None,
            alias=DEPLOYMENT_KEY_HEADER,
        ),
    ):
        if not request.message.strip():
            raise HTTPException(status_code=422, detail="message 不能为空")
        deployment = store.get_api_deployment(deployment_id)
        if deployment is None or not verify_deployment_api_key(
            deployment_api_key or "",
            str(deployment.get("api_key_hash") or ""),
        ):
            raise HTTPException(
                status_code=401,
                detail={"code": "INVALID_DEPLOYMENT_KEY", "message": "无效的 Deployment API Key"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if deployment["status"] != "active":
            raise HTTPException(
                status_code=403,
                detail={"code": "DEPLOYMENT_DISABLED", "message": "Deployment 已停用"},
            )
        if deployment["health"] != "healthy":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DEPLOYMENT_KB_UNAVAILABLE",
                    "message": "Deployment 绑定的知识库不存在或不可用",
                    "knowledge_bases": deployment["unavailable_kbs"],
                },
            )

        selected_kbs = deployment["selected_kbs"]
        _, active_kbs = _validate_deployment_kbs(selected_kbs)
        conversation_id = request.conversation_id
        if conversation_id:
            conversation = store.get_api_conversation(deployment_id, conversation_id)
            if conversation is None:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation 不存在"},
                )
        else:
            conversation_id = generate_conversation_id()
            conversation = store.create_api_conversation(
                deployment_id=deployment_id,
                conversation_id=conversation_id,
                internal_session_id=generate_internal_session_id(),
            )

        store.touch_api_deployment(deployment_id)
        store.touch_api_conversation(deployment_id, conversation_id)
        trace_request_id = current_request_id() or new_request_id()

        async def public_event_stream():
            conversation_event = {
                "type": "conversation",
                "conversation_id": conversation_id,
                "request_id": trace_request_id,
            }
            yield f"data: {json.dumps(conversation_event, ensure_ascii=False)}\n\n"

            kb_event = {
                "type": "knowledge_bases",
                "knowledge_bases": active_kbs,
            }
            public_kb_event = map_public_event(
                "knowledge_bases",
                kb_event,
                conversation_id=conversation_id,
                knowledge_bases=active_kbs,
            )
            public_kb_event["request_id"] = trace_request_id
            kb_event_data = json.dumps(public_kb_event, ensure_ascii=False)
            yield f"data: {kb_event_data}\n\n"

            async for event in _stateful_chat_events(
                query=request.message.strip(),
                session_id=conversation["internal_session_id"],
                selected_diagnosis=request.selected_diagnosis,
                selected_kbs=selected_kbs,
                active_kbs=active_kbs,
                execution_guard=lambda: conversation_locks.hold(
                    deployment_id,
                    conversation_id,
                ),
                request_id=trace_request_id,
            ):
                public_event = map_public_event(
                    str(event.get("type") or ""),
                    event,
                    conversation_id=conversation_id,
                    knowledge_bases=active_kbs,
                )
                if public_event is not None:
                    public_event["request_id"] = trace_request_id
                    yield f"data: {json.dumps(public_event, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return _sse_response(public_event_stream())

    @app.post("/api/chat", summary="对话", description="发送用户输入并获取流式回复")
    async def chat(request: ChatRequest):
        query = request.query.strip()
        session_id = request.session_id
        selected_diagnosis = request.selected_diagnosis
        selected_kbs = list(dict.fromkeys(request.selected_kbs))

        if not query:
            return {"error": "query 不能为空"}
        if not selected_kbs:
            raise HTTPException(status_code=400, detail="请先勾选至少一个知识库")
        active_kbs = {
            kb["kb_id"]: kb for kb in store.list_knowledge_bases(status="active")
            if kb["kb_id"] in selected_kbs
        }
        invalid_kbs = [kb_id for kb_id in selected_kbs if kb_id not in active_kbs]
        if invalid_kbs:
            raise HTTPException(
                status_code=400,
                detail=f"知识库不存在或不可用: {', '.join(invalid_kbs)}",
            )
        trace_request_id = current_request_id() or new_request_id()

        async def event_stream():
            async for event in _stateful_chat_events(
                query=query,
                session_id=session_id,
                selected_diagnosis=selected_diagnosis,
                selected_kbs=selected_kbs,
                active_kbs=list(active_kbs.values()),
                request_id=trace_request_id,
            ):
                payload = json.dumps(event, ensure_ascii=False)
                yield f"data: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return _sse_response(event_stream())

    @app.get("/api/info", summary="会话信息", description="获取当前会话的轮次、实体槽位和报告状态")
    async def info(session_id: str = Query(default="default", description="会话 ID")):
        session = _get_or_create_session(session_id)
        session_info = session["stm"].session_info(session_id)
        entity = session["entity_tracker"]
        return {
            "current_turns": session_info.get("current_turns", 0),
            "entity_slots": entity.slots,
            "has_last_report": bool(session["last_report"][0]),
        }

    @app.get("/api/memory", summary="记忆信息", description="获取会话的对话历史、实体、摘要等记忆信息")
    async def memory(session_id: str = Query(default="default", description="会话 ID")):
        session = _get_or_create_session(session_id)
        stm = session["stm"]
        entity = session["entity_tracker"]
        info = stm.session_info(session_id)
        history = stm.get_history(session_id)
        entities = entity.slots
        summaries = stm.get_summaries(session_id)

        turns = []
        for i in range(0, len(history), 2):
            user_msg = history[i]["content"] if i < len(history) else ""
            assistant_msg = history[i + 1]["content"] if i + 1 < len(history) else ""
            turns.append({
                "round": i // 2 + 1,
                "user": user_msg,
                "assistant": assistant_msg,
            })

        return {
            "turns": turns,
            "entities": entities,
            "summaries": summaries,
            "current_turns": info.get("current_turns", 0),
            "total_turns": info.get("total_turns", 0),
            "max_rounds": info.get("max_rounds", 5),
        }

    @app.get("/api/kb/status", summary="知识库状态", description="获取知识库文档总数、切片文件数等信息")
    async def kb_status():
        total = 0
        db_path = get_db_path()
        try:
            is_remote = db_path.lower().startswith(("http://", "https://"))
            if is_remote or os.path.exists(db_path):
                client = _new_milvus_client()
                try:
                    if client.has_collection(COLLECTION_NAME):
                        stats = client.get_collection_stats(COLLECTION_NAME)
                        total = int(stats.get("row_count", 0))
                finally:
                    client.close()
        except Exception:
            logger.warning("查询 Milvus 知识库状态失败", exc_info=True)
        markdown_dir = get_markdown_dir()
        md_count = len(list(Path(markdown_dir).rglob("*.md"))) if os.path.isdir(markdown_dir) else 0
        meta_exists = any(kb.get("metadata_tree") for kb in store.list_knowledge_bases())
        return {"total": total, "md_count": md_count, "db_path": db_path, "metadata_exists": meta_exists}

    @app.get("/api/kbs", summary="知识库列表")
    async def list_knowledge_bases():
        items = store.list_knowledge_bases()
        public_items = [
            {key: value for key, value in item.items() if key != "source_path"}
            for item in items
        ]
        return {"knowledge_bases": public_items}

    @app.get("/api/kbs/{kb_id}/chunks", summary="知识库切片列表")
    async def list_knowledge_base_chunks(
        kb_id: str,
        page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
        size: int = Query(default=50, ge=1, le=100, description="每页数量，最大 100"),
    ):
        if not store.get_knowledge_base(kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        result = await asyncio.to_thread(
            store.list_knowledge_base_chunks, kb_id, page, size,
        )
        return {
            "code": 0,
            "data": {
                "total": result["total"],
                "items": result["items"],
                "page": page,
                "size": size,
            },
        }

    @app.get("/api/kb/chunks", summary="评测用例可选知识库切片")
    async def list_active_knowledge_base_chunk_options(
        query: str = Query(default="", max_length=200, description="切片正文搜索词"),
        limit: int = Query(default=20, ge=1, le=100, description="最多返回数量"),
        kb_name: str = Query(default="", max_length=255, description="知识库名称"),
        exam_type: str = Query(default="", max_length=128, description="检查类型"),
        level1: str = Query(default="", max_length=255, description="一级分类"),
        level2: str = Query(default="", max_length=255, description="二级分类"),
        level3: str = Query(default="", max_length=255, description="三级分类"),
        level4: str = Query(default="", max_length=255, description="四级分类"),
    ):
        """Search compact SQLite chunk previews without querying Milvus."""
        return await asyncio.to_thread(
            store.list_active_chunk_options,
            query=query,
            limit=limit,
            preview_length=150,
            kb_name=kb_name,
            exam_type=exam_type,
            level1=level1,
            level2=level2,
            level3=level3,
            level4=level4,
        )

    @app.get("/api/kb/filter-options", summary="评测切片级联筛选选项")
    async def list_active_knowledge_base_chunk_filter_options(
        kb_name: str = Query(default="", max_length=255, description="知识库名称"),
        exam_type: str = Query(default="", max_length=128, description="检查类型"),
        level1: str = Query(default="", max_length=255, description="一级分类"),
        level2: str = Query(default="", max_length=255, description="二级分类"),
        level3: str = Query(default="", max_length=255, description="三级分类"),
    ):
        """Return DISTINCT options constrained by the selected parent levels."""
        return await asyncio.to_thread(
            store.list_active_chunk_filter_options,
            kb_name=kb_name,
            exam_type=exam_type,
            level1=level1,
            level2=level2,
            level3=level3,
        )

    @app.get("/api/kb/files", summary="知识库文件列表", description="获取已上传的报告模板文件及其切片信息")
    async def kb_files():
        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_pipeline", "report_template")
        slices_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_pipeline", "xlsx_slices")
        files = []
        if os.path.isdir(report_dir):
            for fname in sorted(os.listdir(report_dir)):
                if fname.endswith(".xlsx") and not fname.startswith("~$"):
                    fpath = os.path.join(report_dir, fname)
                    stat = os.stat(fpath)
                    basename = os.path.splitext(fname)[0]
                    slice_count = 0
                    if os.path.isdir(slices_dir):
                        slice_count = len([f for f in os.listdir(slices_dir) if f.startswith(basename) and f.endswith(".md")])
                    files.append({
                        "name": fname,
                        "slice_count": slice_count,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                    })
        return {"files": files}

    @app.post("/api/kb/build", summary="构建知识库", description="从切片文件构建向量数据库")
    async def kb_build(request: KBBuildRequest):
        rebuild = request.rebuild
        batch_size = request.batch_size
        if not store.get_knowledge_base(request.kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")
        markdown_dir = str(Path(get_markdown_dir()) / request.kb_id)

        async def event_stream():
            loop = asyncio.get_event_loop()
            event_queue = queue.Queue()

            def _emit_log(data):
                event_queue.put_nowait(json.dumps(data, ensure_ascii=False))

            def run():
                build_db(
                    markdown_dir, batch_size=batch_size, rebuild=rebuild,
                    kb_id=request.kb_id, progress_callback=_emit_log,
                )
                event_queue.put_nowait("[DONE]")

            loop.run_in_executor(None, run)

            while True:
                try:
                    event = await loop.run_in_executor(None, event_queue.get, True, 600)
                    if event == "[DONE]":
                        yield "data: [DONE]\n\n"
                        break
                    yield f"data: {event}\n\n"
                except queue.Empty:
                    break

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/api/kb/extract-metadata", summary="提取元数据", description="提取指定知识库目录树并保存到 SQLite")
    async def kb_extract_metadata(kb_id: str = Query(..., description="知识库 ID")):
        if not store.get_knowledge_base(kb_id):
            raise HTTPException(status_code=404, detail="知识库不存在")

        async def event_stream():
            loop = asyncio.get_event_loop()
            event_queue = queue.Queue()

            def _emit_log(data):
                event_queue.put_nowait(json.dumps(data, ensure_ascii=False))

            def run():
                _emit_log({"level": "info", "msg": "正在从 Markdown 提取元数据..."})
                metadata = extract_all(Path(get_markdown_dir()) / kb_id, kb_id=kb_id)
                store.update_knowledge_base(kb_id, metadata_tree=metadata)
                modality_count = len(metadata.get("检查类型", {}))
                _emit_log({
                    "level": "done",
                    "msg": f"层级元数据提取完成，共 {modality_count} 个检查类型",
                })
                event_queue.put_nowait("[DONE]")

            loop.run_in_executor(None, run)

            while True:
                try:
                    event = await loop.run_in_executor(None, event_queue.get, True, 120)
                    if event == "[DONE]":
                        yield "data: [DONE]\n\n"
                        break
                    yield f"data: {event}\n\n"
                except queue.Empty:
                    break

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/api/kb/upload", response_model=KBUploadResponse,
              summary="上传报告模板", description="只保存 Excel，每个文件建立独立知识库")
    async def kb_upload(
        files: List[UploadFile] = File(default=[]),
        file: Optional[UploadFile] = File(default=None),
        action: str = Query(default="", description="当文件重复时的处理: use_existing / rebuild / create_copy"),
        existing_kb_id: str = Query(default="", description="当 action=use_existing/rebuild 时指定已有 kb_id"),
    ):
        uploads = list(files)
        if file is not None:
            uploads.append(file)
        if not uploads:
            raise HTTPException(status_code=400, detail="至少选择一个 .xlsx 文件")
        invalid = [upload.filename for upload in uploads
                   if not upload.filename or not upload.filename.lower().endswith(".xlsx")]
        if invalid:
            raise HTTPException(status_code=400, detail="只支持 .xlsx 文件")

        report_root = Path(__file__).resolve().parents[1] / "data_pipeline" / "report_template"
        report_root.mkdir(parents=True, exist_ok=True)
        knowledge_bases = []
        for upload in uploads:
            contents = await upload.read()
            source_hash = hashlib.sha256(contents).hexdigest()
            safe_name = Path(upload.filename).name
            kb_name = Path(upload.filename).stem[:255]

            client = _new_milvus_client()
            try:
                existing_catalog = find_catalog_by_hash(client, source_hash)
            finally:
                client.close()
            existing_sqlite = store.find_knowledge_base_by_hash(source_hash)

            existing = existing_catalog or existing_sqlite
            if existing and action not in ("rebuild", "create_copy"):
                existing_id = existing.get("kb_id", "")
                existing_name = existing.get("kb_name", "")
                existing_source = existing.get("source_name", "")
                raise HTTPException(
                    status_code=409,
                    detail={
                        "duplicate": True,
                        "existing_kb_id": existing_id,
                        "existing_kb_name": existing_name,
                        "existing_source_name": existing_source,
                        "message": f"该文件内容已存在于知识库「{existing_name}」({existing_id})",
                    },
                )

            if action == "rebuild" and existing_kb_id:
                kb_id = existing_kb_id
            elif action == "create_copy":
                kb_id = uuid.uuid4().hex
            else:
                kb_id = uuid.uuid4().hex

            filepath = report_root / kb_id / safe_name
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(contents)
            record = store.create_knowledge_base(
                kb_id, kb_name, status="uploaded",
                source_path=str(filepath),
                source_name=safe_name,
                source_hash=source_hash,
            )
            client = _new_milvus_client()
            try:
                upsert_catalog_entry(
                    client, kb_id, kb_name, safe_name, source_hash,
                )
            finally:
                client.close()
            knowledge_bases.append(record)
        return {"knowledge_bases": knowledge_bases}

    def _build_knowledge_base_task(kb_id: str, force_rebuild: bool = False) -> None:
        """Background worker for one KB; all failures are persisted as failed."""
        last_persisted_progress = 0

        def persist_progress(progress: int, message: str = "") -> None:
            """Persist at most once per 5% step from this background thread."""
            nonlocal last_persisted_progress
            progress = min(100, max(0, int(progress)))
            if progress < 100 and progress - last_persisted_progress < 5:
                return
            store.update_knowledge_base_progress(kb_id, progress)
            last_persisted_progress = progress
            if message:
                logger.info("知识库构建进度 [%s] %s%%: %s", kb_id, progress, message)

        try:
            store.update_knowledge_base(kb_id, status="processing", progress=0)
            kb = store.get_knowledge_base(kb_id)
            if not kb:
                raise FileNotFoundError(f"知识库不存在: {kb_id}")
            source_path = Path(kb.get("source_path") or "")
            if not source_path.is_file():
                raise FileNotFoundError(f"原始 Excel 不存在: {source_path}")
            new_hash = _compute_file_sha256(source_path)
            old_hash = str(kb.get("source_hash") or "")
            if not force_rebuild and old_hash and new_hash == old_hash:
                logger.info(
                    "知识库 [%s] 内容未变化 (source_hash=%s)，跳过重建",
                    kb_id, new_hash[:16],
                )
                store.update_knowledge_base(kb_id, status="active", progress=100)
                return
            if force_rebuild:
                logger.info(
                    "知识库 [%s] 强制重建 (force_rebuild=True, source_hash=%s)",
                    kb_id, new_hash[:16],
                )
            elif old_hash and new_hash != old_hash:
                logger.info(
                    "知识库 [%s] 内容已变化，更新 source_hash 并重建",
                    kb_id,
                )
            store.update_knowledge_base(kb_id, source_hash=new_hash)
            markdown_root = Path(get_markdown_dir())
            parse_result = process_uploaded_excels(
                [str(source_path)], markdown_root, kb_id,
            )
            if parse_result["errors"]:
                error_preview = "; ".join(parse_result["errors"][:5])
                raise RuntimeError(f"Excel 解析存在错误: {error_preview}")
            persist_progress(10, "Excel 解析与 Markdown 切片完成")
            chunk_count = store.replace_knowledge_base_chunks(
                kb_id, parse_result["chunks"],
            )
            if chunk_count == 0:
                raise ValueError("Excel 中没有可用的知识库切片")
            persist_progress(15, f"SQLite 已保存 {chunk_count} 条切片原文")
            kb_markdown_dir = markdown_root / kb_id
            metadata = extract_all(kb_markdown_dir, kb_id=kb_id)
            store.update_knowledge_base(kb_id, metadata_tree=metadata)
            persist_progress(20, "目录元数据提取完成")
            build_db(
                str(kb_markdown_dir),
                kb_id=kb_id,
                build_progress_callback=persist_progress,
            )
            store.update_knowledge_base(kb_id, status="active", progress=100)
            # 同步更新 Catalog
            kb = store.get_knowledge_base(kb_id)
            if kb:
                catalog_client = _new_milvus_client()
                try:
                    upsert_catalog_entry(
                        catalog_client,
                        kb_id,
                        str(kb.get("kb_name") or ""),
                        str(kb.get("source_name") or ""),
                        str(kb.get("source_hash") or new_hash),
                    )
                finally:
                    catalog_client.close()
        except Exception:
            logger.exception("知识库构建失败: %s", kb_id)
            # 不覆盖 progress，保留失败前最后一次成功持久化的进度。
            store.update_knowledge_base(kb_id, status="failed")

    @app.post("/api/kb/{kb_id}/build", response_model=KBBuildResponse,
              summary="构建知识库", description="异步切片、向量化并写入 Milvus")
    async def build_knowledge_base(kb_id: str, background_tasks: BackgroundTasks):
        kb = store.get_knowledge_base(kb_id)
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
        if kb["status"] == "processing":
            raise HTTPException(status_code=409, detail="知识库正在构建")
        store.update_knowledge_base(kb_id, status="processing", progress=0)
        background_tasks.add_task(_build_knowledge_base_task, kb_id, True)
        return {"kb_id": kb_id, "status": "processing", "progress": 0}

    @app.delete("/api/kb/{kb_id}", summary="删除知识库")
    async def delete_knowledge_base(kb_id: str):
        kb = store.get_knowledge_base(kb_id)
        if not kb:
            raise HTTPException(status_code=404, detail="知识库不存在")
        client = _new_milvus_client()
        try:
            if client.has_collection(COLLECTION_NAME):
                safe_kb_id = kb_id.replace("\\", "").replace('"', "")
                client.delete(
                    collection_name=COLLECTION_NAME,
                    filter=f'kb_id == "{safe_kb_id}"',
                )
                client.flush(COLLECTION_NAME)
            delete_catalog_entry(client, kb_id)
        finally:
            client.close()
        store.delete_knowledge_base(kb_id)
        # SQLite 与 Milvus 删除成功后，清理该知识库的原始文件和切片目录。
        app_data_pipeline = Path(__file__).resolve().parents[1] / "data_pipeline"
        shutil.rmtree(app_data_pipeline / "report_template" / kb_id, ignore_errors=True)
        shutil.rmtree(app_data_pipeline / "output_markdown" / kb_id, ignore_errors=True)
        return {"status": "ok", "kb_id": kb_id}

    @app.delete("/api/session", summary="删除会话", description="删除指定会话及其所有数据")
    async def delete_session(session_id: str = Query(default="default", description="会话 ID")):
        store.delete_session(session_id)
        return {"status": "ok"}

    @app.post("/api/clear", summary="清空会话", description="清空指定会话的内容并重新创建")
    async def clear_session_post(request: ClearSessionRequest):
        session_id = request.session_id
        store.delete_session(session_id)
        store.create_session(session_id)
        return {"status": "ok"}

    @app.get("/api/sessions", summary="会话列表", description="获取所有历史会话列表")
    async def list_sessions():
        return {"sessions": store.list_sessions()}

    @app.get("/api/session/thinking", summary="思考过程", description="获取指定会话的思考过程事件记录")
    async def get_thinking(session_id: str = Query(default="default", description="会话 ID")):
        return {"thinking": store.get_thinking(session_id)}

    @app.post("/api/session/new", summary="创建会话", description="创建一个新会话并返回 session_id")
    async def new_session():
        session_id = SessionStore.generate_session_id()
        store.create_session(session_id)
        return {"session_id": session_id}

    @app.get("/api/config", summary="获取配置", description="获取当前系统配置（API 密钥会返回掩码）")
    async def get_config():
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.yml")
        if not os.path.exists(config_path):
            return {"error": "配置文件不存在"}
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}
        normalize_openai_model_urls(config_data)

        # MILVUS_URI 以 .env/进程环境为准，配置页始终展示实际生效值。
        retrieval_config = config_data.setdefault("retrieval", {})
        if isinstance(retrieval_config, dict):
            retrieval_config.pop("db_path", None)
        milvus_config = config_data.setdefault("milvus", {})
        if not isinstance(milvus_config, dict):
            return {"error": "config.yml 中的 milvus 配置格式无效"}
        milvus_config["uri"] = get_db_path()

        # 密钥只能来自环境变量；绝不把 YAML 占位符或真实密钥返回给前端。
        for model_list_key, env_key_func in [
            ("llms", get_llm_api_key),
            ("embeddings", get_embed_api_key),
            ("reranks", get_rerank_api_key),
        ]:
            models = config_data.get(model_list_key)
            if isinstance(models, list):
                for m in models:
                    m["api_key"] = (
                        "••••••••••••••••••••••••" if env_key_func() else ""
                    )
        if isinstance(milvus_config, dict):
            milvus_config["token"] = (
                "••••••••••••••••••••••••" if get_milvus_token() else ""
            )

        return {"config": config_data, "path": config_path}

    @app.post("/api/config", summary="保存配置", description="保存系统配置并重新加载生效")
    async def save_config(request: ConfigSaveRequest):
        config_data = normalize_openai_model_urls(request.config)
        if config_data is None:
            return {"error": "缺少 config 参数"}

        milvus_config = config_data.setdefault("milvus", {})
        if not isinstance(milvus_config, dict):
            raise HTTPException(status_code=422, detail="milvus 配置必须是对象")
        submitted_milvus_uri = milvus_config.get("uri") or get_db_path()
        try:
            milvus_config["uri"] = sync_milvus_uri(submitted_milvus_uri)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        retrieval_config = config_data.get("retrieval")
        if isinstance(retrieval_config, dict):
            retrieval_config.pop("db_path", None)

        # 配置页只保存非敏感配置；所有密钥位置始终写回环境变量占位符。
        api_key_placeholders = {
            "llms": "${CHAT_API_KEY}",
            "embeddings": "${EMBED_API_KEY}",
            "reranks": "${SILICONFLOW_API_KEY}",
        }
        for model_list_key, placeholder in api_key_placeholders.items():
            models = config_data.get(model_list_key)
            if isinstance(models, list):
                for m in models:
                    m["api_key"] = placeholder
        milvus_config["token"] = "${MILVUS_TOKEN}"

        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.yml")
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        reload_config()
        return {
            "status": "ok",
            "message": "配置已保存并生效，MILVUS_URI 已同步到 .env",
            "milvus_uri": milvus_config["uri"],
        }

    @app.post("/api/test-model", summary="测试模型连接", description="测试模型 API 连接是否正常")
    async def test_model_connection(request: TestModelRequest):
        model_config = request.params
        model_type = request.model_type

        base_url = model_config.get("base_url", "")
        model_name = model_config.get("model", "")
        api_key = model_config.get("api_key", "")

        # 如果前端没传 key（空或掩码占位符），尝试从环境变量 / .env 兜底
        if not api_key or api_key == "••••••••••••••••••••••••":
            if model_type == "embeddings":
                api_key = get_embed_api_key()
            elif model_type == "reranks":
                api_key = get_rerank_api_key()
            else:
                api_key = get_llm_api_key()

        if not base_url:
            return {"success": False, "message": "API 地址不能为空"}
        if not model_name:
            return {"success": False, "message": "模型名不能为空"}

        try:
            if model_type == "embeddings":
                # 使用 OpenAI SDK 测试 Embedding
                client = create_embed_client(base_url, api_key)
                response = client.embeddings.create(model=model_name, input="你好")
                emb_data = response.data
                if emb_data:
                    dim = len(emb_data[0].embedding)
                    return {"success": True, "message": f"连接成功，向量维度: {dim}"}
                return {"success": True, "message": "连接成功"}
                
            elif model_type == "reranks":
                # Rerank 仍使用 requests（OpenAI SDK 不直接支持 rerank）
                headers = {"Content-Type": "application/json"}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                payload = {
                    "model": model_name,
                    "query": "测试查询",
                    "documents": ["测试文档"],
                }
                r = requests.post(
                    _normalize_base_url(base_url, "/rerank"),
                    headers=headers,
                    json=payload,
                    timeout=15,
                )
                if r.status_code == 200:
                    data = r.json()
                    results = data.get("results", [])
                    if results:
                        score = results[0].get("relevance_score", "N/A")
                        return {"success": True, "message": f"连接成功，相关性分数: {score}"}
                    return {"success": True, "message": "连接成功"}
                else:
                    error_msg = r.text[:200] if r.text else f"HTTP {r.status_code}"
                    return {"success": False, "message": error_msg}
                    
            else:
                # 使用 OpenAI SDK 测试 LLM
                client = create_llm_client(base_url, api_key)
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": "你好"}],
                    max_tokens=16,
                    temperature=0.0,
                )
                choices = response.choices
                if choices:
                    reply = choices[0].message.content or ""
                    return {"success": True, "message": f"连接成功，返回: {reply[:50]}"}
                return {"success": True, "message": "连接成功"}
                
        except Exception as e:
            error_msg = str(e)
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                logger.warning("模型测试：连接超时")
                return {"success": False, "message": "连接超时"}
            elif "connection" in error_msg.lower():
                logger.warning("模型测试：无法连接到服务器")
                return {"success": False, "message": "无法连接到服务器"}
            else:
                logger.error("模型测试失败", exc_info=True)
                # 提取 HTTP 状态码（如果有）
                if "status_code" in error_msg or "http" in error_msg.lower():
                    return {"success": False, "message": error_msg[:200]}
                return {"success": False, "message": error_msg[:200]}

    # ——————————————————————————————————————————————————————————
    # 乳腺 MRI 报告工作台代理层
    # ——————————————————————————————————————————————————————————

    _BREAST_MRI_BASE = get_breast_mri_base_url()

    def _sanitize_proxy_headers(headers):
        """为同源代理清理安全头，允许 iframe 嵌入。

        移除 X-Frame-Options，将 CSP 中 frame-ancestors 'none'
        调整为 'self'（同源代理下合法），保留其他 CSP 指令不变。
        """
        stripped = [
            "content-encoding", "transfer-encoding", "content-length",
            "x-frame-options",
        ]
        result = {}
        for k, v in headers.items():
            if k.lower() in stripped:
                continue
            if k.lower() == "content-security-policy":
                v = v.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
            result[k] = v
        return result

    @app.get("/api/breast-mri/health", tags=["breast-mri"])
    async def breast_mri_health():
        """代理 breast-mri-report 健康检查，Node 不可用时返回降级状态。"""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{_BREAST_MRI_BASE}/api/health", timeout=5.0)
                return resp.json()
        except Exception:
            return {
                "status": "unavailable",
                "service": "breast-mri-report",
                "message": "乳腺 MRI 报告服务不可用",
            }

    @app.get("/api/breast-mri/model-status", tags=["breast-mri"])
    async def breast_mri_model_status():
        """代理 breast-mri-report LLM 模型状态检查。"""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{_BREAST_MRI_BASE}/api/model-status", timeout=10.0)
                return resp.json()
        except Exception as exc:
            return {"online": False, "error": str(exc)}

    @app.post("/api/breast-mri/turn", tags=["breast-mri"])
    async def breast_mri_turn(request: Request):
        """代理 breast-mri-report Agent 对话。"""
        body = await request.json()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{_BREAST_MRI_BASE}/api/agent/turn",
                    json=body,
                    timeout=90.0,
                )
                return resp.json()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="乳腺 MRI 报告服务不可用")
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="乳腺 MRI 报告服务响应超时")

    @app.api_route("/breast-mri/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], tags=["breast-mri"])
    async def proxy_breast_mri_static(path: str, request: Request):
        """代理 breast-mri-report 静态资源和前端页面。

        通过同源代理解决 iframe 跨端口问题，前端 JS 中的 API 调用
        也会走 FastAPI 同源，无需修改 breast-mri-report 前端代码。
        """
        try:
            url = f"{_BREAST_MRI_BASE}/{path}"
            async with httpx.AsyncClient() as client:
                # 转发请求体（如果有）
                body = await request.body() if request.method in ("POST", "PUT") else None
                resp = await client.request(
                    method=request.method,
                    url=url,
                    headers={k: v for k, v in request.headers.items()
                             if k.lower() not in ("host", "content-length")},
                    content=body,
                    timeout=30.0,
                )
                return StreamingResponse(
                    resp.aiter_bytes(),
                    status_code=resp.status_code,
                    headers=_sanitize_proxy_headers(resp.headers),
                    media_type=resp.headers.get("content-type"),
                )
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="乳腺 MRI 报告服务不可用")

    return app


app = create_app()


def web_main(port=8001):
    """Backward-compatible Worker launcher; never expose it beyond loopback."""
    logger.info(
        "Worker 启动: http://127.0.0.1:%s, 模型=%s, Embedding=%s",
        port,
        CHAT_MODEL,
        EMBED_MODEL,
    )
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port)
