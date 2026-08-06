# Memory 模块

该模块只负责当前会话的上下文管理、实体追踪，以及 SQLite 会话/知识库持久化。
系统不会学习、读取或注入跨会话的用户偏好，也不会保存历史修正样例。

## 当前组成

```text
app/memory/
├── __init__.py
├── entity_tracker.py   # 当前会话的影像实体槽位提取与上下文消解
├── short_term.py       # 当前会话最近若干轮对话与摘要
├── retriever.py        # 仅针对 STM 历史的语义检索
├── session_store.py    # 会话、知识库和知识库切片的 SQLite 持久化
└── chat_test.py        # STM + EntityTracker 交互测试终端
```

## 数据流

```text
用户输入
  ├─ EntityTracker：提取 modality、body_part、diagnosis 等当前会话实体
  ├─ ShortTermMemory：读取当前 session_id 的近期对话
  ├─ MemoryRetriever：从本会话 STM 中选取相关历史片段
  └─ PromptBuilder：组合实体上下文、任务模板和本会话已有报告
```

这里没有跨会话偏好同步。一个会话中的实体或表达习惯不会写入另一个会话的 Prompt。

## 边界说明

- STM 只服务于当前 `session_id`；服务端可以从 SQLite 的明确历史轮次恢复该会话。
- EntityTracker 只提取和维护当前会话实体，不自动形成用户画像。
- 系统不存在偏好索引、修正样例库或会话结束后的跨会话同步步骤。
- `PromptBuilder` 只组合当前实体上下文、任务模板和当前会话已有报告。

## EntityTracker

`EntityTracker` 保存当前会话的结构化实体槽位，并支持：

- 从用户输入提取检查模态、部位、诊断和临床信息；
- 对“这个部位”“继续”等依赖上下文的输入进行消解；
- 检查意图切换并清理不再适用的旧槽位；
- 通过 `to_context_prompt()` 生成当前会话实体上下文。

示例：

```python
from memory import EntityTracker

tracker = EntityTracker()
tracker.update_from_query("CT 头颅 脑出血")
resolved = tracker.resolve_context("再看一下这个部位")
context = tracker.to_context_prompt()
```

## ShortTermMemory

`ShortTermMemory` 按 `session_id` 隔离，只维护当前会话最近若干轮对话与可选摘要。

```python
from memory import ShortTermMemory

stm = ShortTermMemory(max_rounds=5)
stm.add_turn("session_001", "CT 头颅", "已生成报告")
history = stm.get_history("session_001")
messages = stm.build_messages("session_001", "系统提示", "继续完善")
```

意图切换或用户清空会话时，应同时清理 STM、EntityTracker 和当前报告引用。

## MemoryRetriever

`MemoryRetriever` 只索引当前会话的 STM 历史，不读取任何跨会话数据。

```python
from memory.retriever import MemoryRetriever

retriever = MemoryRetriever(get_embedding)
retriever.index_stm(stm.get_history("session_001"))
relevant = retriever.search_relevant("继续完善头颅报告", top_k_stm=3)
```

返回格式：

```python
{"stm": ["[user] CT 头颅", "[assistant] 已生成报告"]}
```

## SessionStore

`SessionStore` 使用 `data/sessions.db` 持久化：

- 会话元数据和对话轮次；
- 当前会话实体槽位、上一轮报告、已选知识库；
- Agent 思考事件；
- `knowledge_bases` 知识库状态和进度；
- `knowledge_base_chunks` 切片原文，以与 Markdown、Milvus 共用的 32 位 MD5 `chunk_id` 为主键。

SQLite 会话持久化用于恢复明确的会话内容，不会自动归纳或传播用户偏好。

## 测试

运行内存模块单元测试：

```bash
python -m app.test.test_memory
```

运行交互测试终端：

```bash
python app/memory/chat_test.py --debug
```

测试重点包括 STM 清理、实体提取、上下文消解，以及 EntityTracker 上下文注入。
