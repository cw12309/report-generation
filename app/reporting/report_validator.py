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
    # 括号式模糊注记：（…待明确/待确认/待评估/待定/待排…），如"（具体节段、类型及程度待明确）"
    _VAGUE_PAREN = re.compile(
        r"[（(][^（）()\n]{0,40}?(?:待明确|待确认|待评估|待定|待排)[^（）()\n]{0,40}?[）)]"
    )

    # 范式-格式一致性检查关键词
    # 强占位/恶性征象（所见出现即应归纳式）
    _OCC_STRONG = ("占位", "转移", "癌", "肉瘤", "淋巴瘤", "骨髓瘤", "骨质破坏", "破坏性", "毛刺", "廓清")
    # 弱占位词（仅用于排除"退变误用归纳式"的判定，不单独触发归纳式要求）
    _OCC_WEAK = ("肿块", "肿瘤", "mass", "MASS")
    # 退变/良性征象（所见仅含这些且无占位词时，诊断不应为归纳式）
    _DEGEN = ("退变", "膨出", "突出", "脱出", "狭窄", "增生", "终板炎", "Modic", "Pfirrmann",
              "白质高信号", "软化灶", "囊肿", "纤维腺瘤", "炎", "骨折")
    # 归纳式诊断标记
    _INDUCTIVE_MARKERS = ("①", "首先考虑")
    # 专业建议结论信号词（演绎式句末/归纳式第3行必须含其一）
    _ADVISE_KEYWORDS = ("建议", "随访", "会诊", "就诊", "筛查", "活检", "干预", "复查")

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
            "（具体节段、类型及程度待明确）" → 删除该括号注记
        """
        if not text:
            return text
        result = cls._VAGUE_CLAUSE.sub("", text)
        # 先剥离括号式模糊注记，避免整句替换规则吞掉括号前的真实征象
        result = cls._VAGUE_PAREN.sub("", result)
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

        # 7. 检查括号式模糊注记（sanitize 应已剥离；残留则触发修正回路）
        for field_name, field_text in (
            ("影像所见", output.report.imaging_findings),
            ("影像诊断", output.report.imaging_diagnosis),
        ):
            m = self._VAGUE_PAREN.search(field_text or "")
            if m:
                errors.append(f"{field_name}包含括号式模糊注记: {m.group(0)}，应以XX占位或删除并追问")

        # 8. 范式-格式一致性检查（演绎式/归纳式与诊断结构必须匹配）
        errors.extend(self._check_paradigm_consistency(
            output.report.imaging_findings, output.report.imaging_diagnosis
        ))

        # 9. 影像所见必须为三行纯文本（病灶病变→组织器官→图像区域）
        findings_lines = [ln.strip() for ln in (output.report.imaging_findings or "").split("\n") if ln.strip()]
        if findings_lines and len(findings_lines) != 3:
            errors.append(
                f"影像所见必须为三行纯文本（第1行病灶病变、第2行组织器官基线、第3行图像区域），当前为{len(findings_lines)}行，"
                "请按三行结构重写并以换行分隔"
            )

        # 10. 影像诊断结尾必须含专业建议结论（演绎式句末 / 归纳式第3行）
        if not self._check_advice_conclusion(output.report.imaging_diagnosis):
            errors.append(
                "影像诊断结尾缺少专业诊疗建议：演绎式须在句末、归纳式须在第3行给出个体化建议"
                "（就诊科室/会诊 + 结合临床处理 + 随访或进一步检查计划，如\"建议骨科门诊结合腰腿疼痛症状对症处理，1年复查腰椎MRI\"），"
                "严禁\"建议结合临床\"\"建议随访\"等空泛表述"
            )

        return errors

    @classmethod
    def _check_advice_conclusion(cls, diagnosis: str) -> bool:
        """影像诊断结尾是否含专业建议结论信号词。

        归纳式取第3行（下一步诊疗方向），演绎式取末句（句末续写的诊疗建议）。
        """
        diagnosis = diagnosis or ""
        if not diagnosis:
            return False
        lines = [ln.strip() for ln in diagnosis.split("\n") if ln.strip()]
        tail = lines[-1] if len(lines) >= 3 else diagnosis
        return any(k in tail for k in cls._ADVISE_KEYWORDS)

    # 否定前缀：否定词(可带程度修饰)紧邻关键词时视为否定表述
    _NEG_PREFIX = re.compile(r"(?:无|未见|不伴|否认)(?:明显|显著|明确|确切|活动性)?$")

    # 影像所见三行结构的行首锚点（组织器官水平 / 图像区域水平），均为句首完整短语
    _ORGAN_ANCHORS = (
        "腰椎序列规整", "腰骶椎生理曲度", "颈椎序列规整", "颅颈交界", "胸椎序列规整",
        "双侧大脑半球对称", "中线结构居中",
        "本次检查图像质量", "图像质量", "心脏整体形态", "心脏形态规整",
        "双侧乳腺对称", "双侧乳腺呈", "纤维腺体呈",
        "肝形态规整", "双肺纹理清晰", "气管支气管通畅",
    )
    _REGION_ANCHORS = (
        "扫描范围内", "扫描野内", "左侧腋窝", "右侧腋窝", "双侧腋窝",
        "双侧上颌窦", "左侧上颌窦", "右侧上颌窦", "眼眶、乳突", "甲状腺", "鼻窦、眼眶",
    )

    # 互斥（单选）问题关键词：程度/分级/侧别/强化特点等答案唯一
    _EXCLUSIVE_Q_KEYWORDS = (
        "程度", "分级", "强化特点", "强化特征", "强化模式", "强化分布",
        "侧别", "分型", "实测", "数值", "曲线类型", "TIC", "移位", "狭窄",
    )
    # 可并存（多选）问题关键词：受累节段/部位/并存征象等可同时成立
    _MULTI_Q_KEYWORDS = (
        "受累节段", "受累部位", "受累区域", "病灶定位", "分布区域", "并存征象", "并存", "多发灶",
    )

    @classmethod
    def normalize_questions_select(cls, questions) -> list:
        """确定性纠正 multi_select：互斥问题（程度/分级/侧别/强化特点/狭窄等）强制单选，
        并剥离 question 文本中的"（可多选）"；可并存问题保持多选。"""
        for q in questions or []:
            text = q.question or ""
            if any(k in text for k in cls._MULTI_Q_KEYWORDS):
                continue
            if getattr(q, "multi_select", False) and any(k in text for k in cls._EXCLUSIVE_Q_KEYWORDS):
                q.multi_select = False
                q.question = (
                    text.replace("（可多选）", "").replace("(可多选)", "").strip()
                )
        return questions

    @classmethod
    def _first_anchor(cls, text: str, anchors, start: int) -> int:
        """返回 anchors 中在 text[start:] 最早出现的行首锚点下标，未命中返回 -1。

        锚点必须位于句边界（句首，即紧邻"。"/"；"之后或文本开头），
        避免把"腰椎序列规整"从词中切断成"…腰椎 / 序列规整…"。
        """
        best = -1
        for a in anchors:
            if start <= 0 and text.startswith(a):
                if best < 0:
                    best = 0
                continue
            for sep in ("。", "；"):
                p = text.find(sep + a, max(0, start - 1))
                if p >= 0:
                    idx = p + 1
                    if idx >= start and (best < 0 or idx < best):
                        best = idx
        return best

    @classmethod
    def normalize_findings_lines(cls, text: str) -> str:
        """将影像所见归一为三行纯文本（病灶病变→组织器官→图像区域）。

        LLM 偶发将三行连写成单段：按行首锚点确定性切分为三行；
        已为多行时仅清理空行，保持原样。锚点缺失无法切分时原样返回（由 validate 报错触发修正回路）。
        """
        if not text:
            return text
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if len(lines) >= 3:
            return "\n".join(lines)
        flat = "".join(lines)
        i1 = cls._first_anchor(flat, cls._ORGAN_ANCHORS, 1)
        i2 = cls._first_anchor(flat, cls._REGION_ANCHORS, i1 + 1 if i1 > 0 else 1)
        if i1 <= 0 or i2 <= i1:
            return "\n".join(lines)
        return "\n".join([flat[:i1].strip(), flat[i1:i2].strip(), flat[i2:].strip()])

    @classmethod
    def _has_keyword(cls, text: str, keywords) -> bool:
        """关键词命中判定，排除否定表述（如"无占位效应""未见明显肿块"中的关键词不计入）。"""
        for kw in keywords:
            start = 0
            while True:
                idx = text.find(kw, start)
                if idx < 0:
                    break
                if not cls._NEG_PREFIX.search(text[max(0, idx - 8):idx]):
                    return True
                start = idx + len(kw)
        return False

    @classmethod
    def _check_paradigm_consistency(cls, findings: str, diagnosis: str) -> list[str]:
        """校验影像诊断格式与病变性质所选范式是否一致。

        - 所见含强占位/恶性征象 → 诊断必须为归纳式（含①/首先考虑标记）
        - 诊断含归纳式标记 → 必须为三行结构（定性概括/概率排序鉴别/诊疗方向）
        - 所见仅退变/良性征象且无占位词 → 诊断不应为归纳式三行结构
        """
        errors: list[str] = []
        findings = findings or ""
        diagnosis = diagnosis or ""
        if not findings or not diagnosis:
            return errors

        has_occ = cls._has_keyword(findings, cls._OCC_STRONG) or cls._has_keyword(findings, cls._OCC_WEAK)
        has_strong_occ = cls._has_keyword(findings, cls._OCC_STRONG)
        has_degen = cls._has_keyword(findings, cls._DEGEN)
        has_marker = any(k in diagnosis for k in cls._INDUCTIVE_MARKERS)
        diag_lines = [ln.strip() for ln in diagnosis.split("\n") if ln.strip()]

        if has_strong_occ and not has_marker:
            errors.append(
                "影像所见含占位/恶性征象（占位/转移/癌/骨质破坏/毛刺等），影像诊断应使用归纳式三行结构"
                "（第1行定性概括、第2行①首先考虑待鉴别③待排除、第3行诊疗方向），当前为演绎式单段"
            )
        if has_marker and len(diag_lines) < 3:
            errors.append(
                f"影像诊断使用了归纳式标记（①/首先考虑）但仅{len(diag_lines)}行，"
                "必须为三行纯文本：第1行定性概括、第2行①首先考虑②待鉴别③待排除、第3行诊疗方向"
            )
        if has_degen and not has_occ and has_marker:
            errors.append(
                "影像所见仅含退变/良性征象且无占位性病变，影像诊断应使用演绎式（单段连贯：定性诊断+关键征象+建议），"
                "不应使用归纳式三行概率排序结构"
            )
        return errors

    def is_valid(self, output: LLMReportOutput, question_round: int) -> bool:
        """判断是否通过校验"""
        errors = self.validate(output, question_round)
        return len(errors) == 0
