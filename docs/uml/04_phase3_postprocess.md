**Phase 3 — 后处理（Post-LLM）**

```mermaid
sequenceDiagram
    participant Pipe as Pipeline
    participant TR as ToolRegistry
    participant RS as ReportSchema
    participant CLI as CLI / Server
    actor User

    TR-->>Pipe: result

    Pipe->>RS: parse_report_output(result.content)
    RS-->>Pipe: {"results": [{"影像学表现":"...", "诊断意见":"..."}]}

    Pipe->>RS: serialize_report(structured)
    RS-->>Pipe: canonical JSON string

    Pipe->>Pipe: last_report[0] = canonical_report
    Pipe->>CLI: stm.add_turn(session_id, query, display)
    CLI-->>User: 📋 结构化报告
```
