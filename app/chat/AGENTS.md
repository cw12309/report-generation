# AGENTS.md — app/chat/

## Purpose

Worker 服务核心模块。负责 FastAPI 应用初始化、所有业务 API 端点、Agent 主流程编排、LLM 客户端封装、Pydantic Schema 定义和通用工具函数。这是整个系统的运行时入口（除 Gateway 外）。

不负责：全局公开 API Key 鉴权（由 Gateway 负责）、RAG 检索细节（由 `app/rag/` 负责）、工具实现（由 `app/tools/` 负责）、记忆组件实现（由 `app/memory/` 负责）。Deployment Key 的动态摘要/状态/KB 校验属于 Worker 边界。

## File Map

| File               | Responsibility                                                                                                                                      |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `server.py`        | FastAPI Worker：路由、共享 Stateful 执行器、SSE、Deployment/KB/评测/配置 API                                                                        |
| `deployments.py`   | Deployment Key 生成/摘要、固定 KB 校验、公开 SSE 映射、conversation ID 与进程内锁                                                                   |
| `pipeline.py`      | Agent 主流程编排：Phase 1（实体提取/意图/消解）→ Phase 2（Tool Calling 决策与调度 + Recovery 错误自恢复）→ Phase 3（报告校验/序列化/状态更新/推送） |
| `report_schema.py` | Canonical `results[]` 报告定义、旧格式边界归一化、解析/校验、edit/refine 事实保护、Edit Judge 输出校验与 Public 脱敏                                |
| `llm_client.py`    | LLM 网络请求封装：`chat_stream()`（流式对话）、`get_embedding()`（向量化）、`chat_with_tools()`（Tool Calling）、`rerank_with_retry()`（重排重试）  |
| `schemas.py`       | Pydantic 请求模型：`ChatRequest`、Deployment CRUD/Chat、KB 与 Eval 等                                                                               |
| `main.py`          | CLI 模式入口（`--web` 转发到 server）                                                                                                               |
| `utils.py`         | 无状态工具：`retry` 装饰器、`_extract_json`、`json_to_display`                                                                                      |
| `__init__.py`      | 空文件                                                                                                                                              |

## Symbol Map

### `server.py`

- `create_app()` — FastAPI 应用工厂，返回配置好的 app 实例
- `_new_milvus_client()` — 创建 Milvus 客户端
- `_get_or_create_session(session_id)` — 从 SQLite 恢复或创建新会话
- `_stateful_chat_events(...)` — 浏览器 Chat 与 Deployment Chat 复用的 Pipeline/持久化事件执行器
- API 路由约 20+ 个（详见 Entry Points）

### `deployments.py`

- `generate_api_key()` / `hash_api_key()` / `verify_api_key()` — 一次性明文 Key 与 SHA-256 摘要校验
- `normalize_selected_kbs()` — Deployment 固定 Active KB 校验与去重
- `map_public_event()` — 内部 Pipeline Event 到稳定公开 SSE 的白名单映射
- `ConversationLockRegistry` — 同一 Deployment conversation 的进程内串行化

### `pipeline.py`

- `run_pipeline(query, session_id, stm, entity_tracker, client, last_report, _emit, ...)` — Agent 主流程入口
- `search_reports(query, top_k, rerank_top_k, client, _emit, context, selected_kbs, knowledge_bases)` — RAG 检索与 Rerank
- `_build_tool_registry(...)` — 构建并注册三个工具到 ToolRegistry

**Recovery 错误自恢复**（Phase 2 & Phase 3 内联）：

- `RecoveryBudget` — 恢复预算，每次 pipeline 调用独立，`max_attempts=1`，不跨轮污染
- `_RECOVERABLE_REPORT_ERRORS` — 可恢复工具错误白名单：`{"REPORT_PARSE_FAILED", "EDIT_JUDGE_FAILED"}`
- `_classify_tool_error(error_msg)` — 按关键词分类工具错误码（用户可处理 / 可恢复 / terminal）
- `_is_user_facing_error(error_code)` — 判断是否为用户可处理的业务错误（`NO_LAST_REPORT` / `LLM_SERVICE_ERROR`）
- `_parse_serialized_tool_call(content)` — 从 LLM 文本中解析序列化工具调用（JSON/函数格式）
- `_arbitrate_report_tool(selected_tool_names)` — 多报告工具自动仲裁，优先级：rag_search > edit_report > refine_report
- `looks_like_report_json(content)` — 检测 LLM 是否绕过工具直接输出报告 JSON
- `looks_like_serialized_tool_call(content)` — 检测 LLM 是否在文本中嵌入工具调用

### `report_schema.py`

- `parse_report_output(raw)` — 提取 JSON、兼容旧边界格式并验证唯一 Canonical `results[]`
- `parse_report_with_repair(raw, repair_fn)` — 首次失败后只允许一次受约束 JSON 修复
- `collect_edit_fact_violations(...)` / `validate_edit_preservation(...)` — 按数值、侧别、部位、数量、诊断与阳性/否定事实检查编辑范围，不做全文 Diff
- `parse_edit_judge_output(raw)` — 校验受约束 Edit Judge 的 `{valid, unexpected_changes}` 输出
- `validate_refine_preservation(...)` — 保护润色中的未授权医学事实
- `sanitize_public_report(report)` — 公开响应只保留 `results`，移除 reasoning/内部字段

