# 医疗影像报告生成 Agent V2（应用层）

`app/` 是医疗影像报告生成 Agent 的后端应用代码，负责 FastAPI 服务、Tool Calling、RAG 检索、会话记忆，以及 Excel 知识库的上传和构建。

## 当前架构

```text
浏览器（front/）
        │ HTTP / SSE
        ▼
app/chat/server.py                 FastAPI 路由
        │
        ├─ app/chat/pipeline.py     Agent 编排与流式对话
        ├─ app/tools/               rag_search / edit_report / refine_report
        ├─ app/rag/                 Milvus Dense + Sparse 检索、RRF、Rerank
        ├─ app/memory/              SessionStore、实体追踪、短期/长期记忆
        └─ app/data_pipeline/       Excel → Markdown → metadata → Milvus

SQLite（data/sessions.db）          会话与知识库元数据
Milvus Collection                  medical_reports（多个逻辑知识库，共用一个 Collection）
```

多知识库通过 Milvus 标量字段 `kb_id` 进行逻辑隔离，不拆分 Collection。SQLite 保存知识库名称、状态、原始文件路径和目录树元数据。

## 主要功能

- Tool Calling：模型可选择检索生成、编辑报告、重写报告或直接回复。
- Dense/Sparse 混合检索：Milvus 双路召回，RRF 粗排，外部 Rerank 精排，并支持动态降级。
- 多知识库选择：聊天请求携带 `selected_kbs`，检索和 Metadata Navigator 只使用勾选的知识库。
- 上传与构建解耦：上传只落盘并登记，构建由单独接口异步触发。
- 会话持久化：SQLite 保存会话、实体槽位、上一份报告和当前选择的知识库。
- 流式响应：聊天接口使用 SSE 推送思考过程、召回信息和报告内容。

## 启动

在项目根目录执行：

```powershell
pip install -r requirements.txt
$env:PYTHONPATH = "$PWD\app"
python python_start.py
```

访问 <http://localhost:8000>，API 文档位于 <http://localhost:8000/docs>。

配置读取顺序为：环境变量 > 项目根目录 `config.yml` > 默认值。当前远程 Milvus 地址配置在：

```yaml
milvus:
  uri: http://220.154.3.166:19530
  token:
retrieval:
  collection_name: medical_reports
```

`retrieval.db_path` 仅作为本地 Milvus Lite 兼容回退项；配置了 `milvus.uri` 时优先连接远程 Milvus。

## 首次部署：检查 Milvus Schema

当前 Collection 必须包含 `kb_id`（VARCHAR）字段及其标量索引。启动服务时会自动校验，但首次部署建议先手动检查：

```powershell
python app/data_pipeline/migrate_milvus_schema.py --check
```

如果检查结果显示 `kb_id field: MISSING`，需要在确认旧数据可丢弃后执行一次删除重建：

```powershell
python app/data_pipeline/migrate_milvus_schema.py `
  --drop-and-create `
  --confirm "DROP medical_reports"
```

该命令会删除旧 Collection；生产环境如需保留旧数据，应先完成数据导出/迁移，再执行重建。

## 知识库工作流

每个 Excel 文件对应一个独立的 `kb_id`，即使一次上传多个文件，也会生成多个知识库记录。

```text
上传 Excel
  └─ 保存 report_template/{kb_id}/原文件.xlsx
  └─ SQLite 状态：uploaded
        │
        ▼ POST /api/kb/{kb_id}/build
后台构建
  └─ Excel → output_markdown/{kb_id}/...
  └─ 提取 metadata_tree 并保存 SQLite
  └─ 向量化并增量写入 medical_reports（每条实体带 kb_id）
  └─ 成功：active；异常：failed
```

知识库状态：

| 状态 | 含义 | 可执行操作 |
| --- | --- | --- |
| `uploaded` | 文件已上传，尚未构建 | 触发构建 |
| `processing` | 正在切片、提取元数据或写入 Milvus | 等待完成 |
| `active` | 构建完成，可用于聊天检索 | 勾选或删除 |
| `failed` | 构建过程中发生异常 | 重新构建或删除 |

## 主要 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/kbs` | 查询 SQLite 中的知识库列表和状态 |
| `POST` | `/api/kb/upload` | 上传一个或多个 `.xlsx`，只保存文件并创建 `uploaded` 记录 |
| `POST` | `/api/kb/{kb_id}/build` | 将指定知识库置为 `processing`，后台执行构建 |
| `DELETE` | `/api/kb/{kb_id}` | 删除 SQLite、Milvus 实体及本地模板/切片目录 |
| `POST` | `/api/chat` | 聊天；请求体需包含至少一个 `selected_kbs` |

上传示例：

```powershell
curl.exe -X POST http://localhost:8000/api/kb/upload `
  -F "files=@D:\data\head.xlsx" `
  -F "files=@D:\data\chest.xlsx"
```

聊天请求的关键字段：

```json
{
  "session_id": "session-001",
  "query": "CT 头颅脑出血",
  "selected_kbs": ["kb_id_1", "kb_id_2"]
}
```

前端只展示 `active` 知识库供勾选；未选择知识库时应在客户端提示“请至少选择一个知识库”。后端也会校验 ID 是否仍为 active。

## 数据管线命令

通常通过 `/api/kb/{kb_id}/build` 触发，不需要手工执行以下命令。排查或离线处理时可使用：

```powershell
# Excel 转 Markdown（需要指定 kb_id）
python app/data_pipeline/process_reports.py --input-file D:\data\head.xlsx `
  --output app/data_pipeline/output_markdown --kb-id kb_id_1

# 检查 Milvus Schema
python app/data_pipeline/migrate_milvus_schema.py --check
```

详细说明见 [`app/data_pipeline/README.md`](data_pipeline/README.md)。

## 磁盘清理

删除 API 会同步清理：

- `app/data_pipeline/report_template/{kb_id}`
- `app/data_pipeline/output_markdown/{kb_id}`

清理历史遗留且未登记在 SQLite 的目录前，先预览：

```powershell
python app/scripts/cleanup_disk.py --dry-run
python app/scripts/cleanup_disk.py
```

## 目录说明

```text
app/
├── chat/                 FastAPI、请求模型和 Agent 编排
├── tools/                Tool Calling 工具注册与实现
├── rag/                  查询规划、Metadata Navigator、Milvus 检索和 Rerank
├── memory/               SessionStore、实体追踪、短期/长期记忆
├── data_pipeline/       Excel、Markdown、metadata 和向量库构建脚本
├── prompt/               系统提示词与 PromptBuilder
├── scripts/              运维脚本（如 cleanup_disk.py）
└── test/                 单元测试、检索评估和 API 测试
```

运行基础语法检查：

```powershell
python -m compileall -q app
git diff --check
```
