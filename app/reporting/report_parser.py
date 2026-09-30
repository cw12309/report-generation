"""报告解析器"""

import json
import logging
import re
from typing import Any, Optional

from .schemas import LLMReportOutput, ReportContent, ReportQuestion

logger = logging.getLogger(__name__)


class ReportParseError(Exception):
    """报告解析错误"""
    pass


class ReportParser:
    """报告解析器"""

    def parse(self, raw_output: str) -> LLMReportOutput:
        """解析LLM输出
        
        Args:
            raw_output: LLM原始输出
            
        Returns:
            解析后的结构化报告
            
        Raises:
            ReportParseError: 解析失败
        """
        # 0. 去除思考块（部分模型输出 <think>...</think>）
        cleaned = re.sub(r"<think>.*?</think>", "", raw_output or "", flags=re.DOTALL).strip()

        # 1. 提取JSON
        try:
            json_text = self._extract_json(cleaned)
        except ReportParseError:
            # 兜底：尝试从 Markdown 格式报告（## 影像所见 / ## 影像诊断）解析
            md_output = self._parse_markdown(cleaned)
            if md_output:
                logger.warning("LLM输出非JSON，已从Markdown格式兜底解析")
                return md_output
            raise
        
        # 2. 解析JSON
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
            raise ReportParseError(f"无效的JSON格式: {e}")

        # 3. 验证必要字段
        if "report" not in data:
            raise ReportParseError("缺少report字段")

        report_data = data["report"]
        if "imaging_findings" not in report_data and "imaging_diagnosis" not in report_data:
            raise ReportParseError("报告缺少必要字段")
        if "imaging_findings" not in report_data or "imaging_diagnosis" not in report_data:
            logger.warning("报告章节不完整（可能被截断），使用已有章节")

        # 4. 构建结构化对象
        try:
            report = ReportContent(
                imaging_findings=report_data.get("imaging_findings", ""),
                imaging_diagnosis=report_data.get("imaging_diagnosis", "")
            )

            questions = []
            for q in data.get("questions", []):
                try:
                    questions.append(ReportQuestion(**q))
                except Exception:
                    logger.warning("跳过不完整的追问项")
                    continue

            output = LLMReportOutput(
                report=report,
                questions=questions,
                question_round=data.get("question_round", 0),
                can_ask_questions=data.get("can_ask_questions", False),
                changed_sections=data.get("changed_sections", ["imaging_findings", "imaging_diagnosis"]),
                assistant_message=data.get("assistant_message", "")
            )

            return output

        except Exception as e:
            logger.error(f"构建结构化对象失败: {e}", exc_info=True)
            raise ReportParseError(f"数据格式错误: {e}")

    def _parse_markdown(self, text: str) -> Optional[LLMReportOutput]:
        """兜底解析：LLM 输出 Markdown 报告（含"影像所见"/"影像诊断"标题）时提取两节内容"""
        if not text or ("影像所见" not in text and "影像诊断" not in text):
            return None

        def _section(name: str) -> str:
            m = re.search(
                rf"#[#]*\s*{name}\s*\n(.*?)(?=\n#[#]*\s*(?:影像所见|影像诊断|需补充确认)|\Z)",
                text, re.DOTALL,
            )
            if not m:
                # 无标题行时尝试"影像所见："行内格式
                m = re.search(rf"{name}[：:]\s*(.*?)(?=\n\s*(?:影像所见|影像诊断)[：:]|\Z)", text, re.DOTALL)
            return m.group(1).strip() if m else ""

        findings = _section("影像所见")
        diagnosis = _section("影像诊断")
        if not findings and not diagnosis:
            return None

        return LLMReportOutput(
            report=ReportContent(imaging_findings=findings, imaging_diagnosis=diagnosis),
            questions=[],
            question_round=0,
            can_ask_questions=False,
            changed_sections=["imaging_findings", "imaging_diagnosis"],
            assistant_message="",
        )

    def _extract_json(self, text: str) -> str:
        """提取JSON文本"""
        text = text.strip()

        # 1. 如果整体就是JSON对象
        if text.startswith("{") and text.endswith("}"):
            return text

        # 2. 提取代码块中的JSON：取代码块全文再做括号匹配
        #    （不能用非贪婪 \{.*?\}，否则会在嵌套对象的第一个 } 处截断）
        code_block_match = re.search(r"```(?:json|JSON)?\s*\n?(.*?)```", text, re.DOTALL)
        if code_block_match:
            inner = code_block_match.group(1).strip()
            start = inner.find("{")
            if start != -1:
                extracted = self._brace_match(inner, start)
                if extracted:
                    return extracted

        # 3. 查找第一个JSON对象（字符串感知的括号匹配）
        start_idx = text.find("{")
        if start_idx == -1:
            raise ReportParseError("输出中未找到JSON对象")

        extracted = self._brace_match(text, start_idx)
        if extracted:
            return extracted

        # 4. 括号未闭合：输出被截断，尝试修复
        logger.warning("JSON括号未闭合，尝试截断修复")
        return self._repair_truncated_json(text[start_idx:])

    @staticmethod
    def _brace_match(text: str, start_idx: int) -> Optional[str]:
        """从 start_idx 的 '{' 开始做字符串感知的括号匹配，返回完整JSON对象文本；未闭合返回 None"""
        brace_count = 0
        in_str = False
        esc = False
        for i in range(start_idx, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                brace_count += 1
            elif ch == "}":
                brace_count -= 1
                if brace_count == 0:
                    return text[start_idx:i + 1]
        return None

    def _repair_truncated_json(self, text: str) -> str:
        """修复被截断的JSON：从后往前尝试截断点，补全括号直到可解析"""
        # 收集字符串外的分隔符位置（, { [）
        delims = []
        in_str = False
        esc = False
        for i, ch in enumerate(text):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in ",{[":
                delims.append(i)

        for pos in reversed(delims[-80:]):
            if text[pos] == ",":
                s = text[:pos].rstrip()  # 丢弃不完整的最后一个元素
            else:
                s = text[:pos + 1]  # 保留 { 或 [，稍后闭合为空容器
            while s.endswith(","):
                s = s[:-1].rstrip()
            closed = s + self._closing_brackets(s)
            try:
                json.loads(closed)
                return closed
            except Exception:
                continue

        # 兜底：原简单修复逻辑
        s = text
        if len(re.findall(r'(?<!\\)"', s)) % 2 == 1:
            s = s[:s.rfind('"')]
        s = s.rstrip()
        while s.endswith(","):
            s = s[:-1].rstrip()
        return s + self._closing_brackets(s)

    @staticmethod
    def _closing_brackets(s: str) -> str:
        """计算补全未闭合括号所需的后缀"""
        stack = []
        in_str = False
        esc = False
        for ch in s:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
        return "".join("}" if c == "{" else "]" for c in reversed(stack))
