import logging
import sys
from pathlib import Path

from fastapi.testclient import TestClient


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from chat import server as server_module
from memory.session_store import SessionStore
from observability import (
    ObservabilityStore,
    StructuredLogHandler,
    observability_store,
    sanitize_value,
)


class _DummyMilvusClient:
    def load_collection(self, _name):
        return None

    def close(self):
        return None


def test_structured_sanitization_removes_secrets_and_patient_payloads():
    sanitized = sanitize_value({
        "Authorization": "Bearer top-secret-token",
        "api_key": "sk-sensitive-value",
        "message": "query=患者张某右肺占位, result=ok",
        "nested": {"password": "secret-password"},
    })

    serialized = str(sanitized)
    assert "top-secret-token" not in serialized
    assert "sk-sensitive-value" not in serialized
    assert "患者张某右肺占位" not in serialized
    assert "secret-password" not in serialized
    assert serialized.count("[REDACTED]") >= 4


def test_trace_metrics_and_pipeline_stages_share_one_store():
    store = ObservabilityStore(max_traces=20, max_logs=100)
    store.start_request(
        "req_test",
        method="POST",
        path="/api/chat",
        session_id="session-a",
    )
    store.record_pipeline_event(
        "req_test",
        "entity_update",
        {"changes": {"diagnosis": "敏感诊断"}, "slots": {"name": "患者"}},
        session_id="session-a",
    )
    store.record_pipeline_event(
        "req_test",
        "query_plan",
        {"filters": {"modality": "CT"}, "query": "敏感查询"},
    )
    store.record_pipeline_event(
        "req_test",
        "tool_executed",
        {"name": "rag_search", "result": "完整报告内容"},
    )
    store.record_pipeline_event(
        "req_test",
        "error",
        {"code": "RECOVERY_FAILED", "message": "完整错误内容"},
    )
    store.record_pipeline_event(
        "req_test",
        "report",
        {"content": "完整患者报告"},
    )
    store.record_pipeline_event("req_test", "done", {"total_time": 1.2})
    store.finish_request("req_test", status_code=200, duration_ms=123.4)

    trace = store.get_trace("req_test")
    assert trace is not None
    assert [event["stage"] for event in trace["events"]] == [
        "request", "entity", "rag", "tool", "recovery", "report", "done"
    ]
    assert "敏感" not in str(trace)
    assert "完整" not in str(trace)

    overview = store.overview()
    assert overview["request_count"] == 1
    assert overview["success_rate"] == 100.0
    assert overview["average_latency_ms"] == 123.4


def test_structured_log_handler_omits_traceback_and_filters_logs():
    store = ObservabilityStore(max_logs=100)
    handler = StructuredLogHandler(store)
    try:
        raise RuntimeError("password=do-not-expose")
    except RuntimeError:
        record = logging.LogRecord(
            name="chat.pipeline",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="query=患者内容, Authorization=Bearer hidden-token",
            args=(),
            exc_info=sys.exc_info(),
        )
        handler.emit(record)
    handler.emit(logging.LogRecord(
        name="tools.registry",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="工具参数: %s",
        args=({"query": "另一段患者正文"},),
        exc_info=None,
    ))

    logs = store.list_logs(level="ERROR", module="chat.pipeline")
    assert len(logs) == 1
    assert logs[0]["error_type"] == "RuntimeError"
    assert "患者内容" not in logs[0]["message"]
    assert "hidden-token" not in logs[0]["message"]
    assert "Traceback" not in str(logs[0])
    assert "另一段患者正文" not in str(store.list_logs(module="tools.registry"))


def test_observability_routes_reuse_http_request_data(tmp_path):
    observability_store.reset()
    app = server_module.create_app(
        session_store=SessionStore(str(tmp_path / "observability-routes.db"))
    )
    client = TestClient(app)

    health_response = client.get("/health", headers={"X-Request-ID": "req_health"})
    assert health_response.status_code == 200
    assert health_response.headers["X-Request-ID"] == "req_health"

    overview = client.get("/api/observability/overview").json()
    assert overview["request_count"] == 1
    assert overview["success_rate"] == 100.0
    assert overview["sessions"] == {"total": 0, "turns": 0}

    traces = client.get("/api/observability/traces").json()
    assert traces["total"] == 1
    assert traces["items"][0]["request_id"] == "req_health"

    detail = client.get("/api/observability/traces/req_health").json()
    assert detail["events"][0]["stage"] == "request"
    assert any(
        getattr(route, "path", "") == "/api/observability/logs/stream"
        for route in app.routes
    )
    assert client.get(
        "/api/observability/logs/stream?level=INVALID"
    ).status_code == 422


