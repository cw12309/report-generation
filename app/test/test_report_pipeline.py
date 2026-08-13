"""Deterministic StructuredReport pipeline and tool regression tests."""

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

from chat import pipeline as pipeline_module
from chat.deployments import map_public_event
from chat.report_schema import (
    ReportPreservationError,
    ReportSchemaError,
    parse_report_output,
    parse_report_with_repair,
    serialize_report,
    validate_edit_preservation,
    validate_refine_preservation,
)
from memory.entity_tracker import EntityTracker
from tools.edit_tool import EDIT_REPORT_SCHEMA, create_edit_report_handler
from tools.rag_tool import create_rag_search_handler
from tools.refine_tool import REFINE_REPORT_SCHEMA, create_refine_report_handler
from tools.registry import ToolResult


BASE_REPORT = {
    "results": [
        {
            "影像学表现": (
                "CT平扫显示右侧基底节区团块状高密度灶，CT值65HU，范围约3.0×1.6cm，"
                "边界清楚，病灶周围见低密度环包绕，中线结构轻度向对侧移位。"
            ),
            "诊断意见": "右侧基底节区脑出血。",
        }
    ]
}
VALID_EDIT_JUDGEMENT = json.dumps(
    {"valid": True, "unexpected_changes": []},
    ensure_ascii=False,
)


def _payload(raw: str) -> dict:
    value = json.loads(raw)
    assert value.pop("_is_final") is True
    return value


def test_parse_valid_fenced_and_legacy_reports():
    assert parse_report_output(BASE_REPORT) == BASE_REPORT
    fenced = f"```json\n{json.dumps(BASE_REPORT, ensure_ascii=False)}\n```"
    assert parse_report_output(fenced) == BASE_REPORT

    legacy = {"findings": "右侧病灶。", "impression": "脑出血。"}
    assert parse_report_output(legacy) == {
        "results": [{"影像学表现": "右侧病灶。", "诊断意见": "脑出血。"}]
    }


def test_invalid_reasoning_json_repairs_once_or_errors():
    invalid = '{"reasoning":"选择参考", "2. 提取病变：...", "3. 构建结果：..."}'
    calls = []

    async def successful_repair(messages):
        calls.append(messages)
        return json.dumps(BASE_REPORT, ensure_ascii=False)

    repaired, attempted = asyncio.run(parse_report_with_repair(invalid, successful_repair))
    assert repaired == BASE_REPORT
    assert attempted is True
    assert len(calls) == 1

    async def failed_repair(_messages):
        return "仍然不是 JSON"

    with pytest.raises(ReportSchemaError):
        asyncio.run(parse_report_with_repair(invalid, failed_repair))


def test_report_operation_intent_never_switches_but_exam_switch_does():
    tracker = EntityTracker()
    tracker.slots.update({
        "modality": "CT",
        "body_part": ["头颅"],
        "diagnosis": ["脑出血"],
    })
    report_operations = [
        "把65HU改成70HU",
        "修改上一份报告",
        "把病灶大小改成3.2×1.8cm",
        "润色一下",
        "写得正式一些",
        "写得简洁一些",
        "补充一段描述",
        "删除某句话",
    ]
    assert all(tracker.detect_intent(query) != "switch" for query in report_operations)

    actual_switches = [
        "切换到胸部CT",
        "换成MR检查",
        "现在看腹部",
        "重新看胸部",
        "改看肺部CT",
        "开始一个新的检查",
    ]
    assert all(tracker.detect_intent(query) == "switch" for query in actual_switches)


def test_tool_schemas_do_not_expose_current_report():
    edit_parameters = EDIT_REPORT_SCHEMA["function"]["parameters"]
    refine_parameters = REFINE_REPORT_SCHEMA["function"]["parameters"]
    assert set(edit_parameters["properties"]) == {"instruction"}
    assert set(refine_parameters["properties"]) == {"style"}


