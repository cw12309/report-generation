**Phase 3 — 后处理（校验 → 序列化 → 状态更新 → 推送）**

```mermaid
sequenceDiagram
    participant Pipe as Pipeline
    participant RB as RecoveryBudget
    participant TR as ToolRegistry
    participant RS as ReportSchema
    participant LLM as Main LLM
    participant STM as STM
    participant CLI as CLI / Server
    actor User

    TR-->>Pipe: 工具执行结果

    Pipe->>Pipe: 筛选报告工具结果

    alt 多个报告工具结果
        Note over Pipe: 所有工具已执行；按 rag_search > edit_report > refine_report 仲裁终态
        Pipe->>Pipe: _arbitrate_report_tool(report_results)
    else 单个报告工具结果
        Note over Pipe: 直接采用该结果
    end

    alt 存在报告工具结果
        Pipe->>Pipe: 解析工具返回的 JSON

        alt 歧义状态（仅 rag_search）
            Note over Pipe: status=ambiguous，返回候选列表
            Pipe->>STM: 记录本轮对话
            Pipe-->>User: 显示歧义选项，等待选择
        else 工具返回 error
            Note over Pipe: is_final=false 或包含 error 字段
            Pipe->>Pipe: _classify_tool_error() 分类错误

            alt 用户可处理错误（NO_LAST_REPORT / LLM_SERVICE_ERROR）
                Note over Pipe: 直接返回给用户，不重试
                Pipe->>STM: 记录本轮对话
                Pipe-->>User: 返回错误消息
            else 未知错误（UNKNOWN_TOOL_ERROR）
                Note over Pipe: ⚠️ terminal：不重试，禁止再次进入 Main LLM
                Pipe-->>User: 返回 "报告处理失败，请重试。"
                Note over Pipe: 不写 STM
            else 可恢复错误（REPORT_PARSE_FAILED / EDIT_JUDGE_FAILED）
                Note over Pipe: ⚠️ 白名单内的错误才允许内部 retry
                Pipe->>RB: can_recover() ?
                alt 预算可用
                    RB->>Pipe: True，消费一次
                    Pipe->>LLM: 追加 error 上下文 → 调用 Main LLM 重试
                    LLM-->>Pipe: 返回 tool_calls
                    Note over Pipe: 重新执行工具
                else 预算耗尽
                    Pipe-->>User: 返回 "最终处理失败，请稍后重试。"
                    Note over Pipe: 不写 STM
                end
            else 不可恢复错误（EDIT_REPAIR_FAILED 等）
                Note over Pipe: ⚠️ terminal：edit 内部已做 Judge→Repair，不重试
                Pipe-->>User: 返回 "报告处理失败，请重试。"
                Note over Pipe: 不写 STM
            end
        else 正常报告
            Pipe->>RS: 解析报告 JSON → 结构化对象
            Note over RS: extract_json 4策略提取 → normalize（旧格式兼容）→ validate（6项校验）
            alt 解析成功
                RS-->>Pipe: 结构化报告
                Pipe->>RS: 序列化为规范格式
                RS-->>Pipe: 规范 JSON 字符串
                Pipe->>Pipe: 更新 last_report 缓存
                Pipe->>STM: 记录本轮对话历史
                Pipe->>CLI: emit 推送报告
                CLI-->>User: 输出结构化报告
            else 解析失败 (ReportSchemaError)
                Note over Pipe: ⚠️ REPORT_SCHEMA_INVALID：terminal，不重试
                RS-->>Pipe: 校验失败
                Pipe-->>User: 返回 "服务暂时不可用，请稍后重试。"
                Note over Pipe: 不写 STM
            end
        end
    else 不存在报告工具结果
        Note over Pipe: 转入 Optional Final LLM（详见 Phase 2）
    end
```

## 错误分类与 Recovery

| 错误码                  | 分类         | 行为                                         | 写 STM        |
| ----------------------- | ------------ | -------------------------------------------- | ------------- |
| `NO_LAST_REPORT`        | 用户可处理   | 直接返回给用户，提示"没有可编辑的报告"       | ✅            |
| `LLM_SERVICE_ERROR`     | 用户可处理   | 直接返回给用户，提示"LLM 服务异常"           | ✅            |
| `REPORT_PARSE_FAILED`   | 可恢复       | Pipeline 内部 retry 一次，调用 Main LLM 重试 | ❌ 仅成功时写 |
| `EDIT_JUDGE_FAILED`     | 可恢复       | Pipeline 内部 retry 一次，调用 Main LLM 重试 | ❌ 仅成功时写 |
| `EDIT_REPAIR_FAILED`    | **terminal** | edit 内部已完成 Judge→Repair 仍失败，不重试  | ❌            |
| `UNKNOWN_TOOL_ERROR`    | **terminal** | 不重试，禁止再次进入 Main LLM                | ❌            |
| `REPORT_SCHEMA_INVALID` | **terminal** | Schema 校验失败，不重试                      | ❌            |

**关键约束**：

- 可恢复错误白名单：`_RECOVERABLE_REPORT_ERRORS = {"REPORT_PARSE_FAILED", "EDIT_JUDGE_FAILED"}`
- `EDIT_REPAIR_FAILED` 不在白名单：edit_report 内部已消耗一次 Judge→Repair 预算，Pipeline 不应再重试
- 所有 terminal 错误和 recovery 耗尽错误**不写入 STM**，避免污染后续多轮记忆
- 恢复预算 `RecoveryBudget(max_attempts=1)`，每次 pipeline 调用独立
