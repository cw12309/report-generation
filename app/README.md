# 后端与 Agent 开发指南

本文面向需要修改 Worker、Agent、RAG、知识库或评测逻辑的开发者。项目整体定位、部署拓扑和首次启动请先看[根 README](../README.md)；浏览器端实现见[前端 README](../front/README.md)。导航与局部规则以本目录及子目录的 `AGENTS.md` 为准，实际行为以源码为最终事实来源。

## Backend Overview

后端由两个 FastAPI 应用组成：

- `app/gateway.py` 是外部边界，校验全局 Bearer Token 或转发 Deployment Bearer Key，并把公开 API 流式代理到 Worker。
- `app/chat/server.py` 是 Worker，提供静态前端、会话式 Agent API、Deployment 管理与对话、知识库管理、配置、会话记录和评测接口。

本地开发时，`python_start.py` 同时启动 Worker（`127.0.0.1:8001`）和 Gateway（`0.0.0.0:8000`）。Docker 中两者是独立容器，Gateway 通过 `WORKER_BASE_URL` 访问 Worker。

```text
Browser ── /api/* ───────────────► Worker :8001
   ▲                                  │
   │ static HTML/CSS/JS               ├─ Agent / Tools / Memory
   │                                  ├─ RAG ─► Milvus
   │                                  └─ SessionStore ─► SQLite
   │
External client ── /api/v1/* ──► Gateway :8000 ── /internal/* ──► Worker
                  global/deployment Bearer Token
```

浏览器管理接口不是稳定的公共集成 API。外部系统应使用 Gateway 的 `/api/v1/*`；Worker 的 `/internal/*` 只供 Gateway 调用。

## Architecture

一次会话式请求的主要调用链如下：

```text
POST /api/chat
  → server.py 恢复或创建会话
  → pipeline.run_pipeline()
  → 实体/意图/上下文处理
  → 构建 tool_orchestrator prompt
  → LLM 选择一次工具调用批次
  → rag_search | edit_report | refine_report
  → 必要时再进行一次禁用工具的最终 LLM 调用
  → SSE 事件
  → server.py 持久化轮次、槽位、报告和思考事件
```

当前实现不是无限循环的 ReAct：每轮最多执行一次工具调用批次。工具结果若带有 `is_final` 语义，pipeline 会直接返回；否则才进行一次最终组织回复的模型调用。

## Directory Guide

| 目录/文件 | 职责 | 局部导航 |
| --- | --- | --- |
| `gateway.py` | 外部鉴权、公开 API、Worker 代理 | [app/AGENTS.md](AGENTS.md) |
| `config.py` | 合并 `config.yml` 与环境变量，提供配置 getter | [app/AGENTS.md](AGENTS.md) |
| `chat/` | Worker、Agent pipeline、LLM 客户端、请求模型 | [chat/AGENTS.md](chat/AGENTS.md) |
| `tools/` | Tool schema、注册表与 handler | [tools/AGENTS.md](tools/AGENTS.md) |
| `rag/` | 查询规划、混合召回、元数据导航、Rerank | [rag/README.md](rag/README.md) · [rag/AGENTS.md](rag/AGENTS.md) |
| `memory/` | 短期记忆、实体槽位、SQLite 会话存储 | [memory/README.md](memory/README.md) · [memory/AGENTS.md](memory/AGENTS.md) |
| `prompt/` | Prompt 模板与 Builder | [prompt/AGENTS.md](prompt/AGENTS.md) |
| `data_pipeline/` | Excel → Markdown/SQLite/Milvus 构建链路 | [data_pipeline/README.md](data_pipeline/README.md) · [data_pipeline/AGENTS.md](data_pipeline/AGENTS.md) |
| `eval/` | 检索评测执行、指标和结果持久化 | [eval/README.md](eval/README.md) · [eval/AGENTS.md](eval/AGENTS.md) |
| `test/` | pytest 测试与需要外部服务的手动脚本 | [test/AGENTS.md](test/AGENTS.md) |
| `scripts/` | 运维和诊断脚本 | [app/AGENTS.md](AGENTS.md) |

