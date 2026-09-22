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

# 推理模型（reasoning 与正文共享 max_tokens 预算）在生成 Canonical JSON 前会消耗
# 数千 token 推理，2048 会被推理耗尽导致正文为空、报告缺少 results。
# 实测默认思考模式下完整报告需约 4.5k~6.9k completion tokens，故放宽到 8192。
REPORT_MAX_TOKENS = 8192

# Edit Judge 只需返回 {valid, unexpected_changes} 小 JSON，但推理仍占用预算，
# 原值 512 会被推理耗尽导致 Judge 结构非法、编辑误判为超范围修改。
EDIT_JUDGE_MAX_TOKENS = 4096


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


def _parse_markdown_report(markdown_text: str) -> dict[str, Any]:
    """将 Markdown 格式报告转换为 Canonical JSON 格式
    
    支持的 Markdown 格式：
    ## 一、影像学表现
    内容...
    
    ## 二、诊断意见
    内容...
    """
    lines = markdown_text.strip().split('\n')
    
    imaging_section = []
    diagnosis_section = []
    current_section = None
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        if '影像学表现' in line or '影像学所见' in line:
            current_section = 'imaging'
            continue
        elif '诊断意见' in line or '诊断结论' in line or '印象' in line:
            current_section = 'diagnosis'
            continue
        
        # 跳过 Markdown 标题标记
        if line.startswith('#'):
            continue
        
        if current_section == 'imaging':
            imaging_section.append(line)
        elif current_section == 'diagnosis':
            diagnosis_section.append(line)
    
    # 合并为单个条目
    imaging_text = '\n'.join(imaging_section).strip()
    diagnosis_text = '\n'.join(diagnosis_section).strip()
    
    if not imaging_text and not diagnosis_text:
        raise ReportSchemaError("Markdown 报告未包含有效的影像学表现或诊断意见")
    
    return {
        "results": [
            {
                "影像学表现": imaging_text or "未记录",
                "诊断意见": diagnosis_text or "未记录"
            }
        ]
    }


def parse_report_output(raw: Any) -> dict[str, Any]:
    """Extract, normalize, and validate one structured report.
    
    支持两种输入格式：
    1. JSON 格式（标准 Canonical Report）
    2. Markdown 格式（通过 json_to_display 生成的展示格式）
    """
    # 如果是字符串，先尝试判断是 JSON 还是 Markdown
    if isinstance(raw, str):
        raw_stripped = raw.strip()
        
        # 优先尝试 JSON 解析（如果以 { 或 [ 开头）
        if raw_stripped.startswith(('{', '[')):
            try:
                return validate_canonical_report(raw)
            except ReportSchemaError:
                # JSON 解析失败，可能是 Markdown
                pass
        
        # 检测 Markdown 格式的特征（标题开头或包含关键标题）
        markdown_indicators = [
            '## 一、', '## 二、', '## 影像', '## 诊断'
        ]
        # 只有明确的 Markdown 标题才走 Markdown 路径
        if any(indicator in raw_stripped[:300] for indicator in markdown_indicators):
            try:
                return _parse_markdown_report(raw_stripped)
            except ReportSchemaError:
                # Markdown 格式确认但内容无效，直接抛出
                raise
            except Exception:
                # 其他异常，继续尝试 JSON
                pass
    
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


def _findings_text(report: Any) -> str:
    return "\n".join(item["影像学表现"] for item in _core(report)["results"])


_NUMBER_TOKEN_RE = re.compile(
    r"\d+(?:\.\d+)?(?:\s*[×xX*]\s*\d+(?:\.\d+)?){1,2}\s*(?:mm|cm|m)?"
    r"|\d+(?:\.\d+)?\s*(?:HU|mm|cm|mL|ml|个|枚|处|侧|％|%)?",
    re.IGNORECASE,
)
_LATERALITY_RE = re.compile(r"左侧|右侧|双侧|两侧|单侧|左|右")
_NEGATION_RE = re.compile(r"未见|未及|无明显|无|否认|不考虑|不支持|排除")
_COUNT_FACT_RE = re.compile(r"单发|多发|一个|两个|两处|数个|多个")
_ANATOMY_RE = re.compile(
    r"基底节区|基底节|丘脑|额叶|颞叶|顶叶|枕叶|脑干|小脑|脑室|中线结构|头颅|颅脑|胸部|肺部|"
    r"腹部|肝脏|胆囊|胰腺|脾脏|肾脏|盆腔|子宫|卵巢|前列腺|膀胱|脊柱|颈椎|胸椎|"
    r"腰椎|骶椎|膝关节|髋关节|肩关节|肘关节|腕关节|踝关节|甲状腺|心脏|冠脉|主动脉"
)
_DIAGNOSIS_RE = re.compile(
    r"脑出血|蛛网膜下腔出血|硬膜下血肿|硬膜外血肿|脑梗死|脑肿瘤|脑水肿|"
    r"肺结节|肺炎|肺癌|气胸|胸腔积液|肝癌|肝囊肿|胆囊炎|胰腺炎|肾结石|"
    r"骨折|脱位|狭窄|占位(?:性病变)?"
)
_POSITIVE_FACT_RE = re.compile(
    r"出血|梗死|水肿|积液|结节|肿块|占位|骨折|脱位|移位|受压|狭窄|增宽|"
    r"肿大|强化|钙化|高密度|低密度|高信号|低信号"
)
_UNIT_RE = re.compile(r"HU|mm|cm|mL|ml|％|%", re.IGNORECASE)