def test_rag_tool_valid_and_fenced_outputs_are_final():
    async def scenario(output):
        calls = []

        async def chat_fn(_messages, **kwargs):
            calls.append(kwargs.get("caller"))
            return output

        handler = create_rag_search_handler(
            chat_fn=chat_fn,
            entity_tracker=EntityTracker(),
            get_embedding_fn=lambda _text: [],
            search_reports_fn=lambda _query, _emit=None: (
                "### 参考1\n右侧病灶。诊断脑出血。",
                [{"text": "右侧病灶。", "_rerank_score": 0.9}],
            ),
            last_report=[""],
        )
        return _payload(await handler({"query": "CT头颅 脑出血"})), calls

    valid, valid_calls = asyncio.run(scenario(json.dumps(BASE_REPORT, ensure_ascii=False)))
    assert valid == BASE_REPORT
    assert valid_calls == ["rag_search_tool"]

    fenced = f"```json\n{json.dumps(BASE_REPORT, ensure_ascii=False)}\n```"
    parsed, fenced_calls = asyncio.run(scenario(fenced))
    assert parsed == BASE_REPORT
    assert fenced_calls == ["rag_search_tool"]


def test_rag_tool_invalid_output_repairs_once_and_never_returns_raw():
    invalid = '{"reasoning":"x", "2. bad", "3. bad"}'

    async def scenario(repair_output):
        outputs = iter([invalid, repair_output])
        callers = []

        async def chat_fn(_messages, **kwargs):
            callers.append(kwargs.get("caller"))
            return next(outputs)

        handler = create_rag_search_handler(
            chat_fn=chat_fn,
            entity_tracker=EntityTracker(),
            get_embedding_fn=lambda _text: [],
            search_reports_fn=lambda _query, _emit=None: (
                "reference",
                [{"text": "reference", "_rerank_score": 0.9}],
            ),
            last_report=[""],
        )
        return json.loads(await handler({"query": "CT头颅"})), callers

    success, callers = asyncio.run(scenario(json.dumps(BASE_REPORT, ensure_ascii=False)))
    assert success.pop("_is_final") is True
    assert success == BASE_REPORT
    assert callers == ["rag_search_tool", "rag_search_repair"]

    failure, callers = asyncio.run(scenario("still invalid"))
    assert failure["_is_final"] is False
    assert failure["error"] == "报告生成失败，请重试"
    assert invalid not in json.dumps(failure, ensure_ascii=False)
    assert callers == ["rag_search_tool", "rag_search_repair"]


def test_edit_uses_session_report_allows_wording_and_preserves_medical_facts():
    last_report = [serialize_report(BASE_REPORT)]
    captured_user_message = []
    edited = json.loads(json.dumps(BASE_REPORT, ensure_ascii=False))
    edited["results"][0]["影像学表现"] = (
        edited["results"][0]["影像学表现"]
        .replace("65HU", "70HU")
        .replace("CT平扫显示", "CT平扫示")
        .replace("范围约", "大小约")
    )

    async def chat_fn(messages, **kwargs):
        if kwargs.get("caller") == "edit_report_judge":
            return VALID_EDIT_JUDGEMENT
        captured_user_message.append(messages[-1]["content"])
        return json.dumps(edited, ensure_ascii=False)

    handler = create_edit_report_handler(chat_fn=chat_fn, last_report=last_report)
    result = _payload(asyncio.run(handler({
        "instruction": "修改CT为70",
        "current_report": {"results": [{"影像学表现": "伪造报告", "诊断意见": "伪造诊断"}]},
    })))
    assert result == edited
    assert "伪造报告" not in captured_user_message[0]
    assert "65HU" in captured_user_message[0]
    assert "70HU" in result["results"][0]["影像学表现"]
    assert "3.0×1.6cm" in result["results"][0]["影像学表现"]
    assert "右侧基底节区" in result["results"][0]["影像学表现"]
    assert "中线结构轻度向对侧移位" in result["results"][0]["影像学表现"]
    assert result["results"][0]["诊断意见"] == BASE_REPORT["results"][0]["诊断意见"]


