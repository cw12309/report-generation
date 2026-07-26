# RAG Retrieval

该目录负责把自然语言查询转换为可验证的检索计划，并在 Milvus 中执行
标量过滤、Dense/Sparse 双路召回、RRF 粗排和动态降级。`chat/pipeline.py`
负责调用该流程，并对粗排候选执行外部 Rerank 精排。

## 模块职责

```text
rag/
├── query_rewrite.py       # 术语标准化、模糊判断和兼容查询改写
├── query_planner.py       # 生成带置信度和来源的结构化检索计划
├── metadata_navigator.py  # 读取层级元数据、披露候选值并校验 LLM 输出
├── sparse_vector.py       # 建库与查询共用的词面 Sparse 向量
├── retrieval.py           # 标量过滤、Dense/Sparse、RRF 和动态降级
├── rerank.py              # 外部 Rerank API
└── README.md
```

## 完整流程

```text
用户问题 + Entity Tracker 历史槽位
                  |
                  v
        Query Planner
  modality + level_1~4 JSON
                  |
                  v
       Metadata Navigator
   逐层候选披露 + 合法值校验
                  |
                  v
      最严格的标量过滤范围
                  |
          +-------+-------+
          |               |
          v               v
    Dense HNSW      Sparse Inverted
      COSINE               IP
          |               |
          +-------+-------+
                  |
                  v
          Milvus RRFRanker
             粗排 Top-K
                  |
       候选不足则逐级放宽
                  |
                  v
        外部 Rerank 精排
             最终 Top-K
```

## 层级元数据

运行：

```powershell
python app/data_pipeline/extract_metadata.py
```

生成的 `metadata.json` 只记录目录层级，不记录模块名称或 Markdown 文件：

```json
{
  "检查类型": {
    "CT": {
      "一级目录": {
        "头颅": {
          "三级目录": {
            "颅脑平扫": {}
          }
        }
      }
    }
  }
}
```

空层级会跳过。例如原数据的 `level_2` 为空时，`三级目录` 可以直接位于
对应的 `一级目录` 节点下。

`MetadataNavigator` 会将目录树转换为合法路径集合，并支持：

- 获取全部 `modality`。
- 在已确定上级条件下获取某一级候选值。
- 从当前问题中匹配最长的合法目录值。
- 校验 LLM 输出是否存在于对应路径。

## Query Planner

`build_query_plan()` 按以下优先级确定字段：

1. 当前问题中明确出现并存在于元数据的值。
2. Entity Tracker 历史槽位中的检查类型和部位。
3. LLM 从 Metadata Navigator 披露的候选值中选择的值。

LLM 不能自由生成目录值。任何不在当前层级候选集合中的输出都会被丢弃。

输出示例：

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
    "modality": {
      "value": "CT",
      "confidence": 1.0,
      "source": "current_query"
    },
    "level_1": {
      "value": "头颅",
      "confidence": 1.0,
      "source": "current_query"
    },
    "level_2": {
      "value": "",
      "confidence": 0.0,
      "source": ""
    },
    "level_3": {
      "value": "颅脑平扫",
      "confidence": 1.0,
      "source": "current_query"
    },
    "level_4": {
      "value": "",
      "confidence": 0.0,
      "source": ""
    }
  }
}
```

`semantic_query` 保留完整自然语言，用于 Dense 向量。`keyword_query` 会移除
已识别的 modality 和目录层级，用于 Sparse 词面向量。

## 标量过滤

只有经过 Metadata Navigator 校验的字段才会进入过滤表达式：

```text
modality == "CT"
and level_1 == "头颅"
and level_3 == "颅脑平扫"
```

同一个过滤表达式同时附加到 Dense 和 Sparse 的 `AnnSearchRequest`。

## Dense/Sparse 双路召回

Dense 请求：

```python
AnnSearchRequest(
    data=[query_vector],
    anns_field="dense_vector",
    param={"metric_type": "COSINE", "params": {"ef": 64}},
    expr=filter_expr,
    limit=top_k,
)
```

Sparse 请求：

```python
AnnSearchRequest(
    data=[text_to_sparse_vector(keyword_query)],
    anns_field="sparse_vector",
    param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
    expr=filter_expr,
    limit=top_k,
)
```

Milvus 使用 `RRFRanker()` 融合两路排名并返回粗排候选。

## 动态降级

如果严格范围内的累计候选少于目标数量，按照目录深度逐级放宽：

```text
modality + level_1 + level_2 + level_3 + level_4
modality + level_1 + level_2 + level_3
modality + level_1 + level_2
modality + level_1
modality
```

`modality` 不会在目录降级过程中移除。每一阶段都会记录：

```json
{
  "stage": 1,
  "filters": {
    "modality": "CT",
    "level_1": "头颅"
  },
  "filter_expr": "modality == \"CT\" and level_1 == \"头颅\"",
  "result_count": 20
}
```

严格阶段已经召回的实体会优先保留，后续宽松阶段按照 Milvus `id` 去重。

## Rerank 精排

RRF 粗排候选返回 `chat/pipeline.py` 后，使用配置的 Rerank 服务对
`combined_text` 进行精排。Rerank 不访问 Milvus，只对已经召回的候选重新
评分并返回最终 Top-K。

如果 Rerank 服务异常，流程会退化为使用 RRF 粗排的前若干条结果。

## 前端事件

检索过程中会发送：

- `query_plan`：完整结构化检索计划。
- `recall`：候选数量以及所有动态降级阶段。
- `rerank`：精排分数、来源和诊断名称。

这些事件可用于检查某个查询实际使用了哪些标量条件，以及是否发生降级。

## Collection 加载

每次召回前都会调用 `ensure_collection_loaded()`：

1. 读取 Collection Load State。
2. 未加载时主动调用 `load_collection()`。
3. 再次校验状态。
4. 仍未加载则抛出明确错误。

因此 Milvus 重启或 Collection 被 Attu Release 后，下一次检索会尝试自动
恢复加载状态。

