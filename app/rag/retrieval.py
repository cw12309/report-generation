"""Milvus scalar-filtered Dense + Sparse retrieval with dynamic fallback."""

import json
from typing import Dict, List, Optional, Tuple

from pymilvus import AnnSearchRequest, RRFRanker

from config import get_collection_name

from .metadata_navigator import LEVEL_FIELDS
from .sparse_vector import text_to_sparse_vector

COLLECTION_NAME = get_collection_name()
OUTPUT_FIELDS = [
    "id",
    "kb_id",
    "modality",
    "level_1",
    "level_2",
    "level_3",
    "level_4",
    "module_name",
    "imaging_findings",
    "imaging_opinion",
    "combined_text",
]


def _escape(value: str) -> str:
    return value.replace("\\", "").replace('"', "")


def ensure_collection_loaded(client, timeout: int = 120) -> Dict:
    """Load the collection when needed and verify its final state."""
    state_info = client.get_load_state(COLLECTION_NAME, timeout=timeout)
    state_text = str(state_info.get("state", "")).lower().replace("_", "")
    loaded = "loaded" in state_text and "notload" not in state_text
    if not loaded:
        client.load_collection(COLLECTION_NAME, timeout=timeout)
        state_info = client.get_load_state(COLLECTION_NAME, timeout=timeout)
        state_text = str(state_info.get("state", "")).lower().replace("_", "")
        loaded = "loaded" in state_text and "notload" not in state_text
    if not loaded:
        raise RuntimeError(f"Milvus集合 {COLLECTION_NAME} 加载失败: {state_info}")
    return state_info


def build_filter_expression(filters: Dict[str, str],
                            selected_kbs: Optional[List[str]] = None) -> str:
    """Build an AND expression from validated modality and level fields."""
    allowed = ("modality", *LEVEL_FIELDS)
    parts = [
        f'{field} == "{_escape(filters[field])}"'
        for field in allowed
        if filters.get(field)
    ]
    if selected_kbs:
        safe_ids = [str(kb_id)[:64] for kb_id in selected_kbs if str(kb_id).strip()]
        if safe_ids:
            parts.insert(0, f"kb_id in {json.dumps(safe_ids, ensure_ascii=False)}")
    return " and ".join(parts)


def build_fallback_filters(plan: Dict) -> List[Dict[str, str]]:
    """Return strict-to-broad filters, preserving modality throughout."""
    current = dict(plan.get("filters", {}))
    stages = [dict(current)]
    for field in reversed(LEVEL_FIELDS):
        if field in current:
            current.pop(field)
            if current not in stages:
                stages.append(dict(current))
    if not stages:
        stages.append({})
    return stages


def _display_entity(hit: Dict, stage: int, filter_expr: str) -> Dict:
    entity = dict(hit.get("entity", {}))
    entity["id"] = hit.get("id", entity.get("id"))
    entity["text"] = entity.get("combined_text", "")
    entity["source"] = str(entity.get("id", entity.get("module_name", "")))
    entity["检查类型"] = entity.get("modality", "")
    entity["部位"] = entity.get("level_1", "")
    entity["检查项目"] = entity.get("level_3", "")
    entity["诊断结论"] = entity.get("module_name", "")
    entity["一级目录"] = entity.get("level_1", "")
    entity["二级目录"] = entity.get("level_2", "")
    entity["三级目录"] = entity.get("level_3", "")
    entity["四级目录"] = entity.get("level_4", "")
    entity["模块名称"] = entity.get("module_name", "")
    entity["_distance"] = hit.get("distance", 0)
    entity["_recall_path"] = "hybrid"
    entity["_recall_paths"] = ["dense", "sparse"]
    entity["_filter_stage"] = stage
    entity["_filter_expr"] = filter_expr
    return entity


def hybrid_search(
    semantic_query: str,
    keyword_query: str,
    query_vector: List[float],
    filters: Dict[str, str],
    top_k: int,
    client,
    stage: int = 0,
    selected_kbs: Optional[List[str]] = None,
) -> List[Dict]:
    """Run Dense and Sparse ANN requests under one scalar filter."""
    filter_expr = build_filter_expression(filters, selected_kbs)
    dense_request = AnnSearchRequest(
        data=[query_vector],
        anns_field="dense_vector",
        param={"metric_type": "COSINE", "params": {"ef": 64}},
        limit=top_k,
        expr=filter_expr or None,
    )
    sparse_request = AnnSearchRequest(
        data=[text_to_sparse_vector(keyword_query or semantic_query)],
        anns_field="sparse_vector",
        param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
        limit=top_k,
        expr=filter_expr or None,
    )
    results = client.hybrid_search(
        collection_name=COLLECTION_NAME,
        reqs=[dense_request, sparse_request],
        ranker=RRFRanker(),
        limit=top_k,
        output_fields=OUTPUT_FIELDS,
        timeout=60,
    )
    return [
        _display_entity(hit, stage, filter_expr)
        for hit in (results[0] if results else [])
    ]


def retrieve_with_fallback(
    plan: Dict,
    query_vector: List[float],
    top_k: int,
    client,
    min_candidates: Optional[int] = None,
    selected_kbs: Optional[List[str]] = None,
) -> Tuple[List[Dict], Dict]:
    """Retrieve with strict filters and progressively remove deep levels."""
    ensure_collection_loaded(client)
    target = min_candidates or min(5, top_k)
    merged: Dict[str, Dict] = {}
    stage_details = []

    for stage, filters in enumerate(build_fallback_filters(plan)):
        filter_expr = build_filter_expression(filters, selected_kbs)
        results = hybrid_search(
            semantic_query=plan.get("semantic_query", plan.get("query", "")),
            keyword_query=plan.get("keyword_query", plan.get("query", "")),
            query_vector=query_vector,
            filters=filters,
            top_k=top_k,
            client=client,
            stage=stage,
            selected_kbs=selected_kbs,
        )
        stage_details.append({
            "stage": stage,
            "filters": filters,
            "filter_expr": filter_expr,
            "result_count": len(results),
        })
        for entity in results:
            key = entity["source"]
            if key not in merged:
                merged[key] = entity
        if len(merged) >= target:
            break

    candidates = list(merged.values())
    return candidates[:top_k], {
        "plan": plan,
        "stages": stage_details,
        "candidate_count": len(candidates),
    }


def multi_recall(
    query_vec,
    keywords,
    top_k,
    client,
    recall_paths=None,
    return_details=False,
    query="",
):
    """Backward-compatible wrapper for existing evaluation scripts."""
    modality = keywords.get("检查类型", "")
    if modality == "MRI":
        modality = "MR"
    filters = {}
    if modality:
        filters["modality"] = modality
    if keywords.get("部位"):
        filters["level_1"] = keywords["部位"]
    diagnosis = " ".join(keywords.get("诊断关键词", []))
    plan = {
        "query": query,
        "semantic_query": query,
        "keyword_query": diagnosis or query,
        "diagnosis_query": diagnosis,
        "filters": filters,
        "slots": {},
    }
    candidates, details = retrieve_with_fallback(
        plan=plan,
        query_vector=query_vec,
        top_k=top_k,
        client=client,
    )
    if return_details:
        return candidates, details
    return candidates
