import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from rag.metadata_navigator import MetadataNavigator
from rag.query_planner import build_query_plan
from rag.query_rewrite import rewrite_query
from config import normalize_openai_base_url
from rag.retrieval import (
    build_recall_channels,
    retrieve_with_fallback,
)


TREE = {
    "检查类型": {
        "CT": {
            "一级目录": {
                "头颅": {
                    "三级目录": {
                        "颅脑平扫": {},
                    },
                    "二级目录": {
                        "头颅": {},
                    },
                }
            }
        }
    }
}


class FakeMilvusClient:
    def __init__(self):
        self.search_count = 0
        self.last_request_count = 0

    def get_load_state(self, collection_name, timeout=None):
        return {"state": "Loaded"}

    def hybrid_search(self, **kwargs):
        self.search_count += 1
        self.last_request_count = len(kwargs.get("reqs", []))
        hits = []
        for index, entity_id in enumerate((11, 12, 12, 13)):
            hits.append({
                "id": entity_id,
                "distance": 1 / (index + 1),
                "entity": {
                    "id": entity_id,
                    "modality": "CT",
                    "level_1": "头颅",
                    "level_2": "",
                    "level_3": "颅脑平扫",
                    "level_4": "",
                    "module_name": f"诊断{entity_id}",
                    "imaging_findings": "",
                    "imaging_opinion": "",
                    "combined_text": f"报告{entity_id}",
                },
            })
        return [hits]


class RagPlanningTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            encoding="utf-8",
            delete=False,
        )
        json.dump(TREE, temp, ensure_ascii=False)
        temp.close()
        self.metadata_path = Path(temp.name)
        self.navigator = MetadataNavigator(str(self.metadata_path))

    def tearDown(self):
        self.metadata_path.unlink(missing_ok=True)

    def test_planner_selects_one_valid_path(self):
        plan = build_query_plan(
            "CT 头颅 颅脑平扫 脑出血",
            navigator=self.navigator,
            use_llm=False,
        )
        self.assertEqual(
            plan["filters"],
            {
                "modality": "CT",
                "level_1": "头颅",
                "level_3": "颅脑平扫",
            },
        )
        self.assertEqual(plan["keyword_query"], "脑出血")

    def test_duplicate_level_value_is_not_reused(self):
        plan = build_query_plan(
            "CT 头颅",
            navigator=self.navigator,
            use_llm=False,
        )
        self.assertEqual(plan["filters"], {"modality": "CT", "level_1": "头颅"})

    def test_parallel_recall_channels(self):
        plan = {
            "filters": {
                "modality": "CT",
                "level_1": "头颅",
                "level_3": "颅脑平扫",
            }
        }
        self.assertEqual(
            build_recall_channels(plan),
            [
                {
                    "name": "strict",
                    "filters": {
                        "modality": "CT",
                        "level_1": "头颅",
                        "level_3": "颅脑平扫",
                    },
                },
                {"name": "modality", "filters": {"modality": "CT"}},
                {"name": "kb_only", "filters": {}},
            ],
        )

    def test_retrieval_fuses_all_channels_and_deduplicates(self):
        client = FakeMilvusClient()
        plan = {
            "query": "CT头颅脑出血",
            "semantic_query": "CT头颅脑出血",
            "keyword_query": "脑出血",
            "filters": {
                "modality": "CT",
                "level_1": "头颅",
                "level_3": "颅脑平扫",
            },
        }
        candidates, details = retrieve_with_fallback(
            plan,
            query_vector=[0.0] * 1024,
            top_k=10,
            client=client,
            min_candidates=3,
            selected_kbs=["kb_1"],
        )
        self.assertEqual(client.search_count, 1)
        self.assertEqual(client.last_request_count, 6)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(
            [channel["channel"] for channel in details["channels"]],
            ["strict", "modality", "kb_only"],
        )
        self.assertTrue(all("kb_1" in item["filter_expr"] for item in details["channels"]))

    def test_rewrite_url_uses_base_url_semantics(self):
        self.assertEqual(
            normalize_openai_base_url("http://localhost:8001/v1"),
            "http://localhost:8001/v1",
        )
        self.assertEqual(
            normalize_openai_base_url(
                "http://localhost:8001/v1/chat/completions"
            ),
            "http://localhost:8001/v1",
        )

    @patch("rag.query_rewrite.create_openai_client", side_effect=RuntimeError("offline"))
    def test_rewrite_failure_logs_warning_and_falls_back(self, _mock_client):
        with self.assertLogs("rag.query_rewrite", level="WARNING") as captured:
            result = rewrite_query(
                "CT头颅",
                chat_url="http://localhost:8001/v1",
                chat_model="test-model",
            )
        self.assertEqual(result, "CT 头颅")
        self.assertIn("已降级使用标准化原 Query", captured.output[0])


if __name__ == "__main__":
    unittest.main()
