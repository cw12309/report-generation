# 前端开发指南

`front/` 是 Worker 内置的管理与对话界面。它不是独立的 SPA 工程：没有 Node.js 构建步骤，也没有单独部署所需的前端开发服务器。项目总览和启动方式见[根 README](../README.md)，后端接口与 Agent/RAG 运行链路见[后端 README](../app/README.md)。

## Frontend Overview

前端覆盖五个主要页面：

- Agent：选择知识库、发起对话、查看思考过程、参考切片和上下文占用；
- 知识库：上传 Excel、构建/重建、查看进度、切片和删除知识库；
- 评测中心：维护 Retrieval Eval 数据集、自动生成用例、执行/取消评测并恢复状态；
- 系统配置：配置 LLM、Embedding、Rerank、检索与系统参数并测试模型连接；
- 会话监控：查看会话、记忆状态和删除后端会话。

“API 上线”子页面管理真实 Deployment：选择固定的 Active 知识库、创建/编辑/启停/软删除 Deployment、轮换独立 API Key、查看健康状态，并生成可复制的 cURL/Python 多轮 SSE 示例。明文 Key 只在创建或轮换成功后显示一次，不写入 LocalStorage。

## Technology Stack

| 能力 | 当前实现 |
| --- | --- |
| 页面 | 单个 `index.html` |
| 样式 | 原生 CSS，集中在 `style.css` |
| 逻辑 | 原生浏览器 JavaScript，集中在经典脚本 `app.js` |
| 网络 | Fetch API、ReadableStream、TextDecoder、AbortController |
| 路由 | 无路由库；通过 `data-page` 和 DOM 显隐切换页面 |
| 状态 | 无状态管理库；模块级变量 + DOM + LocalStorage |
| 渲染 | DOM API、模板字符串和有限 Markdown 渲染函数 |
| 构建 | 无 `package.json`、打包器、转译器或 TypeScript |
| 测试 | 当前没有独立前端自动化测试或 lint 配置 |

不要按 React/Vue 项目寻找组件、store 或 router；也不要为小修改假设存在 npm 命令。

## Getting Started

前端由 FastAPI Worker 从 `front/` 直接提供：

```powershell
# 从仓库根目录启动完整本地环境
python python_start.py

# 或只启动 Worker
uvicorn app.chat.server:app --host 0.0.0.0 --port 8001
```

完整环境通常从 `http://localhost:8000` 进入；只启动 Worker 时访问 `http://localhost:8001`。以实际 `python_start.py`、Docker 端口映射和日志为准。

直接双击 `index.html` 只能看到静态结构，不能完成正常联调：`app.js` 将 API 基址固定为同源 `/api`，没有前端环境变量或代理配置。

## File Structure

```text
front/
├─ index.html   # 全部页面结构、弹窗、表单与静态资源入口
├─ app.js       # 页面交互、状态、Fetch/SSE、渲染和 LocalStorage
├─ style.css    # 全部布局、组件状态与响应式样式
└─ README.md    # 本文
```

Worker 在 `app/chat/server.py` 中把该目录挂载为 `/static`，并让 `/` 返回 `index.html`。当前页面引用：

```html
<link rel="stylesheet" href="/static/style.css?v=35" />
<script src="/static/app.js?v=50"></script>
```

版本查询参数只是浏览器缓存失效标记，不代表前端发布版本。修改 `app.js` 或 `style.css` 后应同步递增对应参数，否则浏览器可能继续使用旧资源。

## Application Architecture

```text
index.html
  ├─ data-page 按钮 ─► showPage() ─► DOM 页面显隐
  ├─ 表单/按钮事件 ─► app.js 中的功能函数
  └─ style.css 类名 ◄─ app.js 添加/移除状态类

app.js
  ├─ 模块级状态
  ├─ Fetch /api/*
  ├─ Chat SSE 解析与 handleStreamEvent()
  ├─ Eval SSE 解析与对应 handler
  ├─ DOM 渲染
  └─ LocalStorage 历史快照
```

