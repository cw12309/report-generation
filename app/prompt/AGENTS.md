# AGENTS.md — app/prompt/

## Purpose

Prompt 模板管理。存放所有 LLM System Prompt 的 Markdown 模板文件和统一的 Prompt 构建器。`load_prompt()` 提供缓存加载，`PromptBuilder` 负责拼装 Entity 上下文 + 模板 + Last Report。

不负责：LLM 调用（由 `llm_client.py` 负责）、业务逻辑。

## File Map

| File                   | Responsibility                                               |
| ---------------------- | ------------------------------------------------------------ |
| `__init__.py`          | `load_prompt(name)` 和 `reload_prompt(name)` 函数            |
| `builder.py`           | `PromptBuilder`：统一拼装 Entity 上下文 + 模板 + Last Report |
| `structure.md`         | 新报告生成约束：唯一 Canonical `results[]` Schema，可选内部 reasoning |
| `report_generation.md` | 报告生成任务 Prompt（rag_search 使用）                       |
| `multi_disease.md`     | 多疾病报告生成 Prompt                                        |
| `entity_extraction.md` | 实体提取 Prompt（EntityTracker 使用）                        |
| `rewrite.md`           | 查询改写 Prompt（query_rewrite 使用）                        |
| `edit.md`              | Canonical 报告最小编辑 Prompt（edit_tool 使用）              |
| `refine.md`            | Canonical 报告风格润色与事实不可变 Prompt（refine_tool 使用）|
| `tool_orchestrator.md` | Tool Calling 编排器 System Prompt                            |
| `react_system.md`      | ReAct 模式 System Prompt                                     |
| `react_simple.md`      | 简化版 ReAct Prompt                                          |
| `chat.md`              | 通用对话 Prompt                                              |
| `summarize.md`         | 对话摘要 Prompt（ShortTermMemory 淘汰时使用）                |

## Symbol Map

### `__init__.py`

- `load_prompt(name)` → str — 加载 .md 模板（带缓存）
- `reload_prompt(name)` → str — 强制重新加载

### `builder.py`

- `PromptBuilder.build(template_name, entity_context, last_report, last_report_label)` → str — 构建完整 System Prompt

## Entry Points

理解本模块的推荐顺序：

1. `__init__.py` — 理解加载机制
2. `builder.py` — 理解拼装逻辑
3. 按需阅读具体 .md 模板

## Public API

```python
from prompt import load_prompt
from prompt.builder import PromptBuilder

# 加载模板
template = load_prompt("report_generation")

# 构建 Prompt
sys_prompt = PromptBuilder.build("structure", entity_context=..., last_report=...)
```

## File Relationships

所有 .md 模板通过 `load_prompt(name)` 加载，name 为文件名去 .md 扩展名。

`builder.py`:

- uses `prompt/` → `load_prompt`
- uses `langsmith` → `@traceable`

## Dependencies

### Internal Dependencies

- 无内部 Python 依赖

### External Dependencies

- `langsmith` — `@traceable` (builder.py)

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

优先检查: `builder.py` → `PromptBuilder.build()`

### 添加新的 Prompt 模板

创建 .md 文件 → 在调用方通过 `load_prompt("new_name")` 使用

### 修改报告结构

优先检查: `structure.md`, `edit.md`, `refine.md`, `chat/report_schema.py`, 三个报告工具与 `chat/pipeline.py`

## Testing

本模块无独立测试。Prompt 模板的变更通过集成测试和手动验证。

## Local Rules

- 所有 Prompt 模板必须是 .md 文件，放在本目录
- 模板文件名不含 .md 扩展名即为 `load_prompt` 的 name 参数
- 三个报告 Prompt 只能输出 Canonical `results[{影像学表现, 诊断意见}]`；Prompt 不是校验边界，程序校验由 `chat/report_schema.py` 承担
- `PromptBuilder` 的拼接顺序：Entity 上下文 → 模板 → Last Report（用 `---` 分隔）
- 模板修改后，`load_prompt` 的缓存不会自动失效，需重启或调用 `reload_prompt`

## Common Pitfalls

- 修改模板后忘记重启服务，导致缓存未更新
- 修改 `structure.md` 的 JSON Schema 后，`extract_json` 的解析逻辑可能需要同步
- 修改 `entity_extraction.md` 后，`EntityTracker` 的 LLM 提取结果格式可能变化
- 修改 `tool_orchestrator.md` 后，Tool Calling 的 LLM 行为可能改变
