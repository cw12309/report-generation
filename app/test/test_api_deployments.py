"""API Deployment persistence, security, isolation, and public-contract tests."""

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from chat.deployments import (
    ConversationLockRegistry,
    generate_api_key,
    generate_internal_session_id,
    hash_api_key,
    map_public_event,
    normalize_selected_kbs,
    verify_api_key,
)
from gateway import (
    DeploymentChatPayload,
    _forward_request_headers,
    app as gateway_app,
    extract_deployment_api_key,
)
from memory.session_store import SessionStore


def _create_deployment(store: SessionStore, deployment_id: str, selected_kbs: list[str]):
    plaintext, digest, prefix = generate_api_key()
    deployment = store.create_api_deployment(
        deployment_id=deployment_id,
        name=f"Deployment {deployment_id}",
        description="test",
        selected_kbs=selected_kbs,
        api_key_hash=digest,
        api_key_prefix=prefix,
    )
    return plaintext, deployment


@pytest.fixture
def deployment_store(tmp_path):
    store = SessionStore(str(tmp_path / "deployments.db"))
    store.create_knowledge_base("kb_a", "KB A", status="active")
    store.create_knowledge_base("kb_b", "KB B", status="active")
    store.create_knowledge_base("kb_inactive", "KB Inactive", status="disabled")
    return store


def test_deployment_crud_key_rotation_and_soft_delete(deployment_store):
    old_key, deployment = _create_deployment(
        deployment_store,
        "dep_crud",
        ["kb_a", "kb_b"],
    )
    assert deployment["selected_kbs"] == ["kb_a", "kb_b"]
    assert deployment["health"] == "healthy"
    assert old_key not in str(deployment)
    assert deployment_store.list_api_deployments()[0]["deployment_id"] == "dep_crud"

    updated = deployment_store.update_api_deployment(
        "dep_crud",
        name="Updated",
        description="updated description",
        selected_kbs=["kb_b"],
    )
    assert updated["name"] == "Updated"
    assert updated["selected_kbs"] == ["kb_b"]

    disabled = deployment_store.set_api_deployment_status("dep_crud", "disabled")
    assert disabled["status"] == "disabled"
    enabled = deployment_store.set_api_deployment_status("dep_crud", "active")
    assert enabled["status"] == "active"

    new_key, new_hash, new_prefix = generate_api_key()
    rotated = deployment_store.rotate_api_deployment_key(
        "dep_crud",
        api_key_hash=new_hash,
        api_key_prefix=new_prefix,
    )
    assert not verify_api_key(old_key, rotated["api_key_hash"])
    assert verify_api_key(new_key, rotated["api_key_hash"])

    conversation = deployment_store.create_api_conversation(
        deployment_id="dep_crud",
        conversation_id="conv_history",
        internal_session_id=generate_internal_session_id(),
    )
    assert deployment_store.soft_delete_api_deployment("dep_crud") is True
    assert deployment_store.get_api_deployment("dep_crud") is None
    assert deployment_store.session_exists(conversation["internal_session_id"])


def test_kb_scope_validation_and_realtime_health(deployment_store):
    active = {
        kb["kb_id"]: kb
        for kb in deployment_store.list_knowledge_bases(status="active")
    }
    normalized, records = normalize_selected_kbs(
        ["kb_a", "kb_a", " kb_b "],
        active,
    )
    assert normalized == ["kb_a", "kb_b"]
    assert [item["kb_id"] for item in records] == normalized

    with pytest.raises(ValueError, match="至少选择"):
        normalize_selected_kbs([], active)
    with pytest.raises(ValueError, match="kb_unknown"):
        normalize_selected_kbs(["kb_unknown"], active)
    with pytest.raises(ValueError, match="kb_inactive"):
        normalize_selected_kbs(["kb_inactive"], active)

    _create_deployment(deployment_store, "dep_health", ["kb_a"])
    deployment_store.update_knowledge_base("kb_a", status="disabled")
    degraded = deployment_store.get_api_deployment("dep_health")
    assert degraded["health"] == "degraded"
    assert degraded["unavailable_kbs"][0]["kb_id"] == "kb_a"


