"""Canonical structured-report parsing, validation, and safety checks.

The LLM may produce several historical report shapes at system boundaries, but
the rest of the runtime only receives the canonical ``results`` representation.
This module deliberately contains no model or storage dependencies.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Awaitable, Callable


REPORT_SCHEMA_VERSION = "1"
REPORT_TOOL_NAMES = frozenset({"rag_search", "edit_report", "refine_report"})


class ReportSchemaError(ValueError):
    """Raised when content cannot become a valid canonical report."""


class ReportPreservationError(ReportSchemaError):
    """Raised when edit/refine output changes protected medical facts."""


def _extract_json_value(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        raise ReportSchemaError("报告输出为空或不是 JSON object")

    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            raise ReportSchemaError("报告输出不包含 JSON object") from None
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError as exc:
            raise ReportSchemaError("报告 JSON 不完整或格式非法") from exc

    if not isinstance(value, dict):
        raise ReportSchemaError("报告 JSON root 必须是 object")
    return value


def _legacy_pairs(findings: Any, impression: Any) -> list[dict[str, str]]:
    if isinstance(findings, str) and isinstance(impression, str):
        return [{"影像学表现": findings, "诊断意见": impression}]

    if isinstance(findings, dict) or isinstance(impression, dict):
        findings_map = findings if isinstance(findings, dict) else {}
        impression_map = impression if isinstance(impression, dict) else {}
        keys = list(dict.fromkeys([*findings_map.keys(), *impression_map.keys()]))
        if not keys:
            raise ReportSchemaError("旧报告结构中没有可转换的病变条目")
        return [
            {
                "影像学表现": str(findings_map.get(key, findings if isinstance(findings, str) else "")),
                "诊断意见": str(impression_map.get(key, impression if isinstance(impression, str) else "")),
            }
            for key in keys
        ]

    raise ReportSchemaError("旧报告结构缺少可转换的表现或诊断字段")


def normalize_report_schema(value: Any) -> dict[str, Any]:
    """Normalize canonical and supported legacy boundary shapes.

    Supported legacy inputs are top-level Chinese fields and
    ``findings``/``impression``. The returned object never contains those
    legacy shapes.
    """
    obj = _extract_json_value(value)
    reasoning = obj.get("reasoning")

    if "results" in obj:
        raw_results = obj["results"]
        if not isinstance(raw_results, list):
            raise ReportSchemaError("results 必须是 list")
        results: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                raise ReportSchemaError("results 中的每个 item 必须是 object")
            if "影像学表现" in item or "诊断意见" in item:
                results.append({
                    "影像学表现": item.get("影像学表现"),
                    "诊断意见": item.get("诊断意见"),
                })
            elif "findings" in item or "impression" in item:
                results.append({
                    "影像学表现": item.get("findings"),
                    "诊断意见": item.get("impression"),
                })
            else:
                raise ReportSchemaError("results item 缺少影像学表现/诊断意见")
    elif "影像学表现" in obj or "诊断意见" in obj:
        results = _legacy_pairs(obj.get("影像学表现"), obj.get("诊断意见"))
    elif "findings" in obj or "impression" in obj:
        results = _legacy_pairs(obj.get("findings"), obj.get("impression"))
    else:
        raise ReportSchemaError("报告缺少 results")

    normalized: dict[str, Any] = {"results": results}
    if reasoning is not None:
        normalized["reasoning"] = reasoning
        normalized = {"reasoning": reasoning, "results": results}
    return normalized


def validate_canonical_report(value: Any) -> dict[str, Any]:
    """Return a sanitized canonical report or raise ``ReportSchemaError``."""
    obj = normalize_report_schema(value)
    reasoning = obj.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, str):
        raise ReportSchemaError("reasoning 必须是 string")

    results = obj.get("results")
    if not isinstance(results, list) or not results:
        raise ReportSchemaError("results 必须是非空 list")

    canonical_results: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            raise ReportSchemaError("results 中的每个 item 必须是 object")
        findings = item.get("影像学表现")
        impression = item.get("诊断意见")
        if not isinstance(findings, str) or not isinstance(impression, str):
            raise ReportSchemaError("影像学表现和诊断意见必须是 string")
        if not findings.strip() or not impression.strip():
            raise ReportSchemaError("影像学表现和诊断意见不能为空")
        canonical_results.append({
            "影像学表现": findings,
            "诊断意见": impression,
        })

    report: dict[str, Any] = {"results": canonical_results}
    if reasoning:
        report = {"reasoning": reasoning, "results": canonical_results}
    return report


def parse_report_output(raw: Any) -> dict[str, Any]:
    """Extract, normalize, and validate one structured report."""
    return validate_canonical_report(raw)


def serialize_report(report: Any, *, include_reasoning: bool = True) -> str:
    canonical = validate_canonical_report(report)
    if not include_reasoning:
        canonical.pop("reasoning", None)
    return json.dumps(canonical, ensure_ascii=False, indent=2)


def sanitize_public_report(report: Any) -> dict[str, Any]:
    """Return the stable public shape without internal reasoning."""
    canonical = validate_canonical_report(report)
    return {"results": canonical["results"]}


def build_repair_messages(raw: Any) -> list[dict[str, str]]:
    """Build the single constrained repair request used by report tools."""
    raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    return [
        {
            "role": "system",
            "content": (
                "你是 JSON 结构修复器，不是医学报告生成器。只把输入中已经存在的医学内容整理为指定 JSON；"
                "不得新增、删减、改写或推断任何医学事实。只输出一个 JSON object，不要 Markdown fence 或解释。"
                "唯一允许的结构是 {\"results\":[{\"影像学表现\":\"...\",\"诊断意见\":\"...\"}]}，"
                "可保留一个 string 类型的 reasoning。若输入缺少足够内容，输出 {}。"
            ),
        },
        {
            "role": "user",
            "content": f"请仅修复以下输出的 JSON 结构：\n{raw_text}",
        },
    ]


async def parse_report_with_repair(
    raw: Any,
    repair_fn: Callable[[list[dict[str, str]]], Awaitable[Any]],
) -> tuple[dict[str, Any], bool]:
    """Parse a report and, on failure, make exactly one constrained repair."""
    try:
        return parse_report_output(raw), False
    except ReportSchemaError as first_error:
        repaired = await repair_fn(build_repair_messages(raw))
        try:
            return parse_report_output(repaired), True
        except ReportSchemaError as second_error:
            raise ReportSchemaError(
                f"报告结构修复后仍不合法: {second_error}"
            ) from first_error


def _core(report: Any) -> dict[str, Any]:
    canonical = validate_canonical_report(report)
    return {"results": copy.deepcopy(canonical["results"])}


def _report_text(report: Any) -> str:
    return "\n".join(
        f"{item['影像学表现']}\n{item['诊断意见']}"
        for item in _core(report)["results"]
    )


def _find_explicit_replacement(before: Any, instruction: str) -> tuple[str, str] | None:
    match = re.search(r"(.+?)(?:改成|改为|修改为|替换成|替换为|换成)(.+)", instruction)
    if not match:
        return None

    left = re.sub(r"^(?:请|麻烦)?\s*(?:把|将)?", "", match.group(1)).strip(" ：:，,。'\"“”")
    right = match.group(2).strip()
    right = re.split(r"[，,。](?:其他|其它|其余|剩余|未提及|并保持|不要)", right, maxsplit=1)[0]
    right = right.strip(" ：:，,。'\"“”")
    if not left or not right:
        return None

    haystack = _report_text(before)
    source = ""
    numeric_suffix = re.search(
        r"(\d+(?:\.\d+)?(?:\s*[×xX*]\s*\d+(?:\.\d+)?){0,2}\s*(?:HU|mm|cm|mL|ml|%|％)?)$",
        left,
        flags=re.IGNORECASE,
    )
    if numeric_suffix and numeric_suffix.group(1).strip() in haystack:
        source = numeric_suffix.group(1).strip()
    for index in range(len(left)):
        if source:
            break
        suffix = left[index:].strip(" ：:，,。'\"“”")
        if suffix and suffix in haystack:
            source = suffix
            break
    if not source:
        return None
    return source, right


_NUMBER_TOKEN_RE = re.compile(
    r"\d+(?:\.\d+)?(?:\s*[×xX*]\s*\d+(?:\.\d+)?){1,2}\s*(?:mm|cm|m)?"
    r"|\d+(?:\.\d+)?\s*(?:HU|mm|cm|mL|ml|个|枚|处|侧|％|%)?",
    re.IGNORECASE,
)
_LATERALITY_RE = re.compile(r"左侧|右侧|双侧|两侧|单侧|左|右")
_NEGATION_RE = re.compile(r"未见|未及|无明显|无|否认|不考虑|不支持|排除")
_COUNT_FACT_RE = re.compile(r"单发|多发|一个|两个|两处|数个|多个")
_ANATOMY_RE = re.compile(
    r"基底节区|额叶|颞叶|顶叶|枕叶|脑干|小脑|脑室|中线结构|头颅|颅脑|胸部|肺部|"
    r"腹部|肝脏|胆囊|胰腺|脾脏|肾脏|盆腔|子宫|卵巢|前列腺|膀胱|脊柱|颈椎|胸椎|"
    r"腰椎|骶椎|膝关节|髋关节|肩关节|肘关节|腕关节|踝关节|甲状腺|心脏|冠脉|主动脉"
)


def _tokens(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(token.replace(" ", "") for token in pattern.findall(text))


def _allows_numeric_change(token: str, instruction: str) -> bool:
    lowered = token.lower()
    if "hu" in lowered:
        return "HU" in instruction.upper() or "CT值" in instruction or "密度值" in instruction
    if "×" in token or "x" in lowered or any(unit in lowered for unit in ("mm", "cm")):
        return any(word in instruction for word in ("大小", "尺寸", "范围", "长径", "短径"))
    return any(word in instruction for word in ("数量", "个数", "数目"))


def validate_edit_preservation(before: Any, after: Any, instruction: str) -> None:
    """Reject edits that make changes outside the user's requested scope."""
    before_core = _core(before)
    after_core = _core(after)
    before_results = before_core["results"]
    after_results = after_core["results"]

    replacement = _find_explicit_replacement(before_core, instruction)
    if replacement:
        source, target = replacement
        expected = copy.deepcopy(before_core)
        for item in expected["results"]:
            item["影像学表现"] = item["影像学表现"].replace(source, target)
            item["诊断意见"] = item["诊断意见"].replace(source, target)
        if expected != after_core:
            raise ReportPreservationError("明确替换型编辑修改了指令范围之外的内容")
        return

    if len(before_results) != len(after_results):
        if not any(word in instruction for word in ("删除病变", "新增病变", "增加病变")):
            raise ReportPreservationError("编辑意外改变了病变条目数量")

    before_text = _report_text(before_core)
    after_text = _report_text(after_core)
    before_numbers = _tokens(_NUMBER_TOKEN_RE, before_text)
    after_numbers = _tokens(_NUMBER_TOKEN_RE, after_text)
    for token, count in before_numbers.items():
        if after_numbers[token] < count and not _allows_numeric_change(token, instruction):
            raise ReportPreservationError(f"编辑意外改变了受保护数值: {token}")
    for token, count in after_numbers.items():
        if count > before_numbers[token] and token not in instruction.replace(" ", ""):
            raise ReportPreservationError(f"编辑引入了指令中不存在的新数值: {token}")

    if not any(word in instruction for word in ("左", "右", "侧别")):
        if _tokens(_LATERALITY_RE, before_text) != _tokens(_LATERALITY_RE, after_text):
            raise ReportPreservationError("编辑意外改变了侧别")

    diagnosis_change_requested = any(word in instruction for word in ("诊断", "意见", "结论", "病名"))
    if not diagnosis_change_requested:
        before_diagnoses = [item["诊断意见"] for item in before_results]
        after_diagnoses = [item["诊断意见"] for item in after_results]
        if before_diagnoses != after_diagnoses:
            raise ReportPreservationError("编辑意外改变了诊断意见")

    similarity = SequenceMatcher(None, before_text, after_text).ratio()
    if similarity < 0.55:
        raise ReportPreservationError("编辑造成了过多不可解释的文本变化")


