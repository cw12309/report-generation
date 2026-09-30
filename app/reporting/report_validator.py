"""报告校验器"""

import logging
import re

from .schemas import LLMReportOutput

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """校验错误"""
    pass


class ReportValidator:
    """报告校验器"""

    # 模糊表述清理规则
    _VAGUE_CLAUSE = re.compile(r"[，,]\s*[^。；\n，,]*需结合[^。；\n，,]*确认")
    _VAGUE_SENTENCE = re.compile(r"[^。；\n]*(?:待明确|待评估|待确认|具体情况待定|有待进一步检查)[。；]?")
    _VAGUE_STANDALONE = re.compile(r"[。；]?\s*[^。；\n，,]*需结合[^。；\n，,]*确认[。；]?")

    @classmethod
    def _vague_replacement(cls, match: re.Match) -> str:
        """将含'待明确/待评估'的整句替换为对应阴性基线描述"""
        sent = match.group(0)
        if "神经根" in sent:
            return "神经根未见明显受压。"
        if ("椎管" in sent) or ("椎间孔" in sent) or ("狭窄" in sent):
            return "椎管及椎间孔未见明显狭窄。"
        if ("硬膜囊" in sent) or ("脊髓" in sent) or ("马尾" in sent):
            return "硬膜囊形态规整，未见明显受压。"
        return ""

    @classmethod
    def sanitize_vague(cls, text: str) -> str:
        """清理报告中的模糊不确定表述，替换为常用阴性基线描述。

        例："具体节段及突出程度需结合详细影像确认" → 删除该从句；
            "神经根受压情况待明确。" → "神经根未见明显受压。"
        """
        if not text:
            return text
        result = cls._VAGUE_CLAUSE.sub("", text)
        result = cls._VAGUE_SENTENCE.sub(cls._vague_replacement, result)
        result = cls._VAGUE_STANDALONE.sub("", result)
        # 清理残留的重复/悬挂标点
        result = re.sub(r"[，,]\s*[。；]", "。", result)
        result = re.sub(r"[。；]{2,}", "。", result)
        result = re.sub(r"\n\s*[，,]", "\n", result)
        return result.strip()

    def validate(self, output: LLMReportOutput, question_round: int) -> list[str]:
        """校验报告
        
        Args:
            output: 解析后的报告输出
            question_round: 期望的追问轮次
            
        Returns:
            错误列表，空列表表示通过校验
        """
        errors = []

        # 1. 检查必填字段
        if not output.report.imaging_findings:
            errors.append("影像所见为空")
        
        if not output.report.imaging_diagnosis:
            errors.append("影像诊断为空")

        # 2. 检查禁用词
        forbidden_words = ["待补充", "未提供", "未描述", "XXX", "TBD", "不详"]
        
        for word in forbidden_words:
            if word in output.report.imaging_findings:
                errors.append(f"影像所见包含禁用词: {word}")
            if word in output.report.imaging_diagnosis:
                errors.append(f"影像诊断包含禁用词: {word}")

        # 3. 检查追问轮次限制
        max_rounds = 2
        if question_round >= max_rounds:
            if output.questions:
                errors.append(f"已达最大追问轮次({max_rounds})，不应再生成追问")
            if output.can_ask_questions:
                errors.append(f"已达最大追问轮次，can_ask_questions应为false")

        # 4. 检查追问数量
        if len(output.questions) > 3:
            errors.append(f"追问数量超限: {len(output.questions)} > 3")

        # 5. 检查内容长度
        if len(output.report.imaging_findings) < 20:
            errors.append("影像所见内容过短")
        
        if len(output.report.imaging_diagnosis) < 10:
            errors.append("影像诊断内容过短")

        # 6. 检查影像诊断中是否有过长的重复描述
        diagnosis = output.report.imaging_diagnosis
        if len(diagnosis) > 500:
            # 检查是否包含大量T1/T2信号描述
            signal_pattern = r"T[12](?:WI)?.*?(?:高|低|等)信号"
            signal_matches = re.findall(signal_pattern, diagnosis)
            if len(signal_matches) > 3:
                logger.warning("影像诊断包含过多信号描述，建议简化")

        return errors

    def is_valid(self, output: LLMReportOutput, question_round: int) -> bool:
        """判断是否通过校验"""
        errors = self.validate(output, question_round)
        return len(errors) == 0
