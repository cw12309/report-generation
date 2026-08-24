"""rag_tool + StructureRulesLoader 接线测试 (Phase 4)

独立测试，不依赖 pymilvus / Milvus / 真实 LLM。
"""

from __future__ import annotations

import asyncio
import hashlib
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

from chat.report_schema import ReportSchemaError
from memory.entity_tracker import EntityTracker
from tools.rag_tool import create_rag_search_handler, _resolve_rule_body_parts


BASE_REPORT = {
    "results": [
        {
            "影像学表现": "CT平扫显示右侧基底节区团块状高密度灶，CT值65HU。",
            "诊断意见": "右侧基底节区脑出血。",
        }
    ]
}


class _FakeStructureRulesResult:
    """模拟 StructureRulesLoader.get_rules() 返回的 StructureRulesResult"""
    def __init__(self, content="", matched_ids=None, matched_files=None,
                 unmatched=None, normalized_modality="MR", normalized_body_parts=None):
        self.content = content
        self.normalized_modality = normalized_modality
        self.matched_rule_ids = matched_ids or []
        self.matched_rule_files = matched_files or []
        self.unmatched_body_parts = unmatched or []
        self.normalized_body_parts = normalized_body_parts or []


LUMBAR_RULE_CONTENT = "## 腰椎MRI标准化报告模板\n### 椎间盘评估\n应按Pfirrmann分级描述椎间盘退变程度。"
LUMBAR_RULE_MARKER = "腰椎MRI标准化报告模板"
THORACIC_RULE_CONTENT = "## 胸椎MRI标准化报告模板\n### 椎体评估\n应描述椎体形态及信号。"
THORACIC_RULE_MARKER = "胸椎MRI标准化报告模板"


def _make_rag_handler(entity_tracker=None, search_result=None):
    """创建标准 rag_search handler 用于测试"""
    calls_sys_prompt = []

    async def chat_fn(messages, **kwargs):
        calls_sys_prompt.append(messages[0]["content"])
        return json.dumps(BASE_REPORT, ensure_ascii=False)

    if entity_tracker is None:
        entity_tracker = EntityTracker()

    handler = create_rag_search_handler(
        chat_fn=chat_fn,
        entity_tracker=entity_tracker,
        get_embedding_fn=lambda _text: [],
        search_reports_fn=lambda _query, _emit=None: (
            search_result or "### 参考1\n右侧病灶。诊断脑出血。",
            [{"text": "右侧病灶。", "_rerank_score": 0.9}],
        ),
        last_report=[""],
    )
    return handler, calls_sys_prompt


# =============================================================================
# Case 1: MR + 腰椎 → rules 注入
# =============================================================================
def test_lumbar_rules_injected_when_matched(monkeypatch):
    fake_result = _FakeStructureRulesResult(
        content=LUMBAR_RULE_CONTENT,
        matched_ids=["mr_lumbar"],
        matched_files=["腰椎MRI标准化报告模板.md"],
        normalized_body_parts=["腰椎"],
    )

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["腰椎"]

    handler, calls_sys_prompt = _make_rag_handler(entity_tracker=tracker)
    result = json.loads(asyncio.run(handler({"query": "MR 腰椎 腰椎间盘突出"})))

    assert result["_is_final"] is True
    sys_prompt = calls_sys_prompt[0]
    assert "## 部位特化报告标准" in sys_prompt
    assert LUMBAR_RULE_MARKER in sys_prompt


# =============================================================================
# Case 2: factuality 在 rules 前
# =============================================================================
def test_factuality_before_body_part_rules(monkeypatch):
    fake_result = _FakeStructureRulesResult(
        content=LUMBAR_RULE_CONTENT,
        matched_ids=["mr_lumbar"],
        normalized_body_parts=["腰椎"],
    )

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["腰椎"]

    handler, calls_sys_prompt = _make_rag_handler(entity_tracker=tracker)
    asyncio.run(handler({"query": "MR 腰椎"}))

    sys_prompt = calls_sys_prompt[0]
    pos_factuality = sys_prompt.index("事实性硬约束")
    pos_rules = sys_prompt.index(LUMBAR_RULE_MARKER)
    assert pos_factuality < pos_rules, (
        f"事实性硬约束应在部位规则之前，"
        f"但事实性硬约束在 {pos_factuality}，腰椎规则在 {pos_rules}"
    )


