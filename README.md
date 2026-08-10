# 医疗影像报告生成 Agent V2

## Overview

这是一个面向医学影像报告**文本生成**的 Agent + RAG 系统。用户可以用自然语言提供检查类型、部位、诊断、病史或报告修改要求；系统从选定的医学报告模板知识库检索参考内容，并由 LLM 生成、编辑或重写结构化医学影像报告文本。

系统由 FastAPI Gateway 和 Worker 组成。Worker 负责会话、Agent 编排、Tool Calling、RAG、知识库构建和评测；SQLite 保存会话、知识库元数据与切片原文，Milvus 保存 Dense/Sparse 向量和检索字段。浏览器使用有状态的 SSE Chat，外部服务也可以通过带 Bearer Token 的无状态 API 直接检索或生成报告。

本项目当前处理的是文本查询、医学报告模板和结构化报告文本，不读取或分析 DICOM、CT、MR 等医学影像像素。

## Key Features

- 基于 Agent 的医学影像报告文本生成。
- 多知识库选择和基于 `kb_id` 的逻辑隔离。
- Dense + Sparse Hybrid Retrieval。
- Metadata-aware Query Planning 和层级导航。
- Strict、Modality、KB-only 三通道并行召回与 RRF 融合。
- Cross-Encoder Rerank。
- OpenAI-compatible Tool Calling。
- `rag_search`、`edit_report`、`refine_report` 三类业务工具。
- STM、EntityTracker、MemoryRetriever 和 SQLite Session 持久化。
- SSE 状态、检索、报告和完成事件。
- Stateful Chat 与 Stateless Retrieval/Generation API。
- Excel → Markdown → Embedding → Milvus 的知识库构建流程。
- Retrieval Eval、Hit@1/3/5、评测进度持久化和恢复。

## Scope and Limitations

- 输入不是医学影像像素，当前不执行 DICOM 或其他医学图像分析。
- 输出是基于模板知识库、检索结果和 LLM 生成的报告文本。
- 系统不应被描述为独立的医学视觉诊断模型。
- 当前评测中心只覆盖 Retrieval，不评估报告医学正确性或临床有效性。
- 真实临床部署中的医生审核、隐私保护和合规要求需由实际业务环境确定。

## Architecture

```text
Browser / External API Client
              │ HTTP / SSE
              ▼
Gateway (app/gateway.py)
  ├─ Bearer authentication for /api/v1/*
  └─ Streaming proxy to Worker
              │ HTTP :8001
              ▼
Worker (app/chat/server.py)
  ├─ Stateful Chat / SSE
  ├─ Stateless internal APIs
  ├─ Knowledge Base APIs
  ├─ Evaluation APIs
  │
  └─ Agent Pipeline (app/chat/pipeline.py)
       ├─ Prompt System
       ├─ Memory
       └─ Tool Registry
            ├─ rag_search ──> RAG ──> Milvus
            ├─ edit_report       │
            └─ refine_report     └─> LLM / Embedding / Rerank APIs

Data Pipeline
  └─ Excel ──> Markdown / SQLite Chunks ──> Dense + Sparse ──> Milvus

Evaluation
  └─ Production Retrieval + Rerank ──> Hit@1/3/5 ──> SQLite / SSE
```

- **Gateway**：公开 API 边界，负责 Bearer Token 鉴权和透明流式代理，不导入 Worker、LLM 或向量数据库代码。
- **Worker**：FastAPI 业务服务，负责路由、SSE、会话恢复、知识库管理、Agent 启动和评测任务。
- **Agent Pipeline**：处理实体、上下文、Prompt、工具选择、工具结果和会话更新。
- **Tools**：把报告生成、精确编辑和风格重写暴露为 LLM 可调用工具。
- **RAG**：查询改写、规划、元数据导航、混合召回、RRF 和 Rerank。
- **Memory**：管理当前会话历史、实体槽位、相关历史检索和持久化状态。
- **Data Pipeline**：把 Excel 报告模板转换为 SQLite/Markdown/Milvus 三端对齐的数据。
- **Eval**：复用生产检索链，计算 Retrieval Hit@K。

