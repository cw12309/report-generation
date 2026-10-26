"""Report 工作台核心服务"""

import logging
import re
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator, Optional

from app.chat.llm_client import chat_stream

from .schemas import (
    ChatMessage,
    GenerateRequest,
    IterateRequest,
    LLMReportOutput,
    ReportContent,
    ReportSession,
    ReportStatus,
    RouteInfo,
)
from .route_resolver import RouteResolver
from .prompt_loader import PromptLoader
from .report_parser import ReportParser, ReportParseError
from .report_validator import ReportValidator

logger = logging.getLogger(__name__)

# 非报告类输入的标准闲聊回复
CHAT_STANDARD_REPLY = "您好！我是您的医疗影像报告助手，请输入阅片结果。"

# LLM 输出 token 预算：
# 报告 JSON 本身约 1300-1600 tokens。关闭思考时 2560 足够。
# 开启思考时 reasoning 在 vLLM 节点会计入 max_tokens（阿里云不计），
# 实测 reasoning 可达 3500+ tokens，若不放大预算会截断报告 JSON（finish_reason=length）。
# 因此开启思考时使用更大的预算，并同步放宽超时（思考显著增加生成时长）。
_MAX_TOKENS_DEFAULT = 2560
_MAX_TOKENS_THINKING = 8192
_TIMEOUT_DEFAULT = 90.0
_TIMEOUT_THINKING = 240.0

# 报告 JSON 的起始标记：开启思考后，部分节点（qwen36-27b / qwen36_27b_lora / qwen38-27b-int4）
# 不返回 reasoning_content，思考文本会直接混入 content 通道，导致前端思考面板收不到事件、
# 且正文渲染被思考前缀污染。以此标记切分：标记之前的正文按"思考"转投 reasoning 通道。
_REPORT_JSON_RE = re.compile(r'\{\s*"report"\s*:')

# 报告 JSON 的规范开头（压缩空白后用于前缀判断）
_REPORT_JSON_OPENING = '{"report":'


def _is_viable_report_prefix(text: str) -> bool:
    """判断从 '{' 开始的片段是否仍可能长成报告 JSON 的开头。

    压缩空白后比对：'{' / '{ "' / '{ "report' / '{ "report":' 均为可行前缀，
    '{ "x' 则已被排除。可行时需继续等待后续字符，避免把报告 JSON 的 '{' 误判为思考。
    """
    compact = re.sub(r"\s+", "", text)
    return _REPORT_JSON_OPENING.startswith(compact)


class _ThinkingContentSplitter:
    """开启思考时切分 content 流：报告 JSON 之前视为思考过程，之后视为报告正文。

    关闭思考时无副作用；模型自带独立 reasoning 通道时，正文首个字符即 `{`，同样立即原样放行。
    """

    def __init__(self, enabled: bool):
        self._enabled = enabled
        self._buf = ""
        self._json_started = False

    def feed(self, chunk: str) -> tuple[str, str]:
        """返回 (reasoning 增量, token 增量)。"""
        if not self._enabled or not chunk:
            return "", chunk
        if self._json_started:
            return "", chunk

        self._buf += chunk
        reasoning_out = ""
        while True:
            match = _REPORT_JSON_RE.search(self._buf)
            if match:
                self._json_started = True
                reasoning_out += self._buf[: match.start()]
                token_part = self._buf[match.start():]
                self._buf = ""
                return reasoning_out, token_part

            brace = self._buf.find("{")
            if brace == -1:
                # 尚无 '{'：全部是思考文本，立即流出
                reasoning_out += self._buf
                self._buf = ""
                return reasoning_out, ""
            if _is_viable_report_prefix(self._buf[brace:]):
                # 该 '{' 仍可能长成报告开头：先流出其之前的思考，保留 '{' 等待后续字符
                reasoning_out += self._buf[:brace]
                self._buf = self._buf[brace:]
                return reasoning_out, ""
            # 该 '{' 已被排除（后续不是 "report"），按思考文本流出并继续向后找
            reasoning_out += self._buf[: brace + 1]
            self._buf = self._buf[brace + 1:]

    def flush(self) -> str:
        """流结束时清空缓冲；未匹配到报告 JSON 的内容按正文返回，避免输出被吞。"""
        pending = self._buf
        self._buf = ""
        return pending

