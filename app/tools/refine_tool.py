"""报告重写工具

Schema: refine_report
- 根据用户指令调整报告风格/详细程度/语气
- 跳过 RAG 检索，直接基于已有报告文本处理
- 保证医学内容主干不变
- 注入 EntityTracker 当前会话上下文
- 返回重写后的报告 JSON
"""

import json
import logging
from typing import Callable, Optional

from chat.report_schema import (
    REPORT_SCHEMA_VERSION,
    ReportSchemaError,
    parse_report_output,
    parse_report_with_repair,
    serialize_report,
    validate_refine_preservation,
)
from langsmith import traceable

logger = logging.getLogger(__name__)

REFINE_REPORT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "refine_report",
        "description": (
            "重写已有报告的风格、详细程度或语气，不修改医学内容。"
            "适用于：用户要求'写详细点'、'简洁一点'、'换种语气'、'补充鉴别诊断'、'润色一下'等。"
            "注意：此工具不检索新知识，仅基于已有报告文本进行重写，医学诊断结论和关键数值必须原样保留。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "style": {
                    "type": "string",
                    "description": "仅调整表达风格的指令，如：'更详细'、'简洁一点'、'正式一点'、'润色'",
                },
            },
            "required": ["style"],
            "additionalProperties": False,
        },
    },
}


def create_refine_report_handler(
    chat_fn: Callable,
    entity_tracker,
    _emit_fn: Optional[Callable] = None,
    last_report: Optional[list] = None,
) -> Callable[[dict], str]:
    """创建 refine_report 工具的处理函数。

    Args:
        chat_fn: LLM 对话函数，签名 (messages, max_tokens, temperature, _emit, debug, caller) -> str
        entity_tracker: EntityTracker 实例，用于注入上下文
        _emit_fn: SSE 事件发射器
        last_report: Session 中上一轮报告的可变引用列表；它是唯一可信输入

    Returns:
        handler: 签名为 (arguments: dict) -> str 的处理函数
    """

    @traceable(run_type="tool", name="Tool_Refine_Report")
    async def handler(arguments: dict) -> str:
        style = arguments.get("style", "")

        # 永远忽略模型参数中的 current_report；只信任 Session Last Report。
        if not last_report or not last_report[0]:
            return json.dumps(
                {"error": "当前会话没有可润色的报告", "_is_final": False},
                ensure_ascii=False,
            )
        if not style:
            return json.dumps(
                {"error": "缺少 style 参数，请提供重写指令", "_is_final": False},
                ensure_ascii=False,
            )

        logger.info(
            "refine_report start: last_report_present=true schema_version=%s",
            REPORT_SCHEMA_VERSION,
        )

        try:
            old_json = parse_report_output(last_report[0])
            current_report = serialize_report(old_json, include_reasoning=False)

            from prompt.system_prompt_builder import PromptBuilder

            sys_prompt = PromptBuilder.build(
                "report_prompt/refine",
                entity_context=entity_tracker.to_context_prompt() if entity_tracker else None,
            )

            messages = [
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": (
                        f"当前报告：\n{current_report}\n\n"
                        f"重写指令：{style}\n\n"
                        f"请按 JSON 格式输出重写后的完整报告，保持医学内容不变，仅调整风格/表达方式。"
                    ),
                },
            ]

            if _emit_fn:
                _emit_fn("status", {"message": "正在重写报告...", "phase": "refining"})

            output = await chat_fn(
                messages,
                max_tokens=2048,
                temperature=0.5,
                _emit=None,
                debug=True,
                caller="refine_report_tool",
            )

            async def repair(repair_messages):
                return await chat_fn(
                    repair_messages,
                    max_tokens=2048,
                    temperature=0,
                    _emit=None,
                    debug=True,
                    caller="refine_report_repair",
                )

            result, repair_attempted = await parse_report_with_repair(output, repair)
            validate_refine_preservation(old_json, result)
            logger.info(
                "refine_report validated: parse_status=valid repair_attempted=%s schema_version=%s",
                repair_attempted,
                REPORT_SCHEMA_VERSION,
            )

            reasoning = result.get("reasoning", "")
            if reasoning and _emit_fn:
                _emit_fn("reasoning", {"text": reasoning})
            result["_is_final"] = True

            result_str = json.dumps(result, ensure_ascii=False, indent=2)
            if _emit_fn:
                _emit_fn("status", {"message": "报告重写完成", "phase": "done"})
            logger.info("refine_report 完成: 报告长度 %d, is_final=True", len(result_str))
            return result_str

        except ReportSchemaError as exc:
            logger.warning("refine_report validation failed: %s", exc)
            return json.dumps(
                {"error": "报告润色失败，请重试", "_is_final": False},
                ensure_ascii=False,
            )
        except Exception:
            logger.error("refine_report 执行失败", exc_info=True)
            return json.dumps(
                {"error": "报告润色失败，请重试", "_is_final": False},
                ensure_ascii=False,
            )

    return handler