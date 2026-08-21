"""动态 Prompt Builder

统一管理所有 Prompt 的拼装逻辑，消除 rag_tool.py、refine_tool.py、
rag_chat_v2.py 中重复的 Entity 上下文 + Last Report 拼接代码。

拼接顺序（优先级从高到低）：
1. 当前上下文 (Entity)
2. 基础模板 (template)
3. 部位特化规则 (body_part_rules)
4. 已有报告 (Last Report，自动去除 reasoning 字段)
"""

import json
import logging
from typing import Optional
from prompt import load_prompt
from langsmith import traceable

logger = logging.getLogger(__name__)

BODY_PART_RULES_SECTION_TITLE = "## 部位特化报告标准"
BODY_PART_RULES_PREAMBLE = (
    "以下内容是当前检查部位对应的标准化报告规则。"
    "请在不违反事实性约束的前提下，遵循其中的报告结构、描述规范、分级标准和诊断结论规范。"
)


class PromptBuilder:
    """统一 Prompt 拼装器

    使用方式：
        sys_prompt = PromptBuilder.build(
            "structure",
            entity_context=entity_tracker.to_context_prompt(),
            last_report=last_report[0] if last_report else None,
            body_part_rules=rules,
        )
    """

    @staticmethod
    @traceable(run_type="prompt", name="Build_Medical_Prompt")
    def build(
        template_name: str,
        entity_context: Optional[str] = None,
        last_report: Optional[str] = None,
        last_report_label: Optional[str] = None,
        body_part_rules: Optional[str] = None,
    ) -> str:
        """构建完整的系统 Prompt。

        Args:
            template_name: 模板名称（不含 .md 扩展名），如 'structure', 'refine', 'tool_orchestrator'
            entity_context: 实体上下文字符串，由 entity_tracker.to_context_prompt() 生成
            last_report: 上一轮报告 JSON 字符串，会自动去除 reasoning 字段以节省 Token
            last_report_label: 报告段落的标题，默认 "## 已生成的报告（仅参考，请勿重复其中的病变）"
            body_part_rules: 部位特化报告规则字符串，由 StructureRulesLoader 加载，
                             仅注入到 report_prompt/structure 等报告生成模板中

        Returns:
            str: 完整的系统 Prompt 字符串
        """
        parts = []

        # 1. 当前上下文 (Entity) — 告知 LLM 当前检查类型和部位
        if entity_context:
            parts.append(entity_context)

        # 2. 基础模板
        template = load_prompt(template_name)
        if template:
            parts.append(template)
        else:
            logger.warning("模板加载失败: %s，将使用空字符串兜底", template_name)

        # 3. 部位特化报告规则 — 注入当前检查部位的专业写作规范
        if body_part_rules and body_part_rules.strip():
            parts.append(f"{BODY_PART_RULES_SECTION_TITLE}\n\n{BODY_PART_RULES_PREAMBLE}\n\n{body_part_rules.strip()}")

        # 4. 已有报告 (Last Report) — 去除 reasoning 字段以节省 Token
        if last_report:
            try:
                last_obj = json.loads(last_report)
                last_obj.pop("reasoning", None)
                last_text = json.dumps(last_obj, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                last_text = last_report

            label = last_report_label or "## 已生成的报告（包含已有病变，请合并到最终输出，不要丢弃）"
            parts.append(f"{label}\n{last_text}")

        return "\n\n---\n\n".join(parts)