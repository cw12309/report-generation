"""FastAPI Web 服务层"""

import asyncio
import json
import logging
import os
import queue
import shutil
import sys
import time
import uuid
from typing import List, Optional
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml
import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pymilvus import MilvusClient

from memory.entity_tracker import EntityTracker
from memory.short_term import ShortTermMemory
from memory.session_store import SessionStore
from config import (
    get_db_path, get_collection_name, get_markdown_dir, get_milvus_token,
    get_llm_model, get_llm_api_key, get_llm_base_url,
    get_embed_model, get_embed_api_key, get_embed_base_url,
    get_rerank_api_key, get_rerank_model, get_rerank_base_url,
    get_max_rounds, reload_config,
)
from data_pipeline.build_vector_db import build_db, ensure_collection_schema
from data_pipeline.extract_metadata import extract_all
from data_pipeline.process_reports import process_uploaded_excels
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
    EvalCaseRequest, EvalAutoGenerateRequest,
)
from .llm_client import chat_stream
from .pipeline import run_pipeline

# 全局歧义缓存，跨请求存活（按 session_id 索引）
_ambiguity_cache = {}

logger = logging.getLogger(__name__)

# 从配置加载常量
DB_PATH = get_db_path()
COLLECTION_NAME = get_collection_name()
CHAT_MODEL = get_llm_model()
EMBED_MODEL = get_embed_model()


def _new_milvus_client():
    token = get_milvus_token()
    return MilvusClient(uri=DB_PATH, **({"token": token} if token else {}))


