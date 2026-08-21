"""A/B Evaluation Runner for Report Generation.

Usage:
    python -m app.eval.run_ab_eval                    # run all cases
    python -m app.eval.run_ab_eval --case-id lumbar_001  # run single case
    python -m app.eval.run_ab_eval --limit 3             # run first 3 cases
    python -m app.eval.run_ab_eval --save-prompts         # save full prompts in output
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("eval")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# ensure app/ is on sys.path
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from memory.entity_tracker import EntityTracker
from prompt.system_prompt_builder import PromptBuilder
from prompt.structure_rules_loader import StructureRulesLoader
from chat.report_schema import parse_report_output, ReportSchemaError
from eval.metrics import compute_metrics, compute_summary

_EVAL_DIR = Path(__file__).resolve().parent
_CASES_DIR = _EVAL_DIR / "cases"
_OUTPUTS_DIR = _EVAL_DIR / "outputs"
_DEFAULT_CASES_FILE = _CASES_DIR / "report_generation_cases.json"


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _resolve_entity_tracker(case: Dict[str, Any]) -> EntityTracker:
    """根据 case 的 expected 字段构造 EntityTracker 上下文。"""
    tracker = EntityTracker()
    expected = case.get("expected", {})
    if expected.get("modality"):
        tracker.slots["modality"] = expected["modality"]
    if expected.get("body_part"):
        tracker.slots["body_part"] = list(expected["body_part"])
    return tracker


async def _generate_with_fixed_rag(
    sys_prompt: str,
    rag_result: str,
    chat_fn,
    temperature: float = 0.3,
    caller: str = "eval",
) -> Dict[str, Any]:
    """使用固定 RAG 结果生成报告（eval-only helper，不重构生产代码）。

    Returns:
        {"report": dict | None, "error": str | None, "system_prompt_chars": int}
    """
    user_content = f"检索结果：\n{rag_result}\n\n请按 JSON 格式输出结构化报告。"
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        output = await chat_fn(
            messages,
            max_tokens=2048,
            temperature=temperature,
            debug=True,
            caller=caller,
        )
        report = parse_report_output(output)
        return {
            "report": report,
            "error": None,
            "system_prompt_chars": len(sys_prompt),
        }
    except ReportSchemaError as e:
        logger.warning("[%s] 报告解析失败: %s", caller, e)
        return {
            "report": None,
            "error": f"ReportSchemaError: {e}",
            "system_prompt_chars": len(sys_prompt),
        }
    except Exception as e:
        logger.exception("[%s] 生成失败", caller)
        return {
            "report": None,
            "error": f"{type(e).__name__}: {e}",
            "system_prompt_chars": len(sys_prompt),
        }


async def run_single_case(
    case: Dict[str, Any],
    chat_fn,
    rag_fn,
    save_prompts: bool = False,
) -> Dict[str, Any]:
    """执行单个 Case 的 A/B 评测。

    RAG 只检索一次，Baseline 和 Rule Enabled 共享同一份检索结果。
    """
    case_id = case["id"]
    logger.info("[%s] 开始执行", case_id)

    result: Dict[str, Any] = {
        "case_id": case_id,
        "input": case["input"],
        "context": {},
        "rag": {},
        "rules": {},
        "baseline": {},
        "rule_enabled": {},
        "metrics": {},
    }

    try:
        # 1. Resolve Context once
        tracker = _resolve_entity_tracker(case)
        entity_context = tracker.to_context_prompt()
        result["context"] = {
            "modality": tracker.slots["modality"],
            "body_part": tracker.slots["body_part"],
            "diagnosis": tracker.slots.get("diagnosis", []),
        }
        logger.info("[%s] context: modality=%s body_part=%s",
                     case_id, tracker.slots["modality"], tracker.slots["body_part"])

        # 2. RAG Retrieve once
        if rag_fn:
            try:
                import asyncio
                search_result, reranked_entities = rag_fn(case["input"])
                rag_docs = [
                    {"text": e.get("text", ""), "score": e.get("_rerank_score", 0)}
                    for e in (reranked_entities or [])[:5]
                ]
                result["rag"] = {
                    "documents": rag_docs,
                    "result_count": len(reranked_entities) if reranked_entities else 0,
                }
                logger.info("[%s] RAG: %d documents", case_id, len(reranked_entities) if reranked_entities else 0)
            except Exception as e:
                logger.exception("[%s] RAG 检索失败", case_id)
                result["rag"] = {"error": f"{type(e).__name__}: {e}", "documents": []}
                search_result = "未检索到相关报告。"
                reranked_entities = []
        else:
            search_result = "未检索到相关报告。"
            result["rag"] = {"documents": [], "result_count": 0}

        # 3. Resolve rules once
        try:
            rules_result = StructureRulesLoader.get_rules(
                modality=tracker.slots["modality"] or None,
                body_parts=tracker.slots["body_part"] or [],
            )
            result["rules"] = {
                "normalized_modality": rules_result.normalized_modality,
                "normalized_body_parts": rules_result.normalized_body_parts,
                "matched_rule_ids": rules_result.matched_rule_ids,
                "matched_rule_files": rules_result.matched_rule_files,
                "unmatched_body_parts": rules_result.unmatched_body_parts,
                "content_chars": len(rules_result.content) if rules_result.content else 0,
                "content_hash": _hash_content(rules_result.content) if rules_result.content else "",
            }
            logger.info(
                "[%s] rules: matched=%s unmatched=%s",
                case_id, rules_result.matched_rule_ids, rules_result.unmatched_body_parts,
            )
        except Exception as e:
            logger.exception("[%s] 规则加载失败", case_id)
            result["rules"] = {"error": f"{type(e).__name__}: {e}"}
            rules_result = None

        # 4. Baseline (no body-part rules)
        baseline_prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=entity_context,
            body_part_rules=None,
            last_report=None,
        )
        baseline_output = await _generate_with_fixed_rag(
            sys_prompt=baseline_prompt,
            rag_result=search_result,
            chat_fn=chat_fn,
            caller=f"eval_baseline_{case_id}",
        )
        result["baseline"] = baseline_output
        if save_prompts:
            result["baseline"]["_system_prompt"] = baseline_prompt
        logger.info("[%s] baseline: success=%s chars=%d",
                     case_id, baseline_output["error"] is None, baseline_output["system_prompt_chars"])

        # 5. Rule Enabled (with body-part rules)
        rule_prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=entity_context,
            body_part_rules=rules_result.content if rules_result and rules_result.content else None,
            last_report=None,
        )
        rule_output = await _generate_with_fixed_rag(
            sys_prompt=rule_prompt,
            rag_result=search_result,
            chat_fn=chat_fn,
            caller=f"eval_rule_{case_id}",
        )
        result["rule_enabled"] = rule_output
        if save_prompts:
            result["rule_enabled"]["_system_prompt"] = rule_prompt
        logger.info("[%s] rule_enabled: success=%s chars=%d",
                     case_id, rule_output["error"] is None, rule_output["system_prompt_chars"])

        # 6. Compute metrics
        result["metrics"] = compute_metrics(
            case_id=case_id,
            expected=case.get("expected", {}),
            rules_info=result["rules"],
            baseline=result["baseline"],
            rule_enabled=result["rule_enabled"],
        )

    except Exception as e:
        logger.exception("[%s] Case 执行异常", case_id)
        result["error"] = f"{type(e).__name__}: {e}"

    logger.info("[%s] 完成", case_id)
    return result


def _generate_review_md(run_id: str, results: List[Dict[str, Any]]) -> str:
    """生成人工 Review Markdown 文件。"""
    lines = [
        f"# A/B Evaluation Review — {run_id}",
        "",
        "---",
        "",
    ]

    for r in results:
        case_id = r["case_id"]
        lines.append(f"## {case_id}")
        lines.append("")
        lines.append(f"**输入**: {r['input']}")
        lines.append("")

        # RAG Evidence
        rag = r.get("rag", {})
        docs = rag.get("documents", [])
        if docs:
            lines.append("### RAG Evidence")
            lines.append("")
            for i, doc in enumerate(docs[:3], 1):
                lines.append(f"- **参考{i}** (score={doc.get('score', 0):.4f}): {doc.get('text', '')[:200]}...")
            lines.append("")

        # Baseline
        baseline = r.get("baseline", {})
        lines.append("### Baseline")
        lines.append("")
        if baseline.get("error"):
            lines.append(f"**Error**: {baseline['error']}")
        else:
            report = baseline.get("report", {})
            for result_item in (report.get("results", []) if isinstance(report, dict) else []):
                lines.append(f"**影像学表现**: {result_item.get('影像学表现', '')}")
                lines.append("")
                lines.append(f"**诊断意见**: {result_item.get('诊断意见', '')}")
                lines.append("")
        lines.append("")

        # Rule Enabled
        rule_enabled = r.get("rule_enabled", {})
        lines.append("### Rule Enabled")
        lines.append("")
        if rule_enabled.get("error"):
            lines.append(f"**Error**: {rule_enabled['error']}")
        else:
            report = rule_enabled.get("report", {})
            for result_item in (report.get("results", []) if isinstance(report, dict) else []):
                lines.append(f"**影像学表现**: {result_item.get('影像学表现', '')}")
                lines.append("")
                lines.append(f"**诊断意见**: {result_item.get('诊断意见', '')}")
                lines.append("")
        lines.append("")

        # Auto Metrics
        metrics = r.get("metrics", {})
        lines.append("### Auto Metrics")
        lines.append("")
        lines.append(f"- Baseline success: {metrics.get('baseline_success')}")
        lines.append(f"- Rule success: {metrics.get('rule_enabled_success')}")
        lines.append(f"- Baseline chars: {metrics.get('baseline_output_chars')}")
        lines.append(f"- Rule chars: {metrics.get('rule_enabled_output_chars')}")
        lines.append(f"- Needs fact review: {metrics.get('needs_fact_review')}")
        lines.append("")

        # Human Review template
        lines.append("### Human Review")
        lines.append("")
        lines.append("- 专业性: __/5")
        lines.append("- 完整性: __/5")
        lines.append("- 事实准确性: __/5")
        lines.append("- 术语规范性: __/5")
        lines.append("- 诊断意见质量: __/5")
        lines.append("- 是否出现无依据事实: 是 / 否 / 不确定")
        lines.append("- 更优版本: Baseline / Rule / Tie")
        lines.append("- 备注: ")
        lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


async def run_eval(
    cases: List[Dict[str, Any]],
    chat_fn,
    rag_fn=None,
    save_prompts: bool = False,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """执行全部 Case 的 A/B 评测。"""
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime("eval_%Y%m%d_%H%M%S")

    results: List[Dict[str, Any]] = []
    for i, case in enumerate(cases):
        logger.info("=== Case %d/%d: %s ===", i + 1, len(cases), case["id"])
        result = await run_single_case(
            case=case,
            chat_fn=chat_fn,
            rag_fn=rag_fn,
            save_prompts=save_prompts,
        )
        results.append(result)

    # Compute summary
    all_metrics = [r.get("metrics", {}) for r in results]
    summary = compute_summary(all_metrics)
    logger.info("Summary: %s", json.dumps(summary, ensure_ascii=False, indent=2))

    # Build output
    output: Dict[str, Any] = {
        "run_id": run_id,
        "model": getattr(chat_fn, "__name__", "unknown"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_cases": len(cases),
        "summary": summary,
        "cases": results,
    }

    # Save JSON output
    _OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = _OUTPUTS_DIR / f"{run_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info("Output saved: %s", json_path)

    # Generate review.md
    review_path = _OUTPUTS_DIR / f"{run_id}_review.md"
    review_md = _generate_review_md(run_id, results)
    with open(review_path, "w", encoding="utf-8") as f:
        f.write(review_md)
    logger.info("Review saved: %s", review_path)

    return output


# =============================================================================
# CLI Entry Point
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="A/B Evaluation for Report Generation")
    parser.add_argument("--cases", type=str, default=None,
                        help="Path to cases JSON file (default: app/eval/cases/report_generation_cases.json)")
    parser.add_argument("--case-id", type=str, default=None,
                        help="Run a single case by ID")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit to first N cases")
    parser.add_argument("--output", type=str, default=None,
                        help="Output run ID")
    parser.add_argument("--save-prompts", action="store_true", default=False,
                        help="Save full prompts in output JSON")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Validate cases without calling LLM")

    args = parser.parse_args()

    # Load cases
    cases_path = Path(args.cases) if args.cases else _DEFAULT_CASES_FILE
    if not cases_path.exists():
        logger.error("Cases file not found: %s", cases_path)
        sys.exit(1)

    with open(cases_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases", [])

    # Filter
    if args.case_id:
        cases = [c for c in cases if c["id"] == args.case_id]
        if not cases:
            logger.error("Case not found: %s", args.case_id)
            sys.exit(1)
    if args.limit:
        cases = cases[: args.limit]

    logger.info("Loaded %d cases", len(cases))

    if args.dry_run:
        for case in cases:
            tracker = _resolve_entity_tracker(case)
            logger.info("[%s] input=%s modality=%s body_part=%s",
                         case["id"], case["input"],
                         tracker.slots["modality"], tracker.slots["body_part"])
        logger.info("Dry run complete — %d cases validated", len(cases))
        return

    # Check for real LLM client
    try:
        from chat.llm_client import chat_stream as real_chat_fn
        chat_fn = real_chat_fn
        logger.info("Using real LLM client: chat_stream")
    except ImportError:
        logger.error("Cannot import chat.llm_client — real LLM not available")
        sys.exit(1)

    # Check for real RAG
    rag_fn = None
    try:
        from chat.pipeline import search_reports
        rag_fn = search_reports
        logger.info("Using real RAG: search_reports")
    except ImportError:
        logger.warning("Cannot import search_reports — RAG not available, using empty RAG")

    asyncio.run(run_eval(
        cases=cases,
        chat_fn=chat_fn,
        rag_fn=rag_fn,
        save_prompts=args.save_prompts,
        run_id=args.output,
    ))


if __name__ == "__main__":
    import asyncio
    main()