def _tokens(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(token.replace(" ", "") for token in pattern.findall(text))


def _instruction_numbers(instruction: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", instruction))


def _number_parts(token: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", token))


def _allows_numeric_change(token: str, instruction: str) -> bool:
    lowered = token.lower()
    if "hu" in lowered:
        return (
            "HU" in instruction.upper()
            or "CT值" in instruction
            or "密度值" in instruction
            or ("CT" in instruction.upper() and bool(_instruction_numbers(instruction)))
        )
    if "×" in token or "x" in lowered or any(unit in lowered for unit in ("mm", "cm")):
        return any(word in instruction for word in ("大小", "尺寸", "范围", "长径", "短径"))
    return any(word in instruction for word in ("数量", "个数", "数目"))


def _count_semantics(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    counts["single"] = len(re.findall(r"单发|一个|一处|1\s*(?:个|处|枚)", text))
    counts["double"] = len(re.findall(r"两个|两处|2\s*(?:个|处|枚)", text))
    counts["multiple"] = len(re.findall(r"多发|数个|多个|数处", text))
    return counts


def _unit_tokens(text: str) -> Counter[str]:
    return Counter(token.lower().replace("％", "%") for token in _UNIT_RE.findall(text))


# 单位在报告中既有英文符号（mm/cm）也有中文写法（毫米/厘米），两者等价。
_UNIT_ALIASES = {
    "mm": ("毫米",),
    "cm": ("厘米",),
    "ml": ("毫升",),
    "hu": ("CT值", "密度值"),
    "%": ("百分比",),
}

# 同一量纲内的单位互换（mm↔cm）是尺寸指令的正常结果：替换必然让旧单位消失。
_LENGTH_UNITS = frozenset({"mm", "cm"})
_SIZE_WORDS = ("大小", "尺寸", "范围", "长径", "短径")


def _unit_named(unit: str, instruction: str) -> bool:
    lowered = instruction.lower()
    return unit in lowered or any(
        alias in instruction for alias in _UNIT_ALIASES.get(unit, ())
    )


def _unit_change_allowed(before: Counter[str], after: Counter[str], instruction: str) -> bool:
    """判断单位变更是否由编辑指令显式要求。

    放行两类变更，其余仍判为意外改变：
    1. 所有发生变化的单位都能在指令中找到（英文符号或中文写法）；
    2. 变化仅发生在同一量纲的长度单位之间（如"改成3.2×1.8cm"把 mm 换成 cm），
       且指令包含尺寸类表述。跨量纲（如删除 HU）不受此例外保护。
    """
    if "单位" in instruction:
        return True

    differing = {
        unit for unit in set(before) | set(after) if before[unit] != after[unit]
    }
    if not differing:
        return True

    if all(_unit_named(unit, instruction) for unit in differing):
        return True

    return differing <= _LENGTH_UNITS and any(word in instruction for word in _SIZE_WORDS)


def _laterality_semantics(text: str) -> Counter[str]:
    normalized = Counter()
    mapping = {
        "左侧": "left",
        "左": "left",
        "右侧": "right",
        "右": "right",
        "双侧": "bilateral",
        "两侧": "bilateral",
        "单侧": "unilateral",
    }
    for token in _LATERALITY_RE.findall(text):
        normalized[mapping[token]] += 1
    return normalized


def _token_changes(
    pattern: re.Pattern[str],
    before_text: str,
    after_text: str,
) -> set[str]:
    before_tokens = _tokens(pattern, before_text)
    after_tokens = _tokens(pattern, after_text)
    return {
        token
        for token in before_tokens.keys() | after_tokens.keys()
        if before_tokens[token] != after_tokens[token]
    }


def parse_edit_judge_output(raw: Any) -> dict[str, Any]:
    """Validate and normalize the constrained edit-judge response."""
    obj = _extract_json_value(raw)
    valid = obj.get("valid")
    unexpected = obj.get("unexpected_changes")
    if not isinstance(valid, bool):
        raise ReportSchemaError("Edit Judge 的 valid 必须是 boolean")
    if not isinstance(unexpected, list) or not all(
        isinstance(item, str) and item.strip() for item in unexpected
    ):
        raise ReportSchemaError("Edit Judge 的 unexpected_changes 必须是 string list")
    changes = [item.strip() for item in unexpected]
    return {"valid": valid and not changes, "unexpected_changes": changes}


def collect_edit_fact_violations(
    before: Any,
    after: Any,
    instruction: str,
) -> list[str]:
    """Return observable medical-fact changes outside the edit instruction.

    This intentionally avoids whole-report equality and similarity checks.
    Harmless wording changes are accepted while high-value facts remain guarded.
    """
    before_core = _core(before)
    after_core = _core(after)
    before_results = before_core["results"]
    after_results = after_core["results"]
    violations: list[str] = []

    if len(before_results) != len(after_results):
        if not any(word in instruction for word in ("删除病变", "新增病变", "增加病变")):
            violations.append("意外改变了病变条目数量")

    before_text = _report_text(before_core)
    after_text = _report_text(after_core)
    before_numbers = _tokens(_NUMBER_TOKEN_RE, before_text)
    after_numbers = _tokens(_NUMBER_TOKEN_RE, after_text)
    for token, count in before_numbers.items():
        if after_numbers[token] < count and not _allows_numeric_change(token, instruction):
            violations.append(f"意外删除或改变了受保护数值：{token}")
    for token, count in after_numbers.items():
        if count <= before_numbers[token]:
            continue
        requested_numbers = _instruction_numbers(instruction)
        if not (
            _number_parts(token)
            and _number_parts(token).issubset(requested_numbers)
            and (_allows_numeric_change(token, instruction) or token.replace(" ", "") in instruction.replace(" ", ""))
        ):
            violations.append(f"引入了指令中不存在的新数值：{token}")

    before_units = _unit_tokens(before_text)
    after_units = _unit_tokens(after_text)
    if before_units != after_units and not _unit_change_allowed(
        before_units, after_units, instruction
    ):
        violations.append("意外改变或删除了数值单位")

    if not any(word in instruction for word in ("左", "右", "侧别")):
        if _laterality_semantics(before_text) != _laterality_semantics(after_text):
            violations.append("意外改变了左右侧别")

    anatomy_changes = _token_changes(_ANATOMY_RE, before_text, after_text)
    if anatomy_changes and not (
        any(word in instruction for word in ("部位", "位置", "解剖"))
        or any(token in instruction for token in anatomy_changes)
    ):
        violations.append(f"意外改变了解剖部位：{', '.join(sorted(anatomy_changes))}")

    if _count_semantics(before_text) != _count_semantics(after_text) and not any(
        word in instruction for word in ("数量", "个数", "数目", "单发", "多发", "病变条目")
    ):
        violations.append("意外改变了病变数量")

    diagnosis_change_requested = any(word in instruction for word in ("诊断", "意见", "结论", "病名"))
    diagnosis_changes = _token_changes(_DIAGNOSIS_RE, before_text, after_text)
    if diagnosis_changes and not (
        diagnosis_change_requested
        or any(token in instruction for token in diagnosis_changes)
    ):
        violations.append(f"意外改变了诊断：{', '.join(sorted(diagnosis_changes))}")

    before_fact_text = _findings_text(before_core) if diagnosis_change_requested else before_text
    after_fact_text = _findings_text(after_core) if diagnosis_change_requested else after_text
    if len(_NEGATION_RE.findall(before_fact_text)) != len(_NEGATION_RE.findall(after_fact_text)) and not any(
        word in instruction for word in ("否定", "阳性", "阴性", "未见", "无", "排除", "考虑")
    ):
        violations.append("意外改变了否定/阳性事实")

    positive_changes = _token_changes(_POSITIVE_FACT_RE, before_fact_text, after_fact_text)
    unexpected_positive_changes = {
        token for token in positive_changes
        if token not in instruction
    }
    if unexpected_positive_changes:
        violations.append(
            f"意外改变了关键阳性事实：{', '.join(sorted(unexpected_positive_changes))}"
        )

    return list(dict.fromkeys(violations))


def validate_edit_preservation(before: Any, after: Any, instruction: str) -> None:
    """Reject observable medical-fact changes outside the user's instruction."""
    violations = collect_edit_fact_violations(before, after, instruction)
    if violations:
        raise ReportPreservationError("；".join(violations))


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
        r"<\/?tool_call>|<function\s*=|\"(?:name|tool)\"\s*:\s*\"(?:rag_search|edit_report|refine_report)\"",
        content,
        flags=re.IGNORECASE,
    ))