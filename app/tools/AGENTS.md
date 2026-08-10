# AGENTS.md — app/tools/

## Purpose

Tool Calling 工具系统。提供 OpenAI 兼容的 Tool Calling 机制，包括工具注册中心、三个业务工具（RAG 检索生成、报告编辑、报告重写）和公共工具函数。工具由 `pipeline.py` 注册后供 LLM 自主调用。

不负责：Agent 主流程编排（由 `pipeline.py` 负责）、LLM 调用（由 `llm_client.py` 负责）、RAG 检索细节（由 `app/rag/` 负责）。

## File Map

| File             | Responsibility                                                   |
| ---------------- | ---------------------------------------------------------------- |
| `__init__.py`    | 公共导出入口，re-export 所有 public symbols                      |
| `registry.py`    | `ToolRegistry` 类：工具注册、Schema 生成、异步执行               |
| `rag_tool.py`    | `rag_search` 工具：多路召回 + Rerank + 歧义检测 + 结构化报告生成 |
| `edit_tool.py`   | `edit_report` 工具：基于 Last Report 编辑、事实检查、LLM Judge 与单次 Repair |
| `refine_tool.py` | `refine_report` 工具：调整报告风格/详细程度/语气                 |
| `utils.py`       | 旧通用 JSON 提取 helper；正式报告解析已统一迁移到 `chat/report_schema.py` |

## Symbol Map

### `registry.py`

- `ToolRegistry` — 工具注册中心
  - `register(name, schema, handler)` — 注册工具
  - `get_tools_schema()` — 返回 OpenAI 兼容 tools 定义
  - `execute(tool_call_id, name, arguments)` — 异步执行工具，返回 `ToolResult`
- `ToolResult` — 工具执行结果 dataclass，含 `content` 和 `is_final` 标记

### `rag_tool.py`

- `RAG_SEARCH_SCHEMA` — rag_search 工具的 OpenAI function schema
- `create_rag_search_handler(chat_fn, entity_tracker, get_embedding_fn, search_reports_fn, _emit_fn, last_report, selected_diagnosis, last_ambiguity)` — 创建 rag_search handler 的工厂函数
- `detect_ambiguity(reranked_entities, threshold)` — 细粒度歧义检测

### `edit_tool.py`

- `EDIT_REPORT_SCHEMA` — edit_report 工具的 OpenAI function schema
- `create_edit_report_handler(chat_fn, _emit_fn, last_report)` — 只接收 instruction，强制使用 Session Last Report；编辑后执行事实检查与受约束 Judge，失败时只 Repair 一次

### `refine_tool.py`

- `REFINE_REPORT_SCHEMA` — refine_report 工具的 OpenAI function schema
- `create_refine_report_handler(chat_fn, entity_tracker, _emit_fn, last_report)` — 只接收模型的 style，并强制使用注入的 Session Last Report

### `utils.py`

- `extract_json(text)` — 从 LLM 输出提取 JSON（多策略：code block → 括号计数 → 修复 → 直接解析）

## Entry Points

理解本模块的推荐顺序：

1. `__init__.py` — 了解 public API
2. `registry.py` — 理解 Tool Calling 机制
3. `rag_tool.py` — 最核心的业务工具
4. `edit_tool.py`, `refine_tool.py` — 辅助工具

## Public API

其他模块应从 `tools/__init__.py` 导入：

```python
from tools import ToolRegistry, ToolResult, create_rag_search_handler, ...
```

## File Relationships

```
pipeline.py
  → registry.py (ToolRegistry)
  → rag_tool.py (create_rag_search_handler)
  → edit_tool.py (create_edit_report_handler)
  → refine_tool.py (create_refine_report_handler)
```

三个报告工具：

- uses `chat/report_schema.py` → Canonical parse/validate 与 edit/refine preservation
- `edit_tool.py` 额外执行受约束 LLM Judge；Schema、事实检查或 Judge 失败共享一次 Edit Repair 预算

`rag_tool.py`:

- uses `prompt/builder.py` → `PromptBuilder`
- uses `prompt/` → `load_prompt` (for report_generation, multi_disease templates)
- uses `langsmith` → `@traceable`

`edit_tool.py`:

- uses `prompt/` → `load_prompt` (for edit.md template)

`refine_tool.py`:

- uses `prompt/` → `load_prompt` (for refine.md template)

## Dependencies

### Internal Dependencies

- `app/prompt/` — 加载 Prompt 模板
- `app/chat/report_schema.py` — 唯一正式报告结构与程序级校验
- `app/chat/llm_client.py` — `chat_stream` (通过注入的 `chat_fn`)

### External Dependencies

- `langsmith` — `@traceable` 装饰器

## Dependents

- `app/chat/pipeline.py` — `_build_tool_registry()` 注册所有工具
- `app/chat/server.py` — `/internal/report/generate` 直接使用 `create_rag_search_handler`

## Change Map

### 添加新工具

通常检查: `__init__.py` (export), `registry.py` (无需修改), 新建工具文件, `pipeline.py` → `_build_tool_registry()` (注册)

### 修改 rag_search 行为

优先检查: `rag_tool.py`, `prompt/report_generation.md`, `prompt/multi_disease.md`, `prompt/structure.md`

### 修改 Tool Calling 机制

优先检查: `registry.py`, `pipeline.py` → `run_pipeline()` (调用方式)

### 修改 JSON 提取逻辑

正式报告优先检查: `chat/report_schema.py` → `parse_report_output()` / `parse_report_with_repair()` 与三个 handler。`utils.py::extract_json()` 只影响仍显式调用它的旧通用路径。

## Testing

本模块无独立测试文件。工具行为通过以下测试间接覆盖：

- `app/test/test_rag_planning.py`
- `app/test/test_memory.py`
- `app/test/test_report_pipeline.py`
- 集成测试通过 `/api/chat` SSE 端点

## Local Rules

- 工具 handler 通过工厂函数创建，接收注入的依赖（chat_fn, entity_tracker 等），不直接 import 外部模块
- 三个报告工具只有在产出合法 Canonical StructuredReport 时才返回 `_is_final: true`；失败返回 error 且由 Pipeline 直接终止
- edit/refine Schema 不暴露 `current_report`，handler 也必须忽略同名额外参数并只使用 Session Last Report
- 新增工具必须在 `__init__.py` 中 export
- 工具 Schema 必须符合 OpenAI function calling 格式

## Common Pitfalls

- 修改工具 Schema 后，检查 `pipeline.py` 中 `_build_tool_registry()` 是否需要更新
- 修改 `report_schema.py` 后，检查三个报告工具、Last Report、Public SSE 与 `json_to_display()`
- 报告工具的非法 JSON 禁止包装成 raw report；edit 的 Schema/事实/Judge 校验在一次 repair 后仍失败必须返回 error
- 工具 handler 必须是 async 函数