def test_chat_reuses_pipeline_events_for_request_trace(tmp_path, monkeypatch):
    observability_store.reset()
    session_store = SessionStore(str(tmp_path / "observability-chat.db"))
    session_store.create_knowledge_base("kb-a", "KB A", status="active")

    async def fake_pipeline(
        query,
        session_id,
        stm,
        _entity_tracker,
        _client,
        _last_report,
        emit,
        **_kwargs,
    ):
        emit("intent", {"intent": "TOOL_CALL"})
        emit("entity_update", {"changes": {"modality": "CT"}, "slots": {}})
        emit("memory_retrieval", {"count": 1, "content": "患者原文"})
        emit("tool_executed", {"name": "rag_search", "result": "完整检索内容"})
        emit("query_plan", {"filters": {"modality": "CT"}, "query": query})
        emit("error", {"code": "RECOVERY_FAILED", "message": "敏感错误内容"})
        emit("report", {"content": "患者完整报告"})
        stm.add_turn(session_id, query, "done")
        return "done"

    monkeypatch.setattr(
        server_module,
        "_new_milvus_client",
        lambda: _DummyMilvusClient(),
    )
    monkeypatch.setattr(server_module, "run_pipeline", fake_pipeline)

    app = server_module.create_app(session_store=session_store)
    client = TestClient(app)
    response = client.post(
        "/api/chat",
        headers={"X-Request-ID": "req_chat_trace"},
        json={
            "query": "患者敏感问题",
            "session_id": "session-trace",
            "selected_kbs": ["kb-a"],
        },
    )
    assert response.status_code == 200

    trace = client.get(
        "/api/observability/traces/req_chat_trace"
    ).json()
    stages = [event["stage"] for event in trace["events"]]
    for expected in ("request", "entity", "memory", "tool", "rag", "recovery", "report", "done"):
        assert expected in stages
    serialized = str(trace)
    assert "患者敏感问题" not in serialized
    assert "患者完整报告" not in serialized
    assert "完整检索内容" not in serialized


def test_monitor_page_keeps_one_top_level_page_with_four_parallel_tabs(tmp_path):
    app = server_module.create_app(
        session_store=SessionStore(str(tmp_path / "observability-ui.db"))
    )
    client = TestClient(app)

    html = client.get("/").text
    script = client.get("/static/app.js").text
    style = client.get("/static/style.css").text
    assert html.count('id="pageMonitor"') == 1
    assert 'data-monitor-tab="overview"' in html
    assert 'data-monitor-tab="sessions"' in html
    assert 'data-monitor-tab="traces"' in html
    assert 'data-monitor-tab="logs"' in html
    assert "会话记录" in html
    assert "请求 Trace" in html
    assert "实时日志" in html
    overview_html = html[
        html.index('id="monitorPanelOverview"'):
        html.index('id="monitorPanelSessions"')
    ]
    sessions_html = html[
        html.index('id="monitorPanelSessions"'):
        html.index('id="monitorPanelTraces"')
    ]
    assert 'id="monitorSessionsList"' not in overview_html
    assert 'id="monitorSessionsList"' in sessions_html
    assert '["overview", "sessions", "traces", "logs"]' in script
    assert "new EventSource" in script
    assert "function renderMonitorIdentifier(value, kind" in script
    assert script.count("renderMonitorIdentifier(") >= 8
    assert "data-monitor-copy-id" in script
    assert "data-monitor-copy-label" in script
    assert "copyButton.dataset.monitorCopyId" in script
    assert "data-open-trace-request-id" in script
    assert "monitorTraceRequestFilter.value = requestId" in script
    assert 'activateMonitorTab("traces")' in script
    assert "s.id.slice" not in script
    assert "sessionId.slice" not in script
    assert "requestId.slice" not in script
    assert "overflow-wrap: anywhere" in style
    assert ".monitor-identifier code" in style
    assert ".monitor-id-link" in style
    assert ".monitor-log-row > code" in style
    mobile_style = style[style.index("@media (max-width: 620px)"):]
    assert ".monitor-log-context {\n    grid-column: 1 / -1;" in mobile_style
    for field in ("level", "module", "session_id", "request_id"):
        assert f'["{field}",' in script
