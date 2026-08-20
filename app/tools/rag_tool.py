"""RAG 检索工具

Schema: rag_search
- 封装多路召回 + Rerank + 结构化报告生成
- 注入 EntityTracker 当前会话上下文
- 返回结构化报告 JSON
"""

import json
import logging
import re
from typing import Callable, Optional, List, Dict

from chat.report_schema import (
    REPORT_SCHEMA_VERSION,
    ReportSchemaError,
    parse_report_with_repair,
)
from langsmith import traceable

logger = logging.getLogger(__name__)


def detect_ambiguity(reranked_entities: List[Dict], threshold=0.03) -> Optional[Dict]:
    """检测细粒度歧义：top-N 分数接近且有多种不同诊断时，让用户选择

    规则：
    1. top-1 和 top-2 分数差 < threshold
    2. top-N 中存在至少 2 种不同诊断

    Args:
        reranked_entities: Rerank 后的候选列表，每个含 '诊断结论' 和 '_rerank_score'
        threshold: 分数差阈值，top1 - top2 < threshold 才判定为歧义

    Returns:
        None 表示无歧义，否则返回 {"base_disease": ..., "alternatives": [...], "scores": [...]}
    """
    if len(reranked_entities) < 2:
        return None

    score_diff = reranked_entities[0].get("_rerank_score", 0) - reranked_entities[1].get("_rerank_score", 0)
    if score_diff >= threshold:
        return None

    # 收集所有不同的诊断结论（top-10 内）
    seen = set()
    alternatives = []
    scores = []
    for entity in reranked_entities[:10]:
        d = entity.get("诊断结论", "").strip()
        if d and d not in seen:
            seen.add(d)
            alternatives.append(d)
            scores.append(round(entity.get("_rerank_score", 0), 4))

    if len(alternatives) <= 1:
        return None

    return {
        "base_disease": "相关诊断",
        "alternatives": alternatives,
        "scores": scores,
    }


def _filter_by_selected_diagnosis(reranked_entities: List[Dict], selected_diagnosis: str) -> List[Dict]:
    """从缓存中过滤出匹配用户选择的诊断，排除不相关结果

    匹配策略：精确匹配 > 包含匹配 > 不匹配
    如果精确匹配命中，只返回精确匹配的；否则返回包含匹配的。
    """
    exact_matches = []
    partial_matches = []

    for entity in reranked_entities:
        d = entity.get("诊断结论", "").strip()
        if d == selected_diagnosis:
            exact_matches.append(entity)
        elif selected_diagnosis in d or d in selected_diagnosis:
            partial_matches.append(entity)

    if exact_matches:
        logger.info("_filter_by_selected_diagnosis: '%s' → 精确匹配 %d 条", selected_diagnosis, len(exact_matches))
        return exact_matches
    elif partial_matches:
        logger.info("_filter_by_selected_diagnosis: '%s' → 部分匹配 %d 条", selected_diagnosis, len(partial_matches))
        return partial_matches
    else:
        logger.warning("_filter_by_selected_diagnosis: '%s' → 无匹配，返回全部", selected_diagnosis)
        return reranked_entities


def _rebuild_search_result(reranked_entities: List[Dict]) -> str:
    """用过滤后的实体重建 search_result 文本"""
    parts = []
    for i, entity in enumerate(reranked_entities, 1):
        score = entity.get("_rerank_score", 0)
        parts.append(f"### 参考{i}（Rerank分数: {score:.4f}）\n{entity['text']}\n")
    return "\n".join(parts)

RAG_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "rag_search",
        "description": (
            "检索医学影像知识库，根据用户查询生成结构化报告。"
            "适用于：用户要求生成一份新的影像报告、查询某个检查类型/部位的参考信息。"
            "当用户提供明确的检查类型（CT/MR/DR等）和部位时，应使用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "经过上下文消解和标准化的查询语句，包含检查类型、检查部位等关键信息。例如：'CT头颅 脑出血'",
                },
                "modality": {
                    "type": "string",
                    "enum": ["CT", "MR", "MRI", "DR", "X线", "超声", "PET-CT", "DSA", "CTA", "不限"],
                    "description": "检查类型/模态，用于辅助检索过滤",
                },
                "body_part": {
                    "type": "string",
                    "description": "检查部位，如：头颅、胸部、腹部、肝脏、膝关节等",
                },
            },
            "required": ["query"],
        },
    },
}


