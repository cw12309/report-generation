# AGENTS.md — app/test/

## Purpose

测试目录。包含 pytest 测试用例、手动评测脚本和 API 连通性测试。覆盖 RAG 检索、记忆系统、Rerank 流程、知识库切片等核心功能。

不负责：正式评测（由 `app/eval/` 负责）、生产代码。

## File Map

| File                                      | Responsibility                              |
| ----------------------------------------- | ------------------------------------------- |
| `test_rag_planning.py`                    | pytest: 查询规划测试                        |
| `test_memory.py`                          | pytest: 记忆系统（STM + EntityTracker）测试 |
| `test_rerank_flow.py`                     | pytest: Rerank 流程测试                     |
| `test_knowledge_base_chunks.py`           | pytest: 知识库切片 CRUD 测试                |
| `test_api_deployments.py`                 | pytest: Deployment 安全、存储、事件和并发锁 |
| `test_api_deployment_routes.py`           | pytest: Deployment 管理/Chat 路由集成与回归 |
| `test_report_pipeline.py`                 | pytest: Canonical Report、repair、Last Report 注入、事实保护与 Final Tool |
| `verify_chunk_id_retrieval.py`            | pytest: chunk_id 三端一致性验证             |
| `eval_retrieval.py`                       | 手动脚本: 检索评测（旧版）                  |
| `eval_retrieval_v2.py`                    | 手动脚本: 检索评测（新版）                  |
| `eval_rerank_compare.py`                  | 手动脚本: Rerank 对比评测                   |
| `_test_search.py`                         | 手动脚本: 搜索测试                          |
| `retrieval_eval_dataset.jsonl`            | 检索评测数据集                              |
| `tests_api/call_qwen_model_test.py`       | API 测试: 模型调用连通性                    |
| `tests_api/test_embedding.py`             | API 测试: Embedding 连通性                  |
| `tests_api/test_siliconflow_embedding.py` | API 测试: SiliconFlow Embedding             |
| `tests_api/deepsearcher_client_test.py`   | API 测试: DeepSearcher 客户端               |
| `tests_api/system_prompt.md`              | API 测试用 System Prompt                    |

## Entry Points

理解本目录的推荐顺序：

1. 按需查看对应被测模块的测试文件
2. pytest 文件以 `test_` 开头，可通过 `pytest` 直接运行
3. 手动脚本以 `eval_` 或 `_test` 开头，需独立运行

## Testing

运行所有 pytest 测试：

```powershell
pytest app/test/
```

运行单个测试文件：

```powershell
pytest app/test/test_rag_planning.py -v

# API 上线：纯逻辑/存储 + FastAPI 路由集成
pytest app/test/test_api_deployments.py app/test/test_api_deployment_routes.py -v

# Agent 报告确定性链路（无外部模型/Milvus）
pytest app/test/test_report_pipeline.py -v
```

运行手动脚本：

```powershell
python app/test/eval_retrieval_v2.py
python app/test/eval_rerank_compare.py
```

## Local Rules

- pytest 测试文件必须以 `test_` 开头
- 手动脚本不需要 pytest 框架
- `tests_api/` 中的测试依赖外部 API 服务，可能需要网络和认证
- `test_api_deployment_routes.py` 使用隔离的临时 SQLite、Milvus/Pipeline 替身，不调用真实模型或外部服务
- `test_report_pipeline.py` 使用注入的 LLM/Registry 替身，不调用真实模型、Embedding、Rerank 或 Milvus
- 测试数据文件（如 `retrieval_eval_dataset.jsonl`）与测试代码放在一起

## Common Pitfalls

- 部分测试依赖 Milvus 和 LLM API 可用，CI 环境可能需要 mock
- 修改被测模块的 API 后，对应测试可能失败
- `tests_api/` 中的测试包含真实 API 调用，注意不要在生产环境运行
