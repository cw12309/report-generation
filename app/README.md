# 医疗影像报告生成 Agent V2（应用层）

`app/` 是医疗影像报告生成 Agent 的后端应用代码，负责 FastAPI 服务、Tool Calling、RAG 检索、当前会话上下文，以及 Excel 知识库的上传和构建。

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
        ├─ app/memory/              SessionStore、实体追踪、短期记忆
        └─ app/data_pipeline/       Excel → Markdown → metadata → Milvus

SQLite（data/sessions.db）          会话、知识库元数据与切片原文
Milvus Collection                  medical_reports（多个逻辑知识库，共用一个 Collection）
```

多知识库通过 Milvus 标量字段 `kb_id` 进行逻辑隔离，不拆分 Collection。当前采用双流存储：SQLite 保存知识库元数据和完整切片原文，负责管理、分页和前端详情展示；Milvus 保存检索标量、Dense/Sparse 向量和受长度约束的检索文本，专门负责 RAG 召回。两端通过 `MD5(kb_id + 文件名 + combined_text)` 生成的 32 位 `chunk_id` 精确对应。

## 主要功能

- Tool Calling：模型可选择检索生成、编辑报告、重写报告或直接回复。
- Dense/Sparse 混合检索：严格、仅模态、仅知识库三种范围并行召回，Milvus RRF 粗排后由外部 Rerank 精排。
- 多知识库选择：聊天请求携带 `selected_kbs`，检索和 Metadata Navigator 只使用勾选的知识库。
- 上传与构建解耦：上传只落盘并登记，构建由单独接口异步触发。
- 切片详情：Excel 解析结果批量写入 SQLite，知识库页面可以分页查看检查类型、层级、模块、影像学表现和意见。
- 构建进度：`knowledge_bases.progress` 保存 0～100 的真实阶段进度，前端以行内状态展示。
- 会话持久化：SQLite 保存会话、实体槽位、上一份报告和当前选择的知识库。
- 当前会话记忆：只保留 STM 和 EntityTracker，不学习或注入跨会话用户偏好。
- 流式响应：聊天接口使用 SSE 推送思考过程、召回、Rerank、Token 和最终报告；前端在正式回答下附加参考切片。

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
  uri: http://<milvus-host>:19530
  token:
retrieval:
  collection_name: medical_reports
```

`retrieval.db_path` 仅作为本地 Milvus Lite 兼容回退项；配置了 `milvus.uri` 时优先连接远程 Milvus。

## 首次部署：检查 Milvus Schema

当前 Collection 必须包含 `kb_id`（VARCHAR）和 `chunk_id`（VARCHAR(32)）字段及其标量索引。启动服务时会自动校验，但首次部署建议先手动检查：

```powershell
python app/data_pipeline/migrate_milvus_schema.py --check
```

如果检查结果显示 `kb_id field: MISSING` 或 `chunk_id field: MISSING`，需要在确认旧数据可重建后执行一次删除重建：

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
  └─ 批量替换 SQLite knowledge_base_chunks（完整原文）
  └─ 提取 metadata_tree 并保存 SQLite
  └─ 向量化并增量写入 medical_reports（每条实体带 kb_id + chunk_id）
  └─ 成功：active；异常：failed
