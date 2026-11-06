"""Deterministic template matching, caching and SQLite counters."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.reporting import template_admin as admin, template_usage as usage, user_env


@pytest.fixture
def template_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(admin, "TEMPLATES_FILE", tmp_path / "templates.yml")
    monkeypatch.setattr(admin, "_cache_loaded", False)
    monkeypatch.setattr(admin, "_index_cache", {})
    # OrderedDict is required for bounded LRU eviction.
    from collections import OrderedDict
    monkeypatch.setattr(admin, "_index_cache", OrderedDict())
    monkeypatch.setattr(user_env, "_ENV_DIR", tmp_path / "users")
    monkeypatch.setattr(user_env, "_cache", {})
    monkeypatch.setattr(user_env, "_cache_versions", {})
    monkeypatch.setattr(usage, "DB_PATH", tmp_path / "usage.sqlite3")
    user_env.set_current_user("alice")
    template = dict(id="t1", name="范例", route_id="test", enabled=True,
                    match_keywords=["突出"], tags=[], imaging_findings="范文所见", imaging_diagnosis="范文诊断",
                    quality_score=5, usage_count=7)
    admin.save_templates([template])
    yield template
    user_env.set_current_user("")


def test_matching_reasons_and_no_popularity_only_match(template_environment):
    template, reference = admin.match_template_with_reference("test", "椎间盘突出")
    assert template["id"] == "t1"
    assert reference["matched_keywords"] == ["突出"]
    assert reference["text_score"] >= 2
    assert len(reference["template_version"]) == 16
    assert admin.match_template("other", "突出") is None
    assert admin.match_template("test", "zzzz") is None


def test_warm_index_reuses_features_and_counter_write_preserves_files(template_environment):
    admin.match_template("test", "突出")
    before = admin.TEMPLATES_FILE.read_bytes()
    with patch.object(admin, "_bigrams", wraps=admin._bigrams) as grams:
        _, reference = admin.match_template_with_reference("test", "突出")
    assert grams.call_count == 1  # Only input, not every template.
    usage.record_injection("alice", reference)
    assert admin.TEMPLATES_FILE.read_bytes() == before
    assert not user_env._ENV_DIR.exists()


def test_edits_toggle_delete_and_user_isolation_invalidate_index(template_environment):
    _, original = admin.match_template_with_reference("test", "突出")
    user_env.upsert_template("alice", {**template_environment, "name": "个人范例"})
    template, changed = admin.match_template_with_reference("test", "突出")
    assert template["name"] == "个人范例"
    assert changed["template_version"] != original["template_version"]
    user_env.set_current_user("bob")
    assert admin.match_template("test", "突出")["name"] == "范例"
    user_env.set_current_user("alice")
    user_env.toggle_template("alice", "t1", True)
    assert admin.match_template("test", "突出") is None
    user_env.toggle_template("alice", "t1", True)
    assert admin.match_template("test", "突出") is not None
    user_env.delete_template("alice", "t1", is_base=True)
    assert admin.match_template("test", "突出") is None


def test_baseline_file_edit_invalidates_cached_index(template_environment):
    admin.match_template("test", "突出")
    import yaml
    admin.TEMPLATES_FILE.write_text(yaml.safe_dump({"templates": [{**template_environment, "name": "new baseline"}]}), encoding="utf-8")
    assert admin.match_template("test", "突出")["name"] == "new baseline"


def test_idempotent_success_is_separate_from_injection_and_scoped(template_environment):
    _, reference = admin.match_template_with_reference("test", "突出")
    usage.record_injection("alice", reference)
    usage.record_injection("alice", reference)
    usage.mark_success("alice", reference)
    assert usage.counts("alice")["t1"] == {"injection_count": 1, "success_count": 0}
    reference["validation_passed"] = True
    usage.mark_success("bob", reference)
    assert usage.counts("bob") == {}
    usage.mark_success("alice", reference)
    usage.mark_success("alice", reference)
    assert usage.counts("alice")["t1"] == {"injection_count": 1, "success_count": 1}
    annotated = usage.annotate([template_environment], "alice")[0]
    assert annotated["usage_count"] == 8 and annotated["legacy_usage_count"] == 7


def test_parallel_writes_do_not_lose_counts(template_environment):
    def record(i):
        _, reference = admin.match_template_with_reference("test", "突出")
        usage.record_injection("alice", reference)
        reference["validation_passed"] = i % 2 == 0
        usage.mark_success("alice", reference)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(40)))
    assert usage.counts("alice")["t1"] == {"injection_count": 40, "success_count": 20}


def test_index_is_bounded(template_environment):
    for i in range(80):
        user_env.set_current_user(f"user{i}")
        admin.match_template("test", "突出")
    assert len(admin._index_cache) == 64
