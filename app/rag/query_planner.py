"""Build a validated, confidence-aware retrieval plan from natural language."""

import json
import logging
import re
from typing import Dict, Optional

from config import get_rewrite_client, get_rewrite_model

from .metadata_navigator import LEVEL_FIELDS, MetadataNavigator
from .query_rewrite import parse_query_keywords

logger = logging.getLogger(__name__)
PLAN_FIELDS = ("modality", *LEVEL_FIELDS)


def _slot(value="", confidence=0.0, source=""):
    return {"value": value or "", "confidence": confidence, "source": source}


def _extract_json(text: str) -> Dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            loaded = json.loads(match.group(0))
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}


def _context_values(context: Optional[Dict]) -> Dict[str, str]:
    context = context or {}
    body_parts = context.get("body_part", [])
    if isinstance(body_parts, str):
        body_parts = [body_parts]
    diagnoses = context.get("diagnosis", [])
    if isinstance(diagnoses, str):
        diagnoses = [diagnoses]
    return {
        "modality": str(context.get("modality") or ""),
        "body_part": " ".join(str(value) for value in body_parts if value),
        "diagnosis": " ".join(str(value) for value in diagnoses if value),
    }


def _llm_plan(query: str, context: Dict, navigator: MetadataNavigator, selected: Dict) -> Dict:
    candidates = {
        "modality": navigator.modalities(),
    }
    progressive = dict(selected)
    for field in LEVEL_FIELDS:
        candidates[field] = navigator.candidates(field, progressive)
        if selected.get(field):
            progressive[field] = selected[field]

    prompt = (
        "你是医疗影像检索规划器。根据用户问题、历史槽位和允许值，"
        "提取检查类型和目录层级。每个字段只能从对应允许值中选择；"
        "无法确定必须返回空字符串。不要根据常识编造目录。\n"
        "只输出JSON，格式："
        '{"modality":"","level_1":"","level_2":"","level_3":"","level_4":"",'
        '"diagnosis_query":""}\n'
        f"用户问题：{query}\n"
        f"历史槽位：{json.dumps(context, ensure_ascii=False)}\n"
        f"允许值：{json.dumps(candidates, ensure_ascii=False)}"
    )
    client = get_rewrite_client()
    response = client.chat.completions.create(
        model=get_rewrite_model(),
        messages=[
            {"role": "system", "content": "严格按要求输出一个JSON对象。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=256,
        timeout=15,
    )
    return _extract_json(response.choices[0].message.content or "")


def build_query_plan(
    query: str,
    context: Optional[Dict] = None,
    navigator: Optional[MetadataNavigator] = None,
    use_llm: bool = True,
) -> Dict:
    """Return validated filters plus semantic and lexical search text."""
    navigator = navigator or MetadataNavigator()
    context_values = _context_values(context)
    parsed = parse_query_keywords(query)
    plan = {field: _slot() for field in PLAN_FIELDS}
    selected: Dict[str, str] = {}

    modality = navigator.match(query, "modality") if navigator.has_data() else ""
    modality = modality or parsed.get("检查类型", "")
    if modality == "MRI" and navigator.validate("modality", "MR"):
        modality = "MR"
    if modality:
        plan["modality"] = _slot(modality, 1.0, "current_query")
        selected["modality"] = modality
    elif context_values["modality"]:
        value = context_values["modality"]
        if not navigator.has_data() or navigator.validate("modality", value):
            plan["modality"] = _slot(value, 0.8, "history")
            selected["modality"] = value

    explicit_path = (
        navigator.best_matching_path(query, selected.get("modality", ""))
        if navigator.has_data()
        else {}
    )
    for field in LEVEL_FIELDS:
        value = explicit_path.get(field, "")
        if not value and field == "level_1" and context_values["body_part"]:
            value = navigator.match(context_values["body_part"], field, selected)
            source = "history"
            confidence = 0.8
        else:
            source = "current_query"
            confidence = 1.0
        if value:
            plan[field] = _slot(value, confidence, source)
            selected[field] = value

    llm_result = {}
    if use_llm and navigator.has_data():
        try:
            llm_result = _llm_plan(query, context_values, navigator, selected)
        except Exception as exc:
            logger.warning("查询规划LLM失败，使用规则规划: %s", exc)

    validated = dict(selected)
    for field in PLAN_FIELDS:
        if plan[field]["value"]:
            validated[field] = plan[field]["value"]
            continue
        value = str(llm_result.get(field) or "").strip()
        if navigator.validate(field, value, validated):
            plan[field] = _slot(value, 0.65, "llm_inference")
            validated[field] = value

    filters = {
        field: plan[field]["value"]
        for field in PLAN_FIELDS
        if plan[field]["value"]
    }
    diagnosis_query = query
    removable_values = list(filters.values())
    if parsed.get("检查类型"):
        removable_values.append(parsed["检查类型"])
    if parsed.get("部位"):
        removable_values.append(parsed["部位"])
    for value in sorted(set(removable_values), key=len, reverse=True):
        diagnosis_query = diagnosis_query.replace(value, " ")
    diagnosis_query = re.sub(r"[\s,，、;；]+", " ", diagnosis_query).strip()
    if not diagnosis_query:
        diagnosis_query = str(llm_result.get("diagnosis_query") or "").strip()
    if not diagnosis_query:
        diagnosis_query = context_values["diagnosis"]

    return {
        "query": query,
        "semantic_query": query,
        "keyword_query": diagnosis_query or query,
        "diagnosis_query": diagnosis_query,
        "filters": filters,
        "slots": plan,
    }
