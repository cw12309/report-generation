# AGENTS.md — app/rag/

## Purpose

RAG 检索管线。负责查询改写、查询规划、多路混合召回（Dense + Sparse + RRF 融合）、Rerank 精排，以及检索元数据导航。为 `pipeline.py` 提供端到端的检索能力。

不负责：LLM 调用（由 `llm_client.py` 负责）、报告生成（由 `tools/rag_tool.py` 负责）、向量库构建（由 `data_pipeline/` 负责）。

## File Map

| File                    | Responsibility                                                           |
| ----------------------- | ------------------------------------------------------------------------ |
| `__init__.py`           | 包说明                                                                   |
| `query_rewrite.py`      | 查询改写：模糊输入扩展、术语标准化、过度模糊追问                         |
| `query_planner.py`      | 查询规划：NL→结构化 plan（modality + level filter + semantic query）     |
| `retrieval.py`          | 多路召回：strict/modality/kb_only 三通道，每通道 Dense+Sparse + RRF 融合 |
| `rerank.py`             | Rerank 精排：SiliconFlow Qwen3-VL-Reranker-8B Cross-Encoder              |
| `sparse_vector.py`      | 稀疏向量生成：中文单字+bigram+英文 token 的 BLAKE2 哈希                  |
| `metadata_navigator.py` | 元数据导航：加载 metadata.json 层级树，提供过滤候选项                    |

## Symbol Map

### `query_rewrite.py`

- `needs_rewrite(user_input)` → bool — 判断是否需要改写
- `rewrite_query(user_input)` → str — 使用 LLM 改写查询
- `standardize_query(text)` → str — 标准化术语（如"CT头部"→"CT头颅"）
- `is_too_vague(user_input)` → bool — 过于模糊（仅有检查类型）
- `get_clarification(user_input)` → str — 生成追问提示

### `query_planner.py`

- `build_query_plan(query, context, navigator)` → dict — 构建检索计划：`{filters, semantic_query, confidence}`
- `parse_query_keywords(query)` → dict — 规则兜底的关键词提取

### `retrieval.py`

- `retrieve_with_fallback(plan, query_vector, top_k, client, selected_kbs)` → (list, details) — 多路召回主函数
- `build_filter_expression(filters, selected_kbs)` → str — 构建 Milvus 标量过滤表达式
- `build_recall_channels(plan)` → list — 构建 strict/modality/kb_only 三通道
- `ensure_collection_loaded(client, timeout)` → dict — 确保 Collection 已加载

### `rerank.py`

- `rerank_documents(query, documents, top_n)` → list — 对候选文档进行 Cross-Encoder 精排

### `sparse_vector.py`

- `text_to_sparse_vector(text)` → dict — 生成稀疏向量（维度 2^20）

### `metadata_navigator.py`

- `MetadataNavigator` — 元数据导航类
  - `from_knowledge_bases(knowledge_bases)` — 从活跃 KB 合并元数据树
  - `candidates_for(level, context)` — 获取指定层级的候选项
- `LEVEL_FIELDS` — 层级字段名常量

## Entry Points

理解本模块的推荐顺序：

1. `retrieval.py` — 核心召回逻辑
2. `query_planner.py` — 查询规划
3. `query_rewrite.py` — 查询改写
4. `rerank.py` — 精排
5. `metadata_navigator.py` — 元数据约束

## Public API

本模块无统一的 `__init__.py` 导出。调用方直接 import 具体模块：

```python
from rag.query_rewrite import needs_rewrite, rewrite_query, standardize_query
from rag.query_planner import build_query_plan
from rag.retrieval import retrieve_with_fallback
from rag.rerank import rerank_documents
from rag.metadata_navigator import MetadataNavigator
```

## File Relationships

```
pipeline.py → search_reports()
  → query_planner.py → build_query_plan()
      → metadata_navigator.py → MetadataNavigator
      → query_rewrite.py → parse_query_keywords()
  → retrieval.py → retrieve_with_fallback()
      → sparse_vector.py → text_to_sparse_vector()
      → metadata_navigator.py → LEVEL_FIELDS
  → llm_client.py → rerank_with_retry()
      → rerank.py → rerank_documents()
```

`query_rewrite.py`:

- uses `prompt/` → `load_prompt` (for rewrite.md)
- uses `config.py` → shared OpenAI-compatible client factory and Rewrite model configuration

`query_planner.py`:

- uses `config.py` → `get_rewrite_client`, `get_rewrite_model`

## Dependencies

### Internal Dependencies

- `app/prompt/` — `load_prompt` (rewrite.md)
- `app/config.py` — 模型和路径配置
- `app/data_pipeline/metadata.json` — 元数据层级树

### External Dependencies

- `pymilvus` — `AnnSearchRequest`, `RRFRanker`, `MilvusClient`
- `openai` — 由 `config.py` 的共享 client 工厂调用 Query Rewrite/Planner
- `requests` — 仅用于 rerank API 调用
- `langsmith` — `@traceable`

## Dependents

- `app/chat/pipeline.py` — `search_reports()` 使用全部 rag 模块
- `app/eval/run_retrieval_eval.py` — 直接使用 `retrieve_with_fallback`
- `app/data_pipeline/build_vector_db.py` — 使用 `sparse_vector.py`

## Change Map

### 修改检索召回逻辑

优先检查: `retrieval.py` → `retrieve_with_fallback()`, `build_recall_channels()`, `pipeline.py` → `search_reports()`

### 修改查询改写规则

优先检查: `query_rewrite.py`, `prompt/rewrite.md`

### 修改查询规划

优先检查: `query_planner.py` → `build_query_plan()`, `metadata_navigator.py`

### 修改 Rerank 策略

优先检查: `rerank.py`, `llm_client.py` → `rerank_with_retry()`, `config.yml` → reranks 配置

### 修改稀疏向量生成

优先检查: `sparse_vector.py`, `data_pipeline/build_vector_db.py` (索引端需一致)

### 修改元数据层级

优先检查: `metadata_navigator.py`, `data_pipeline/metadata.json`, `data_pipeline/extract_metadata.py`

## Testing

关联测试：

- `app/test/test_rag_planning.py` — 查询规划测试
- `app/test/test_rerank_flow.py` — Rerank 流程测试
- `app/test/eval_retrieval.py`, `eval_retrieval_v2.py` — 检索评测脚本
- `app/test/eval_rerank_compare.py` — Rerank 对比评测

## Local Rules

- 检索管线是纯函数式的，不持有状态
- 查询改写和查询规划使用独立的 LLM 配置（rewrite_llm），不占用主对话模型
- 多路召回的三通道（strict/modality/kb_only）必须保持并行结构

## Common Pitfalls

- 修改召回通道后，必须在 `pipeline.py` → `search_reports()` 中同步更新 `_emit` 的 `recall` 事件格式
- 修改 `sparse_vector.py` 的哈希算法会破坏所有已有向量索引
- 修改 `metadata.json` 结构后，`query_planner.py` 和 `metadata_navigator.py` 都需要同步
- `retrieve_with_fallback` 的 fallback 逻辑较复杂，修改时注意各通道的降级顺序
