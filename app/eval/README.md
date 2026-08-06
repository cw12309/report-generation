# Eval 模块

`app/eval/` 是应用内独立的自动化评测子模块，集中维护评测代码与数据集。当前已实现 Retrieval Eval，后续可继续增加 Generation Eval、医学事实一致性指标和评测报告输出。

## 目录

```text
app/eval/
├── __init__.py
├── metrics.py
├── run_retrieval_eval.py
├── datasets/
│   └── retrieval_test_cases.json
└── README.md
```

## 数据集格式

`datasets/retrieval_test_cases.json` 是 JSON 数组。每条用例包含：

```json
{
  "query": "CT",
  "expected_chunk_ids": ["aec89df22574e99397b6af2be36533ba"],
  "metadata": {"category": "test"}
}
```

- `query`：发给 Embedding、Hybrid Search 和 Rerank 的查询；
- `expected_chunk_ids`：一个或多个相关切片的 32 位 MD5；
- `metadata`：评测分组、数据来源、难度等扩展属性，目前只展示、不参与检索。

测试数据与 Python 代码分离；新增用例时只修改或增加 `datasets/` 下的数据文件。

## 运行

在项目根目录执行：

```powershell
python app/eval/run_retrieval_eval.py
```

也支持模块方式：

```powershell
python -m app.eval.run_retrieval_eval
```

仅校验 JSON 结构和 MD5 格式，不连接外部服务：

```powershell
python app/eval/run_retrieval_eval.py --validate-only
```

从已激活知识库的 SQLite 切片中随机抽样，并调用当前配置的 LLM 自动生成 50 条测试用例：

```powershell
python app/eval/generate_test_cases.py
```

可调整生成数量和最小切片长度：

```powershell
python app/eval/generate_test_cases.py --count 20 --min-length 150
```

脚本排除测试集中已经使用的 Chunk ID，逐条显示生成进度，并以原子替换方式追加 `type=auto_generated` 的测试用例。若部分 LLM 请求失败或 Query 重复，脚本会保留成功结果并提示再次运行补足。

指定其他数据集或知识库：

```powershell
python app/eval/run_retrieval_eval.py `
  --dataset app/eval/datasets/retrieval_test_cases.json `
  --kb-id 69590101e80e4517943669bae956a5e6 `
  --candidate-top-k 50 `
  --rerank-top-k 10
```

不传 `--kb-id` 时使用 SQLite 中全部 `active` 知识库。真实评测需要配置可访问的 Embedding、Milvus 和 Rerank 服务；任何基础设施错误会直接终止，不会被静默统计为检索未命中。

## 当前指标

`metrics.py` 提供：

- `first_relevant_rank()`：计算第一个相关切片的 1-based 名次；
- `hit_at_k()`：计算单条用例是否命中 Top-K；
- `aggregate_hit_rates()`：汇总 Hit@1、Hit@3、Hit@5。

执行器会逐条展示期望 ID、Hybrid 候选数、命中排名、命中 Rerank 分数和 Rerank Top 5，然后输出总体 Hit@1/3/5。

当前基础用例 `query="CT"` 已确认能在 Rerank Top 3 内命中。由于使用真实外部 Rerank 服务，具体分数和第 1～3 名之间的排序可能随模型服务状态产生小幅变化。

## FastAPI 集成

`run_retrieval_eval.py` 暴露无参数函数：

```python
from eval.run_retrieval_eval import run_evaluation

result = run_evaluation()
```

该函数不会打印 CLI 报告，返回可直接序列化的 `metrics`、`details` 和 `config`。Web 服务使用 `stream_run_evaluation()`，将每条同步检索任务放入工作线程并通过 SSE 返回进度：

```text
POST /api/eval/run
```

事件顺序为 `start → progress × N → done`，其中 `done` 包含完整 `metrics`、`details` 和 `config`；执行错误使用 `error` 事件返回。接口同一时间只允许一个评测任务，重复请求返回 HTTP 409。

运行期间可请求协作式取消：

```text
POST /api/eval/cancel
```

取消标志会在每条 Query 开始前检查；当前正在执行的同步 SDK 调用安全完成后，流返回 `cancelled` 事件并停止。FastAPI 使用独立后台 Task 消费评测生成器，浏览器刷新只断开 SSE，不会取消计算；新页面通过 `GET /api/eval/status` 轮询恢复当前 Query 和百分比。正常、取消或异常都会在 `finally` 中关闭 Milvus 客户端并重置运行锁。

最后一次完整结果保存在 SQLite `evaluation_state.result_json`，内容包括 `metrics`、`details`、`bad_cases` 和 `config`。没有运行中任务时，状态接口优先返回该结果；测试集改变后旧结果自动失效，避免展示与当前用例不匹配的历史成绩。

人工用例也可通过接口追加：

```text
POST /api/eval/add_case
Content-Type: application/json

{"query":"CT检查的定义是什么？","expected_chunk_ids":["aec89df22574e99397b6af2be36533ba"]}
```

`expected_chunk_ids` 也接受英文或中文逗号分隔的字符串。后端校验 32 位 MD5、去重并转为小写，通过原子文件替换写入数据集，同时添加 `metadata: {"type": "manual"}`。

Web UI 不要求手抄 MD5。“新增用例”弹窗先通过 `GET /api/kb/filter-options` 提供知识库、检查类型及 Level 1～4 级联筛选，再用 300ms 防抖搜索把文本和精确条件组合提交到 `GET /api/kb/chunks`。页面始终只渲染最多 20 个结果，避免数千条 `<option>` 导致 DOM 卡顿。用户选中切片后，前端把对应 `chunk_id` 作为 `expected_chunk_ids` 提交。

级联顺序为 `kb_name → exam_type(modality) → level1 → level2 → level3 → level4`。选项接口返回剔除 NULL 和空字符串后的去重数组；搜索接口执行 `combined_text LIKE ?`，并对所有已选择的结构化字段追加参数化精确匹配。人工用例保存的数据格式不变，仍只写入 Query、稳定 MD5 Chunk ID 和 `metadata.type=manual`。

前端自动生成使用 SSE 接口：

```text
POST /api/eval/auto_generate
Content-Type: application/json

{"count":10,"min_length":100}
```

响应事件包含 `start`、`progress`、`warning`、`done` 或 `error`。每个 `progress` 事件都表示该条用例已经原子追加到数据集；同一时间只允许运行一个自动生成任务。

测试集管理接口：

```text
GET    /api/eval/dataset
DELETE /api/eval/dataset
DELETE /api/eval/dataset/{chunk_id}
```

清空和单删均复用 `write_dataset_atomically()`。清空后文件保持为合法的空数组 `[]`，数据加载器仍允许后续人工新增或自动生成；空测试集直接运行评测时会返回明确错误。