脚本不是 ES module，函数和状态共享同一个全局脚本作用域。修改函数名、元素 ID 或 CSS class 时，要同时搜索 HTML、JavaScript 和 CSS 的引用。

## Pages and Responsibilities

### Agent

核心流程位于 `sendMessage(selectedDiagnosis)`：

1. 校验至少选择一个知识库；
2. 创建用户消息、思考容器和助手输出状态；
3. `POST /api/chat`，发送 `query`、`session_id`、`selected_diagnosis`、`selected_kbs`；
4. 使用 `ReadableStream.getReader()` 和 `TextDecoder` 逐行解析 `data:` SSE；
5. 由 `handleStreamEvent()` 更新思考步骤、诊断选项、报告、参考切片和耗时；
6. 请求结束后把当前浏览器对话快照保存到 LocalStorage。

单次聊天请求使用 `AbortController` 设置 60 秒超时。超时、网络失败、HTTP 非成功状态和后端 `error` 事件都会在聊天区域显示系统错误。

### Knowledge Bases

页面负责：

- 上传 `.xlsx` 文件到 `/api/kb/upload`；
- 从 `/api/kbs` 加载列表；
- 触发 `/api/kb/{kb_id}/build`；
- 查看 `/api/kbs/{kb_id}/chunks`；
- 删除 `/api/kb/{kb_id}`；
- 维护 Agent 当前选择的知识库集合。

如果存在 `processing` 状态，页面每 4 秒刷新知识库列表；没有处理中的任务时停止轮询。代码中还保留 `/api/kb/build` 和 `/api/kb/extract-metadata` 的维护入口，调整时需同时确认 Worker 对应路由仍然受支持。

### Evaluation

评测页支持手工添加、删除和清空用例，按知识库/检查方式/多级元数据筛选候选切片，并调用：

- `/api/eval/auto_generate`：SSE 自动生成测试用例；
- `/api/eval/run`：SSE 执行 Retrieval Eval；
- `/api/eval/cancel`：请求取消；
- `/api/eval/status`：页面加载或轮询时恢复最近运行现场；
- `/api/eval/dataset`：管理评测数据集。

自动生成消费 `start`、`warning`、`progress`、`done`、`error`；评测运行消费 `start`、`progress`、`done`、`cancelled`、`error`。两者通过 `consumeSSEStream()` 按空行切分标准 SSE frame，与聊天的逐行解析函数不是同一套实现。

### Configuration

配置页通过 `GET /api/config` 加载配置，通过 `POST /api/config` 保存完整配置对象，通过 `POST /api/test-model` 测试 LLM、Embedding 或 Rerank 连接。后端会掩码返回的密钥；前端不应把真实服务密钥写进 HTML、JavaScript、LocalStorage 或 README。

配置中的 API 子页面调用 `/api/deployments` 管理真实 Deployment。创建和编辑时只能选择 Active KB；列表显示启用状态、运行健康度、固定 KB、Key 前缀和最后使用时间。创建/轮换返回的明文 Key 仅保存在当前页面内存并显示在一次性提示区，刷新后无法找回，只能再次轮换。

页面生成的外部地址是 `/api/v1/deployments/{deployment_id}/chat`。示例请求只包含 `message`、`conversation_id` 和可选 `selected_diagnosis`，不允许调用方传 `selected_kbs`。这套公开 Chat 使用独立的稳定 SSE 契约，不应直接复用 Agent 页的内部事件展示逻辑。

### Session Monitor

监控页调用 `/api/sessions` 获取后端会话列表，调用 `/api/memory?session_id=...` 查看记忆，调用 `DELETE /api/session` 删除指定会话。

## State and Persistence

当前状态管理是模块级变量，不存在中央 store。重要状态包括：

- `SESSION_ID` 和当前浏览器对话 ID；
- `isProcessing`、流 reader 与 AbortController；
- `knowledgeBases`、`selectedKnowledgeBaseIds` 和知识库轮询 timer；
- 评测运行、取消、轮询和筛选状态；
- `_configData`、Deployment 列表/知识库选择，以及仅存在于当前页面内存的一次性明文 Key。

