# Data Pipeline

该目录负责把上传的 Excel 医疗影像模板转换为按 `kb_id` 隔离的 Markdown、SQLite 切片原文、层级目录树和 Milvus Dense/Sparse 向量。

正常业务流程由 FastAPI 的独立上传/构建接口触发；命令行脚本主要用于首次迁移、排错和离线构建。

## 当前流程

```text
POST /api/kb/upload
  └─ report_template/{kb_id}/原文件.xlsx
  └─ SQLite knowledge_bases.status = uploaded

POST /api/kb/{kb_id}/build
  ├─ process_reports.py
  │    └─ output_markdown/{kb_id}/.../*.md
  │    └─ 返回完整 chunks
  ├─ SessionStore 批量替换 knowledge_base_chunks
  ├─ extract_metadata.py 生成 metadata_tree 并写入 knowledge_bases
  └─ build_vector_db.py
       ├─ Dense Embedding
       ├─ Sparse 向量
       ├─ 向量生成成功后删除 Milvus 中该 kb_id 的旧实体
       └─ 增量写入 medical_reports
```

一次上传多个 Excel 时，后端为每个文件生成独立 `kb_id`。当前构建任务读取一个知识库对应的单个原始 Excel。

## 目录

```text
app/data_pipeline/
├── report_template/{kb_id}/     # 上传的原始 Excel
├── output_markdown/{kb_id}/     # 构建阶段生成的 Markdown
├── chunk_id.py                  # 三端共用的稳定 MD5 函数
├── process_reports.py           # Excel 解析、切片和 Markdown 输出
├── extract_metadata.py          # 层级目录树提取
├── build_vector_db.py           # Milvus Schema、向量化和增量写入
├── migrate_milvus_schema.py     # 旧 Collection 检查/迁移
├── rebuild_all_knowledge_bases.py # 全量双流重建（带 Drop 确认）
└── README.md
```

## 1. Excel 解析与 Markdown

业务代码调用：

```python
process_uploaded_excels(file_paths, output_markdown_root, kb_id)
```

每条有效 Excel 数据行对应一个 Chunk，并生成：

- `chunk_id`：`MD5(kb_id + "\\n" + Markdown文件名 + "\\n" + combined_text)`，固定 32 位；
- 来源字段：原文件、Sheet、Excel 行号；
- `modality`、`level_1`～`level_4`、`module_name`；
- `imaging_findings`、`imaging_opinion`；
- 用于展示和向量化的 `combined_text`。

同一个 `chunk_id` 会写入 SQLite、Markdown YAML Front Matter 和 Milvus。Front Matter 同时包含 `kb_id` 和层级字段。输出目录固定隔离在：

```text
output_markdown/{kb_id}/modality/level_1/.../module_name.md
```

Milvus 入库以 Front Matter 中合法的 32 位 `chunk_id` 为跨流契约，不会对其二次改写；只有旧 Markdown 缺失或包含非法 ID 时，才使用公共函数现场生成兜底值。

单文件离线处理：

```powershell
python app/data_pipeline/process_reports.py `
  --input-file D:\data\head.xlsx `
  --output app/data_pipeline/output_markdown `
  --kb-id 0123456789abcdef
```

不带 `--input-file` 的旧批处理入口只扫描 `report_template/` 根目录下的 Excel，不适用于当前 `{kb_id}` 子目录上传结构，推荐使用 API 或显式参数。

## 2. SQLite 切片与目录树

构建任务在 Markdown 生成后，将完整 Chunk 使用单事务批量写入 `knowledge_base_chunks`。同一 `kb_id` 的旧切片会先在事务内删除；任意插入失败时整体回滚。

`extract_all(path, kb_id)` 从该知识库 Markdown Front Matter 生成目录树，并保存到 SQLite：

```text
knowledge_bases.metadata_tree
```

Metadata Navigator 在聊天检索前只合并当前 `selected_kbs` 对应的 `metadata_tree`。

评测中心人工新增用例时，`GET /api/kb/filter-options` 从该表和 `knowledge_bases` 计算知识库、检查类型及 Level 1～4 的级联去重值；`GET /api/kb/chunks?query=...&limit=20` 再组合文本与六个精确字段，参数化搜索 active 知识库的稳定 `chunk_id`，并返回 150 字 `combined_text` 预览。整个选择过程不需要反查 Milvus。

直接运行 `extract_metadata.py` 仍可生成 JSON 文件用于排查：

```powershell
python app/data_pipeline/extract_metadata.py --stats --sample 5
python app/data_pipeline/extract_metadata.py --no-save
```

正式服务不依赖全局 `metadata.json`。

## 3. 增量写入 Milvus

对单个知识库离线构建：

```powershell
python app/data_pipeline/build_vector_db.py `
  --input app/data_pipeline/output_markdown/0123456789abcdef `
  --kb-id 0123456789abcdef `
  --batch-size 32
```