## Request Flow

一次 Stateful Chat 请求的主要流程如下：

```text
User Query
  → Gateway legacy/private proxy or Worker
  → Validate selected knowledge bases
  → Restore/Create Session
  → Entity extraction and intent detection
  → Context resolution and optional query rewrite
  → Retrieve relevant STM messages
  → Build tool-orchestrator prompt
  → LLM tool selection
  → Tool batch execution
       ├─ rag_search → Retrieval + Rerank + Report Generation
       ├─ edit_report → Edit existing report
       └─ refine_report → Rewrite existing report style
  → Final tool result or one final LLM response
  → Persist turn / entity slots / last report / thinking events
  → SSE response and [DONE]
```

当前 Agent 不是无限递归的 ReAct 循环。每轮主要包含：

1. 一次 LLM 工具选择。
2. 一个 Tool Call 批次。
3. 工具结果不是 Final Result 时，可选的一次最终 LLM 调用。

报告类工具通过 `is_final` 终止本轮，Pipeline 会直接返回报告，避免二次 LLM 改写医学内容。

## Project Structure

```text
app/
├── chat/           # Worker、API、Agent 编排、LLM 客户端和 Schema
├── tools/          # Tool Registry 与三个业务工具
├── rag/            # Query Rewrite、Planning、Hybrid Retrieval、Rerank
├── memory/         # STM、EntityTracker、MemoryRetriever、SessionStore
├── prompt/         # Markdown Prompt 模板和 PromptBuilder
├── data_pipeline/  # Excel、Markdown、Metadata、Embedding、Milvus
├── eval/           # Retrieval Eval 和测试集管理
└── test/           # 单元测试、集成测试和手动脚本
```

详细模块导航、Symbol Map 和 Change Map 请查看根目录及各模块的 `AGENTS.md`。

## Core Components

### Chat / Agent

`app/chat/server.py` 是 Worker 入口，`app/chat/pipeline.py::run_pipeline()` 是 Agent 主编排入口。Pipeline 组织 Entity、Memory、Prompt 和 Tools，并把结果通过 SSE 事件交给 Worker。

### Tools

`ToolRegistry` 保存 OpenAI-compatible Function Schema 和 Handler。Handler 通过工厂函数接收 LLM、检索、EntityTracker、Last Report 等依赖。

### RAG

RAG 把自然语言转换为受 Metadata Tree 约束的 Query Plan，然后执行 Dense/Sparse 多通道召回和 Cross-Encoder Rerank。报告生成由 `rag_search` 在检索完成后执行。

### Memory

Memory 只保存和检索当前会话上下文。SQLite 提供跨请求持久化，STM 和 EntityTracker 为每次运行提供工作状态。

### Prompt System

Prompt 模板位于 `app/prompt/*.md`，由 `load_prompt()` 缓存加载。`PromptBuilder` 按 Entity Context、基础模板、Last Report 的顺序组合 System Prompt。当前主报告生成路径使用 `structure.md`。

### Data Pipeline

Data Pipeline 解析 Excel 报告模板，生成 Markdown、稳定 Chunk ID、层级 Metadata、Dense/Sparse 向量，并写入共享 Milvus Collection。

### Evaluation

Evaluation 使用与生产相同的 Embedding、Hybrid Retrieval 和 Rerank，对 `query + expected_chunk_ids` 数据集计算 Hit@1/3/5。

## RAG Pipeline

```text
Query
  → Standardize / optional Rewrite
  → Query Planning
  → Metadata Navigation and validation
  → Dense Embedding
  → Parallel recall channels
       ├─ Strict filters: Dense + Sparse
       ├─ Modality-only: Dense + Sparse
       └─ KB-only: Dense + Sparse
  → Milvus RRF Fusion
  → Deduplicate by chunk_id
  → Cross-Encoder Rerank
  → Context Assembly
  → Structured Report Generation
```

需要注意：