### LocalStorage keys

| Key | 内容 |
| --- | --- |
| `chat_history_list` | 浏览器端对话索引、标题、时间和对应 `session_id` |
| `chat_messages_{conversationId}` | 已渲染的消息 HTML 与思考过程快照 |
| `selectedKnowledgeBaseIds` | 当前选中的知识库 ID 列表 |

浏览器 `conversationId` 用于管理本地 UI 历史，后端 `session_id` 用于恢复 SQLite 中的轮次、槽位和上一份报告，两者不能互换。新建浏览器对话时会生成 `web_` 前缀的 session ID；后端 `/api/session/new` 使用另一种 ID 格式。

LocalStorage 保存的是渲染快照，不是后端会话事实来源。修改消息 DOM 结构或 class 名时，要检查旧快照恢复是否仍兼容；不要在其中存储敏感医疗信息或密钥，除非部署方已经明确接受浏览器持久化风险。

## API Contract

前端固定使用：

```js
const API_BASE = "/api";
```

因此所有请求默认同源。当前主要调用如下：

| 功能 | 方法与路径 |
| --- | --- |
| 对话 | `POST /api/chat`、`POST /api/clear`、`GET /api/memory` |
| 知识库 | `GET /api/kbs`、`POST /api/kb/upload`、`POST /api/kb/{id}/build`、`DELETE /api/kb/{id}`、切片和筛选接口 |
| 配置 | `GET /api/config`、`POST /api/config`、`POST /api/test-model` |
| API 上线 | `GET/POST /api/deployments`、Deployment 更新/软删除、启停和 Key 轮换 |
| 评测 | `/api/eval/dataset`、`/api/eval/auto_generate`、`/api/eval/run`、`/api/eval/cancel`、`/api/eval/status` |
| 会话监控 | `GET /api/sessions`、`GET /api/memory`、`DELETE /api/session` |

路由请求模型、完整列表以及外部 `/api/v1/*` 契约见[后端 README](../app/README.md)。

## Chat SSE Contract

`handleStreamEvent()` 当前显式处理以下类型：

| 事件 | 前端行为 |
| --- | --- |
| `status` | 更新顶部状态文案 |
| `intent`、`query_rewrite`、`context_resolve`、`search` | 添加思考步骤 |
| `recall`、`rerank`、`search_result` | 展示检索细节；`rerank` 同时收集报告参考切片 |
| `reasoning`、`memory_retrieval`、`tool_executed` | 展示 Agent/记忆细节 |
| `entity_update`、`intent_switch` | 展示上下文变化 |
| `context_usage` | 更新上下文占用 |
| `ambiguous` | 结束思考状态并显示诊断选择按钮 |
| `token` | 追加助手文本增量 |
| `report` | 完成助手消息并按“结构化报告”渲染 |
| `message` | 完成普通助手消息 |
| `error` | 中止当前助手消息并显示错误 |
| `done` | 完成消息并追加总耗时 |

`search_result` 是兼容分支；在已核对的当前主 Worker/pipeline 中没有对应的主要发送点。后端还可能发出 `query_plan`、`cache_hit`，但当前 `handleStreamEvent()` 没有显式分支，会直接忽略。若这些信息需要展示，应同时定义 payload、增加渲染分支并更新本文。

虽然前端支持 `token`，当前主 Agent 报告路径通常发送过程事件和最终 `report`/`message`，不保证逐 token 输出。不要让 UI 状态转换只依赖首个 `token`。

聊天 reader 还识别纯文本 `[DONE]` 终止标记。增加多行 data、event/id/retry 字段等 SSE 能力时，应评估是否把聊天解析器统一到 `consumeSSEStream()`，避免两套解析规则继续分化。

## Rendering and Styling

