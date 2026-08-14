# AGENTS.md — app/data_pipeline/

## Purpose

数据构建流水线。负责将 Excel 模板转换为 Markdown 报告、生成层级元数据树、构建 Milvus 向量索引，以及一次性全量重建。是知识库从原始数据到可检索向量的完整 ETL 管线。

不负责：运行时检索（由 `app/rag/` 负责）、会话管理、报告生成。

## File Map

共享的 `app/milvus_compat.py` 统一创建 MilvusClient，并在 Windows 本地
Milvus Lite 3.0.0 上修正 manifest 覆盖写入语义；建库、Worker、Eval 和迁移工具
必须通过该工厂创建客户端。

| File                             | Responsibility                                                          |
| -------------------------------- | ----------------------------------------------------------------------- |
| `process_reports.py`             | Excel→Markdown 转换：解析 Excel，生成层级文件夹结构和 Markdown 文件     |
| `build_vector_db.py`             | 向量库构建：解析 Markdown → Embedding → 写入 Milvus（含 Schema 管理）   |
| `extract_metadata.py`            | 元数据提取：从 Markdown Front Matter 构建层级元数据树 → `metadata.json` |
| `chunk_id.py`                    | `generate_chunk_id(kb_id, filename, text)` → 32 位 MD5，三端对齐        |
| `rebuild_all_knowledge_bases.py` | 一次性全量重建：Markdown + SQLite chunks + Milvus 全量                  |
| `migrate_milvus_schema.py`       | Milvus Schema 迁移工具（`--check` / `--drop-and-create`）               |
| `metadata.json`                  | 生成的元数据层级树（由 extract_metadata.py 产出）                       |
| `output_markdown/`               | 生成的 Markdown 报告（按 kb_id/检查类型/层级 组织）                     |

## Symbol Map

### `process_reports.py`

- `process_uploaded_excels(kb_id, xlsx_paths, output_dir)` → dict — Excel 处理主函数
- `sanitize_filename(name)` → str — 清理文件名
- `FIELD_MAPPING` — 列名映射
- `STANDARD_FIELDS`, `CONTENT_FIELDS` — 字段常量

### `build_vector_db.py`

- `build_db(kb_id, markdown_dir, client, batch_size)` — 构建向量库（增量）
- `ensure_collection_schema(client)` — 确保 Collection Schema 存在
- `create_collection(client)` — 创建 Collection
- `_ensure_scalar_indexes(client, collection_name, field_names)` — 远程 Milvus 补建标量索引；Milvus Lite 使用无标量索引兼容模式
- `parse_markdown(path, kb_id_override)` → dict — 解析单个 Markdown 文件

### `extract_metadata.py`

- `extract_all(kb_id, output_dir)` → dict — 提取全量元数据
- `extract_frontmatter(md_file)` → dict — 提取单个文件的 Front Matter

### `chunk_id.py`

- `generate_chunk_id(kb_id, filename, text)` → str — 生成 32 位 MD5

### `rebuild_all_knowledge_bases.py`

- 命令行工具，全量重建所有知识库的本地 Markdown + SQLite chunks + Milvus

### `migrate_milvus_schema.py`

- 命令行工具，`--check` 检查 Schema，`--drop-and-create` 重建

## Entry Points

理解本模块的推荐顺序：

1. `chunk_id.py` — 理解三端对齐的 ID 生成
2. `process_reports.py` — 理解 Excel→Markdown 转换
3. `build_vector_db.py` — 理解 Markdown→Milvus 构建
4. `extract_metadata.py` — 理解元数据提取
5. `rebuild_all_knowledge_bases.py` — 理解全量重建流程

## Public API

本模块无统一的 `__init__.py` 导出。调用方直接 import：

```python
from data_pipeline.build_vector_db import build_db, ensure_collection_schema
from data_pipeline.process_reports import process_uploaded_excels
from data_pipeline.extract_metadata import extract_all
from data_pipeline.chunk_id import generate_chunk_id
```

