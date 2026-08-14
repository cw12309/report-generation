"""One-time Milvus schema migration helper for the multi-KB layout.

Use ``--check`` first.  ``--drop-and-create`` is intentionally guarded by an
exact confirmation string because it permanently removes the collection.
"""

import argparse
import re
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import get_collection_name, get_db_path, get_milvus_token  # noqa: E402
from data_pipeline.build_vector_db import create_collection  # noqa: E402
from milvus_compat import create_milvus_client  # noqa: E402
from pymilvus import MilvusClient  # noqa: E402


def client() -> MilvusClient:
    return create_milvus_client(get_db_path(), get_milvus_token())


def fields_and_indexes(milvus: MilvusClient, name: str):
    description = milvus.describe_collection(name)
    schema = description.get("schema", description)
    fields = schema.get("fields", []) if isinstance(schema, dict) else []
    indexes = milvus.list_indexes(name)
    return fields, indexes


def main() -> int:
    parser = argparse.ArgumentParser(description="检查或迁移多知识库 Milvus Schema")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="只检查字段和索引")
    group.add_argument(
        "--drop-and-create", action="store_true",
        help="删除旧 Collection 并创建带 kb_id 的新 Collection",
    )
    parser.add_argument(
        "--confirm", default="",
        help="删除操作必须填写：DROP <collection_name>",
    )
    args = parser.parse_args()

    name = get_collection_name()
    milvus = client()
    try:
        exists = milvus.has_collection(name)
        print(f"Collection: {name}")
        print(f"Exists: {exists}")
        if args.check:
            if not exists:
                print("Collection 不存在；下次建库时会自动创建新 Schema。")
                return 0
            fields, indexes = fields_and_indexes(milvus, name)
            print("Fields:")
            for field in fields:
                print(f"  - {field.get('name')}: {field.get('data_type', field.get('type'))}")
            print("Indexes:")
            for index in indexes:
                print(f"  - {index}")
            field_names = {field.get("name") for field in fields}
            has_kb = "kb_id" in field_names
            has_chunk = "chunk_id" in field_names
            print(f"kb_id field: {'OK' if has_kb else 'MISSING'}")
            print(f"chunk_id field: {'OK' if has_chunk else 'MISSING'}")
            if has_chunk:
                stats = milvus.get_collection_stats(name)
                row_count = int(stats.get("row_count", 0))
                print(f"Row count: {row_count}")
                samples = milvus.query(
                    collection_name=name,
                    filter='chunk_id != ""',
                    output_fields=["chunk_id", "kb_id"],
                    limit=3,
                )
                valid_md5 = bool(samples) and all(
                    re.fullmatch(r"[0-9a-f]{32}", str(item.get("chunk_id", "")))
                    for item in samples
                )
                print(f"chunk_id sample format: {'OK' if valid_md5 else 'INVALID'}")
                for item in samples:
                    print(f"  - {item.get('chunk_id')} ({item.get('kb_id')})")
            else:
                valid_md5 = False
            return 0 if has_kb and has_chunk and valid_md5 else 2

        expected = f"DROP {name}"
        if args.confirm != expected:
            print(f"拒绝删除。请使用 --confirm \"{expected}\" 明确确认。")
            return 2
        if exists:
            milvus.drop_collection(name)
            print(f"已删除旧 Collection: {name}")
        create_collection(milvus)
        print(
            f"已创建新 Collection: {name}"
            "（包含 kb_id、chunk_id 和 INVERTED 索引）"
        )
        return 0
    finally:
        milvus.close()


if __name__ == "__main__":
    raise SystemExit(main())
