# AGENTS.md — app/memory/

## Purpose

会话记忆系统。负责短期对话历史管理、实体槽位追踪、会话持久化存储和记忆检索注入。为 Agent 提供"记住刚才说了什么"和"理解用户在说什么"的能力。

不负责：LLM 调用（由 `llm_client.py` 负责）、RAG 检索（由 `app/rag/` 负责）、报告生成（由 `tools/` 负责）。

## File Map

| File                            | Responsibility                                                                               |
| ------------------------------- | -------------------------------------------------------------------------------------------- |
| `__init__.py`                   | 公共导出入口                                                                                 |
| `short_term.py`                 | `ShortTermMemory`：内存对话历史管理，含智能摘要淘汰                                          |
| `entity_tracker.py`             | `EntityTracker`：实体槽位提取（LLM JSON + 规则双引擎）、意图检测、上下文消解                 |
| `session_store.py`              | `SessionStore`：SQLite 会话、KB/Eval、API Deployment 与 conversation 映射持久化             |
| `retriever.py`                  | `MemoryRetriever`：基于语义相似度的 STM 历史片段检索注入                                     |
| `chat_test.py`                  | 手动测试终端：验证 STM 和 EntityTracker 行为                                                 |
| `generate_entity_config.py`     | 从 metadata.json 智能生成实体提取配置                                                        |
| `generate_metadata_keywords.py` | 从 metadata.json 自动生成关键词和诊断→部位映射                                               |

## Symbol Map

### `short_term.py`

- `ShortTermMemory` — 短期记忆管理器
  - `add_turn(session_id, user_msg, assistant_msg)` — 添加一轮对话
  - `get_history(session_id)` → list — 获取对话历史
  - `get_summaries(session_id)` → list — 获取淘汰轮次的摘要
  - `clear(session_id)` — 清空会话
  - `session_info(session_id)` → dict — 会话统计信息

### `entity_tracker.py`

- `EntityTracker` — 实体槽位追踪器
  - `slots` — 槽位 dict: `{modality, body_part, clinical_history, diagnosis, intent}`
  - `update_from_query(query)` → dict — 从用户输入更新槽位
  - `detect_intent(query)` → str — 意图检测：`new_session/append/switch`
  - `resolve_context(query)` → str — 上下文消解（补全省略信息）
  - `apply_switch(query)` — 切换意图时清空旧槽位
  - `to_context_prompt()` → str — 生成上下文 Prompt 片段

### `session_store.py`

- `SessionStore` — SQLite 持久化存储
  - `create_session(session_id)` — 创建会话
  - `session_exists(session_id)` → bool
  - `load_session(session_id)` → dict — 加载完整会话数据
  - `save_turn(session_id, turn_index, user_input, assistant_output)` — 保存对话轮次
  - `save_state(session_id, entity_slots, last_report, selected_kbs)` — 保存会话状态
  - `save_thinking(session_id, turn_index, events)` — 保存思考过程
  - `list_knowledge_bases(status)` → list — 列出知识库
  - `list_knowledge_base_chunks(kb_id, page, size)` → dict — 分页查询切片
  - `save_evaluation_result(result)` / `save_evaluation_runtime_state(...)` — 评测持久化
  - `create/list/get/update/set/rotate/soft_delete_api_deployment(...)` — Deployment 生命周期与健康状态
  - `create/get/touch_api_conversation(...)` — 外部 conversation 到内部 Session 的隔离映射

### `retriever.py`

- `MemoryRetriever` — 记忆检索注入器
  - `index_stm(history)` — 增量索引对话历史
  - `search(query, top_k_stm)` → dict — 检索最相关对话片段

## Entry Points

理解本模块的推荐顺序：

1. `__init__.py` — 了解 public API
2. `session_store.py` — 理解数据持久化结构
3. `entity_tracker.py` — 理解实体追踪机制
4. `short_term.py` — 理解对话历史管理
5. `retriever.py` — 理解记忆检索注入

## Public API

其他模块应从 `memory/__init__.py` 导入：

```python
from memory import EntityTracker, ShortTermMemory, SessionStore
```

## File Relationships