def test_edit_then_second_edit_then_refine_uses_evolving_last_report():
    last_report = [serialize_report(BASE_REPORT)]

    edit_one = json.loads(json.dumps(BASE_REPORT, ensure_ascii=False))
    edit_one["results"][0]["影像学表现"] = edit_one["results"][0]["影像学表现"].replace(
        "65HU", "70HU"
    )
    edit_two = json.loads(json.dumps(edit_one, ensure_ascii=False))
    edit_two["results"][0]["影像学表现"] = edit_two["results"][0]["影像学表现"].replace(
        "3.0×1.6cm", "3.2×1.8cm"
    )
    refined = json.loads(json.dumps(edit_two, ensure_ascii=False))
    refined["results"][0]["影像学表现"] = refined["results"][0]["影像学表现"].replace(
        "CT平扫显示", "CT平扫示"
    )

    outputs = iter([edit_one, edit_two])
    edit_inputs = []

    async def edit_chat(messages, **kwargs):
        if kwargs.get("caller") == "edit_report_judge":
            return VALID_EDIT_JUDGEMENT
        edit_inputs.append(messages[-1]["content"])
        return json.dumps(next(outputs), ensure_ascii=False)

    edit_handler = create_edit_report_handler(chat_fn=edit_chat, last_report=last_report)
    first = _payload(asyncio.run(edit_handler({"instruction": "把65HU改成70HU"})))
    last_report[0] = serialize_report(first)
    second = _payload(asyncio.run(edit_handler({"instruction": "病灶大小改成3.2×1.8cm"})))
    assert "70HU" in edit_inputs[1]
    assert "65HU" not in edit_inputs[1]
    last_report[0] = serialize_report(second)

    async def refine_chat(messages, **_kwargs):
        assert "70HU" in messages[-1]["content"]
        assert "3.2×1.8cm" in messages[-1]["content"]
        return json.dumps(refined, ensure_ascii=False)

    refine_handler = create_refine_report_handler(
        chat_fn=refine_chat,
        entity_tracker=EntityTracker(),
        last_report=last_report,
    )
    final = _payload(asyncio.run(refine_handler({"style": "润色得更正式"})))
    assert "70HU" in final["results"][0]["影像学表现"]
    assert "3.2×1.8cm" in final["results"][0]["影像学表现"]
    assert "右侧" in final["results"][0]["影像学表现"]
    assert final["results"][0]["诊断意见"] == "右侧基底节区脑出血。"


def test_edit_judge_rejection_repairs_once_and_keeps_requested_change():
    last_report = [serialize_report(BASE_REPORT)]
    over_edited = json.loads(json.dumps(BASE_REPORT, ensure_ascii=False))
    over_edited["results"][0]["影像学表现"] = (
        over_edited["results"][0]["影像学表现"]
        .replace("65HU", "70HU")
        .replace("中线结构轻度向对侧移位", "中线结构居中")
    )
    repaired = json.loads(json.dumps(BASE_REPORT, ensure_ascii=False))
    repaired["results"][0]["影像学表现"] = (
        repaired["results"][0]["影像学表现"]
        .replace("65HU", "70HU")
        .replace("CT平扫显示", "CT平扫示")
    )
    callers = []

    async def chat_fn(_messages, **kwargs):
        caller = kwargs.get("caller")
        callers.append(caller)
        if caller == "edit_report_tool":
            return json.dumps(over_edited, ensure_ascii=False)
        if caller == "edit_report_repair":
            return json.dumps(repaired, ensure_ascii=False)
        if caller == "edit_report_judge" and callers.count("edit_report_judge") == 1:
            return json.dumps({
                "valid": False,
                "unexpected_changes": ["中线移位被改为中线居中"],
            }, ensure_ascii=False)
        return VALID_EDIT_JUDGEMENT

    handler = create_edit_report_handler(chat_fn=chat_fn, last_report=last_report)
    result = _payload(asyncio.run(handler({"instruction": "修改CT为70"})))

    assert callers == [
        "edit_report_tool",
        "edit_report_judge",
        "edit_report_repair",
        "edit_report_judge",
    ]
    assert "70HU" in result["results"][0]["影像学表现"]
    assert "3.0×1.6cm" in result["results"][0]["影像学表现"]
    assert "右侧基底节区" in result["results"][0]["影像学表现"]
    assert "中线结构轻度向对侧移位" in result["results"][0]["影像学表现"]
    assert result["results"][0]["诊断意见"] == "右侧基底节区脑出血。"


