# 医疗影像报告生成 Agent V2

基于 FastAPI、Tool Calling、RAG、SQLite 和 Milvus 的医疗影像报告生成系统。系统支持一次上传多个 Excel，但每个 Excel 都会成为一个独立的逻辑知识库；医生在对话时选择一个或多个已激活知识库，Agent 再执行带 `kb_id` 隔离的 Dense/Sparse 混合检索和 Rerank，最终流式生成报告。

## 当前能力

- 多知识库逻辑隔离：所有向量共用 Milvus Collection `medical_reports`，通过 `kb_id` 标量过滤隔离。
- 上传与构建解耦：上传只保存 Excel 并创建 `uploaded` 记录，构建由独立接口异步触发。
- 双流存储：SQLite 保存知识库元数据和完整切片原文，Milvus 保存检索字段与向量；两端通过稳定的 32 位 MD5 `chunk_id` 对齐。
- 增量重建：重新构建某个知识库时，只删除该 `kb_id` 的旧 Milvus 实体，不删除整个 Collection。
- 混合检索：严格层级、仅模态、仅知识库三通道，每个通道包含 Dense 和 Sparse 请求，并使用 RRF 融合。
- Rerank 精排：粗排候选池默认 50 条，最终保留 10 条供报告生成。
- 流式对话：FastAPI SSE 推送状态、召回、Rerank、Token、正式报告和完成事件。
- 来源溯源：正式 AI 气泡底部展示可折叠的参考切片。
- 独立评测中心：支持 Hit@1/3/5、逐 Query SSE 进度、协作式取消、人工/LLM 自动生成用例、测试集原子清空与单删；人工标注可通过六级级联筛选和异步文本搜索定位 SQLite active 切片。
- 当前会话记忆：保留 STM 和 EntityTracker，不学习、不注入跨会话用户偏好。
- 会话与选择持久化：SQLite 保存服务端会话，浏览器 LocalStorage 保存聊天 UI 和知识库勾选状态。

## 架构

```text
浏览器（原生 HTML / CSS / JavaScript）
                 │ HTTP / SSE
                 ▼
FastAPI：app/chat/server.py
  ├─ Agent 编排：app/chat/pipeline.py
  ├─ Tool Calling：app/tools/
  ├─ RAG：app/rag/
  ├─ STM / EntityTracker：app/memory/
  └─ Excel 构建流水线：app/data_pipeline/
                 │
       ┌─────────┴─────────┐
       ▼                   ▼
SQLite                  Milvus
data/sessions.db        medical_reports
会话、KB、切片原文       Dense/Sparse、标量字段
        └──── 32 位 MD5 chunk_id 双向对应 ────┘
```

## 当前已验证数据基线

截至 2026-08-06，远程 `medical_reports` 已按最新 Schema 完成一次全量重建：

- 6 个知识库，状态均为 `active / 100%`；
- SQLite `knowledge_base_chunks`：3143 条；
- Markdown：3143 个；
- Milvus：3143 条实体；
- 三端 `chunk_id` 均为 32 位 MD5，3143 个 ID 全部唯一且集合一致；
- `CT` Hybrid Search 烟测返回的 `source` 已确认等于 `chunk_id`，并能回查 SQLite 原文。

## 快速启动

### 1. 安装依赖

建议使用 Python 3.11 或项目现有 Conda 环境：

```powershell
pip install -r requirements.txt
```

运行测试如需 `pytest`，需额外安装：

```powershell
pip install pytest
```

### 2. 配置服务

主要配置位于项目根目录 `config.yml`：

```yaml
milvus:
  uri: http://<milvus-host>:19530
  token: ""

retrieval:
  collection_name: medical_reports
  rag_top_k: 50
  rerank_top_k: 10
```

LLM、Embedding、Rerank 可以在 `config.yml` 中配置，也可以使用环境变量覆盖：

| 环境变量 | 作用 |
| --- | --- |
| `CHAT_URL` / `CHAT_MODEL` | 对话模型地址与模型名 |
| `EMBED_URL` / `EMBED_MODEL` | Embedding 地址与模型名 |
| `RERANK_URL` / `RERANK_MODEL` | Rerank 地址与模型名 |
| `MILVUS_URI` | Milvus 地址，优先于 `config.yml` |
| `SILICONFLOW_API_KEY` 或 `RERANK_API_KEY` | Rerank 密钥 |

不要把真实 API Key 提交到版本库。

### 3. 检查 Milvus Schema

首次部署或从旧版升级时执行：

```powershell
python app/data_pipeline/migrate_milvus_schema.py --check
```

Collection 必须包含 `kb_id` 和 `chunk_id` 字段。旧 Collection 缺少字段且旧向量允许丢弃时，执行一次受控删除重建：

```powershell
python app/data_pipeline/migrate_milvus_schema.py `
  --drop-and-create `
  --confirm "DROP medical_reports"
```

该命令会删除 Collection 中的全部旧向量，执行前应确认数据可重建或已经备份。正常启动只校验 Schema，不会静默删除旧 Collection。

若需要从 SQLite 登记的原始 Excel 一次性重建 Markdown、SQLite 切片和整个 Milvus Collection，使用：

