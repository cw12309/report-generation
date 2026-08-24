"""Worker route tests for Deployment management and Stateful SSE reuse."""

import json
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _install_optional_dependency_stubs() -> None:
    """Allow isolated route tests when optional model/trace SDKs are absent."""
    try:
        __import__("openai")
    except ModuleNotFoundError:
        openai = types.ModuleType("openai")

        class DummyClient:
            def __init__(self, *args, **kwargs):
                pass

        class DummyOpenAIError(Exception):
            pass

        openai.AsyncOpenAI = DummyClient
        openai.OpenAI = DummyClient
        openai.APIConnectionError = DummyOpenAIError
        openai.APITimeoutError = DummyOpenAIError
        openai.APIStatusError = DummyOpenAIError
        sys.modules["openai"] = openai

    try:
        __import__("langsmith")
    except ModuleNotFoundError:
        langsmith = types.ModuleType("langsmith")

        def traceable(function=None, **_kwargs):
            if function is not None:
                return function
            return lambda wrapped: wrapped

        langsmith.traceable = traceable
        langsmith.wrappers = types.SimpleNamespace(wrap_openai=lambda client: client)
        run_helpers = types.ModuleType("langsmith.run_helpers")
        run_helpers.get_current_run_tree = lambda: None
        sys.modules["langsmith"] = langsmith
        sys.modules["langsmith.run_helpers"] = run_helpers

    try:
        __import__("multipart")
    except ModuleNotFoundError:
        multipart = types.ModuleType("multipart")
        multipart.__version__ = "0.0-test"
        multipart_parser = types.ModuleType("multipart.multipart")
        multipart_parser.parse_options_header = lambda value: (value, {})
        sys.modules["multipart"] = multipart
        sys.modules["multipart.multipart"] = multipart_parser


_install_optional_dependency_stubs()

from chat import server as server_module
from memory.session_store import SessionStore


class DummyMilvusClient:
    def load_collection(self, _name):
        return None

    def close(self):
        return None


def _sse_events(response) -> list[dict]:
    events = []
    for line in response.text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        events.append(json.loads(line[6:]))
    return events


