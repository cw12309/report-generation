"""报告编辑工具

Schema: edit_report
- 根据用户指令修改已有报告
- 使用 prompt/edit.md 调用 LLM
- 保证报告结构不被破坏
- 返回修改后的报告 JSON
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
    validate_edit_preservation,
)
from langsmith import traceable

logger = logging.getLogger(__name__)

EDIT_REPORT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_report",
        "description": (
            "修改已有报告中的指定内容。"
            "适用于：用户要求修改报告中的某个字段、删除某个病变、更新某个数值等。"
            "注意：此工具修改已有报告，不会检索新知识。如果用户要求生成新报告，应使用 rag_search。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "用户的修改指令，描述需要修改什么内容。例如：'把脑出血的CT值改为70HU'、'删除右侧基底节区病变'",
                },
            },
            "required": ["instruction"],
            "additionalProperties": False,
        },
    },
}


def create_edit_report_handler(
    chat_fn: Callable,
    _emit_fn: Optional[Callable] = None,
    last_report: Optional[list] = None,
) -> Callable[[dict], str]:
    """创建 edit_report 工具的处理函数。

    Args:
        chat_fn: LLM 对话函数，签名 (messages, max_tokens, temperature, _emit, debug, caller) -> str
        _emit_fn: SSE 事件发射器
        last_report: Session 中上一轮报告的可变引用列表；它是唯一可信输入

    Returns:
        handler: 签名为 (arguments: dict) -> str 的处理函数
    """

    @traceable(run_type="tool", name="Tool_Edit_Report")
    async def handler(arguments: dict) -> str:
        instruction = arguments.get("instruction", "")

        # 永远忽略模型参数中的 current_report；只信任 Session Last Report。
        if not last_report or not last_report[0]:
            return json.dumps(
                {"error": "当前会话没有可修改的报告", "_is_final": False},
                ensure_ascii=False,
            )
        if not instruction:
            return json.dumps(
                {"error": "缺少 instruction 参数，请提供修改指令", "_is_final": False},
                ensure_ascii=False,
            )

        logger.info(
            "edit_report start: last_report_present=true schema_version=%s",
            REPORT_SCHEMA_VERSION,
        )

        try:
            old_json = parse_report_output(last_report[0])
            current_report = serialize_report(old_json, include_reasoning=False)

            from prompt import load_prompt
            EDIT_PROMPT = load_prompt("edit")

            sys_prompt = EDIT_PROMPT
            messages = [
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": f"当前报告：\n{current_report}\n\n修改指令：{instruction}\n\n请按 JSON 格式输出修改后的完整报告。",
                },
            ]

            if _emit_fn:
                _emit_fn("status", {"message": "正在修改报告...", "phase": "editing"})

            output = await chat_fn(
                messages,
                max_tokens=2048,
                temperature=0.3,
                _emit=None,
                debug=True,
                caller="edit_report_tool",
            )

            async def repair(repair_messages):
                return await chat_fn(
                    repair_messages,
                    max_tokens=2048,
                    temperature=0,
                    _emit=None,
                    debug=True,
                    caller="edit_report_repair",
                )

            result, repair_attempted = await parse_report_with_repair(output, repair)
            validate_edit_preservation(old_json, result, instruction)
            logger.info(
                "edit_report validated: parse_status=valid repair_attempted=%s schema_version=%s",
                repair_attempted,
                REPORT_SCHEMA_VERSION,
            )

            reasoning = result.get("reasoning", "")
            if reasoning and _emit_fn:
                _emit_fn("reasoning", {"text": reasoning})
            result["_is_final"] = True

            result_str = json.dumps(result, ensure_ascii=False, indent=2)
            if _emit_fn:
                _emit_fn("status", {"message": "报告修改完成", "phase": "done"})
            logger.info("edit_report 完成: 报告长度 %d, is_final=True", len(result_str))
            return result_str

        except ReportSchemaError as exc:
            logger.warning("edit_report validation failed: %s", exc)
            return json.dumps(
                {"error": "报告编辑失败，请重试", "_is_final": False},
                ensure_ascii=False,
            )
        except Exception:
            logger.error("edit_report 执行失败", exc_info=True)
            return json.dumps(
                {"error": "报告编辑失败，请重试", "_is_final": False},
                ensure_ascii=False,
            )

    return handler