```powershell
python app/data_pipeline/rebuild_all_knowledge_bases.py `
  --batch-size 32 `
  --confirm "DROP medical_reports"
```

该命令具有破坏性确认保护；只有全部本地 Excel 解析成功后才会删除旧 Collection。

### 4. 启动 FastAPI

```powershell
python python_start.py
```

- Web 页面：<http://localhost:8000>
- API 文档：<http://localhost:8000/docs>

## 知识库使用流程

```text
1. 前端上传一个或多个 .xlsx
   POST /api/kb/upload
   └─ 每个文件生成独立 kb_id，状态 uploaded

2. 在知识库管理页点击“构建”
   POST /api/kb/{kb_id}/build
   └─ processing → active / failed

3. 构建任务
   Excel → Markdown → SQLite 切片 → metadata_tree
         → Dense/Sparse → Milvus（实体带 kb_id）

4. Agent 页面选择 active 知识库
   POST /api/chat
   selected_kbs: ["kb_id_1", "kb_id_2"]
```

上传与构建是两个独立动作。上传完成不会自动生成 Markdown，也不会写入 Milvus。

## 常用 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/kbs` | 获取知识库列表、状态和进度 |
| `POST` | `/api/kb/upload` | 上传一个或多个 Excel，每个文件创建一个 KB |
| `POST` | `/api/kb/{kb_id}/build` | 后台构建指定知识库 |
| `GET` | `/api/kbs/{kb_id}/chunks` | 分页读取 SQLite 切片原文 |
| `GET` | `/api/kb/filter-options` | 接收知识库、检查类型及 Level 1～3 父级条件，返回六级去重选项 |
| `GET` | `/api/kb/chunks?query=关键词&limit=20` | 结合文本及知识库/检查类型/Level 1～4 精确条件搜索 active 切片 |
| `DELETE` | `/api/kb/{kb_id}` | 删除 SQLite、Milvus 实体和对应本地目录 |
| `POST` | `/api/chat` | SSE 流式对话，必须携带 `selected_kbs` |
| `POST` | `/api/eval/run` | SSE 流式运行 Retrieval Eval，逐条返回进度并在完成事件中返回指标 |
| `POST` | `/api/eval/cancel` | 协作式取消当前流式评测，当前 Query 完成后停止 |
| `POST` | `/api/eval/add_case` | 人工追加检索用例，Chunk ID 支持数组或逗号分隔字符串 |
| `POST` | `/api/eval/auto_generate` | SSE 流式调用 LLM 自动生成评测用例并实时返回进度 |
| `GET` | `/api/eval/dataset` | 读取测试集用例列表，不执行检索 |
| `DELETE` | `/api/eval/dataset` | 原子清空全部测试用例 |
| `DELETE` | `/api/eval/dataset/{chunk_id}` | 按期望 Chunk ID 原子删除对应测试用例 |

评测切片搜索支持以下可选参数：`query`、`limit`、`kb_name`、`exam_type`、`level1`、`level2`、`level3`、`level4`。其中 `exam_type` 映射 SQLite 的 `modality`，Level 参数分别映射 `level_1～level_4`；文本条件使用参数化 `LIKE`，结构化条件使用参数化精确匹配并通过 `AND` 组合。`filter-options` 接收 `kb_name`、`exam_type`、`level1～level3`，用于计算后续层级的可用选项。

## 数据目录

```text
data/sessions.db
app/data_pipeline/report_template/{kb_id}/原文件.xlsx
app/data_pipeline/output_markdown/{kb_id}/.../*.md
```

删除知识库时，两个以 `kb_id` 命名的目录会同步清理。清理未登记在 SQLite 的遗留文件前先预览：

```powershell
python app/scripts/cleanup_disk.py --dry-run
python app/scripts/cleanup_disk.py
```

## Docker

项目保留 `Dockerfile`、`docker-compose.yml` 和 `start.sh`。默认端口映射为宿主机 `7860` 到容器 `8000`：

```bash
chmod +x start.sh
./start.sh start
```

访问 <http://localhost:7860>。当前 Compose 中仍保留部分历史本地数据卷配置；若生产环境使用远程 Milvus并要求上传文件跨容器重建后仍保留，应确认 `report_template`、`output_markdown` 和 `data/` 已映射到持久卷。

## 验证

```powershell
python -m compileall -q app
python -m app.test.test_memory
python -m unittest app.test.test_rag_planning
python app/data_pipeline/migrate_milvus_schema.py --check
git diff --check
```

只执行 Hybrid Search 并验证 `source/chunk_id` 可回查 SQLite：

```powershell
python app/test/verify_chunk_id_retrieval.py
```

运行自动化 Retrieval Eval：

```powershell
python app/eval/run_retrieval_eval.py
```

只校验评测数据集：

```powershell
python app/eval/run_retrieval_eval.py --validate-only
```

## 详细文档

- [应用层与 API](app/README.md)
- [数据管线](app/data_pipeline/README.md)
- [RAG 检索](app/rag/README.md)
- [当前会话记忆](app/memory/README.md)
- [前端](front/README.md)
- [自动化评测](app/eval/README.md)
