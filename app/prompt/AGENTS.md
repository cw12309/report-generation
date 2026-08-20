# AGENTS.md — app/prompt/

## Purpose

Prompt 模板管理。存放所有 LLM System Prompt 的 Markdown 模板文件和统一的 Prompt 构建器。`load_prompt()` 提供缓存加载，`PromptBuilder` 负责拼装 Entity 上下文 + 模板 + Last Report。

不负责：LLM 调用（由 `llm_client.py` 负责）、业务逻辑。

## File Map

| File                   | Responsibility                                               |
| ---------------------- | ------------------------------------------------------------ |
| `__init__.py`          | `load_prompt(name)` 和 `reload_prompt(name)` 函数            |
| `system_prompt_builder.py` | `PromptBuilder`：统一拼装 Entity 上下文 + 模板 + Last Report |
| `report_prompt/`       | 报告相关 Prompt（structure、report_generation、edit、refine、multi_disease） |
| `agent_prompt/`        | Agent/对话编排 Prompt（tool_orchestrator、chat、react_system、react_simple） |
| `rag_prompt/`          | RAG/检索 Prompt（rewrite） |
| `memory_prompt/`       | 记忆/上下文 Prompt（entity_extraction、summarize） |

## Symbol Map

### `__init__.py`

- `load_prompt(name)` → str — 加载 .md 模板（带缓存）
- `reload_prompt(name)` → str — 强制重新加载

### `system_prompt_builder.py`

- `PromptBuilder.build(template_name, entity_context, last_report, last_report_label)` → str — 构建完整 System Prompt

## Entry Points

理解本模块的推荐顺序：

1. `__init__.py` — 理解加载机制
2. `system_prompt_builder.py` — 理解拼装逻辑
3. 按需阅读具体 .md 模板

## Public API

```python
from prompt import load_prompt
from prompt.system_prompt_builder import PromptBuilder

# 加载模板
template = load_prompt("report_prompt/structure")

# 构建 Prompt
sys_prompt = PromptBuilder.build("structure", entity_context=..., last_report=...)
```

## File Relationships

所有 .md 模板通过 `load_prompt(name)` 加载，name 为子目录 + 文件名去 .md 扩展名，如 `"report_prompt/structure"`。

`system_prompt_builder.py`:

- uses `prompt/` → `load_prompt`
- uses `langsmith` → `@traceable`

## Dependencies

### Internal Dependencies

- 无内部 Python 依赖

### External Dependencies

- `langsmith` — `@traceable` (system_prompt_builder.py)

## Dependents

- `app/tools/rag_tool.py` — 使用 `load_prompt` + `PromptBuilder`
- `app/tools/edit_tool.py` — 使用 `load_prompt`
- `app/tools/refine_tool.py` — 使用 `load_prompt`
- `app/memory/entity_tracker.py` — 使用 `load_prompt` (entity_extraction.md)
- `app/memory/short_term.py` — 使用 `load_prompt` (summarize.md)
- `app/rag/query_rewrite.py` — 使用 `load_prompt` (rewrite.md)
- `app/chat/pipeline.py` — 使用 `PromptBuilder`

## Change Map

### 修改 Prompt 模板内容

直接编辑对应的 .md 文件即可，`load_prompt` 有缓存，修改后需重启服务或调用 `reload_prompt(name)`

### 修改 Prompt 拼装逻辑

优先检查: `system_prompt_builder.py` → `PromptBuilder.build()`

### 添加新的 Prompt 模板

创建 .md 文件 → 在调用方通过 `load_prompt("new_name")` 使用

### 修改报告结构

优先检查: `structure.md`, `edit.md`, `refine.md`, `chat/report_schema.py`, 三个报告工具与 `chat/pipeline.py`

## Testing

本模块无独立测试。Prompt 模板的变更通过集成测试和手动验证。

## Local Rules

- 所有 Prompt 模板必须是 .md 文件，放在对应的子目录（report_prompt/、agent_prompt/、rag_prompt/、memory_prompt/）
- 模板加载路径为 `子目录/文件名`（去 .md），如 `"report_prompt/structure"`
- 三个报告 Prompt 只能输出 Canonical `results[{影像学表现, 诊断意见}]`；Prompt 不是校验边界，程序校验由 `chat/report_schema.py` 承担
- `PromptBuilder` 的拼接顺序：Entity 上下文 → 模板 → Last Report（用 `---` 分隔）
- 模板修改后，`load_prompt` 的缓存不会自动失效，需重启或调用 `reload_prompt`

## Common Pitfalls

- 修改模板后忘记重启服务，导致缓存未更新
- 修改 `structure.md` 的 JSON Schema 后，`extract_json` 的解析逻辑可能需要同步
- 修改 `entity_extraction.md` 后，`EntityTracker` 的 LLM 提取结果格式可能变化
- 修改 `tool_orchestrator.md` 后，Tool Calling 的 LLM 行为可能改变