```

构建阶段的当前进度分配：

| 进度 | 阶段 |
| --- | --- |
| `0%` | 进入 `processing` |
| `10%` | Excel 解析和 Markdown 生成完成 |
| `15%` | SQLite 切片原文批量事务提交完成 |
| `20%` | 目录元数据提取完成 |
| `20%～70%` | Dense Embedding |
| `70%～82%` | Sparse 向量生成 |
| `82%～98%` | 写入 Milvus |
| `100%` | Collection 刷新完成，状态变为 `active` |

SQLite 使用“删除该 `kb_id` 的旧切片 + `executemany` 批量插入”的单事务替换策略。插入任意一条失败时整个事务回滚，不会留下半套新切片。删除 `knowledge_bases` 记录时，`knowledge_base_chunks` 通过 `ON DELETE CASCADE` 自动清理。

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
| `GET` | `/api/kbs/{kb_id}/chunks?page=1&size=50` | 分页查询 SQLite 切片原文，`size` 最大 100 |
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

切片接口响应：

```json
{
  "code": 0,
  "data": {
    "total": 100,
    "page": 1,
    "size": 50,
    "items": [
      {
        "chunk_id": "850001fc663caf5a7353b9938aecd06d",
        "chunk_index": 1,
        "modality": "CT",
        "level_1": "头颅",
        "module_name": "脑出血",
        "imaging_findings": "...",
        "imaging_opinion": "..."
      }
    ]
  }
}
```

旧部署中尚未写入 SQLite 切片或仍使用 `kb_id:序号` ID 的知识库，需要执行一次“重新构建”后才能获得稳定 MD5 `chunk_id` 和切片详情。当前已验证数据基线中的 6 个知识库均已完成迁移。

## 前端页面结构

- 全局左侧导航：Agent、知识库管理、配置等一级页面切换。
- 一级导航采用浅色布局；Agent、知识库和配置页分别拥有自己的局部二级侧栏。
- Agent 页面：知识库选择器位于工具栏弹窗，只展示 `active` 知识库；计数徽章显示当前选择数量，选择状态保存在 LocalStorage。
- 知识库页面：左侧为上传、状态、构建和删除操作；右侧为所选知识库的切片详情。
- 点击知识库后立即请求切片分页接口；构建进度到达 SQLite 入库阶段或构建完成时，当前详情会自动刷新。
- SSE 的第一个 Token 创建正式助手气泡，最终 `report/message` 更新同一个气泡；`rerank` 来源显示为可折叠参考切片。

## 当前 RAG 检索参数

| 环节 | 当前实现 |
| --- | --- |
| Chunk | 一条有效 Excel 数据行对应一个 Chunk |
| Dense Embedding | `bge-m3`，1024 维 |
| Dense 索引 | HNSW，COSINE，查询 `ef=64` |
| Sparse | 中文单字、二元组和英文数字词项的 Hash Sparse，IP |
| 融合 | Milvus `RRFRanker` |
| 粗排候选 | `rag_top_k=50` |
| 精排 | `Qwen/Qwen3-VL-Reranker-8B` |
| 精排结果 | `rerank_top_k=10` |

`combined_text` 当前按以下顺序构造：检查类型、`level_1～4`、模块名称、影像学表现、影像学意见。Milvus 中的表现和意见分别限制为 4096 字符，`combined_text` 限制为 8192 字符；SQLite 保存未按 Milvus 字段长度截断的原文。

Sparse 分支已经参与 Hybrid Search，但当前实现不是标准 BM25：它没有 IDF 和文档长度归一化。查询前会执行实体槽位更新、上下文补全和术语标准化；条件满足时调用 Query Rewrite，Query Planner 再结合所选知识库的目录树生成标量过滤条件、Dense `semantic_query` 和 Sparse `keyword_query`。

## 召回率优化路线

按建议优先级推进：

1. 使用稳定 `chunk_id` 建立真实黄金评测集，统计 Dense/Sparse/Hybrid 的 Recall@20/50，以及 Rerank 后的 MRR、nDCG。
2. 修正并验证 Query Rewrite 的完整 `/chat/completions` 调用地址，记录原始、标准化、改写后的 Query 和最终过滤表达式。
3. 持续评估严格层级、仅模态、仅 `kb_id` 三通道的单路命中率和融合贡献，必要时根据规划置信度调整权重。
4. 当前 Rerank 前候选池已提高到 50、最终上下文保持 Top 10；继续用评测数据比较 50/80 候选的延迟与收益。
5. 用 Milvus BM25（服务端版本支持时）或 BGE-M3 Sparse lexical weights 替换当前 Hash Sparse，并评估降低 `drop_ratio` 对医学低频词的收益。
6. 增加受控医学同义词和缩写扩展，保留原始 Query 作为独立召回通道，避免 LLM 改写漂移。
7. 最后评估 HNSW `ef=128/256`、RRF权重和字段级多向量表示。

现有检索评测文件仍包含占位 `expected_source`；现在检索已稳定返回 MD5 `chunk_id`，下一步应基于这些 ID 完成黄金集标注后再判断真实召回率。

## 数据管线命令

通常通过 `/api/kb/{kb_id}/build` 触发，不需要手工执行以下命令。排查或离线处理时可使用：

```powershell
# Excel 转 Markdown（需要指定 kb_id）
python app/data_pipeline/process_reports.py --input-file D:\data\head.xlsx `
  --output app/data_pipeline/output_markdown --kb-id kb_id_1

# 检查 Milvus Schema
python app/data_pipeline/migrate_milvus_schema.py --check

# 全量重建 SQLite / Markdown / Milvus（会删除整个 Collection）
python app/data_pipeline/rebuild_all_knowledge_bases.py `
  --confirm "DROP medical_reports"

# 真实 Hybrid Search + Rerank Top 3 调试
python debug_retrieval.py "CT"
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
├── memory/               SessionStore、实体追踪、短期记忆
├── data_pipeline/       Excel、Markdown、metadata 和向量库构建脚本
├── prompt/               系统提示词与 PromptBuilder
├── scripts/              运维脚本（如 cleanup_disk.py）
└── test/                 单元测试、检索评估和 API 测试
```

运行基础语法检查：

```powershell
python -m compileall -q app
python -m app.test.test_memory
python -m unittest app.test.test_rag_planning
# 若已安装 pytest：
python -m pytest app/test/test_knowledge_base_chunks.py -q
git diff --check
```

## 模块文档

- [数据管线](data_pipeline/README.md)
- [RAG 检索](rag/README.md)
- [当前会话记忆](memory/README.md)
- [前端](../front/README.md)
