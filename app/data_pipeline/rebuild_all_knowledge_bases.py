"""One-time full rebuild of SQLite chunks, Markdown, and the Milvus Collection."""

import argparse
import hashlib
import re
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import get_collection_name, get_markdown_dir  # noqa: E402
from data_pipeline.build_vector_db import (  # noqa: E402
    _client,
    build_db,
    create_collection,
    upsert_catalog_entry,
)
from data_pipeline.extract_metadata import extract_all  # noqa: E402
from data_pipeline.process_reports import process_uploaded_excels  # noqa: E402
from memory.session_store import SessionStore  # noqa: E402

MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _compute_file_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def rebuild_local_artifacts(store: SessionStore) -> tuple[list[str], int]:
    """Regenerate Markdown and transactionally replace SQLite chunks per KB."""
    kb_ids: list[str] = []
    total_chunks = 0
    markdown_root = Path(get_markdown_dir())

    knowledge_bases = store.list_knowledge_bases()
    if not knowledge_bases:
        raise RuntimeError("SQLite 中没有知识库记录")

    for kb in knowledge_bases:
        kb_id = str(kb["kb_id"])
        source_path = Path(kb.get("source_path") or "")
        if not source_path.is_file():
            raise FileNotFoundError(f"知识库 {kb_id} 的原始 Excel 不存在: {source_path}")

        print(f"[本地重建] {kb_id} <- {source_path.name}", flush=True)
        new_hash = _compute_file_sha256(source_path)
        old_hash = str(kb.get("source_hash") or "")
        if old_hash and new_hash == old_hash:
            print(f"[本地重建] {kb_id} 内容未变化，跳过重建", flush=True)
            store.update_knowledge_base(kb_id, status="active", progress=100)
            kb_ids.append(kb_id)
            continue
        if old_hash:
            print(f"[本地重建] {kb_id} 内容已变化，更新 source_hash", flush=True)
        store.update_knowledge_base(kb_id, source_hash=new_hash)

        result = process_uploaded_excels([str(source_path)], markdown_root, kb_id)
        if result["errors"]:
            raise RuntimeError(
                f"知识库 {kb_id} 解析失败: {'; '.join(result['errors'][:5])}"
            )
        chunks = result["chunks"]
        invalid_ids = [
            chunk.get("chunk_id")
            for chunk in chunks
            if not MD5_PATTERN.fullmatch(str(chunk.get("chunk_id") or ""))
        ]
        if invalid_ids:
            raise ValueError(f"知识库 {kb_id} 生成了非法 chunk_id")
        if len({chunk["chunk_id"] for chunk in chunks}) != len(chunks):
            raise ValueError(f"知识库 {kb_id} 存在重复 chunk_id")

        stored = store.replace_knowledge_base_chunks(kb_id, chunks)
        metadata = extract_all(markdown_root / kb_id, kb_id=kb_id)
        store.update_knowledge_base(
            kb_id,
            metadata_tree=metadata,
            status="processing",
            progress=20,
        )
        kb_ids.append(kb_id)
        total_chunks += stored
        print(f"[本地重建] {kb_id}: {stored} 条", flush=True)

    return kb_ids, total_chunks


def recreate_collection() -> None:
    """Drop the explicitly confirmed Collection and recreate the new schema."""
    client = _client()
    collection_name = get_collection_name()
    try:
        if client.has_collection(collection_name):
            client.drop_collection(collection_name)
            print(f"[Milvus] 已删除旧 Collection: {collection_name}", flush=True)
        create_collection(client)
        print(f"[Milvus] 已创建包含 chunk_id 的 Collection: {collection_name}", flush=True)
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="完整重建知识库双流数据")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    collection_name = get_collection_name()
    expected = f"DROP {collection_name}"
    if args.confirm != expected:
        print(f'拒绝执行。请传入 --confirm "{expected}"', flush=True)
        return 2

    store = SessionStore(str(PROJECT_ROOT / "data" / "sessions.db"))
    kb_ids: list[str] = []
    try:
        kb_ids, local_count = rebuild_local_artifacts(store)
        print(f"[本地重建] 完成：{len(kb_ids)} 个知识库，{local_count} 条切片", flush=True)
        recreate_collection()
        milvus_count = build_db(get_markdown_dir(), batch_size=args.batch_size)
        if milvus_count != local_count:
            raise RuntimeError(
                f"数量不一致：SQLite={local_count}, Milvus写入={milvus_count}"
            )
        for kb_id in kb_ids:
            store.update_knowledge_base(kb_id, status="active", progress=100)
        print(f"[完成] Milvus 写入 {milvus_count} 条，所有知识库已恢复 active", flush=True)
        # 同步更新 Catalog
        catalog_client = _client()
        try:
            for kb_id in kb_ids:
                kb = store.get_knowledge_base(kb_id)
                if kb:
                    upsert_catalog_entry(
                        catalog_client,
                        kb_id,
                        str(kb.get("kb_name") or ""),
                        str(kb.get("source_name") or ""),
                        str(kb.get("source_hash") or ""),
                    )
            print(f"[Catalog] 已同步 {len(kb_ids)} 个知识库", flush=True)
        finally:
            catalog_client.close()
        return 0
    except Exception:
        for kb_id in kb_ids:
            store.update_knowledge_base(kb_id, status="failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())