def create_rag_search_handler(
    chat_fn: Callable,
    entity_tracker,
    get_embedding_fn: Callable,
    search_reports_fn: Callable,
    _emit_fn: Optional[Callable] = None,
    last_report: Optional[list] = None,
    selected_diagnosis: Optional[str] = None,
    last_ambiguity: Optional[list] = None,
) -> Callable[[dict], str]:
    """创建 rag_search 工具的处理函数。

    Args:
        chat_fn: LLM 对话函数，签名 (messages, max_tokens, temperature, _emit, debug, caller) -> str
        entity_tracker: EntityTracker 实例，用于注入上下文和参数补全
        get_embedding_fn: 向量化函数
        search_reports_fn: RAG 检索函数
        _emit_fn: SSE 事件发射器
        last_report: 上一轮报告的可变引用列表

    Returns:
        handler: 签名为 (arguments: dict) -> str 的处理函数
    """

    @traceable(run_type="tool", name="Tool_RAG_Search")
    async def handler(arguments: dict) -> str:
        query = arguments.get("query", "")
        modality = arguments.get("modality", "")
        body_part = arguments.get("body_part", "")

        # ── 参数容错：从 EntityTracker 自动补全缺失参数 ──
        if not modality and entity_tracker:
            modality = entity_tracker.slots.get("modality", "")
            if modality:
                logger.info("rag_search: 从 EntityTracker 自动补全 modality=%s", modality)
        if not body_part and entity_tracker:
            bp = entity_tracker.slots.get("body_part", [])
            if isinstance(bp, list):
                body_part = " ".join(bp) if bp else ""
            elif bp:
                body_part = str(bp)
            if body_part:
                logger.info("rag_search: 从 EntityTracker 自动补全 body_part=%s", body_part)

        if not query:
            error_result = json.dumps(
                {"error": "缺少 query 参数", "_is_final": False},
                ensure_ascii=False,
            )
            return error_result

        logger.info("rag_search 开始执行: query=%s, modality=%s, body_part=%s", query, modality, body_part)

        try:
            # ── 缓存加速：用户点击歧义选项时，直接复用上次检索结果 ──
            if selected_diagnosis and last_ambiguity and last_ambiguity[0] is not None:
                logger.info("rag_search: 命中歧义缓存，跳过检索 (selected_diagnosis=%s)", selected_diagnosis)
                search_result, reranked_entities = last_ambiguity[0]
                # 不删除缓存，允许用户多次点击不同按钮
            else:
                if _emit_fn:
                    _emit_fn("status", {"message": f"正在检索: {query}", "phase": "searching"})
                    _emit_fn("search", {"query": query, "modality": modality, "body_part": body_part})

                search_result, reranked_entities = search_reports_fn(query, _emit=_emit_fn)

            if not search_result or search_result == "未检索到相关报告。":
                logger.warning("rag_search: 未检索到相关报告")
                return json.dumps(
                    {
                        "error": "未检索到相关报告",
                        "message": "知识库中未找到与您查询相关的报告，请尝试更换查询词或检查部位。",
                        "_is_final": False,
                    },
                    ensure_ascii=False,
                )

            # ── 歧义检测：同病不同修饰，追问用户 ──
            # 如果用户从歧义选项中选择了精确诊断，跳过歧义检测
            if selected_diagnosis:
                logger.info("rag_search: 用户已选择诊断 '%s', 跳过歧义检测", selected_diagnosis)
                ambiguity = None
                # 从缓存中过滤出匹配的诊断，排除不相关结果
                if reranked_entities:
                    reranked_entities = _filter_by_selected_diagnosis(reranked_entities, selected_diagnosis)
                    search_result = _rebuild_search_result(reranked_entities)
                    logger.info("rag_search: 过滤后剩 %d 条参考", len(reranked_entities))
            else:
                ambiguity = detect_ambiguity(reranked_entities)
            if ambiguity:
                logger.info("rag_search: 检测到歧义，共 %d 个选项: %s",
                            len(ambiguity["alternatives"]), ambiguity["alternatives"][:5])
                # ── 缓存检索结果，下次点击时直接复用 ──
                if last_ambiguity is not None:
                    last_ambiguity[0] = (search_result, reranked_entities)
                    logger.info("rag_search: 已缓存歧义检索结果，供下次点击复用")
                return json.dumps(
                    {
                        "status": "ambiguous",
                        "question": f"检测到多种相关诊断，您指的是哪一种？",
                        "options": ambiguity["alternatives"],
                        "scores": ambiguity["scores"],
                        "_is_final": True,
                    },
                    ensure_ascii=False,
                )

            if _emit_fn:
                _emit_fn("status", {"message": "检索完成，正在生成结构化报告...", "phase": "generating"})

            from prompt.system_prompt_builder import PromptBuilder

            sys_prompt = PromptBuilder.build(
                "report_prompt/structure",
                entity_context=entity_tracker.to_context_prompt() if entity_tracker else None,
                last_report=last_report[0] if last_report and last_report[0] else None,
            )

            user_content = f"检索结果：\n{search_result}"
            if selected_diagnosis:
                user_content += (
                    f"\n\n【重要】用户已明确选择诊断：{selected_diagnosis}。"
                    f"请忽略'选最高分'规则，从检索结果中找出诊断结论与'{selected_diagnosis}'名称一致的参考报告，"
                    f"以此为准生成报告。若无完全一致的，选最接近的。注意：必须输出 JSON，不要输出其他内容。"
                )
            user_content += "\n\n请按 JSON 格式输出结构化报告。"

            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ]

            output = await chat_fn(
                messages,
                max_tokens=2048,
                temperature=0.3,
                _emit=None,
                debug=True,
                caller="rag_search_tool",
            )

            async def repair(repair_messages):
                return await chat_fn(
                    repair_messages,
                    max_tokens=2048,
                    temperature=0,
                    _emit=None,
                    debug=True,
                    caller="rag_search_repair",
                )

            report_json, repair_attempted = await parse_report_with_repair(output, repair)
            logger.info(
                "rag_search validated: parse_status=valid repair_attempted=%s schema_version=%s",
                repair_attempted,
                REPORT_SCHEMA_VERSION,
            )
            report_json["_is_final"] = True

            result_str = json.dumps(report_json, ensure_ascii=False, indent=2)

            if _emit_fn:
                reasoning = report_json.get("reasoning", "") if isinstance(report_json, dict) else ""
                if reasoning:
                    _emit_fn("reasoning", {"text": reasoning})
                _emit_fn("status", {"message": "报告生成完成", "phase": "done"})

            logger.info("rag_search 完成: 报告长度 %d, is_final=True", len(result_str))
            return result_str

        except ReportSchemaError as exc:
            logger.warning("rag_search report validation failed: %s", exc)
            return json.dumps(
                {"error": "报告生成失败，请重试", "_is_final": False},
                ensure_ascii=False,
            )
        except Exception:
            logger.error("rag_search 执行失败", exc_info=True)
            return json.dumps(
                {"error": "报告生成失败，请重试", "_is_final": False},
                ensure_ascii=False,
            )

    return handler