def test_conversation_restores_existing_memory_and_isolates_deployments(deployment_store):
    _create_deployment(deployment_store, "dep_a", ["kb_a"])
    _create_deployment(deployment_store, "dep_b", ["kb_b"])

    conv_a = deployment_store.create_api_conversation(
        deployment_id="dep_a",
        conversation_id="conversation_1",
        internal_session_id=generate_internal_session_id(),
    )
    conv_b = deployment_store.create_api_conversation(
        deployment_id="dep_b",
        conversation_id="conversation_1",
        internal_session_id=generate_internal_session_id(),
    )
    assert conv_a["internal_session_id"] != conv_b["internal_session_id"]

    deployment_store.save_turn(
        conv_a["internal_session_id"],
        0,
        "CT头颅，考虑脑出血",
        "右侧见出血灶",
    )
    deployment_store.save_state(
        conv_a["internal_session_id"],
        {"modality": "CT", "diagnosis": ["脑出血"]},
        '{"results":[{"影像学表现":"右侧病灶"}]}',
        last_ambiguity={"options": ["脑出血", "钙化"]},
        selected_kbs=["kb_a"],
    )

    restored_mapping = deployment_store.get_api_conversation("dep_a", "conversation_1")
    restored = deployment_store.load_session(restored_mapping["internal_session_id"])
    isolated = deployment_store.load_session(conv_b["internal_session_id"])
    assert restored["turns"][0]["user_input"] == "CT头颅，考虑脑出血"
    assert restored["state"]["entity_slots"]["modality"] == "CT"
    assert "右侧病灶" in restored["state"]["last_report"]
    assert restored["state"]["last_ambiguity"]["options"] == ["脑出血", "钙化"]
    assert restored["state"]["selected_kbs"] == ["kb_a"]
    assert isolated["turns"] == []
    assert isolated["state"]["last_report"] == ""


def test_public_request_schema_and_key_authentication():
    payload = DeploymentChatPayload(message="生成报告")
    assert payload.conversation_id is None
    with pytest.raises(ValidationError):
        DeploymentChatPayload(message="生成报告", selected_kbs=["kb_escape"])

    plaintext, digest, _ = generate_api_key()
    assert digest == hash_api_key(plaintext)
    assert verify_api_key(plaintext, digest)
    assert not verify_api_key("wrong", digest)

    with pytest.raises(HTTPException) as missing:
        asyncio.run(extract_deployment_api_key(None))
    assert missing.value.status_code == 401
    assert asyncio.run(extract_deployment_api_key(f"Bearer {plaintext}")) == plaintext

    paths = {route.path for route in gateway_app.routes}
    assert "/api/v1/deployments/{deployment_id}/chat" in paths
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/deployments/dep/chat",
        "headers": [
            (b"authorization", b"Bearer must-not-forward"),
            (b"x-deployment-api-key", b"spoofed"),
            (b"x-request-id", b"request-1"),
        ],
        "query_string": b"",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
        "root_path": "",
    })
    forwarded = _forward_request_headers(request)
    assert "authorization" not in forwarded
    assert "x-deployment-api-key" not in forwarded
    assert forwarded["x-request-id"] == "request-1"


def test_public_sse_mapper_exposes_only_stable_events():
    conversation_id = "conv_public"
    mapped = [
        map_public_event("search", {"query": "secret planning detail"}, conversation_id=conversation_id),
        map_public_event("message", {"content": "普通回复"}, conversation_id=conversation_id),
        map_public_event(
            "report",
            {"content": "报告", "data": {"results": [{"诊断意见": "脑出血"}]}},
            conversation_id=conversation_id,
        ),
        map_public_event("ambiguous", {"options": ["A", "B"]}, conversation_id=conversation_id),
        map_public_event("error", {"message": "失败"}, conversation_id=conversation_id),
        map_public_event("done", {}, conversation_id=conversation_id),
    ]
    assert [event["type"] for event in mapped] == [
        "status", "message", "report", "ambiguous", "error", "done",
    ]
    assert mapped[-1]["conversation_id"] == conversation_id
    assert map_public_event("query_plan", {"filters": {"kb_id": "hidden"}}, conversation_id=conversation_id) is None
    assert map_public_event("memory_retrieval", {"results": ["hidden"]}, conversation_id=conversation_id) is None


def test_conversation_lock_serializes_same_key_but_not_different_keys():
    async def scenario():
        registry = ConversationLockRegistry()
        active = 0
        max_same_key = 0
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        different_entered = asyncio.Event()

        async def first():
            nonlocal active, max_same_key
            async with registry.hold("dep", "conv"):
                active += 1
                max_same_key = max(max_same_key, active)
                first_entered.set()
                await release_first.wait()
                active -= 1

        async def second():
            nonlocal active, max_same_key
            await first_entered.wait()
            async with registry.hold("dep", "conv"):
                active += 1
                max_same_key = max(max_same_key, active)
                active -= 1

        async def different():
            await first_entered.wait()
            async with registry.hold("dep", "other"):
                different_entered.set()

        tasks = [
            asyncio.create_task(first()),
            asyncio.create_task(second()),
            asyncio.create_task(different()),
        ]
        await first_entered.wait()
        await asyncio.wait_for(different_entered.wait(), timeout=1)
        release_first.set()
        await asyncio.gather(*tasks)
        return max_same_key

    assert asyncio.run(scenario()) == 1
