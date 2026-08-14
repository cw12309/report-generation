# AGENTS.md — app/

## Purpose

应用核心代码根目录。包含 Worker 服务、API Gateway、配置管理、以及所有业务子模块。这是整个系统的代码主目录。

不负责：部署配置（Dockerfile, compose.yaml 在根目录）、前端静态文件（front/ 目录）。

## File Map

| File         | Responsibility                                                                    |
| ------------ | --------------------------------------------------------------------------------- |
| `gateway.py` | API Gateway：全局 API Key 鉴权、Deployment Key 内部转发 + HTTP 流式代理到 Worker |
| `config.py`  | 统一配置管理：`config.yml` + `.env` 解析、OpenAI 客户端工厂、所有配置 getter 函数 |
| `observability.py` | Monitor 共用的进程内有界事件层：请求指标、Trace、结构化日志、上下文关联与脱敏 |

## Symbol Map

### `gateway.py`

- `app` — FastAPI Gateway 实例，端口 8000
- `RAGSearchPayload` — 检索请求模型
- `ReportGeneratePayload` — 报告生成请求模型
- `DeploymentChatPayload` — 公开 Deployment Chat 请求模型，拒绝 `selected_kbs`
- 路由: `POST /api/v1/rag/search`, `POST /api/v1/report/generate`, `POST /api/v1/deployments/{id}/chat`
- `verify_api_key()` — Bearer Token 鉴权依赖
- `extract_deployment_api_key()` — 提取 Deployment Bearer 并通过内部 Header 交给 Worker 校验

### `config.py`

- `get_llm_base_url()`, `get_llm_model()`, `get_llm_api_key()` — LLM 配置
- `get_embed_base_url()`, `get_embed_model()`, `get_embed_api_key()`, `get_embed_dimension()` — Embedding 配置
- `get_rerank_base_url()`, `get_rerank_model()`, `get_rerank_api_key()` — Rerank 配置
- `get_rewrite_base_url()`, `get_rewrite_model()`, `get_rewrite_api_key()` — Query Rewrite 配置
- `get_db_path()`, `get_milvus_token()`, `get_collection_name()` — Milvus 配置
- `normalize_milvus_uri()` / `sync_milvus_uri()` — 校验 Milvus URI，并同步写入 `.env` 与当前进程环境
- `get_markdown_dir()`, `get_metadata_path()` — 路径配置
- `get_rag_top_k()`, `get_rerank_top_k()`, `get_max_rounds()`, `get_decay_factor()` — 检索/记忆参数
- `get_llm_client()` → AsyncOpenAI, `get_embed_client()` / `get_rewrite_client()` → OpenAI — 客户端工厂
- `create_openai_client()`, `create_llm_client()`, `create_embed_client()` — Config 测试与运行链路共用的客户端创建入口
- `reload_config()` — 热重载配置
- `normalize_openai_base_url()` / `normalize_openai_model_urls()` — Chat/Embedding Base URL 与旧 endpoint 配置归一化
- `_normalize_base_url(base_url, path_suffix)` — Rerank 等直接 HTTP endpoint 标准化

### `observability.py`

- `ObservabilityStore` — 有界保存请求指标、Trace stage 与结构化日志
- `StructuredLogHandler` — 将应用日志转换为脱敏事件，不保存 traceback
- `bind_observability_context()` — 让异步任务/线程日志关联 `request_id` 与 `session_id`
- `pipeline_stage()` / `sanitize_value()` — 复用 Pipeline SSE 事件并执行字段白名单与脱敏

## Entry Points

理解本目录的推荐顺序：

1. `config.py` — 理解所有配置来源和 getter 函数
2. `gateway.py` — 理解对外 API 鉴权和代理
3. 进入具体子模块的 `AGENTS.md`

## Public API

```python
from config import get_llm_model, get_embed_model, get_db_path, ...
```

`gateway.py` 是独立的 FastAPI 应用，Worker 通过 HTTP 调用。

## File Relationships

```
gateway.py
  → HTTP 代理到 Worker (http://worker:8001)

config.py
  → config.yml (非敏感配置)
  → .env (密钥)
  → 被所有子模块通过 getter 函数消费

observability.py
  → 被 chat/server.py 的 HTTP middleware、Pipeline event bridge 和 Monitor API 消费
  → 不依赖 Agent/RAG/Deployment/Gateway 业务实现
```

## Dependencies

### Internal Dependencies

- 无（`config.py` 与 `observability.py` 被其他模块依赖，但不依赖业务子模块）

### External Dependencies

- `fastapi` + `httpx` — Gateway
- `pyyaml` + `python-dotenv` — 配置解析
- `openai` — 客户端工厂
- `langsmith` — 可观测性 wrapping

## Dependents

- `gateway.py` 被 Docker Compose 独立部署
- `config.py` 被所有子模块使用
- `observability.py` 被 Worker Monitor 和请求执行边界使用

## Child Contexts

- `chat/AGENTS.md` — Worker 服务、Agent 编排、LLM 客户端
- `tools/AGENTS.md` — Tool Calling 工具系统
- `rag/AGENTS.md` — RAG 检索管线
- `memory/AGENTS.md` — 会话记忆系统
- `prompt/AGENTS.md` — Prompt 模板与构建
- `eval/AGENTS.md` — 评测中心
- `data_pipeline/AGENTS.md` — 数据构建流水线
- `test/AGENTS.md` — 测试
- `scripts/` — 运维工具脚本

## Change Map

### 修改配置结构

优先检查: `config.py` (getter 函数), `config.yml` (配置值), `.env.example` (环境变量模板), 所有消费配置的模块

### 修改 Gateway 鉴权

无状态全局 Key 优先检查: `gateway.py` → `verify_api_key()`, `.env` → `API_SECRET_KEY`。Deployment Key 优先检查 `extract_deployment_api_key()`、Header 剥离/注入、Worker `/internal/deployments/{id}/chat`。

### 添加新的配置项

流程: `config.yml` / `.env` → `config.py` (getter 函数) → 消费方模块

### 修改 OpenAI 客户端配置

优先检查: `config.py` → `get_llm_client()`, `get_embed_client()`, `config.yml` → llms/embeddings

### 修改 Monitor / Trace / 实时日志

优先检查: `observability.py`, `chat/server.py` 的 middleware 与 `/api/observability/*`, `front/app.js` 的 Monitor 状态；同步运行 `app/test/test_observability.py`

## Local Rules

- `config.py` 是配置的唯一入口，其他模块不直接读取 `config.yml` 或 `.env`
- Config 页面展示实际生效的 `MILVUS_URI`；保存 Milvus 地址时必须通过 `sync_milvus_uri()` 同步 `.env`，运行时客户端不得缓存旧地址
- 密钥只从 `.env` 或环境变量读取，不回退到 `config.yml`
- `gateway.py` 不 import Worker 代码，只做 HTTP 代理
- 可观测事件必须保持有界并先脱敏；禁止保存 Authorization/API Key、完整 Prompt、患者正文或 traceback
- Gateway 不读取 Deployment 数据库；Deployment Key 的动态摘要校验和状态检查由 Worker 完成
- 新增配置 getter 函数遵循 `get_<name>()` 命名规范

## Common Pitfalls

- 修改 `config.yml` 的 key 结构后，`config.py` 的所有相关 getter 都需要更新
- 修改 Gateway 代理的请求/响应模型后，Worker 的对应接口也需要同步
- `config.py` 的 `_load()` 有缓存，修改 `config.yml` 后需要调用 `reload_config()` 或重启
- OpenAI SDK 的 `base_url` 只需填到 `/v1`，SDK 会自动拼接路径后缀
