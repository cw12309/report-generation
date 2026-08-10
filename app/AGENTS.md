# AGENTS.md — app/

## Purpose

应用核心代码根目录。包含 Worker 服务、API Gateway、配置管理、以及所有业务子模块。这是整个系统的代码主目录。

不负责：部署配置（Dockerfile, compose.yaml 在根目录）、前端静态文件（front/ 目录）。

## File Map

| File         | Responsibility                                                                    |
| ------------ | --------------------------------------------------------------------------------- |
| `gateway.py` | API Gateway：Bearer Token 鉴权 + HTTP 代理到 Worker                               |
| `config.py`  | 统一配置管理：`config.yml` + `.env` 解析、OpenAI 客户端工厂、所有配置 getter 函数 |

## Symbol Map

### `gateway.py`

- `app` — FastAPI Gateway 实例，端口 8000
- `RAGSearchPayload` — 检索请求模型
- `ReportGeneratePayload` — 报告生成请求模型
- 路由: `POST /api/v1/rag/search`, `POST /api/v1/report/generate`
- `verify_api_key()` — Bearer Token 鉴权依赖

### `config.py`

- `get_llm_base_url()`, `get_llm_model()`, `get_llm_api_key()` — LLM 配置
- `get_embed_base_url()`, `get_embed_model()`, `get_embed_api_key()`, `get_embed_dimension()` — Embedding 配置
- `get_rerank_base_url()`, `get_rerank_model()`, `get_rerank_api_key()` — Rerank 配置
- `get_rewrite_base_url()`, `get_rewrite_model()`, `get_rewrite_api_key()` — Query Rewrite 配置
- `get_db_path()`, `get_milvus_token()`, `get_collection_name()` — Milvus 配置
- `get_markdown_dir()`, `get_metadata_path()` — 路径配置
- `get_rag_top_k()`, `get_rerank_top_k()`, `get_max_rounds()`, `get_decay_factor()` — 检索/记忆参数
- `get_llm_client()` → AsyncOpenAI, `get_embed_client()` → OpenAI — 客户端工厂
- `reload_config()` — 热重载配置
- `_normalize_base_url(base_url, path_suffix)` — URL 标准化

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
```

## Dependencies

### Internal Dependencies

- 无（`config.py` 被所有子模块依赖，但不依赖任何子模块）

### External Dependencies

- `fastapi` + `httpx` — Gateway
- `pyyaml` + `python-dotenv` — 配置解析
- `openai` — 客户端工厂
- `langsmith` — 可观测性 wrapping

## Dependents

- `gateway.py` 被 Docker Compose 独立部署
- `config.py` 被所有子模块使用

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

优先检查: `gateway.py` → `verify_api_key()`, `.env` → `API_SECRET_KEY`

### 添加新的配置项

流程: `config.yml` / `.env` → `config.py` (getter 函数) → 消费方模块

### 修改 OpenAI 客户端配置

优先检查: `config.py` → `get_llm_client()`, `get_embed_client()`, `config.yml` → llms/embeddings

## Local Rules

- `config.py` 是配置的唯一入口，其他模块不直接读取 `config.yml` 或 `.env`
- 密钥只从 `.env` 或环境变量读取，不回退到 `config.yml`
- `gateway.py` 不 import Worker 代码，只做 HTTP 代理
- 新增配置 getter 函数遵循 `get_<name>()` 命名规范

## Common Pitfalls

- 修改 `config.yml` 的 key 结构后，`config.py` 的所有相关 getter 都需要更新
- 修改 Gateway 代理的请求/响应模型后，Worker 的对应接口也需要同步
- `config.py` 的 `_load()` 有缓存，修改 `config.yml` 后需要调用 `reload_config()` 或重启
- OpenAI SDK 的 `base_url` 只需填到 `/v1`，SDK 会自动拼接路径后缀
