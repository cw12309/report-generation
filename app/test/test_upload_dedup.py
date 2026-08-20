# -*- coding: utf-8 -*-
r"""source_hash 去重 + 上传防重复测试。

运行方式：
    cd d:\pythonProject\report-generation
    python app\test\test_upload_dedup.py
"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from config import get_collection_name
from data_pipeline.build_vector_db import (
    CATALOG_COLLECTION_NAME,
    create_catalog_collection,
    ensure_catalog_collection,
    find_catalog_by_hash,
    upsert_catalog_entry,
    delete_catalog_entry,
    list_catalog_entries,
    build_db,
)
from data_pipeline.build_vector_db import _client as _new_milvus_client
from memory.session_store import SessionStore

COLLECTION = get_collection_name()

PASS = 0
FAIL = 0


def _msg(ok: bool, text: str):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  \u2705 {text}")
    else:
        FAIL += 1
        print(f"  \u274c {text}")


def _make_milvus_client():
    return _new_milvus_client()


def _count_entities(client, kb_id: str) -> int:
    client.load_collection(COLLECTION)
    results = client.query(
        collection_name=COLLECTION,
        filter=f'kb_id == "{kb_id}"',
        output_fields=["kb_id"],
        limit=10000,
    )
    return len(results)


def _total_entities(client) -> int:
    results = client.query(
        collection_name=COLLECTION,
        filter="kb_id != ''",
        output_fields=["kb_id"],
        limit=10000,
    )
    return len(results)


def _make_test_xlsx(dir_path: Path, name: str, payload: bytes = b"unique content for dedup test") -> Path:
    path = dir_path / name
    path.write_bytes(payload)
    return path


def _mk_store() -> SessionStore:
    db_path = tempfile.mktemp(suffix=".db")
    return SessionStore(db_path)


def _setup_catalog(client):
    ensure_catalog_collection(client)


def main():
    global PASS, FAIL
    print("=" * 60)
    print("source_hash 去重 + 上传防重复 测试")
    print("=" * 60)

    client = _make_milvus_client()
    _setup_catalog(client)

    # ── 辅助：清理 ──────────────────────────────────────────
    cleanup_ids = []
    def _register(kb_id):
        cleanup_ids.append(kb_id)

    def _cleanup():
        for kb_id in cleanup_ids:
            try:
                client.load_collection(COLLECTION)
                client.delete(collection_name=COLLECTION, filter=f'kb_id == "{kb_id}"')
            except Exception:
                pass
            try:
                delete_catalog_entry(client, kb_id)
            except Exception:
                pass

    try:
        # ── 测试 1：空 Catalog 查不到 hash ──────────────────
        print("\n\U0001F4CB 测试 1：空 Catalog 查不到 hash")
        result = find_catalog_by_hash(client, "nonexistent_hash_12345")
        _msg(result is None, "不存在 hash 返回 None")

        # ── 测试 2：写入后能找到相同 hash ───────────────────
        print("\n\U0001F4CB 测试 2：写入 Catalog 后能找到相同 hash")
        test_kb_id = "dedup_test_001"
        test_hash = hashlib.sha256(b"test content A").hexdigest()
        _register(test_kb_id)
        upsert_catalog_entry(
            client, test_kb_id, "测试KB", "test.xlsx", test_hash,
        )

        found = find_catalog_by_hash(client, test_hash)
        _msg(found is not None, "find_catalog_by_hash 找到条目")
        _msg(found["kb_id"] == test_kb_id, f"kb_id 匹配: {found['kb_id']}")
        _msg(found["kb_name"] == "测试KB", f"kb_name 匹配: {found['kb_name']}")
        _msg(found["source_name"] == "test.xlsx", f"source_name 匹配")
        _msg(found["source_hash"] == test_hash, f"source_hash 匹配")

        # ── 测试 3：不同 hash 互不干扰 ──────────────────────
        print("\n\U0001F4CB 测试 3：不同 hash 互不干扰")
        other_hash = hashlib.sha256(b"different content").hexdigest()
        found = find_catalog_by_hash(client, other_hash)
        _msg(found is None, "不同 hash 找不到条目")

        # ── 测试 4：SQLite find_knowledge_base_by_hash ──────
        print("\n\U0001F4CB 测试 4：SQLite find_knowledge_base_by_hash")
        store = _mk_store()
        db_kb_id = "dedup_sqlite_001"
        db_hash = hashlib.sha256(b"sqlite content").hexdigest()
        store.create_knowledge_base(
            db_kb_id, "SQLite KB", status="active",
            source_name="sqlite.xlsx", source_hash=db_hash,
        )
        found = store.find_knowledge_base_by_hash(db_hash)
        _msg(found is not None, "SQLite 找到条目")
        _msg(found["kb_id"] == db_kb_id, "kb_id 匹配")
        _msg(found["source_hash"] == db_hash, "source_hash 匹配")

        not_found = store.find_knowledge_base_by_hash("no_such_hash")
        _msg(not_found is None, "不存在的 hash 返回 None")

        empty = store.find_knowledge_base_by_hash("")
        _msg(empty is None, "空字符串 hash 返回 None")

        # ── 测试 5：同一文件重复上传能被检测 ─────────────────
        print("\n\U0001F4CB 测试 5：同一文件重复上传被检测（模拟 kb_upload 逻辑）")
        dup_content = b"duplicate upload test content v1"
        dup_hash = hashlib.sha256(dup_content).hexdigest()
        first_kb = "dedup_first_001"
        _register(first_kb)

        upsert_catalog_entry(client, first_kb, "首次上传", "first.xlsx", dup_hash)

        catalog_found = find_catalog_by_hash(client, dup_hash)
        _msg(catalog_found is not None, "首次上传后 Catalog 有记录")
        _msg(catalog_found["kb_id"] == first_kb, "首次上传 kb_id 正确")

        catalog_found2 = find_catalog_by_hash(client, dup_hash)
        _msg(catalog_found2 is not None, "第二次相同 hash 查找到已有 KB")
        _msg(catalog_found2["kb_id"] == first_kb, "重复上传检测到的是同一个 kb_id")

        # ── 测试 6：create_copy 允许生成新 kb_id ────────────
        print("\n\U0001F4CB 测试 6：create_copy 允许生成新 kb_id")
        copy_kb = "dedup_copy_001"
        _register(copy_kb)
        upsert_catalog_entry(client, copy_kb, "副本", "copy.xlsx", dup_hash)
        entries = list_catalog_entries(client)
        dup_entries = [e for e in entries if e["source_hash"] == dup_hash]
        _msg(len(dup_entries) == 2, f"create_copy 后存在 2 个条目: {len(dup_entries)}")
        kb_ids_with_same_hash = {e["kb_id"] for e in dup_entries}
        _msg(kb_ids_with_same_hash == {first_kb, copy_kb}, "两个 kb_id 不同")

        # ── 测试 7：rebuild 不增加实体总量 ──────────────────
        print("\n\U0001F4CB 测试 7：rebuild 不增加实体总量（DELETE → INSERT）")
        rebuild_kb = "dedup_rebuild_t1"
        _register(rebuild_kb)

        # 首次构建
        tmp_dir = tempfile.mkdtemp()
        md_dir = Path(tmp_dir) / "markdown"
        md_dir.mkdir(parents=True, exist_ok=True)
        md_file = md_dir / "test.md"
        md_file.write_text(
            f"# 测试\n\nkb_id: {rebuild_kb}\n\n## 模块1\n\n### 影像所见\n\n正常。\n\n### 影像意见\n\n无异常。\n",
            encoding="utf-8",
        )

        build_db(
            input_dir=str(md_dir),
            kb_id=rebuild_kb,
        )
        count1 = _count_entities(client, rebuild_kb)
        _msg(count1 > 0, f"首次构建 entity count: {count1}")

        # 第二次构建（rebuild）
        build_db(
            input_dir=str(md_dir),
            kb_id=rebuild_kb,
        )
        count2 = _count_entities(client, rebuild_kb)
        _msg(count2 == count1, f"rebuild 后 entity count 不变: {count2} == {count1}")

        # ── 测试 8：连续 rebuild 3 次 entity count 保持稳定 ──
        print("\n\U0001F4CB 测试 8：连续 rebuild 3 次 entity count 保持稳定")
        for i in range(3):
            build_db(
                input_dir=str(md_dir),
                kb_id=rebuild_kb,
            )
            cnt = _count_entities(client, rebuild_kb)
            _msg(cnt == count1, f"第 {i + 1} 次 rebuild: {cnt} == {count1}")

        # ── 测试 9：同一 kb_id + chunk_id 不产生重复 ─────────
        print("\n\U0001F4CB 测试 9：同一 kb_id + chunk_id 不产生重复")
        from collections import Counter

        client.load_collection(COLLECTION)
        all_rows = client.query(
            collection_name=COLLECTION,
            filter=f'kb_id == "{rebuild_kb}"',
            output_fields=["kb_id", "chunk_id"],
            limit=10000,
        )
        key_counter = Counter((r["kb_id"], r["chunk_id"]) for r in all_rows)
        dup_keys = {k: v for k, v in key_counter.items() if v > 1}
        _msg(len(dup_keys) == 0, f"无重复 (kb_id, chunk_id): {len(dup_keys)} 组")

        # ── 测试 10：删除后三方都清理 ───────────────────────
        print("\n\U0001F4CB 测试 10：删除后三方都清理")
        del_kb = "dedup_delete_test"
        _register(del_kb)
        del_hash = hashlib.sha256(b"delete test").hexdigest()
        upsert_catalog_entry(client, del_kb, "待删除", "delete.xlsx", del_hash)
        store2 = _mk_store()
        store2.create_knowledge_base(
            del_kb, "待删除", status="active",
            source_name="delete.xlsx", source_hash=del_hash,
        )

        client.load_collection(COLLECTION)
        client.delete(collection_name=COLLECTION, filter=f'kb_id == "{del_kb}"')
        delete_catalog_entry(client, del_kb)
        store2.delete_knowledge_base(del_kb)

        cat_after = list_catalog_entries(client)
        cat_ids = {e["kb_id"] for e in cat_after}
        _msg(del_kb not in cat_ids, "Catalog 中已删除")
        sql_after = store2.get_knowledge_base(del_kb)
        _msg(sql_after is None, "SQLite 中已删除")
        chunk_after = _count_entities(client, del_kb)
        _msg(chunk_after == 0, f"Chunks 中已删除: {chunk_after}")

        # ── 测试 11：Catalog 不覆盖已有 SQLite 数据 ──────────
        print("\n\U0001F4CB 测试 11：Catalog 不覆盖已有 SQLite 业务元数据")
        existing_kb = "dedup_existing_sql"
        _register(existing_kb)
        store3 = _mk_store()
        store3.create_knowledge_base(
            existing_kb, "已有KB", status="active",
            source_name="existing.xlsx", source_hash="existing_hash_001",
        )
        from data_pipeline.build_vector_db import upsert_catalog_entry as _upsert_cat
        _upsert_cat(client, existing_kb, "Catalog中的名字", "cat.xlsx", "cat_hash_001")

        recovered = store3.upsert_knowledge_base_from_catalog(
            existing_kb, "Catalog中的名字", "cat.xlsx", "cat_hash_001",
        )
        _msg(recovered is not None, "已有 SQLite 记录不被覆盖")
        _msg(recovered["kb_name"] == "已有KB", f"kb_name 保持原值: {recovered['kb_name']}")
        _msg(recovered["source_hash"] == "existing_hash_001", f"source_hash 保持原值: {recovered['source_hash']}")
        _msg(recovered["status"] == "active", f"status 保持原值: {recovered['status']}")

        # ── 测试 12：一致性检查：SQLite+Catalog 有、Chunks 无 → degraded ──
        print("\n\U0001F4CB 测试 12：一致性检查：SQLite+Catalog 有、Chunks 无 → degraded")
        deg_kb = "dedup_deg_test"
        _register(deg_kb)
        store4 = _mk_store()
        store4.create_knowledge_base(
            deg_kb, "降级测试", status="active",
            source_name="degraded.xlsx", source_hash="deg_hash_001",
        )
        upsert_catalog_entry(client, deg_kb, "降级测试", "degraded.xlsx", "deg_hash_001")
        # 模拟一致性检查：有 SQLite + Catalog，但 chunks 为空
        chunks_exist = _count_entities(client, deg_kb) > 0
        if not chunks_exist:
            store4.update_knowledge_base(deg_kb, status="degraded")
        updated = store4.get_knowledge_base(deg_kb)
        _msg(updated["status"] == "degraded", f"Chunks 缺失 → status 自动变为 degraded: {updated['status']}")

        # ── 测试 13：一致性检查：Catalog+Chunks 有、SQLite 无 → recovered ──
        print("\n\U0001F4CB 测试 13：一致性检查：Catalog+Chunks 有、SQLite 无 → recovered")
        rec_kb = "dedup_rec_test"
        _register(rec_kb)
        store5 = _mk_store()
        # 先写入 Catalog + 构建一个 chunk
        upsert_catalog_entry(client, rec_kb, "恢复测试", "recover.xlsx", "rec_hash_001")
        tmp_dir2 = tempfile.mkdtemp()
        md_dir2 = Path(tmp_dir2) / "markdown"
        md_dir2.mkdir(parents=True, exist_ok=True)
        md_file2 = md_dir2 / "test.md"
        md_file2.write_text(
            f"# 恢复测试\n\nkb_id: {rec_kb}\n\n## 模块\n\n### 影像所见\n\n恢复测试。\n\n### 影像意见\n\n无异常。\n",
            encoding="utf-8",
        )
        build_db(input_dir=str(md_dir2), kb_id=rec_kb)
        _msg(_count_entities(client, rec_kb) > 0, "Chunks 已写入")
        # 此时 SQLite 应该没有这个 KB
        sql_before = store5.get_knowledge_base(rec_kb)
        _msg(sql_before is None, "SQLite 中无此 KB")
        # 模拟恢复
        recovered_kb = store5.upsert_knowledge_base_from_catalog(
            rec_kb, "恢复测试", "recover.xlsx", "rec_hash_001",
        )
        _msg(recovered_kb is not None, "从 Catalog 恢复到 SQLite 成功")
        _msg(recovered_kb["status"] == "recovered", f"status 为 recovered: {recovered_kb['status']}")

        # ── 测试 14：source_hash 普通索引存在但不是 UNIQUE ──
        print("\n\U0001F4CB 测试 14：source_hash 普通索引存在但不是 UNIQUE")
        import sqlite3 as _sqlite3
        idx_conn = _sqlite3.connect(store4._db_path)
        idx_conn.row_factory = _sqlite3.Row
        indexes = idx_conn.execute(
            "SELECT name FROM pragma_index_list('knowledge_bases') "
            "WHERE name = 'idx_knowledge_bases_source_hash'"
        ).fetchall()
        _msg(len(indexes) > 0, "idx_knowledge_bases_source_hash 索引存在")
        # 验证不是 UNIQUE
        idx_info = idx_conn.execute(
            "PRAGMA index_info('idx_knowledge_bases_source_hash')"
        ).fetchall()
        _msg(len(idx_info) > 0, f"索引包含 {len(idx_info)} 列")
        # 验证 create_copy 可以成功（相同 hash 不同 kb_id）
        copy_hash = hashlib.sha256(b"index test").hexdigest()
        kb_a = "dedup_idx_a"
        kb_b = "dedup_idx_b"
        _register(kb_a)
        _register(kb_b)
        store4.create_knowledge_base(kb_a, "索引测试A", status="active", source_hash=copy_hash)
        store4.create_knowledge_base(kb_b, "索引测试B", status="active", source_hash=copy_hash)
        a = store4.get_knowledge_base(kb_a)
        b = store4.get_knowledge_base(kb_b)
        _msg(a is not None and b is not None, "相同 source_hash 两个 kb_id 均成功创建")
        _msg(a["kb_id"] != b["kb_id"], "两个 kb_id 不同")
        idx_conn.close()

        # ── 测试 15：force_rebuild 语义：相同 hash 不跳过 ──
        print("\n\U0001F4CB 测试 15：force_rebuild 语义：相同 hash 不跳过")
        force_kb = "dedup_force_t1"
        _register(force_kb)
        tmp_dir3 = tempfile.mkdtemp()
        md_dir3 = Path(tmp_dir3) / "markdown"
        md_dir3.mkdir(parents=True, exist_ok=True)
        md_file3 = md_dir3 / "test.md"
        md_file3.write_text(
            f"# 强制重建\n\nkb_id: {force_kb}\n\n## 模块\n\n### 影像所见\n\n强制重建测试。\n\n### 影像意见\n\n无异常。\n",
            encoding="utf-8",
        )
        # 首次构建
        build_db(input_dir=str(md_dir3), kb_id=force_kb)
        count_before = _count_entities(client, force_kb)
        _msg(count_before > 0, f"首次构建 entity count: {count_before}")
        # 模拟 force_rebuild：同一数据再次 build_db（不跳过）
        build_db(input_dir=str(md_dir3), kb_id=force_kb)
        count_after = _count_entities(client, force_kb)
        _msg(count_after == count_before, f"force_rebuild 后 entity count 不变: {count_after} == {count_before}")
        _msg(count_after > 0, f"force_rebuild 后 chunks > 0: {count_after}")

        # ── 测试 16：连续 force rebuild 3 次 entity count 不增长 ──
        print("\n\U0001F4CB 测试 16：连续 force rebuild 3 次 entity count 不增长")
        for i in range(3):
            build_db(input_dir=str(md_dir3), kb_id=force_kb)
            cnt = _count_entities(client, force_kb)
            _msg(cnt == count_before, f"第 {i + 1} 次 force rebuild: {cnt} == {count_before}")

    finally:
        _cleanup()
        client.close()

    # ── 汇总 ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"结果: {PASS} 通过 / {FAIL} 失败 / {total} 总计")
    if FAIL == 0:
        print("\U0001F389 全部测试通过！")
    else:
        print(f"\u274c 有 {FAIL} 个测试失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())