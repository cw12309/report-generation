"""Report 工作台 API 路由"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from .auth import require_admin, require_user
from .schemas import (
    GenerateRequest,
    IterateRequest,
    ReviewRequest,
    SaveRequest,
    ReportResponse,
    ReportSession,
)
from .service import ReportService, CHAT_STANDARD_REPLY

logger = logging.getLogger(__name__)

# 路由级依赖：工作台全部业务接口需登录（/auth/* 在独立 router，不受影响）
router = APIRouter(
    prefix="/api/report-workbench",
    tags=["report-workbench"],
    dependencies=[Depends(require_user)],
)

# 全局服务实例
_service = ReportService()

# 简单的内存存储（后续可改为数据库）
_sessions: dict[str, ReportSession] = {}

# 历史报告持久化（JSON 文件）
_HISTORY_FILE = Path(__file__).resolve().parents[2] / "data" / "report_history.json"
# 每个用户仅保留最近 N 条历史报告，超出自动删除该用户最旧会话
_HISTORY_LIMIT = 100

# 历史列表内存缓存：{"mtime","size","items"}，文件未变时直接命中，避免重复全量读盘
_history_cache: dict = {"mtime": 0.0, "size": -1, "items": []}


def _load_history() -> list:
    """读取历史列表：内存缓存 + mtime/size 失效，避免每次请求都全量读盘解析（446KB JSON 同步 IO 会阻塞事件循环）"""
    try:
        st = _HISTORY_FILE.stat()
    except OSError:
        _history_cache.update(mtime=0.0, size=-1, items=[])
        return []
    if _history_cache["size"] == st.st_size and _history_cache["mtime"] == st.st_mtime:
        return _history_cache["items"]
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"读取报告历史失败: {e}")
        return []
    _history_cache.update(mtime=st.st_mtime, size=st.st_size, items=items)
    return items


def _write_history(history: list):
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        st = _HISTORY_FILE.stat()
        _history_cache.update(mtime=st.st_mtime, size=st.st_size, items=history)
        return True
    except Exception as e:
        logger.warning(f"写入报告历史失败: {e}")
        return False


def _persist_history(session: ReportSession, owner: str = ""):
    """将会话写入历史（按 conversation_id upsert，记录归属用户；
    每个用户仅保留最近 _HISTORY_LIMIT 条，超出自动过期该用户最旧会话）"""
    history = _load_history()
    history = [h for h in history if h.get("conversation_id") != session.conversation_id]
    record = session.dict()
    if owner:
        record["owner"] = owner
    history.insert(0, record)
    if owner:
        mine = [h for h in history if h.get("owner") == owner]
        if len(mine) > _HISTORY_LIMIT:
            drop_ids = {h.get("conversation_id") for h in mine[_HISTORY_LIMIT:]}
            logger.info(f"用户 {owner} 历史超过 {_HISTORY_LIMIT} 条，自动过期最旧 {len(drop_ids)} 条会话")
            history = [h for h in history if h.get("conversation_id") not in drop_ids]
            for cid in drop_ids:
                _sessions.pop(cid, None)
    return _write_history(history)


async def _complete_template_reference(session: ReportSession, username: str):
    reference = session.template_reference
    if not reference or reference.get("report_version") != session.current_version:
        return
    from . import template_usage
    try:
        await asyncio.to_thread(template_usage.mark_success, username, reference)
    except Exception:
        logger.warning("模板生成成功计数写入失败", exc_info=True)


def _get_or_restore_session(conversation_id: str) -> Optional[ReportSession]:
    """获取会话；内存中不存在时从历史文件恢复（服务重启后历史报告仍可对话修改）"""
    session = _sessions.get(conversation_id)
    if session:
        return session
    for h in _load_history():
        if h.get("conversation_id") == conversation_id:
            try:
                restored = ReportSession(**h)
                _sessions[conversation_id] = restored
                logger.info(f"从历史恢复会话: {conversation_id}")
                return restored
            except Exception as e:
                logger.warning(f"恢复历史会话失败 {conversation_id}: {e}")
                return None
    return None


def _sse(data: dict) -> str:
    """序列化为 SSE data 帧"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# 流式队列中允许下发的事件类型（token=报告正文增量，reasoning=思考过程增量，notice=系统提示）
_STREAM_EVENT_TYPES = ("token", "reasoning", "notice")


def _make_stream_event(kind: str, payload: str) -> str:
    """把队列中的 (kind, payload) 转成 SSE 帧；未知类型返回空串（调用方跳过）"""
    if kind in _STREAM_EVENT_TYPES and payload:
        return _sse({"type": kind, "content": payload})
    return ""


