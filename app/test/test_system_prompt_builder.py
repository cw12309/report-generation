"""PromptBuilder 回归测试

覆盖:
  - 旧行为保持不变 (body_part_rules 未传)
  - body_part_rules 注入与顺序
  - None / 空字符串 / 纯空格处理
  - 只有规则没有 Last Report / 只有 Last Report 没有规则
  - 其他模板不受影响
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pytest
from prompt.system_prompt_builder import PromptBuilder, BODY_PART_RULES_SECTION_TITLE

ENTITY_MARKER = "## 当前上下文\n当前检查类型: MR\n当前检查部位: 腰椎"
LAST_REPORT_MARKER = "## 已生成的报告"
STRUCTURE_MARKER = "医疗影像报告结构化提取器"
BODY_RULE_MARKER = "BODY_RULE_CONTENT_XYZ"


class TestOldBehaviorPreserved:
    """不传 body_part_rules 时，旧行为完全不变"""

    def test_no_body_part_rules_section(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            last_report=None,
        )
        assert BODY_PART_RULES_SECTION_TITLE not in prompt

    def test_with_last_report_no_rules(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            last_report='{"影像学表现":"测试","诊断意见":"测试"}',
        )
        assert ENTITY_MARKER in prompt
        assert LAST_REPORT_MARKER in prompt
        assert BODY_PART_RULES_SECTION_TITLE not in prompt

    def test_no_entity_no_rules(self):
        prompt = PromptBuilder.build("report_prompt/structure")
        assert BODY_PART_RULES_SECTION_TITLE not in prompt
        assert STRUCTURE_MARKER in prompt


class TestBodyPartRulesInjection:
    """正常注入 body_part_rules"""

    def test_rules_appear_in_prompt(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            body_part_rules=BODY_RULE_MARKER,
            last_report=None,
        )
        assert BODY_RULE_MARKER in prompt
        assert BODY_PART_RULES_SECTION_TITLE in prompt

    def test_rules_section_has_preamble(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            body_part_rules=BODY_RULE_MARKER,
        )
        assert "标准化报告规则" in prompt
        assert "分级标准" in prompt

    def test_rules_with_entity_and_last_report(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            body_part_rules=BODY_RULE_MARKER,
            last_report='{"影像学表现":"测试","诊断意见":"测试"}',
        )
        assert ENTITY_MARKER in prompt
        assert BODY_RULE_MARKER in prompt
        assert LAST_REPORT_MARKER in prompt

    def test_rules_section_stripped(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            body_part_rules="  \n  STRIPPED_RULE_CONTENT  \n  ",
        )
        assert "STRIPPED_RULE_CONTENT" in prompt
        lines = prompt.split("\n")
        last_line = lines[-1].strip() if lines else ""
        assert last_line == "STRIPPED_RULE_CONTENT"


class TestSectionOrder:
    """验证 section 顺序: entity < template < rules < last_report"""

    def test_order_entity_before_template(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
        )
        assert prompt.index(ENTITY_MARKER) < prompt.index(STRUCTURE_MARKER)

    def test_order_template_before_rules(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            body_part_rules=BODY_RULE_MARKER,
        )
        assert prompt.index(STRUCTURE_MARKER) < prompt.index(BODY_RULE_MARKER)

    def test_order_entity_before_rules_before_last_report(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            body_part_rules=BODY_RULE_MARKER,
            last_report='{"影像学表现":"测试","诊断意见":"测试"}',
        )
        pos_entity = prompt.index(ENTITY_MARKER)
        pos_template = prompt.index(STRUCTURE_MARKER)
        pos_rules = prompt.index(BODY_RULE_MARKER)
        pos_last = prompt.index(LAST_REPORT_MARKER)

        assert pos_entity < pos_template
        assert pos_template < pos_rules
        assert pos_rules < pos_last

    def test_order_rules_before_last_report(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            body_part_rules=BODY_RULE_MARKER,
            last_report='{"影像学表现":"测试","诊断意见":"测试"}',
        )
        assert prompt.index(BODY_RULE_MARKER) < prompt.index(LAST_REPORT_MARKER)

    def test_full_order_with_no_entity(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            body_part_rules=BODY_RULE_MARKER,
            last_report='{"影像学表现":"测试","诊断意见":"测试"}',
        )
        assert prompt.index(STRUCTURE_MARKER) < prompt.index(BODY_RULE_MARKER)
        assert prompt.index(BODY_RULE_MARKER) < prompt.index(LAST_REPORT_MARKER)


class TestEmptyRules:
    """body_part_rules 为 None / 空 / 纯空格时不生成 section"""

    def test_none_no_section(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            body_part_rules=None,
        )
        assert BODY_PART_RULES_SECTION_TITLE not in prompt

    def test_empty_string_no_section(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            body_part_rules="",
        )
        assert BODY_PART_RULES_SECTION_TITLE not in prompt

    def test_whitespace_only_no_section(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            body_part_rules="   ",
        )
        assert BODY_PART_RULES_SECTION_TITLE not in prompt

    def test_newline_only_no_section(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            body_part_rules="\n\n",
        )
        assert BODY_PART_RULES_SECTION_TITLE not in prompt


class TestOnlyRulesNoLastReport:
    """只有规则，没有 Last Report"""

    def test_rules_appear_without_last_report(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            body_part_rules=BODY_RULE_MARKER,
            last_report=None,
        )
        assert BODY_RULE_MARKER in prompt
        assert LAST_REPORT_MARKER not in prompt

    def test_rules_appear_without_last_report_and_no_entity(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            body_part_rules=BODY_RULE_MARKER,
        )
        assert BODY_RULE_MARKER in prompt
        assert LAST_REPORT_MARKER not in prompt


class TestOnlyLastReportNoRules:
    """只有 Last Report，没有规则 — 保持现有行为"""

    def test_last_report_without_rules(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            last_report='{"影像学表现":"测试","诊断意见":"测试"}',
        )
        assert LAST_REPORT_MARKER in prompt
        assert BODY_PART_RULES_SECTION_TITLE not in prompt

    def test_last_report_after_template(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            last_report='{"影像学表现":"测试","诊断意见":"测试"}',
        )
        assert prompt.index(STRUCTURE_MARKER) < prompt.index(LAST_REPORT_MARKER)


class TestOtherTemplatesUnaffected:
    """其他模板调用不受影响"""

    def test_agent_prompt_without_rules(self):
        prompt = PromptBuilder.build(
            "agent_prompt/tool_orchestrator",
            entity_context=ENTITY_MARKER,
        )
        assert BODY_PART_RULES_SECTION_TITLE not in prompt
        assert ENTITY_MARKER in prompt

    def test_agent_prompt_with_rules_is_harmless(self):
        prompt = PromptBuilder.build(
            "agent_prompt/tool_orchestrator",
            entity_context=ENTITY_MARKER,
            body_part_rules=BODY_RULE_MARKER,
        )
        assert BODY_RULE_MARKER in prompt
        assert ENTITY_MARKER in prompt

    def test_agent_prompt_with_none_rules(self):
        prompt = PromptBuilder.build(
            "agent_prompt/tool_orchestrator",
            entity_context=ENTITY_MARKER,
            body_part_rules=None,
        )
        assert BODY_PART_RULES_SECTION_TITLE not in prompt
        assert ENTITY_MARKER in prompt


class TestBackwardCompatibility:
    """确保所有现有调用模式不受影响"""

    def test_two_positional_args(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            ENTITY_MARKER,
        )
        assert ENTITY_MARKER in prompt
        assert BODY_PART_RULES_SECTION_TITLE not in prompt

    def test_two_positional_with_last_report(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            ENTITY_MARKER,
            '{"影像学表现":"测试","诊断意见":"测试"}',
        )
        assert ENTITY_MARKER in prompt
        assert LAST_REPORT_MARKER in prompt
        assert BODY_PART_RULES_SECTION_TITLE not in prompt

    def test_keyword_with_last_report_label(self):
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            entity_context=ENTITY_MARKER,
            last_report='{"影像学表现":"测试","诊断意见":"测试"}',
            last_report_label="## 自定义标题",
        )
        assert "自定义标题" in prompt
        assert BODY_PART_RULES_SECTION_TITLE not in prompt


class TestFactualityConstraints:
    """验证 structure.md 中的事实性硬约束内容"""

    def test_factuality_section_exists(self):
        """Case 1: structure.md 中存在明确的事实性硬约束章节"""
        prompt = PromptBuilder.build("report_prompt/structure")
        assert "事实性硬约束" in prompt

    def test_body_part_rules_not_patient_evidence(self):
        """Case 2: 必须包含'部位特化规则不是患者事实证据'语义"""
        prompt = PromptBuilder.build("report_prompt/structure")
        assert "部位特化规则" in prompt
        assert "不是患者事实证据" in prompt or "不属于患者事实证据" in prompt

    def test_no_fabrication_when_evidence_insufficient(self):
        """Case 3: 必须包含'证据不足不得编造'"""
        prompt = PromptBuilder.build("report_prompt/structure")
        assert "不得猜测" in prompt
        assert "不得根据医学常识补齐" in prompt

    def test_allow_judgment_when_evidence_sufficient(self):
        """Case 4: 必须包含'证据充分时允许根据标准进行判定'"""
        prompt = PromptBuilder.build("report_prompt/structure")
        assert "证据充分时允许根据标准进行判定" in prompt

    def test_factuality_before_body_part_rules(self):
        """Case 5: 事实性硬约束出现在 body_part_rules 之前"""
        prompt = PromptBuilder.build(
            "report_prompt/structure",
            body_part_rules="BODY_RULE_MARKER",
        )
        pos_factuality = prompt.index("事实性硬约束")
        pos_body_rules = prompt.index("BODY_RULE_MARKER")
        assert pos_factuality < pos_body_rules, (
            f"事实性硬约束应在部位规则之前，"
            f"但事实性硬约束在 {pos_factuality}，部位规则在 {pos_body_rules}"
        )

    def test_no_body_part_rules_still_works(self):
        """Case 6: 不传 body_part_rules 时旧行为仍然正常"""
        prompt = PromptBuilder.build("report_prompt/structure")
        assert BODY_PART_RULES_SECTION_TITLE not in prompt
        assert "事实性硬约束" in prompt
        assert "输出格式" in prompt