## File Relationships

```
server.py (KB 构建路由)
  → build_vector_db.py (build_db, ensure_collection_schema)
  → extract_metadata.py (extract_all)
  → process_reports.py (process_uploaded_excels)

build_vector_db.py
  → chunk_id.py (generate_chunk_id)
  → rag/sparse_vector.py (text_to_sparse_vector)
  → config.py (各种配置)
  → pymilvus (CollectionSchema, FieldSchema, etc.)

process_reports.py
  → chunk_id.py (generate_chunk_id)

rebuild_all_knowledge_bases.py
  → build_vector_db.py (build_db, create_collection)
  → extract_metadata.py (extract_all)
  → process_reports.py (process_uploaded_excels)
  → memory/session_store.py (SessionStore)

metadata.json (产出)
  → rag/metadata_navigator.py (MetadataNavigator 消费)
  → memory/ (generate_entity_config.py, generate_metadata_keywords.py 消费)
```

## Dependencies

### Internal Dependencies

- `app/rag/sparse_vector.py` — `text_to_sparse_vector`
- `app/memory/session_store.py` — `SessionStore` (rebuild 脚本)
- `app/config.py` — 各种配置 getter

### External Dependencies

- `pymilvus` — Collection 管理、数据插入
- `openpyxl` / `pandas` — Excel 处理
- `pyyaml` — Markdown Front Matter 解析
- `openai` — 通过 `config.py` 的共享 Embedding client 调用

## Dependents

- `app/chat/server.py` — KB 构建 API
- `app/rag/metadata_navigator.py` — 消费 metadata.json
- `app/memory/generate_entity_config.py` — 消费 metadata.json
- `app/eval/run_retrieval_eval.py` — 评测时使用 chunk_id

## Change Map

### 修改 Excel 处理逻辑

优先检查: `process_reports.py` (转换逻辑), `build_vector_db.py` (Markdown 解析), `extract_metadata.py` (Front Matter 格式)

### 修改 Milvus Schema

优先检查: `build_vector_db.py` → `create_collection()`, `migrate_milvus_schema.py`, `rag/retrieval.py` (检索端), `rag/sparse_vector.py` (稀疏向量维度)

### 修改 chunk_id 生成规则

优先检查: `chunk_id.py`, `build_vector_db.py` (使用方), `eval/` (评测使用 chunk_id)

### 修改元数据结构

优先检查: `extract_metadata.py`, `metadata.json` 格式, `rag/metadata_navigator.py`, `rag/query_planner.py`

### 新增知识库

流程: 上传 Excel → `process_uploaded_excels` → `build_db` → `extract_all`

## Testing

关联测试：

- `app/test/test_knowledge_base_chunks.py` — 知识库切片测试
- `app/test/test_milvus_schema_compat.py` — Milvus Lite 标量索引及 Windows manifest 覆盖写入兼容测试
- `app/test/verify_chunk_id_retrieval.py` — chunk_id 一致性验证

## Local Rules

- `chunk_id.py` 的 `generate_chunk_id` 是唯一 ID 生成函数，SQLite、Markdown、Milvus 三端必须使用同一函数
- `build_vector_db.py` 的 `parse_markdown` 与 `process_reports.py` 的 Markdown 格式必须一致
- `metadata.json` 是生成文件，不应手动编辑
- `output_markdown/` 是生成文件，应通过 `process_reports.py` 重新生成

## Common Pitfalls

- 修改 Excel 模板的列名后，`process_reports.py` 的 `FIELD_MAPPING` 必须同步
- 修改 Milvus Schema 后，必须重建所有向量数据（旧数据 Schema 不兼容）
- 修改 `chunk_id.py` 会破坏所有已有的 chunk_id，评测数据集和 SQLite 引用都会失效
- 修改 Embedding 模型后，旧向量与新向量不可混合检索，必须全量重建
- `build_vector_db.py` 依赖 Embedding API 可用，离线环境无法构建