# =============================================================================
# Case 3: 未配置部位 → 正常降级
# =============================================================================
def test_unmatched_body_part_safe_degradation(monkeypatch):
    fake_result = _FakeStructureRulesResult(
        content="",
        unmatched=["骶髂关节"],
    )

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["骶髂关节"]

    handler, calls_sys_prompt = _make_rag_handler(entity_tracker=tracker)
    result = json.loads(asyncio.run(handler({"query": "MR 骶髂关节"})))

    assert result["_is_final"] is True
    sys_prompt = calls_sys_prompt[0]
    assert "## 部位特化报告标准" not in sys_prompt


# =============================================================================
# Case 4: CT + 腰椎 → 不使用 MR rules
# =============================================================================
def test_ct_does_not_leak_mr_rules(monkeypatch):
    fake_result = _FakeStructureRulesResult(
        content="",
        unmatched=["腰椎"],
    )

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "CT"
    tracker.slots["body_part"] = ["腰椎"]

    handler, calls_sys_prompt = _make_rag_handler(entity_tracker=tracker)
    result = json.loads(asyncio.run(handler({"query": "CT 腰椎", "modality": "CT", "body_part": "腰椎"})))

    assert result["_is_final"] is True
    sys_prompt = calls_sys_prompt[0]
    assert LUMBAR_RULE_MARKER not in sys_prompt


# =============================================================================
# Case 5: MRI + 脑部 → alias 命中颅脑
# =============================================================================
def test_alias_routing_brain_to_颅脑(monkeypatch):
    BRAIN_RULE = "## 颅脑MRI标准化报告模板\n### 脑实质评估"
    fake_result = _FakeStructureRulesResult(
        content=BRAIN_RULE,
        matched_ids=["mr_brain"],
        matched_files=["颅脑MRI标准化报告模板.md"],
        normalized_body_parts=["颅脑"],
    )

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "MRI"
    tracker.slots["body_part"] = ["脑部"]

    handler, calls_sys_prompt = _make_rag_handler(entity_tracker=tracker)
    asyncio.run(handler({"query": "MRI 脑部", "modality": "MRI", "body_part": "脑部"}))

    sys_prompt = calls_sys_prompt[0]
    assert "颅脑MRI标准化报告模板" in sys_prompt


# =============================================================================
# Case 6: ["腰椎","胸椎"] → Loader 收到两个元素
# =============================================================================
def test_multi_body_parts_both_injected(monkeypatch):
    combined = f"{LUMBAR_RULE_CONTENT}\n\n---\n\n{THORACIC_RULE_CONTENT}"
    fake_result = _FakeStructureRulesResult(
        content=combined,
        matched_ids=["mr_lumbar", "mr_thoracic"],
        matched_files=["腰椎MRI标准化报告模板.md", "胸椎MRI标准化报告模板.md"],
        normalized_body_parts=["腰椎", "胸椎"],
    )

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["腰椎", "胸椎"]

    handler, calls_sys_prompt = _make_rag_handler(entity_tracker=tracker)
    asyncio.run(handler({"query": "MR 腰椎 胸椎"}))

    sys_prompt = calls_sys_prompt[0]
    assert LUMBAR_RULE_MARKER in sys_prompt
    assert THORACIC_RULE_MARKER in sys_prompt
    assert sys_prompt.index(LUMBAR_RULE_MARKER) < sys_prompt.index(THORACIC_RULE_MARKER)


# =============================================================================
# Case 7: EntityTracker body_part list 不被错误 join
# =============================================================================
def test_entity_tracker_list_not_joined(monkeypatch):
    captured_body_parts = []

    def fake_get_rules(modality, body_parts):
        captured_body_parts.append(body_parts)
        return _FakeStructureRulesResult(content="")

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        fake_get_rules,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["腰椎", "胸椎"]

    handler, _calls = _make_rag_handler(entity_tracker=tracker)
    asyncio.run(handler({"query": "MR 腰椎 胸椎"}))

    assert captured_body_parts, "get_rules 未被调用"
    body_parts = captured_body_parts[0]
    assert isinstance(body_parts, list)
    assert "腰椎" in body_parts
    assert "胸椎" in body_parts
    assert "腰椎 胸椎" not in body_parts


