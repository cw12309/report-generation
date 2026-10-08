"""Report 工作台数据模型"""

from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class ReportStatus(str, Enum):
    """报告状态"""
    IDLE = "idle"
    RESOLVING = "resolving"
    GENERATING = "generating"
    WAITING_INPUT = "waiting_input"
    ITERATING = "iterating"
    VALIDATING = "validating"
    SAVING = "saving"
    COMPLETED = "completed"
    FAILED = "failed"


class ReportOperation(str, Enum):
    """报告操作类型"""
    GENERATE = "generate"
    ITERATE = "iterate"
    EDIT_FINDINGS = "edit_findings"
    EDIT_DIAGNOSIS = "edit_diagnosis"
    REFINE = "refine"
    EXPLAIN = "explain"


class ReportContent(BaseModel):
    """报告内容"""
    imaging_findings: str = Field(default="", description="影像所见")
    imaging_diagnosis: str = Field(default="", description="影像诊断")


class ReportQuestion(BaseModel):
    """追问问题"""
    id: str = Field(..., description="问题ID")
    question: str = Field(..., description="问题内容")
    target: Literal["imaging_findings", "imaging_diagnosis", "both"] = Field(
        default="imaging_findings",
        description="目标章节"
    )
    priority: Literal["high", "medium", "low"] = Field(
        default="medium",
        description="优先级"
    )
    options: list[str] = Field(default_factory=list, description="候选选项（供医生点选快速回答）")
    multi_select: bool = Field(
        default=False,
        description="是否允许多选：互斥答案(侧别/分级/程度)为false，可并存答案(多节段/多部位)为true"
    )
    answered: bool = Field(default=False, description="是否已回答")


class ChatMessage(BaseModel):
    """对话记录消息"""
    role: str = Field(..., description="角色: user/assistant")
    content: str = Field(default="", description="消息内容")
    questions: list[ReportQuestion] = Field(default_factory=list, description="该条助手消息附带的追问列表")
    created_at: str = Field(default="", description="时间戳")


class ReportSession(BaseModel):
    """报告会话"""
    report_id: str = Field(..., description="报告ID")
    conversation_id: str = Field(..., description="会话ID")
    route_id: str = Field(..., description="路由ID")
    route_name: str = Field(..., description="路由名称")
    prompt_version: str = Field(default="1.0", description="Prompt版本")
    language: str = Field(default="zh-CN", description="语言")
    status: ReportStatus = Field(default=ReportStatus.IDLE, description="状态")
    question_round: int = Field(default=0, description="追问轮次")
    max_question_rounds: int = Field(default=2, description="最大追问轮次")
    current_version: int = Field(default=0, description="当前版本")
    report: ReportContent = Field(default_factory=ReportContent, description="报告内容")
    questions: list[ReportQuestion] = Field(default_factory=list, description="追问列表")
    messages: list[ChatMessage] = Field(default_factory=list, description="对话记录（用户输入与AI回复）")
    is_dirty: bool = Field(default=False, description="是否有未保存修改")
    elapsed_seconds: float = Field(default=0.0, description="完整生成耗时（秒）")
    created_at: str = Field(..., description="创建时间")
    updated_at: str = Field(..., description="更新时间")


class GenerateRequest(BaseModel):
    """生成报告请求"""
    input_text: str = Field(..., description="医生输入的阅片结论")
    route_id: Optional[str] = Field(None, description="指定的路由ID")
    language: str = Field(default="zh-CN", description="语言")
    images: list[dict] = Field(default_factory=list, description="上传的图片")
    llm_name: str = Field(default="", description="指定使用的模型名称（为空则用全局激活模型）")
    enable_thinking: bool = Field(default=False, description="是否开启模型思考（默认关闭）")


class IterateRequest(BaseModel):
    """迭代报告请求"""
    input_text: str = Field(..., description="补充内容或修改指令")
    images: list[dict] = Field(default_factory=list, description="上传的图片")
    base_version: int = Field(..., description="基于的版本号")
    question_round: int = Field(..., description="当前追问轮次")
    language: str = Field(default="zh-CN", description="语言")
    llm_name: str = Field(default="", description="指定使用的模型名称（为空则用全局激活模型）")
    enable_thinking: bool = Field(default=False, description="是否开启模型思考（默认关闭）")


class SaveRequest(BaseModel):
    """保存报告请求"""
    imaging_findings: str = Field(..., description="影像所见")
    imaging_diagnosis: str = Field(..., description="影像诊断")
    base_version: int = Field(..., description="基于的版本号")
    source: str = Field(default="manual_edit", description="来源")


class ReportResponse(BaseModel):
    """报告响应"""
    conversation_id: str
    report: Optional[ReportSession] = None


class RouteInfo(BaseModel):
    """路由信息"""
    id: str = Field(..., description="路由ID")
    name: str = Field(..., description="路由名称")
    aliases: list[str] = Field(default_factory=list, description="别名列表")
    modalities: list[str] = Field(default_factory=list, description="检查类型关键词")
    body_parts: list[str] = Field(default_factory=list, description="部位关键词")
    prompt_file: str = Field(..., description="Prompt文件")
    template_source: Optional[str] = Field(default=None, description="模板源文件")
    quick_tags: list[str] = Field(default_factory=list, description="快捷输入标签（点选插入标准术语）")
    enabled: bool = Field(default=True, description="是否启用")


class LLMReportOutput(BaseModel):
    """LLM 输出的报告结构"""
    report: ReportContent = Field(..., description="报告内容")
    questions: list[ReportQuestion] = Field(default_factory=list, description="追问列表")
    question_round: int = Field(..., description="当前追问轮次")
    can_ask_questions: bool = Field(..., description="是否可以继续追问")
    changed_sections: list[str] = Field(default_factory=list, description="修改的章节")
    assistant_message: str = Field(default="", description="助手消息")
