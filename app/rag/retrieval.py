"""多路召回模块。

支持三种召回路径，合并去重后统一交给 Rerank 精排：

1. 向量检索（Bi-Encoder）：语义相似度匹配，适合模糊查询
2. 元数据过滤：按检查类型/部位/诊断结论精确匹配，适合关键词明确的查询
3. 关键词检索：基于 Milvus like 查询的全文匹配，适合专业术语检索

用法：
    from retrieval import multi_recall

    candidates = multi_recall(
        query="CT脑出血",
        query_vec=[0.1, -0.2, ...],
        top_k=5,
        client=milvus_client,
    )
"""
import os

from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker
from config import get_collection_name, get_milvus_token

try:
    from .query_rewrite import parse_query_keywords
    from .sparse_vector import text_to_sparse_vector
except ImportError:
    from query_rewrite import parse_query_keywords
    from sparse_vector import text_to_sparse_vector

COLLECTION_NAME = get_collection_name()

OUTPUT_FIELDS = [
    "id", "modality", "level_1", "level_2", "level_3", "level_4",
    "module_name", "imaging_findings", "imaging_opinion", "combined_text",
]


def _add_display_fields(entity):
    """Expose the ASCII Milvus fields under the existing business keys."""
    entity["text"] = entity.get("combined_text", "")
    entity["source"] = str(entity.get("id", entity.get("module_name", "")))
    entity["vector_type"] = "hybrid"
    entity["检查类型"] = entity.get("modality", "")
    entity["部位"] = entity.get("level_1", "")
    entity["检查项目"] = entity.get("level_3", "")
    entity["诊断结论"] = entity.get("module_name", "")
    entity["一级目录"] = entity.get("level_1", "")
    entity["二级目录"] = entity.get("level_2", "")
    entity["三级目录"] = entity.get("level_3", "")
    entity["四级目录"] = entity.get("level_4", "")
    entity["模块名称"] = entity.get("module_name", "")
    return entity


def _esc_filter(value):
    """转义 Milvus 过滤表达式中的特殊字符，防止注入。

    移除双引号和反斜杠，避免破坏过滤表达式语法。
    """
    return value.replace('"', '').replace('\\', '')


def _scalar_filter(keywords):
    conditions = []
    if keywords.get("检查类型"):
        check_type = "MR" if keywords["检查类型"] == "MRI" else keywords["检查类型"]
        conditions.append(f'modality == "{_esc_filter(check_type)}"')
    if keywords.get("部位"):
        conditions.append(f'level_1 == "{_esc_filter(keywords["部位"])}"')
    return " and ".join(conditions)


def vector_search(query, query_vec, keywords, top_k, client):
    """Dense + sparse hybrid search with shared scalar filters.

    Args:
        query_vec: 查询向量 (1024维)
        top_k: 返回数量
        client: MilvusClient 实例

    Returns:
        list[dict]: 候选文档列表，每项包含 text, source, 元数据, _distance, _recall_path
    """
    filter_expr = _scalar_filter(keywords)
    dense_request = AnnSearchRequest(
        data=[query_vec],
        anns_field="dense_vector",
        param={"metric_type": "COSINE", "params": {"ef": 64}},
        limit=top_k,
        expr=filter_expr or None,
    )
    sparse_request = AnnSearchRequest(
        data=[text_to_sparse_vector(query)],
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
        timeout=60,  # 60秒超时，防止 Milvus 卡住导致整个请求挂起
    )

    candidates = []
    for hit in results[0]:
        entity = hit["entity"]
        entity["id"] = hit.get("id", entity.get("id"))
        entity = _add_display_fields(entity)
        entity["_distance"] = hit.get("distance", 0)
        entity["_recall_path"] = "vector"
        candidates.append(entity)
    return candidates


