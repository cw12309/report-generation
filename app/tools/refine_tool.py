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

from tools.utils import extract_json
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
                "current_report": {
                    "type": "string",
                    "description": "当前报告的 JSON 字符串，包含 '影像学表现' 和 '诊断意见' 等字段",
                },
                "style": {
                    "type": "string",
                    "description": "重写风格指令，如：'更详细'、'简洁一点'、'正式一点'、'润色'、'补充鉴别诊断'",
                },
            },
            "required": ["current_report", "style"],
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
        last_report: 上一轮报告的可变引用列表（用于参数容错补全）

    Returns:
        handler: 签名为 (arguments: dict) -> str 的处理函数
    """

    @traceable(run_type="tool", name="Tool_Refine_Report")
    async def handler(arguments: dict) -> str:
        current_report = arguments.get("current_report", "")
        style = arguments.get("style", "")

        # ── 参数容错：从 last_report 自动补全 current_report ──
        if not current_report and last_report and last_report[0]:
            current_report = last_report[0]
            logger.info("refine_report: 从 last_report 自动补全 current_report")

        if not current_report:
            return json.dumps(
                {"error": "缺少 current_report 参数，没有可重写的报告", "_is_final": False},
                ensure_ascii=False,
            )
        if not style:
            return json.dumps(
                {"error": "缺少 style 参数，请提供重写指令", "_is_final": False},
                ensure_ascii=False,
            )

        logger.info("refine_report 开始执行: style=%s", style[:100])

        try:
            old_json = json.loads(current_report)
        except json.JSONDecodeError:
            old_json = {"raw": current_report}

        try:
            from prompt.builder import PromptBuilder

            sys_prompt = PromptBuilder.build(
                "refine",
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

            if _emit_fn:
                _emit_fn("status", {"message": "报告重写完成", "phase": "done"})

            new_json = extract_json(output)

            # 结构保护：Key 集合不应变化
            if isinstance(new_json, dict) and isinstance(old_json, dict):
                if "影像学表现" in new_json and "影像学表现" in old_json:
                    old_keys = set(old_json["影像学表现"].keys())
                    new_keys = set(new_json["影像学表现"].keys())
                    if old_keys != new_keys:
                        logger.warning("REFINE 导致 Key 集合变化：旧 %s → 新 %s，使用旧报告兜底", old_keys, new_keys)
                        old_json["_is_final"] = True
                        return json.dumps(old_json, ensure_ascii=False, indent=2)

            result = new_json if isinstance(new_json, dict) and "error" not in new_json else old_json

            # ── 标记为最终结果，跳过二次 LLM 调用，避免幻觉 ──
            if isinstance(result, dict):
                result["_is_final"] = True

            result_str = json.dumps(result, ensure_ascii=False, indent=2)
            logger.info("refine_report 完成: 报告长度 %d, is_final=True", len(result_str))
            return result_str

        except Exception as e:
            logger.error("refine_report 执行失败: %s", e, exc_info=True)
            return json.dumps(
                {"error": f"重写失败: {str(e)}", "_is_final": False},
                ensure_ascii=False,
            )

    return handler
