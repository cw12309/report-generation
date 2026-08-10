# AGENTS.md — 医疗影像报告生成 Agent V2

## Project Overview

基于 FastAPI + Tool Calling + RAG + SQLite + Milvus 的医疗影像报告生成系统。支持多知识库逻辑隔离、混合检索、Rerank 精排、SSE 流式生成报告，并内置独立评测中心。

## Architecture

```
浏览器 / 外部 API
    │ HTTP/SSE :8000
    ▼
Gateway (app/gateway.py) — 全局/Deployment Bearer Token 边界 + 代理
    │ http://worker:8001
    ▼
Worker (app/chat/server.py) — 静态 Web UI + 业务接口 + Agent 编排
    ├─ pipeline.py — Agent 主流程
    ├─ deployments.py — API 上线密钥、公开事件和会话锁
    ├─ tools/ — Tool Calling 工具 (rag_search, edit_report, refine_report)
    ├─ rag/ — 查询改写 → 查询规划 → 多路召回 → Rerank
    ├─ memory/ — STM + EntityTracker + SessionStore
    ├─ prompt/ — 模板 + Builder
    └─ data_pipeline/ — Excel→Markdown→Milvus 构建流水线
        │
    ┌───┴───┐
    ▼       ▼
  SQLite   Milvus
  sessions.db  medical_reports
  (会话/KB/切片)  (Dense/Sparse向量)
```

## Directory Map

| Directory            | Responsibility                                |
| -------------------- | --------------------------------------------- |
| `front/`             | 原生 Web 前端；一级页面依次为 Agent、Knowledge Base、API 上线、Eval、Monitor、Config |
| `app/`               | 核心应用代码                                  |
| `app/chat/`          | Worker 服务、Agent 编排、LLM 客户端、工具函数 |
| `app/tools/`         | Tool Calling 工具注册与实现                   |
| `app/rag/`           | RAG 检索管线（查询改写/规划/召回/重排）       |
| `app/memory/`        | 会话记忆（短期记忆、实体追踪、持久化）        |
| `app/prompt/`        | Prompt 模板与 Builder                         |
| `app/eval/`          | 独立评测中心                                  |
| `app/data_pipeline/` | 数据构建流水线（Excel→Markdown→Milvus）       |
| `app/test/`          | 测试（pytest + 手动脚本）                     |
| `app/scripts/`       | 运维工具脚本                                  |

## Key Entry Points

- **应用启动**: `python_start.py` (本地开发), `uvicorn app.chat.server:app` (Docker Worker)
- **API Gateway**: `app/gateway.py` — 无状态全局 Key 鉴权、Deployment Key 转发 + 代理
- **Worker 服务**: `app/chat/server.py` — 所有业务接口与 Deployment 管理/对话
- **Web 前端**: `front/index.html` + `front/app.js` — 六个一级页面、DOM 切换和 API/SSE 交互
- **API 上线**: `app/chat/deployments.py` — Deployment Key、KB 范围、SSE 映射和 conversation lock
- **Agent 主流程**: `app/chat/pipeline.py` → `run_pipeline()`
- **CLI 入口**: `app/chat/main.py` → `main()`
- **配置**: `app/config.py` + `config.yml` + `.env`
- **数据构建**: `app/data_pipeline/build_vector_db.py`, `process_reports.py`
- **评测**: `app/eval/run_retrieval_eval.py`

## Global Development Rules

- **Language**: Python 3.11+
- **Framework**: FastAPI + Uvicorn
- **Package Manager**: pip (requirements.txt)
- **Async**: FastAPI async endpoints + asyncio; LLM 调用通过 `AsyncOpenAI`
- **Error Handling**: 异常通过 `HTTPException` 返回 4xx/5xx; 内部 log + 兜底
- **Logging**: `logging.getLogger(__name__)`, 格式 `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- **Typing**: 使用 type hints，但非强制 mypy
- **Lint/Format**: 无强制执行工具
- **Testing**: pytest (手动安装)，测试位于 `app/test/`
- **Naming**: snake_case for files/variables, PascalCase for classes
- **Config**: `config.yml` 存非敏感配置，`.env` 存密钥；`app/config.py` 提供统一 getter 函数
- **Docker**: 多阶段构建，Gateway + Worker 分离容器
- **Import**: 内部模块优先使用绝对 import（从 `app` 开始），`app/chat/` 内使用相对 import

## Common Commands

```powershell
# Install dependencies
pip install -r requirements.txt

# Development (local)
python python_start.py

# Worker only
uvicorn app.chat.server:app --host 0.0.0.0 --port 8001

# Docker
docker compose up -d

# Run tests
pytest app/test/

# Run evaluation
python app/eval/run_retrieval_eval.py
```

## Agent Navigation

When working on a task:

1. Read this root `AGENTS.md`.
2. Identify the smallest relevant module.
3. Read that module's `AGENTS.md`.
4. Use its `Change Map` to identify likely files.
5. Use its `File Map` and `Symbol Map` to decide which files to open.
6. Read only the directly relevant source files first.
7. Follow imports/calls only when additional context is actually needed.
8. Do not scan the entire repository by default.
9. If changing a public API, inspect relevant dependents.
10. Run the tests/checks listed in the relevant `AGENTS.md`.
11. After implementation, update nearby `AGENTS.md` if structure, responsibility, API, dependencies, or modification paths changed.

Treat `AGENTS.md` as navigation context, but treat source code as the final source of truth.

If `AGENTS.md` conflicts with the actual code, trust the code and update `AGENTS.md`.

## AGENTS.md Maintenance

Whenever you make a structural or architectural change, update the nearest relevant `AGENTS.md` in the same change.

After completing a code change, check whether the following have been invalidated:

- File Map, Symbol Map, Entry Points, Public API, File Relationships, Dependencies, Dependents, Change Map, Testing, Local Rules

Cases that typically require updating `AGENTS.md`:

- Adding/deleting/renaming important files
- File responsibility changes
- Adding/removing important public symbols
- Public API changes
- Module responsibility changes
- Module dependency changes
- Adding important modules
- Test approach changes
- Development command changes
- Architecture changes

Ordinary implementation detail changes do not require updating.

## Child Contexts

- `app/chat/AGENTS.md` — Worker 服务、Agent 编排、LLM 客户端
- `app/tools/AGENTS.md` — Tool Calling 工具系统
- `app/rag/AGENTS.md` — RAG 检索管线
- `app/memory/AGENTS.md` — 会话记忆系统
- `app/prompt/AGENTS.md` — Prompt 模板与构建
- `app/eval/AGENTS.md` — 评测中心
- `app/data_pipeline/AGENTS.md` — 数据构建流水线
- `app/test/AGENTS.md` — 测试
