"""A/B Evaluation Metrics — 第一版仅做客观自动指标。

不包含 LLM Judge，不做医学事实自动判断。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


SPECIFIC_TERM_PATTERNS = [
    r"[I-V]+级",
    r"[1-5]级",
    r"\d+\s*mm",
    r"\d+\s*cm",
    r"\d+\s*%",
    r"Pfirrmann",
    r"Modic",
    r"Lee",
]


def compute_metrics(
    case_id: str,
    expected: Dict[str, Any],
    rules_info: Dict[str, Any],
    baseline: Dict[str, Any],
    rule_enabled: Dict[str, Any],
) -> Dict[str, Any]:
    """计算单个 Case 的全部自动指标。"""
    metrics: Dict[str, Any] = {}

    metrics["baseline_success"] = baseline.get("error") is None
    metrics["rule_enabled_success"] = rule_enabled.get("error") is None

    metrics["baseline_json_valid"] = _is_valid_json(baseline.get("report"))
    metrics["rule_enabled_json_valid"] = _is_valid_json(rule_enabled.get("report"))

    metrics["baseline_result_count"] = _result_count(baseline.get("report"))
    metrics["rule_enabled_result_count"] = _result_count(rule_enabled.get("report"))

    metrics["expected_rule_matched"] = _check_expected_rule(expected, rules_info)
    metrics["wrong_rule_matched"] = _check_wrong_rule(expected, rules_info)

    metrics["baseline_output_chars"] = _output_chars(baseline.get("report"))
    metrics["rule_enabled_output_chars"] = _output_chars(rule_enabled.get("report"))

    metrics["baseline_prompt_chars"] = baseline.get("system_prompt_chars", 0)
    metrics["rule_enabled_prompt_chars"] = rule_enabled.get("system_prompt_chars", 0)

    metrics["baseline_specific_terms"] = _extract_specific_terms(baseline.get("report"))
    metrics["rule_enabled_specific_terms"] = _extract_specific_terms(rule_enabled.get("report"))

    metrics["needs_fact_review"] = _detect_new_specific_terms(
        metrics["baseline_specific_terms"],
        metrics["rule_enabled_specific_terms"],
    )

    return metrics


def _is_valid_json(report: Any) -> bool:
    if report is None:
        return False
    try:
        if isinstance(report, dict) and "results" in report:
            results = report.get("results", [])
            if isinstance(results, list) and len(results) > 0:
                for item in results:
                    if isinstance(item, dict) and "影像学表现" in item and "诊断意见" in item:
                        return True
        return False
    except Exception:
        return False


def _result_count(report: Any) -> int:
    if not isinstance(report, dict):
        return 0
    results = report.get("results", [])
    if isinstance(results, list):
        return len(results)
    return 0


def _check_expected_rule(expected: Dict[str, Any], rules_info: Dict[str, Any]) -> bool:
    expected_rule_id = expected.get("expected_rule_id")
    if not expected_rule_id:
        return True
    matched_ids = rules_info.get("matched_rule_ids", [])
    return expected_rule_id in matched_ids


def _check_wrong_rule(expected: Dict[str, Any], rules_info: Dict[str, Any]) -> bool:
    expected_rule_id = expected.get("expected_rule_id")
    matched_ids = rules_info.get("matched_rule_ids", [])
    if not matched_ids:
        return False
    if expected_rule_id and expected_rule_id in matched_ids:
        return False
    return len(matched_ids) > 0


def _output_chars(report: Any) -> int:
    if report is None:
        return 0
    try:
        return len(json.dumps(report, ensure_ascii=False))
    except Exception:
        return 0


def _extract_specific_terms(report: Any) -> List[str]:
    """提取报告中的具体分级/数值/专业术语。"""
    if report is None:
        return []
    try:
        text = json.dumps(report, ensure_ascii=False)
    except Exception:
        return []
    found: List[str] = []
    for pattern in SPECIFIC_TERM_PATTERNS:
        matches = re.findall(pattern, text)
        found.extend(matches)
    return sorted(set(found))


def _detect_new_specific_terms(
    baseline_terms: List[str],
    rule_terms: List[str],
) -> bool:
    """Rule Enabled 是否比 Baseline 新增了具体分级/数值。"""
    baseline_set = set(baseline_terms)
    rule_set = set(rule_terms)
    return len(rule_set - baseline_set) > 0


DEFAULT_HIT_KS = [1, 3, 5]


def first_relevant_rank(
    retrieved_ids: List[str],
    expected_ids: List[str],
):
    """返回第一个相关结果的 (1-based rank, matched_id)，未找到返回 (None, None)。"""
    for idx, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id in expected_ids:
            return idx, chunk_id
    return None, None


def aggregate_hit_rates(
    ranks: List[int | None],
    ks: List[int],
) -> Dict[int, float]:
    """计算各 k 值的命中率。"""
    n = len(ranks)
    if n == 0:
        return {k: 0.0 for k in ks}
    return {
        k: sum(1 for r in ranks if r is not None and r <= k) / n
        for k in ks
    }


def compute_summary(metrics_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总所有 Case 的指标。"""
    n = len(metrics_list)
    if n == 0:
        return {"cases": 0}

    baseline_success = sum(1 for m in metrics_list if m.get("baseline_success"))
    rule_success = sum(1 for m in metrics_list if m.get("rule_enabled_success"))
    expected_rule_matched = sum(1 for m in metrics_list if m.get("expected_rule_matched"))
    wrong_rule = sum(1 for m in metrics_list if m.get("wrong_rule_matched"))
    needs_fact_review = sum(1 for m in metrics_list if m.get("needs_fact_review"))

    baseline_chars = [m.get("baseline_output_chars", 0) for m in metrics_list]
    rule_chars = [m.get("rule_enabled_output_chars", 0) for m in metrics_list]
    baseline_prompt_chars = [m.get("baseline_prompt_chars", 0) for m in metrics_list]
    rule_prompt_chars = [m.get("rule_enabled_prompt_chars", 0) for m in metrics_list]

    return {
        "cases": n,
        "baseline_success": f"{baseline_success}/{n}",
        "rule_enabled_success": f"{rule_success}/{n}",
        "expected_rule_matched": f"{expected_rule_matched}/{n}",
        "wrong_rule_matched": f"{wrong_rule}/{n}",
        "needs_fact_review": f"{needs_fact_review}/{n}",
        "avg_baseline_output_chars": round(sum(baseline_chars) / n) if n else 0,
        "avg_rule_enabled_output_chars": round(sum(rule_chars) / n) if n else 0,
        "avg_baseline_prompt_chars": round(sum(baseline_prompt_chars) / n) if n else 0,
        "avg_rule_enabled_prompt_chars": round(sum(rule_prompt_chars) / n) if n else 0,
    }