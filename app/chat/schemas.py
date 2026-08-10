"""Pydantic 数据模型（请求/响应 Schema）"""

from typing import Optional, List, Dict, Any, Union

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# 请求模型
# =============================================================================
class ChatRequest(BaseModel):
    """对话请求"""
    query: str = Field(..., description="用户输入内容")
    session_id: str = Field(default="default", description="会话 ID")
    selected_diagnosis: Optional[str] = Field(default=None, description="选择的诊断（歧义场景）")
    selected_kbs: List[str] = Field(default_factory=list, description="本轮检索的知识库 ID")


class DeploymentCreateRequest(BaseModel):
    """创建一个固定知识库范围的 Stateful Agent Deployment。"""
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    selected_kbs: List[str] = Field(..., min_length=1)


class DeploymentUpdateRequest(BaseModel):
    """更新 Deployment 元数据或知识库范围。"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    selected_kbs: Optional[List[str]] = Field(default=None, min_length=1)


class DeploymentChatRequest(BaseModel):
    """Gateway 调用的公开多轮 Agent Contract；KB 范围不属于请求。"""
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=200)
    selected_diagnosis: Optional[str] = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


class ConfigSaveRequest(BaseModel):
    """保存配置请求"""
    config: Dict[str, Any] = Field(..., description="完整的配置对象")


class TestModelRequest(BaseModel):
    """测试模型连接请求"""
    params: Dict[str, Any] = Field(..., alias="model_config", description="模型配置（包含 base_url, model, api_key）")
    model_type: str = Field(default="llms", description="模型类型：llms/embeddings/reranks")


class KBBuildRequest(BaseModel):
    """构建知识库请求"""
    rebuild: bool = Field(default=False, description="是否重建（清空现有数据）")
    batch_size: int = Field(default=16, description="批次大小")
    kb_id: str = Field(..., description="要增量写入的知识库 ID")


class EvalCaseRequest(BaseModel):
    """人工录入的 Retrieval Eval 测试用例。"""
    query: str = Field(..., description="测试 Query", max_length=2000)
    expected_chunk_ids: Union[List[str], str] = Field(
        ...,
        description="32 位 MD5 Chunk ID 列表或英文逗号分隔字符串",
    )


class EvalAutoGenerateRequest(BaseModel):
    """LLM 自动生成 Retrieval Eval 用例的批量参数。"""
    count: int = Field(default=10, ge=1, le=100, description="生成数量")
    min_length: int = Field(
        default=100,
        ge=0,
        le=10000,
        description="参与抽样的切片最小字符数",
    )


class KnowledgeBaseInfo(BaseModel):
    kb_id: str
    kb_name: str
    created_at: Optional[str] = None
    status: str
    progress: int = Field(default=0, ge=0, le=100)


class KBUploadResponse(BaseModel):
    knowledge_bases: List[KnowledgeBaseInfo]


class KBBuildResponse(BaseModel):
    kb_id: str
    status: str
    progress: int = Field(default=0, ge=0, le=100)


class SessionTitleUpdate(BaseModel):
    """更新会话标题请求"""
    title: str = Field(..., description="新的会话标题")


class ClearSessionRequest(BaseModel):
    """清空会话请求"""
    session_id: str = Field(default="default", description="会话 ID")


# =============================================================================
# 响应模型
# =============================================================================
class KBStatusResponse(BaseModel):
    """知识库状态响应"""
    total: int = Field(description="知识库文档总数")
    md_count: int = Field(description="MD 切片文件数")
    db_path: str = Field(description="数据库路径")
    metadata_exists: bool = Field(description="metadata.json 是否存在")


class KBFileInfo(BaseModel):
    """知识库文件信息"""
    name: str
    slice_count: int
    size: int
    mtime: float


class KBFilesResponse(BaseModel):
    files: List[KBFileInfo]


class ConfigResponse(BaseModel):
    config: Dict[str, Any]
    path: str


class TestModelResponse(BaseModel):
    success: bool
    message: str


class SessionResponse(BaseModel):
    session_id: str


class SessionsListResponse(BaseModel):
    sessions: List[Dict[str, Any]]


class SessionInfoResponse(BaseModel):
    current_turns: int
    entity_slots: Dict[str, Any]
    has_last_report: bool


class MemoryTurn(BaseModel):
    round: int
    user: str
    assistant: str


class MemoryResponse(BaseModel):
    turns: List[MemoryTurn]
    entities: Dict[str, Any]
    summaries: Any
    current_turns: int
    total_turns: int
    max_rounds: int


class ThinkingResponse(BaseModel):
    thinking: List[Any]


class StatusResponse(BaseModel):
    status: str = "ok"
    message: Optional[str] = None
