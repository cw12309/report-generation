"""Route reference and draft regressions, without external APIs or writes."""
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

from app.reporting import kb_reference as kb


class RouteReferenceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(kb, 'build_query_plan', return_value={'semantic_query': 'test'}))
        self.stack.enter_context(patch.object(kb, 'get_embedding', return_value=[0.0]))
        self.client = Mock()
        self.stack.enter_context(patch.object(kb, 'create_milvus_client', return_value=self.client))
        self.retrieve = self.stack.enter_context(patch.object(kb, 'retrieve_with_fallback', return_value=([], {})))
        self.local = self.stack.enter_context(patch.object(kb, '_local_markdown_reference', return_value=[]))
        self.item = {'title': 'test', 'level_1': '腰椎', 'modality': 'MRI', 'findings': '腰椎模板', 'opinion': '模板意见'}

    def test_empty_results_return_empty_reference(self):
        result = kb.kb_reference_for_route('腰椎MRI', modality='MRI')
        self.assertEqual(result, {'text': '', 'chars': 0, 'count': 0, 'items': []})
        self.local.assert_called_once()
        self.client.close.assert_called_once()

    def test_empty_results_use_local_reference(self):
        self.local.return_value = [self.item]
        result = kb.kb_reference_for_route('腰椎MRI', modality='MRI')
        self.assertEqual(result['count'], 1)
        self.assertIn('腰椎模板', result['text'])

    def test_matches_are_deduplicated_and_other_body_parts_filtered(self):
        hit = {'chunk_id': 'one', 'module_name': '腰椎模板', 'imaging_findings': '腰椎改变', 'imaging_opinion': '意见'}
        other = {'chunk_id': 'two', 'module_name': '乳腺', 'imaging_findings': '乳腺改变'}
        self.retrieve.return_value = ([hit, hit, other], {})
        result = kb.kb_reference_for_route('腰椎MRI', modality='MRI')
        self.assertEqual(result['count'], 1)
        self.assertEqual(result['items'][0]['findings'], '腰椎改变')
        self.local.assert_not_called()

    def test_filtered_results_use_local_reference(self):
        self.retrieve.return_value = ([{'chunk_id': 'one', 'imaging_findings': '乳腺改变'}], {})
        self.local.return_value = [self.item]
        self.assertEqual(kb.kb_reference_for_route('腰椎MRI')['items'], [self.item])

    def test_unavailable_retrieval_uses_local_reference(self):
        self.retrieve.side_effect = RuntimeError('unavailable')
        self.local.return_value = [self.item]
        self.assertEqual(kb.kb_reference_for_route('腰椎MRI')['count'], 1)
        self.client.close.assert_called_once()

    def test_draft_endpoint_returns_json_when_vector_search_is_empty(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.reporting import router as routes, route_admin
        from app.reporting.auth import require_user
        app = FastAPI()
        app.include_router(routes.router)
        app.dependency_overrides[require_user] = lambda: {'username': 'regression-test'}
        with patch.object(route_admin, 'chat_stream', new=AsyncMock(return_value='{"prompt_md":"test draft"}')) as llm:
            response = TestClient(app).post('/api/report-workbench/admin/routes/draft', json={'name': '腰椎MRI', 'body_part': '腰椎', 'modality': 'MRI'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['draft']['prompt_md'], 'test draft')
        self.assertFalse(response.json()['draft']['fallback'])
        llm.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