# 医学/影像关键词：命中即认为是报告生成意图
_MEDICAL_KEYWORDS = [
    "椎", "间盘", "脊髓", "硬膜", "椎管", "神经根", "病变", "结节", "肿块", "占位",
    "影", "信号", "突出", "膨出", "脱出", "狭窄", "受压", "水肿", "积液", "增厚",
    "退变", "骨折", "梗死", "出血", "转移", "钙化", "囊肿", "强化", "灶",
    "MRI", "MR", "CT", "超声", "磁共振", "X线", "平扫",
    "腰椎", "颈椎", "胸椎", "颅脑", "脑", "乳腺", "心脏", "心肌", "肝", "肺", "肾", "甲状腺", "前列腺",
]

# 闲聊/问候特征：命中且无医学关键词时走闲聊
_CONVERSATIONAL_PATTERNS = [
    "你好", "您好", "hello", "hi", "在吗", "在不在", "你是谁", "你叫什么",
    "你能做什么", "你会什么", "怎么用", "如何使用", "帮助", "谢谢", "早上好",
    "晚上好", "下午好", "请问你", "介绍一下",
]


class ReportService:
    """报告生成服务"""

    def __init__(self):
        self.route_resolver = RouteResolver()
        self.prompt_loader = PromptLoader()
        self.parser = ReportParser()
        self.validator = ReportValidator()

    @staticmethod
    def _resolve_llm_override(llm_name: str) -> tuple:
        """按名称解析模型覆盖项，返回 (model, base_url, api_key)；无效或为空时返回 ("", "", "")。"""
        if not llm_name:
            return "", "", ""
        from app.config import get_llm_entry_by_name
        entry = get_llm_entry_by_name(llm_name)
        if not entry:
            logger.warning(f"指定的模型不存在: {llm_name}，回退到全局激活模型")
            return "", "", ""
        return entry["model"], entry["base_url"], entry.get("api_key", "")

    async def _call_llm_and_parse(
        self,
        messages: list,
        on_token=None,
        on_notice=None,
        on_reasoning=None,
        model_override: str = "",
        base_url_override: str = "",
        api_key_override: str = "",
        caller: str = "ReportService",
        enable_thinking: bool = False,
    ):
        """调用LLM并解析输出；解析失败时自动重试一次（要求仅输出JSON）。

        Returns:
            (llm_output, raw_output, llm_elapsed, first_token_at)
            first_token_at: 首个非空 token 出现的绝对 perf_counter 时间；未出现则为 None
        """
        first_token_at = None
        # 开启思考时，无独立 reasoning 通道的节点会把思考混入 content，需在此切分回 reasoning
        splitter = _ThinkingContentSplitter(enable_thinking)

        def _dispatch_token(content: str):
            nonlocal first_token_at
            if not content:
                return
            if first_token_at is None:
                first_token_at = time.perf_counter()
            if on_token:
                on_token(content)

        def _emit(evt, data):
            content = data.get("content", "")
            if evt == "token":
                reasoning_part, token_part = splitter.feed(content)
                if reasoning_part and on_reasoning:
                    on_reasoning(reasoning_part)
                if token_part:
                    _dispatch_token(token_part)
            elif evt == "reasoning":
                # 思考内容不计入首字耗时口径（医生等待的是报告正文）
                if on_reasoning:
                    on_reasoning(content)
            elif evt == "notice" and on_notice:
                on_notice(content)

        t_llm = time.perf_counter()
        # 开启思考时放大 token 预算与超时，避免 reasoning 挤占额度导致报告 JSON 被截断
        max_tokens = _MAX_TOKENS_THINKING if enable_thinking else _MAX_TOKENS_DEFAULT
        llm_timeout = _TIMEOUT_THINKING if enable_thinking else _TIMEOUT_DEFAULT
        raw_output = await chat_stream(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
            _emit=_emit,
            debug=True,
            caller=caller,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            timeout=llm_timeout,
            max_retries=0,
            enable_thinking=enable_thinking,
        )
        # 流结束：切分器中仍缓冲的内容（未出现 JSON 标记）按正文补发，避免输出被吞
        pending = splitter.flush()
        if pending:
            _dispatch_token(pending)
        llm_elapsed = time.perf_counter() - t_llm

        try:
            return self.parser.parse(raw_output), raw_output, llm_elapsed, first_token_at
        except ReportParseError as e:
            logger.error(f"{caller} 报告解析失败: {e}; 原始输出前300字: {raw_output[:300]!r}")

        # 重试一次：附上失败输出，强制要求仅输出JSON（不再向前端推流token，避免重复渲染）
        if on_notice:
            on_notice("首次输出格式异常，正在自动重试…")
        retry_messages = messages + [
            {"role": "assistant", "content": raw_output[:1500]},
            {"role": "user", "content": "上面的输出格式不正确。请只输出一个合法JSON对象，不要Markdown、不要代码块、不要解释文字。"},
        ]
        t_llm2 = time.perf_counter()
        raw_output2 = await chat_stream(
            messages=retry_messages,
            max_tokens=_MAX_TOKENS_DEFAULT,
            temperature=0.1,
            _emit=(lambda evt, data: on_notice(data.get("content", "")) if (on_notice and evt == "notice") else None),
            debug=True,
            caller=caller + "_retry",
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            timeout=_TIMEOUT_DEFAULT,
            max_retries=0,
            # 重试仅为修正输出格式，强制关闭思考，避免推理内容再次挤占 max_tokens
            enable_thinking=False,
        )
        llm_elapsed += time.perf_counter() - t_llm2
        return self.parser.parse(raw_output2), raw_output2, llm_elapsed, first_token_at

    async def _repair_validation_errors(
        self,
        messages: list,
        raw_output: str,
        errors: list,
        on_notice=None,
        model_override: str = "",
        base_url_override: str = "",
        api_key_override: str = "",
        caller: str = "ReportService",
    ):
        """校验失败时让 LLM 修正一次：附校验错误与原始输出，要求仅输出修正后的 JSON。

        Returns:
            (llm_output, raw_output)；修正后仍失败则抛出 ReportParseError。
        """
        if on_notice:
            on_notice("报告未通过质量校验，正在自动修正…")
        repair_messages = messages + [
            {"role": "assistant", "content": raw_output[:1500]},
            {
                "role": "user",
                "content": (
                    "上面的报告未通过校验，错误如下：\n"
                    + "\n".join(f"- {e}" for e in errors)
                    + "\n请修正以上问题后，只输出一个合法JSON对象（保持原有格式字段），"
                    "不要Markdown、不要代码块、不要解释文字。"
                ),
            },
        ]
        raw2 = await chat_stream(
            messages=repair_messages,
            max_tokens=_MAX_TOKENS_DEFAULT,
            temperature=0.1,
            _emit=lambda evt, data: None,
            debug=True,
            caller=caller + "_repair",
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            timeout=_TIMEOUT_DEFAULT,
            max_retries=0,
            enable_thinking=False,
        )
        return self.parser.parse(raw2), raw2

    def reload_routes(self):
        """路由配置变更后热重载（resolver + prompt 缓存）"""
        self.route_resolver.reload()
        self.prompt_loader._prompt_cache.clear()
        logger.info(f"路由配置已重载: {len(self.route_resolver.routes)} 个路由")

    def _template_injection(self, route_id: str, doctor_input: str) -> str:
        """模板库匹配注入：命中启用模板时返回范例章节文本并计数，否则空串"""
        try:
            from . import template_admin

            tpl = template_admin.match_template(route_id, doctor_input or "")
            if not tpl:
                return ""
            template_admin.bump_usage(tpl["id"])
            return template_admin.build_injection_block(tpl)
        except Exception as e:
            logger.warning(f"模板库匹配失败（忽略注入）: {e}")
            return ""

    @staticmethod
    def is_report_intent(text: str) -> bool:
        """判断输入是否为报告生成意图（否则走闲聊标准回复）"""
        t = (text or "").strip()
        if not t:
            return False
        has_medical = any(k in t for k in _MEDICAL_KEYWORDS)
        has_conversational = any(p in t for p in _CONVERSATIONAL_PATTERNS)
        if has_conversational and not has_medical:
            return False
        if has_medical:
            return True
        # 较长的描述性文本视为阅片结论
        return len(t) >= 20

    async def generate_report(
        self,
        request: GenerateRequest,
        conversation_id: str,
        emit_event: Optional[callable] = None,
        on_token: Optional[callable] = None,
        on_notice: Optional[callable] = None,
        on_reasoning: Optional[callable] = None,
    ) -> tuple[ReportSession, LLMReportOutput]:
        """生成报告
        
        Args:
            request: 生成请求
            conversation_id: 会话ID
            emit_event: 事件发射器
            on_token: 流式 token 回调（同步函数，接收 str）
            on_notice: 通知回调（同步函数，接收 str，如模型降级提示）
            on_reasoning: 思考过程回调（同步函数，接收 str；仅开启思考时有内容）
            
        Returns:
            (报告会话, LLM输出)
        """
        t_start = time.perf_counter()
        logger.info(f"开始生成报告: conversation_id={conversation_id}")

        # 1. 路由解析
        if emit_event:
            await emit_event("report_status", {"status": "resolving", "message": "正在识别报告类型"})

        route = self.route_resolver.resolve(
            input_text=request.input_text,
            route_id=request.route_id,
        )

        if not route:
            raise ValueError("无法确定报告模板")

        logger.info(f"路由解析完成: {route.name}")

        if emit_event:
            await emit_event("route_resolved", {
                "route_id": route.id,
                "route_name": route.name,
                "prompt_version": "1.0",
            })

        # 2. 构建Prompt
        if emit_event:
            await emit_event("report_status", {"status": "generating", "message": "正在生成报告"})

        system_prompt = self.prompt_loader.get_system_prompt(
            route=route,
            question_round=0,
            operation="generate",
        )

        # 模板库注入：命中本院优秀范例时追加范例章节（风格参照，数值以本次输入为准）
        injection = self._template_injection(route.id, request.input_text)
        if injection:
            system_prompt += injection

        # 模板直选线路：医生未输入阅片结论，仅点选模板按钮触发
        template_only = bool(request.route_id) and not (request.input_text or "").strip()
        user_prompt = self.prompt_loader.build_user_prompt(
            doctor_input=request.input_text,
            operation="generate",
            template_only=template_only,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # 3. 调用LLM（流式 token 透传 + 解析失败自动重试）
        model_override, base_url_override, api_key_override = self._resolve_llm_override(request.llm_name)
        llm_output, raw_output, llm_elapsed, first_token_at = await self._call_llm_and_parse(
            messages=messages,
            on_token=on_token,
            on_notice=on_notice,
            on_reasoning=on_reasoning,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            caller="ReportService.generate",
            enable_thinking=bool(request.enable_thinking),
        )
        logger.info(f"LLM输出长度: {len(raw_output)}, LLM耗时: {llm_elapsed:.2f}s")

        # 4. 校验
        if emit_event:
            await emit_event("report_status", {"status": "validating", "message": "正在校验报告"})

        # 清理模糊不确定表述，替换为常用阴性基线描述
        llm_output.report.imaging_findings = self.validator.sanitize_vague(llm_output.report.imaging_findings)
        llm_output.report.imaging_diagnosis = self.validator.sanitize_vague(llm_output.report.imaging_diagnosis)
        # 归一影像所见为三行纯文本（LLM 连写单段时按行首锚点切分）
        llm_output.report.imaging_findings = self.validator.normalize_findings_lines(llm_output.report.imaging_findings)
        # 纠正追问单选/多选标注（互斥问题强制单选）
        llm_output.questions = self.validator.normalize_questions_select(llm_output.questions)

        # 强制限制追问轮次
        if llm_output.question_round >= 2:
            llm_output.questions = []
            llm_output.can_ask_questions = False

        errors = self.validator.validate(llm_output, question_round=0)
        if errors:
            logger.warning(f"报告校验发现问题: {errors}; 触发一次自动修正")
            try:
                llm_output, raw_output = await self._repair_validation_errors(
                    messages=messages,
                    raw_output=raw_output,
                    errors=errors,
                    on_notice=on_notice,
                    model_override=model_override,
                    base_url_override=base_url_override,
                    api_key_override=api_key_override,
                    caller="ReportService.generate",
                )
                llm_output.report.imaging_findings = self.validator.sanitize_vague(llm_output.report.imaging_findings)
                llm_output.report.imaging_diagnosis = self.validator.sanitize_vague(llm_output.report.imaging_diagnosis)
                llm_output.report.imaging_findings = self.validator.normalize_findings_lines(llm_output.report.imaging_findings)
                llm_output.questions = self.validator.normalize_questions_select(llm_output.questions)
                if llm_output.question_round >= 2:
                    llm_output.questions = []
                    llm_output.can_ask_questions = False
                errors2 = self.validator.validate(llm_output, question_round=0)
                if errors2:
                    logger.warning(f"修正后仍未通过校验，交付原始问题报告: {errors2}")
            except ReportParseError as e:
                logger.error(f"校验修正输出解析失败，沿用首次结果: {e}")

        # 5. 创建会话（含对话记录）
        now = datetime.now().isoformat()
        # 耗时口径：首个 token 出现时间（用户感知等待时间）；无 token 时回退总耗时
        elapsed = (first_token_at - t_start) if first_token_at else (time.perf_counter() - t_start)
        assistant_text = llm_output.assistant_message or (
            "报告已生成，我还有几个问题想确认：" if llm_output.questions else "报告已生成。"
        )
        session = ReportSession(
            report_id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            route_id=route.id,
            route_name=route.name,
            prompt_version="1.0",
            language=request.language,
            status=ReportStatus.COMPLETED,
            question_round=0,
            current_version=1,
            report=llm_output.report,
            questions=llm_output.questions,
            messages=[
                ChatMessage(role="user", content=request.input_text, created_at=now),
                ChatMessage(
                    role="assistant",
                    content=assistant_text,
                    questions=llm_output.questions,
                    created_at=now,
                ),
            ],
            elapsed_seconds=round(elapsed, 2),
            created_at=now,
            updated_at=now,
        )

        logger.info(f"报告生成完成: version={session.current_version}, questions={len(llm_output.questions)}, 总耗时={elapsed:.2f}s")

        return session, llm_output

    async def iterate_report(
        self,
        request: IterateRequest,
        current_session: ReportSession,
        emit_event: Optional[callable] = None,
        on_token: Optional[callable] = None,
        on_notice: Optional[callable] = None,
        on_reasoning: Optional[callable] = None,
    ) -> tuple[ReportSession, LLMReportOutput]:
        """迭代报告
        
        Args:
            request: 迭代请求
            current_session: 当前会话
            emit_event: 事件发射器
            on_token: 流式 token 回调（同步函数，接收 str）
            on_reasoning: 思考过程回调（同步函数，接收 str；仅开启思考时有内容）
            
        Returns:
            (更新后的会话, LLM输出)
        """
        t_start = time.perf_counter()
        logger.info(f"开始迭代报告: conversation_id={current_session.conversation_id}, round={request.question_round}")

        # 1. 获取路由
        route = self.route_resolver.get_route_by_id(current_session.route_id)
        if not route:
            raise ValueError(f"路由不存在: {current_session.route_id}")

        # 2. 构建Prompt
        if emit_event:
            await emit_event("report_status", {"status": "iterating", "message": "正在更新报告"})

        # 追问轮次以服务端会话为准，防止客户端伪造 round 绕过上限
        new_round = current_session.question_round + 1

        system_prompt = self.prompt_loader.get_system_prompt(
            route=route,
            question_round=new_round,
            operation="iterate",
        )

        # 模板库注入：迭代修订同样参照本院优秀范例风格
        injection = self._template_injection(route.id, request.input_text)
        if injection:
            system_prompt += injection

        user_prompt = self.prompt_loader.build_user_prompt(
            doctor_input=request.input_text,
            current_report={
                "imaging_findings": current_session.report.imaging_findings,
                "imaging_diagnosis": current_session.report.imaging_diagnosis,
            },
            operation="iterate",
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # 3. 调用LLM（流式 token 透传 + 解析失败自动重试）
        model_override, base_url_override, api_key_override = self._resolve_llm_override(request.llm_name)
        llm_output, raw_output, llm_elapsed, first_token_at = await self._call_llm_and_parse(
            messages=messages,
            on_token=on_token,
            on_notice=on_notice,
            on_reasoning=on_reasoning,
            model_override=model_override,
            base_url_override=base_url_override,
            api_key_override=api_key_override,
            caller="ReportService.iterate",
            enable_thinking=bool(request.enable_thinking),
        )
        logger.info(f"迭代LLM耗时: {llm_elapsed:.2f}s")

        # 4. 校验
        if emit_event:
            await emit_event("report_status", {"status": "validating", "message": "正在校验报告"})

        # 清理模糊不确定表述，替换为常用阴性基线描述
        llm_output.report.imaging_findings = self.validator.sanitize_vague(llm_output.report.imaging_findings)
        llm_output.report.imaging_diagnosis = self.validator.sanitize_vague(llm_output.report.imaging_diagnosis)
        # 归一影像所见为三行纯文本（LLM 连写单段时按行首锚点切分）
        llm_output.report.imaging_findings = self.validator.normalize_findings_lines(llm_output.report.imaging_findings)
        # 纠正追问单选/多选标注（互斥问题强制单选）
        llm_output.questions = self.validator.normalize_questions_select(llm_output.questions)

        # 强制限制追问轮次
        if new_round >= 2:
            llm_output.questions = []
            llm_output.can_ask_questions = False

        errors = self.validator.validate(llm_output, question_round=new_round)
        if errors:
            logger.warning(f"迭代报告校验发现问题: {errors}; 触发一次自动修正")
            try:
                llm_output, raw_output = await self._repair_validation_errors(
                    messages=messages,
                    raw_output=raw_output,
                    errors=errors,
                    on_notice=on_notice,
                    model_override=model_override,
                    base_url_override=base_url_override,
                    api_key_override=api_key_override,
                    caller="ReportService.iterate",
                )
                llm_output.report.imaging_findings = self.validator.sanitize_vague(llm_output.report.imaging_findings)
                llm_output.report.imaging_diagnosis = self.validator.sanitize_vague(llm_output.report.imaging_diagnosis)
                llm_output.report.imaging_findings = self.validator.normalize_findings_lines(llm_output.report.imaging_findings)
                llm_output.questions = self.validator.normalize_questions_select(llm_output.questions)
                if new_round >= 2:
                    llm_output.questions = []
                    llm_output.can_ask_questions = False
                errors2 = self.validator.validate(llm_output, question_round=new_round)
                if errors2:
                    logger.warning(f"修正后仍未通过校验，交付原始问题报告: {errors2}")
            except ReportParseError as e:
                logger.error(f"校验修正输出解析失败，沿用首次结果: {e}")

        # 5. 更新会话（追加对话记录）
        # 耗时口径：首个 token 出现时间（用户感知等待时间）；无 token 时回退总耗时
        elapsed = (first_token_at - t_start) if first_token_at else (time.perf_counter() - t_start)
        now = datetime.now().isoformat()
        current_session.report = llm_output.report
        current_session.questions = llm_output.questions
        current_session.question_round = new_round
        current_session.current_version += 1
        current_session.status = ReportStatus.COMPLETED
        current_session.elapsed_seconds = round(elapsed, 2)
        current_session.updated_at = now
        if current_session.messages is None:
            current_session.messages = []
        current_session.messages.append(
            ChatMessage(role="user", content=request.input_text, created_at=now)
        )
        current_session.messages.append(
            ChatMessage(
                role="assistant",
                content=llm_output.assistant_message or (
                    "报告已更新，我还有几个问题：" if llm_output.questions else "报告已更新。"
                ),
                questions=llm_output.questions,
                created_at=now,
            )
        )

        logger.info(f"报告迭代完成: version={current_session.current_version}, round={new_round}, questions={len(llm_output.questions)}")

        return current_session, llm_output
