"""SQLite knowledge-base chunk persistence tests."""

import sqlite3
import sys
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
    assert chunk["chunk_id"] == "kb_excel:00000001"
    assert chunk["modality"] == "CT"
    assert chunk["source_row"] == 2
    assert "双肺见斑片状密度增高影" in chunk["combined_text"]