def metadata_filter(keywords, top_k, client):
    """元数据过滤（按检查类型/部位/诊断结论精确匹配）。

    Args:
        keywords: parse_query_keywords 返回的关键词字典
        top_k: 返回数量
        client: MilvusClient 实例

    Returns:
        list[dict]: 候选文档列表
    """
    conditions = []

    if keywords.get("检查类型"):
        check_type = keywords["检查类型"]
        if check_type == "MRI":
            check_type = "MR"
        conditions.append(f'modality == "{_esc_filter(check_type)}"')

    if keywords.get("部位"):
        conditions.append(f'level_1 == "{_esc_filter(keywords["部位"])}"')

    if keywords.get("诊断关键词"):
        for kw in keywords["诊断关键词"]:
            conditions.append(f'module_name like "%{_esc_filter(kw)}%"')

    if not conditions:
        return []

    filter_expr = " and ".join(conditions)

    try:
        client.load_collection(COLLECTION_NAME, timeout=30)
        results = client.query(
            COLLECTION_NAME,
            filter=filter_expr,
            output_fields=OUTPUT_FIELDS,
            limit=top_k,
            timeout=60,  # 60秒超时
        )
    except Exception:
        return []

    candidates = []
    for r in results:
        _add_display_fields(r)
        r["_distance"] = -1
        r["_recall_path"] = "metadata"
        candidates.append(r)
    return candidates


def keyword_search(keywords, top_k, client):
    """关键词检索（基于 Milvus like 查询的全文匹配）。

    对诊断关键词在 text 字段中进行 like 匹配，
    适合用户输入包含专业术语的场景。

    Args:
        keywords: parse_query_keywords 返回的关键词字典
        top_k: 返回数量
        client: MilvusClient 实例

    Returns:
        list[dict]: 候选文档列表
    """
    search_terms = []

    if keywords.get("诊断关键词"):
        search_terms.extend(keywords["诊断关键词"])

    if not search_terms:
        return []

    all_conditions = []
    for term in search_terms:
        all_conditions.append(f'combined_text like "%{_esc_filter(term)}%"')

    if not all_conditions:
        return []

    filter_expr = " or ".join(all_conditions)

    type_cond = ""
    if keywords.get("检查类型"):
        ct = keywords["检查类型"]
        if ct == "MRI":
            ct = "MR"
        type_cond = f'modality == "{_esc_filter(ct)}"'

    part_cond = ""
    if keywords.get("部位"):
        part_cond = f'level_1 == "{_esc_filter(keywords["部位"])}"'

    extra_conds = [c for c in [type_cond, part_cond] if c]
    if extra_conds:
        filter_expr = f"({filter_expr}) and {' and '.join(extra_conds)}"

    try:
        client.load_collection(COLLECTION_NAME, timeout=30)
        results = client.query(
            COLLECTION_NAME,
            filter=filter_expr,
            output_fields=OUTPUT_FIELDS,
            limit=top_k,
            timeout=60,  # 60秒超时
        )
    except Exception:
        return []

    candidates = []
    for r in results:
        _add_display_fields(r)
        r["_distance"] = -1
        r["_recall_path"] = "keyword"
        candidates.append(r)
    return candidates


