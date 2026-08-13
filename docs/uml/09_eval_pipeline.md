**Eval Pipeline — 评测管线（用例管理 → 流式评测 → Hit@K 指标）**

```mermaid
sequenceDiagram
    autonumber

    participant Admin as 👤 管理员
    participant Server as 🖥️ Server API
    participant Gen as 🤖 generate_test_cases
    participant LLM as 🧠 LLM
    participant Eval as 📊 run_retrieval_eval
    participant RAG as 🔍 RAG 检索
    participant Metrics as 📈 metrics
    participant SS as 💾 SessionStore

    rect rgb(240, 248, 255)
        Note over Admin,SS: 阶段 1 — 测试用例管理
    end

    Admin->>Server: POST /api/eval/add_case (人工录入)
    Server->>Eval: add_retrieval_case(query, expected_chunk_ids)
    Eval->>Eval: 校验 chunk_id 格式 (32位MD5)
    Eval->>Eval: 写入 JSON 数据集
    Eval-->>Server: 用例信息
    Server-->>Admin: 添加成功

    Admin->>Server: POST /api/eval/auto_generate (LLM 自动生成)
    Server->>Gen: stream_generate_cases(count, min_length)
    Gen->>SS: 读取 SQLite 切片 (随机抽样)
    SS-->>Gen: 切片列表
    Gen->>LLM: 发送切片文本 → 生成查询词
    LLM-->>Gen: 查询词
    Gen->>Gen: 校验 chunk_id → 去重 → 写入 JSON
    Gen-->>Server: 流式返回生成进度
    Server-->>Admin: SSE 流式进度

    Admin->>Server: GET /api/eval/dataset
    Server->>Eval: load_dataset()
    Eval-->>Server: 用例列表
    Server-->>Admin: 用例列表

    Admin->>Server: DELETE /api/eval/dataset/{chunk_id}
    Server->>Eval: delete_retrieval_case(chunk_id)
    Eval-->>Admin: 删除成功

    rect rgb(255, 248, 240)
        Note over Admin,SS: 阶段 2 — 流式评测执行
    end

    Admin->>Server: POST /api/eval/run
    Server->>Eval: stream_run_evaluation()
    Eval->>SS: save_evaluation_runtime_state (保存现场)
    Eval->>Eval: 加载测试用例

    loop 遍历每条用例
        Eval->>Server: SSE: 当前用例进度
        Server-->>Admin: 进度推送

        Eval->>RAG: retrieve_with_fallback(query)
        RAG-->>Eval: 检索结果 (chunk_id 列表)

        Eval->>Eval: rerank_with_retry (重排序)
        Eval->>Metrics: first_relevant_rank(retrieved_ids, expected_ids)
        Metrics-->>Eval: (rank, matched_chunk_id)

        alt 管理员取消
            Eval->>Eval: cancel_requested = True
            Eval-->>Server: 取消状态
            Server-->>Admin: 评测已取消
        end
    end

    Eval->>Metrics: aggregate_hit_rates(ranks, DEFAULT_HIT_KS)
    Metrics-->>Eval: Hit@1 / Hit@3 / Hit@5

    Eval->>SS: save_evaluation_result (持久化结果)
    Eval-->>Server: SSE: 最终结果
    Server-->>Admin: 评测完成 ✅

    rect rgb(240, 255, 240)
        Note over Admin,SS: 阶段 3 — 现场恢复
    end

    Admin->>Server: GET /api/eval/status (刷新页面后)
    Server->>SS: get_evaluation_status()
    SS-->>Server: 前一次的评测结果
    Server-->>Admin: 恢复现场数据
```

---

**参与组件**

| 组件 | 职责 |
|------|------|
| `run_retrieval_eval.py` | 评测引擎：流式执行、用例 CRUD、取消、现场恢复 |
| `generate_test_cases.py` | LLM 自动生成测试用例（SQLite 切片抽样 → LLM 生成 Query） |
| `metrics.py` | Hit@K 指标计算：`first_relevant_rank`, `aggregate_hit_rates` |
| `datasets/retrieval_test_cases.json` | 测试用例数据集（JSON 数组） |

---

**关键设计**

| 机制 | 说明 |
|------|------|
| 流式 SSE | 评测进度通过 SSE 实时推送到浏览器 |
| 现场恢复 | 评测状态通过 SQLite 持久化，刷新页面不丢数据 |
| 协作取消 | `cancel_requested` 标志位，下一轮循环检查 |
| 单任务锁 | `evaluation_lock` 保证同时只有一个评测运行 |
| 三端对齐 | 测试用例的 chunk_id 必须与 data_pipeline 的 `generate_chunk_id` 一致 |