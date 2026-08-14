**Phase 2 — LLM 编排决策（工具注册 → 记忆检索 → Prompt 构建 → 决策与调度 + Recovery）**

```mermaid
sequenceDiagram
    participant Pipe as Pipeline
    participant RB as RecoveryBudget
    participant TR as ToolRegistry
    participant MR as MemoryRetriever
    participant PB as PromptBuilder
    participant LLM as LLM Client
    participant STM as STM

    Pipe->>TR: 构建工具注册中心
    TR-->>Pipe: 注册3个工具: rag_search、edit_report、refine_report

    Pipe->>MR: 检索相关历史对话
    MR-->>Pipe: 3条相关对话片段

    Pipe->>PB: 构建系统提示词
    Note over PB: 注入 EntityTracker 上下文<br/>注入 last_report（如有）
    PB-->>Pipe: 完整系统提示 + 历史消息

    Pipe->>Pipe: 计算上下文使用率
    Pipe->>LLM: 第一次 LLM 调用（带 tools）
    LLM-->>Pipe: 返回 content + tool_calls

    alt 无工具调用，有 content
        alt content 是合法报告 JSON
            Note over Pipe: ⚠️ REPORT_TOOL_REQUIRED：LLM 绕过工具直接输出报告
            Pipe->>RB: can_recover() ?
            alt 预算可用
                RB->>Pipe: True，消费一次
                Pipe->>LLM: 追加提示 → 要求必须选择工具
                LLM-->>Pipe: 返回 tool_calls（恢复成功）
                Note over Pipe: 继续执行工具调用
            else 预算耗尽
                RB->>Pipe: False
                Pipe-->>Pipe: 返回 "本次报告处理未能完成，请稍后再试。"
                Note over Pipe: 不写 STM
            end
        else content 是序列化工具调用
            Note over Pipe: ⚠️ INVALID_TOOL_CALL：LLM 文本中嵌入了工具调用
            Pipe->>Pipe: _parse_serialized_tool_call()
            alt 解析成功且工具名合法
                Note over Pipe: 恢复为正式 tool_calls，继续执行
            else 解析失败
                Pipe->>RB: can_recover() ?
                alt 预算可用
                    RB->>Pipe: True，消费一次
                    Pipe->>LLM: 追加提示 → 要求标准 function calling
                    LLM-->>Pipe: 返回 tool_calls（恢复成功）
                else 预算耗尽
                    RB->>Pipe: False
                    Pipe-->>Pipe: 返回 "服务暂时不可用，请稍后重试。"
                    Note over Pipe: 不写 STM
                end
            end
        else 正常回复
            Pipe->>STM: 记录本轮对话
            Pipe-->>Pipe: 返回直接回复
        end
    else 无工具调用，无 content
        Pipe-->>Pipe: emit error，返回“抱歉，模型未返回有效回复。”
    else 有工具调用
        Note over Pipe: 保存 assistant tool_calls
        Pipe->>TR: 依次执行每个工具
        TR-->>Pipe: 收集全部 ToolResult

        alt 多个报告工具同时调用
            Note over Pipe: 工具均已执行；这里只仲裁报告终态
            Pipe->>Pipe: _arbitrate_report_tool()
            Note over Pipe: 优先级：rag_search > edit_report > refine_report
        end

        alt 调用了报告工具（rag_search/edit/refine）
            Note over Pipe: 进入 Phase 3 后处理（含工具错误分类与 Recovery）
        else 调用了非报告工具
            Pipe->>LLM: 二次 LLM 总结工具结果
            LLM-->>Pipe: 自然语言回复
            alt 二次 LLM 输出序列化工具调用
                Note over Pipe: ⚠️ INVALID_FINAL_TOOL_CALL
                Pipe->>RB: can_recover() ?
                alt 预算可用
                    RB->>Pipe: True，消费一次
                    Pipe->>LLM: 追加提示 → 普通文本回复
                    LLM-->>Pipe: 正常文本（恢复成功）
                else 预算耗尽
                    Pipe-->>Pipe: 兜底 "操作完成，请查看结果。"
                end
            end
            Pipe->>STM: 记录本轮对话
            Pipe-->>Pipe: 返回回复
        end
    end
```

## Recovery 机制说明

Pipeline 内置**错误自恢复机制**，在 LLM 偶发行为异常时自动恢复，避免直接暴露内部错误给用户。

| 场景                                  | 触发条件                                            | 恢复策略                                                       | 预算耗尽后                            |
| ------------------------------------- | --------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------- |
| Report Tool required                  | LLM 在 content 中直接返回合法报告，未走 tool_calls  | 追加提示要求选择工具，重试 1 次                                | “本次报告处理未能完成，请稍后再试。”  |
| Invalid textual tool call             | LLM 在文本中序列化工具调用（如 `<tool_call>`）      | 先解析 JSON/函数格式；解析失败才追加 function calling 提示重试 | “服务暂时不可用，请稍后重试。”        |
| Multiple report tools                 | LLM 同时调用多个报告工具                            | 全部执行后按 rag_search > edit_report > refine_report 选择终态 | —                                     |
| Invalid optional-final tool call      | 非报告工具的二次 LLM 输出序列化工具调用             | 追加提示要求普通文本，重试 1 次                                | 兜底“操作完成，请查看结果。”          |

**关键约束**：

- 顶层 `run_pipeline()` 创建 `RecoveryBudget(max_attempts=1)`；递归恢复显式传入同一实例，不跨用户轮次污染
- Main LLM 格式恢复、可恢复报告工具错误和 Optional Final 格式恢复共享这一次预算
- 内部错误（`RECOVERY_FAILED` / `RECOVERY_EXHAUSTED` / `UNKNOWN_TOOL_ERROR`）**不写入 STM**，避免污染后续多轮记忆
- 用户可处理的业务错误（`NO_LAST_REPORT` / `LLM_SERVICE_ERROR`）直接返回并写入 STM
