"""Agent 核心编排逻辑"""

import json
import logging

from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree

from memory.retriever import MemoryRetriever
from tools.registry import ToolRegistry, ToolResult
from prompt.builder import PromptBuilder
from rag.query_rewrite import needs_rewrite, rewrite_query, standardize_query
from rag.query_planner import build_query_plan
from rag.retrieval import retrieve_with_fallback
from rag.metadata_navigator import MetadataNavigator
from config import get_rag_top_k, get_rerank_top_k

from .llm_client import get_embedding, chat_stream, chat_with_tools, rerank_with_retry
from .report_schema import (
    REPORT_SCHEMA_VERSION,
    REPORT_TOOL_NAMES,
    ReportSchemaError,
    looks_like_serialized_tool_call,
    parse_report_output,
    serialize_report,
)
from .utils import json_to_display

logger = logging.getLogger(__name__)

RAG_TOP_K = get_rag_top_k()
RERANK_TOP_K = get_rerank_top_k()


# =============================================================================
# RAG 检索
# =============================================================================
def search_reports(
    query,
    top_k=RAG_TOP_K,
    rerank_top_k=RERANK_TOP_K,
    client=None,
    _emit=None,
    context=None,
    selected_kbs=None,
    knowledge_bases=None,
):
    """Plan filters, run Hybrid Search with fallback, then Rerank."""
    if rerank_top_k > top_k:
        rerank_top_k = top_k

    navigator = (
        MetadataNavigator.from_knowledge_bases(knowledge_bases)
        if knowledge_bases is not None else MetadataNavigator()
    )
    plan = build_query_plan(query, context=context, navigator=navigator)
    query_vec = get_embedding(plan["semantic_query"])
    candidates, recall_details = retrieve_with_fallback(
        plan=plan,
        query_vector=query_vec,
        top_k=top_k,
        client=client,
        selected_kbs=selected_kbs,
    )

    if not candidates:
        return "未检索到相关报告。", []

    if _emit:
        _emit("query_plan", plan)
        _emit("recall", {
            "vector_count": len(candidates),
            "metadata_count": 0,
            "keyword_count": len(candidates),
            "total_before": len(candidates),
            "total_after": len(candidates),
            "dedup": 0,
            "stages": recall_details.get("stages", []),
        })

    documents = [e["text"] for e in candidates]

    reranked_entities = []
    try:
        rerank_results = rerank_with_retry(query, documents, top_n=rerank_top_k)
        for rr in rerank_results:
            idx = rr.get("index", 0)
            if idx < len(candidates):
                rerank_score = rr.get("relevance_score", 0)
                entity = candidates[idx]
                entity["_rerank_score"] = rerank_score
                reranked_entities.append(entity)
    except Exception:
        logger.warning("重排序失败，使用原始候选列表", exc_info=True)
        reranked_entities = candidates[:rerank_top_k]
    if _emit:
        _emit("rerank", {
            "results": [
                {
                    "index": i,
                    "score": e.get("_rerank_score", 0),
                    "source": e.get("source", ""),
                    "diagnosis": e.get("诊断结论", ""),
                    "text": e.get("text", ""),
                }
                for i, e in enumerate(reranked_entities)
            ]
        })

    parts = []
    for i, entity in enumerate(reranked_entities, 1):
        score = entity.get("_rerank_score", 0)
        parts.append(f"### 参考{i}（Rerank分数: {score:.4f}）\n{entity['text']}\n")

    return "\n".join(parts), reranked_entities


