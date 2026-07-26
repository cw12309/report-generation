import json
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from rag.metadata_navigator import MetadataNavigator
from rag.query_planner import build_query_plan
from rag.retrieval import build_fallback_filters, retrieve_with_fallback


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

    def get_load_state(self, collection_name, timeout=None):
        return {"state": "Loaded"}

    def hybrid_search(self, **kwargs):
        self.search_count += 1
        count = 1 if self.search_count == 1 else 3
        hits = []
        for index in range(count):
            entity_id = self.search_count * 10 + index
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

    def test_fallback_removes_deepest_levels_first(self):
        plan = {
            "filters": {
                "modality": "CT",
                "level_1": "头颅",
                "level_3": "颅脑平扫",
            }
        }
        self.assertEqual(
            build_fallback_filters(plan),
            [
                {"modality": "CT", "level_1": "头颅", "level_3": "颅脑平扫"},
                {"modality": "CT", "level_1": "头颅"},
                {"modality": "CT"},
            ],
        )

    def test_retrieval_falls_back_when_candidates_are_insufficient(self):
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
        )
        self.assertEqual(client.search_count, 2)
        self.assertEqual(len(candidates), 4)
        self.assertEqual(len(details["stages"]), 2)


if __name__ == "__main__":
    unittest.main()