`build_vector_db.py` 不再删除整个 Collection。它会：

1. 创建不存在的 Collection，或校验现有 Schema；
2. 读取 Markdown 中的 `kb_id`，也可由 `--kb-id` 显式覆盖；
3. 生成 Dense 和 Sparse 向量；
4. 向量生成成功后执行 `delete(filter='kb_id == "..."')` 清理目标知识库旧实体；
5. 分批写入、Flush 并 Load Collection。

`rebuild` 参数目前不用于删除整个 Collection。Collection 级迁移只能通过 `migrate_milvus_schema.py` 显式完成。

## Milvus Schema

默认 Collection：`medical_reports`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INT64 / auto_id | Milvus 主键 |
| `kb_id` | VARCHAR(64) | 逻辑知识库隔离字段 |
| `chunk_id` | VARCHAR(32) | SQLite、Markdown、Milvus 共用的稳定 MD5 标识 |
| `modality` | VARCHAR(32) | 检查类型 |
| `level_1`～`level_4` | VARCHAR(64) | 目录层级 |
| `module_name` | VARCHAR(128) | 模块名称 |
| `imaging_findings` | VARCHAR(4096) | 影像学表现 |
| `imaging_opinion` | VARCHAR(4096) | 影像学意见 |
| `combined_text` | VARCHAR(8192) | 完整检索文本 |
| `dense_vector` | FLOAT_VECTOR(1024) | 默认 BGE-M3 Dense 向量 |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | 词面 Sparse 向量 |

SQLite 保存的切片原文不受上述 Milvus VARCHAR 长度截断影响。

## 索引

| 字段 | 索引 | Metric / 参数 |
| --- | --- | --- |
| `kb_id`、`chunk_id`、`modality`、`level_1`～`level_4` | INVERTED | 标量过滤，PyMilvus 3.x 传入空 `metric_type` |
| `dense_vector` | HNSW | COSINE，`M=16`，`efConstruction=256` |
| `sparse_vector` | SPARSE_INVERTED_INDEX | IP，`drop_ratio_build=0.2` |

## Schema 检查与迁移

```powershell
python app/data_pipeline/migrate_milvus_schema.py --check
```

如果旧 Collection 缺少 `kb_id` 或 `chunk_id`，Milvus 不能原地新增字段。确认全部旧向量可以丢弃后执行：

```powershell
python app/data_pipeline/migrate_milvus_schema.py `
  --drop-and-create `
  --confirm "DROP medical_reports"
```

服务启动时会调用 `ensure_collection_schema()`：Collection 不存在时自动创建；字段缺失时明确报错；缺少 `kb_id` 或 `chunk_id` 索引时尝试补建，不会自动删除 Collection。

需要从 SQLite 中登记的原始 Excel 完整重建双流数据时，使用带确认保护的脚本：

```powershell
python app/data_pipeline/rebuild_all_knowledge_bases.py `
  --batch-size 32 `
  --confirm "DROP medical_reports"
```

脚本按以下顺序执行：重新解析全部已登记 Excel → 替换 SQLite 切片 → 重写 Markdown → Drop/Create Collection → Dense/Sparse 向量化 → 写入 Milvus → 将成功知识库恢复为 `active/100%`。任意原始 Excel 缺失或解析失败时，不会进入 Collection 删除阶段。

### 当前验证基线

2026-08-06 已完成一次全量重建并验证：6 个知识库，SQLite/Markdown/Milvus 均为 3143 条；所有 `chunk_id` 为唯一 32 位 MD5，远程 `medical_reports` 行数为 3143。

## 构建进度

| 进度 | 阶段 |
| --- | --- |
| `0%` | 进入 processing |
| `10%` | Excel 与 Markdown 完成 |
| `15%` | SQLite 切片事务完成 |
| `20%` | metadata_tree 完成 |
| `20%～70%` | Dense Embedding |
| `70%～82%` | Sparse 向量 |
| `82%～98%` | 写入 Milvus |
| `100%` | Flush/Load 完成，状态 active |

进度每增加约 5% 写入一次 SQLite，失败时状态改为 `failed` 并保留最后成功进度。

## Demo

```powershell
python app/data_pipeline/build_vector_db.py --demo
```

Demo 只替换 `kb_id="demo"` 的实体，不删除其他知识库，但仍会连接当前配置的 Milvus，生产环境使用前应确认目标地址。
