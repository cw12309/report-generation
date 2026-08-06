# 前端

前端由原生 HTML、CSS 和 JavaScript 实现，由 FastAPI 作为静态资源托管，无需 Vue、React 或前端构建工具。

## 文件

| 文件 | 说明 |
| --- | --- |
| `index.html` | 页面骨架、Agent/知识库/配置/监控视图和弹窗 |
| `app.js` | 路由切换、SSE、会话历史、知识库管理和切片详情 |
| `style.css` | macOS 风格浅色层级、页面布局、状态、气泡和引用样式 |

## 页面结构

```text
全局浅色一级导航
├─ Agent
│   ├─ 左侧：当前浏览器的历史对话
│   └─ 主区：工具栏、知识库选择弹窗、聊天记录和输入框
├─ 知识库
│   ├─ 左侧：上传、KB 状态、构建/重建/删除和执行日志
│   └─ 右侧：所选知识库的 SQLite 切片详情
├─ 评测
│   └─ Retrieval Eval 运行按钮、Hit@1/3/5 指标卡和 Query 明细表
├─ 配置
│   ├─ 左侧：配置分类
│   └─ 右侧：模型与检索参数
└─ 监控
    └─ 服务端 SQLite 会话列表和详情
```

全局导航背景为 `#f0f2f5`；各页面二级侧栏为 `#f9fafb`；主内容区为白色并带轻微左侧阴影。

## 知识库管理

### 上传

上传使用普通 `fetch` 和 `multipart/form-data`：

```text
POST /api/kb/upload
files=<file1.xlsx>
files=<file2.xlsx>
```

一次可以选择多个 Excel，但每个文件独立生成一个 `kb_id`。上传完成后状态为 `uploaded`，不会自动切片或写入 Milvus。

### 状态与操作

| 状态 | 行内展示 | 操作 |
| --- | --- | --- |
| `uploaded` | 待构建 | 构建、删除 |
| `processing` | Spinner、进度百分比 | 禁用构建和删除 |
| `active` | 已激活 | 重新构建、删除、查看切片 |
| `failed` | 构建失败 | 重新构建、删除 |

构建调用：

```text
POST /api/kb/{kb_id}/build
```

列表中存在 `processing` 知识库时，每 4 秒调用 `GET /api/kbs`；全部结束后停止轮询。进度采用行内展示，不使用阻塞式构建弹窗。

### 切片详情

点击管理列表中的知识库后调用：

```text
GET /api/kbs/{kb_id}/chunks?page=1&size=50
```

右侧卡片展示检查类型、目录层级、模块、影像学表现和意见。旧知识库若 SQLite 中没有切片，需要重新构建后才能显示详情。

## Agent 知识库选择

知识库管理页不提供聊天复选框。Agent 工具栏中的“选择知识库”按钮打开独立弹窗，只渲染 `status === "active"` 的知识库。

勾选 ID 保存在：

```text
localStorage["selectedKnowledgeBaseIds"]
```

页面刷新后会恢复仍然有效的 ID；知识库被删除或不再 active 时自动清理。发送聊天请求时附加：

```json
{
  "query": "CT 头颅脑出血",
  "session_id": "web_...",
  "selected_kbs": ["kb_id_1", "kb_id_2"]
}
```

没有选择知识库时，前端会拦截并提示“请至少选择一个知识库”，后端也会再次校验。

## SSE 流式对话

前端使用 `fetch()` + `ReadableStream.getReader()` 解析 `/api/chat` 返回的 `text/event-stream`。

| 事件 | 用途 |
| --- | --- |
| `status` | 当前处理阶段 |
| `intent` / `intent_switch` | Tool Calling 意图和意图切换 |
| `query_rewrite` / `context_resolve` | 查询改写和当前会话上下文补全 |
| `memory_retrieval` | 仅展示当前会话 STM 相关片段 |
| `search` / `recall` | 检索查询和多通道召回详情 |
| `rerank` | 收集分数、诊断、来源和切片原文 |
| `token` | 实时追加到正式 AI 回答气泡 |
| `reasoning` | 思考面板中的模型推理 |
| `ambiguous` | 多诊断候选按钮 |
| `report` | 最终结构化报告，覆盖当前流式气泡内容 |
| `message` | 最终普通回复，覆盖当前流式气泡内容 |
| `error` | 显示错误并结束流式光标 |
| `done` / `[DONE]` | 完成当前流并保存会话 |

