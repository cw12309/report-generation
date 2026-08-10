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
