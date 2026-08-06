"""Smoke-test remote hybrid retrieval and print the Top-1 raw entity."""

import json
import re
import sqlite3
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from chat.llm_client import get_embedding  # noqa: E402
from config import get_collection_name, get_db_path, get_milvus_token  # noqa: E402
from memory.session_store import SessionStore  # noqa: E402
from pymilvus import MilvusClient  # noqa: E402
from rag.retrieval import retrieve_with_fallback  # noqa: E402


def main() -> int:
    sqlite_path = PROJECT_ROOT / "data" / "sessions.db"
    store = SessionStore(str(sqlite_path))
    selected_kbs = [kb["kb_id"] for kb in store.list_knowledge_bases(status="active")]
    if not selected_kbs:
        raise RuntimeError("没有 active 知识库可供验证")

    kwargs = {"uri": get_db_path()}
    token = get_milvus_token()
    if token:
        kwargs["token"] = token
    client = MilvusClient(**kwargs)
    try:
        query = "CT"
        plan = {
            "query": query,
            "semantic_query": query,
            "keyword_query": query,
            "filters": {"modality": "CT"},
        }
        candidates, details = retrieve_with_fallback(
            plan=plan,
            query_vector=get_embedding(query),
            top_k=1,
            client=client,
            selected_kbs=selected_kbs,
        )
        if not candidates:
            raise RuntimeError("Query=CT 未召回任何结果")
        top1 = candidates[0]
        print(f"Collection: {get_collection_name()}")
        print(f"candidate_count: {details['candidate_count']}")
        print("Top 1 raw_data:")
        print(json.dumps(top1, ensure_ascii=False, indent=2))
        source = str(top1.get("source") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", source):
            raise AssertionError(f"source 不是 32 位 MD5: {source!r}")
        if source != str(top1.get("chunk_id") or ""):
            raise AssertionError("source 未优先使用 chunk_id")
        with sqlite3.connect(sqlite_path) as sqlite_conn:
            exists_in_sqlite = sqlite_conn.execute(
                "SELECT 1 FROM knowledge_base_chunks WHERE chunk_id = ?",
                (source,),
            ).fetchone()
        if not exists_in_sqlite:
            raise AssertionError(f"Milvus source 在 SQLite 中不存在: {source}")
        print(f"验证通过: source={source}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