第一个 `token` 会创建正式助手气泡，后续 Token 在同一个容器中重新进行安全 Markdown 渲染。最终 `report` 或 `message` 以服务端完整内容为准更新同一气泡，不再新增重复回答。

`rerank` 事件会临时收集引用。回答完成后，气泡底部附加原生 `<details>` “参考切片”面板，每个来源可以继续展开查看具体原文。当前 `source` 是跨 SQLite、Markdown、Milvus 一致的 32 位 MD5 `chunk_id`，不再使用会随 Collection 重建变化的 Milvus 自增主键。

## 对话历史与 LocalStorage

| Key | 内容 |
| --- | --- |
| `chat_history_list` | 对话 ID、标题、session_id、时间 |
| `chat_messages_{conversationId}` | 气泡 HTML 与思考过程 HTML |
| `selectedKnowledgeBaseIds` | Agent 当前勾选的知识库 ID 数组 |

引用折叠面板位于 `.bubble` 内，因此会随消息 HTML 一并保存和恢复。浏览器历史用于快速恢复 UI；服务端仍使用 SQLite 保存会话轮次、实体槽位和思考事件。

## 歧义交互

当后端发送 `ambiguous` 时，前端显示诊断候选。用户点击后会携带 `selected_diagnosis` 再次请求 `/api/chat`，后端可以复用当前会话内的检索缓存并生成对应报告。

## 运行

启动后端即可访问：

```powershell
python python_start.py
```

- 页面：<http://localhost:8000>
- 前端资源挂载：`/static/*`

修改 `app.js` 或 `style.css` 后，应同步提升 `index.html` 中的 `?v=` 版本，避免浏览器缓存旧资源。

## 评测中心

左侧一级导航的“评测”入口对应 `#pageEval`。点击“开始评测”后调用：

```text
POST /api/eval/run
```

运行期间按钮被禁用，状态区通过 `fetch + ReadableStream` 读取 SSE，逐条显示当前 Query 和精确百分比。收到 `done` 后进度条平滑拉满并隐藏，再渲染 Hit@1、Hit@3、Hit@5 百分比、命中数及 Query 明细表。失败时保留页面并在状态栏显示后端错误，不影响 Agent 对话和知识库轮询。

评测进行时显示“🛑 取消评测”。点击后前端中断 SSE，并调用 `POST /api/eval/cancel` 请求后端协作式停止；进度条切换为灰色中止状态。当前 Query 会安全收尾，但不会启动下一条，状态清理后可重新开始评测。

点击“➕ 新增用例”会打开人工录入弹窗。切片字段上方提供“知识库 → 检查类型 → Level 1～4”六个级联筛选器，通过 `GET /api/kb/filter-options` 刷新下级可用值；文本框继续使用 300ms 防抖异步搜索，并将六个精确条件一并传给 `GET /api/kb/chunks`。旧请求会被取消，浮层最多渲染 20 条 active 切片。用户选中后，真实 Chunk ID 写入隐藏字段并提交至 `POST /api/eval/add_case`。

级联更新不会依赖 DOM `change` 的递归触发：代码直接替换 `<option>`，保留仍然有效的已选值，无效值回退为“全部”；如果父级变化清除了旧子级，会最多追加一次稳定化请求以刷新更深层选项。筛选选项和切片搜索分别使用独立的 `AbortController` 与请求序号，较慢的旧响应不会覆盖新选择。

进入评测页面或刷新页面时，前端调用 `GET /api/eval/status`。`running` 状态恢复进度条、当前 Query、取消按钮并每 1.5 秒轮询；`completed` 状态直接调用原有结果渲染函数恢复指标和明细，不重新执行检索。当前静态资源版本为 `style.css?v=33`、`app.js?v=48`。

点击“🤖 自动生成用例”可设置生成数量与最小切片长度。页面通过 `POST /api/eval/auto_generate` 读取 SSE 流，实时更新进度条和逐条 Query 日志；可主动停止任务，已显示成功的用例已完成落盘。结束后页面会更新测试集总数，并提示重新运行评测。

进入评测页时，前端通过 `GET /api/eval/dataset` 直接展示测试集，不会触发检索。每行的“❌ 删除”调用单删接口；“🗑️ 清空测试集”经二次确认后将 JSON 重置为空数组。删除完成后自动刷新列表，并将旧指标标记为等待重新评测。

测试集和评测结果表格的第一列是纯展示序号，由当前数组 `index + 1` 动态生成；真实删除主键始终是 32 位 `chunk_id`。单删并重新拉取后，序号会自动连续重排。
