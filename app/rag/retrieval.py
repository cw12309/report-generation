"""Milvus scalar-filtered, multi-channel Dense + Sparse retrieval."""

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
    "chunk_id",
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


def build_recall_channels(plan: Dict) -> List[Dict]:
    """Build strict, modality-only, and KB-only parallel recall channels."""
    strict_filters = {
        field: value
        for field, value in dict(plan.get("filters", {})).items()
        if field in ("modality", *LEVEL_FIELDS) and value
    }
    definitions = [
        ("strict", strict_filters),
        (
            "modality",
            {"modality": strict_filters["modality"]}
            if strict_filters.get("modality") else {},
        ),
        ("kb_only", {}),
    ]

    channels = []
    seen = set()
    for name, filters in definitions:
        signature = tuple(sorted(filters.items()))
        if signature in seen:
            continue
        seen.add(signature)
        channels.append({"name": name, "filters": filters})
    return channels


def _display_entity(hit: Dict, stage: int, filter_expr: str) -> Dict:
    entity = dict(hit.get("entity", {}))
    entity["id"] = hit.get("id", entity.get("id"))
    entity["text"] = entity.get("combined_text", "")
    entity["source"] = entity.get("chunk_id") or str(entity.get("id", ""))
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


def _ann_requests(
    semantic_query: str,
    keyword_query: str,
    query_vector: List[float],
    filter_expr: str,
    top_k: int,
) -> List[AnnSearchRequest]:
    """Create the Dense and Sparse requests for one scalar-filter channel."""
    return [
        AnnSearchRequest(
            data=[query_vector],
            anns_field="dense_vector",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=top_k,
            expr=filter_expr or None,
        ),
        AnnSearchRequest(
            data=[text_to_sparse_vector(keyword_query or semantic_query)],
            anns_field="sparse_vector",
            param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
            limit=top_k,
            expr=filter_expr or None,
        ),
    ]


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
    results = client.hybrid_search(
        collection_name=COLLECTION_NAME,
        reqs=_ann_requests(
            semantic_query, keyword_query, query_vector, filter_expr, top_k,
        ),
        ranker=RRFRanker(),
        limit=top_k,
        output_fields=OUTPUT_FIELDS,
        timeout=60,
    )
    return [
        _display_entity(hit, stage, filter_expr)
        for hit in (results[0] if results else [])
    ]


def multi_channel_hybrid_search(
    plan: Dict,
    query_vector: List[float],
    top_k: int,
    client,
    selected_kbs: Optional[List[str]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Fuse strict, modality-only, and KB-only Dense/Sparse channels via RRF."""
    semantic_query = plan.get("semantic_query", plan.get("query", ""))
    keyword_query = plan.get("keyword_query", plan.get("query", ""))
    channels = build_recall_channels(plan)
    requests = []
    channel_details = []

    for channel in channels:
        filter_expr = build_filter_expression(channel["filters"], selected_kbs)
        requests.extend(_ann_requests(
            semantic_query,
            keyword_query,
            query_vector,
            filter_expr,
            top_k,
        ))
        channel_details.append({
            "channel": channel["name"],
            "filters": channel["filters"],
            "filter_expr": filter_expr,
            "request_count": 2,
        })

    if not requests:
        return [], channel_details

    results = client.hybrid_search(
        collection_name=COLLECTION_NAME,
        reqs=requests,
        ranker=RRFRanker(),
        limit=top_k,
        output_fields=OUTPUT_FIELDS,
        timeout=60,
    )

    # Milvus Hybrid Search normally returns unique PKs. Keep an explicit guard
    # keyed by the stable chunk_id shared with SQLite and Markdown.
    merged: Dict[str, Dict] = {}
    filter_summary = " | ".join(
        detail["filter_expr"] or "<no scalar filter>"
        for detail in channel_details
    )
    for hit in (results[0] if results else []):
        entity = _display_entity(hit, -1, filter_summary)
        key = str(
            entity.get("chunk_id")
            or entity.get("id")
            or entity.get("source")
            or entity.get("combined_text", "")
        )
        if key not in merged:
            entity["_recall_channels"] = [
                detail["channel"] for detail in channel_details
            ]
            entity["_filter_exprs"] = [
                detail["filter_expr"] for detail in channel_details
            ]
            merged[key] = entity
        if len(merged) >= top_k:
            break
    return list(merged.values()), channel_details


def retrieve_with_fallback(
    plan: Dict,
    query_vector: List[float],
    top_k: int,
    client,
    min_candidates: Optional[int] = None,
    selected_kbs: Optional[List[str]] = None,
) -> Tuple[List[Dict], Dict]:
    """Retrieve through parallel strict, modality-only, and KB-only channels."""
    ensure_collection_loaded(client)
    # Kept in the signature for backward compatibility; parallel recall no
    # longer stops after an arbitrary minimum candidate count.
    del min_candidates
    candidates, channel_details = multi_channel_hybrid_search(
        plan=plan,
        query_vector=query_vector,
        top_k=top_k,
        client=client,
        selected_kbs=selected_kbs,
    )
    return candidates[:top_k], {
        "plan": plan,
        "stages": channel_details,
        "channels": channel_details,
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