def test_edit_repair_failure_returns_clear_non_final_error():
    last_report = [serialize_report(BASE_REPORT)]
    invalid = json.loads(json.dumps(BASE_REPORT, ensure_ascii=False))
    invalid["results"][0]["影像学表现"] = invalid["results"][0]["影像学表现"].replace(
        "右侧", "左侧"
    )
    callers = []

    async def chat_fn(_messages, **kwargs):
        caller = kwargs.get("caller")
        callers.append(caller)
        if caller == "edit_report_judge":
            return json.dumps({
                "valid": False,
                "unexpected_changes": ["侧别被改变"],
            }, ensure_ascii=False)
        return json.dumps(invalid, ensure_ascii=False)

    handler = create_edit_report_handler(chat_fn=chat_fn, last_report=last_report)
    result = json.loads(asyncio.run(handler({"instruction": "修改CT为70"})))

    assert result["_is_final"] is False
    assert "一次修复后仍未通过" in result["error"]
    assert callers.count("edit_report_repair") == 1


def test_preservation_validators_reject_unrequested_fact_changes():
    changed = json.loads(json.dumps(BASE_REPORT, ensure_ascii=False))
    changed["results"][0]["影像学表现"] = changed["results"][0]["影像学表现"].replace(
        "65HU", "70HU"
    ).replace("中线结构轻度向对侧移位", "中线结构居中")
    with pytest.raises(ReportPreservationError):
        validate_edit_preservation(BASE_REPORT, changed, "把65HU改成70HU")

    refined = json.loads(json.dumps(BASE_REPORT, ensure_ascii=False))
    refined["results"][0]["影像学表现"] = refined["results"][0]["影像学表现"].replace(
        "右侧", "左侧"
    )
    with pytest.raises(ReportPreservationError):
        validate_refine_preservation(BASE_REPORT, refined)


class _FakeSTM:
    def __init__(self):
        self.history = []
        self.clear_count = 0

    def get_history(self, _session_id):
        return list(self.history)

    def add_turn(self, _session_id, user, assistant):
        self.history.extend([
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ])

    def clear(self, _session_id):
        self.clear_count += 1
        self.history.clear()


class _FakeRetriever:
    def __init__(self, _embedding):
        pass

    def index_stm(self, _history):
        pass

    def search_relevant(self, _query, top_k_stm=3):
        del top_k_stm
        return {"stm": []}