### `llm_client.py`

- `chat_stream(messages, max_tokens, temperature, _emit, debug, caller)` — 异步流式 LLM 调用
- `get_embedding(text)` — 同步 Embedding 调用
- `chat_with_tools(messages, tools, tool_choice, ...)` — 非流式 Tool Calling 调用
- `rerank_with_retry(query, documents, top_n)` — 带重试的 Rerank 调用

### `schemas.py`

- `ChatRequest` — 对话请求
- `KBBuildRequest`, `KBUploadResponse`, `KBBuildResponse` — 知识库构建
- `EvalCaseRequest`, `EvalAutoGenerateRequest` — 评测用例
- `DeploymentCreateRequest`, `DeploymentUpdateRequest`, `DeploymentChatRequest` — API 上线管理与公开 Chat

### `main.py`

- `main()` — CLI 对话入口，初始化 STM + EntityTracker + Milvus 客户端

### `utils.py`

- `retry(max_attempts, delay, exceptions)` — 重试装饰器
- `_extract_json(text)` — 从 LLM 输出提取 JSON
- `json_to_display(json_str)` — JSON 格式化展示

## Entry Points

理解本模块的推荐顺序：

1. `server.py` → `create_app()` — 了解所有路由和启动流程
2. `pipeline.py` → `run_pipeline()` — 了解 Agent 主流程
3. `schemas.py` — 了解请求/响应结构
4. `deployments.py` — 了解 API 上线的安全与公共契约
5. `llm_client.py` — 了解 LLM 调用方式

## Public API

本模块是 Worker 内部实现，不对外暴露 Python API。其他模块通过以下方式使用：

- `app/tools/` imports `chat_stream` from `app.chat.pipeline`
- `app/eval/` imports `get_embedding`, `rerank_with_retry` from `chat.llm_client`
- `app/memory/` imports `chat_stream` from `chat.llm_client` (via `app.chat.server`)

## File Relationships

```
server.py (路由注册)
  → deployments.py (Deployment 安全/公共 SSE/锁)
  → pipeline.py (Agent 主流程)
      → llm_client.py (LLM 调用)
      → report_schema.py (报告结构、终态校验与事实保护)
      → tools/ (工具注册与执行)
      → rag/ (查询改写/规划/检索)
      → memory/ (记忆管理)
      → prompt/ (Prompt 构建)
  → memory/ (会话持久化)
  → data_pipeline/ (知识库构建)
  → eval/ (评测)
```

`pipeline.py`:

- uses `llm_client.py` for `get_embedding`, `chat_stream`, `chat_with_tools`, `rerank_with_retry`
- uses `tools/registry.py` for `ToolRegistry`
- uses `tools/rag_tool.py`, `edit_tool.py`, `refine_tool.py` for tool handlers
- uses `prompt/builder.py` for `PromptBuilder`
- uses `rag/query_rewrite.py` for `needs_rewrite`, `rewrite_query`, `standardize_query`
- uses `rag/query_planner.py` for `build_query_plan`
- uses `rag/retrieval.py` for `retrieve_with_fallback`
- uses `rag/metadata_navigator.py` for `MetadataNavigator`
- uses `memory/retriever.py` for `MemoryRetriever`

`server.py`:

- uses `memory/session_store.py` for `SessionStore`
- uses `memory/entity_tracker.py` for `EntityTracker`
- uses `memory/short_term.py` for `ShortTermMemory`
- uses `deployments.py` for key verification, KB scope, public event mapping and conversation locks
- uses `data_pipeline/build_vector_db.py` for `build_db`, `ensure_collection_schema`
- uses `data_pipeline/extract_metadata.py` for `extract_all`
- uses `data_pipeline/process_reports.py` for `process_uploaded_excels`
- uses `eval/` for `stream_run_evaluation`, `stream_generate_cases`, `add_retrieval_case`, etc.

## Dependencies

### Internal Dependencies

- `app/tools/` — 工具注册与执行
- `app/rag/` — 查询改写、规划、检索、重排
- `app/memory/` — 短期记忆、实体追踪、会话持久化
- `app/prompt/` — Prompt 模板与构建
- `app/data_pipeline/` — 知识库构建
- `app/eval/` — 评测
- `app/config.py` — 配置获取

### External Dependencies

- `fastapi` + `uvicorn` — Web 框架
- `pymilvus` — 向量数据库客户端
- `openai` — LLM SDK (AsyncOpenAI)
- `langsmith` — 可观测性 trace

## Dependents

- `app/gateway.py` — 通过 HTTP 代理调用 Worker 的 `/internal/rag/search`、`/internal/report/generate` 和 `/internal/deployments/{id}/chat`
- 前端（浏览器）— 通过 SSE 调用 `/api/chat`，通过管理路由维护 Deployment