- `renderMarkdown()` 先转义 HTML，再处理有限的 `##`/`###` 标题、粗体和换行；它不是完整 Markdown 引擎。
- Rerank 参考信息使用原生 DOM 和 `<details>`/展开区域渲染，字段来自 SSE payload。
- 动态后端文本应通过 `escapeHtml()` 或 `textContent` 进入页面。新增 `innerHTML` 模板时要明确区分可信静态模板与外部数据。
- 视觉状态主要由 CSS class 控制。新增状态时优先复用现有 class 命名和设计变量，并验证窄屏布局。
- HTML 中存在较多由 `app.js` 查询的固定 ID；重命名必须跨三个文件同步。

## Development Workflow

修改前先定位最小范围：

1. 页面结构或可访问性：从 `index.html` 的元素 ID、`data-page` 和对应表单开始。
2. 行为或 API：在 `app.js` 中搜索路由字符串、事件类型或元素 ID。
3. 视觉：在 `style.css` 搜索同一 class，并确认 JavaScript 是否动态切换它。
4. 后端契约：阅读 `app/chat/server.py`、`app/chat/schemas.py` 和最近的 `AGENTS.md`。

变更后至少手工检查：

- 页面导航、弹窗打开/关闭和键盘 Escape；
- 新建/恢复/清空对话与刷新后的 LocalStorage 恢复；
- 正常聊天、诊断歧义、错误和 60 秒超时状态；
- 知识库上传、构建轮询、切片查看与选中状态；
- 配置加载/保存/模型测试；
- 评测开始、进度、取消、完成和刷新后恢复；
- 浏览器控制台无语法错误，网络请求路径和 payload 符合 Worker 接口；
- 静态资源查询参数已按需更新。

当前没有前端单元测试、端到端测试、lint 或构建命令。可使用浏览器开发者工具做网络与控制台检查；涉及后端契约时，运行后端对应的最小测试，而不是虚构 `npm test`。

## Known Constraints

- `app.js` 和 `style.css` 都是大型单文件，功能之间共享全局状态，改动时容易产生隐式耦合。
- 页面切换不改变 URL，也没有浏览器前进/后退路由语义。
- API 基址固定为同源 `/api`，没有运行时前端配置层。
- 聊天和评测使用两套 SSE 解析实现。
- 没有自动化前端回归测试。
- 本地聊天快照包含渲染 HTML，DOM 结构迁移需要兼容旧数据。
- Deployment 管理没有单独的前端自动化或浏览器 E2E 测试；修改后应检查创建、编辑、轮换、启停、健康状态和一次性 Key 提示。

这些约束是当前代码状态，不是路线图。若实际重构消除了某项限制，应在同一次变更中更新本文。

## Change Guide

| 要修改的内容 | 主要位置 | 必须联查 |
| --- | --- | --- |
| 新增页面/导航 | `index.html`、页面切换逻辑 | `style.css`、默认页面状态 |
| 聊天请求字段 | `sendMessage()` | 后端 `ChatRequest`、`/api/chat` |
| SSE 新事件 | `handleStreamEvent()` | 后端发送点、payload 转义、终态处理 |
| 消息/报告渲染 | assistant stream 与 `renderMarkdown()` | LocalStorage 恢复、XSS 边界 |
| 知识库操作 | KB 函数与 modal | Worker KB 路由、轮询终止条件 |
| 评测流程 | Eval 函数与 `consumeSSEStream()` | Worker Eval 事件、状态恢复 |
| 配置字段 | 配置表单和序列化 | `/api/config`、`app/config.py`、真实消费者 |
| API 上线/Deployment | Config 页 Deployment 函数与弹窗 | Worker 管理路由、Gateway 公开路由、Key 一次性显示、SSE 示例 |
| 样式或 class | `style.css` | HTML 和 JS 的选择器/动态 class |
| 静态资源 | `app.js`/`style.css` | `index.html` 中对应 `?v=` |

## Related Documentation

- [项目总览、安装与部署](../README.md)
- [后端与 Agent 开发指南](../app/README.md)
- [后端导航索引](../app/AGENTS.md)
- [RAG 详细说明](../app/rag/README.md)
- [评测中心说明](../app/eval/README.md)