- 当前 Retrieval 是 Strict、Modality 和 KB-only **并行多通道召回**，不是传统的逐级 Sequential Fallback。
- Sparse Retrieval 不是 BM25。它使用中文单字、Bigram 和英文 Token 的 BLAKE2 哈希稀疏向量。
- MetadataNavigator 从所选 Active KB 合并层级树，并限制 Query Planning 只能使用合法的 Modality/Level 值。
- 检索候选保留 `kb_id`、`chunk_id`、目录字段、原文和 Rerank Score。

## Tool Calling

| Tool | 用途 | 是否重新检索 |
| --- | --- | --- |
| `rag_search` | 检索知识库并生成新的结构化报告 | 是 |
| `edit_report` | 按具体指令修改已有报告 | 否 |
| `refine_report` | 调整已有报告的详细程度、表达或风格 | 否 |

工具 Schema 由主 LLM 用于选择操作，实际 Handler 由 `app/chat/pipeline.py::_build_tool_registry()` 注册。`edit_report` 和 `refine_report` 依赖 Session 中的 Last Report。

## Memory

- **ShortTermMemory (STM)**：保存当前会话最近若干轮对话。
- **EntityTracker**：跟踪检查类型、部位、病史、诊断和意图，并进行上下文消解。
- **MemoryRetriever**：通过 Embedding 和 Cosine Similarity 检索与当前 Query 最相关的 STM Message。
- **SessionStore**：SQLite 访问层，持久化 Session、Turns、Entity Slots、Last Report、Thinking Events、KB 和 Eval State。

Memory 与 RAG 的职责不同：

- Memory 回答“当前用户在这次会话中说过什么”。
- RAG 回答“所选知识库中有哪些报告模板可参考”。

## Knowledge Base Pipeline

```text
.xlsx
  → Read all sheets
  → Normalize columns
  → One valid row = one main report chunk
  → Markdown + YAML Front Matter
  → SQLite knowledge_base_chunks
  → Hierarchical Metadata Tree
  → Dense Embedding + Sparse Vector
  → Milvus Collection
  → KB status: active
```

主要元数据：

- `kb_id`
- `chunk_id`
- `modality`
- `level_1`
- `level_2`
- `level_3`
- `level_4`
- `module_name`

`chunk_id` 是基于 KB、文件名和 `combined_text` 生成的稳定 32 位 MD5，在 Markdown、SQLite、Milvus 和 Eval Dataset 中保持一致。

## Requirements

- Python 3.11+。
- FastAPI 和 Uvicorn。
- SQLite。
- Milvus 或 Milvus Lite。
- OpenAI-compatible Chat LLM API。
- OpenAI-compatible Embedding API。
- Cross-Encoder Rerank API。
- Excel/Markdown 处理依赖。

依赖版本以 `requirements.txt` 为准。Excel 导入代码直接使用 `pandas`；如果当前环境没有通过其他依赖提供它，需要额外安装。测试使用 `pytest`，它当前也需要单独安装。

## Installation

建议在独立虚拟环境中安装：

```powershell
pip install -r requirements.txt
pip install pandas pytest
```

创建本地环境文件：

```powershell
Copy-Item .env.example .env
```

Linux/macOS 可使用：

```bash
cp .env.example .env
```

不要把真实 API Key 或 Token 提交到版本库。

## Configuration

非敏感配置位于 `config.yml`，Secret 和运行时覆盖项通过 `.env` 或进程环境变量提供。主要 Getter 集中在 `app/config.py`。

### Chat LLM

| 变量 | 用途 |
| --- | --- |
| `CHAT_URL` | Chat LLM 的 OpenAI-compatible Base URL |
| `CHAT_MODEL` | Chat/Tool Calling 模型名 |
| `CHAT_API_KEY` | Chat LLM 认证 |

### Rewrite / Planning

| 变量 | 用途 |
| --- | --- |
| `REWRITE_URL` | Query Rewrite/Planning 模型地址；未设置时可回退主 LLM 配置 |
| `REWRITE_MODEL` | Rewrite/Planning 模型名 |
| `REWRITE_API_KEY` | Rewrite/Planning 认证 |