# =============================================================================
# Case 8: content="" → 不生成动态 rules section
# =============================================================================
def test_empty_rules_content_passed_as_none(monkeypatch):
    fake_result = _FakeStructureRulesResult(
        content="",
        unmatched=["骶髂关节"],
    )

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["骶髂关节"]

    handler, calls_sys_prompt = _make_rag_handler(entity_tracker=tracker)
    asyncio.run(handler({"query": "MR 骶髂关节"}))

    sys_prompt = calls_sys_prompt[0]
    assert "## 部位特化报告标准" not in sys_prompt


# =============================================================================
# Case 9: Loader 配置异常 → 系统错误路径
# =============================================================================
def test_loader_config_error_goes_to_system_error_path(monkeypatch):
    def failing_get_rules(modality, body_parts):
        raise ValueError("manifest 损坏")

    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        failing_get_rules,
    )

    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["腰椎"]

    handler, _calls = _make_rag_handler(entity_tracker=tracker)
    result = json.loads(asyncio.run(handler({"query": "MR 腰椎"})))

    assert result["_is_final"] is False
    assert "报告生成失败" in result["error"]


# =============================================================================
# _resolve_rule_body_parts helper 测试
# =============================================================================
def test_resolve_rule_body_parts_from_entity_tracker():
    tracker = EntityTracker()
    tracker.slots["body_part"] = ["腰椎", "颈椎"]
    result = _resolve_rule_body_parts(tracker, "头颅")
    assert result == ["腰椎", "颈椎"]


def test_resolve_rule_body_parts_from_fallback():
    result = _resolve_rule_body_parts(None, "膝关节")
    assert result == ["膝关节"]


def test_resolve_rule_body_parts_empty():
    result = _resolve_rule_body_parts(None, None)
    assert result == []


def test_resolve_rule_body_parts_entity_tracker_priority():
    tracker = EntityTracker()
    tracker.slots["body_part"] = ["腰椎"]
    result = _resolve_rule_body_parts(tracker, "头颅")
    assert result == ["腰椎"]


def test_resolve_rule_body_parts_none_entity_tracker_empty_list():
    tracker = EntityTracker()
    tracker.slots["body_part"] = []
    result = _resolve_rule_body_parts(tracker, "膝关节")
    assert result == ["膝关节"]


# =============================================================================
# Prompt Trace / Prompt Debug
# =============================================================================
def _prompt_trace_events(events):
    return [data for event_type, data in events if event_type == "prompt_trace"]


def test_prompt_trace_disabled_emits_nothing(monkeypatch):
    monkeypatch.setenv("PROMPT_TRACE_ENABLED", "false")
    monkeypatch.setenv("PROMPT_TRACE_FULL", "true")
    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: _FakeStructureRulesResult(content=""),
    )
    events = []

    async def chat_fn(messages, **_kwargs):
        return json.dumps(BASE_REPORT, ensure_ascii=False)

    handler = create_rag_search_handler(
        chat_fn=chat_fn,
        entity_tracker=EntityTracker(),
        get_embedding_fn=lambda _text: [],
        search_reports_fn=lambda _query, _emit=None: (
            "### 参考1\n右侧病灶。",
            [{"text": "右侧病灶。", "_rerank_score": 0.9}],
        ),
        _emit_fn=lambda event_type, data: events.append((event_type, data)),
        last_report=[""],
    )

    result = json.loads(asyncio.run(handler({"query": "MR 乳腺"})))

    assert result["_is_final"] is True
    assert _prompt_trace_events(events) == []


