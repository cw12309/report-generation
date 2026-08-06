"""Command-line Hybrid Search + Rerank debugger.

Usage:
    python debug_retrieval.py "CT"
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from chat.llm_client import get_embedding, rerank_with_retry  # noqa: E402
from config import (  # noqa: E402
    get_db_path,
    get_milvus_token,
    get_rag_top_k,
)
from memory.session_store import SessionStore  # noqa: E402
from pymilvus import MilvusClient  # noqa: E402
from rag.retrieval import retrieve_with_fallback  # noqa: E402


def _truncate(text: str, limit: int = 50) -> str:
    compact = " ".join(str(text or "").split())
    return compact if len(compact) <= limit else f"{compact[:limit]}…"


def main() -> int:
    parser = argparse.ArgumentParser(description="调试医疗知识库 Hybrid Search + Rerank")
    parser.add_argument("query", help='检索语句，例如 "CT"')
    args = parser.parse_args()

    store = SessionStore(str(PROJECT_ROOT / "data" / "sessions.db"))
    selected_kbs = [
        str(kb["kb_id"])
        for kb in store.list_knowledge_bases(status="active")
    ]
    if not selected_kbs:
        raise RuntimeError("SQLite 中没有 active 知识库")

    kwargs = {"uri": get_db_path()}
    token = get_milvus_token()
    if token:
        kwargs["token"] = token
    client = MilvusClient(**kwargs)
    try:
        query_vector = get_embedding(args.query)
        plan = {
            "query": args.query,
            "semantic_query": args.query,
            "keyword_query": args.query,
            "filters": {},
        }
        candidates, details = retrieve_with_fallback(
            plan=plan,
            query_vector=query_vector,
            top_k=get_rag_top_k(),
            client=client,
            selected_kbs=selected_kbs,
        )
        if not candidates:
            print("未召回任何结果")
            return 1

        documents = [item.get("text", "") for item in candidates]
        rerank_results = rerank_with_retry(args.query, documents, top_n=3)

        print("=" * 72)
        print(f"Query: {args.query}")
        print(f"Hybrid 候选数: {details.get('candidate_count', len(candidates))}")
        print(f"知识库数: {len(selected_kbs)}")
        print("=" * 72)
        for rank, result in enumerate(rerank_results[:3], start=1):
            candidate_index = int(result.get("index", 0))
            if not 0 <= candidate_index < len(candidates):
                continue
            entity = candidates[candidate_index]
            score = float(result.get("relevance_score", 0))
            source = str(entity.get("source") or entity.get("chunk_id") or "")
            print(f"Top {rank}")
            print(f"  chunk_id/source : {source}")
            print(f"  rerank score    : {score:.6f}")
            print(f"  text[0:50]      : {_truncate(entity.get('text', ''), 50)}")
            print("-" * 72)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