# =============================================================================
# 工具注册
# =============================================================================
def _build_tool_registry(
    entity_tracker, client, last_report, _emit, selected_diagnosis=None,
    last_ambiguity=None, selected_kbs=None, knowledge_bases=None,
):
    """构建并注册工具到 ToolRegistry。

    将 chat_stream 包装为符合 Tool Handler 签名的函数传入各工具。

    Args:
        entity_tracker: EntityTracker 实例
        client: MilvusClient 实例
        last_report: 上一轮报告的可变引用
        _emit: SSE 事件发射器

    Returns:
        ToolRegistry: 已注册所有工具的注册中心
    """
    from tools.rag_tool import RAG_SEARCH_SCHEMA, create_rag_search_handler
    from tools.edit_tool import EDIT_REPORT_SCHEMA, create_edit_report_handler
    from tools.refine_tool import REFINE_REPORT_SCHEMA, create_refine_report_handler

    registry = ToolRegistry()

    async def _chat_fn(messages, max_tokens=2048, temperature=0.3, _emit=None, debug=False, caller="tool"):
        return await chat_stream(messages, max_tokens=max_tokens, temperature=temperature,
                           _emit=_emit, debug=debug, caller=caller)

    def _search_reports_fn(query, _emit=None):
        return search_reports(
            query,
            client=client,
            _emit=_emit,
            context=entity_tracker.slots if entity_tracker else None,
            selected_kbs=selected_kbs,
            knowledge_bases=knowledge_bases,
        )

    rag_handler = create_rag_search_handler(
        chat_fn=_chat_fn,
        entity_tracker=entity_tracker,
        get_embedding_fn=get_embedding,
        search_reports_fn=_search_reports_fn,
        _emit_fn=_emit,
        last_report=last_report,
        selected_diagnosis=selected_diagnosis,
        last_ambiguity=last_ambiguity,
    )
    registry.register("rag_search", RAG_SEARCH_SCHEMA, rag_handler)

    edit_handler = create_edit_report_handler(
        chat_fn=_chat_fn,
        _emit_fn=_emit,
        last_report=last_report,
    )
    registry.register("edit_report", EDIT_REPORT_SCHEMA, edit_handler)

    refine_handler = create_refine_report_handler(
        chat_fn=_chat_fn,
        entity_tracker=entity_tracker,
        _emit_fn=_emit,
        last_report=last_report,
    )
    registry.register("refine_report", REFINE_REPORT_SCHEMA, refine_handler)

    return registry


