import sys
from pathlib import Path

import pytest


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from data_pipeline import build_vector_db
from milvus_compat import _install_windows_manifest_replace


class _FakeIndexParams:
    def __init__(self):
        self.items = []

    def add_index(self, **kwargs):
        self.items.append(kwargs)


class _ScalarIndexClient:
    def __init__(self, error):
        self.error = error
        self.params = _FakeIndexParams()

    def list_indexes(self, _collection_name):
        return ["dense_vector", "sparse_vector"]

    def prepare_index_params(self):
        return self.params

    def create_index(self, **_kwargs):
        raise self.error


def test_local_milvus_lite_skips_optional_scalar_indexes(monkeypatch):
    client = _ScalarIndexClient(RuntimeError("must not be called"))
    monkeypatch.setattr(build_vector_db, "_uses_milvus_lite", lambda: True)

    created = build_vector_db._ensure_scalar_indexes(
        client, "medical_reports", ("kb_id", "chunk_id")
    )

    assert created == []
    assert client.params.items == []


def test_known_scalar_index_compatibility_error_is_non_fatal(monkeypatch):
    client = _ScalarIndexClient(
        RuntimeError("create_index missing required 'metric_type' parameter")
    )
    monkeypatch.setattr(build_vector_db, "_uses_milvus_lite", lambda: False)

    created = build_vector_db._ensure_scalar_indexes(
        client, "medical_reports", ("kb_id", "chunk_id")
    )

    assert created == []
    assert client.params.items == [
        {"field_name": "kb_id", "index_type": "INVERTED"},
        {"field_name": "chunk_id", "index_type": "INVERTED"},
    ]


def test_unexpected_scalar_index_error_still_fails_startup(monkeypatch):
    client = _ScalarIndexClient(RuntimeError("permission denied"))
    monkeypatch.setattr(build_vector_db, "_uses_milvus_lite", lambda: False)

    with pytest.raises(RuntimeError, match="permission denied"):
        build_vector_db._ensure_scalar_indexes(client, "medical_reports", ("kb_id",))


def test_manifest_patch_replaces_an_existing_manifest(tmp_path):
    from milvus_lite.storage import manifest as manifest_module

    assert _install_windows_manifest_replace(
        manifest_module.Manifest,
        manifest_module,
    )

    manifest = manifest_module.Manifest(str(tmp_path))
    manifest.save()
    manifest.current_seq = 1
    manifest.save()

    loaded = manifest_module.Manifest.load(str(tmp_path))
    assert loaded.current_seq == 1
    assert loaded.version == 2
    assert (tmp_path / "manifest.json.prev").exists()