```
server.py
  → session_store.py (SessionStore)
  → entity_tracker.py (EntityTracker)
  → short_term.py (ShortTermMemory)

pipeline.py
  → retriever.py (MemoryRetriever)
  → entity_tracker.py (slots, update_from_query, detect_intent, resolve_context)
  → short_term.py (add_turn, get_history, clear)
```

`entity_tracker.py`:

- uses `prompt/` → `load_prompt` (for entity_extraction.md)

`session_store.py`:

- uses `data_pipeline/chunk_id.py` → `generate_chunk_id`

`retriever.py`:

- uses `numpy` for vector similarity

## Dependencies

### Internal Dependencies

- `app/prompt/` — `load_prompt` (entity_extraction.md, summarize.md)
- `app/data_pipeline/chunk_id.py` — `generate_chunk_id`
- `app/config.py` — `get_max_rounds`, `get_decay_factor`

### External Dependencies

- `sqlite3` — 标准库
- `numpy` — 向量相似度计算

## Dependents

- `app/chat/server.py` — 创建和管理所有记忆组件
- `app/chat/pipeline.py` — 使用 EntityTracker 和 ShortTermMemory
- `app/chat/main.py` — CLI 模式使用
- `app/eval/run_retrieval_eval.py` — 使用 SessionStore 读取 evaluations
- `app/chat/deployments.py` / `app/chat/server.py` — Deployment 管理、鉴权和多轮 Session 映射

## Change Map

### 修改会话持久化结构

优先检查: `session_store.py` (表结构), `server.py` → `_get_or_create_session()` (恢复逻辑), `server.py` → `chat()` (保存逻辑)

### 修改实体提取规则

优先检查: `entity_tracker.py` (关键词列表、提取逻辑), `prompt/entity_extraction.md`

检查 switch 规则时必须区分检查级切换与报告编辑：只有带明确切换表达及检查类型/部位/检查目标的输入可清状态；“改成数值、修改报告、润色”等报告操作不能触发 switch。

### 修改对话历史管理

优先检查: `short_term.py`, `pipeline.py` → `run_pipeline()` (调用方)

### 修改记忆检索注入

优先检查: `retriever.py`, `pipeline.py` → `run_pipeline()` (注入点)

### 添加新的知识库操作

优先检查: `session_store.py` (SQL 操作), `server.py` (路由)

### 修改 Deployment 持久化或 conversation 映射

优先检查: `session_store.py` 的 `api_deployments`、关系表和 `api_conversations`, `chat/deployments.py`, `chat/server.py` 管理/internal 路由，以及 `test_api_deployments.py`/`test_api_deployment_routes.py`。

## Testing

关联测试：

- `app/test/test_memory.py` — 记忆系统测试
- `app/test/test_knowledge_base_chunks.py` — 知识库切片测试
- `app/test/test_api_deployments.py` — Deployment/KB/conversation 存储测试
- `app/test/test_api_deployment_routes.py` — 路由级多轮恢复与软删除测试
- `app/memory/chat_test.py` — 手动测试终端

## Local Rules

- `ShortTermMemory` 是纯内存存储，重启丢失；持久化依赖 `SessionStore`
- `EntityTracker` 每个会话持有独立实例，无需加锁
- `detect_intent()` 不得用单独的“改/换/修改/改成”作为 switch 依据；报告操作必须保留 STM、Entity 与 Last Report
- `SessionStore` 是 SQLite 的唯一访问层，其他模块不直接操作 SQLite
- Deployment API Key 只保存 SHA-256 摘要与显示前缀；明文不得进入任何 SQLite 字段
- Deployment 软删除不删除既有 Session；外部 conversation ID 必须与 `deployment_id` 组成隔离键
- `generate_entity_config.py` 和 `generate_metadata_keywords.py` 是辅助脚本，不是运行时依赖

## Common Pitfalls

- 修改 `session_store.py` 的表结构后，需要更新 `_get_or_create_session()` 的恢复逻辑
- 修改 EntityTracker 的槽位字段后，`to_context_prompt()` 和 `pipeline.py` 的上下文注入都需要同步
- 修改 `ShortTermMemory` 的 max_rounds 后，注意 `config.yml` 中的 `short_term_memory.max_rounds`
- SQLite WAL 模式下注意并发写入