async def _emit_sse_event(event_type: str, data: dict):
    """SSE事件发射器（占位）"""
    logger.debug(f"SSE事件: {event_type} = {data}")


@router.get("/history")
async def list_history(user: dict = Depends(require_user)):
    """历史报告列表（仅当前用户自己的，摘要，按更新时间倒序）"""
    username = user.get("username", "")
    history = [h for h in _load_history() if h.get("owner") == username]
    items = []
    for h in history:
        report = h.get("report") or {}
        findings = report.get("imaging_findings") or ""
        items.append({
            "conversation_id": h.get("conversation_id"),
            "report_id": h.get("report_id"),
            "route_id": h.get("route_id"),
            "route_name": h.get("route_name"),
            "question_round": h.get("question_round", 0),
            "current_version": h.get("current_version", 0),
            "created_at": h.get("created_at"),
            "updated_at": h.get("updated_at"),
            "preview": findings[:60].replace("\n", " "),
        })
    return {"history": items}


def _find_history_item(conversation_id: str, username: str = "") -> Optional[dict]:
    """按会话 id 查历史记录；username 非空时仅匹配归属该用户的历史"""
    for h in _load_history():
        if h.get("conversation_id") == conversation_id and (not username or h.get("owner") == username):
            return h
    return None


@router.get("/history/{conversation_id}")
async def get_history_item(conversation_id: str, user: dict = Depends(require_user)):
    """获取历史报告完整内容（仅本人历史）"""
    h = _find_history_item(conversation_id, user.get("username", ""))
    if not h:
        raise HTTPException(status_code=404, detail="历史报告不存在")
    return {"session": h}


@router.delete("/history/{conversation_id}")
async def delete_history_item(conversation_id: str, user: dict = Depends(require_user)):
    """删除历史报告（仅本人历史）"""
    history = _load_history()
    target = _find_history_item(conversation_id, user.get("username", ""))
    if not target:
        raise HTTPException(status_code=404, detail="历史报告不存在")
    _write_history([h for h in history if h.get("conversation_id") != conversation_id])
    _sessions.pop(conversation_id, None)
    return {"success": True}


@router.get("/models")
async def list_models():
    """列出可选模型（config.yml 中有效的 LLM 条目）"""
    from app.config import get_llm_entries, get_llm_model, get_llm_base_url
    entries = get_llm_entries()
    active_model = get_llm_model()
    active_url = get_llm_base_url()
    active_name = ""
    for e in entries:
        if e["model"] == active_model and e["base_url"] == active_url:
            active_name = e["name"]
            break
    return {"models": entries, "active": active_name or (entries[0]["name"] if entries else "")}


@router.get("/models/check")
async def check_model(name: str = ""):
    """探活指定模型是否可响应（选择模型前预校验）"""
    from app.chat.llm_client import check_model_available
    ok, message = await check_model_available(name)
    return {"ok": ok, "message": message}


@router.get("/models/thinking-test")
async def test_model_thinking(name: str = ""):
    """实测指定模型的思考开关效果（开/关各发一次请求比较输出）"""
    from app.chat.llm_client import test_thinking_switch
    return await test_thinking_switch(name)