### Embedding

| 变量 | 用途 |
| --- | --- |
| `EMBED_URL` | Embedding API 地址 |
| `EMBED_MODEL` | Embedding 模型名 |
| `EMBED_API_KEY` | Embedding API 认证 |

Embedding 模型和维度必须与现有 Milvus Dense Vector 一致；更换后需要重建向量数据。

### Rerank

| 变量 | 用途 |
| --- | --- |
| `RERANK_URL` | Rerank API 地址 |
| `RERANK_MODEL` | Cross-Encoder Rerank 模型名 |
| `RERANK_API_KEY` | 通用 Rerank API 认证 |
| `SILICONFLOW_API_KEY` | 当前 Rerank Provider 的兼容认证变量 |

### Milvus

| 变量 | 用途 |
| --- | --- |
| `MILVUS_URI` | Milvus Server URI；未设置时使用配置文件路径/URI |
| `MILVUS_TOKEN` | Milvus 认证 Token |

### Gateway / Worker

| 变量 | 用途 |
| --- | --- |
| `API_SECRET_KEY` | `/api/v1/*` Bearer Token；生产环境必须显式配置 |
| `WORKER_BASE_URL` | Gateway 转发到 Worker 的地址 |
| `WORKER_REQUEST_TIMEOUT` | Gateway 上游请求超时秒数 |
| `ALLOW_LEGACY_PUBLIC_PROXY` | 是否允许旧版 Web UI 代理接受非私网来源 |

`config.yml` 还包含 Active Model、Collection Name、RAG Top-K、Rerank Top-K、Embedding Dimension 和 STM 窗口等配置。保存配置后建议重启进程，因为部分模块会在导入时缓存模型名或其他配置。

## Running the Project

### Local Development

```powershell
python python_start.py
```

该入口会：

1. 启动 Worker：`127.0.0.1:8001`。
2. 轮询 Worker `/health`，等待资源初始化完成。
3. 启动 Gateway：`0.0.0.0:8000`。

本地访问：

- Web/Gateway：<http://localhost:8000>
- Gateway OpenAPI：<http://localhost:8000/docs>
- Worker：仅本机 `127.0.0.1:8001`

只启动 Worker：

```powershell
uvicorn app.chat.server:app --host 0.0.0.0 --port 8001
```

### Docker Compose

```bash
docker compose up -d
docker compose ps
docker compose logs -f worker gateway
```

Compose 拓扑：

| Service | Container Port | Host Port | Role |
| --- | ---: | ---: | --- |
| `worker` | 8001 | 不发布 | Agent、模型、RAG、Session、KB、Eval |
| `gateway` | 8000 | 7860 | 对外入口、鉴权和代理 |

Docker 访问：

- Web/Gateway：<http://localhost:7860>
- Gateway OpenAPI：<http://localhost:7860/docs>

不要把 Worker 的 8001 端口直接发布到公网。

## API Overview

### Stateful Chat

```text
POST /api/chat
Content-Type: application/json
Response: text/event-stream
```

用途：浏览器/内网有状态对话。请求包含 Session ID 和本轮选择的知识库；当前实现要求 `selected_kbs` 至少包含一个 Active KB。响应是 SSE，包含状态、检索、报告、错误和完成事件。

该接口是 Worker API。通过 Gateway 旧版代理访问时，默认只允许回环、私网或链路本地来源。

### Stateless Retrieval

```text
POST /api/v1/rag/search
Authorization: Bearer <token>
```

用途：无状态执行 Query Planning、Hybrid Retrieval 和 Rerank。不会创建或修改 Chat Session。

### Stateless Report Generation

```text
POST /api/v1/report/generate
Authorization: Bearer <token>
```

用途：无状态执行检索和报告生成。可额外提供 Modality、Body Part、Clinical History、Diagnosis 等结构化上下文，但不会持久化 Session。

Gateway 公开的 `/api/v1/*` 路由会转发到 Worker 的 `/internal/*` 路由；外部调用方不应直接访问 Internal API。

## API Examples

