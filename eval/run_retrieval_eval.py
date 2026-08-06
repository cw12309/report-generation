"""Run the real Hybrid Search + Rerank retrieval evaluation.

Examples:
    python eval/run_retrieval_eval.py
    python eval/run_retrieval_eval.py --dataset eval/datasets/retrieval_test_cases.json
    python -m eval.run_retrieval_eval --validate-only
"""

import argparse
import asyncio
import json
import os
import re
import sys
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent
APP_DIR = PROJECT_ROOT / "app"
for import_root in (PROJECT_ROOT, APP_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from chat.llm_client import get_embedding, rerank_with_retry  # noqa: E402
from config import (  # noqa: E402
    get_db_path,
    get_milvus_token,
    get_rag_top_k,
    get_rerank_top_k,
)
from eval.metrics import DEFAULT_HIT_KS, aggregate_hit_rates, first_relevant_rank  # noqa: E402
from memory.session_store import SessionStore  # noqa: E402
from pymilvus import MilvusClient  # noqa: E402
from rag.retrieval import retrieve_with_fallback  # noqa: E402

DEFAULT_DATASET = EVAL_DIR / "datasets" / "retrieval_test_cases.json"
MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$")
DATASET_LOCK = threading.RLock()
EVAL_STATUS_LOCK = threading.Lock()
eval_status = {"running": False, "cancel_requested": False}


def _set_evaluation_status(*, running: bool, cancel_requested: bool) -> None:
    with EVAL_STATUS_LOCK:
        eval_status["running"] = running
        eval_status["cancel_requested"] = cancel_requested


def is_evaluation_cancel_requested() -> bool:
    with EVAL_STATUS_LOCK:
        return bool(eval_status["cancel_requested"])


def request_evaluation_cancel() -> dict[str, bool]:
    """Request cooperative cancellation of the active streaming evaluation."""
    with EVAL_STATUS_LOCK:
        running = bool(eval_status["running"])
        if running:
            eval_status["cancel_requested"] = True
        return {
            "running": running,
            "cancel_requested": bool(eval_status["cancel_requested"]),
        }


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """Load and validate retrieval cases from one JSON array."""
    if not path.is_file():
        raise FileNotFoundError(f"测试集不存在: {path}")
    with DATASET_LOCK:
        with path.open("r", encoding="utf-8") as file:
            cases = json.load(file)
    if not isinstance(cases, list):
        raise ValueError("测试集必须是 JSON 数组")

    normalized_cases = []
    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"第 {index} 条用例必须是 JSON 对象")
        query = str(case.get("query") or "").strip()
        expected_ids = case.get("expected_chunk_ids")
        if not query:
            raise ValueError(f"第 {index} 条用例缺少 query")
        if not isinstance(expected_ids, list) or not expected_ids:
            raise ValueError(f"第 {index} 条用例缺少 expected_chunk_ids")
        expected_ids = [str(chunk_id).strip().lower() for chunk_id in expected_ids]
        invalid_ids = [
            chunk_id for chunk_id in expected_ids
            if not MD5_PATTERN.fullmatch(chunk_id)
        ]
        if invalid_ids:
            raise ValueError(f"第 {index} 条用例包含非法 MD5: {invalid_ids}")
        metadata = case.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError(f"第 {index} 条用例的 metadata 必须是 JSON 对象")
        normalized_cases.append({
            "query": query,
            "expected_chunk_ids": expected_ids,
            "metadata": metadata,
        })
    return normalized_cases