## Agent Execution

`app/chat/pipeline.py` 的 `run_pipeline()` 是核心入口，主要阶段是：

1. 从 `EntityTracker` 和历史轮次解析当前意图、检查方式、部位、诊断等上下文。
2. 在配置允许且确有需要时改写检索 Query；缺少关键检查方式或诊断歧义时提前返回交互事件。
3. 使用 `MemoryRetriever` 从本会话短期记忆中挑选与当前问题相关的轮次。
4. 通过 Prompt Builder 组合实体、历史、上一份报告和工具说明。
5. 调用 LLM 获取工具选择；由注册表校验并执行工具。
6. 直接返回最终工具结果，或做一次不带工具的最终 LLM 调用。

与 pipeline 配套的关键文件：

- `chat/server.py`：HTTP/SSE 边界和持久化时机。
- `chat/llm_client.py`：普通聊天与流式聊天封装。
- `chat/schemas.py`：Worker 请求/响应模型。
- `tools/registry.py`：工具发现、参数校验与分发。

## Tools

| 工具 | 主要用途 | 是否检索 RAG | 典型依赖 |
| --- | --- | --- | --- |
| `rag_search` | 检索参考切片并生成结构化影像报告；也处理诊断歧义 | 是 | RAG、实体槽位、`structure.md` |
| `edit_report` | 按用户指令编辑上一份报告 | 否 | `last_report`、`edit.md` |
| `refine_report` | 调整上一份报告的表达、细节或风格 | 否 | `last_report`、`refine.md` |

新增或修改工具时，至少检查 `tools/registry.py`、对应 handler、`chat/pipeline.py` 对最终结果的处理以及 [tools/AGENTS.md](tools/AGENTS.md) 中的 Public API、Change Map 和 Testing 是否仍然准确。

## RAG Pipeline

当前检索主链路不是简单的“Embedding → Top K”：

```text
原始 Query
  → 标准化 / 可选 LLM 改写
  → Query Planner
  → strict / modality / kb_only 多通道过滤
  → 每通道 Dense(COSINE) + Sparse(IP) Hybrid Search
  → RRF 融合
  → 按稳定 chunk_id 去重
  → Cross-Encoder Rerank
  → 参考切片 + 结构化报告生成
```

重要实现约束：

- `MetadataNavigator` 用于校验和解析候选元数据，不负责直接召回。
- Sparse 向量是基于 BLAKE2 哈希的中文字符/双字词与英文 token 特征，不是 BM25。
- 相同过滤条件的检索通道会去重，避免重复搜索。
- Rerank 失败时会保留召回候选顺序作为降级结果，而不是让整次请求失败。
- `chunk_id` 是跨 Markdown、SQLite、Milvus 和评测集对齐的稳定 32 位 MD5 标识。

算法、过滤字段和配置项的细节见 [RAG README](rag/README.md)。

## Memory and Session State

Memory 与 RAG 是两套不同的数据来源：RAG 从知识库找参考报告，Memory 从当前会话找用户上下文。

运行时状态包括：

- 短期对话记忆（STM）；
- `EntityTracker` 的检查方式、部位、诊断、临床史和意图槽位；
- 上一份报告，用于编辑与润色；
- 诊断歧义候选与当前选择；
- 本轮选择的知识库 ID。

`SessionStore` 使用 `data/sessions.db` 持久化会话、轮次、session state、思考事件、知识库/切片信息和评测数据。`MemoryRetriever` 则在每次 pipeline 执行期间，从已恢复的短期记忆中选择相关轮次，不是另一套长期向量库。

### 会话生命周期

