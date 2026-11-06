"""Report integration without external models or patient data."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI

from app.reporting import router as routes, template_admin as admin, template_usage as usage, user_env
from app.reporting.auth import require_user
from app.reporting.schemas import GenerateRequest, IterateRequest, LLMReportOutput, ReportContent, ReportSession
from app.reporting.service import ReportService
from app.reporting.report_parser import ReportParseError
from app.test.test_template_usage import template_environment


@pytest.fixture
def service(template_environment):
    s = ReportService.__new__(ReportService)
    route = SimpleNamespace(id="test", name="test")
    s.route_resolver = Mock()
    s.route_resolver.resolve.return_value = route
    s.route_resolver.get_route_by_id.return_value = route
    s.prompt_loader = Mock()
    s.prompt_loader.get_system_prompt.return_value = "system"
    s.prompt_loader.build_user_prompt.return_value = "synthetic"
    s._kb_style_injection = Mock(return_value="")
    s._resolve_llm_override = Mock(return_value=(None, None, None))
    s.validator = Mock()
    s.validator.sanitize_vague.side_effect = lambda value: value
    s.validator.normalize_findings_lines.side_effect = lambda value: value
    s.validator.normalize_questions_select.side_effect = lambda value: value
    s.validator.validate.return_value = []
    async def model(**kwargs):
        output = LLMReportOutput(report=ReportContent(imaging_findings="synthetic findings", imaging_diagnosis="synthetic diagnosis"),
                                 questions=[], question_round=0, can_ask_questions=False)
        return output, output.model_dump_json(), 0.01, None
    s._call_llm_and_parse = AsyncMock(side_effect=model)
    return s


def generate(service):
    return service.generate_report(GenerateRequest(input_text="突出"), "synthetic-report", username="alice")


def test_reference_survives_serialization_and_records_per_version(service):
    async def run():
        session, _ = await generate(service)
        original = dict(session.template_reference)
        restored = ReportSession.model_validate_json(session.model_dump_json())
        assert restored.template_reference == original
        assert restored.messages[-1].template_reference == original
        assert usage.counts("alice")["t1"] == {"injection_count": 1, "success_count": 0}
        await routes._complete_template_reference(session, "alice")
        await routes._complete_template_reference(session, "alice")
        assert usage.counts("alice")["t1"]["success_count"] == 1
        session, _ = await service.iterate_report(IterateRequest(input_text="突出", base_version=1, question_round=0), session, username="alice")
        assert session.template_reference["report_version"] == 2
        assert session.template_reference["attempt_id"] != original["attempt_id"]
        assert session.messages[1].template_reference == original
        assert session.messages[-1].template_reference == session.template_reference
        await routes._complete_template_reference(session, "alice")
        session.current_version += 1  # A manual save must not count another generation.
        await routes._complete_template_reference(session, "alice")
        assert usage.counts("alice")["t1"] == {"injection_count": 2, "success_count": 2}
    asyncio.run(run())


def test_model_failure_is_only_an_injection(service):
    service._call_llm_and_parse.side_effect = RuntimeError("synthetic model error")
    with pytest.raises(RuntimeError):
        asyncio.run(generate(service))
    assert usage.counts("alice")["t1"] == {"injection_count": 1, "success_count": 0}


def test_invalid_report_does_not_count_as_success(service):
    service.validator.validate.return_value = ["synthetic invalid report"]
    service._repair_validation_errors = AsyncMock(side_effect=ReportParseError("invalid"))
    async def run():
        session, _ = await generate(service)
        assert session.template_reference["validation_passed"] is False
        await routes._complete_template_reference(session, "alice")
    asyncio.run(run())
    assert usage.counts("alice")["t1"]["success_count"] == 0


def test_no_matching_template_clears_latest_reference_but_keeps_history(service):
    async def run():
        session, _ = await generate(service)
        original = dict(session.template_reference)
        user_env.toggle_template("alice", "t1", True)
        session, _ = await service.iterate_report(IterateRequest(input_text="突出", base_version=1, question_round=0), session, username="alice")
        assert session.template_reference is None
        assert session.messages[-1].template_reference is None
        assert session.messages[1].template_reference == original
    asyncio.run(run())
    assert usage.counts("alice")["t1"]["injection_count"] == 1


@pytest.mark.parametrize("persisted", [True, False])
def test_http_generation_persistence_and_template_stats(service, tmp_path, monkeypatch, persisted):
    monkeypatch.setattr(routes, "_service", service)
    monkeypatch.setattr(routes, "_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(routes, "_history_cache", {"mtime": 0, "size": -1, "items": []})
    monkeypatch.setattr(routes, "_sessions", {})
    if not persisted:
        monkeypatch.setattr(routes, "_write_history", Mock(return_value=False))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_user] = lambda: {"username": "alice"}
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/report-workbench/sessions/synthetic-report/generate", params={"input_text": "突出"})
            assert response.status_code == 200
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert not any(event["type"] == "error" for event in events), events
            report = next(event["session"] for event in events if event["type"] == "report_updated")
            assert report["template_reference"]["matched_keywords"] == ["突出"]
            result = await client.get("/api/report-workbench/admin/templates")
            template = result.json()["templates"][0]
            assert template["injection_count"] == 1
            assert template["success_count"] == int(persisted)
            assert template["legacy_usage_count"] == 7
            detail = await client.get("/api/report-workbench/sessions/synthetic-report")
            assert detail.json()["report"]["template_reference"] == report["template_reference"]
    asyncio.run(run())
    if persisted:
        history = json.loads(routes._HISTORY_FILE.read_text())
        assert history[0]["messages"][-1]["template_reference"] == history[0]["template_reference"]


def test_stats_failure_does_not_break_report_generation(service, monkeypatch):
    monkeypatch.setattr(usage, "record_injection", Mock(side_effect=OSError("database unavailable")))
    monkeypatch.setattr(usage, "mark_success", Mock(side_effect=OSError("database unavailable")))
    async def run():
        session, _ = await generate(service)
        assert session.template_reference["template_id"] == "t1"
        await routes._complete_template_reference(session, "alice")
    asyncio.run(run())


def test_template_management_remains_available_when_statistics_fail(service, monkeypatch):
    monkeypatch.setattr(usage, "annotate", Mock(side_effect=OSError("database unavailable")))
    response = asyncio.run(routes.admin_list_templates({"username": "alice"}))
    assert response["templates"][0]["stats_available"] is False
    assert response["templates"][0]["success_count"] is None