## Change Map

### 添加新的 API 端点

通常检查: `server.py` (路由), `schemas.py` (请求/响应模型), 相关业务逻辑模块

### 修改 Agent 主流程

优先检查: `pipeline.py` → `run_pipeline()`, `memory/entity_tracker.py`, `tools/rag_tool.py`, `prompt/` 相关模板

### 修改 Recovery 错误自恢复

优先检查: `pipeline.py` 的 `RecoveryBudget`、`_RECOVERABLE_REPORT_ERRORS`、`_classify_tool_error`、`_is_user_facing_error`、`_parse_serialized_tool_call`、`_arbitrate_report_tool`；`app/test/test_report_pipeline.py` 的 Recovery 相关测试

### 修改正式报告结构或终态

优先检查: `report_schema.py`, `pipeline.py`, `tools/rag_tool.py` / `edit_tool.py` / `refine_tool.py`, `prompt/structure.md` / `edit.md` / `refine.md`, `deployments.py`, `server.py` 的 Last Report 恢复与 SSE 数据注入。

### 修改 LLM 调用方式

优先检查: `llm_client.py`, `config.py` (模型配置), `pipeline.py` (调用方)

### 修改 RAG 检索行为

优先检查: `pipeline.py` → `search_reports()`, `rag/retrieval.py`, `rag/query_planner.py`, `rag/rerank.py`

### 修改知识库构建流程

优先检查: `server.py` (KB 相关路由), `data_pipeline/build_vector_db.py`, `data_pipeline/process_reports.py`

### 修改会话持久化

优先检查: `server.py` → `_get_or_create_session()`, `memory/session_store.py`

### 修改 API 上线 / Deployment

优先检查: `deployments.py`, `server.py` 的管理与 internal chat 路由, `schemas.py`, `memory/session_store.py`, `app/gateway.py`, `front/app.js`。公开字段或 SSE 变化必须同步示例和部署测试。

## Testing

关联测试：

- `app/test/test_api_deployments.py` — Key、存储、KB 健康、事件映射与锁
- `app/test/test_api_deployment_routes.py` — 管理 API、Gateway/Worker 鉴权、多轮 SSE 与原接口回归
- `app/test/test_report_pipeline.py` — Canonical Schema、repair、Last Report 注入、报告工具终态与事实保护、Recovery 错误自恢复（12 个 Recovery 测试）
- `app/test/test_rag_planning.py` — 查询规划测试
- `app/test/test_memory.py` — 记忆系统测试
- `app/test/test_rerank_flow.py` — Rerank 流程测试
- `app/test/test_knowledge_base_chunks.py` — 知识库切片测试
- `app/test/verify_chunk_id_retrieval.py` — chunk_id 一致性验证

修改本模块后建议运行: `pytest app/test/`

## Local Rules

- `server.py` 是唯一的路由注册位置，不要在其他文件注册路由
- `pipeline.py` 是 Agent 编排的唯一入口，不要在 server.py 中直接编排
- 通用 Chat/Embedding 调用优先复用 `llm_client.py`；Rewrite、Planner、Rerank 等模块当前仍有独立客户端，修改前先核对真实调用点
- 新增 Worker 请求/响应模型应定义在 `schemas.py`；既有 Stateless internal 模型当前仍在 `server.py`
- Deployment 明文 Key 只能出现在创建/轮换响应，禁止写入数据库、日志或列表/详情响应
- Deployment Chat 不接受调用方选择 KB，必须使用部署关系表中的固定范围
- 正式报告只能以 Canonical `results[{影像学表现, 诊断意见}]` 在运行时传播；旧格式只允许在 `report_schema.py` 边界归一化
- 报告工具成功必须 Final；失败必须直接返回 error，禁止进入 Optional Final LLM
- 工具错误分类：`UNKNOWN_TOOL_ERROR` 和 `EDIT_REPAIR_FAILED` 为 terminal，禁止 Recovery retry；只有 `_RECOVERABLE_REPORT_ERRORS` 白名单内的错误才允许重试
- 内部错误（RECOVERY_FAILED / RECOVERY_EXHAUSTED / UNKNOWN_TOOL_ERROR / REPORT_SCHEMA_INVALID）**禁止写入 STM**，避免污染后续多轮记忆

## Common Pitfalls

- 修改 `schemas.py` 的请求模型后，检查 Gateway 是否也需要同步更新
- 修改 `run_pipeline` 签名后，检查 `server.py` 和 `main.py` 的调用方
- 修改 Embedding 方式后，检查 `data_pipeline/build_vector_db.py` 是否一致
- SSE 事件格式变更会影响前端解析，需要同步
- 修改 Milvus Schema 后必须运行 `migrate_milvus_schema.py`
- 修改 Recovery 白名单或错误分类逻辑后，必须运行 `pytest app/test/test_report_pipeline.py -v` 验证全部 30 个测试