def test_prompt_trace_metadata_for_mr_breast(monkeypatch):
    monkeypatch.setenv("PROMPT_TRACE_ENABLED", "true")
    monkeypatch.setenv("PROMPT_TRACE_FULL", "false")
    rule_content = "# Body-Part Rule: 乳腺\n\n## 乳腺MRI标准化报告模板"
    fake_result = _FakeStructureRulesResult(
        content=rule_content,
        matched_ids=["mr_breast"],
        matched_files=["乳腺MRI标准化报告模板.md"],
        normalized_body_parts=["乳腺"],
    )
    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )
    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["乳腺"]
    events = []

    async def chat_fn(messages, **_kwargs):
        return json.dumps(BASE_REPORT, ensure_ascii=False)

    handler = create_rag_search_handler(
        chat_fn=chat_fn,
        entity_tracker=tracker,
        get_embedding_fn=lambda _text: [],
        search_reports_fn=lambda _query, _emit=None: (
            "### 参考1\n乳腺病灶。",
            [{"text": "乳腺病灶。", "_rerank_score": 0.9}],
        ),
        _emit_fn=lambda event_type, data: events.append((event_type, data)),
        last_report=[""],
    )

    asyncio.run(handler({"query": "MR 乳腺", "modality": "MR", "body_part": "乳腺"}))
    trace = _prompt_trace_events(events)[0]

    assert trace["stage"] == "report_generation"
    assert trace["context"] == {
        "modality": "MR",
        "body_parts": ["乳腺"],
        "diagnosis": [],
    }
    assert trace["rag"]["result_count"] == 1
    assert trace["rules"]["matched_rule_ids"] == ["mr_breast"]
    assert trace["rules"]["matched_rule_files"] == ["乳腺MRI标准化报告模板.md"]
    assert trace["rules"]["injected"] is True
    assert trace["rules"]["content_hash"] == hashlib.sha256(
        rule_content.encode("utf-8")
    ).hexdigest()[:16]
    assert trace["prompt"]["has_body_part_rules"] is True
    assert "system_prompt" not in trace["prompt"]
    assert "user_message" not in trace["prompt"]


def test_prompt_trace_full_uses_exact_messages_and_precedes_llm(monkeypatch):
    monkeypatch.setenv("PROMPT_TRACE_ENABLED", "true")
    monkeypatch.setenv("PROMPT_TRACE_FULL", "true")
    fake_result = _FakeStructureRulesResult(
        content=LUMBAR_RULE_CONTENT,
        matched_ids=["mr_lumbar"],
        matched_files=["腰椎MRI标准化报告模板.md"],
        normalized_body_parts=["腰椎"],
    )
    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )
    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["腰椎"]
    events = []
    order = []
    llm_messages = []

    def emit(event_type, data):
        events.append((event_type, data))
        if event_type == "prompt_trace":
            order.append("prompt_trace")

    async def chat_fn(messages, **_kwargs):
        order.append("chat_fn")
        llm_messages.extend(messages)
        return json.dumps(BASE_REPORT, ensure_ascii=False)

    handler = create_rag_search_handler(
        chat_fn=chat_fn,
        entity_tracker=tracker,
        get_embedding_fn=lambda _text: [],
        search_reports_fn=lambda _query, _emit=None: (
            "### 参考1\n腰椎病灶。",
            [{"text": "腰椎病灶。", "_rerank_score": 0.9}],
        ),
        _emit_fn=emit,
        last_report=[""],
    )

    result = json.loads(asyncio.run(handler({"query": "MR 腰椎"})))
    trace = _prompt_trace_events(events)[0]

    assert order == ["prompt_trace", "chat_fn"]
    assert trace["prompt"]["system_prompt"] == llm_messages[0]["content"]
    assert trace["prompt"]["user_message"] == llm_messages[1]["content"]
    assert trace["prompt"]["system_prompt_chars"] == len(llm_messages[0]["content"])
    assert trace["prompt"]["user_message_chars"] == len(llm_messages[1]["content"])
    assert result == {**BASE_REPORT, "_is_final": True}


