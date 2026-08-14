**总览：报告生成管线全流程**

```mermaid
flowchart TD
    START([👤 用户输入]) --> CACHE{"🎯 缓存命中？<br/>（歧义选项 / 匹配诊断）"}

    CACHE -->|命中| FAST["快速通道：跳过 Phase 1<br/>保留槽位，直接进入编排"]
    CACHE -->|未命中| P1

    subgraph P1["01 输入处理"]
        direction TB
        A1["实体提取"] --> A2["意图检测"] --> A3["上下文消解"] --> A4["查询改写"]
    end

    P1 --> INTERCEPT{"拦截检查"}
    INTERCEPT -->|模糊输入| ASK1["追问用户意图"]
    INTERCEPT -->|缺少模态| ASK2["追问检查类型"]
    INTERCEPT -->|通过| P2

    FAST --> P2

    subgraph P2["02 LLM 编排与 Recovery"]
        direction TB
        B1["注册工具 / 记忆检索 / 构建 Prompt"] --> B2["LLM 决策"]
        B2 --> FORMAT{"返回格式有效？"}
        FORMAT -->|直接报告 / 文本化调用| RECOVER["解析或重试一次<br/>共享 RecoveryBudget"]
        RECOVER -->|恢复成功| B3
        RECOVER -.->|失败 / 预算耗尽| ERR3["返回稳定错误<br/>不写 STM"]
        FORMAT -->|有效| B3{"调用哪个工具？"}
        B3 -->|rag_search| RAG["03 rag_search 检索 + 生成报告"]
        B3 -->|edit_report| EDIT["05 编辑已有报告"]
        B3 -->|refine_report| REFINE["07 精炼优化报告"]
        B3 -->|非报告工具| NON_REPORT["执行工具"]
        B3 -->|普通文本| DIRECT["直接回复并写 STM"]
    end

    RAG --> AMB{"歧义检测？"}
    AMB -->|有歧义| AMB_FLOW["06 歧义消解流程"]
    AMB_FLOW --> USER_SEL["用户选择诊断"]
    USER_SEL --> CACHE
    AMB -->|无歧义| P3

    EDIT --> P3
    REFINE --> P3

    subgraph P3["04 后处理"]
        direction TB
        C1["报告校验"] --> C2["序列化"] --> C3["更新 last_report / STM"] --> C4["emit 推送"]
    end

    P3 --> END([📋 输出报告])
    DIRECT --> END2

    NON_REPORT --> LLM2["二次 LLM 总结"]
    LLM2 --> END2([📋 输出回复])

    RAG -.->|检索无结果| ERR1["提示无结果"]
    EDIT -.->|校验失败| ERR2["提示错误，请重试"]
    REFINE -.->|校验失败| ERR2
    P3 -.->|Schema 不合法| ERR2

    style P1 fill:#e8f4fd,stroke:#4a90d9
    style P2 fill:#fef3e2,stroke:#e6a23c
    style P3 fill:#e8f5e9,stroke:#4caf50
    style FAST fill:#f3e5f5,stroke:#9c27b0
    style CACHE fill:#fff3e0,stroke:#ff9800
    style NON_REPORT fill:#fce4ec,stroke:#e91e63
    style LLM2 fill:#fce4ec,stroke:#e91e63
    style RECOVER fill:#fff8e1,stroke:#f9a825
    style ERR3 fill:#ffebee,stroke:#d32f2f
```

## 总览说明

