"""FastAPI Web 服务层"""

import asyncio
import json
import logging
import os
import queue
import shutil
import time
from pathlib import Path

import yaml
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pymilvus import MilvusClient

from memory.entity_tracker import EntityTracker
from memory.short_term import ShortTermMemory
from memory.long_term import LongTermMemory
from memory.session_store import SessionStore
from config import (
    get_db_path, get_collection_name, get_markdown_dir, get_milvus_token,
    get_llm_model, get_llm_api_key, get_llm_base_url,
    get_embed_model, get_embed_api_key, get_embed_base_url,
    get_rerank_api_key, get_rerank_model, get_rerank_base_url,
    get_max_rounds, reload_config,
)
from data_pipeline.build_vector_db import build_db
from data_pipeline.extract_metadata import extract_all, save_json
from data_pipeline.process_reports import main as process_reports

from .schemas import (
    ChatRequest, ConfigSaveRequest, TestModelRequest,
    KBBuildRequest, ClearSessionRequest,
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

    front_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "front")
    if os.path.isdir(front_dir):
        app.mount("/static", StaticFiles(directory=front_dir), name="static")

    @app.get("/")
    async def index():
        index_path = os.path.join(front_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"message": "影像报告生成Agent v2", "docs": "/docs"}

    def _get_or_create_session(session_id):
        """从 SQLite 加载或创建会话，返回内存对象字典"""
        if store.session_exists(session_id):
            # ── 恢复已有会话 ──
            session_data = store.load_session(session_id)
            logger.info(f"恢复会话: {session_id}, 标题={session_data['title']}, 轮次={len(session_data['turns'])}")
            stm = ShortTermMemory(max_rounds=get_max_rounds())
            entity_tracker = EntityTracker(llm_chat_fn=chat_stream)
            ltm = LongTermMemory()
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
                "ltm": ltm,
                "client": client,
                "last_report": last_report,
                "last_ambiguity": last_ambiguity,
            }
        else:
            # ── 创建新会话 ──
            logger.info(f"创建新会话: {session_id}")
            stm = ShortTermMemory(max_rounds=get_max_rounds())
            entity_tracker = EntityTracker(llm_chat_fn=chat_stream)
            ltm = LongTermMemory()
            client = _new_milvus_client()
            client.load_collection(COLLECTION_NAME)

            store.create_session(session_id)

            return {
                "stm": stm,
                "entity_tracker": entity_tracker,
                "ltm": ltm,
                "client": client,
                "last_report": [""],
                "last_ambiguity": [None],
            }

    @app.post("/api/chat", summary="对话", description="发送用户输入并获取流式回复")
    async def chat(request: ChatRequest):
        query = request.query.strip()
        session_id = request.session_id
        selected_diagnosis = request.selected_diagnosis

        if not query:
            return {"error": "query 不能为空"}

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
                        stm, entity_tracker, session["ltm"], session["client"],
                        last_report,
                        _emit_sync,
                        selected_diagnosis=selected_diagnosis,
                        last_ambiguity=last_ambiguity,
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
                        store.save_state(session_id, entity_tracker.slots, last_report[0])
                        # 保存思考过程
                        if thinking_events:
                            store.save_thinking(session_id, turn_index, thinking_events)
                        # 如果第一轮对话，自动更新标题
                        if turn_index == 0:
                            title = query[:20] if len(query) > 20 else query
                            store.update_title(session_id, title)
                            logger.info(f"会话标题已更新: {session_id} → {title}")
                        
                        # 同步到长期记忆（更新用户偏好）
                        session["ltm"].sync_from_short_term(stm, session_id, entity_tracker)
                        logger.info(f"长期记忆已同步: session={session_id}")
                    except Exception as e:
                        logger.warning("保存会话/长期记忆失败: %s", e)
                        
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
        md_count = len([f for f in os.listdir(slices_dir) if f.endswith(".md")]) if os.path.isdir(slices_dir) else 0
        metadata_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_pipeline", "report_template", "metadata.json")
        meta_exists = os.path.exists(metadata_path)
        return {"total": total, "md_count": md_count, "db_path": DB_PATH, "metadata_exists": meta_exists}

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
        slices_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "app", "data_pipeline", "xlsx_slices")

        async def event_stream():
            loop = asyncio.get_event_loop()
            event_queue = queue.Queue()

            def _emit_log(data):
                event_queue.put_nowait(json.dumps(data, ensure_ascii=False))

            def run():
                build_db(markdown_dir, batch_size=batch_size, rebuild=rebuild, progress_callback=_emit_log)
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

    @app.post("/api/kb/extract-metadata", summary="提取元数据", description="从报告模板中提取元数据到 metadata.json")
    async def kb_extract_metadata():
        metadata_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_pipeline", "report_template")
        metadata_path = os.path.join(metadata_dir, "metadata.json")

        async def event_stream():
            loop = asyncio.get_event_loop()
            event_queue = queue.Queue()

            def _emit_log(data):
                event_queue.put_nowait(json.dumps(data, ensure_ascii=False))

            def run():
                _emit_log({"level": "info", "msg": "正在从 Markdown 提取元数据..."})
                metadata = extract_all(Path(get_markdown_dir()))
                save_json(metadata, Path(metadata_path))
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

    @app.post("/api/kb/upload", summary="上传报告模板", description="上传 .xlsx 报告模板文件并自动切片")
    async def kb_upload(file: UploadFile = File(...)):
        if not file.filename.endswith(".xlsx"):
            return {"error": "只支持 .xlsx 文件"}

        report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_pipeline", "report_template")
        slices_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_pipeline", "xlsx_slices")
        os.makedirs(report_dir, exist_ok=True)
        os.makedirs(slices_dir, exist_ok=True)

        filepath = os.path.join(report_dir, file.filename)

        async def event_stream():
            loop = asyncio.get_event_loop()
            event_queue = queue.Queue()

            def _emit_log(data):
                event_queue.put_nowait(json.dumps(data, ensure_ascii=False))

            def run():
                _emit_log({"level": "info", "msg": f"上传文件: {file.filename}"})
                with open(filepath, "wb") as f:
                    shutil.copyfileobj(file.file, f)
                _emit_log({"level": "info", "msg": "正在整理 Markdown 报告..."})
                process_reports()
                _emit_log({"level": "done", "msg": "Markdown 报告整理完成"})
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
