"""A/B Evaluation Framework 单元测试

不调用真实 LLM / Milvus / RAG。
通过 mock 验证 eval 框架逻辑正确性。
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

# stub langsmith
try:
    import langsmith  # noqa: F401
except ModuleNotFoundError:
    langsmith_stub = types.ModuleType("langsmith")

    def _traceable(*_args, **_kwargs):
        def decorator(func):
            return func
        return decorator

    langsmith_stub.traceable = _traceable
    langsmith_stub.wrappers = types.SimpleNamespace(wrap_openai=lambda client: client)
    run_helpers_stub = types.ModuleType("langsmith.run_helpers")
    run_helpers_stub.get_current_run_tree = lambda: None
    sys.modules["langsmith"] = langsmith_stub
    sys.modules["langsmith.run_helpers"] = run_helpers_stub

# stub openai
try:
    import openai  # noqa: F401
except ModuleNotFoundError:
    openai_stub = types.ModuleType("openai")

    class _DummyClient:
        def __init__(self, *_args, **_kwargs):
            pass

    class _DummyOpenAIError(Exception):
        pass

    openai_stub.AsyncOpenAI = _DummyClient
    openai_stub.OpenAI = _DummyClient
    openai_stub.APIConnectionError = _DummyOpenAIError
    openai_stub.APITimeoutError = _DummyOpenAIError
    openai_stub.APIStatusError = _DummyOpenAIError
    sys.modules["openai"] = openai_stub

from eval.run_ab_eval import (
    run_single_case,
    _generate_with_fixed_rag,
    _resolve_entity_tracker,
    _generate_review_md,
    _hash_content,
)
from eval.metrics import compute_metrics, compute_summary


BASE_REPORT = {
    "results": [
        {
            "影像学表现": "腰椎生理曲度存在，L4/5椎间盘T2WI信号减低，向后突出约3mm。",
            "诊断意见": "L4/5椎间盘突出。",
        }
    ]
}

BASE_REPORT_JSON = json.dumps(BASE_REPORT, ensure_ascii=False)

LUMBAR_CASE = {
    "id": "lumbar_001",
    "input": "MR 腰椎 腰椎间盘突出",
    "expected": {
        "modality": "MR",
        "body_part": ["腰椎"],
        "expected_rule_id": "mr_lumbar_spine",
    },
    "tags": ["lumbar", "disc"],
}


# =============================================================================
# Helpers
# =============================================================================
def _mock_chat_fn(output):
    async def fn(messages, **kwargs):
        return output
    return fn


def _mock_rag_fn():
    def fn(query):
        return (
            "### 参考1\nL4/5椎间盘突出，硬膜囊受压。",
            [{"text": "L4/5椎间盘突出。", "_rerank_score": 0.95}],
        )
    return fn


# =============================================================================
# Test: _generate_with_fixed_rag
# =============================================================================
class TestGenerateWithFixedRag:
    def test_generates_valid_report(self):
        chat_fn = _mock_chat_fn(BASE_REPORT_JSON)
        result = asyncio.run(_generate_with_fixed_rag(
            sys_prompt="system prompt",
            rag_result="reference docs",
            chat_fn=chat_fn,
        ))
        assert result["error"] is None
        assert result["report"] == BASE_REPORT
        assert result["system_prompt_chars"] == len("system prompt")

    def test_handles_parse_error(self):
        chat_fn = _mock_chat_fn("not valid json at all")
        result = asyncio.run(_generate_with_fixed_rag(
            sys_prompt="system prompt",
            rag_result="reference docs",
            chat_fn=chat_fn,
        ))
        assert result["error"] is not None
        assert result["report"] is None

    def test_handles_llm_exception(self):
        async def failing_chat_fn(messages, **kwargs):
            raise RuntimeError("LLM crashed")

        result = asyncio.run(_generate_with_fixed_rag(
            sys_prompt="system prompt",
            rag_result="reference docs",
            chat_fn=failing_chat_fn,
        ))
        assert result["error"] is not None
        assert "RuntimeError" in result["error"]
        assert result["report"] is None


# =============================================================================
# Test: run_single_case
# =============================================================================
class TestRunSingleCase:
    def test_rag_called_once(self, monkeypatch):
        """Case 1: 一个 Case 只调用一次 RAG"""
        rag_call_count = []

        def counting_rag_fn(query):
            rag_call_count.append(query)
            return (
                "### 参考1\nL4/5椎间盘突出。",
                [{"text": "L4/5椎间盘突出。", "_rerank_score": 0.95}],
            )

        chat_fn = _mock_chat_fn(BASE_REPORT_JSON)

        result = asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=counting_rag_fn,
        ))

        assert len(rag_call_count) == 1, f"RAG should be called once, was called {len(rag_call_count)} times"

    def test_baseline_and_rule_share_rag(self, monkeypatch):
        """Case 2: Baseline 和 Rule Enabled 使用同一份 RAG 数据"""
        captured_rag_results = []

        def rag_fn(query):
            result = (
                "### 参考1\nspecific reference text",
                [{"text": "specific reference text", "_rerank_score": 0.95}],
            )
            captured_rag_results.append(result)
            return result

        chat_fn = _mock_chat_fn(BASE_REPORT_JSON)

        asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=rag_fn,
        ))

        assert len(captured_rag_results) == 1

    def test_baseline_no_body_part_rules(self, monkeypatch):
        """Case 3: Baseline 不包含动态 Body-Part Rules"""
        captured_prompts = []

        async def chat_fn(messages, **kwargs):
            captured_prompts.append(messages[0]["content"])
            return BASE_REPORT_JSON

        asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=_mock_rag_fn(),
        ))

        # 第一个调用是 baseline
        baseline_prompt = captured_prompts[0]
        assert "## 部位特化报告标准" not in baseline_prompt

    def test_rule_enabled_has_body_part_rules(self, monkeypatch):
        """Case 4: Rule Enabled 包含 Body-Part Rules"""
        captured_prompts = []

        async def chat_fn(messages, **kwargs):
            captured_prompts.append(messages[0]["content"])
            return BASE_REPORT_JSON

        asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=_mock_rag_fn(),
        ))

        # 第二个调用是 rule_enabled
        rule_prompt = captured_prompts[1]
        assert "## 部位特化报告标准" in rule_prompt

    def test_expected_rule_matched(self, monkeypatch):
        """Case 5: expected_rule_matched 正确"""
        chat_fn = _mock_chat_fn(BASE_REPORT_JSON)

        result = asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=_mock_rag_fn(),
        ))

        assert result["metrics"]["expected_rule_matched"] is True

    def test_wrong_rule_detected(self, monkeypatch):
        """Case 6: wrong rule 可被 metrics 检测"""
        case_with_wrong_rule = {
            **LUMBAR_CASE,
            "expected": {
                "modality": "MR",
                "body_part": ["腰椎"],
                "expected_rule_id": "mr_cervical_spine",
            },
        }

        chat_fn = _mock_chat_fn(BASE_REPORT_JSON)

        result = asyncio.run(run_single_case(
            case=case_with_wrong_rule,
            chat_fn=chat_fn,
            rag_fn=_mock_rag_fn(),
        ))

        assert result["metrics"]["expected_rule_matched"] is False
        assert result["metrics"]["wrong_rule_matched"] is True

    def test_variant_failure_preserves_case(self, monkeypatch):
        """Case 7: 一个 variant 失败不会导致整个 case 丢失"""

        async def chat_fn(messages, **kwargs):
            caller = kwargs.get("caller", "")
            if "baseline" in caller:
                raise RuntimeError("baseline crash")
            return BASE_REPORT_JSON

        result = asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=_mock_rag_fn(),
        ))

        assert result["case_id"] == "lumbar_001"
        assert result["baseline"]["error"] is not None
        assert result["rule_enabled"]["error"] is None
        assert "metrics" in result

    def test_case_failure_does_not_stop_run(self, monkeypatch):
        """Case 8: 一个 case 失败不会导致 run 中断"""

        async def chat_fn(messages, **kwargs):
            raise RuntimeError("total crash")

        result = asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=_mock_rag_fn(),
        ))

        assert result["case_id"] == "lumbar_001"
        assert result["baseline"]["error"] is not None
        assert result["rule_enabled"]["error"] is not None
        assert "RuntimeError" in result["baseline"]["error"]

    def test_review_md_generated(self, monkeypatch):
        """Case 9: review.md 正常生成"""
        chat_fn = _mock_chat_fn(BASE_REPORT_JSON)

        result = asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=_mock_rag_fn(),
        ))

        review = _generate_review_md("test_run", [result])
        assert "lumbar_001" in review
        assert "Baseline" in review
        assert "Rule Enabled" in review
        assert "Human Review" in review
        assert "专业性" in review

    def test_json_output_serializable(self, monkeypatch):
        """Case 10: JSON output 可序列化"""
        chat_fn = _mock_chat_fn(BASE_REPORT_JSON)

        result = asyncio.run(run_single_case(
            case=LUMBAR_CASE,
            chat_fn=chat_fn,
            rag_fn=_mock_rag_fn(),
        ))

        serialized = json.dumps(result, ensure_ascii=False)
        deserialized = json.loads(serialized)
        assert deserialized["case_id"] == "lumbar_001"


# =============================================================================
# Test: _resolve_entity_tracker
# =============================================================================
class TestResolveEntityTracker:
    def test_resolves_modality_and_body_part(self):
        tracker = _resolve_entity_tracker(LUMBAR_CASE)
        assert tracker.slots["modality"] == "MR"
        assert tracker.slots["body_part"] == ["腰椎"]

    def test_resolves_partial_case(self):
        case = {
            "id": "test",
            "input": "test",
            "expected": {},
        }
        tracker = _resolve_entity_tracker(case)
        assert tracker.slots["modality"] is None
        assert tracker.slots["body_part"] == []


# =============================================================================
# Test: metrics
# =============================================================================
class TestMetrics:
    def test_compute_metrics_both_success(self):
        metrics = compute_metrics(
            case_id="test",
            expected={"expected_rule_id": "mr_lumbar_spine"},
            rules_info={"matched_rule_ids": ["mr_lumbar_spine"]},
            baseline={"report": BASE_REPORT, "error": None, "system_prompt_chars": 100},
            rule_enabled={"report": BASE_REPORT, "error": None, "system_prompt_chars": 200},
        )
        assert metrics["baseline_success"] is True
        assert metrics["rule_enabled_success"] is True
        assert metrics["baseline_json_valid"] is True
        assert metrics["rule_enabled_json_valid"] is True
        assert metrics["expected_rule_matched"] is True
        assert metrics["wrong_rule_matched"] is False

    def test_compute_metrics_baseline_failure(self):
        metrics = compute_metrics(
            case_id="test",
            expected={"expected_rule_id": "mr_lumbar_spine"},
            rules_info={"matched_rule_ids": []},
            baseline={"report": None, "error": "crash", "system_prompt_chars": 100},
            rule_enabled={"report": BASE_REPORT, "error": None, "system_prompt_chars": 200},
        )
        assert metrics["baseline_success"] is False
        assert metrics["rule_enabled_success"] is True
        assert metrics["baseline_json_valid"] is False

    def test_compute_metrics_wrong_rule(self):
        metrics = compute_metrics(
            case_id="test",
            expected={"expected_rule_id": "mr_lumbar_spine"},
            rules_info={"matched_rule_ids": ["mr_cervical_spine"]},
            baseline={"report": BASE_REPORT, "error": None, "system_prompt_chars": 100},
            rule_enabled={"report": BASE_REPORT, "error": None, "system_prompt_chars": 200},
        )
        assert metrics["expected_rule_matched"] is False
        assert metrics["wrong_rule_matched"] is True

    def test_needs_fact_review_detection(self):
        baseline_report = {"results": [{"影像学表现": "椎间盘突出。", "诊断意见": "椎间盘突出。"}]}
        rule_report = {"results": [{"影像学表现": "椎间盘突出，Pfirrmann II级。", "诊断意见": "椎间盘突出。"}]}

        metrics = compute_metrics(
            case_id="test",
            expected={},
            rules_info={"matched_rule_ids": []},
            baseline={"report": baseline_report, "error": None, "system_prompt_chars": 100},
            rule_enabled={"report": rule_report, "error": None, "system_prompt_chars": 200},
        )
        assert metrics["needs_fact_review"] is True

    def test_compute_summary(self):
        metrics_list = [
            {"baseline_success": True, "rule_enabled_success": True,
             "expected_rule_matched": True, "wrong_rule_matched": False,
             "needs_fact_review": False,
             "baseline_output_chars": 100, "rule_enabled_output_chars": 120,
             "baseline_prompt_chars": 500, "rule_enabled_prompt_chars": 600},
            {"baseline_success": True, "rule_enabled_success": True,
             "expected_rule_matched": True, "wrong_rule_matched": False,
             "needs_fact_review": True,
             "baseline_output_chars": 200, "rule_enabled_output_chars": 250,
             "baseline_prompt_chars": 500, "rule_enabled_prompt_chars": 600},
        ]

        summary = compute_summary(metrics_list)
        assert summary["cases"] == 2
        assert summary["baseline_success"] == "2/2"
        assert summary["needs_fact_review"] == "1/2"
        assert summary["avg_baseline_output_chars"] == 150
        assert summary["avg_rule_enabled_output_chars"] == 185


# =============================================================================
# Test: _hash_content
# =============================================================================
def test_hash_content():
    h = _hash_content("hello")
    assert len(h) == 16
    assert _hash_content("hello") == _hash_content("hello")
    assert _hash_content("hello") != _hash_content("world")