# -*- coding: utf-8 -*-
r"""Milvus 重复数据诊断脚本 — 只读，不修改任何数据。

运行方式：
    cd d:\pythonProject\report-generation
    python app\test\diagnose_duplicates.py
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from config import get_collection_name
from data_pipeline.build_vector_db import (
    CATALOG_COLLECTION_NAME,
    list_catalog_entries,
    list_distinct_kb_ids,
)
from data_pipeline.build_vector_db import _client as _new_milvus_client
from memory.session_store import SessionStore

COLLECTION = get_collection_name()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SQLITE_PATH = PROJECT_ROOT / "data" / "sessions.db"


def _query_all(client, collection_name: str, fields: list[str], page_size=5000):
    rows = []
    offset = 0
    while True:
        batch = client.query(
            collection_name=collection_name,
            filter="kb_id != ''",
            output_fields=fields,
            limit=page_size,
            offset=offset,
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def main():
    print("=" * 70)
    print("Milvus knowledge_base_catalog 重复数据诊断")
    print("=" * 70)
    print(f"Collection: {COLLECTION}")
    print(f"SQLite:      {SQLITE_PATH}")
    print()

    client = _new_milvus_client()

    # ── 1. 总实体数 ＋ distinct kb_id ──────────────────────
    print("=" * 70)
    print("1. medical_reports 总览")
    print("=" * 70)

    all_rows = _query_all(client, COLLECTION, ["kb_id", "chunk_id"])
    total = len(all_rows)
    kb_id_counts = Counter(r["kb_id"] for r in all_rows)
    distinct_kb = len(kb_id_counts)

    print(f"  总实体数:              {total}")
    print(f"  distinct kb_id 数量:   {distinct_kb}")
    print()

    # ── 2. 每个 kb_id chunk 数 ──────────────────────────────
    print("2. 每个 kb_id 的 chunk 数")
    for kb_id, cnt in sorted(kb_id_counts.items(), key=lambda x: -x[1]):
        print(f"  {kb_id}  →  {cnt} chunks")
    print()

    # ── 3. Catalog 信息 ─────────────────────────────────────
    print("=" * 70)
    print("3. Catalog 中每个 KB 的 source_hash")
    print("=" * 70)
    catalog_entries = list_catalog_entries(client)
    catalog_by_id = {e["kb_id"]: e for e in catalog_entries}
    print(f"  Catalog 条目数: {len(catalog_entries)}")
    for e in catalog_entries:
        print(f"  {e['kb_id']}  |  {e['kb_name']}  |  {e['source_hash'][:16]}...  |  {e['source_name']}")
    print()

    # ── 4. source_hash → kb_id 映射 ─────────────────────────
    print("=" * 70)
    print("4. source_hash 重复分析")
    print("=" * 70)
    hash_to_kbs = defaultdict(list)
    for e in catalog_entries:
        h = e.get("source_hash", "")
        hash_to_kbs[h].append(e["kb_id"])

    duplicates_found = False
    for h, kbs in sorted(hash_to_kbs.items()):
        if len(kbs) > 1:
            duplicates_found = True
            print(f"  ⚠️  source_hash={h[:16]}... 对应 {len(kbs)} 个 kb_id:")
            for kb in kbs:
                print(f"      - {kb}")
    if not duplicates_found:
        print("  ✅ 没有相同 source_hash 对应多个 kb_id 的情况")
    else:
        print(f"  ⚠️ 存在重复 source_hash！")
    print()

    # ── 5. Chunks 中 kb_id 分布（跨 Catalog 对比） ──────────
    print("=" * 70)
    print("5. Chunks 中的 kb_id 与 Catalog 对比")
    print("=" * 70)
    catalog_ids = set(catalog_by_id.keys())
    chunk_ids_in_chunks = set(kb_id_counts.keys())

    in_both = catalog_ids & chunk_ids_in_chunks
    only_chunks = chunk_ids_in_chunks - catalog_ids
    only_catalog = catalog_ids - chunk_ids_in_chunks

    print(f"  Catalog 中有 + Chunks 中有: {len(in_both)}")
    print(f"  仅 Chunks 中有（无 Catalog）: {len(only_chunks)}")
    if only_chunks:
        print(f"    列表: {sorted(only_chunks)[:20]}...")
    print(f"  仅 Catalog 中有（无 Chunks）: {len(only_catalog)}")
    if only_catalog:
        print(f"    列表: {sorted(only_catalog)}")
    print()

    # ── 6. kb_id + chunk_id 重复检测 ────────────────────────
    print("=" * 70)
    print("6. 同一 kb_id + chunk_id 重复实体检测")
    print("=" * 70)
    key_counter = Counter()
    for r in all_rows:
        key = (r["kb_id"], r["chunk_id"])
        key_counter[key] += 1

    dup_keys = {k: v for k, v in key_counter.items() if v > 1}
    if dup_keys:
        print(f"  ❌ 发现 {len(dup_keys)} 组重复 (kb_id, chunk_id)！")
        for (kb_id, chunk_id), cnt in sorted(dup_keys.items(), key=lambda x: -x[1])[:20]:
            print(f"  kb_id={kb_id}  chunk_id={chunk_id}  →  {cnt} 条重复")
        if len(dup_keys) > 20:
            print(f"  ... 还有 {len(dup_keys) - 20} 组")
    else:
        print("  ✅ 没有同一 kb_id + chunk_id 重复实体")
    print()

    # ── 7. SQLite 对比 ──────────────────────────────────────
    print("=" * 70)
    print("7. SQLite  knowledge_bases")
    print("=" * 70)
    if SQLITE_PATH.exists():
        store = SessionStore(str(SQLITE_PATH))
        sqlite_kbs = store.list_knowledge_bases()
        print(f"  SQLite KB 数量: {len(sqlite_kbs)}")
        for kb in sqlite_kbs:
            h = (kb.get("source_hash") or "")[:16]
            print(f"  {kb['kb_id']}  |  {kb['kb_name']}  |  {h}...  |  status={kb['status']}")
    else:
        print(f"  ❌ sessions.db 不存在: {SQLITE_PATH}")
    print()

    # ── 8. 汇总 ─────────────────────────────────────────────
    print("=" * 70)
    print("诊断汇总")
    print("=" * 70)
    print(f"  medical_reports 总实体数:     {total}")
    print(f"  distinct kb_id:                {distinct_kb}")
    print(f"  Catalog 条目:                  {len(catalog_entries)}")
    print(f"  相同 source_hash 多 kb_id:     {'是' if duplicates_found else '否'}")
    print(f"  同 kb_id+chunk_id 重复:        {'是' if dup_keys else '否'}")
    print(f"  仅 Chunks 无 Catalog 的 kb_id:  {len(only_chunks)}")
    kbs_with_dups = set(k[0] for k in dup_keys)
    print(f"  有重复实体的 kb_id 数:         {len(kbs_with_dups)}")
    print()

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())