def multi_recall(
    query_vec, keywords, top_k, client, recall_paths=None,
    return_details=False, query="",
):
    """多路召回 + 去重。

    支持双通道向量化（full_row 和 opinion），同一 source 的多个向量会被合并，
    保留最高分数的向量文本，同时标记命中的向量类型。

    Args:
        query_vec: 查询向量 (1024维)
        keywords: parse_query_keywords 返回的关键词字典
        top_k: 每路召回的数量
        client: MilvusClient 实例
        recall_paths: 启用的召回路径列表，默认 ["vector", "metadata", "keyword"]
        return_details: 是否同时返回各路原始结果（供前端展示用）

    Returns:
        list[dict]: 去重后的候选文档列表，每项包含:
            - text, source, 元数据
            - _distance: 向量检索距离（-1 表示非向量检索）
            - _recall_path: 召回路径（"vector"/"metadata"/"keyword"/"multi"）
            - _recall_paths: 所有命中该文档的召回路径列表
            - _vector_types: 命中的向量类型列表（["full_row"], ["opinion"], 或两者都有）

        当 return_details=True 时，返回 (candidates, details) 元组，
        details 为 dict，包含各路原始结果:
            {"vector": [...], "metadata": [...], "keyword": [...]}
    """
    if recall_paths is None:
        recall_paths = ["vector", "metadata", "keyword"]

    candidates = {}
    details = {}

    if "vector" in recall_paths:
        hybrid_query = query or " ".join(
            filter(None, [
                keywords.get("检查类型", ""),
                keywords.get("部位", ""),
                " ".join(keywords.get("诊断关键词", [])),
            ])
        )
        vec_results = vector_search(hybrid_query, query_vec, keywords, top_k, client)
        details["vector"] = vec_results
        for entity in vec_results:
            src = entity["source"]
            vector_type = entity.get("vector_type", "full_row")
            
            if src in candidates:
                candidates[src]["_recall_paths"].append("vector")
                # 收集命中的向量类型
                if "_vector_types" not in candidates[src]:
                    candidates[src]["_vector_types"] = set()
                candidates[src]["_vector_types"].add(vector_type)
                
                # 如果新向量的分数更高，更新 text 和 distance
                if entity.get("_distance", 0) > candidates[src].get("_distance", -1):
                    candidates[src]["text"] = entity["text"]
                    candidates[src]["_distance"] = entity["_distance"]
                    candidates[src]["_primary_vector"] = vector_type
            else:
                entity["_recall_paths"] = ["vector"]
                entity["_vector_types"] = {vector_type}
                entity["_primary_vector"] = vector_type
                candidates[src] = entity

    if "metadata" in recall_paths:
        meta_results = metadata_filter(keywords, top_k, client)
        details["metadata"] = meta_results
        for entity in meta_results:
            src = entity["source"]
            if src in candidates:
                candidates[src]["_recall_paths"].append("metadata")
            else:
                entity["_recall_paths"] = ["metadata"]
                entity["_vector_types"] = set()
                candidates[src] = entity

    if "keyword" in recall_paths:
        kw_results = keyword_search(keywords, top_k, client)
        details["keyword"] = kw_results
        for entity in kw_results:
            src = entity["source"]
            if src in candidates:
                candidates[src]["_recall_paths"].append("keyword")
            else:
                entity["_recall_paths"] = ["keyword"]
                entity["_vector_types"] = set()
                candidates[src] = entity

    result = list(candidates.values())
    for entity in result:
        if len(entity["_recall_paths"]) > 1:
            entity["_recall_path"] = "multi"
        else:
            entity["_recall_path"] = entity["_recall_paths"][0]
        
        # 将 set 转换为 list 以便 JSON 序列化
        entity["_vector_types"] = list(entity.get("_vector_types", set()))

    if return_details:
        return result, details
    return result


if __name__ == "__main__":
    import requests as http_requests

    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_pipeline", "milvus_lite.db")
    EMBED_URL = os.environ.get("EMBED_URL", "http://14.22.83.225:11002/v1/embeddings")
    EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")

    test_queries = ["CT脑出血", "动脉瘤（宽颈多发）", "头颅", "CT"]

    token = get_milvus_token()
    client = MilvusClient(uri=DB_PATH, **({"token": token} if token else {}))
    client.load_collection(COLLECTION_NAME)

    for q in test_queries:
        print(f"\n{'='*60}")
        print(f"查询: {q}")
        print(f"{'='*60}")

        keywords = parse_query_keywords(q)
        print(f"  解析关键词: {keywords}")

        payload = {"model": EMBED_MODEL, "input": [q]}
        resp = http_requests.post(EMBED_URL, json=payload, timeout=30)
        query_vec = resp.json()["data"][0]["embedding"]

        candidates = multi_recall(query_vec, keywords, top_k=5, client=client)

        path_counts = {}
        for c in candidates:
            path = c["_recall_path"]
            path_counts[path] = path_counts.get(path, 0) + 1

        print(f"  总候选数: {len(candidates)} (去重后)")
        print(f"  召回路径分布: {path_counts}")

        for i, c in enumerate(candidates[:5], 1):
            print(f"\n  候选{i}: 来源={c['source']}")
            print(f"        路径={c['_recall_path']} ({', '.join(c['_recall_paths'])})")
            print(f"        类型={c.get('检查类型','')} | 部位={c.get('部位','')} | 诊断={c.get('诊断结论','')}")

    client.close()