1. 前端通常生成 `web_*` 的 `session_id`；`POST /api/session/new` 生成 `rag_v2_*` ID。
2. `/api/chat` 按 ID 从 SQLite 恢复轮次、槽位和上一份报告；不存在时创建新会话。
3. pipeline 处理本轮请求并持续发出思考/结果事件。
4. Worker 在结束时写入用户轮次、助手轮次、槽位、报告和相关事件。
5. `/api/clear` 清空并重建指定会话，`DELETE /api/session` 删除指定会话。

浏览器 LocalStorage 保存的是前端历史列表和渲染快照，不替代后端 SQLite 会话状态。

## Prompt System

Prompt 由 `app/prompt/` 下的 Markdown 模板和 Builder 加载。当前主路径中已经确认的模板包括：

| 模板 | 当前职责 |
| --- | --- |
| `tool_orchestrator.md` | 选择工具及组织一次工具调用 |
| `structure.md` | 根据检索结果生成结构化报告 |
| `entity_extraction.md` | 抽取/更新实体槽位 |
| `rewrite.md` | 改写检索 Query |
| `edit.md` | 编辑上一份报告 |
| `refine.md` | 润色或细化上一份报告 |
| `summarize.md` | 历史摘要 |

目录中还保留 `report_generation.md`、`multi_disease.md`、`react_*`、`chat.md` 等模板；它们未出现在已核对的当前主报告生成链路中。修改前应先搜索实际加载点，不要仅凭文件名判断其已启用。

## API and SSE

### 对外 Gateway API

无状态接口需要全局 `Authorization: Bearer <API_SECRET_KEY>`；Deployment Chat 需要创建该 Deployment 时一次性显示的独立 Key。

| 方法与路径 | 用途 | Worker 目标 |
| --- | --- | --- |
| `POST /api/v1/rag/search` | 无状态检索，返回 `query`、可展示的 `content` 与结构化 `results` | `/internal/rag/search` |
| `POST /api/v1/report/generate` | 无状态检索并生成报告 | `/internal/report/generate` |
| `POST /api/v1/deployments/{deployment_id}/chat` | 绑定固定 KB 的有状态多轮 SSE Chat | `/internal/deployments/{deployment_id}/chat` |

前两个无状态接口都接受 `query`、可选 `top_k`、`rerank_top_k` 和 `selected_kbs`。报告生成额外接受 `modality`、`body_part`、`clinical_history`、`diagnosis`、`selected_diagnosis`，且不会创建或修改聊天会话。

Deployment Chat 只接受 `message`、可选 `conversation_id` 和可选 `selected_diagnosis`；请求模型明确拒绝 `selected_kbs`。KB 范围由 Deployment 管理配置固定，首次请求创建并返回 `conv_*`，后续复用后可恢复原有 Session 状态。公开 SSE 只映射 `conversation/status/message/report/ambiguous/error/done`，内部规划和检索事件不属于公共契约。

### Worker API 分组

| 分组 | 主要路由 |
| --- | --- |
| 对话 | `POST /api/chat`、`POST /api/clear`、`GET /api/info`、`GET /api/memory` |
| 会话 | `GET /api/sessions`、`POST /api/session/new`、`DELETE /api/session`、`GET /api/session/thinking` |
| 知识库 | `GET /api/kbs`、`POST /api/kb/upload`、`POST /api/kb/{kb_id}/build`、`DELETE /api/kb/{kb_id}`、切片/筛选接口 |
| 评测 | 数据集增删查、`POST /api/eval/auto_generate`、`POST /api/eval/run`、状态与取消接口 |
| 配置 | `GET/POST /api/config`、`POST /api/test-model` |
| Deployment 管理 | `GET/POST /api/deployments`、详情/更新/软删除、启用/停用、轮换 Key |
| 内部 | `POST /internal/rag/search`、`POST /internal/report/generate`、`POST /internal/deployments/{id}/chat` |