| 编号 | 文件                            | 类型   | 内容                                                     |
| ---- | ------------------------------- | ------ | -------------------------------------------------------- |
| 01   | `01_phase1_input.md`            | 序列图 | 输入处理：实体提取 → 意图检测 → 上下文消解 → 查询改写    |
| 02   | `02_phase2_orchestration.md`    | 序列图 | LLM 编排：工具注册 → 决策/格式恢复 → 工具执行/终态分流   |
| 03   | `03_phase2.1_rag_search.md`     | 序列图 | rag_search 内部：查询规划 → 混合检索 → Rerank → 生成报告 |
| 04   | `04_phase3_postprocess.md`      | 序列图 | 后处理：报告校验 → 序列化 → 状态更新 → 推送              |
| 05   | `05_phase2.2_edit_report.md`    | 序列图 | 编辑报告：LLM 编辑 + 三级校验 + 事实保护 + 修复          |
| 06   | `06_ambiguity_resolution.md`    | 序列图 | 歧义消解：两轮交互时序                                   |
| 07   | `07_phase2.3_refine_report.md`  | 序列图 | 精炼报告：LLM 重写 + 风格调整 + 事实保护                 |
| 08   | `08_data_pipeline.md`           | 序列图 | 离线数据管线：Excel → Markdown → 向量库 → 元数据树       |
| 09   | `09_eval_pipeline.md`           | 序列图 | 评测管线：用例管理 → 流式评测 → Hit@K 指标               |
| 10   | `10_memory_architecture.md`     | 序列图 | 记忆系统：EntityTracker / STM / SessionStore / Retriever |
| 11   | `11_deployment_architecture.md` | 序列图 | 部署架构：Gateway (8000) → Worker (8001) 两层拓扑        |

## 关键路径说明

### 🎯 缓存命中 & 快速通道

用户输入首先经过**缓存命中检查**：如果输入文本匹配上一轮歧义消解中缓存的诊断名称（精确匹配或包含关系），则直接命中，**跳过整个 Phase 1（实体提取/意图检测/上下文消解/查询改写）**，保留上一轮的 modality/body_part 槽位，直接进入 Phase 2 LLM 编排。

触发场景：

- 歧义消解后用户点击/输入了某个诊断名称（如"肝囊肿"）
- 用户输入与缓存诊断完全匹配或包含关系

### 🔧 非报告工具 → 二次 LLM 调用

当 LLM 选择调用**非报告工具**（非 rag_search/edit_report/refine_report）时，工具执行后不会直接返回，而是再调用一次 LLM 对工具结果进行总结，生成自然语言回复。报告工具（rag_search/edit_report/refine_report）则标记 `is_final=true`，直接进入后处理，**跳过二次 LLM 调用**以避免引入幻觉。

### ⚠️ 错误降级路径（虚线）

- **检索无结果**：rag_search 返回"未检索到相关报告"，不进入歧义检测
- **报告校验失败**：工具内部失败先按错误码分类；仅白名单错误可共享预算重跑一次，terminal 错误直接结束
- **模型格式异常**：模型直接返回合法报告 JSON 时要求改走 Report Tool；文本化工具调用优先解析，解析失败才重试

### 🔄 Recovery 错误自恢复（新增）

Pipeline 内置**错误自恢复机制**，在 LLM 偶发行为异常时自动恢复：

| 场景                                           | 代码行为                                                        | 失败/耗尽后             |
| ---------------------------------------------- | --------------------------------------------------------------- | ----------------------- |
| Main LLM 绕过工具直接输出合法报告             | 追加提示，要求选择 Report Tool，重试 1 次                        | “本次报告处理未能完成”  |
| Main LLM 输出文本化工具调用                    | 先解析 JSON/函数格式；解析失败时提示使用 function calling 并重试 | “服务暂时不可用”        |
| Main LLM 同时选择多个报告工具                  | 全部执行后按 rag_search > edit_report > refine_report 选择终态   | 不额外消耗恢复预算      |
| Optional Final LLM 输出文本化工具调用          | 追加普通文本约束并重试 1 次                                     | “操作完成，请查看结果”  |

**工具错误分类**：

- **用户可处理**（`NO_LAST_REPORT` / `LLM_SERVICE_ERROR`）→ 直接返回用户，写入 STM，不重试
- **可恢复**（`REPORT_PARSE_FAILED` / `EDIT_JUDGE_FAILED`）→ 白名单内，retry 1 次
- **terminal**（`UNKNOWN_TOOL_ERROR` / `EDIT_REPAIR_FAILED` / `REPORT_SCHEMA_INVALID`）→ 不重试，禁止再次进入 Main LLM

**关键约束**：内部错误不写入 STM，避免污染后续多轮记忆。恢复预算在每次顶层 Pipeline 调用创建，递归恢复沿用同一个预算。多报告工具仲裁发生在工具全部执行后，只选择终态结果，不撤销其他工具执行。