@router.get("/sessions/{conversation_id}/generate")
async def generate_report(
    conversation_id: str,
    input_text: str = "",
    language: str = "zh-CN",
    modality: str = "",
    body_part: str = "",
    route_id: str = "",
    llm_name: str = "",
    enable_thinking: bool = False,
    user: dict = Depends(require_user),
):
    """生成报告（SSE流式，GET方法用于EventSource）"""
    from . import user_env

    # 用户环境隔离：本请求的路由/提示词/模板走该用户的合并视图
    user_env.set_current_user(user.get("username", ""))

    # 构建请求对象
    request = GenerateRequest(
        input_text=input_text,
        language=language,
        modality=modality or None,
        body_part=body_part or None,
        route_id=route_id or None,
        llm_name=llm_name or "",
        enable_thinking=bool(enable_thinking)
    )
    
    async def event_stream():
        """SSE事件流（真流式：token 实时推送 + 完整耗时记录）"""
        # 会话ID
        yield _sse({'type': 'conversation', 'conversation_id': conversation_id})

        # 闲聊分流：非报告类输入返回标准回复，不调用LLM（显式指定模板路由时跳过分流）
        if not route_id and not _service.is_report_intent(input_text):
            logger.info(f"非报告类输入，走闲聊标准回复: {input_text[:30]}")
            yield _sse({'type': 'chat', 'content': CHAT_STANDARD_REPLY})
            yield _sse({'type': 'done', 'status': 'chat', 'elapsed_seconds': 0.0})
            return

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        # 提前解析路由并立即下发，减少用户等待空白期
        early_route = _service.route_resolver.resolve(
            input_text=input_text,
            route_id=route_id or None,
        )
        if early_route:
            request.route_id = early_route.id
            yield _sse({
                'type': 'route_resolved',
                'route_id': early_route.id,
                'route_name': early_route.name,
            })
            yield _sse({'type': 'status', 'status': 'generating', 'message': '正在生成报告，请稍候…'})

        def on_token(content: str):
            """LLM token 回调（同步，跨线程安全入队）"""
            if content:
                loop.call_soon_threadsafe(queue.put_nowait, ('token', content))

        def on_notice(content: str):
            """通知回调（模型降级等，同步入队）"""
            if content:
                loop.call_soon_threadsafe(queue.put_nowait, ('notice', content))

        def on_reasoning(content: str):
            """思考过程回调（仅开启思考的模型有内容）"""
            if content:
                loop.call_soon_threadsafe(queue.put_nowait, ('reasoning', content))

        task = asyncio.create_task(
            _service.generate_report(
                request=request,
                conversation_id=conversation_id,
                emit_event=None,
                on_token=on_token,
                on_notice=on_notice,
                username=user.get("username", ""),
                on_reasoning=on_reasoning,
            )
        )

        try:
            # 先推流式 token，直到任务完成
            while not task.done():
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=0.5)
                    sse = _make_stream_event(kind, payload)
                    if sse:
                        yield sse
                except asyncio.TimeoutError:
                    continue

            # 排空剩余 token / notice / reasoning
            while not queue.empty():
                kind, payload = queue.get_nowait()
                sse = _make_stream_event(kind, payload)
                if sse:
                    yield sse

            session, llm_output = task.result()
            _sessions[conversation_id] = session
            if _persist_history(session, owner=user.get("username", "")):
                await _complete_template_reference(session, user.get("username", ""))

            # 路由解析事件
            yield _sse({
                'type': 'route_resolved',
                'route_id': session.route_id,
                'route_name': session.route_name,
            })

            # 报告更新事件（含耗时）
            yield _sse({
                'type': 'report_updated',
                'session': session.dict(),
                'elapsed_seconds': session.elapsed_seconds,
            })

            # 追问
            if llm_output.questions:
                yield _sse({
                    'type': 'report_questions',
                    'questions': [q.dict() for q in llm_output.questions],
                })

            # 助手消息
            if llm_output.assistant_message:
                yield _sse({
                    'type': 'message',
                    'content': llm_output.assistant_message or '报告已生成。',
                })

            # 完成事件（含完整耗时）
            yield _sse({
                'type': 'done',
                'status': 'completed',
                'elapsed_seconds': session.elapsed_seconds,
            })

        except Exception as e:
            logger.error(f"生成报告失败: {e}", exc_info=True)
            if not task.done():
                task.cancel()
            yield _sse({'type': 'error', 'message': str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{conversation_id}/iterate")
async def iterate_report(conversation_id: str, request: IterateRequest, user: dict = Depends(require_user)):
    """迭代报告（SSE流式）"""
    from . import user_env

    user_env.set_current_user(user.get("username", ""))

    # 获取当前会话（内存缺失时从历史恢复，支持历史报告对话修改）
    session = _get_or_restore_session(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 版本冲突保护：提交基于的版本落后于服务端当前版本时拒绝，防止并发/重复提交静默覆盖
    if request.base_version != session.current_version:
        raise HTTPException(
            status_code=409,
            detail=f"报告已被更新到 v{session.current_version}（本次提交基于 v{request.base_version}），请刷新报告后重试",
        )

    async def event_stream():
        """SSE事件流（真流式 token 推送 + 耗时记录）"""
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def on_token(content: str):
            if content:
                loop.call_soon_threadsafe(queue.put_nowait, ('token', content))

        def on_notice(content: str):
            if content:
                loop.call_soon_threadsafe(queue.put_nowait, ('notice', content))

        def on_reasoning(content: str):
            if content:
                loop.call_soon_threadsafe(queue.put_nowait, ('reasoning', content))

        task = asyncio.create_task(
            _service.iterate_report(
                request=request,
                current_session=session,
                emit_event=None,
                on_token=on_token,
                on_notice=on_notice,
                on_reasoning=on_reasoning,
                username=user.get("username", ""),
            )
        )

        try:
            while not task.done():
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=0.5)
                    sse = _make_stream_event(kind, payload)
                    if sse:
                        yield sse
                except asyncio.TimeoutError:
                    continue

            while not queue.empty():
                kind, payload = queue.get_nowait()
                sse = _make_stream_event(kind, payload)
                if sse:
                    yield sse

            updated_session, llm_output = task.result()
            _sessions[conversation_id] = updated_session
            if _persist_history(updated_session, owner=user.get("username", "")):
                await _complete_template_reference(updated_session, user.get("username", ""))

            yield _sse({
                'type': 'report_updated',
                'session': updated_session.dict(),
                'elapsed_seconds': updated_session.elapsed_seconds,
            })

            if llm_output.questions:
                yield _sse({
                    'type': 'report_questions',
                    'questions': [q.dict() for q in llm_output.questions],
                })

            yield _sse({
                'type': 'message',
                'content': llm_output.assistant_message or '报告已更新。',
                # 追问达上限后的更新消息带评价卡，前端渲染 采纳/修改/拒绝 三按钮
                'review': updated_session.messages[-1].review if updated_session.messages else None,
                'message_index': len(updated_session.messages) - 1 if updated_session.messages else -1,
            })

            yield _sse({
                'type': 'done',
                'status': 'completed',
                'elapsed_seconds': updated_session.elapsed_seconds,
            })

        except Exception as e:
            logger.error(f"迭代报告失败: {e}", exc_info=True)
            if not task.done():
                task.cancel()
            yield _sse({'type': 'error', 'message': str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{conversation_id}/review")
async def submit_review(conversation_id: str, request: ReviewRequest, user: dict = Depends(require_user)):
    """记录用户对某次报告更新的采纳程度（采纳/修改/拒绝），随会话持久化"""
    session = _get_or_restore_session(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    if request.message_index >= len(session.messages or []):
        raise HTTPException(status_code=404, detail="消息不存在")
    msg = session.messages[request.message_index]
    if not msg.review:
        raise HTTPException(status_code=400, detail="该消息不含评价卡")
    msg.review["choice"] = request.choice
    msg.review["comment"] = request.comment
    msg.review["chosen_by"] = user.get("username", "")
    msg.review["chosen_at"] = datetime.now(timezone.utc).isoformat()
    _sessions[conversation_id] = session
    _persist_history(session, owner=user.get("username", ""))
    logger.info(
        f"报告更新评价: conversation={conversation_id}, version={msg.review.get('version')}, "
        f"choice={request.choice}, user={user.get('username', '')}"
    )
    return {"ok": True, "review": msg.review}


@router.get("/sessions/{conversation_id}")
async def get_report_session(conversation_id: str):
    """获取当前报告"""
    session = _get_or_restore_session(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    return ReportResponse(
        conversation_id=conversation_id,
        report=session,
    )


@router.post("/sessions/{conversation_id}/save")
async def save_report(conversation_id: str, request: SaveRequest, user: dict = Depends(require_user)):
    """手动保存报告"""
    session = _get_or_restore_session(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 版本冲突保护：提交基于的版本落后于服务端当前版本时拒绝，防止并发/重复提交静默覆盖
    if request.base_version != session.current_version:
        raise HTTPException(
            status_code=409,
            detail=f"报告已被更新到 v{session.current_version}（本次提交基于 v{request.base_version}），请刷新报告后重试",
        )

    # 更新报告内容
    session.report.imaging_findings = request.imaging_findings
    session.report.imaging_diagnosis = request.imaging_diagnosis
    session.current_version += 1
    session.is_dirty = False
    
    from datetime import datetime, timezone
    session.updated_at = datetime.now(timezone.utc).isoformat()
    
    _sessions[conversation_id] = session
    _persist_history(session, owner=user.get("username", ""))
    
    return {"success": True, "version": session.current_version}


@router.get("/routes")
async def list_routes(user: dict = Depends(require_user)):
    """列出当前用户可用的报告路由（基线+个人新增/启停覆写，含快捷输入标签）"""
    from . import route_admin, user_env
    from .schemas import RouteInfo

    username = user.get("username", "")
    config = user_env.merged_routes(username, route_admin.load_routes_raw())
    routes = []
    for rd in config.get("routes") or []:
        try:
            r = RouteInfo(**rd)
        except Exception:
            continue
        if r.enabled:
            routes.append(r)
    return {
        "routes": [
            {
                "id": r.id,
                "name": r.name,
                "aliases": r.aliases,
                "quick_tags": r.quick_tags,
            }
            for r in routes
        ]
    }


# ===== 路由配置管理 =====

@router.get("/admin/routes")
async def admin_list_routes(user: dict = Depends(require_user)):
    """路由配置全量视图（基线+个人覆写合并，含专科Prompt全文、校验器联动规则）；改动仅影响当前用户"""
    from . import route_admin, user_env

    username = user.get("username", "")
    config = user_env.merged_routes(username, route_admin.load_routes_raw())
    linkage = route_admin.validator_linkage()
    base_ids = {r.get("id") for r in (route_admin.load_routes_raw().get("routes") or [])}
    my_routes = user_env.load_env(username).get("routes") or {}
    items = []
    for r in config.get("routes") or []:
        rid = r.get("id")
        override = user_env.get_prompt_override(username, rid)
        items.append({
            **r,
            "prompt_md": override if override is not None else route_admin.read_prompt(r),
            "is_custom": rid not in base_ids,
            "has_override": rid in my_routes,
        })
    return {"routes": items, "validator_linkage": linkage}


@router.post("/admin/routes/{route_id}/toggle")
async def admin_toggle_route(route_id: str, user: dict = Depends(require_user)):
    """启用/停用路由（仅对当前用户生效）"""
    from . import route_admin, user_env

    username = user.get("username", "")
    config = route_admin.load_routes_raw()
    base_enabled = True
    is_base = False
    for r in config.get("routes") or []:
        if r.get("id") == route_id:
            base_enabled = bool(r.get("enabled", True))
            is_base = True
            break
    merged = user_env.merged_routes(username, config)
    hit = next((r for r in merged.get("routes") or [] if r.get("id") == route_id), None)
    if not hit:
        raise HTTPException(status_code=404, detail="路由不存在")
    if route_id == "generic":
        raise HTTPException(status_code=400, detail="通用路由不可停用")
    enabled = user_env.toggle_route(username, route_id, base_enabled if is_base else bool(hit.get("enabled", True)))
    return {"success": True, "enabled": enabled}


@router.get("/admin/validator-rules", dependencies=[Depends(require_admin)])
async def admin_get_validator_rules():
    """校验器联动规则当前值（可编辑字段）"""
    from . import route_admin

    return {"rules": route_admin.validator_linkage()}


@router.post("/admin/validator-rules", dependencies=[Depends(require_admin)])
async def admin_save_validator_rules(payload: dict):
    """保存校验器联动规则并热加载生效"""
    from .report_validator import save_validator_rules

    save_validator_rules(payload or {})
    return {"success": True}


@router.post("/admin/routes/parse-doc")
async def admin_parse_reference_doc(
    file: UploadFile = File(...),
    focus: str = Form(""),
    llm_name: str = Form(""),
    user: dict = Depends(require_user),
):
    """解析上传的参考资料文档（pdf/docx/xlsx/txt/md/csv）为纯文本，并按路由名称提取相关材料"""
    from . import route_admin

    data = await file.read()
    try:
        result = route_admin.extract_reference_text(
            file.filename or "", data, focus=focus or "", llm_name=llm_name or ""
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/admin/routes/web-search/check")
async def admin_web_search_check(user: dict = Depends(require_user)):
    """联网检索连通性探活（点击联网检索前先测，通了才发起正式检索）"""
    from . import route_admin

    return await asyncio.to_thread(route_admin.check_web_search_available)


@router.get("/admin/routes/reference-limits")
async def admin_reference_limits(llm_name: str = ""):
    """Reference character limits for the selected route model."""
    from app.config import get_route_draft_context_config
    limits = get_route_draft_context_config(llm_name)
    limits["web_search_max_chars"] = min(limits["web_search_max_chars"], limits["final_ref_max_chars"])
    return limits


@router.post("/admin/routes/web-search")
async def admin_web_search_reference(payload: dict, user: dict = Depends(require_user)):
    """联网检索与路由名称相关的公开资料（过滤后仅作参考材料）"""
    from . import route_admin

    focus = str((payload or {}).get("focus") or "").strip()
    if not focus:
        raise HTTPException(status_code=400, detail="请先填写路由名称再联网检索")
    try:
        result = await asyncio.to_thread(route_admin.web_search_reference, focus, llm_name=str((payload or {}).get("llm_name") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.post("/admin/routes/draft")
async def admin_generate_draft(payload: dict, user: dict = Depends(require_user)):
    """新路由草稿生成 skill：依据部位名+可选参考资料生成完整路由配置草稿（不落盘）"""
    from . import route_admin

    name = str((payload or {}).get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    try:
        draft = await route_admin.generate_route_draft(
            name=name,
            body_part=str((payload or {}).get("body_part") or name).strip(),
            modality=str((payload or {}).get("modality") or "").strip(),
            reference_text=str((payload or {}).get("reference_text") or ""),
            llm_name=str((payload or {}).get("llm_name") or ""),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"draft": draft}


@router.post("/admin/routes/{route_id}")
async def admin_update_route(route_id: str, payload: dict, user: dict = Depends(require_user)):
    """更新路由配置（元信息+专科Prompt+胶囊），仅保存到当前用户的个人环境，不影响他人"""
    from . import route_admin, user_env

    username = user.get("username", "")
    config = route_admin.load_routes_raw()
    existing = [r.get("id") for r in config.get("routes") or []]
    payload = dict(payload or {})
    payload["id"] = route_id
    errors = route_admin.validate_route_payload(payload, existing, allow_same=True)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    prompt_md = payload.pop("prompt_md", None)
    if route_id not in existing and not str(prompt_md or "").strip():
        raise HTTPException(status_code=400, detail="新增路由必须提供专科 Prompt 内容")
    payload.setdefault("prompt_file", f"{route_id}.md")
    user_env.upsert_route(username, payload, prompt_md)
    return {"success": True}


@router.post("/admin/routes")
async def admin_create_route(payload: dict, user: dict = Depends(require_user)):
    """新增路由：仅写入当前用户的个人环境（基线 routes.yml 不变），保存后对该用户立即生效"""
    from . import route_admin, user_env

    username = user.get("username", "")
    config = route_admin.load_routes_raw()
    existing = [r.get("id") for r in config.get("routes") or []]
    payload = dict(payload or {})
    errors = route_admin.validate_route_payload(payload, existing)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    prompt_md = payload.pop("prompt_md", None)
    if not str(prompt_md or "").strip():
        raise HTTPException(status_code=400, detail="新增路由必须提供专科 Prompt 内容")
    rid = str(payload.get("id") or "").strip()
    payload["prompt_file"] = f"{rid}.md"
    payload.setdefault("enabled", True)
    user_env.upsert_route(username, payload, prompt_md)
    return {"success": True, "route_id": rid}


# ===== 报告模板库管理 =====

def _personal_template_id(username: str) -> str:
    """个人新增模板 id：u{用户名hash}_{时间戳}，避免与基线/他人冲突"""
    import hashlib
    import time as _t

    h = hashlib.md5(username.encode("utf-8")).hexdigest()[:6]
    return f"u{h}_{int(_t.time())}"


@router.get("/admin/templates")
async def admin_list_templates(user: dict = Depends(require_user)):
    """模板库全量列表（基线+个人覆写合并，含禁用模板）；个人改动不影响他人"""
    from . import template_admin, user_env

    username = user.get("username", "")
    merged = user_env.merged_templates(username, template_admin.load_templates())
    base_ids = {t.get("id") for t in template_admin.load_templates()}
    my_tpls = user_env.load_env(username).get("templates") or {}
    items = [{**t, "is_custom": t.get("id") not in base_ids, "has_override": t.get("id") in my_tpls} for t in merged]
    from . import template_usage
    try:
        items = await asyncio.to_thread(template_usage.annotate, items, username)
    except Exception:
        logger.warning("模板引用统计暂不可用", exc_info=True)
        items = [{**item, "stats_available": False, "legacy_usage_count": item.get("usage_count", 0),
                  "injection_count": None, "success_count": None} for item in items]
    return {"templates": items}


@router.post("/admin/templates")
async def admin_create_template(payload: dict, user: dict = Depends(require_user)):
    """新增模板（手工录入），仅存入当前用户个人环境，入库前跑范文结构校验"""
    from . import template_admin, user_env

    username = user.get("username", "")
    payload = dict(payload or {})
    payload.pop("id", None)
    errors = template_admin.validate_template_payload(payload, [])
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    tid = _personal_template_id(username)
    payload["id"] = tid
    tpl = template_admin.normalize_template(payload)
    tpl.setdefault("usage_count", 0)
    tpl["owner"] = username
    user_env.upsert_template(username, tpl)
    return {"success": True, "template_id": tid}


@router.post("/admin/templates/{template_id}")
async def admin_update_template(template_id: str, payload: dict, user: dict = Depends(require_user)):
    """编辑模板（所见/诊断/标签/名称），覆写仅保存到当前用户个人环境"""
    from . import template_admin, user_env

    username = user.get("username", "")
    payload = dict(payload or {})
    payload["id"] = template_id
    errors = template_admin.validate_template_payload(payload, [], allow_same=True)
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    base = next((t for t in template_admin.load_templates() if t.get("id") == template_id), None)
    mine = (user_env.load_env(username).get("templates") or {}).get(template_id)
    existing = base or mine
    if existing is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    tpl = template_admin.normalize_template(payload, existing)
    user_env.upsert_template(username, tpl)
    return {"success": True}


@router.post("/admin/templates/{template_id}/toggle")
async def admin_toggle_template(template_id: str, user: dict = Depends(require_user)):
    """启用/停用模板（仅对当前用户生效；停用后不再参与该用户的生成注入）"""
    from . import template_admin, user_env

    username = user.get("username", "")
    base = next((t for t in template_admin.load_templates() if t.get("id") == template_id), None)
    mine = (user_env.load_env(username).get("templates") or {}).get(template_id)
    if base is None and mine is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    base_enabled = bool((base or mine).get("enabled", True))
    enabled = user_env.toggle_template(username, template_id, base_enabled)
    return {"success": True, "enabled": enabled}


@router.delete("/admin/templates/{template_id}")
async def admin_delete_template(template_id: str, user: dict = Depends(require_user)):
    """删除模板（仅对当前用户生效：基线模板打个人删除标记，个人新增直接移除）"""
    from . import template_admin, user_env

    username = user.get("username", "")
    is_base = any(t.get("id") == template_id for t in template_admin.load_templates())
    mine = (user_env.load_env(username).get("templates") or {}).get(template_id)
    if not is_base and mine is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    user_env.delete_template(username, template_id, is_base)
    return {"success": True}


@router.post("/sessions/{conversation_id}/favorite")
async def admin_favorite_session(conversation_id: str, payload: dict, user: dict = Depends(require_user)):
    """收藏当前会话报告为模板候选，存入当前用户个人环境（默认停用，待确认启用）"""
    from . import template_admin, user_env

    username = user.get("username", "")
    session = _get_or_restore_session(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    payload = dict(payload or {})
    # 医生在确认弹窗中可编辑所见/诊断范文；未提供则取当前会话报告原文
    findings = str(payload.get("imaging_findings") or "").strip() or session.report.imaging_findings
    diagnosis = str(payload.get("imaging_diagnosis") or "").strip() or session.report.imaging_diagnosis
    tpl = {
        "id": _personal_template_id(username),
        "route_id": payload.get("route_id") or session.route_id,
        "name": str(payload.get("name") or f"{session.route_name or '报告'}优秀范例").strip(),
        "tags": payload.get("tags") or [],
        "match_keywords": payload.get("match_keywords") or [],
        "imaging_findings": findings,
        "imaging_diagnosis": diagnosis,
        "source": "session",
        "enabled": bool(payload.get("enabled", True)),
        "quality_score": 5,
        "owner": username,
    }
    errors = template_admin.validate_template_payload(tpl, [])
    if errors:
        raise HTTPException(status_code=400, detail="；".join(errors))
    created = template_admin.normalize_template(tpl)
    created.setdefault("usage_count", 0)
    user_env.upsert_template(username, created)
    return {"success": True, "template_id": created["id"], "enabled": created.get("enabled", True)}


# ===== 管理员：用户环境查看面板 =====


@router.get("/admin/users", dependencies=[Depends(require_admin)])
async def admin_list_users():
    """用户列表 + 各用户个人环境概览（路由/模板覆写数量）"""
    from . import auth, user_env

    users = []
    for u in auth._load_users():
        uname = u.get("username", "")
        env = user_env.load_env(uname)
        users.append({
            "username": uname,
            "role": u.get("role", "user"),
            "display_name": u.get("display_name", uname),
            "created_at": u.get("created_at", 0),
            "custom_routes": len(env.get("routes") or {}),
            "custom_templates": len(env.get("templates") or {}),
            "deleted_templates": len(env.get("deleted_templates") or []),
        })
    return {"users": users}


@router.get("/admin/users/{username}/env", dependencies=[Depends(require_admin)])
async def admin_get_user_env(username: str, scope: str = "changes"):
    """查看指定用户的路由/模板环境：scope=changes 仅个人改动；scope=all 为生成时实际生效的全量视图"""
    from . import user_env

    return user_env.admin_snapshot(username, scope if scope in ("changes", "all") else "changes")


@router.get("/admin/insights", dependencies=[Depends(require_admin)])
async def admin_insights():
    """管理中台数据：报告总量/评价分布（采纳·修改·拒绝）/每用户统计/每份报告明细"""
    history = _load_history()
    users_stat: dict = {}
    reports = []
    total_review = {"adopt": 0, "modify": 0, "reject": 0, "pending": 0}

    for h in history:
        owner = h.get("owner") or "未知"
        messages = h.get("messages") or []
        # 每份报告的评价：取消息内 review 卡（一次更新一张卡）
        reviews = [m.get("review") for m in messages if isinstance(m, dict) and m.get("review")]
        dist = {"adopt": 0, "modify": 0, "reject": 0, "pending": 0}
        last_choice = None
        for rv in reviews:
            choice = rv.get("choice") or "pending"
            dist[choice if choice in dist else "pending"] += 1
            if rv.get("choice"):
                last_choice = rv.get("choice")
        for k in total_review:
            total_review[k] += dist[k]
        report = h.get("report") or {}
        findings = report.get("imaging_findings") or ""
        diagnosis = report.get("imaging_diagnosis") or ""
        reports.append({
            "conversation_id": h.get("conversation_id", ""),
            "owner": owner,
            "route_name": h.get("route_name", ""),
            "version": h.get("current_version", 0),
            "question_round": h.get("question_round", 0),
            "review_dist": dist,
            "last_choice": last_choice,
            "updated_at": h.get("updated_at", ""),
            "findings_len": len(findings),
            "diagnosis_len": len(diagnosis),
            "findings": findings,
            "diagnosis": diagnosis,
        })
        st = users_stat.setdefault(owner, {
            "username": owner, "reports": 0,
            "adopt": 0, "modify": 0, "reject": 0, "pending": 0,
        })
        st["reports"] += 1
        for k in dist:
            st[k] += dist[k]

    users = []
    for st in users_stat.values():
        rated = st["adopt"] + st["modify"] + st["reject"]
        st["rated"] = rated
        st["adopt_rate"] = round(st["adopt"] / rated, 4) if rated else None
        users.append(st)
    users.sort(key=lambda x: x["reports"], reverse=True)

    rated_total = total_review["adopt"] + total_review["modify"] + total_review["reject"]
    return {
        "totals": {
            "reports": len(reports),
            "users": len(users),
            "reviews": sum(total_review.values()),
            "rated": rated_total,
            "review_dist": total_review,
            "adopt_rate": round(total_review["adopt"] / rated_total, 4) if rated_total else None,
        },
        "users": users,
        "reports": reports,
    }


@router.post("/sessions/{conversation_id}/export/word")
async def export_word(conversation_id: str):
    """导出报告为 Word 文档（.docx），返回文件流"""
    session = _get_or_restore_session(conversation_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        from docx import Document
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError:
        raise HTTPException(status_code=500, detail="服务端未安装 python-docx，无法导出 Word")

    from datetime import datetime, timezone

    doc = Document()

    # 全局中文字体
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    def set_cn_font(run, name="宋体"):
        run.font.name = name
        run._element.rPr.rFonts.set(qn("w:eastAsia"), name)

    # 报告头
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(f"{session.route_name} 影像报告")
    run.bold = True
    run.font.size = Pt(16)
    set_cn_font(run, "黑体")

    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_run = meta.add_run(
        f"报告编号：{session.report_id}    生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}    版本：v{session.current_version}"
    )
    meta_run.font.size = Pt(9)
    set_cn_font(meta_run)

    def add_section(heading: str, text: str):
        h = doc.add_paragraph()
        h_run = h.add_run(heading)
        h_run.bold = True
        h_run.font.size = Pt(13)
        set_cn_font(h_run, "黑体")
        for line in (text or "").split("\n"):
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(2)
            r = p.add_run(line)
            set_cn_font(r)

    add_section("影像所见", session.report.imaging_findings)
    add_section("影像诊断", session.report.imaging_diagnosis)

    # 免责声明
    note = doc.add_paragraph()
    note_run = note.add_run("本报告由AI辅助生成，仅供临床参考，最终诊断以医师审核签发为准。")
    note_run.font.size = Pt(8)
    set_cn_font(note_run)

    import io
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    filename = f"报告_{session.route_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
    from urllib.parse import quote
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
