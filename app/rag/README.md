# RAG Retrieval

`app/rag/` 负责查询标准化、Query Rewrite、目录规划、Milvus 多通道 Dense/Sparse 召回和 RRF 融合。`app/chat/pipeline.py` 在召回后调用外部 Rerank，并把最终参考切片交给报告生成工具。

## 模块

```text
app/rag/
├── query_rewrite.py       # 术语标准化、模糊判断和 LLM Query Rewrite
├── query_planner.py       # 生成经过目录候选校验的结构化检索计划
├── metadata_navigator.py  # 合并所选 KB 的 metadata_tree 并提供合法层级值
├── sparse_vector.py       # 建库/查询共用的 Hash Sparse 向量
├── retrieval.py           # kb_id 标量过滤、三通道 Dense/Sparse、RRF
├── rerank.py              # 外部 Rerank API
└── README.md
```

## 完整链路

```text
原始 Query
  ├─ EntityTracker：当前会话实体提取与上下文补全
  ├─ standardize_query()：术语标准化
  └─ needs_rewrite() 为真时调用 rewrite_query()
             │
             ▼
MetadataNavigator
  └─ 只合并 selected_kbs 中 active KB 的 SQLite metadata_tree
             │
             ▼
Query Planner
  ├─ semantic_query：Dense
  ├─ keyword_query：Sparse
  └─ modality + level_1～4：标量过滤
             │
             ▼
三通道 × Dense/Sparse（最多 6 个 AnnSearchRequest）
  ├─ strict：kb_id + modality + level_1～4
  ├─ modality：kb_id + modality
  └─ kb_only：仅 kb_id
             │
             ▼
Milvus RRFRanker → 去重并截断 Top-50
             │
             ▼
外部 Rerank → Top-10 → LLM 报告生成
```

## Query Rewrite

Pipeline 会先用规则更新 EntityTracker 和标准化术语。只有 `needs_rewrite()` 判定需要改写时才调用 LLM；失败时记录 warning 并回退到未改写 Query。

OpenAI 兼容地址会被规范化为：

```text
<base_url>/chat/completions
```

如果配置已经以 `/chat/completions` 结尾，不会重复拼接。改写后的 Query 不替代 `selected_kbs` 或已验证的标量约束。

## Metadata Navigator

正式服务不依赖全局 `metadata.json`。每个知识库的目录树保存在：

```text
SQLite knowledge_bases.metadata_tree
```

`MetadataNavigator.from_knowledge_bases()` 只接收聊天请求中已选择且状态为 `active` 的知识库，并在内存中合并目录树。

Navigator 会：

- 枚举合法 `modality`；
- 根据上级目录逐层披露候选值；
- 从 Query 中匹配最长合法目录值；
- 丢弃 LLM 生成但不存在于候选树中的层级值。

## Query Planner

字段来源优先级：

1. 当前 Query 中明确出现并存在于目录树的值；
2. EntityTracker 当前会话槽位；
3. LLM 从 Navigator 披露的候选值中选择的值。

计划示例：

```json
{
  "query": "CT头颅颅脑平扫脑出血",
  "semantic_query": "CT头颅颅脑平扫脑出血",
  "keyword_query": "脑出血",
  "diagnosis_query": "脑出血",
  "filters": {
    "modality": "CT",
    "level_1": "头颅",
    "level_3": "颅脑平扫"
  },
  "slots": {
    "modality": {"value": "CT", "confidence": 1.0, "source": "current_query"},
    "level_1": {"value": "头颅", "confidence": 1.0, "source": "current_query"}
  }
}
```

`semantic_query` 保留完整语义；`keyword_query` 尽量保留诊断词和低频医学词项供 Sparse 分支使用。

## 安全标量过滤

过滤字段只允许：

```text
modality, level_1, level_2, level_3, level_4
```

`kb_id` 来自后端验证后的 `selected_kbs`。字符串在拼接表达式前会移除反斜杠和双引号。