Deployment 创建/更新只允许绑定 Active KB，并对重复 ID 去重。运行时还会再次检查状态和 KB 健康度；任一绑定 KB 不可用时 Deployment 显示 `degraded` 并拒绝公开对话。创建和轮换响应中的明文 API Key 只出现一次，持久化层仅保存摘要和前缀。删除是软删除：Deployment 不再列出或调用，但历史 Session 不被级联删除。

`POST /api/chat` 的 JSON 字段为：

```json
{
  "query": "胸部 CT，可见右肺上叶结节",
  "session_id": "web_...",
  "selected_diagnosis": null,
  "selected_kbs": ["kb-id"]
}
```

至少需要一个处于可用状态的已选知识库。响应为 `text/event-stream`，每条消息使用 `data: <JSON>`。

### SSE 事件

主聊天链路可能发出：

- 过程：`status`、`intent`、`query_rewrite`、`context_resolve`、`search`、`query_plan`、`recall`、`rerank`、`reasoning`；
- Agent/记忆：`entity_update`、`intent_switch`、`memory_retrieval`、`tool_executed`、`context_usage`、`cache_hit`；
- 交互与结果：`ambiguous`、`report`、`message`、`error`、`done`；
- 内容增量：`token`。

`chat_stream()` 支持 `token`，但当前已核对的主报告/编辑/润色 handler 没有把 `_emit` 传入该调用，初次和最终 Agent 调用也使用非流式 `chat_with_tools()`。因此当前主路径通常是“过程事件 + 最终 `report`/`message`”，不能假设一定会逐 token 输出。新增事件或改变 payload 时，要同步检查 `front/app.js` 的消费分支和[前端 README](../front/README.md)。

评测和自动生成测试集也使用 SSE，但事件集合独立：评测使用 `start`、`progress`、`done`、`cancelled`、`error`；自动生成使用 `start`、`warning`、`progress`、`done`、`error`。

## Knowledge Base Build

每个上传的 `.xlsx` 文件会创建一个逻辑独立的知识库。后台构建流程是：

```text
Excel
  → 校验并解析有效行
  → 每行生成主 chunk 与 Markdown
  → 写入 SQLite chunks
  → 构建 metadata tree
  → 生成 dense + sparse vectors
  → 删除 Milvus 中该 kb_id 的旧数据
  → 批量插入
  → knowledge base 状态变为 active
```

构建由后台任务执行，状态为 `processing` 时前端每 4 秒轮询 `/api/kbs`。上传、构建、删除或字段映射变化时，优先阅读 [data_pipeline/README.md](data_pipeline/README.md) 和最近的 `AGENTS.md`。

## Milvus Data Model

默认 collection 为 `medical_reports`。核心字段包括：

- 标识与隔离：`id`、`kb_id`、`chunk_id`；
- 层级元数据：`modality`、`level_1` 至 `level_4`、`module_name`；
- 报告内容：`imaging_findings`、`opinion`、`combined_text`；
- 向量：`dense_vector`、`sparse_vector`。

知识库隔离依赖 `kb_id` 过滤。修改 schema、Embedding 维度、Sparse 生成方法或 collection 名称通常需要重建向量数据，并同步检查检索与评测代码。

## Evaluation

评测中心只评估检索，不评估最终报告文本。每个用例包含 Query、一个或多个 `expected_chunk_ids` 以及用于筛选/展示的元数据。执行器记录 Hit@1、Hit@3、Hit@5、目标排名、Rerank 分数和 bad cases，状态与结果持久化到 SQLite。

评测运行和自动生成测试集都支持进度 SSE；页面刷新后可通过 `/api/eval/status` 恢复最近现场，运行中的任务可取消。详细数据格式和 CLI 使用见 [eval/README.md](eval/README.md)。

## Configuration

配置入口是根目录 `config.yml`、`.env` 与 `app/config.py`：