class _FakeRegistry:
    def __init__(self, result, tool_name="edit_report"):
        self.result = result
        self.tool_name = tool_name
        self._tools = {tool_name: {}}
        self.arguments = None

    def get_tools_schema(self):
        return [{
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": "test",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    async def execute(self, _tool_call_id, _name, arguments):
        self.arguments = arguments
        return self.result


def _run_pipeline_with_report_result(monkeypatch, tool_result, tool_name="edit_report"):
    calls = []
    registry = _FakeRegistry(tool_result, tool_name=tool_name)

    async def fake_chat_with_tools(_messages, **_kwargs):
        calls.append(1)
        if len(calls) > 1:
            return "不过别担心，我已经手动帮你完成。<tool_call>", None
        return "", [{
            "id": "call_edit",
            "name": tool_name,
            "arguments": {
                "instruction": "把65HU改成70HU",
                "current_report": {"results": [{"影像学表现": "fake", "诊断意见": "fake"}]},
            },
        }]

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    tracker.slots.update({
        "modality": "CT",
        "body_part": ["头颅"],
        "diagnosis": ["脑出血"],
    })
    last_report = [serialize_report(BASE_REPORT)]
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "把65HU改成70HU",
        "session-report",
        stm,
        tracker,
        None,
        last_report,
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    return result, calls, registry, stm, tracker, last_report, events


@pytest.mark.parametrize("tool_name", ["rag_search", "edit_report", "refine_report"])
def test_report_tool_success_is_final_and_skips_optional_final_llm(monkeypatch, tool_name):
    edited = json.loads(json.dumps(BASE_REPORT, ensure_ascii=False))
    edited["results"][0]["影像学表现"] = edited["results"][0]["影像学表现"].replace(
        "65HU", "70HU"
    )
    result, calls, registry, stm, tracker, last_report, events = _run_pipeline_with_report_result(
        monkeypatch,
        ToolResult(content=serialize_report(edited), is_final=True),
        tool_name=tool_name,
    )
    assert len(calls) == 1
    if tool_name in {"edit_report", "refine_report"}:
        assert "current_report" not in registry.arguments
    assert stm.clear_count == 0
    assert tracker.slots["modality"] == "CT"
    assert tracker.slots["body_part"] == ["头颅"]
    assert "脑出血" in tracker.slots["diagnosis"]
    assert "70HU" in last_report[0]
    assert "70HU" in result
    assert "report" in [event["type"] for event in events]


def test_report_tool_failure_stops_without_fake_medical_fallback(monkeypatch):
    result, calls, _registry, _stm, _tracker, last_report, events = _run_pipeline_with_report_result(
        monkeypatch,
        ToolResult(content=json.dumps({"error": "broken"}), is_final=False),
    )
    assert len(calls) == 1
    assert result == "报告处理失败，请重试。"
    assert "65HU" in last_report[0]
    assert "手动帮你完成" not in result
    assert "report" not in [event["type"] for event in events]
    assert "error" in [event["type"] for event in events]


@pytest.mark.parametrize(
    ("model_content", "expected_event", "expected_text"),
    [
        ("你好，我可以帮助生成或修改影像报告。", "message", "你好"),
        (json.dumps(BASE_REPORT, ensure_ascii=False), "error", "受控报告工具"),
    ],
)
def test_direct_chat_remains_allowed_but_main_llm_cannot_bypass_report_tools(
    monkeypatch,
    model_content,
    expected_event,
    expected_text,
):
    async def fake_chat_with_tools(_messages, **_kwargs):
        return model_content, None

    registry = _FakeRegistry(ToolResult(content="{}", is_final=False))
    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    tracker = EntityTracker()
    stm = _FakeSTM()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "你好",
        "session-direct",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    assert expected_text in result
    assert expected_event in [event["type"] for event in events]


def test_public_deployment_report_strips_reasoning_and_internal_fields():
    public = map_public_event(
        "report",
        {
            "content": "报告内容",
            "data": {
                "reasoning": "internal chain",
                "results": BASE_REPORT["results"],
                "query_plan": {"hidden": True},
            },
        },
        conversation_id="conv_public",
    )
    assert public == {
        "type": "report",
        "content": "报告内容",
        "data": {"results": BASE_REPORT["results"]},
    }


# =============================================================================
# Recovery 机制测试
# =============================================================================

def test_recovery_report_tool_required_retry_then_success(monkeypatch):
    """REPORT_TOOL_REQUIRED: LLM返回报告但不走工具 → 内部重试 → 成功"""
    registry = _FakeRegistry(
        ToolResult(content=serialize_report(BASE_REPORT), is_final=True),
        tool_name="rag_search"
    )
    call_count = [0]

    async def fake_chat_with_tools(_messages, **_kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # 第一次：LLM 直接在 content 中返回报告，不走工具
            return json.dumps(BASE_REPORT, ensure_ascii=False), None
        else:
            # 第二次：恢复后正确选择工具
            return "", [{
                "id": "call_rag",
                "name": "rag_search",
                "arguments": {"query": "CT头颅 脑出血"},
            }]

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "CT头颅 脑出血",
        "session-recovery-1",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    assert call_count[0] == 2
    assert "report" in [event["type"] for event in events]
    assert "服务暂时不可用" not in result


def test_recovery_report_tool_required_exhausted(monkeypatch):
    """REPORT_TOOL_REQUIRED: 恢复预算耗尽 → 返回服务不可用"""
    registry = _FakeRegistry(
        ToolResult(content=serialize_report(BASE_REPORT), is_final=True),
        tool_name="rag_search"
    )

    async def fake_chat_with_tools(_messages, **_kwargs):
        # 始终返回报告而不走工具
        return json.dumps(BASE_REPORT, ensure_ascii=False), None

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "CT头颅 脑出血",
        "session-recovery-2",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    assert "服务暂时不可用" in result
    assert "error" in [event["type"] for event in events]


def test_recovery_serialized_tool_call_parse_success(monkeypatch):
    """INVALID_TOOL_CALL: 序列化工具调用 → 解析成功 → 继续执行"""
    registry = _FakeRegistry(
        ToolResult(content=serialize_report(BASE_REPORT), is_final=True),
        tool_name="rag_search"
    )

    async def fake_chat_with_tools(_messages, **_kwargs):
        return '{"tool": "rag_search", "arguments": {"query": "CT头颅"}}', None

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "CT头颅",
        "session-recovery-3",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    assert "report" in [event["type"] for event in events]
    assert "服务暂时不可用" not in result


def test_recovery_multiple_report_tools_arbitrate(monkeypatch):
    """MULTIPLE_REPORT_TOOLS: 多个报告工具 → 自动仲裁，保留 rag_search"""
    registry = _FakeRegistry(
        ToolResult(content=serialize_report(BASE_REPORT), is_final=True),
        tool_name="rag_search"
    )
    # 注册多个工具让仲裁逻辑生效
    registry._tools = {
        "rag_search": {},
        "edit_report": {},
        "refine_report": {},
    }
    # 覆盖 get_tools_schema 返回多个工具
    original_get_schema = registry.get_tools_schema
    registry.get_tools_schema = lambda: [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": "test",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in ["rag_search", "edit_report", "refine_report"]
    ]
    registry.execute = original_execute = registry.execute

    async def fake_chat_with_tools(_messages, **_kwargs):
        return "", [
            {"id": "call_rag", "name": "rag_search", "arguments": {"query": "CT"}},
            {"id": "call_edit", "name": "edit_report", "arguments": {"instruction": "改"}},
            {"id": "call_refine", "name": "refine_report", "arguments": {"style": "正式"}},
        ]

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "CT头颅",
        "session-recovery-4",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    assert "report" in [event["type"] for event in events]
    assert "服务暂时不可用" not in result


def test_recovery_tool_failed_internal_retry(monkeypatch):
    """REPORT_TOOL_FAILED: 内部错误 → 重试 pipeline → 成功"""
    tool_result = ToolResult(
        content=json.dumps({"error": "report parse failed"}, ensure_ascii=False),
        is_final=False,
    )
    registry = _FakeRegistry(tool_result, tool_name="rag_search")
    call_count = [0]

    async def fake_chat_with_tools(_messages, **_kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            # 第一次：LLM 选择工具，但工具返回错误
            return "", [{
                "id": "call_rag",
                "name": "rag_search",
                "arguments": {"query": "CT头颅"},
            }]
        else:
            # 第二次：恢复后 LLM 再次选择工具
            return "", [{
                "id": "call_rag",
                "name": "rag_search",
                "arguments": {"query": "CT头颅"},
            }]

    # 第二次 execute 返回成功
    execute_count = [0]
    original_execute = registry.execute
    async def execute_with_retry(*args, **kwargs):
        execute_count[0] += 1
        if execute_count[0] == 1:
            return tool_result
        return ToolResult(content=serialize_report(BASE_REPORT), is_final=True)
    registry.execute = execute_with_retry

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "CT头颅",
        "session-recovery-5",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    # 重试后应该成功
    assert "report" in [event["type"] for event in events]
    assert "服务暂时不可用" not in result
    assert "最终处理失败" not in result


def test_recovery_tool_failed_user_facing_no_retry(monkeypatch):
    """REPORT_TOOL_FAILED: 用户可处理错误（NO_LAST_REPORT）→ 直接展示，不重试"""
    tool_result = ToolResult(
        content=json.dumps({"error": "当前会话没有可修改的报告"}, ensure_ascii=False),
        is_final=False,
    )
    registry = _FakeRegistry(tool_result, tool_name="edit_report")
    call_count = [0]

    async def fake_chat_with_tools(_messages, **_kwargs):
        call_count[0] += 1
        return "", [{
            "id": "call_edit",
            "name": "edit_report",
            "arguments": {"instruction": "修改"},
        }]

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "修改报告",
        "session-recovery-6",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    # 用户可处理错误，不重试，直接展示
    assert call_count[0] == 1  # 只调用了一次 LLM
    assert "没有可修改的报告" in result


def test_recovery_schema_invalid_terminal(monkeypatch):
    """REPORT_SCHEMA_INVALID: 工具返回非法 JSON → 日志记录并终止，不重试"""
    tool_result = ToolResult(
        content='{"results": "not a list"}',
        is_final=True,
    )
    registry = _FakeRegistry(tool_result, tool_name="rag_search")
    call_count = [0]

    async def fake_chat_with_tools(_messages, **_kwargs):
        call_count[0] += 1
        return "", [{
            "id": "call_rag",
            "name": "rag_search",
            "arguments": {"query": "CT头颅"},
        }]

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "CT头颅",
        "session-recovery-7",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    assert "服务暂时不可用" in result
    assert call_count[0] == 1  # 不重试


def test_recovery_final_llm_serialized_retry(monkeypatch):
    """INVALID_FINAL_TOOL_CALL: 非报告工具二次LLM输出序列化 → 重试 → 成功"""
    registry = _FakeRegistry(
        ToolResult(content=json.dumps({"status": "ok"}), is_final=False),
        tool_name="utility_tool"
    )
    # 注册一个非报告工具
    registry._tools = {"utility_tool": {}}
    registry.get_tools_schema = lambda: [
        {
            "type": "function",
            "function": {
                "name": "utility_tool",
                "description": "utility",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]
    call_count = [0]

    async def fake_chat_with_tools(messages, **kwargs):
        call_count[0] += 1
        has_tools = kwargs.get("tools") is not None
        if has_tools:
            # 第一次：带工具调用
            return "", [{
                "id": "call_util",
                "name": "utility_tool",
                "arguments": {},
            }]
        else:
            # 第二次：不带工具（final LLM）
            if call_count[0] == 2:
                # 第一次 final LLM：返回序列化工具调用
                return '{"tool": "rag_search", "arguments": {}}', None
            else:
                # 第三次 final LLM：正常文本
                return "操作完成", None

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "查询",
        "session-recovery-8",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    assert "操作完成" in result
    assert call_count[0] == 3  # 1次工具调用 + 2次 final LLM


def test_recovery_budget_not_contaminated_by_retry(monkeypatch):
    """内部恢复错误不写入 STM，避免污染多轮对话"""
    tool_result = ToolResult(
        content=json.dumps({"error": "report parse failed"}, ensure_ascii=False),
        is_final=False,
    )
    registry = _FakeRegistry(tool_result, tool_name="rag_search")
    call_count = [0]

    async def fake_chat_with_tools(_messages, **_kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return "", [{
                "id": "call_rag",
                "name": "rag_search",
                "arguments": {"query": "CT头颅"},
            }]
        else:
            return "", [{
                "id": "call_rag",
                "name": "rag_search",
                "arguments": {"query": "CT头颅"},
            }]

    execute_count = [0]
    original_execute = registry.execute
    async def execute_with_retry(*args, **kwargs):
        execute_count[0] += 1
        if execute_count[0] == 1:
            return tool_result
        return ToolResult(content=serialize_report(BASE_REPORT), is_final=True)
    registry.execute = execute_with_retry

    monkeypatch.setattr(pipeline_module, "chat_with_tools", fake_chat_with_tools)
    monkeypatch.setattr(pipeline_module, "needs_rewrite", lambda _query: False)
    monkeypatch.setattr(pipeline_module, "MemoryRetriever", _FakeRetriever)
    monkeypatch.setattr(pipeline_module, "_build_tool_registry", lambda *_args, **_kwargs: registry)

    stm = _FakeSTM()
    tracker = EntityTracker()
    events = []
    result = asyncio.run(pipeline_module.run_pipeline(
        "CT头颅",
        "session-recovery-9",
        stm,
        tracker,
        None,
        [""],
        lambda event_type, data: events.append({"type": event_type, **data}),
    ))
    # STM 中不应包含内部错误消息
    stm_texts = [msg["content"] for msg in stm.history]
    assert not any("report parse failed" in t for t in stm_texts)
    assert not any("正在重试" in t for t in stm_texts)
    # 最终成功结果在 STM 中
    assert "report" in [event["type"] for event in events]


def test_recovery_classify_tool_error():
    """_classify_tool_error 正确细分错误类型"""
    assert pipeline_module._classify_tool_error(
        json.dumps({"error": "当前会话没有可修改的报告"})
    ) == "NO_LAST_REPORT"
    assert pipeline_module._classify_tool_error(
        json.dumps({"error": "Judge 判定存在超范围修改"})
    ) == "EDIT_JUDGE_FAILED"
    assert pipeline_module._classify_tool_error(
        json.dumps({"error": "一次修复后仍未通过"})
    ) == "EDIT_REPAIR_FAILED"
    assert pipeline_module._classify_tool_error(
        json.dumps({"error": "report parse failed"})
    ) == "REPORT_PARSE_FAILED"
    assert pipeline_module._classify_tool_error(
        json.dumps({"error": "service unavailable"})
    ) == "LLM_SERVICE_ERROR"
    assert pipeline_module._classify_tool_error(
        json.dumps({"error": "unknown error"})
    ) == "UNKNOWN_TOOL_ERROR"


def test_recovery_parse_serialized_tool_call():
    """_parse_serialized_tool_call 正确解析各种格式"""
    # JSON 格式
    result = pipeline_module._parse_serialized_tool_call(
        '{"tool": "rag_search", "arguments": {"query": "CT"}}'
    )
    assert result == {"name": "rag_search", "arguments": {"query": "CT"}}

    # name 格式
    result = pipeline_module._parse_serialized_tool_call(
        '{"name": "edit_report", "arguments": {"instruction": "修改"}}'
    )
    assert result == {"name": "edit_report", "arguments": {"instruction": "修改"}}

    # 函数调用格式
    result = pipeline_module._parse_serialized_tool_call(
        'rag_search({"query": "CT头颅"})'
    )
    assert result == {"name": "rag_search", "arguments": {"query": "CT头颅"}}

    # 无效格式
    assert pipeline_module._parse_serialized_tool_call("hello world") is None
    assert pipeline_module._parse_serialized_tool_call("") is None


def test_recovery_arbitrate_report_tool():
    """_arbitrate_report_tool 按优先级保留一个工具"""
    tool_results = [
        ("id1", "refine_report", ToolResult("", False)),
        ("id2", "edit_report", ToolResult("", False)),
        ("id3", "rag_search", ToolResult("", False)),
    ]
    kept = pipeline_module._arbitrate_report_tool(tool_results)
    assert kept[1] == "rag_search"

    tool_results = [
        ("id1", "refine_report", ToolResult("", False)),
        ("id2", "edit_report", ToolResult("", False)),
    ]
    kept = pipeline_module._arbitrate_report_tool(tool_results)
    assert kept[1] == "edit_report"


if __name__ == "__main__":
    import pytest
    import sys
    args = [
        __file__,
        "-v",
        "--tb=short",
        "-k", "recovery",
    ]
    if len(sys.argv) > 1:
        args = [__file__] + sys.argv[1:]
    sys.exit(pytest.main(args))