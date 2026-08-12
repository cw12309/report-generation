**总览：报告生成管线全流程**

```mermaid
flowchart TD
    START([👤 用户输入]) --> P1

    subgraph P1["01 输入处理"]
        direction TB
        A1["实体提取"] --> A2["意图检测"] --> A3["上下文消解"] --> A4["查询改写"]
    end

    P1 --> INTERCEPT{"拦截检查"}
    INTERCEPT -->|模糊输入| ASK1["追问用户"]
    INTERCEPT -->|缺少模态| ASK2["追问检查类型"]
    INTERCEPT -->|通过| P2

    subgraph P2["02 LLM 编排"]
        direction TB
        B1["注册工具 / 记忆检索 / 构建 Prompt"] --> B2["LLM 决策"]
        B2 --> B3{"调用哪个工具？"}
        B3 -->|rag_search| RAG["03 rag_search 检索 + 生成报告"]
        B3 -->|edit_report| EDIT["05 编辑已有报告"]
        B3 -->|refine_report| REFINE["精炼优化报告"]
    end

    RAG --> AMB{"歧义检测？"}
    AMB -->|有歧义| AMB_FLOW["06 歧义消解流程"]
    AMB_FLOW --> USER_SEL["用户选择诊断"]
    USER_SEL --> RAG
    AMB -->|无歧义| P3

    EDIT --> P3
    REFINE --> P3

    subgraph P3["04 后处理"]
        direction TB
        C1["报告校验"] --> C2["序列化"] --> C3["更新 last_report / STM"] --> C4["emit 推送"]
    end

    P3 --> END([📋 输出报告])

    style P1 fill:#e8f4fd,stroke:#4a90d9
    style P2 fill:#fef3e2,stroke:#e6a23c
    style P3 fill:#e8f5e9,stroke:#4caf50
```

## 总览说明

| 编号 | 文件                         | 类型   | 内容                                                     |
| ---- | ---------------------------- | ------ | -------------------------------------------------------- |
| 01   | `01_phase1_input.md`         | 序列图 | 输入处理：实体提取 → 意图检测 → 上下文消解 → 查询改写    |
| 02   | `02_llm_orchestration.md`    | 序列图 | LLM 编排：工具注册 → 记忆检索 → Prompt 构建 → LLM 决策   |
| 03   | `03_rag_search_internal.md`  | 序列图 | rag_search 内部：查询规划 → 混合检索 → Rerank → 生成报告 |
| 04   | `04_phase3_postprocess.md`   | 序列图 | 后处理：报告校验 → 序列化 → 状态更新 → 推送              |
| 05   | `05_edit_report.md`          | 序列图 | 编辑报告：LLM 编辑 + 医学事实保护校验                    |
| 06   | `06_ambiguity_resolution.md` | 序列图 | 歧义消解：两轮交互时序                                   |