- `config.yml` 保存非敏感的模型、检索、Rerank、Milvus 和记忆参数；
- `.env` 保存 API Key、服务地址等环境相关值；
- `app/config.py` 提供统一 getter，并处理环境变量覆盖；
- Worker 的 `/api/config` 可读写配置，返回时会掩码敏感字段；
- Gateway 另外读取 `API_SECRET_KEY`、`WORKER_BASE_URL`、`WORKER_REQUEST_TIMEOUT`、`ALLOW_LEGACY_PUBLIC_PROXY`。

配置字段会影响不同模块：LLM/Embedding/Rerank 指向各自客户端，检索 Top K 和规划开关影响 `rag/`，记忆轮数影响 `memory/`，Milvus URI/token/collection 影响数据构建和在线检索。字段变更时同时检查消费者，不要只改配置页面。

## Development

从仓库根目录执行：

```powershell
# 安装依赖
pip install -r requirements.txt

# 本地同时启动 Worker 与 Gateway
python python_start.py

# 只启动 Worker（直接访问浏览器管理 UI）
uvicorn app.chat.server:app --host 0.0.0.0 --port 8001

# 容器启动
docker compose up -d
```

首次配置、端口映射、环境变量示例和服务验证见[根 README](../README.md)。

## Testing

优先运行与变更最近的测试，不要默认执行依赖模型、Embedding、Milvus 或大量数据的脚本。

```powershell
# 查询规划与过滤逻辑
pytest app/test/test_rag_planning.py -v

# 知识库切片存储逻辑
pytest app/test/test_knowledge_base_chunks.py -v

# Memory 自带的脚本式测试入口
python -m app.test.test_memory
```

`app/test/test_rerank_flow.py` 在导入/运行时会访问外部模型与检索依赖；`verify_chunk_id_retrieval.py`、`eval_retrieval_v2.py`、`eval_rerank_compare.py` 也属于集成或手动验证脚本。执行完整 `pytest app/test/` 前，先准备相应服务、模型凭据、Milvus 和测试数据。测试分类与前置条件见 [test/AGENTS.md](test/AGENTS.md)。

## Change Guide

| 要修改的行为 | 首选入口 | 同时检查 |
| --- | --- | --- |
| Worker 路由或 SSE | `chat/server.py` | `chat/schemas.py`、`front/app.js`、Gateway 是否代理 |
| Deployment/API 上线 | `chat/deployments.py`、`chat/server.py` | `memory/session_store.py`、`gateway.py`、Config UI、部署测试 |
| Agent 决策与调用次数 | `chat/pipeline.py` | Prompt Builder、工具 registry、memory |
| 新增/修改工具 | `tools/` | `tools/registry.py`、orchestrator prompt、工具测试 |
| 检索策略或过滤条件 | `rag/` | 配置、Milvus 字段、评测数据与指标 |
| 会话/实体/摘要 | `memory/` | `server.py` 的恢复与持久化、SQLite schema |
| 报告格式或文案约束 | `prompt/` | 实际模板加载点、工具 handler、前端 Markdown 渲染 |
| Excel 导入或向量 schema | `data_pipeline/` | SQLite、Milvus、RAG、评测 chunk ID |
| 外部公开 API | `gateway.py` | Worker `/internal/*`、鉴权、OpenAPI 请求模型 |
| 评测流程 | `eval/`、`chat/server.py` | 数据集存储、前端 SSE、状态恢复 |

完成后检查最近的 `AGENTS.md` 是否因文件职责、公共符号、依赖、测试方法或修改路径变化而失效。普通实现细节变化不需要更新导航文档。

## Related Documentation

- [项目总览、安装与部署](../README.md)
- [前端开发指南](../front/README.md)
- [后端导航索引](AGENTS.md)
- [RAG 详细说明](rag/README.md)
- [Memory 详细说明](memory/README.md)
- [数据构建说明](data_pipeline/README.md)
- [评测中心说明](eval/README.md)
