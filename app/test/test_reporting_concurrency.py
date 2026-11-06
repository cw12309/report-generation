"""Concurrency regressions: external retrieval must not stall history reads."""
import asyncio
import time
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from app.reporting.service import ReportService
from app.reporting.schemas import GenerateRequest, IterateRequest
from app.reporting import route_admin, kb_reference


class StopAfterRetrieval(Exception):
    pass


class ReportingConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def check_service(self, operation, slow_stage="kb"):
        service = ReportService.__new__(ReportService)
        route = SimpleNamespace(id="test", name="test")
        service.route_resolver = Mock()
        service.route_resolver.resolve.return_value = route
        service.route_resolver.get_route_by_id.return_value = route
        service.prompt_loader = Mock()
        service.prompt_loader.get_system_prompt.return_value = ""
        service.prompt_loader.build_user_prompt.side_effect = StopAfterRetrieval()
        service._template_injection = Mock(return_value=("", None))
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def slow_reference(*args):
            entered.set()
            release.wait(2.0)
            finished.set()
            return ("", None) if slow_stage == "template" else ""
        if slow_stage == "template":
            service._template_injection = slow_reference
            service._kb_style_injection = Mock(return_value="")
        else:
            service._kb_style_injection = slow_reference
        if operation == "generate":
            call = service.generate_report(GenerateRequest(input_text="synthetic"), "test")
        else:
            session = SimpleNamespace(conversation_id="test", route_id="test", question_round=0,
                report=SimpleNamespace(imaging_findings="", imaging_diagnosis=""))
            call = service.iterate_report(current_session=session, request=IterateRequest(input_text="synthetic",base_version=1,question_round=0))
        started = time.monotonic()
        task = asyncio.create_task(call)
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2.0))
            self.assertFalse(finished.is_set(), "retrieval blocked the event loop")
        finally:
            release.set()
            with self.assertRaises(StopAfterRetrieval):
                await task


    async def test_generation_keeps_event_loop_responsive(self):
        await self.check_service("generate")

    async def test_iteration_keeps_event_loop_responsive(self):
        await self.check_service("iterate")

    async def test_generation_template_storage_keeps_event_loop_responsive(self):
        await self.check_service("generate", slow_stage="template")

    async def test_iteration_template_storage_keeps_event_loop_responsive(self):
        await self.check_service("iterate", slow_stage="template")

    async def test_history_http_remains_responsive_during_draft_retrieval(self):
        import httpx
        from fastapi import FastAPI
        from app.reporting import router as routes
        from app.reporting.auth import require_user
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[require_user] = lambda: {"username":"test"}
        history = [{"conversation_id":"test-history","owner":"test","report":{}}]
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def slow_reference(**kwargs):
            entered.set()
            release.wait(2.0)
            finished.set()
            return {"count":0}
        with patch.object(kb_reference,"kb_reference_for_route",side_effect=slow_reference), \
             patch.object(route_admin,"chat_stream",new=AsyncMock(return_value='{"prompt_md":"test"}')), \
             patch.object(routes,"_load_history",return_value=history):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                await client.get("/api/report-workbench/history/test-history")
                started = time.monotonic()
                task = asyncio.create_task(route_admin.generate_route_draft("test",body_part="test"))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2.0))
                response = await client.get("/api/report-workbench/history/test-history")
                responsive = not finished.is_set()
                release.set()
            await task
        self.assertEqual(response.status_code,200)
        self.assertTrue(responsive,"history waited for unrelated retrieval")


if __name__ == "__main__":
    unittest.main()
