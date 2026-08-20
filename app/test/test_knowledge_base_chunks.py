"""SQLite knowledge-base chunk persistence tests."""

import hashlib
import sqlite3
import sys
import re
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memory.session_store import SessionStore
from data_pipeline.process_reports import process_uploaded_excel


def test_chunk_replace_pagination_and_cascade(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    kb_id = "kb_test"
    store.create_knowledge_base(kb_id, "Test KB")

    store.replace_knowledge_base_chunks(
        kb_id,
        [{"chunk_index": 1, "module_name": "old", "combined_text": "old"}],
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.replace_knowledge_base_chunks(
            kb_id,
            [
                {"chunk_id": "duplicate", "chunk_index": 1},
                {"chunk_id": "duplicate", "chunk_index": 2},
            ],
        )

    # The failed replacement must roll back its preceding DELETE.
    unchanged = store.list_knowledge_base_chunks(kb_id)
    assert unchanged["total"] == 1
    assert unchanged["items"][0]["module_name"] == "old"

    store.replace_knowledge_base_chunks(
        kb_id,
        [
            {"chunk_index": 1, "module_name": "first"},
            {"chunk_index": 2, "module_name": "second"},
        ],
    )
    second_page = store.list_knowledge_base_chunks(kb_id, page=2, size=1)
    assert second_page["total"] == 2
    assert second_page["items"][0]["module_name"] == "second"

    assert store.delete_knowledge_base(kb_id) is True
    assert store.list_knowledge_base_chunks(kb_id)["total"] == 0


def test_excel_parser_returns_chunk_records(tmp_path):
    import pandas as pd

    source = tmp_path / "reports.xlsx"
    pd.DataFrame([
        {
            "检查类型": "CT",
            "一级目录": "胸部",
            "模块名称": "肺部感染",
            "影像学表现": "双肺见斑片状密度增高影。",
            "影像学意见": "考虑肺部感染。",
        },
    ]).to_excel(source, index=False)

    result = process_uploaded_excel(str(source), tmp_path / "markdown", "kb_excel")

    assert result["processed_rows"] == 1
    assert result["errors"] == []
    assert len(result["chunks"]) == 1
    chunk = result["chunks"][0]
    assert re.fullmatch(r"[0-9a-f]{32}", chunk["chunk_id"])
    assert chunk["modality"] == "CT"
    assert chunk["source_row"] == 2
    assert "双肺见斑片状密度增高影" in chunk["combined_text"]

    markdown = next((tmp_path / "markdown" / "kb_excel").rglob("*.md"))
    assert f"chunk_id: {chunk['chunk_id']}" in markdown.read_text(encoding="utf-8")


# ── source_hash 相关测试 ────────────────────────────────────


def _compute_sha256(file_path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def test_source_hash_stored_in_catalog(tmp_path):
    """新建 KB 时 source_hash 写入 SQLite 知识库目录。"""
    import pandas as pd

    store = SessionStore(str(tmp_path / "sessions.db"))
    source = tmp_path / "CT颅脑平扫.xlsx"
    pd.DataFrame([
        {
            "检查类型": "CT",
            "一级目录": "头颅",
            "模块名称": "颅脑平扫",
            "影像学表现": "未见异常。",
            "影像学意见": "颅脑CT平扫未见异常。",
        },
    ]).to_excel(source, index=False)

    expected_hash = _compute_sha256(source)
    kb_id = "kb_test_hash"
    kb_name = "CT颅脑平扫"
    source_name = source.name

    store.create_knowledge_base(
        kb_id, kb_name, status="uploaded",
        source_path=str(source),
        source_name=source_name,
        source_hash=expected_hash,
    )
    kb = store.get_knowledge_base(kb_id)
    assert kb is not None
    assert kb["kb_id"] == kb_id
    assert kb["kb_name"] == kb_name
    assert kb["source_name"] == source_name
    assert kb["source_hash"] == expected_hash


def test_same_name_same_content_different_kb_id(tmp_path):
    """同名同内容：新建知识库即使文件名相同，也生成新 kb_id。"""
    import pandas as pd

    store = SessionStore(str(tmp_path / "sessions.db"))
    source = tmp_path / "report.xlsx"
    pd.DataFrame([
        {
            "检查类型": "CT",
            "一级目录": "胸部",
            "模块名称": "测试",
            "影像学表现": "无异常。",
            "影像学意见": "无异常。",
        },
    ]).to_excel(source, index=False)

    source_hash = _compute_sha256(source)

    kb1 = store.create_knowledge_base(
        "kb_001", "Report", status="uploaded",
        source_path=str(source),
        source_name=source.name,
        source_hash=source_hash,
    )
    kb2 = store.create_knowledge_base(
        "kb_002", "Report", status="uploaded",
        source_path=str(source),
        source_name=source.name,
        source_hash=source_hash,
    )

    assert kb1["kb_id"] != kb2["kb_id"]
    assert kb1["source_name"] == kb2["source_name"] == "report.xlsx"
    assert kb1["source_hash"] == kb2["source_hash"] == source_hash


def test_same_name_different_content_different_kb_id(tmp_path):
    """同名不同内容：新建知识库即使文件名相同，也生成新 kb_id 且 hash 不同。"""
    import pandas as pd

    store = SessionStore(str(tmp_path / "sessions.db"))

    source_a = tmp_path / "report.xlsx"
    pd.DataFrame([
        {
            "检查类型": "CT",
            "一级目录": "胸部",
            "模块名称": "测试A",
            "影像学表现": "内容A。",
            "影像学意见": "内容A。",
        },
    ]).to_excel(source_a, index=False)

    source_b = tmp_path / "report.xlsx"
    os_module = __import__("os")
    os_module.remove(str(source_a))
    pd.DataFrame([
        {
            "检查类型": "MR",
            "一级目录": "头部",
            "模块名称": "测试B",
            "影像学表现": "内容B。",
            "影像学意见": "内容B。",
        },
    ]).to_excel(source_b, index=False)

    hash_a = _compute_sha256(source_a)
    hash_b = _compute_sha256(source_b)
    assert hash_a != hash_b

    kb1 = store.create_knowledge_base(
        "kb_a", "Report", status="uploaded",
        source_path=str(source_a),
        source_name="report.xlsx",
        source_hash=hash_a,
    )
    kb2 = store.create_knowledge_base(
        "kb_b", "Report", status="uploaded",
        source_path=str(source_b),
        source_name="report.xlsx",
        source_hash=hash_b,
    )

    assert kb1["kb_id"] != kb2["kb_id"]
    assert kb1["source_name"] == kb2["source_name"] == "report.xlsx"
    assert kb1["source_hash"] != kb2["source_hash"]


def test_rebuild_preserves_kb_id_and_updates_hash(tmp_path):
    """重建当前 KB：保持原 kb_id，更新 source_hash。"""
    import pandas as pd

    store = SessionStore(str(tmp_path / "sessions.db"))

    source = tmp_path / "report.xlsx"
    pd.DataFrame([
        {
            "检查类型": "CT",
            "一级目录": "胸部",
            "模块名称": "原始",
            "影像学表现": "原始内容。",
            "影像学意见": "原始内容。",
        },
    ]).to_excel(source, index=False)

    old_hash = _compute_sha256(source)
    kb_id = "kb_rebuild"
    store.create_knowledge_base(
        kb_id, "Report", status="uploaded",
        source_path=str(source),
        source_name="report.xlsx",
        source_hash=old_hash,
    )

    kb_before = store.get_knowledge_base(kb_id)
    assert kb_before["source_hash"] == old_hash

    os_module = __import__("os")
    os_module.remove(str(source))
    pd.DataFrame([
        {
            "检查类型": "CT",
            "一级目录": "胸部",
            "模块名称": "更新",
            "影像学表现": "更新内容。",
            "影像学意见": "更新内容。",
        },
    ]).to_excel(source, index=False)

    new_hash = _compute_sha256(source)
    assert new_hash != old_hash

    store.update_knowledge_base(kb_id, source_hash=new_hash)
    kb_after = store.get_knowledge_base(kb_id)
    assert kb_after["kb_id"] == kb_id
    assert kb_after["source_hash"] == new_hash
    assert kb_after["source_hash"] != old_hash


def test_new_kb_same_name_generates_new_kb_id(tmp_path):
    """新建知识库：同名文件也生成新 kb_id。"""
    import pandas as pd

    store = SessionStore(str(tmp_path / "sessions.db"))

    source = tmp_path / "report.xlsx"
    pd.DataFrame([
        {
            "检查类型": "CT",
            "一级目录": "胸部",
            "模块名称": "测试",
            "影像学表现": "内容。",
            "影像学意见": "内容。",
        },
    ]).to_excel(source, index=False)

    source_hash = _compute_sha256(source)

    kb_id_1 = "kb_new_001"
    kb_id_2 = "kb_new_002"

    store.create_knowledge_base(
        kb_id_1, "Report", status="uploaded",
        source_path=str(source),
        source_name="report.xlsx",
        source_hash=source_hash,
    )
    store.create_knowledge_base(
        kb_id_2, "Report", status="uploaded",
        source_path=str(source),
        source_name="report.xlsx",
        source_hash=source_hash,
    )

    kb1 = store.get_knowledge_base(kb_id_1)
    kb2 = store.get_knowledge_base(kb_id_2)

    assert kb1["kb_id"] != kb2["kb_id"]
    assert kb1["source_name"] == kb2["source_name"]
    assert kb1["source_hash"] == kb2["source_hash"]