def web_main(port=8000):
    # 确保日志配置正确
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    app = FastAPI(title="影像报告生成Agent v2")
    store = SessionStore(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "sessions.db"))
    evaluation_lock = asyncio.Lock()
    auto_generation_lock = asyncio.Lock()
    evaluation_tasks: set[asyncio.Task] = set()

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

    front_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "front")
    if os.path.isdir(front_dir):
        app.mount("/static", StaticFiles(directory=front_dir), name="static")

    @app.get("/")
    async def index():
        index_path = os.path.join(front_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"message": "影像报告生成Agent v2", "docs": "/docs"}

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

            # 恢复 last_report
            last_report = [session_data["state"]["last_report"]]

            # 恢复歧义缓存（跨请求存活）
            cached = _ambiguity_cache.get(session_id)
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

        logger.info(f"收到查询: session={session_id}, query={query[:50]}..., selected_diagnosis={selected_diagnosis}")
        session = _get_or_create_session(session_id)
        stm = session["stm"]
        entity_tracker = session["entity_tracker"]
        last_report = session["last_report"]
        last_ambiguity = session["last_ambiguity"]

        async def event_stream():
            from langsmith.run_helpers import get_current_run_tree
            thinking_events = []  # 持久化保存思考过程
            start_time = time.time()  # 记录请求开始时间
            event_queue = asyncio.Queue()

            def _emit_sync(event_type, data):
                try:
                    payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
                    logger.info(f"── _emit_sse: type={event_type}, len={len(payload)}")
                    event_queue.put_nowait(payload)
                    # 记录思考过程事件
                    thinking_events.append({"type": event_type, "data": data})
                except Exception:
                    logger.warning("SSE事件入队失败", exc_info=True)

            async def run():
                try:
                    # ── LangSmith Metadata 注入 ──
                    run_tree = get_current_run_tree()
                    if run_tree is not None:
                        metadata = {
                            "session_id": session_id,
                            "modality": entity_tracker.slots.get("modality", ""),
                            "body_part": entity_tracker.slots.get("body_part", ""),
                        }
                        run_tree.add_metadata(metadata)
                        logger.info(f"LangSmith metadata 已注入: {metadata}")

                    # 记录本轮对话前的轮次索引
                    info_before = stm.session_info(session_id)
                    turn_index = info_before.get("total_turns", 0)

                    result = await run_pipeline(
                        query, session_id,
                        stm, entity_tracker, session["client"],
                        last_report,
                        _emit_sync,
                        selected_diagnosis=selected_diagnosis,
                        last_ambiguity=last_ambiguity,
                        selected_kbs=selected_kbs,
                        knowledge_bases=list(active_kbs.values()),
                    )

                    if result:
                        logger.info(f"run_pipeline 完成: result长度={len(result)}")

                    # ── 同步歧义缓存到全局 dict（跨请求存活）──
                    if last_ambiguity[0] is not None:
                        _ambiguity_cache[session_id] = last_ambiguity[0]
                        logger.info(f"歧义缓存已同步到全局: session={session_id}")

                    # ── 持久化：保存对话记录、会话状态和思考过程 ──
                    try:
                        store.save_turn(session_id, turn_index, query, result or "")
                        store.save_state(
                            session_id, entity_tracker.slots, last_report[0],
                            selected_kbs=selected_kbs,
                        )
                        # 保存思考过程
                        if thinking_events:
                            store.save_thinking(session_id, turn_index, thinking_events)
                        # 如果第一轮对话，自动更新标题
                        if turn_index == 0:
                            title = query[:20] if len(query) > 20 else query
                            store.update_title(session_id, title)
                            logger.info(f"会话标题已更新: {session_id} → {title}")
                        
                    except Exception as e:
                        logger.warning("保存会话失败: %s", e)
                        
                except Exception as e:
                    logger.error("run_pipeline 执行异常", exc_info=True)
                    error_payload = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
                    event_queue.put_nowait(error_payload)
                finally:
                    # 计算总耗时并发送
                    elapsed = time.time() - start_time
                    done_payload = json.dumps({"type": "done", "total_time": round(elapsed, 1)}, ensure_ascii=False)
                    event_queue.put_nowait(done_payload)
                    event_queue.put_nowait("[DONE]")

            # 启动后台任务
            asyncio.create_task(run())

            # 流式推送事件
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=120)
                    if event == "[DONE]":
                        logger.info("── event_stream(chat): 发送 [DONE]")
                        yield "data: [DONE]\n\n"
                        break
                    try:
                        evt = json.loads(event)
                        evt_type = evt.get("type", "?")
                        logger.info(f"── event_stream(chat): 发送 type={evt_type}, len={len(event)}")
                    except Exception:
                        logger.info(f"── event_stream(chat): 发送 raw, len={len(event)}")
                    yield f"data: {event}\n\n"
                except asyncio.TimeoutError:
                    logger.info("── event_stream(chat): 队列超时，退出")
                    break

        return StreamingResponse(event_stream(), media_type="text/event-stream")

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
        try:
            if os.path.exists(DB_PATH):
                client = _new_milvus_client()
                total = len(client.query(COLLECTION_NAME, filter="", output_fields=["count(*)"]))
                client.close()
        except Exception:
            logger.warning("查询 Milvus 知识库状态失败", exc_info=True)
        markdown_dir = get_markdown_dir()
        md_count = len(list(Path(markdown_dir).rglob("*.md"))) if os.path.isdir(markdown_dir) else 0
        meta_exists = any(kb.get("metadata_tree") for kb in store.list_knowledge_bases())
        return {"total": total, "md_count": md_count, "db_path": DB_PATH, "metadata_exists": meta_exists}

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
    ):
        uploads = list(files)
        # 兼容旧版前端/客户端使用单数 file 字段。
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
            kb_id = uuid.uuid4().hex
            kb_name = Path(upload.filename).stem[:255]
            safe_name = Path(upload.filename).name
            filepath = report_root / kb_id / safe_name
            filepath.parent.mkdir(parents=True, exist_ok=True)
            contents = await upload.read()
            filepath.write_bytes(contents)
            record = store.create_knowledge_base(
                kb_id, kb_name, status="uploaded", source_path=str(filepath),
            )
            knowledge_bases.append(record)
        return {"knowledge_bases": knowledge_bases}

    def _build_knowledge_base_task(kb_id: str) -> None:
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
        background_tasks.add_task(_build_knowledge_base_task, kb_id)
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

        # 如果 config.yml 中 api_key 为空但 .env 中有对应的 key，
        # 则返回掩码占位符，让前端知道已配置了 key
        for model_list_key, env_key_func in [
            ("llms", get_llm_api_key),
            ("embeddings", get_embed_api_key),
            ("reranks", get_rerank_api_key),
        ]:
            models = config_data.get(model_list_key)
            if isinstance(models, list):
                for m in models:
                    if not m.get("api_key") and env_key_func():
                        m["api_key"] = "••••••••••••••••••••••••"

        return {"config": config_data, "path": config_path}

    @app.post("/api/config", summary="保存配置", description="保存系统配置并重新加载生效")
    async def save_config(request: ConfigSaveRequest):
        config_data = request.config
        if config_data is None:
            return {"error": "缺少 config 参数"}

        # 如果 api_key 是前端掩码占位符，说明实际 key 在 .env 中，清空避免写入明文
        for model_list_key in ["llms", "embeddings", "reranks"]:
            models = config_data.get(model_list_key)
            if isinstance(models, list):
                for m in models:
                    if m.get("api_key") == "••••••••••••••••••••••••":
                        m["api_key"] = ""

        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "config.yml")
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        reload_config()
        return {"status": "ok", "message": "配置已保存并生效"}

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
                from openai import OpenAI
                client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")
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
                r = requests.post(base_url, headers=headers, json=payload, timeout=15)
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
                from openai import AsyncOpenAI
                client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed")
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

    logger.info(f"Web 服务启动: http://localhost:{port}, 模型={CHAT_MODEL}, Embedding={EMBED_MODEL}")
    print(f"\n{'='*60}")
    print(f"  影像报告生成Agent v2 Web 服务")
    print(f"  {'='*60}")
    print(f"  地址: http://localhost:{port}")
    print(f"  API 文档: http://localhost:{port}/docs")
    print(f"  生成模型: {CHAT_MODEL}")
    print(f"  Embedding: {EMBED_MODEL}")
    print(f"  {'='*60}\n")

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=port)
