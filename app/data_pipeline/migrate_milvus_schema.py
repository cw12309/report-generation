"""One-time Milvus schema migration helper for the multi-KB layout.

Use ``--check`` first.  ``--drop-and-create`` is intentionally guarded by an
exact confirmation string because it permanently removes the collection.
"""

import argparse
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import get_collection_name, get_db_path, get_milvus_token  # noqa: E402
from data_pipeline.build_vector_db import create_collection  # noqa: E402
from pymilvus import MilvusClient  # noqa: E402


def client() -> MilvusClient:
    kwargs = {"uri": get_db_path()}
    token = get_milvus_token()
    if token:
        kwargs["token"] = token
    return MilvusClient(**kwargs)


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
            has_kb = any(field.get("name") == "kb_id" for field in fields)
            print(f"kb_id field: {'OK' if has_kb else 'MISSING'}")
            return 0 if has_kb else 2

        expected = f"DROP {name}"
        if args.confirm != expected:
            print(f"拒绝删除。请使用 --confirm \"{expected}\" 明确确认。")
            return 2
        if exists:
            milvus.drop_collection(name)
            print(f"已删除旧 Collection: {name}")
        create_collection(milvus)
        print(f"已创建新 Collection: {name}（包含 kb_id 和 INVERTED 索引）")
        return 0
    finally:
        milvus.close()


if __name__ == "__main__":
    raise SystemExit(main())