def write_dataset_atomically(
    cases: list[dict[str, Any]],
    path: Path = DEFAULT_DATASET,
) -> None:
    """Persist a complete dataset with a locked atomic file replacement."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    with DATASET_LOCK:
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(cases, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def normalize_expected_chunk_ids(value: list[str] | str) -> list[str]:
    """Normalize a list or comma-separated value into unique lowercase MD5 IDs."""
    raw_values = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for raw_value in raw_values:
        for chunk_id in str(raw_value).replace("，", ",").split(","):
            chunk_id = chunk_id.strip().lower()
            if chunk_id and chunk_id not in normalized:
                normalized.append(chunk_id)

    if not normalized:
        raise ValueError("expected_chunk_ids 至少需要一个 Chunk ID")
    invalid_ids = [
        chunk_id for chunk_id in normalized
        if not MD5_PATTERN.fullmatch(chunk_id)
    ]
    if invalid_ids:
        raise ValueError(f"Chunk ID 必须是 32 位 MD5: {invalid_ids}")
    return normalized


def add_retrieval_case(
    query: str,
    expected_chunk_ids: list[str] | str,
    dataset_path: Path = DEFAULT_DATASET,
) -> dict[str, Any]:
    """Append one manual case using a locked, atomic JSON file replacement."""
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("query 不能为空")
    if len(normalized_query) > 2000:
        raise ValueError("query 不能超过 2000 个字符")

    normalized_ids = normalize_expected_chunk_ids(expected_chunk_ids)
    new_case = {
        "query": normalized_query,
        "expected_chunk_ids": normalized_ids,
        "metadata": {"type": "manual"},
    }

    dataset_path = Path(dataset_path)
    with DATASET_LOCK:
        cases = load_dataset(dataset_path)
        cases.append(new_case)
        write_dataset_atomically(cases, dataset_path)

    return {"total": len(cases), "case": new_case}


def clear_retrieval_dataset(
    dataset_path: Path = DEFAULT_DATASET,
) -> dict[str, int]:
    """Remove every case while keeping a valid empty JSON array on disk."""
    dataset_path = Path(dataset_path)
    with DATASET_LOCK:
        previous_count = len(load_dataset(dataset_path)) if dataset_path.exists() else 0
        write_dataset_atomically([], dataset_path)
    return {"deleted": previous_count, "total": 0}


def delete_retrieval_case(
    chunk_id: str,
    dataset_path: Path = DEFAULT_DATASET,
) -> dict[str, int]:
    """Delete cases whose expected IDs contain the specified chunk ID."""
    normalized_chunk_id = str(chunk_id or "").strip().lower()
    if not MD5_PATTERN.fullmatch(normalized_chunk_id):
        raise ValueError("chunk_id 必须是 32 位 MD5")

    dataset_path = Path(dataset_path)
    with DATASET_LOCK:
        cases = load_dataset(dataset_path)
        remaining_cases = [
            case for case in cases
            if normalized_chunk_id not in case["expected_chunk_ids"]
        ]
        deleted_count = len(cases) - len(remaining_cases)
        if deleted_count == 0:
            raise KeyError(normalized_chunk_id)
        write_dataset_atomically(remaining_cases, dataset_path)
    return {"deleted": deleted_count, "total": len(remaining_cases)}


def new_milvus_client() -> MilvusClient:
    kwargs = {"uri": get_db_path()}
    token = get_milvus_token()
    if token:
        kwargs["token"] = token
    return MilvusClient(**kwargs)


def active_kb_ids(store: SessionStore, requested: list[str] | None) -> list[str]:
    active_ids = {
        str(kb["kb_id"])
        for kb in store.list_knowledge_bases(status="active")
    }
    if requested:
        missing = [kb_id for kb_id in requested if kb_id not in active_ids]
        if missing:
            raise ValueError(f"知识库不存在或不是 active: {missing}")
        return list(dict.fromkeys(requested))
    if not active_ids:
        raise RuntimeError("SQLite 中没有 active 知识库")
    return sorted(active_ids)


def retrieve_and_rerank(
    query: str,
    client: MilvusClient,
    selected_kbs: list[str],
    candidate_top_k: int,
    rerank_top_k: int,
) -> tuple[list[dict[str, Any]], int]:
    """Run production Dense/Sparse Hybrid Search followed by the real Reranker."""
    plan = {
        "query": query,
        "semantic_query": query,
        "keyword_query": query,
        "filters": {},
    }
    candidates, details = retrieve_with_fallback(
        plan=plan,
        query_vector=get_embedding(query),
        top_k=candidate_top_k,
        client=client,
        selected_kbs=selected_kbs,
    )
    if not candidates:
        return [], int(details.get("candidate_count", 0))

    documents = [candidate.get("text", "") for candidate in candidates]
    rerank_rows = rerank_with_retry(query, documents, top_n=rerank_top_k)
    ranked_results = []
    seen_indexes = set()
    for row in rerank_rows:
        candidate_index = int(row.get("index", -1))
        if candidate_index in seen_indexes or not 0 <= candidate_index < len(candidates):
            continue
        seen_indexes.add(candidate_index)
        candidate = candidates[candidate_index]
        ranked_results.append({
            "chunk_id": str(candidate.get("source") or candidate.get("chunk_id") or ""),
            "score": float(row.get("relevance_score", 0.0)),
            "text": str(candidate.get("text") or ""),
        })
    return ranked_results, int(details.get("candidate_count", len(candidates)))


def _short_text(text: str, limit: int = 50) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def build_case_detail(
    case: dict[str, Any],
    ranked_results: list[dict[str, Any]],
    candidate_count: int,
) -> dict[str, Any]:
    """Build one JSON-safe case result without producing console output."""
    retrieved_ids = [result["chunk_id"] for result in ranked_results]
    rank, matched_id = first_relevant_rank(
        retrieved_ids,
        case["expected_chunk_ids"],
    )
    score = ranked_results[rank - 1]["score"] if rank is not None else None

    top_results = [
        {
            "rank": result_rank,
            "chunk_id": result["chunk_id"],
            "score": result["score"],
            # Keep API responses bounded as datasets grow; metrics only need IDs.
            "text": _short_text(result["text"], 240),
        }
        for result_rank, result in enumerate(ranked_results[:5], start=1)
    ]
    return {
        "query": case["query"],
        "expected_chunk_ids": case["expected_chunk_ids"],
        "metadata": case["metadata"],
        "candidate_count": candidate_count,
        "hit_rank": rank,
        "matched_chunk_id": matched_id,
        "rerank_score": score,
        "hit1": rank is not None and rank <= 1,
        "hit3": rank is not None and rank <= 3,
        "hit5": rank is not None and rank <= 5,
        "top_results": top_results,
    }


def evaluate_cases(
    cases: list[dict[str, Any]],
    selected_kbs: list[str],
    candidate_top_k: int,
    rerank_top_k: int,
) -> dict[str, Any]:
    """Run all cases silently and return a structure suitable for JSON APIs."""
    details = []
    client = new_milvus_client()
    try:
        for case in cases:
            ranked_results, candidate_count = retrieve_and_rerank(
                query=case["query"],
                client=client,
                selected_kbs=selected_kbs,
                candidate_top_k=candidate_top_k,
                rerank_top_k=rerank_top_k,
            )
            details.append(build_case_detail(case, ranked_results, candidate_count))
    finally:
        client.close()

    return build_evaluation_result(
        details=details,
        selected_kbs=selected_kbs,
        candidate_top_k=candidate_top_k,
        rerank_top_k=rerank_top_k,
    )


def build_evaluation_result(
    details: list[dict[str, Any]],
    selected_kbs: list[str],
    candidate_top_k: int,
    rerank_top_k: int,
) -> dict[str, Any]:
    """Aggregate case details into the stable API/CLI result shape."""
    ranks = [detail["hit_rank"] for detail in details]
    rates = aggregate_hit_rates(ranks, DEFAULT_HIT_KS)
    return {
        "metrics": {
            "hit1": rates[1],
            "hit3": rates[3],
            "hit5": rates[5],
            "total": len(details),
            "hit1_count": sum(1 for detail in details if detail["hit1"]),
            "hit3_count": sum(1 for detail in details if detail["hit3"]),
            "hit5_count": sum(1 for detail in details if detail["hit5"]),
        },
        "details": details,
        "config": {
            "candidate_top_k": candidate_top_k,
            "rerank_top_k": rerank_top_k,
            "knowledge_base_count": len(selected_kbs),
        },
    }


async def stream_run_evaluation() -> AsyncIterator[dict[str, Any]]:
    """Run the default evaluation and yield per-query JSON-safe progress."""
    _set_evaluation_status(running=True, cancel_requested=False)
    client = None
    try:
        cases = await asyncio.to_thread(load_dataset, DEFAULT_DATASET)
        if not cases:
            raise ValueError("测试集为空，请先新增或自动生成测试用例")
        store = await asyncio.to_thread(
            SessionStore,
            str(PROJECT_ROOT / "data" / "sessions.db"),
        )
        selected_kbs = await asyncio.to_thread(active_kb_ids, store, None)
        candidate_top_k = get_rag_top_k()
        rerank_top_k = max(5, get_rerank_top_k())
        total = len(cases)
        details: list[dict[str, Any]] = []

        yield {
            "type": "start",
            "current": 0,
            "total": total,
            "progress": 0,
            "message": "正在准备 Embedding、Hybrid Search 和 Rerank",
        }

        if is_evaluation_cancel_requested():
            yield {
                "type": "cancelled",
                "current": 0,
                "total": total,
                "progress": 0,
                "message": "评测已取消",
            }
            return

        client = await asyncio.to_thread(new_milvus_client)
        for index, case in enumerate(cases, start=1):
            if is_evaluation_cancel_requested():
                yield {
                    "type": "cancelled",
                    "current": len(details),
                    "total": total,
                    "progress": int(len(details) / total * 100),
                    "message": "评测已取消",
                }
                return
            retrieval_task = asyncio.create_task(
                asyncio.to_thread(
                    retrieve_and_rerank,
                    case["query"],
                    client,
                    selected_kbs,
                    candidate_top_k,
                    rerank_top_k,
                )
            )
            try:
                ranked_results, candidate_count = await asyncio.shield(
                    retrieval_task
                )
            except asyncio.CancelledError:
                # A disconnected browser cannot kill a synchronous SDK call.
                # Let the current query finish before closing its Milvus client,
                # then stop before starting another query.
                request_evaluation_cancel()
                try:
                    await retrieval_task
                except Exception:
                    pass
                raise
            details.append(
                build_case_detail(case, ranked_results, candidate_count)
            )
            yield {
                "type": "progress",
                "current": index,
                "total": total,
                "progress": int(index / total * 100),
                "query": case["query"],
                "message": f"已完成 Query: {case['query']}",
            }

        if is_evaluation_cancel_requested():
            yield {
                "type": "cancelled",
                "current": len(details),
                "total": total,
                "progress": int(len(details) / total * 100),
                "message": "评测已取消",
            }
            return

        result = build_evaluation_result(
            details=details,
            selected_kbs=selected_kbs,
            candidate_top_k=candidate_top_k,
            rerank_top_k=rerank_top_k,
        )
        yield {
            "type": "done",
            "current": total,
            "total": total,
            "progress": 100,
            **result,
        }
    finally:
        if client is not None:
            await asyncio.to_thread(client.close)
        _set_evaluation_status(running=False, cancel_requested=False)


def run_evaluation() -> dict:
    """Run the default dataset silently for FastAPI and other integrations."""
    cases = load_dataset(DEFAULT_DATASET)
    if not cases:
        raise ValueError("测试集为空，请先新增或自动生成测试用例")
    store = SessionStore(str(PROJECT_ROOT / "data" / "sessions.db"))
    selected_kbs = active_kb_ids(store, requested=None)
    return evaluate_cases(
        cases=cases,
        selected_kbs=selected_kbs,
        candidate_top_k=get_rag_top_k(),
        rerank_top_k=max(5, get_rerank_top_k()),
    )


def print_case_detail(index: int, total: int, detail: dict[str, Any]) -> None:
    """Render one structured detail row for the CLI."""
    rank = detail["hit_rank"]
    score = detail["rerank_score"]

    print("\n" + "=" * 80)
    print(f"[{index}/{total}] Query: {detail['query']}")
    print(f"Metadata: {json.dumps(detail['metadata'], ensure_ascii=False)}")
    print(f"Expected: {', '.join(detail['expected_chunk_ids'])}")
    print(f"Hybrid candidates: {detail['candidate_count']}")
    if rank is None:
        print("Hit: MISS（Top 5 未命中）")
    else:
        print(
            f"Hit: rank={rank}, chunk_id={detail['matched_chunk_id']}, "
            f"rerank_score={score:.6f}"
        )
    print("Rerank Top 5:")
    for result in detail["top_results"]:
        marker = (
            "[HIT]"
            if result["chunk_id"] in detail["expected_chunk_ids"]
            else "     "
        )
        print(
            f"  {marker} #{result['rank']} score={result['score']:.6f} "
            f"chunk_id={result['chunk_id']} text={_short_text(result['text'])}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="医疗知识库 Retrieval Eval")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--candidate-top-k", type=int, default=get_rag_top_k())
    parser.add_argument(
        "--rerank-top-k",
        type=int,
        default=max(5, get_rerank_top_k()),
        help="必须至少为 5，才能计算 Hit@5",
    )
    parser.add_argument(
        "--kb-id",
        action="append",
        dest="kb_ids",
        help="限定 active 知识库；可重复传入，默认使用全部 active KB",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验数据集格式，不连接外部服务",
    )
    args = parser.parse_args()
    if args.candidate_top_k < 5:
        parser.error("--candidate-top-k 必须至少为 5")
    if args.rerank_top_k < 5:
        parser.error("--rerank-top-k 必须至少为 5")

    dataset_path = args.dataset.resolve()
    cases = load_dataset(dataset_path)
    print(f"数据集: {dataset_path}")
    print(f"用例数: {len(cases)}")
    if args.validate_only:
        print("数据集校验通过")
        return 0
    if not cases:
        raise ValueError("测试集为空，请先新增或自动生成测试用例")

    store = SessionStore(str(PROJECT_ROOT / "data" / "sessions.db"))
    selected_kbs = active_kb_ids(store, args.kb_ids)
    print(f"Active KB: {len(selected_kbs)}")
    print(
        f"候选池: Top {args.candidate_top_k}; "
        f"Rerank: Top {args.rerank_top_k}"
    )

    result = evaluate_cases(
        cases=cases,
        selected_kbs=selected_kbs,
        candidate_top_k=args.candidate_top_k,
        rerank_top_k=args.rerank_top_k,
    )
    for index, detail in enumerate(result["details"], start=1):
        print_case_detail(index, len(result["details"]), detail)

    metrics = result["metrics"]
    print("\n" + "=" * 80)
    print("Retrieval Eval Summary")
    print("=" * 80)
    for k in DEFAULT_HIT_KS:
        rate = metrics[f"hit{k}"]
        hits = metrics[f"hit{k}_count"]
        print(f"Hit@{k}: {rate:.2%} ({hits}/{metrics['total']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