# =============================================================================
# 主流程：run_pipeline
# =============================================================================
@traceable(run_type="chain", name="Agent_Main_Pipeline")
async def run_pipeline(query, session_id, stm, entity_tracker, client, last_report,
                       _emit, selected_diagnosis=None, last_ambiguity=None,
                       selected_kbs=None, knowledge_bases=None):
    """Tool Calling 架构主流程

    记忆模块集成点：
    1. Phase 1 (Pre-LLM): 实体提取 → 意图检测 → 上下文消解
    2. 切换意图: 清空 STM 和 last_report (彻底清洗)
    3. Phase 2 (Tool Calling): LLM 自主决定调用工具或直接回复
    4. Phase 3 (Post-LLM): 更新 STM 和当前会话实体
    """
    logger.info(f"── run_pipeline 开始: session={session_id}, query={query[:50]}..., selected_diagnosis={selected_diagnosis}")

    # Session restore is a compatibility boundary: normalize supported legacy
    # reports once, then keep only the canonical representation in the runtime.
    last_report_valid = False
    if last_report and last_report[0]:
        try:
            last_report[0] = serialize_report(parse_report_output(last_report[0]))
            last_report_valid = True
        except ReportSchemaError as exc:
            logger.warning("last_report normalization failed: %s", exc)
    logger.info(
        "report_context: last_report_present=%s last_report_valid=%s schema_version=%s",
        bool(last_report and last_report[0]),
        last_report_valid,
        REPORT_SCHEMA_VERSION,
    )

    # ── 缓存命中检查：新输入是否匹配缓存中的诊断 ──
    if not selected_diagnosis and last_ambiguity and last_ambiguity[0] is not None:
        _, cached_reranked = last_ambiguity[0]
        for entity in cached_reranked:
            diagnosis_name = entity.get("diagnosis_name", "") or entity.get("诊断结论", "")
            # 检查用户输入是否与缓存中的诊断匹配（完全匹配或包含关系）
            if diagnosis_name and (query == diagnosis_name or diagnosis_name in query or query in diagnosis_name):
                logger.info(f"输入命中缓存诊断: query='{query}' ≈ diagnosis='{diagnosis_name}'，自动跳过检索")
                selected_diagnosis = diagnosis_name
                break

    # ── Phase 1: 输入处理 ──
    # 如果用户点击了歧义选项，或输入命中缓存诊断，跳过实体提取/意图检测/上下文消解，
    # 保留上一轮的 modality/body_part 槽位，确保缓存命中
    if selected_diagnosis:
        logger.info("run_pipeline: 用户通过歧义选项/缓存命中选择诊断，跳过 Phase 1，保留槽位")
        # 明确告知 LLM：用户已选择诊断，请调用 rag_search 生成报告
        enhanced = f"用户选择了诊断：{selected_diagnosis}。请使用 rag_search 工具生成该诊断的结构化报告。"
        _emit("intent", {"intent": "TOOL_CALL"})
        _emit("cache_hit", {"query": query, "matched_diagnosis": selected_diagnosis})
    else:
        # 1. 实体提取：从用户输入提取实体更新槽位
        changes = entity_tracker.update_from_query(query)
        if changes:
            logger.info(f"实体更新: {changes}")
            _emit("entity_update", {"changes": changes, "slots": entity_tracker.slots})

        # 2. 意图检测：new_session / append / switch
        detected_intent = entity_tracker.detect_intent(query)
        logger.info(f"实体意图: {detected_intent}, slots: {entity_tracker.slots}")

        # 切换意图：必须彻底清空，严禁旧病灶残留
        if detected_intent == "switch":
            logger.info("检测到切换意图，清空会话上下文")
            stm.clear(session_id)
            if last_report:
                last_report[0] = ""
                last_report_valid = False
            entity_tracker.apply_switch(query)
            _emit("intent_switch", {"message": "已清空旧上下文，开始新检查"})

        # 3. 上下文消解：补全省略信息
        enhanced = entity_tracker.resolve_context(query)
        enhanced = standardize_query(enhanced)
        if enhanced != query:
            logger.info(f"上下文消解: '{query}' → '{enhanced}'")
            _emit("context_resolve", {"original": query, "resolved": enhanced})

    # 如果用户通过歧义选项选择诊断，跳过查询改写
    if not selected_diagnosis and needs_rewrite(enhanced):
        original = enhanced
        rewritten = rewrite_query(enhanced)
        if rewritten and rewritten != enhanced:
            enhanced = rewritten
            logger.info(f"查询改写: '{original}' → '{rewritten}'")
            _emit("query_rewrite", {"original": original, "rewritten": rewritten})

    # ── 模糊输入拦截：如果只有模态，没有部位/诊断，且有 last_report，追问用户意图 ──
    if not selected_diagnosis:
        has_report = last_report and last_report[0]
        
        if has_report:
            # 检查本次查询是否只包含 modality 词（如只输入"CT"）
            query_has_new_modality = entity_tracker._extract_modality_rule(query) is not None
            query_has_body_part = len(entity_tracker._extract_body_part_rule(query)) > 0
            # 检查槽位中的诊断（可能来自歧义选择）
            slots_has_diagnosis = len(entity_tracker.slots.get("diagnosis", [])) > 0
            
            # 只有当查询只包含 modality，且没有任何部位/诊断时，才拦截
            if query_has_new_modality and not query_has_body_part and not slots_has_diagnosis:
                # 输入过于模糊（如只有"CT"），追问用户意图
                logger.info(f"模糊输入拦截：modality={entity_tracker.slots['modality']}, 无部位/诊断，已有报告")
                clarification_msg = (
                    f"检测到您只输入了检查类型 '{entity_tracker.slots['modality']}'，但未指定检查部位或诊断。\n\n"
                    f"请选择您想执行的操作：\n"
                    f"1. **修改当前报告**：修改已有的报告内容\n"
                    f"2. **重新检索**：用 '{entity_tracker.slots['modality']}' 重新检索知识库生成新报告\n"
                    f"3. **补充部位**：如 'CT 头颅'、'CT 腹部' 等\n\n"
                    f"请明确告知您的意图，或直接输入完整的查询（如 'CT 脑出血'）。"
                )
                _emit("message", {"content": clarification_msg})
                stm.add_turn(session_id, query, clarification_msg)
                logger.info(f"── run_pipeline 完成：模糊输入追问")
                return clarification_msg

        # ── 缺少模态拦截：如果有部位/诊断但没有模态，追问检查类型 ──
        has_body_part = len(entity_tracker.slots.get("body_part", [])) > 0
        has_diagnosis = len(entity_tracker.slots.get("diagnosis", [])) > 0
        has_modality = entity_tracker.slots.get("modality") is not None
        
        if not has_modality and (has_body_part or has_diagnosis):
            # 构建提示信息
            parts = []
            if has_body_part:
                parts.append(f"检查部位: {', '.join(entity_tracker.slots['body_part'])}")
            if has_diagnosis:
                parts.append(f"诊断: {', '.join(entity_tracker.slots['diagnosis'])}")
            
            info_text = "、".join(parts)
            clarification_msg = (
                f"已识别到{info_text}，但未指定检查类型。\n\n"
                f"请补充检查类型（如 CT、MR、DR、超声等），例如：\n"
                f"- 'CT 脑出血'\n"
                f"- 'MR 脑部'\n\n"
                f"或回复'继续'使用默认检查类型。"
            )
            _emit("message", {"content": clarification_msg})
            stm.add_turn(session_id, query, clarification_msg)
            logger.info(f"── run_pipeline 完成：缺少模态追问")
            return clarification_msg

    history = stm.get_history(session_id)

    # ── Phase 2: Tool Calling 主循环 ──
    # 构建工具注册中心
    registry = _build_tool_registry(
        entity_tracker, client, last_report, _emit,
        selected_diagnosis=selected_diagnosis, last_ambiguity=last_ambiguity,
        selected_kbs=selected_kbs, knowledge_bases=knowledge_bases,
    )
    tools_schema = registry.get_tools_schema()
    logger.info(f"已注册工具: {list(registry._tools.keys())}")

    # ── 记忆检索注入：仅检索当前会话的 STM 历史 ──
    retriever = MemoryRetriever(get_embedding)
    retriever.index_stm(history)
    relevant = retriever.search_relevant(enhanced, top_k_stm=3)
    logger.info(f"记忆检索: STM={len(relevant['stm'])}条")
    if _emit:
        _emit("memory_retrieval", {
            "stm": relevant["stm"],
            "query": enhanced[:50],
        })

    # 构建系统消息：仅注入 EntityTracker 的当前会话上下文
    sys_prompt = PromptBuilder.build(
        "tool_orchestrator",
        entity_context=entity_tracker.to_context_prompt(),
    )

    # 注入检索后的相关对话历史（供 LLM 参考上下文）
    if relevant["stm"]:
        stm_context = "\n".join(f"- {msg}" for msg in relevant["stm"])
        sys_prompt += f"\n\n---\n\n## 相关历史对话\n{stm_context}"

    # 注入上一轮报告信息（供工具决策参考）
    if last_report_valid:
        sys_prompt += (
            f"\n\n---\n\n"
            f"## 当前已有报告\n"
            f"以下为上一轮生成的报告 JSON，如果用户要求修改或重写，请直接使用 edit_report 或 "
            f"refine_report 工具，无需重新检索。\n"
            f"```json\n{last_report[0][:2000]}\n```"
        )

    # 构建消息列表
    messages = [{"role": "system", "content": sys_prompt}]

    for msg in history[-6:]:
        content = msg.get("content", "").strip()
        if not content:
            continue
        role = msg.get("role", "")
        if role == "assistant" and len(content) > 500:
            content = content[:500] + "...（已省略后续内容）"
        messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": enhanced})

    # ── 计算上下文使用率 ──
    from .llm_client import _estimate_tokens
    total_chars, est_tokens = _estimate_tokens(messages)
    # 模型上下文窗口估算：max_tokens(512) 是输出限制，输入+输出总 token 约 4096
    context_window = 4096
    usage_percent = (est_tokens / context_window) * 100
    if _emit:
        _emit("context_usage", {"percent": usage_percent, "tokens": est_tokens, "chars": total_chars})

    # ── 第一次 LLM 调用（带 tools） ──
    if _emit:
        _emit("status", {"message": "正在分析请求..."})

    logger.info(f"第一次 LLM 调用 (带 tools): {len(messages)} 条消息, tools={[t['function']['name'] for t in tools_schema]}")
    content, tool_calls = await chat_with_tools(
        messages,
        tools=tools_schema,
        max_tokens=512,
        temperature=0.3,
        debug=True,
    )

    # ── 如果没有工具调用，直接回复 ──
    if not tool_calls:
        if content:
            try:
                parse_report_output(content)
            except ReportSchemaError:
                pass
            else:
                error_message = "报告必须通过受控报告工具生成，请重试。"
                logger.warning("main LLM attempted to return a report without a report tool")
                _emit("error", {"code": "REPORT_TOOL_REQUIRED", "message": error_message})
                stm.add_turn(session_id, query, error_message)
                return error_message
            if looks_like_serialized_tool_call(content):
                error_message = "模型返回了无效的工具调用格式，请重试。"
                logger.warning("direct response contained serialized tool call")
                _emit("error", {"code": "INVALID_TOOL_CALL", "message": error_message})
                stm.add_turn(session_id, query, error_message)
                return error_message
            if _emit:
                _emit("message", {"content": content})
            stm.add_turn(session_id, query, content)
            logger.info(f"── run_pipeline 完成: 直接回复 (无工具调用), content长度={len(content)}")
            return content
        else:
            _emit("error", {"message": "模型未返回有效回复"})
            logger.warning(f"── run_pipeline 完成: 模型未返回有效回复")
            return "抱歉，模型未返回有效回复。"

    # ── 执行工具调用 ──
    if _emit:
        _emit("intent", {"intent": "TOOL_CALL", "tools": [tc["name"] for tc in tool_calls]})

    logger.info(f"LLM 选择工具: {[tc['name'] for tc in tool_calls]}")
    for tc in tool_calls:
        logger.info(
            "selected_tool=%s argument_keys=%s last_report_present=%s",
            tc["name"],
            sorted((tc.get("arguments") or {}).keys()),
            bool(last_report and last_report[0]),
        )

    # 将 assistant 消息（含 tool_calls）追加到消息列表
    assistant_tool_calls = []
    for tc in tool_calls:
        assistant_tool_calls.append({
            "id": tc["id"],
            "type": "function",
            "function": {
                "name": tc["name"],
                "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
            },
        })

    messages.append({
        "role": "assistant",
        "content": content or None,
        "tool_calls": assistant_tool_calls,
    })

    # 执行每个工具。报告工具的 current_report 永远不来自模型参数；
    # edit/refine handler 通过闭包直接读取 Session Last Report。
    tool_results: list[tuple[str, str, ToolResult]] = []
    for tc in tool_calls:
        tool_name = tc["name"]
        tool_args = dict(tc.get("arguments") or {})
        if tool_name in ("edit_report", "refine_report"):
            tool_args.pop("current_report", None)

        result = await registry.execute(tc["id"], tool_name, tool_args)
        tool_results.append((tc["id"], tool_name, result))
        logger.info(
            "tool_complete: selected_tool=%s tool_is_final=%s result_length=%d last_report_present=%s",
            tool_name,
            result.is_final,
            len(result.content),
            bool(last_report and last_report[0]),
        )
        if _emit:
            _emit("tool_executed", {
                "tool": tool_name,
                "params": tool_args,
                "param_keys": sorted(tool_args.keys()),
                "result_length": len(result.content),
                "is_final": result.is_final,
            })

    for tool_id, _tool_name, result in tool_results:
        messages.append({
            "role": "tool",
            "tool_call_id": tool_id,
            "content": result.content,
        })

    report_results = [item for item in tool_results if item[1] in REPORT_TOOL_NAMES]
    if report_results:
        if len(report_results) != 1:
            error_message = "一次请求只能执行一个报告操作，请重试。"
            logger.warning("multiple report tools selected: %s", [item[1] for item in report_results])
            _emit("error", {"code": "MULTIPLE_REPORT_TOOLS", "message": error_message})
            stm.add_turn(session_id, query, error_message)
            return error_message

        _tool_id, report_tool_name, report_result = report_results[0]
        try:
            raw_result = json.loads(report_result.content)
        except json.JSONDecodeError:
            raw_result = None

        # Diagnosis ambiguity is a deliberate terminal RAG outcome, not a report.
        if (
            report_tool_name == "rag_search"
            and report_result.is_final
            and isinstance(raw_result, dict)
            and raw_result.get("status") == "ambiguous"
        ):
            _emit("ambiguous", {
                "question": raw_result.get("question", ""),
                "options": raw_result.get("options", []),
                "scores": raw_result.get("scores", []),
            })
            display = f"🔍 {raw_result.get('question', '')}\n\n" + "\n".join(
                f"{index + 1}. {option}"
                for index, option in enumerate(raw_result.get("options", []))
            )
            stm.add_turn(session_id, query, display)
            logger.info("report_tool_terminal: selected_tool=rag_search status=ambiguous")
            return display

        if not report_result.is_final or (
            isinstance(raw_result, dict) and "error" in raw_result
        ):
            error_message = "报告处理失败，请重试。"
            logger.warning(
                "report_tool_terminal: selected_tool=%s status=error tool_is_final=%s",
                report_tool_name,
                report_result.is_final,
            )
            _emit("error", {"code": "REPORT_TOOL_FAILED", "message": error_message})
            stm.add_turn(session_id, query, error_message)
            return error_message

        try:
            structured_report = parse_report_output(report_result.content)
        except ReportSchemaError as exc:
            error_message = "报告结构校验失败，请重试。"
            logger.warning(
                "report_tool_terminal: selected_tool=%s parse_status=invalid error=%s",
                report_tool_name,
                exc,
            )
            _emit("error", {"code": "REPORT_SCHEMA_INVALID", "message": error_message})
            stm.add_turn(session_id, query, error_message)
            return error_message

        canonical_report = serialize_report(structured_report)
        last_report[0] = canonical_report
        display = json_to_display(structured_report)
        _emit("status", {"message": "报告已生成", "phase": "done"})
        _emit("report", {"content": display})
        stm.add_turn(session_id, query, display)
        logger.info(
            "report_tool_terminal: selected_tool=%s status=success tool_is_final=true "
            "parse_status=valid schema_version=%s",
            report_tool_name,
            REPORT_SCHEMA_VERSION,
        )
        return display

    # Only non-report utility tools may use an optional final LLM response.
    if _emit:
        _emit("status", {"message": "正在生成回复..."})
    logger.info("non-report tool result: executing optional final LLM")
    final_content, _ = await chat_with_tools(
        messages,
        tools=None,
        max_tokens=1024,
        temperature=0.3,
        debug=True,
    )
    if not final_content:
        final_content = "操作完成，请查看结果。"
    if looks_like_serialized_tool_call(final_content):
        error_message = "模型返回了无效的工具调用格式，请重试。"
        logger.warning("optional final LLM emitted serialized tool call")
        _emit("error", {"code": "INVALID_FINAL_TOOL_CALL", "message": error_message})
        stm.add_turn(session_id, query, error_message)
        return error_message

    _emit("message", {"content": final_content})
    stm.add_turn(session_id, query, final_content)
    logger.info("── run_pipeline 完成: optional final LLM, content长度=%d", len(final_content))
    return final_content