def test_prompt_trace_unmatched_rules_still_emits(monkeypatch):
    monkeypatch.setenv("PROMPT_TRACE_ENABLED", "true")
    monkeypatch.setenv("PROMPT_TRACE_FULL", "false")
    fake_result = _FakeStructureRulesResult(
        content="",
        unmatched=["骶髂关节"],
        normalized_body_parts=[],
    )
    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: fake_result,
    )
    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["骶髂关节"]
    events = []

    async def chat_fn(messages, **_kwargs):
        return json.dumps(BASE_REPORT, ensure_ascii=False)

    handler = create_rag_search_handler(
        chat_fn=chat_fn,
        entity_tracker=tracker,
        get_embedding_fn=lambda _text: [],
        search_reports_fn=lambda _query, _emit=None: (
            "### 参考1\n骶髂关节病灶。",
            [{"text": "骶髂关节病灶。", "_rerank_score": 0.9}],
        ),
        _emit_fn=lambda event_type, data: events.append((event_type, data)),
        last_report=[""],
    )

    asyncio.run(handler({"query": "MR 骶髂关节"}))
    trace = _prompt_trace_events(events)[0]

    assert trace["rules"] == {
        "normalized_modality": "MR",
        "normalized_body_parts": [],
        "matched_rule_ids": [],
        "matched_rule_files": [],
        "unmatched_body_parts": ["骶髂关节"],
        "content_chars": 0,
        "content_hash": None,
        "injected": False,
    }
    assert trace["prompt"]["has_body_part_rules"] is False


def test_real_mr_breast_trace_contains_injected_rule(monkeypatch):
    """Acceptance check against the real manifest/rule file and actual PromptBuilder."""
    monkeypatch.setenv("PROMPT_TRACE_ENABLED", "true")
    monkeypatch.setenv("PROMPT_TRACE_FULL", "true")
    tracker = EntityTracker()
    tracker.slots["modality"] = "MR"
    tracker.slots["body_part"] = ["乳腺"]
    events = []

    async def chat_fn(messages, **_kwargs):
        return json.dumps(BASE_REPORT, ensure_ascii=False)

    handler = create_rag_search_handler(
        chat_fn=chat_fn,
        entity_tracker=tracker,
        get_embedding_fn=lambda _text: [],
        search_reports_fn=lambda _query, _emit=None: (
            "### 参考1\n乳腺增强扫描见病灶。",
            [{"text": "乳腺增强扫描见病灶。", "_rerank_score": 0.9}],
        ),
        _emit_fn=lambda event_type, data: events.append((event_type, data)),
        last_report=[""],
    )

    result = json.loads(asyncio.run(handler({"query": "MR 乳腺"})))
    trace = _prompt_trace_events(events)[0]
    system_prompt = trace["prompt"]["system_prompt"]

    assert result["_is_final"] is True
    assert trace["rules"]["matched_rule_ids"] == ["mr_breast"]
    assert "乳腺MRI标准化报告模板.md" in trace["rules"]["matched_rule_files"]
    assert trace["rules"]["injected"] is True
    assert "## 部位特化报告标准" in system_prompt
    assert "乳腺MRI标准化报告模板" in system_prompt


def test_prompt_trace_failure_does_not_change_report_result(monkeypatch):
    monkeypatch.setenv("PROMPT_TRACE_ENABLED", "true")
    monkeypatch.setenv("PROMPT_TRACE_FULL", "true")
    monkeypatch.setattr(
        "prompt.structure_rules_loader.StructureRulesLoader.get_rules",
        lambda modality, body_parts: _FakeStructureRulesResult(content=""),
    )

    def emit(event_type, _data):
        if event_type == "prompt_trace":
            raise RuntimeError("simulated trace transport failure")

    async def chat_fn(messages, **_kwargs):
        return json.dumps(BASE_REPORT, ensure_ascii=False)

    handler = create_rag_search_handler(
        chat_fn=chat_fn,
        entity_tracker=EntityTracker(),
        get_embedding_fn=lambda _text: [],
        search_reports_fn=lambda _query, _emit=None: (
            "### 参考1\n右侧病灶。",
            [{"text": "右侧病灶。", "_rerank_score": 0.9}],
        ),
        _emit_fn=emit,
        last_report=[""],
    )

    result = json.loads(asyncio.run(handler({"query": "CT 头颅"})))

    assert result == {**BASE_REPORT, "_is_final": True}