### Stateful Chat

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "query": "胸部 CT，考虑肺结节",
    "session_id": "example-session",
    "selected_kbs": ["example-kb-id"]
  }'
```

### Stateless Retrieval

```bash
curl -X POST http://localhost:8000/api/v1/rag/search \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "胸部 CT，考虑肺结节",
    "top_k": 20,
    "rerank_top_k": 5,
    "selected_kbs": ["example-kb-id"]
  }'
```

`rerank_top_k` 不能大于 `top_k`。

### Stateless Report Generation

```bash
curl -X POST http://localhost:8000/api/v1/report/generate \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "胸部 CT，考虑肺结节",
    "selected_kbs": ["example-kb-id"],
    "modality": "CT",
    "body_part": "胸部",
    "clinical_history": "体检发现肺结节",
    "diagnosis": ["肺结节"]
  }'
```

Docker Compose 部署时，将示例中的 `localhost:8000` 改为 `localhost:7860`。

## Report Output

当前报告生成 Prompt 要求输出以下主要结构：

```json
{
  "results": [
    {
      "影像学表现": "...",
      "诊断意见": "..."
    }
  ]
}
```

每个 `results` 元素对应一个独立病变。模型输出中还可能包含 `reasoning` 辅助字段；Stateful Chat 的正式报告展示不会把它作为报告正文，调用方也不应把它视为稳定的临床报告 Contract。

检索结果中保留 `chunk_id` 和来源元数据，但当前最终 Report Schema 没有内置 Citation 字段。

## Building a Knowledge Base

### 1. 准备 Excel

上传接口只接受 `.xlsx`。主要列包括：

- 检查类型
- 一级目录～四级目录
- 模块名称
- 影像学表现
- 影像学意见

### 2. 上传

```text
POST /api/kb/upload
Content-Type: multipart/form-data
```

支持 `files` 多文件字段，并兼容单数 `file`。每个上传文件创建一个独立 KB，初始状态为 `uploaded`；上传本身不会生成向量。

示例：

```bash
curl -X POST http://localhost:8000/api/kb/upload \
  -F "files=@report-template.xlsx"
