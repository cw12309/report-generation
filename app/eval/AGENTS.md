# AGENTS.md — app/eval/

## Purpose

独立评测中心。提供 Retrieval Eval 的完整能力：测试用例管理（人工录入/LLM 自动生成/清空/单删）、流式评测执行（Hit@K 指标）、评测现场恢复（SQLite 持久化 + 浏览器 SSE 解耦）、协作式取消。

不负责：报告生成质量评测（Generation Eval 尚未实现）、知识库构建。

## File Map

| File                                 | Responsibility                                                           |
| ------------------------------------ | ------------------------------------------------------------------------ |
| `__init__.py`                        | 公共导出：`DEFAULT_HIT_KS`, `aggregate_hit_rates`, `first_relevant_rank` |
| `run_retrieval_eval.py`              | 评测执行引擎：流式运行、用例 CRUD、取消、现场恢复                        |
| `generate_test_cases.py`             | LLM 自动生成测试用例：从 SQLite 切片抽样 → LLM 生成 Query                |
| `metrics.py`                         | 评测指标：`hit_at_k`, `aggregate_hit_rates`, `first_relevant_rank`       |
| `datasets/retrieval_test_cases.json` | 测试用例数据集                                                           |
| `README.md`                          | 评测模块使用说明                                                         |

## Symbol Map

### `run_retrieval_eval.py`

- `stream_run_evaluation()` → AsyncIterator — 流式执行全量评测
- `add_retrieval_case(query, expected_chunk_ids)` → dict — 添加测试用例
- `clear_retrieval_dataset()` → dict — 清空测试集
- `delete_retrieval_case(chunk_id)` → dict — 删除单条用例
- `load_dataset(path)` → list — 加载测试集
- `get_evaluation_status()` → dict — 获取评测状态
- `request_evaluation_cancel()` → dict — 请求取消评测
- `DEFAULT_DATASET` — 默认测试集路径

### `generate_test_cases.py`

- `stream_generate_cases(count, min_length)` → AsyncIterator — 流式自动生成用例
- `load_dataset(path)` → list — 加载测试集

### `metrics.py`

- `DEFAULT_HIT_KS` — 默认 Hit@K 值: (1, 3, 5)
- `first_relevant_rank(retrieved_ids, expected_ids)` → (int|None, str|None) — 首个相关结果的排名
- `hit_at_k(rank, k)` → int — 判断是否命中
- `aggregate_hit_rates(ranks, ks)` → dict — 计算 Hit@K 率

## Entry Points

理解本模块的推荐顺序：

1. `README.md` — 了解模块功能
2. `run_retrieval_eval.py` — 核心评测引擎
3. `metrics.py` — 指标计算
4. `generate_test_cases.py` — 自动生成

## Public API

```python
from eval import DEFAULT_HIT_KS, aggregate_hit_rates, first_relevant_rank
from eval.run_retrieval_eval import stream_run_evaluation, add_retrieval_case, ...
from eval.generate_test_cases import stream_generate_cases
```

## File Relationships

```
server.py (评测路由)
  → run_retrieval_eval.py (stream_run_evaluation, add_retrieval_case, ...)
  → generate_test_cases.py (stream_generate_cases)
  → memory/session_store.py (保存/恢复评测状态)

run_retrieval_eval.py
  → chat/llm_client.py (get_embedding, rerank_with_retry)
  → rag/retrieval.py (retrieve_with_fallback)
  → eval/metrics.py (aggregate_hit_rates, first_relevant_rank)
  → memory/session_store.py (SessionStore)
  → pymilvus (MilvusClient)
```

## Dependencies

### Internal Dependencies

- `app/chat/llm_client.py` — `get_embedding`, `rerank_with_retry`, `chat_with_tools`
- `app/rag/retrieval.py` — `retrieve_with_fallback`
- `app/memory/session_store.py` — `SessionStore`
- `app/config.py` — 各种配置 getter

### External Dependencies

- `pymilvus` — `MilvusClient`
- `sqlite3` — 用例生成时读取切片

## Dependents

- `app/chat/server.py` — 评测相关 API 路由

## Change Map

### 添加新的评测指标

优先检查: `metrics.py` (新增指标函数), `run_retrieval_eval.py` (计算和输出)

### 修改评测执行流程

优先检查: `run_retrieval_eval.py` → `stream_run_evaluation()`, `server.py` → `/api/eval/run`

### 修改测试用例格式

优先检查: `datasets/retrieval_test_cases.json` (数据), `run_retrieval_eval.py` (加载和验证), `generate_test_cases.py` (生成)

### 修改自动生成逻辑

优先检查: `generate_test_cases.py` → `stream_generate_cases()`, `server.py` → `/api/eval/auto_generate`

### 添加 Generation Eval

需要新建文件，可能修改 `__init__.py` 和 `server.py`

## Testing

本模块是评测工具本身，无独立测试。可通过以下方式验证：

```powershell
python app/eval/run_retrieval_eval.py                    # 运行评测
python app/eval/run_retrieval_eval.py --validate-only    # 仅校验数据集
python app/eval/generate_test_cases.py --count 10        # 生成测试用例
```

## Local Rules

- 评测任务与浏览器 SSE 连接解耦：刷新页面不会中断评测
- 评测状态通过 SQLite 持久化，支持现场恢复
- 单次只允许一个评测任务运行（通过 `evaluation_lock` 控制）
- 测试用例数据集使用 JSON 数组格式，与代码分离

## Common Pitfalls

- 修改 `retrieve_with_fallback` 的签名后，`run_retrieval_eval.py` 的调用方需要同步
- 修改测试用例格式后，`add_retrieval_case` 的验证逻辑和 `generate_test_cases` 的生成逻辑都需要更新
- 评测使用的 chunk_id 必须是 32 位 MD5，与 `chunk_id.py` 生成逻辑一致
- 自动生成依赖 LLM，生成质量受模型能力影响