def test_deployment_management_auth_stateful_chat_and_regression(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "routes.db"))
    store.create_knowledge_base("kb_a", "KB A", status="active")
    store.create_knowledge_base("kb_b", "KB B", status="active")
    store.create_knowledge_base("kb_inactive", "KB Inactive", status="disabled")

    pipeline_calls = []

    async def fake_run_pipeline(
        query,
        session_id,
        stm,
        entity_tracker,
        _client,
        last_report,
        emit,
        *,
        selected_diagnosis=None,
        last_ambiguity=None,
        selected_kbs=None,
        knowledge_bases=None,
    ):
        del selected_diagnosis, knowledge_bases
        pipeline_calls.append({
            "query": query,
            "session_id": session_id,
            "selected_kbs": list(selected_kbs or []),
            "history": list(stm.get_history(session_id)),
            "modality": entity_tracker.slots.get("modality", ""),
            "last_report": last_report[0],
        })
        emit("query_plan", {"filters": {"internal": "must-not-leak"}})
        if query == "first":
            entity_tracker.slots["modality"] = "CT"
            last_report[0] = json.dumps(
                {"results": [{"影像学表现": "右侧病灶", "诊断意见": "脑出血"}]},
                ensure_ascii=False,
            )
            emit(
                "prompt_trace",
                {
                    "request_id": "spoofed-request-id",
                    "stage": "report_generation",
                    "prompt": {"system_prompt_chars": 10, "user_message_chars": 20},
                },
            )
            emit("report", {"content": "影像学表现：右侧病灶"})
            result = last_report[0]
        elif query == "ambiguous":
            last_ambiguity[0] = {"options": ["脑出血", "钙化"]}
            emit("ambiguous", {"options": ["脑出血", "钙化"]})
            result = "请选择诊断"
        elif query == "raise":
            raise RuntimeError("simulated failure")
        else:
            emit("message", {"content": "已恢复多轮状态"})
            result = "已恢复多轮状态"
        stm.add_turn(session_id, query, result)
        return result

    monkeypatch.setattr(server_module, "_new_milvus_client", lambda: DummyMilvusClient())
    monkeypatch.setattr(server_module, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        server_module,
        "ensure_collection_schema",
        lambda _client: {"created": False},
    )
    monkeypatch.setattr(
        server_module,
        "search_reports",
        lambda query, **_kwargs: (f"context:{query}", [{"chunk_id": "chunk-a"}]),
    )

    app = server_module.create_app(session_store=store)
    client = TestClient(app)

    assert client.post("/api/deployments", json={"name": "empty", "selected_kbs": []}).status_code == 422
    assert client.post(
        "/api/deployments",
        json={"name": "unknown", "selected_kbs": ["kb_unknown"]},
    ).status_code == 400
    assert client.post(
        "/api/deployments",
        json={"name": "inactive", "selected_kbs": ["kb_inactive"]},
    ).status_code == 400

    created_response = client.post(
        "/api/deployments",
        json={
            "name": "Clinical Agent",
            "description": "integration",
            "selected_kbs": ["kb_a", "kb_a", "kb_b"],
        },
    )
    assert created_response.status_code == 200
    created = created_response.json()
    deployment_id = created["deployment_id"]
    original_key = created["api_key"]
    assert created["selected_kbs"] == ["kb_a", "kb_b"]

    listed = client.get("/api/deployments").json()["deployments"]
    assert len(listed) == 1
    assert "api_key" not in listed[0]
    assert "api_key_hash" not in listed[0]
    assert listed[0]["api_key_prefix"].endswith("****")

    updated = client.patch(
        f"/api/deployments/{deployment_id}",
        json={"name": "Updated Agent", "selected_kbs": ["kb_a"]},
    ).json()
    assert updated["name"] == "Updated Agent"
    assert updated["selected_kbs"] == ["kb_a"]

    internal_path = f"/internal/deployments/{deployment_id}/chat"
    assert client.post(internal_path, json={"message": "first"}).status_code == 401
    assert client.post(
        internal_path,
        headers={"X-Deployment-API-Key": "wrong"},
        json={"message": "first"},
    ).status_code == 401
    assert client.post(
        internal_path,
        headers={"X-Deployment-API-Key": original_key},
        json={"message": "first", "selected_kbs": ["kb_b"]},
    ).status_code == 422

    first_response = client.post(
        internal_path,
        headers={"X-Deployment-API-Key": original_key},
        json={"message": "first"},
    )
    assert first_response.status_code == 200
    first_events = _sse_events(first_response)
    assert [event["type"] for event in first_events] == ["conversation", "report", "done"]
    assert all(event.get("request_id") for event in first_events)
    assert "data" in first_events[1]
    conversation_id = first_events[0]["conversation_id"]
    assert pipeline_calls[-1]["selected_kbs"] == ["kb_a"]

    second_response = client.post(
        internal_path,
        headers={"X-Deployment-API-Key": original_key},
        json={"message": "second", "conversation_id": conversation_id},
    )
    second_events = _sse_events(second_response)
    assert [event["type"] for event in second_events] == ["conversation", "message", "done"]
    assert pipeline_calls[-1]["history"]
    assert pipeline_calls[-1]["modality"] == "CT"
    assert "右侧病灶" in pipeline_calls[-1]["last_report"]

    ambiguous = client.post(
        internal_path,
        headers={"X-Deployment-API-Key": original_key},
        json={"message": "ambiguous", "conversation_id": conversation_id},
    )
    assert "ambiguous" in [event["type"] for event in _sse_events(ambiguous)]

    failed = client.post(
        internal_path,
        headers={"X-Deployment-API-Key": original_key},
        json={"message": "raise", "conversation_id": conversation_id},
    )
    assert [event["type"] for event in _sse_events(failed)][-2:] == ["error", "done"]

    rotated = client.post(f"/api/deployments/{deployment_id}/rotate-key").json()
    new_key = rotated["api_key"]
    assert client.post(
        internal_path,
        headers={"X-Deployment-API-Key": original_key},
        json={"message": "old key"},
    ).status_code == 401
    assert client.post(
        internal_path,
        headers={"X-Deployment-API-Key": new_key},
        json={"message": "new key", "conversation_id": conversation_id},
    ).status_code == 200

    assert client.post(f"/api/deployments/{deployment_id}/disable").status_code == 200
    assert client.post(
        internal_path,
        headers={"X-Deployment-API-Key": new_key},
        json={"message": "disabled", "conversation_id": conversation_id},
    ).status_code == 403
    assert client.post(f"/api/deployments/{deployment_id}/enable").status_code == 200

    store.update_knowledge_base("kb_a", status="disabled")
    degraded = client.get(f"/api/deployments/{deployment_id}").json()
    assert degraded["health"] == "degraded"
    assert client.post(
        internal_path,
        headers={"X-Deployment-API-Key": new_key},
        json={"message": "degraded", "conversation_id": conversation_id},
    ).status_code == 409
    store.update_knowledge_base("kb_a", status="active")

    # Existing browser Stateful Chat still uses the same extracted executor.
    browser_chat = client.post(
        "/api/chat",
        headers={"X-Request-ID": "req_prompt_trace_sse"},
        json={
            "query": "first",
            "session_id": "browser-regression",
            "selected_kbs": ["kb_a"],
        },
    )
    assert browser_chat.status_code == 200
    browser_events = _sse_events(browser_chat)
    assert "prompt_trace" in [event["type"] for event in browser_events]
    assert "report" in [event["type"] for event in browser_events]
    assert all(
        event["request_id"] == "req_prompt_trace_sse"
        for event in browser_events
    )

    browser_error = client.post(
        "/api/chat",
        headers={"X-Request-ID": "req_prompt_trace_error"},
        json={
            "query": "raise",
            "session_id": "browser-error",
            "selected_kbs": ["kb_a"],
        },
    )
    error_events = _sse_events(browser_error)
    assert [event["type"] for event in error_events][-2:] == ["error", "done"]
    assert all(
        event["request_id"] == "req_prompt_trace_error"
        for event in error_events
    )
    assert "data: [DONE]" in browser_error.text

    stateless = client.post(
        "/internal/rag/search",
        json={"query": "test", "selected_kbs": ["kb_a"]},
    )
    assert stateless.status_code == 200
    assert stateless.json()["results"][0]["chunk_id"] == "chunk-a"

    assert client.delete(f"/api/deployments/{deployment_id}").status_code == 200
    assert store.session_exists(
        store.get_api_conversation(deployment_id, conversation_id)["internal_session_id"]
    )