```

### 3. 构建

```text
POST /api/kb/{kb_id}/build
```

构建在 Worker Background Task 中运行，状态变化为：

```text
uploaded → processing → active | failed
```

构建过程：

1. 解析全部 Sheet 和有效 Row。
2. 为每个有效 Row 生成主要 Report Chunk 和 Markdown。
3. 把 Chunk 原文写入 SQLite。
4. 提取并保存 KB Metadata Tree。
5. 生成 Dense Embedding 和 Sparse Vector。
6. 删除该 `kb_id` 的旧 Milvus 实体。
7. 批量写入并加载 Milvus Collection。
8. 将 KB 标记为 `active`。

### 4. Schema 检查与全量重建

检查 Collection Schema：

```powershell
python app/data_pipeline/migrate_milvus_schema.py --check
```

全量重建所有已登记知识库：

```powershell
python app/data_pipeline/rebuild_all_knowledge_bases.py --help
```

迁移和全量重建可能删除旧向量，执行前应阅读命令帮助、确认源 Excel 完整并做好备份。

## Evaluation

当前只实现 **Retrieval Evaluation**。

数据集默认位于：

```text
app/eval/datasets/retrieval_test_cases.json
```

基本用例格式：

```json
{
  "query": "...",
  "expected_chunk_ids": ["32-character-md5"],
  "metadata": {}
}
```

实际指标：

- Hit@1
- Hit@3
- Hit@5
- 首个相关结果排名
- 对应 Rerank Score
- Bad Cases

运行评测：

```powershell
python app/eval/run_retrieval_eval.py
```

只校验数据集：

```powershell
python app/eval/run_retrieval_eval.py --validate-only
```

Web UI/Worker 还提供 `/api/eval/run`、`/api/eval/status`、`/api/eval/cancel`、数据集管理和自动生成用例接口。评测任务通过 SSE 报告进度，并把现场和最终结果保存到 SQLite。

当前没有正式实现：

- Report Quality Eval
- Faithfulness Eval
- Medical Correctness Eval
- Tool Selection Eval

## Testing

### Unit Tests

Query Planning、Metadata Navigation 和 Retrieval Channel：

```powershell
pytest app/test/test_rag_planning.py -v
```

SQLite KB Chunk 和 Excel Parser：

```powershell
pytest app/test/test_knowledge_base_chunks.py -v
```

Memory 当前使用自定义测试入口：

```powershell
python -m app.test.test_memory
```

### Full / Integration / Manual Tests

```powershell
pytest app/test/
```

完整目录中包含依赖真实 Milvus、Embedding、LLM 或 Rerank 服务的脚本，运行前需确保服务、网络和认证可用。以下属于集成或手动验证，不应视为纯单元测试：

```powershell
python app/test/verify_chunk_id_retrieval.py
python app/test/eval_retrieval_v2.py
python app/test/eval_rerank_compare.py
```

## Development Guide

修改代码前：

1. 阅读根目录 `AGENTS.md`。
2. 阅读目标模块最近的 `AGENTS.md`。
3. 根据 File Map、Symbol Map 和 Change Map 定位最小修改范围。
4. 阅读实际实现和相关测试，以源码为最终事实来源。
5. 完成修改并运行对应测试。
6. 如果结构、公共 API、职责、依赖或测试方式变化，同步更新最近的 `AGENTS.md`。

不要为了理解单个任务默认扫描整个仓库。

## Important Files

| File | Purpose |
| --- | --- |
| `app/gateway.py` | Gateway 鉴权和 Worker 代理 |
| `app/config.py` | 配置 Getter 和 OpenAI-compatible Client Factory |
| `app/chat/server.py` | Worker 路由、SSE、Session、KB 和 Eval 入口 |
| `app/chat/pipeline.py` | Agent 主编排 |
| `app/chat/llm_client.py` | Chat、Embedding 和 Rerank 上层调用封装 |
| `app/tools/registry.py` | Tool 注册、Schema 暴露和执行 |
| `app/tools/rag_tool.py` | RAG 报告生成和歧义处理 |
| `app/rag/query_planner.py` | Metadata-aware Query Plan |
| `app/rag/retrieval.py` | Dense/Sparse 并行多通道召回 |
| `app/memory/session_store.py` | SQLite 会话、KB Chunk 和 Eval State |
| `app/prompt/tool_orchestrator.md` | 主 LLM 工具选择规则 |
| `app/prompt/structure.md` | 当前结构化报告生成规则 |
| `app/data_pipeline/build_vector_db.py` | Milvus Schema、Embedding 和写入 |
| `app/eval/run_retrieval_eval.py` | Retrieval Eval 主引擎 |

## Known Limitations

- 当前不处理 DICOM 或医学影像像素。
- Retrieval Eval 只覆盖检索层，不评估报告质量和医学正确性。
- Stateful Chat 中仍有部分同步 Embedding、Milvus、Rewrite/Planning 和 Rerank I/O。
- Tool Orchestration 是单轮工具批次，不是递归多轮 ReAct。
- 最终 Report Schema 没有内置 Citation 字段。
- 部分模型和检索配置在模块导入时缓存，配置热重载后建议重启服务。
- `/api/chat` 当前要求每轮显式提供至少一个 Active `selected_kbs`。

## Documentation

- [`README.md`](README.md)：面向人类开发者的项目介绍、快速开始和使用入口。
- [`AGENTS.md`](AGENTS.md)：全局架构、开发规则和 AI Coding Agent 导航入口。
- [`app/AGENTS.md`](app/AGENTS.md)：应用层导航。
- `app/*/AGENTS.md`：各模块的 File Map、Symbol Map、Change Map、依赖和测试说明。
- [`app/eval/README.md`](app/eval/README.md)：Retrieval Eval 的详细使用说明。
- [`front/README.md`](front/README.md)：前端说明。

发生冲突时，以实际源码为准；README 负责项目入口，`AGENTS.md` 负责代码导航和修改规则。