示例：

```text
kb_id in ["kb_id_1", "kb_id_2"]
and modality == "CT"
and level_1 == "头颅"
```

同一通道的 Dense 和 Sparse `AnnSearchRequest` 使用完全相同的表达式。

## Dense / Sparse

Dense：

```python
AnnSearchRequest(
    data=[query_vector],
    anns_field="dense_vector",
    param={"metric_type": "COSINE", "params": {"ef": 64}},
    expr=filter_expr,
    limit=top_k,
)
```

Sparse：

```python
AnnSearchRequest(
    data=[text_to_sparse_vector(keyword_query)],
    anns_field="sparse_vector",
    param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
    expr=filter_expr,
    limit=top_k,
)
```

当前 Sparse 是中文单字、二元组及英文数字词项的 Hash Sparse，不是标准 BM25，也没有 IDF 和文档长度归一化。

## 三通道融合

| 通道 | 过滤范围 | 作用 |
| --- | --- | --- |
| `strict` | `kb_id + modality + level_1～4` | 高精度层级命中 |
| `modality` | `kb_id + modality` | 防止错误层级过滤导致漏召回 |
| `kb_only` | 仅 `kb_id` | 保留所选知识库范围内的语义兜底 |

过滤条件完全相同的通道会去重。所有有效请求在一次 `hybrid_search()` 中由 `RRFRanker()` 融合，不再使用“累计 5 条就停止放宽”的旧漏斗逻辑。

Milvus 通常按 PK 返回唯一结果，代码优先使用跨 SQLite/Markdown/Milvus 一致的 `chunk_id` 去重，并在达到 `top_k` 时截断；旧数据没有该字段时才回退到 Milvus `id` 或文本。

## 候选池与 Rerank

当前 `config.yml` 默认：

```yaml
retrieval:
  rag_top_k: 50
  rerank_top_k: 10
```

- Milvus RRF 最多返回 50 条候选；
- 外部 Rerank 对候选 `combined_text` 重新评分；
- 最终 10 条进入 LLM 上下文；
- Rerank 异常时回退为 RRF 前 10 条。

## SSE 与来源

RAG 阶段会发送：

- `query_plan`：结构化检索计划；
- `recall`：候选数和三通道过滤详情；
- `rerank`：分数、诊断、来源和切片文本。

前端把 `rerank` 结果附加到正式回答的“参考切片”折叠面板。`source` 优先使用 Milvus 返回的 32 位 `chunk_id`，因此可与 SQLite 切片直接对应，并在 Collection 重建后保持稳定。

只验证 Hybrid Search 和 SQLite/Milvus `chunk_id` 对应关系、不调用 Rerank 时可运行：

```powershell
python app/test/verify_chunk_id_retrieval.py
```

## Collection Load

每次检索前 `ensure_collection_loaded()` 会：

1. 查询 Collection Load State；
2. 未加载时主动 Load；
3. 再次验证；
4. 仍失败时抛出明确异常。

Milvus 重启或 Collection 被 Release 后，下一次检索会尝试恢复加载。

## 测试

```powershell
python -m unittest app.test.test_rag_planning
```

正式检索评测位于 `app/eval/` 模块：`app/eval/run_retrieval_eval.py` 执行真实 Hybrid Search + Rerank，数据集存放在 `app/eval/datasets/retrieval_test_cases.json`，当前统计 Hit@1/3/5。Web 评测中心支持逐 Query SSE 进度、协作式取消和测试集维护；黄金标签使用稳定的 32 位 `chunk_id`。

人工维护黄金标签时，前端通过 SQLite 级联筛选接口按知识库、`modality` 和 `level_1～level_4` 缩小范围，再以异步文本搜索选择 Chunk。该管理链路不改变正式 RAG 的 Milvus 三通道召回逻辑，只用于更准确地定位预期 `chunk_id`。
