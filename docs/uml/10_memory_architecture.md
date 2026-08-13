**Memory Architecture — 记忆系统架构（实体追踪 / 短期记忆 / 持久化 / 检索注入）**

```mermaid
sequenceDiagram
    autonumber

    participant Pipe as 🔄 pipeline.py
    participant ET as 🏷️ EntityTracker
    participant STM as 📝 ShortTermMemory
    participant MR as 🔎 MemoryRetriever
    participant SS as 💾 SessionStore
    participant LLM as 🧠 LLM

    rect rgb(240, 248, 255)
        Note over Pipe,LLM: Phase 1 — 实体追踪 & 上下文管理
    end

    Pipe->>ET: update_from_query(query)
    ET->>ET: _extract_modality_rule (规则提取)
    ET->>ET: _extract_body_part_rule (规则提取)
    ET->>LLM: 发送 LLM 提取请求 (entity_extraction.md)
    LLM-->>ET: 结构化实体 JSON
    ET->>ET: 合并规则 + LLM 结果 → 更新 slots
    ET-->>Pipe: slots {modality, body_part, clinical_history, diagnosis}

    Pipe->>ET: detect_intent(query)
    ET->>ET: 分析意图: new_session / append / switch

    alt 意图 == switch
        ET->>ET: apply_switch → 清空旧 slots
        ET->>STM: clear(session_id)
    end

    ET-->>Pipe: intent

    Pipe->>ET: resolve_context(query)
    ET->>ET: 补全省略信息 (代词/简称 → 完整实体)
    ET-->>Pipe: 消解后的 query

    Pipe->>ET: to_context_prompt()
    ET-->>Pipe: 上下文 Prompt 片段

    rect rgb(255, 248, 240)
        Note over Pipe,LLM: Phase 2 — 记忆检索注入
    end

    Pipe->>MR: search(query, top_k_stm)
    MR->>MR: 向量化 query → 计算与历史对话的相似度
    MR-->>Pipe: 最相关的历史对话片段

    Pipe->>STM: get_history(session_id)
    STM-->>Pipe: 对话历史列表

    rect rgb(255, 255, 240)
        Note over Pipe,LLM: Phase 3 — 会话状态更新
    end

    Pipe->>STM: add_turn(session_id, user_msg, assistant_msg)
    STM->>STM: 追加一轮对话

    alt 对话轮次超过 max_rounds
        STM->>STM: 触发智能摘要淘汰
        STM->>LLM: 发送淘汰轮次 → 生成摘要
        LLM-->>STM: 摘要文本
        STM->>STM: 移除旧轮次，保留摘要
    end

    STM-->>Pipe: 完成

    Pipe->>SS: save_turn(session_id, turn_index, user_input, assistant_output)
    Pipe->>SS: save_state(session_id, entity_slots, last_report, selected_kbs)
    SS-->>Pipe: 持久化完成

    rect rgb(240, 255, 240)
        Note over Pipe,LLM: 会话恢复
    end

    Pipe->>SS: load_session(session_id)
    SS-->>Pipe: 完整会话数据 (turns, state, thinking)
    Pipe->>ET: 恢复 slots
    Pipe->>STM: 恢复对话历史
```

---

**四组件职责**

| 组件 | 一句话定位 | 关键方法 |
|------|-----------|----------|
| **EntityTracker** | 管理医学实体槽位，驱动输入处理 | `update_from_query`, `detect_intent`, `resolve_context`, `to_context_prompt` |
| **ShortTermMemory** | 内存对话历史，智能摘要淘汰 | `add_turn`, `get_history`, `get_summaries`, `clear` |
| **SessionStore** | SQLite 持久化，会话/KB/Deployment 管理 | `create_session`, `save_turn`, `save_state`, `load_session` |
| **MemoryRetriever** | 语义相似度检索历史对话片段 | `index_stm`, `search` |

---

**数据流转图**

```mermaid
flowchart LR
    User[👤 用户输入] --> ET[EntityTracker<br/>实体提取/意图/消解]
    ET -->|slots + context| Prompt[Prompt 构建]
    MR[MemoryRetriever<br/>相似历史片段] --> Prompt
    STM[ShortTermMemory<br/>对话历史] --> Prompt
    Prompt --> LLM[LLM 决策]
    LLM -->|输出| STM
    STM -->|淘汰| Summary[摘要存储]
    STM --> SS[SessionStore<br/>SQLite 持久化]
    ET --> SS
```

---

**智能摘要淘汰机制**

```
对话轮次 > max_rounds
    ↓
选取最早的 N 轮对话
    ↓
LLM 生成摘要 (summarize.md)
    ↓
移除旧轮次，保留摘要
    ↓
后续对话注入摘要 + 最近轮次
```

---

**与 AGENTS.md 的一致性**

- `memory/AGENTS.md` 的 Entry Points 顺序与本 UML 一致：`session_store → entity_tracker → short_term → retriever`
- switch 意图的规则：只有带明确切换表达 + 检查类型/部位/目标的输入才清状态；"改成数值、修改报告、润色"等报告操作不触发 switch