def validate_refine_preservation(before: Any, after: Any) -> None:
    """Require style refinement to preserve programmatically observable facts."""
    before_core = _core(before)
    after_core = _core(after)
    before_results = before_core["results"]
    after_results = after_core["results"]
    if len(before_results) != len(after_results):
        raise ReportPreservationError("润色改变了病变条目数量")

    before_text = _report_text(before_core)
    after_text = _report_text(after_core)
    protected_patterns = (
        (_NUMBER_TOKEN_RE, "数值"),
        (_LATERALITY_RE, "侧别"),
        (_NEGATION_RE, "阳性/否定事实"),
        (_COUNT_FACT_RE, "病变数量"),
        (_ANATOMY_RE, "解剖部位"),
    )
    for pattern, label in protected_patterns:
        if _tokens(pattern, before_text) != _tokens(pattern, after_text):
            raise ReportPreservationError(f"润色改变了受保护的{label}")

    if [item["诊断意见"] for item in before_results] != [
        item["诊断意见"] for item in after_results
    ]:
        raise ReportPreservationError("润色改变了诊断意见")

    if SequenceMatcher(None, before_text, after_text).ratio() < 0.45:
        raise ReportPreservationError("润色造成了过多医学内容变化")


def looks_like_serialized_tool_call(content: str) -> bool:
    """Detect textual tool-call markup emitted while tools are disabled."""
    if not content:
        return False
    return bool(re.search(
        r"<\/?tool_call>|<function\s*=|\"name\"\s*:\s*\"(?:rag_search|edit_report|refine_report)\"",
        content,
        flags=re.IGNORECASE,
    ))
