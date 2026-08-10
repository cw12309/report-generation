# Project Guide

这份文档是本项目的完整技术复习指南。阅读目标是：读完以后，能够重新解释整个系统、追踪一次请求、定位主要代码，并知道常见修改应该从哪里开始。

它不替代其他文档：安装、启动、部署与可复制命令见 [README.md](README.md)；后端和前端开发入口见 [app/README.md](app/README.md) 与 [front/README.md](front/README.md)；专项操作见模块 README；AI Coding Agent 的 File Map、Symbol Map 和局部修改规则见 [AGENTS.md](AGENTS.md) 及分层 `AGENTS.md`。发生冲突时，以实际源码为准。

## 1. 这个项目到底是什么

系统解决的是“如何基于既有医学影像报告模板和用户上下文，生成或修改结构化影像报告文本”。用户输入检查类型、部位、诊断、病史或修改要求，系统输出普通回复、诊断歧义选项，或包含“影像学表现/诊断意见”的报告文本。

模板知识库存在，是因为报告措辞、层级、检查方式和疾病描述需要有可检索的专业参考；Agent 存在，是因为同一句自然语言可能是在生成新报告，也可能是在编辑、润色或普通咨询；RAG 负责从共享模板库找依据；Memory 负责理解当前用户在本会话里已经说过什么；Tool Calling 把这些不同动作变成明确、可路由的能力。

当前产品能力包括：

- 有状态的多轮报告生成、编辑和润色；
- 多知识库选择与 `kb_id` 逻辑隔离；
- Metadata-aware Dense/Sparse Hybrid Retrieval 与 Rerank；
- Excel 知识库上传、构建、切片查看和删除；
- Retrieval Eval、进度恢复和数据集维护；
- 带 Bearer Token 的无状态检索与报告生成 API。

明确边界：本项目处理的是**医学影像报告文本**。它不读取 DICOM，不分析 CT/MR 像素，不运行视觉诊断模型，也不能被描述为独立的医学图像诊断系统。真实临床应用仍需要医生审核、隐私治理和合规控制。

## 2. 一句话理解整个系统

用户通过原生 Web 前端提交检查或报告修改要求，Worker 恢复 SQLite Session 后由 Agent 判断是直接回复、生成新报告、精确编辑还是风格润色；新报告走 `rag_search`，将 Query 规划为语义词、词面词和合法 Metadata Filter，在 Milvus 中执行多通道 Dense/Sparse + RRF 召回，再经 Cross-Encoder Rerank，把参考模板送入 `structure.md` 生成最终报告；编辑和润色则复用 Session 中的 Last Report，不重新检索，所有过程通过 SSE 返回并在结束时持久化。

## 3. 整体系统架构

```text
                          ┌──────────────────────────┐
                          │ Frontend                 │
                          │ HTML + CSS + browser JS  │
                          └────────────┬─────────────┘
                                       │ same-origin /api/* + SSE
                                       ▼
External Client ── Bearer ──► ┌──────────────────────┐
                              │ Gateway              │
                              │ auth + stream proxy  │
                              └──────────┬───────────┘
                                         │ HTTP to :8001
                                         ▼
                              ┌──────────────────────┐
                              │ Worker / FastAPI     │
                              │ routes + runtime hub │
                              └───────┬──────┬───────┘
                                      │      │
                       Stateful Chat ─┘      └─ KB / Eval / Config APIs
                                      │
                                      ▼
                              ┌──────────────────────┐
                              │ Agent Pipeline       │
                              └───┬────────┬─────┬───┘
                                  │        │     │
                                  ▼        ▼     ▼
                               Memory   Prompt  Tool Registry
                                                 │
                               ┌─────────────────┼────────────────┐
                               ▼                 ▼                ▼
                          rag_search        edit_report       refine_report
                               │                 │                │
                               ▼                 └──── Last Report┘
                  Query Rewrite / Planning
                               │
                 Metadata Navigator + Embedding
                               │
              Dense + Sparse × Recall Channels
                               │
                         Milvus RRF
                               │
                    External Cross-Encoder Rerank
                               │
                     structure.md + Chat LLM
                               │
                      Structured Report

Offline: Excel ─► Markdown + SQLite Chunks + Metadata Tree
                              └─► Dense/Sparse ─► shared Milvus Collection

Shared state: SQLite SessionStore ─► Sessions / Turns / State / Thinking
                                  └► KB metadata/chunks / Eval state

External model services: Chat LLM / Rewrite-Planning LLM / Embedding / Rerank
```

各层存在的理由：Gateway 隔离公开鉴权与业务运行时；Worker 统一管理 HTTP、SSE 和共享资源；Pipeline 负责一轮 Agent 编排；Tools 把业务动作变成明确边界；RAG 只解决知识库参考选择；Memory 只解决当前会话上下文；SQLite 保存关系型状态与原文；Milvus 保存在线向量检索数据；Data Pipeline 保证两类存储和人类可读中间态一致；Eval 用稳定 Chunk ID 衡量检索结果。

## 4. 从用户输入到最终结果：完整生命周期

这是理解项目的主线。以浏览器 Stateful Chat 为例：

1. **Frontend 收集输入**

   输入是用户文本、当前 `SESSION_ID`、可选 `selected_diagnosis` 和 `selected_kbs`。`sendMessage()` 先要求至少选择一个知识库，再 `POST /api/chat`。关键代码：`front/app.js`。

2. **Gateway 或 Worker 接收**

   完整本地/Docker入口通常先到 Gateway 的 catch-all legacy proxy；直接启动 Worker 时则直接进入 Worker。Gateway 只校验来源并透明转发，不理解 Session 或 Agent。关键代码：`app/gateway.py`。

3. **Worker 验证知识库**

   `/api/chat` 要求每轮显式传至少一个 Active KB，并拒绝不存在、禁用或构建失败的 ID。输出是去重后的 KB ID 和对应 SQLite KB 记录。

4. **恢复 Session**

   `_get_or_create_session()` 从 `data/sessions.db` 加载 Turns、Entity Slots、Last Report 和 Selected KBs，再重建 STM、EntityTracker 和 MilvusClient；新 ID 则创建数据库记录。歧义缓存实际从进程内 `_ambiguity_cache` 恢复。

5. **Entity 与 Intent**

   Pipeline 尝试更新 `modality/body_part/clinical_history/diagnosis`，检测 `new_session/append/switch`。`switch` 会清 STM、Last Report 和旧实体；`append` 表示沿用上下文；`new_session` 在当前实现中是默认意图标签，不等于创建新的 SQLite Session。

6. **Context Resolution 与 Rewrite**

   EntityTracker 用已有 modality/body part 补全“继续”“这个部位”等省略表达，再执行规则标准化。只有 `needs_rewrite()` 判断为“有类型或部位但缺诊断”等情形时才调用 Rewrite LLM；失败返回标准化后的原 Query。缺模态或过于模糊时可提前发送追问并结束本轮。

7. **Memory Retrieval**

   Pipeline 从 STM 取历史，用 `MemoryRetriever` 对每条消息和当前增强 Query 做 Embedding/Cosine Similarity，选择最多三条相关消息。输出是给主 Agent Prompt 的“相关历史对话”，不是 RAG 文档。

8. **PromptBuilder 组装主 Prompt**

   `tool_orchestrator.md` 与 Entity Context 组合；随后 Pipeline 追加相关 STM、Last Report 和最近历史消息。输出是 OpenAI-compatible messages 和三个 Tool Schema。

9. **Main LLM 决定动作**

   `chat_with_tools()` 非流式调用主模型。无 Tool Call 时直接把 content 作为 `message`；有 Tool Call 时进入注册表。这里最多产生一个 Tool Call 批次，不会反复 ReAct。

10. **执行 Tool**

    `rag_search` 做检索与报告生成；`edit_report`、`refine_report` 自动补入 Last Report。Registry 把 handler 返回 JSON 中的 `_is_final` 剥离为 `ToolResult.is_final`。

11. **可选 RAG**

    只有 `rag_search` 走 Query Plan、Embedding、Milvus Hybrid Search、RRF、Dedup 和 Rerank，再调用报告 LLM。编辑和润色不访问 Milvus。

12. **可选 Final LLM**

    如果没有任何 Final Tool Result，Pipeline 把 Tool Result 加回 messages，再调用一次禁用 Tools 的主 LLM。只要存在 Final Result，就跳过该调用。

13. **Last Report 与展示结果**

    Final JSON 若包含 `results` 或兼容的报告字段，就保存到可变 `last_report[0]`，再由 `json_to_display()` 转成前端 Markdown 文本并发出 `report`。歧义结果发出 `ambiguous`，不作为报告保存。

14. **持久化与 SSE 结束**

    Worker 保存 Turn、Slots、Last Report、Selected KBs 和 Thinking Events，然后发出 `done` 与 `[DONE]`。Frontend 更新思考区、报告气泡、参考切片和耗时，并把渲染快照写入 LocalStorage。

关键理解：SSE 事件在 Pipeline 执行期间已经陆续进入队列；SQLite 持久化发生在 Pipeline 返回后、`done` 之前。浏览器断开聊天流并不会显式取消已创建的后台 `run()` task。

## 5. Stateful 和 Stateless 两条路径

### Stateful Chat

`POST /api/chat` 面向浏览器/内网交互。它需要 `session_id`、每轮 `selected_kbs`，使用 STM、EntityTracker、Last Report 和歧义缓存，并以 SSE 返回过程。其价值是支持“继续”“把上一份改短一点”等依赖历史的请求。

### Stateless Retrieval

`POST /api/v1/rag/search` 由 Gateway 校验 Bearer Token，再转发到 Worker `/internal/rag/search`。它不创建 Session，不更新 Memory，只返回原 Query、拼装后的参考文本和结构化 Retrieval Candidates。空 `selected_kbs` 在该路径表示使用所有 Active KB。

### Stateless Report Generation

`POST /api/v1/report/generate` 先复用同一个 `_stateless_search()`，再创建临时 EntityTracker，并复用 `create_rag_search_handler()` 与 `structure.md` 生成报告。它没有 Last Report、STM 或持久化，但可以接受 modality、body part、clinical history、diagnosis 和 selected diagnosis。

重要差异：Stateful Pipeline 在 Tool 决策前执行 Query 标准化和可选 Rewrite；Stateless `_stateless_search()` 直接把去空白的原 Query 交给 `search_reports()`，只做 Query Planning，不经过 `standardize_query()/rewrite_query()`。

| 维度 | Stateful Chat | Stateless Retrieval | Stateless Generation |
| --- | --- | --- | --- |
| 入口 | `/api/chat` | `/api/v1/rag/search` | `/api/v1/report/generate` |
| 鉴权 | Legacy 来源限制；Worker 直连无 Bearer | Gateway Bearer | Gateway Bearer |
| Session/Memory | 有 | 无 | 无 |
| Last Report | 有 | 无 | 无 |
| Query Rewrite | 有条件执行 | 不执行 | 不执行 |
| Retrieval/Rerank | Tool 选择后可执行 | 总是执行 | 总是执行 |
| Report LLM | 仅 `rag_search` | 无 | 有，复用 RAG Tool handler |
| 响应 | SSE | JSON（Gateway 仍透明流式代理字节） | JSON |
| KB 为空 | 拒绝 | 使用全部 Active KB | 使用全部 Active KB |

## 6. Frontend：用户看到的系统

### 6.1 技术栈

前端是单页静态应用，但不是框架 SPA：单个 `index.html`、原生 CSS、经典浏览器 JavaScript。没有 React/Vue、TypeScript、Node 构建工具、Router、状态库、Axios、组件库、前端环境变量、lint 或自动化测试。网络层使用 Fetch、ReadableStream、TextDecoder 和 AbortController；API 基址固定为同源 `/api`。

Worker 把 `front/` 挂载到 `/static`，`/` 返回 `index.html`。静态资源通过 `?v=` 手动失效缓存。

### 6.2 页面结构

| 页面 | DOM 容器 | 真实职责 |
| --- | --- | --- |
| Agent | `pageAgent` | KB 选择、聊天、思考步骤、参考切片、Memory 面板和本地历史 |
| Knowledge Base | `pageKB` | 上传、构建、轮询进度、切片详情、删除 |
| Config | `pageConfig` | 模型/检索/系统配置、模型连通性测试；API 上线子页是 Mock |
| Eval | `pageEval` | 数据集 CRUD、级联筛选、自动生成、运行/取消/恢复评测 |
| Monitor | `pageMonitor` | 后端会话列表、Memory 查看和 Session 删除 |

页面切换依赖 `data-page` 与 DOM class，不改变 URL，没有前进/后退路由语义。

### 6.3 Chat UI 数据流

```text
textarea + selected KB Set
  → sendMessage()
  → POST /api/chat
  → response.body.getReader()
  → 逐行识别 "data: "
  → JSON.parse()
  → handleStreamEvent()
  → Thinking / Assistant / References DOM
  → LocalStorage snapshot
```

聊天请求有 60 秒前端超时。HTTP 非 2xx 只显示 `HTTP <status>`，不会解析 FastAPI 的详细错误体。聊天 SSE parser 只接受以 `data: ` 开头的单行 JSON 和 `[DONE]`；Eval 使用另一个按空行切 frame 的 `consumeSSEStream()`。

### 6.4 前端保存哪些状态

- 内存：`SESSION_ID`、当前 conversation ID、处理中标志、reader/controller、KB 列表和选择、KB timer、Eval 状态、配置对象和 Mock API 状态。
- LocalStorage：`chat_history_list`、`chat_messages_{conversationId}`、`selectedKnowledgeBaseIds`。
- 不在浏览器保存为权威状态：Entity Slots、Last Report、后端 Turns、Eval 结果和 KB 元数据；它们由 Worker/SQLite 管理。

LocalStorage 保存消息 `innerHTML` 和 Thinking `outerHTML`。它能恢复 UI，但不是 Session 真相来源，也意味着浏览器可能持久化医学文本。

### 6.5 SSE

前端显式消费：`status`、`intent`、`query_rewrite`、`context_resolve`、`search`、`recall`、`rerank`、`search_result`、`token`、`reasoning`、`report`、`message`、`error`、`entity_update`、`intent_switch`、`memory_retrieval`、`tool_executed`、`ambiguous`、`context_usage`、`done`。

其中 `search_result` 是前端兼容分支，当前主后端没有对应发送点；后端会发 `query_plan` 和 `cache_hit`，但前端 switch 没有分支，所以被静默忽略。`rerank` 同时收集参考切片供回答底部展示。

### 6.6 Frontend 与 Backend Contract

最关键请求是：

```json
{
  "query": "CT 头颅 脑出血",
  "session_id": "web_...",
  "selected_diagnosis": null,
  "selected_kbs": ["kb-id"]
}
```

结果不是单个 JSON response，而是若干 `data: {event}` 和最终 `data: [DONE]`。正式报告事件的 `content` 已由后端从报告 JSON 转成人类可读 Markdown；前端不会直接理解 `results` Schema。前端 `renderMarkdown()` 只支持转义后的二/三级标题、粗体和换行，不是完整 Markdown parser。

### 6.7 修改前端时去哪里

- 页面结构、表单、ID：`front/index.html`。
- Chat/SSE/API/LocalStorage：`front/app.js`。
- 布局、状态和响应式：`front/style.css`。
- 请求模型和事件发送点：`app/chat/schemas.py`、`app/chat/server.py`、`app/chat/pipeline.py`、`app/tools/`。
- 修改 JS/CSS 后同步更新 `index.html` 的资源 `?v=`。

## 7. Gateway：为什么还需要一层 Gateway

Gateway 是安全与部署边界，不是业务层。它创建一个复用的 `httpx.AsyncClient`，剥离 hop-by-hop 和 Authorization 等头，透明转发请求体和上游响应流。

- 负责：`/api/v1/*` Bearer 校验、请求模型的边界验证、Worker 超时/不可用映射、Legacy 来源限制和流式代理。
- 不负责：Session、LLM、Tool、RAG、SQLite、Milvus 或报告生成。
- Public Stateless：`/api/v1/rag/search`、`/api/v1/report/generate`，必须 Bearer。
- Stateful Legacy：其他路径走 catch-all。默认只允许 loopback/private/link-local 来源；`ALLOW_LEGACY_PUBLIC_PROXY=true` 才放开。

Gateway 与 Worker 分离让公开进程不导入模型或数据库代码，也能使 Worker 在 Docker 中不暴露主机端口。需要注意：本地浏览器访问 `:8000` 时也经过 Legacy Proxy；直接访问 `:8001` 才是直连 Worker。

## 8. Worker：整个后端运行中心

`app/chat/server.py` 是高连接度运行中心。它创建 FastAPI、SessionStore 和评测锁，启动时校验 Milvus Schema，挂载前端，并注册 Chat、Session、KB、Config、Eval 和 Internal API。

它的合理职责是 HTTP/SSE 边界和资源生命周期；当前它还直接编排知识库后台构建、模型连通性测试、评测后台 task 和 Session 重建，所以任何跨模块契约变化都容易汇聚到这里。修改它时通常要联查 Pydantic Schema、Frontend、SessionStore 和实际业务模块。

Worker 不是鉴权边界。将 `:8001` 暴露到公网会绕过 Gateway 的 Bearer 和 Legacy 来源限制。

## 9. Agent Pipeline：系统的大脑

`run_pipeline()` 的真实形态：

```text
Input
  → ambiguity cache match
  → Entity update
  → Intent detection / optional switch cleanup
  → Context resolution
  → Standardization / optional Rewrite
  → missing-information gates
  → STM semantic retrieval
  → tool_orchestrator Prompt
  → Main LLM with Tools
       ├─ no tool: direct message
       └─ one Tool Call batch
             → execute all calls
             → any final result: direct report/ambiguity
             → otherwise: one final LLM without Tools
```

它不是无限 ReAct Loop。模型可以在一个响应中返回多个 Tool Calls，Registry 会逐个执行；Pipeline 使用 `any_final` 判断是否直接结束，而不是继续下一轮决策。

## 10. 为什么需要 Tool Calling

```text
Main LLM
   ├─ no tool       → 问候、咨询、补充说明
   ├─ rag_search    → 新报告，需要共享知识库
   ├─ edit_report   → 精确改已有报告
   └─ refine_report → 调整已有报告风格/详细程度
```

如果每次都强制 RAG，“把 70HU 改成 65HU”也会重新检索并引入无关模板。Tool Calling 把“决定做什么”与“怎样完成业务动作”分开：主 LLM 负责路由，Tool 负责受约束执行。

## 11. rag_search：新报告是怎么产生的

输入是 Tool arguments 中的 Query、可选 modality/body part，以及注入的 EntityTracker、KB 列表、Milvus client 和歧义缓存。缺少 modality/body part 时会从 Entity Slots 补全。

正常路径：

```text
Tool Call
  → search_reports()
  → QueryPlan + Hybrid Retrieval + Rerank
  → "### 参考N（Rerank分数）" Context
  → PromptBuilder.build("structure", entity, last_report)
  → Chat LLM
  → extract_json()
  → {results:[...], _is_final:true}
```

Top 候选分数接近且诊断名不同会返回 `status=ambiguous` 和选项，并缓存 Retrieval Result。用户选择后复用缓存、按诊断名过滤候选、跳过重新检索。

`is_final` 的意义是：Tool 已完成报告 LLM 和 JSON 解析，结果已经是用户需要的业务产物。再次送回 Main LLM 只会增加延迟，并可能改写医学术语，所以 Pipeline 直接发 `report`。

## 12. edit_report

`edit_report` 输入 Current Report 和具体 instruction；Current Report 通常由 Pipeline 从 Last Report 自动注入。它加载 `edit.md`，调用 Chat LLM，尝试保持原结构，只修改指定内容，并返回 Final Result。

它不重新 RAG，因为用户目标是修改既有事实，而不是重新选择模板。适用例子：修改数值、删除指定病变、替换某段诊断意见。

当前结构保护只在新旧 JSON 都含顶层 `影像学表现` 字典时比较 Key；主报告当前采用 `results` 数组，因此该保护不能覆盖主 Schema。参见 §53 的 Prompt/Schema 风险。

## 13. refine_report

`refine_report` 输入 Current Report 和 `style`，通过 PromptBuilder 把 Entity Context 与 `refine.md` 组合，再让 LLM 调整详细程度、正式度或表达方式。它同样不重新 RAG，因为其设计边界是保留医学内容主干，只改变表达。

与 edit 的区别：edit 针对“改哪个事实/字段”，refine 针对“怎么写”。当前 refine 允许“补充鉴别诊断”等 style，但这与“不改变医学内容”的原则存在张力，必须通过人工或测试验证输出。

## 14. RAG：整个检索系统为什么这么设计

RAG 的目标不是泛化的“找相似文本”，而是：**在选定医学模板知识库内，在尽量遵守检查类型和目录约束的同时，保持足够召回率，找出与当前诊断语义和术语最相关的参考报告。**

它位于 `rag_search` 内部：上游是 Agent/Tool，输入为增强 Query、Entity Context 和 KB 范围；下游是 Milvus、Embedding、Rerank，输出为带来源元数据的候选和拼装好的参考上下文。后续章节拆解每个阶段。

## 15. Query Rewrite

Rewrite 只存在于 Stateful Pipeline 的预处理阶段。顺序是先 `resolve_context()`，再 `standardize_query()`，最后根据 `needs_rewrite()` 决定是否请求 Rewrite LLM。

- Rule Standardization：确定性别名与中英文边界处理，例如在合适上下文中把“头部”规范为“头颅”、把 `CT脑出血` 变为 `CT 脑出血`。
- LLM Rewrite：只在已有检查类型或部位、但没有可识别诊断词时触发，用 `rewrite.md` 和模块级 metadata 术语扩展 Query。
- 不全量 Rewrite：已有明确诊断时避免模型改掉关键词；过于模糊时优先追问；用户选择歧义诊断时直接复用选择。
- 失败降级：请求异常、超时或返回无效内容时使用标准化后的原 Query。

Rewrite 通过 `requests.post()` 直接调用 OpenAI-compatible `/chat/completions`，不经过 `llm_client.py`。它使用模块导入时加载的 API Key、metadata 和 Prompt；配置或数据变化后不一定自动刷新。

Stateless Retrieval/Generation 不调用 Rewrite。它们直接进入 §16 的 Query Planner。

## 16. Query Planner

不能简单执行 `User Query → Milvus`，因为同一句话同时包含语义、精确医学词和目录约束；把整个字符串既当过滤器又当向量文本，会造成错误过滤或词面信息损失。

`build_query_plan()` 通过规则、Entity Context、Metadata Navigator 和可选 Planning LLM 生成：

```json
{
  "query": "CT 头颅 颅脑平扫 脑出血",
  "semantic_query": "CT 头颅 颅脑平扫 脑出血",
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

字段去向：

- `semantic_query` → Dense Embedding；当前等于 Planner 收到的 Query。
- `keyword_query` → Sparse Vector；尽量去掉已识别的 modality/levels，保留诊断词。
- `diagnosis_query` → 当前主要作为计划解释字段，不直接驱动 Retrieval API。
- `filters` → strict/modality recall channels 的 Milvus scalar expression。
- `slots` → 可观测性与置信来源；包含 rule/history/LLM 的来源。

规则匹配优先于历史槽位，历史优先于 LLM 推断；LLM 只能从 Navigator 提供的候选中选择，最终还会再次 validate。Planning LLM 使用同步 `OpenAI` 客户端直接调用，失败时保留规则计划。

## 17. Metadata Navigator

Metadata Navigator 不是 Retriever。它不返回相似文档；它解决的是：**阻止 LLM 生成知识库中不存在或不属于当前父级路径的医学层级 Filter。**

在线 Stateful/Stateless 服务把所选或全部 Active KB 的 `knowledge_bases.metadata_tree` 从 SQLite 传给 `MetadataNavigator.from_knowledge_bases()`，合并后形成合法路径。直接构造 `MetadataNavigator()` 时才会尝试配置的 JSON 路径和 `app/data_pipeline/metadata.json` fallback。

它约束：`modality`、`level_1`、`level_2`、`level_3`、`level_4`。对每一层，候选会受已经选择的祖先层级限制；显式文本匹配选择最长合法值；LLM 值只有在 `validate()` 成功时进入 Plan。

这对医学模板库很重要，因为同名子目录可能属于不同 modality/父级，模型凭常识生成的目录也可能根本不存在。Navigator 把“模型推理”限制到“本次实际可搜索的数据空间”。

## 18. Dense Retrieval

Dense 解决不同措辞但语义相近的问题。`semantic_query` 经 Embedding API 变成与建库维度相同的向量，在 Milvus `dense_vector` 上用 HNSW + COSINE 搜索候选。

```text
semantic_query
  → Embedding model
  → float vector
  → HNSW / COSINE under kb_id + metadata filter
  → ranked candidates
```

它适合“脑内高密度出血灶”和“脑出血”等语义近似，但不能替代精确目录与术语过滤。Embedding 模型和 dimension 是离线/在线契约，换模型后旧向量不能安全混用。

## 19. Sparse Retrieval

当前 Sparse **不是 BM25**，没有 IDF 和文档长度归一化。`text_to_sparse_vector()` 生成：

- 中文单字；
- 连续中文 Bigram；
- 英文和数字 token；
- BLAKE2b 哈希到 `2^20` 维空间；
- 词频权重 `1 + log(count)`。

Milvus 在 `sparse_vector` 上使用 Sparse Inverted Index + Inner Product。它主要补充精确疾病名、缩写、数值和词面短语，使 Dense 没有把明确术语稀释掉。

建库端 `build_vector_db.py` 与查询端 `retrieval.py` 都复用同一 `app/rag/sparse_vector.py`。任何 tokenization、hash、dimension 或权重变化都要求全量重建，否则同一词会落到不同空间。

## 20. Recall Channels

当前不是 Sequential Fallback，而是一次 Milvus `hybrid_search()` 中的并行请求：

| Channel | Scalar filter | 解决的问题 |
| --- | --- | --- |
| `strict` | `kb_id + modality + level_1..4` 中已有字段 | 精确遵守规划路径 |
| `modality` | `kb_id + modality` | Planner 层级过严时保住同检查类型召回 |
| `kb_only` | 只限制 `kb_id` | 在所选知识库内提供语义兜底 |

每个有效 Channel 同时创建 Dense 和 Sparse 两个 `AnnSearchRequest`，最多六个请求。过滤条件相同的 Channel 会按 signature 去重：例如没有任何 metadata filter 时，三个定义最终只剩一个 `kb_only` Channel。

只用 strict 容易因目录误判漏召回；只用 kb-only 会让不同检查类型和部位互相污染。并行三通道让高精度与高召回同时参与融合，而不是“够五条就停止放宽”。

## 21. RRF Fusion

Dense COSINE、Sparse IP 和不同过滤范围的原始 Score 不在同一尺度。RRF（Reciprocal Rank Fusion）关注每个结果在各列表中的**排名位置**，而不是直接比较原始分数，因此适合融合异构搜索空间。

当前代码把所有 Channel 的 Dense/Sparse 请求一次性交给 Milvus `RRFRanker()`，再限制总 Top-K。直觉是：在多个列表反复靠前的报告获得更高融合排名；只在某一路靠前的报告仍有进入候选池的机会。

## 22. Deduplication

融合后以 `chunk_id` 为首选 key 去重，旧数据缺失时才退回 Milvus `id`、source 或文本。这样同一报告即使被多个 Channel/Dense/Sparse 命中，也只进入 Rerank 一次。

按 `chunk_id` 而非文本去重还能保持 SQLite、Markdown、Milvus 与 Eval 的同一身份语义，并避免内容截断或格式变化导致身份漂移。

## 23. Rerank

第一阶段 Retrieval 的目标是快且不漏，Rerank 的目标是在较小候选池中更精确：

```text
Query + each candidate.combined_text
  → external Cross-Encoder Rerank API
  → relevance_score + original index
  → Top-N candidates
```

当前调用由 `rag/rerank.py` 使用同步 `requests` 完成，`llm_client.rerank_with_retry()` 添加通用重试。Rerank 失败时 Stateful `search_reports()` 回退为 RRF 原顺序前 N 条；Eval 的 `retrieve_and_rerank()` 没有同样的捕获，会让基础设施错误终止评测，以免把错误误计为未命中。

Retrieval vs Rerank：前者在整个 Collection 上用索引找 Candidate，快而近似；后者逐对读取 Query 和候选全文，慢但更准确。RRF 是第一阶段内部的多路排名融合，不等于第二阶段 Cross-Encoder。

## 24. Context Assembly

Rerank 后每条实体被拼成：

```text
### 参考1（Rerank分数: 0.9234）
检查类型：...
一级目录：...
模块名称：...
影像学表现：...
影像学意见：...
```

这些参考作为 `rag_search` 的 User Message，和由 `PromptBuilder.build("structure", entity_context, last_report)` 生成的 System Prompt 一起进入 Report LLM。RAG Context 是本次检索证据，不是用户历史，因此不写入 Memory Prompt 的“相关历史”段落。

## 25. 为什么最终报告没有 Citation

Retrieval Candidate 包含 `source`（优先稳定 `chunk_id`）、`kb_id`、层级元数据、文本和 Rerank Score；前端也会从 `rerank` 事件收集这些参考。

但 `structure.md` 的正式 Report Schema 只有 `reasoning` 和 `results[{影像学表现, 诊断意见}]`，没有 citation/source 字段。`json_to_display()` 也只渲染报告内容。

因此：“系统保留检索来源”不等于“最终报告自带逐条引用”。参考面板是旁路可观测信息，不是报告 JSON 的稳定 Citation Contract。

## 26. Prompt System

Prompt 不是一份大模板，而是不同任务的约束层：

| Prompt | 谁使用 | 作用 |
| --- | --- | --- |
| `tool_orchestrator.md` | Main Agent | 决定直接回复或选择 Tool |
| `structure.md` | `rag_search` Report LLM | 从参考生成当前 `results` 报告 Schema |
| `entity_extraction.md` | EntityTracker | 约束实体 JSON；当前异步调用不匹配导致实际降级 |
| `rewrite.md` | Query Rewrite | 用 metadata 扩展缺诊断 Query |
| `edit.md` | `edit_report` | 只修改指定内容；仍描述旧版顶层字典 Schema |
| `refine.md` | `refine_report` | 调整表达、保持内容；仍描述旧版顶层字典 Schema |
| `summarize.md` | STM | 摘要约束；当前运行时未注入 summarize_fn |

`report_generation.md`、`multi_disease.md`、`react_*` 和 `chat.md` 文件存在，但源码搜索没有发现它们进入当前主运行路径。文件存在不代表启用。

### PromptBuilder

PromptBuilder 的顺序是：Entity Context → 基础模板 → Last Report，以 `---` 分隔；Last Report 是 JSON 时会移除顶层 `reasoning`。Pipeline 在 Builder 外再追加相关 STM、当前已有报告提示和最近历史，所以主 Agent Prompt 的完整组装不全由 Builder 独立完成。

### 四类上下文

- Entity Context：当前检查类型、部位、病史、诊断，进入 System Prompt。
- Memory：相关 STM 消息和最近历史，帮助理解指代与操作意图。
- Last Report：让主 Agent选择 edit/refine，并让 structure 合并已有病变。
- RAG Context：只在 Report Tool 内作为本次 User Context，代表共享知识库参考。

## 27. Memory：为什么需要四个不同组件

### ShortTermMemory

解决“最近几轮说了什么”。它在一次请求中由 SQLite Turns 重建，只保留 `max_rounds` 轮；淘汰轮次会生成规则摘要，因为 Worker 没有传 `summarize_fn`。

### EntityTracker

解决“当前检查类型、部位、病史、诊断和意图到底是什么”。它把结构化状态与自然语言历史分开，使“继续看这个部位”不必完全依赖 LLM 从长历史猜测。

### MemoryRetriever

解决“最近历史中本轮最相关的是哪几条”。它对 STM messages 做 Embedding 和 Cosine Similarity，阈值大于 0.3，最多返回三条。

### SessionStore

解决“下一次 HTTP 请求或进程重启后如何恢复”。它用 SQLite 保存明确的 Turns、Slots、Last Report、Thinking、Selected KBs，还兼任 KB 和 Eval 持久化。

## 28. Memory ≠ RAG

```text
Memory = 当前用户、当前 session 的上下文
RAG    = 所有用户共享、由 selected_kbs 限定的医学模板知识
```

例子：“把刚才报告里的 70HU 改成 65HU”依赖 Memory/Last Report，不应 RAG；“生成胸部 CT 肺结节报告”需要 RAG；“继续看这个部位”先由 Entity/Memory 补全，再可能调用 RAG。

混淆两者会导致两个典型问题：把用户历史当医学知识，或为了编辑已有报告重新检索并改变事实。

## 29. EntityTracker

核心 Slots：

| Slot | 意义 | 更新方式 |
| --- | --- | --- |
| `modality` | CT/MR/DR 等当前检查方式 | 新值覆盖 |
| `body_part` | 当前一个或多个解剖部位 | 去重追加 |
| `clinical_history` | 症状/既往史 | 新值覆盖 |
| `diagnosis` | 已明确疾病/诊断 | 去重追加 |
| `intent` | `new_session/append/switch` | switch 时明确写入；其他检测结果未持续回写 |

意图语义：`append` 是包含“继续/这个”等指代且已有 modality；`switch` 是命中切换词，清理旧 STM、Last Report 和 Slots 后重新抽取；其他返回 `new_session`。这里的 `new_session` 是业务意图，不会生成新 `session_id`。

Context Resolution 只在输入缺 modality/body part 或有指代时，用现有 Slots 前置补全。诊断不会直接拼入 `resolve_context()` 的 fill list，但会进入 Entity Context 和 Planner Context。

当前 Worker 把异步 `chat_stream` 作为 `llm_chat_fn` 传给同步 `_extract_llm()`；后者不 `await` 就把 coroutine 交给 JSON parser，异常后回退规则。因此“LLM + 规则双引擎”在当前在线路径中实际上主要依赖规则，这是需优先修复的实现风险。

## 30. Session 生命周期

```text
Browser SESSION_ID
  → SQLite sessions/session_state/turns
  → each request rebuilds STM + EntityTracker + Last Report
  → run_pipeline()
  → update runtime objects
  → save Turn + Slots + Last Report + Selected KBs + Thinking
  → next request restores again
```

- `Turn`：`turns` 表，一条用户输入与对应助手输出，按 `turn_index` 排序。
- `Slots`：`session_state.entity_slots` JSON。
- `Last Report`：`session_state.last_report`，供 edit/refine 和多病变合并。
- `Selected KBs`：`session_state.selected_kbs`；但 `/api/chat` 仍要求每轮请求显式传入。
- `Thinking Events`：`thinking` 表，保存 Pipeline 发出的过程事件；手工排队的最终 `done` 不在该列表。
- `Ambiguity`：表中有 `last_ambiguity` 字段和 save/load 方法，但 Worker 当前保存时不传它，实际使用进程内 `_ambiguity_cache`。

Frontend 的 `conversationId` 只组织 LocalStorage UI；后端 `session_id` 才标识 SQLite Session。前端生成 `web_*`，后端 `/api/session/new` 生成 `rag_v2_*`。

## 31. SQLite 到底存了什么

`SessionStore` 不只是 Session Repository，而是共享关系型基础设施：

| 表 | 保存内容 | 主要消费者 |
| --- | --- | --- |
| `sessions` | ID、标题、创建时间 | Chat/Monitor |
| `turns` | 用户/助手轮次 | Session Restore、Memory UI |
| `session_state` | Slots、Last Report、Ambiguity 字段、Selected KBs | Pipeline Restore/Persist |
| `thinking` | 每轮过程事件 | 会话思考记录 API |
| `knowledge_bases` | KB 名称、状态、进度、源路径、Metadata Tree | Chat KB validation、Navigator、KB UI |
| `knowledge_base_chunks` | 稳定 Chunk ID、来源、层级、报告原文 | KB UI、Eval 用例选择 |
| `evaluation_state` | 运行进度、最后结果、时间 | Eval 状态恢复 |

每次操作创建短连接，启用 WAL、foreign keys、30 秒 busy timeout，并显式提交/回滚。SQLite 保存可展示原文和业务状态；Milvus 保存在线向量检索副本。两者通过 `kb_id/chunk_id` 对齐，而不是互相替代。

## 32. Data Pipeline：知识库是怎么来的

```text
.xlsx upload
  → one KB record: uploaded
  → read every sheet
  → normalize recognized columns
  → skip invalid/exact duplicate rows
  → one accepted row = one report chunk
  → Markdown + YAML Front Matter
  → replace SQLite knowledge_base_chunks in one transaction
  → extract hierarchical Metadata Tree to knowledge_bases
  → batch Dense Embedding
  → Sparse hashing
  → delete old Milvus entities for this kb_id
  → insert / flush / load
  → KB status active, progress 100
```

上传与构建分开：`POST /api/kb/upload` 只保存文件和创建 `uploaded` 记录；`POST /api/kb/{id}/build` 才启动 Background Task。向量全部成功生成后才删除该 KB 的旧 Milvus 实体，减少 Embedding 失败造成的空窗；但删除和分批插入之间仍不是跨系统原子事务。

## 33. Excel 数据模型

识别的层级列是：`检查类型`、`一级目录`、`二级目录`、`三级目录`、`四级目录`、`模块名称`；内容列是 `影像学表现`、`影像学意见`。代码也接受已标准化的 `modality/level_1...module_name` 列名。

每个 Sheet 都会读取。一个 Row 只要任一标准层级/模块字段非空，就会进入后续处理；完全相同的“目录路径 + 模块名 + 表现/意见”会去重，同路径/模块但内容不同会生成带编号 Markdown。

当前没有 token-based split：一个被接受的 Excel Row 对应一个主要 Report Chunk。原因是模板行本身已经表达一组完整的检查层级、影像表现和意见；再次按 token 切分会破坏字段绑定。代价是长文本会在 Milvus 字段上限处截断，且粒度完全取决于 Excel 维护质量。

## 34. Markdown 的作用

Markdown 是离线构建的可读中间态，而不是在线 Session 数据库。YAML Front Matter 保存 `kb_id`、`chunk_id`、modality、levels 和 module name；正文用 `### 影像学表现`、`### 影像学意见` 保存报告内容。

它有三项价值：让人可检查转换结果；为 Metadata Tree 和 Milvus 提供统一输入；允许迁移/排错脚本从文件重建向量。正式在线 Navigator 使用 SQLite KB Metadata Tree，不依赖全局 `metadata.json`，但部分直接构造和辅助脚本仍保留 JSON fallback。

## 35. Stable Chunk ID

Chunk ID 的公式是：

```text
MD5(kb_id + "\n" + markdown_filename + "\n" + combined_text)
```

同一个 32 位小写 ID 出现在：

- Markdown Front Matter：中间态身份；
- SQLite `knowledge_base_chunks`：人类浏览、Eval 选标签；
- Milvus：检索结果来源和去重 key；
- Eval Dataset `expected_chunk_ids`：黄金标签。

它把离线构建、在线检索和评测串成一个生命周期。修改算法、文件命名或 `combined_text` 组成会改变 ID，需要同步重建 Markdown/SQLite/Milvus，并迁移或重做所有 Eval 标签。

“Stable”是相对同一输入确定，而不是内容变更后永久不变。

## 36. Metadata Tree

`extract_all()` 读取每个 Markdown 的 Front Matter，把 modality 和 `level_1..4` 转成层级树，并写入 `knowledge_bases.metadata_tree`。

```text
Excel directory columns
  → Markdown Front Matter
  → offline Metadata Tree
  → SQLite per KB
  → online merge for selected Active KBs
  → Navigator candidates
  → validated QueryPlan filters
```

这条链把离线数据结构直接连接到在线 Query Planning。增加层级、改字段名或改变父子关系必须同时检查 Excel parser、Markdown parser、Metadata extraction、Navigator、Planner、Milvus schema/filter 和 Eval UI 级联筛选。

## 37. Milvus 里到底存什么

共享 Collection（当前配置 `medical_reports`）的每条实体同时保存：

- 身份与隔离：auto ID、`kb_id`、`chunk_id`；
- Metadata：modality、`level_1..4`、module name；
- Text：findings、opinion、`combined_text`；
- Vector：Dense float vector、Sparse float vector。

多个知识库共用 Collection，通过 `kb_id in [...]` 做逻辑隔离。这样无需为每个上传文件创建和维护独立 schema/index，也能一次搜索多个 KB；删除/重建单个 KB 时按 `kb_id` 清理其实体。

Dense 使用 HNSW/COSINE；Sparse 使用 SPARSE_INVERTED_INDEX/IP；标量字段使用 INVERTED index。Worker 启动时 `ensure_collection_schema()` 会创建缺失 Collection、检查必需字段并尝试补部分标量索引，但不会自动破坏性迁移旧 schema。

## 38. 建库和在线检索的耦合点

以下契约必须一致：

| 契约 | 建库端 | 在线端 | 不一致后果 |
| --- | --- | --- | --- |
| Embedding model | `build_vector_db.py` | `llm_client.get_embedding()` | 向量空间不可比较 |
| Embedding dimension | Collection schema | Query vector | 请求失败或错误结果 |
| Sparse algorithm/dimension | `sparse_vector.py` | 同一文件 | 词项落入不同坐标 |
| Metadata fields | Front Matter/schema | Planner/filter/output fields | 过滤失效或字段缺失 |
| Collection name/schema | build/config | retrieval/server | 查错库或启动失败 |
| Chunk ID | parser/build | dedup/Eval/SQLite | 来源和黄金标签断裂 |
| `combined_text` | index input | Rerank/context | 搜索目标与展示不一致 |

因此换 Embedding、改 Sparse 或改 schema 通常不是在线热配置，而是数据迁移。先检查 schema、备份/源 Excel，再使用 `migrate_milvus_schema.py` 或 `rebuild_all_knowledge_bases.py` 的确认保护流程；详细命令见 [Data Pipeline README](app/data_pipeline/README.md)。

## 39. Eval：怎么知道 RAG 有没有变好

当前 Eval 目标是 **Retrieval Evaluation**。Dataset 每条包含 Query、一个或多个 `expected_chunk_ids` 和展示用 metadata。执行过程：

```text
Eval Case
  → Embedding
  → retrieve_with_fallback()
  → Rerank
  → ranked chunk_ids
  → first expected ID rank
  → Hit@1 / Hit@3 / Hit@5 + bad cases
```

CLI、Web SSE 和状态恢复共享 `app/eval/run_retrieval_eval.py`。Web 任务与浏览器连接解耦：后台 task 继续运行，进度和最终结果写 SQLite；取消是协作式的，只在当前同步 Query 完成后停止。

重要事实：Eval 的 `retrieve_and_rerank()` 自己构造无 filters 的简单 Plan，`semantic_query` 和 `keyword_query` 都等于原 Query；因此 Recall Channels 会去重成 `kb_only`，且 Eval 不执行 Query Rewrite、Metadata Navigator 或 Query Planner。它复用了生产 Dense/Sparse Retrieval 和 Rerank，但**没有覆盖完整 Stateful RAG 前处理/规划链路**。

## 40. Hit@1 / Hit@3 / Hit@5 怎么理解

- 标准答案 Chunk 排第 1：Hit@1、Hit@3、Hit@5 都是 1。
- 标准答案排第 2：Hit@1 是 0，Hit@3 和 Hit@5 是 1。
- 标准答案排第 4：只有 Hit@5 是 1。
- 前 5 没出现：三者都是 0，进入 bad cases。

汇总值是所有 Query 的命中比例。多个 `expected_chunk_ids` 表示任意一个相关 Chunk 首次出现就算命中；同时记录 first relevant rank、matched ID、Rerank score 和 Top 结果摘要。

## 41. Eval 当前没有覆盖什么

当前不评估：

- 最终报告的医学正确性、完整性或文风；
- Report LLM 是否忠实于 RAG Context；
- Citation 正确性（报告本身也无 Citation 字段）；
- Main Agent 的 Tool 选择；
- Entity extraction、Context Resolution、Rewrite、Planning/Metadata Filter；
- Stateful 多轮、Last Report 编辑/润色；
- Frontend/SSE 的端到端体验；
- 临床有效性、安全性与人工医生一致性。

所以 Hit@K 上升只能说明当前 Dataset 上的候选+Rerank 更容易找回黄金 Chunk，不能等价为整体产品质量上升。

## 42. SSE：前后端怎么共享执行过程

Backend Chat Event 按职责分组：

- 状态：`status`、`context_usage`、`done`；
- 输入/上下文：`entity_update`、`intent_switch`、`context_resolve`、`query_rewrite`、`memory_retrieval`；
- Agent：`intent`、`tool_executed`、`cache_hit`；
- Retrieval：`search`、`query_plan`、`recall`、`rerank`；
- 结果：`reasoning`、`ambiguous`、`report`、`message`、`error`；
- LLM 增量能力：`token`。

Frontend 实际处理 §6.5 列出的分支；差异是：

| Event | Backend | Frontend |
| --- | --- | --- |
| `query_plan` | `search_reports()` 发送 | 未处理 |
| `cache_hit` | 歧义选择/匹配时发送 | 未处理 |
| `search_result` | 主路径未发送 | 保留兼容 handler |
| `token` | `chat_stream` 支持 | 支持追加文本 |
| `done` | Worker finally 发送 | 完成 UI 并显示耗时 |

`reasoning` 来自 Report JSON 的可选字段，会单独显示在 Thinking；正式 `report` 内容不会包含 reasoning。Eval/自动生成使用独立的 `start/progress/warning/done/cancelled/error` 集合。

## 43. Streaming 并不等于所有 Token 都实时输出

SSE Streaming 是 HTTP 响应可以逐事件发送；LLM Token Streaming 是模型生成的每个 delta 被立即转发。两者不是同一件事。

当前 `chat_stream()` 确实以 `stream=True` 调模型，也能在 `_emit` 存在时发 `token`。但三个 Tool handler 都显式传 `_emit=None`；Main Agent 和 Optional Final LLM 使用非流式 `chat_with_tools()`。因此主路径通常流式展示 status/search/rerank/report/done，而报告正文一次性出现在 `report`，不是逐 token。

前端保留 `token` 分支是能力兼容，不能据此声称当前报告正文实时流出。

## 44. 核心数据结构

| 结构 | 核心含义 | 谁创建 → 谁使用 |
| --- | --- | --- |
| `ChatRequest` | Query、Session、诊断选择、KB 范围 | Frontend → Worker |
| Entity Slots | modality/body part/history/diagnosis/intent | EntityTracker/SQLite → Prompt/Planner/Tools |
| `QueryPlan` | semantic/keyword/diagnosis Query、filters、slot confidence | Planner → Retrieval/SSE |
| Tool Call | ID、name、arguments | Main LLM → Registry/Pipeline |
| `ToolResult` | content + `is_final` | Registry → Pipeline 终态决策 |
| Retrieval Candidate | IDs、metadata、text、recall/rerank fields | Milvus/Retrieval → Rerank/Tool/Frontend refs |
| Structured Report | 当前主格式 `results[{影像学表现,诊断意见}]`，可含 reasoning | Report/Edit/Refine LLM → Last Report/Display/API |
| KB Chunk | 来源、层级、findings/opinion、combined text、stable ID | Excel Pipeline → Markdown/SQLite/Milvus |
| Session State | Slots、Last Report、Selected KBs、Ambiguity 字段 | Worker/Pipeline ↔ SQLite |
| Eval Case | Query + expected IDs + metadata | JSON Dataset/UI → Eval runner |
| Eval Result | Hit rates、details、bad cases、运行 config | Eval runner → SQLite/SSE/UI |

Schema 不是集中定义：Gateway Pydantic、Worker Pydantic、Tool JSON Schema、Prompt JSON 格式、SQLite DDL、Milvus schema 和 Frontend payload 各自在不同文件，修改时必须按消费者检查。

## 45. 配置系统

`config.yml` 保存可提交的非敏感模型和检索选择；`.env`/进程环境保存 Secret 与覆盖值；`app/config.py` 缓存 YAML 并提供 getter。按“控制什么”理解：

### Chat Model

控制 Main Agent、报告生成、编辑和润色。Client 每次通过 `get_llm_client()` 新建，但 `llm_client.CHAT_MODEL` 在模块导入时缓存。

### Rewrite / Planning Model

控制 Query Rewrite 和 Query Planner，可由 `active_models.rewrite_llm` 指定，否则回退 active Chat LLM。两个模块各自直接创建网络请求，API Key/Prompt 有模块级缓存。

### Embedding

控制 RAG Query、Memory 消息与离线建库。模型、dimension 和 Milvus schema 必须一致；`llm_client.EMBED_MODEL` 在导入时缓存，建库端则动态 getter。

### Rerank

控制第二阶段排序。URL 可自动补 `/rerank`，但 model/API key 在 `rag/rerank.py` 导入时缓存。

### Milvus

URI/token/collection 决定在线与离线向量存储。`server.DB_PATH/COLLECTION_NAME`、`retrieval.COLLECTION_NAME` 等存在导入时常量。

### Retrieval

`rag_top_k` 控制 RRF Candidate 池，`rerank_top_k` 控制最终参考数。Pipeline 默认参数在导入时缓存；Stateless 显式请求可覆盖。

### Memory

`max_rounds` 控制每次恢复后的 STM 窗口。`decay_factor` 有 getter 和配置，但源码没有消费者，当前不产生行为。

### Gateway

`API_SECRET_KEY` 控制 Public Bearer；`WORKER_BASE_URL/TIMEOUT` 控制上游；`ALLOW_LEGACY_PUBLIC_PROXY` 控制 Legacy 来源限制。这些在 Gateway 进程启动时读取。

`POST /api/config` 会写 YAML 并 `reload_config()`，但多个模块已经缓存常量，所以“保存并生效”不是全量热重载保证。修改 active model、collection、top-k 后应重启 Worker；Gateway 环境变化需要重启 Gateway。

## 46. Online Data Flow

```text
Browser query + session_id + selected_kbs
  │
  ▼
Gateway legacy proxy ── source policy ──► Worker /api/chat
  │                                         │
  │                                         ├─ SQLite: restore turns/state/KB records
  │                                         └─ MilvusClient
  │                                                  │
  │                                    Entity / Intent / Resolve / Rewrite
  │                                                  │
  │                              STM ── Embedding ── related history
  │                                                  │
  │                               tool_orchestrator + Main LLM
  │                                   ┌──────────────┼──────────────┐
  │                                   │              │              │
  │                              rag_search       edit          refine
  │                                   │              └──── Last Report
  │                    QueryPlan + Metadata Tree
  │                                   │
  │                    Query Embedding + Sparse Query
  │                                   │
  │                    Milvus Dense/Sparse × Channels
  │                                   │
  │                              RRF + Dedup
  │                                   │
  │                         External Rerank Top-N
  │                                   │
  │                      References + structure.md
  │                                   │
  │                           Report Chat LLM
  │                                   │
  │                         Structured Report JSON
  │                                   │
  │                SQLite persist ◄───┴───► SSE report/done
  │                                             │
  └──────────────── response stream ────────────┘
                                                ▼
                                  Frontend DOM + LocalStorage snapshot
```

## 47. Offline Knowledge Base Flow

```text
Human-maintained .xlsx
  → upload: report_template/{kb_id}/source.xlsx
  → SQLite knowledge_bases(uploaded)
  → process every Sheet/Row
  → normalize + deduplicate
  → combined_text + stable chunk_id
  ├─ Markdown Front Matter + report body
  ├─ SQLite knowledge_base_chunks
  └─ Metadata Tree → SQLite knowledge_bases.metadata_tree
          │
          ├─ combined_text → Embedding API → dense_vector
          └─ combined_text → BLAKE2 lexical hash → sparse_vector
                                             │
                         shared Milvus Collection, filtered by kb_id
                                             │
                               KB status active / progress 100
```

离线产物不是平行的三份独立数据，而是同一 Chunk 的三种用途：Markdown 便于审计与重建，SQLite 便于业务状态/原文查询，Milvus 便于在线检索。

## 48. Session State Flow

```text
Browser
  SESSION_ID ─────────────────────────────────────────┐
  conversationId / rendered messages / selected KBs  │ LocalStorage only
                                                     │
                                                     ▼
Worker request runtime                         SQLite SessionStore
  STM ◄────────── restore Turns ─────────────── turns
  EntityTracker ◄── entity_slots ────────────── session_state
  last_report ◄──── last_report ─────────────── session_state
  selected_kbs ◄── request + persisted copy ─── session_state
  last_ambiguity ◄─ process-global dict          field exists but unused by Worker
       │
       ▼
  Pipeline mutates runtime objects
       │
       └──── persist Turn / Slots / Report / KBs / Thinking ──► SQLite
```

记忆方式：Browser 保存“怎么显示”，Runtime 保存“本轮怎么工作”，SQLite 保存“下轮怎么恢复”。歧义缓存是当前例外：它只在 Worker 进程内跨请求存活。

## 49. 模块依赖关系

```text
front/* ─HTTP/SSE─► gateway.py ─HTTP─► chat/server.py
                                         │
                                         ├─► chat/pipeline.py
                                         │     ├─► prompt/
                                         │     ├─► memory/
                                         │     ├─► tools/
                                         │     └─► rag/
                                         │
                                         ├─► memory/session_store.py ─► SQLite
                                         ├─► data_pipeline/ ─► Markdown/SQLite/Milvus
                                         └─► eval/ ─► rag/retrieval + llm_client + SQLite

tools/rag_tool.py ─► pipeline.search_reports ─► rag/planner/navigation/retrieval
                                             ├─► Embedding
                                             └─► Rerank

data_pipeline/build_vector_db.py ─► rag/sparse_vector.py
eval/run_retrieval_eval.py ─► rag/retrieval.py + chat/llm_client.py

config.py ◄──────── almost every backend module
external services ◄─ llm_client + query_rewrite + query_planner + rerank + build pipeline
```

架构方向应保持：Gateway 不 import Worker；RAG 不依赖 Session；离线和在线只通过 schema/config/shared sparse function 耦合；Frontend 不直接访问 SQLite/Milvus。

## 50. 核心文件地图

| 文件 | 为什么重要 | 什么时候打开 |
| --- | --- | --- |
| `README.md` | 运行与集成入口 | 启动、部署、API 示例 |
| `AGENTS.md` | 全局代码导航规则 | 任何代码任务开始时 |
| `python_start.py` | 本地 Worker/Gateway 启动拓扑 | 本地启动问题 |
| `compose.yaml` | Docker 网络、端口和 volume | 部署与数据持久化 |
| `config.yml` | Active 模型、检索和 Milvus 默认值 | 行为/环境配置 |
| `front/index.html` | 五个页面和所有固定 DOM ID | 页面结构/表单 |
| `front/app.js` | Frontend 状态、API、SSE、LocalStorage | 所有前端行为 |
| `front/style.css` | 全部视觉和状态 class | 样式/响应式 |
| `app/gateway.py` | Public auth 与 stream proxy | 外部 API、安全边界 |
| `app/config.py` | 配置解析、getter、client factory | 新配置或热重载 |
| `app/chat/server.py` | Worker 路由、Session、SSE、KB、Eval | 后端入口/跨模块问题 |
| `app/chat/pipeline.py` | 一轮 Agent 主编排 | Tool 决策和请求生命周期 |
| `app/chat/llm_client.py` | Chat/Embedding/Rerank 上层调用 | 模型调用、streaming |
| `app/chat/schemas.py` | Worker Pydantic contract | Stateful/管理 API 字段 |
| `app/chat/utils.py` | LLM JSON 展示兼容 | 报告渲染格式 |
| `app/tools/registry.py` | Tool 注册、执行、Final 语义 | 新 Tool/执行机制 |
| `app/tools/rag_tool.py` | 新报告、歧义、Report LLM | 报告生成主业务 |
| `app/tools/edit_tool.py` | 精确编辑 Last Report | 编辑行为/Schema |
| `app/tools/refine_tool.py` | 风格重写 Last Report | 润色行为/Schema |
| `app/rag/query_rewrite.py` | 标准化与有条件改写 | Stateful Query 预处理 |
| `app/rag/query_planner.py` | 自然语言到合法 QueryPlan | Filter/Query 拆分 |
| `app/rag/metadata_navigator.py` | 合并/验证 Metadata Tree | 目录候选与约束 |
| `app/rag/retrieval.py` | Channels、Dense/Sparse、RRF、Dedup | 第一阶段检索 |
| `app/rag/sparse_vector.py` | 离线/在线共享词面向量 | Sparse 算法变更 |
| `app/rag/rerank.py` | Cross-Encoder API | 第二阶段排序 |
| `app/prompt/builder.py` | Entity/Template/Report 拼装 | Prompt 上下文顺序 |
| `app/prompt/tool_orchestrator.md` | Main Agent 决策规则 | Tool 选择错误 |
| `app/prompt/structure.md` | 当前报告 Schema 和生成规则 | 报告结构/内容 |
| `app/prompt/edit.md` / `refine.md` | 编辑与润色约束 | Last Report 变换 |
| `app/memory/short_term.py` | STM window 与摘要 | 多轮历史问题 |
| `app/memory/entity_tracker.py` | Slots、Intent、Context Resolution | 实体和指代问题 |
| `app/memory/retriever.py` | STM semantic selection | 历史相关性/Embedding |
| `app/memory/session_store.py` | SQLite 所有共享表 | Session/KB/Eval 持久化 |
| `app/data_pipeline/process_reports.py` | Excel → Chunk/Markdown | Excel schema/切片 |
| `app/data_pipeline/build_vector_db.py` | Milvus schema 与写入 | Embedding/schema/rebuild |
| `app/data_pipeline/extract_metadata.py` | Markdown → Metadata Tree | Planner 候选来源 |
| `app/data_pipeline/chunk_id.py` | 全生命周期稳定 ID | 任何 identity 变更 |
| `app/eval/run_retrieval_eval.py` | Dataset、Runner、SSE、结果 | Retrieval 评测 |
| `app/eval/metrics.py` | Hit@K 定义 | 新指标/聚合 |
| `app/test/test_rag_planning.py` | Planner/Channels 的纯测试 | RAG 逻辑回归 |
| `app/test/test_knowledge_base_chunks.py` | SQLite Chunk/Excel parser 测试 | 建库数据变更 |

## 51. 修改入口：我想改 X 应该去哪

| 目标 | 先看这些文件（由近到远） |
| --- | --- |
| Agent 决策/调用次数 | `chat/pipeline.py`、`prompt/tool_orchestrator.md`、`tools/registry.py` |
| Tool 选择规则 | `prompt/tool_orchestrator.md`、三个 Tool Schema、`pipeline.py` |
| 新增 Tool | 新 `tools/*.py`、`tools/__init__.py`、`pipeline._build_tool_registry()`、orchestrator prompt |
| 报告结构 | `prompt/structure.md`、`tools/rag_tool.py`、`chat/utils.py`、edit/refine prompts、Frontend |
| 报告生成 Prompt | `prompt/structure.md`、`prompt/builder.py`、`tools/rag_tool.py` |
| 报告编辑 | `tools/edit_tool.py`、`prompt/edit.md`、Last Report 保存逻辑 |
| 报告润色 | `tools/refine_tool.py`、`prompt/refine.md`、Entity Context |
| Query Rewrite | `rag/query_rewrite.py`、`prompt/rewrite.md`、rewrite config |
| Query Planning | `rag/query_planner.py`、`metadata_navigator.py`、planning tests |
| Metadata Filter | `metadata_navigator.py`、`query_planner.py`、`retrieval.build_filter_expression()` |
| Dense Retrieval | `chat/llm_client.py`、`rag/retrieval.py`、`data_pipeline/build_vector_db.py`、config |
| Sparse Retrieval | `rag/sparse_vector.py`、`rag/retrieval.py`、`build_vector_db.py`；然后全量重建 |
| Recall Channel | `rag/retrieval.py`、`pipeline.search_reports()` SSE、planning tests |
| RRF | `rag/retrieval.py` 中两处 `RRFRanker()` 和候选限制 |
| Rerank | `rag/rerank.py`、`llm_client.rerank_with_retry()`、`pipeline.search_reports()`、Eval |
| Memory/STM | `memory/short_term.py`、`memory/retriever.py`、`pipeline.py` |
| Entity/Intent | `memory/entity_tracker.py`、`prompt/entity_extraction.md`、Pipeline gates |
| Session | `memory/session_store.py`、`server._get_or_create_session()`、Chat persist block |
| SSE | Backend emit site、`server.event_stream()`、`front/app.js::handleStreamEvent()` |
| Gateway API | `gateway.py` payload/auth/proxy、Worker `/internal/*` models/routes |
| Stateful API | `chat/schemas.py::ChatRequest`、`server.py::chat`、`front/app.js::sendMessage` |
| Stateless API | `gateway.py` models、`server.py` 的 RAG/Report request 与 internal handlers |
| Excel Schema | `process_reports.FIELD_MAPPING/CONTENT_FIELDS`、Markdown parser、Data Pipeline README |
| Chunk 组成/ID | `process_reports.py`、`chunk_id.py`、SessionStore、Milvus/Eval |
| Milvus Schema | `build_vector_db.py`、migration/rebuild scripts、`rag/retrieval.py`、config |
| Eval Dataset/流程 | `eval/run_retrieval_eval.py`、`generate_test_cases.py`、`metrics.py`、Eval UI |
| Frontend Chat | `front/index.html` Agent DOM、`front/app.js` send/parser/render、`front/style.css` |
| KB UI | `front/app.js` KB functions、`index.html` KB page、Worker KB routes |
| Eval UI | `front/app.js` Eval functions/SSE、`index.html` Eval page、Worker Eval routes |

完成代码修改后，再按最近的 `AGENTS.md` 检查依赖、Public API、Testing 和导航是否失效。

## 52. Design Rationale

### 为什么 Gateway / Worker 分离

事实：Gateway 只鉴权和代理，Worker 不公开 Docker host port。`Inference:` 这样缩小公开进程的依赖与攻击面，也允许独立调整代理超时和业务资源。

### 为什么 RAG 是 Tool

事实：并非所有输入需要知识库。`Inference:` 把 RAG 设为 Tool 可避免普通回复、精确编辑和润色承担检索成本与模板噪声。

### 为什么 Report Tool 使用 `is_final`

事实：报告 Tool 已经调用专用 Prompt/LLM 并产生 JSON。直接返回可避免 Main LLM 二次总结引入幻觉、丢字段或改变术语。

### 为什么 Memory 和 RAG 分离

事实：Memory 按 Session 隔离，RAG 按 KB 共享。混合存储会模糊用户上下文与医学知识的来源、生命周期和隐私边界。

### 为什么使用 Metadata Navigator

事实：Planner LLM 只能从实际树候选中选择。`Inference:` 这是以少量约束牺牲自由度，换取可执行且与真实模板层级一致的 Filter。

### 为什么 Dense + Sparse

Dense 处理语义改写，Sparse 保住精确词面。两者互补，降低单一向量表示对医学缩写、疾病名和数字不敏感的风险。

### 为什么多个 Recall Channel

Strict 提供精度，modality/kb-only 抵御 Planner 或数据层级错误。并行让宽松结果不会因 strict“看似够数”而被完全排除。

### 为什么 RRF 后还有 Rerank

RRF 只融合多个 ANN 排名，不深入联合理解 Query 与全文；Cross-Encoder 在较小候选池中做更贵、更细的相关性判断。

### 为什么多 KB 共用 Collection

事实：所有实体带 `kb_id`，检索强制 KB filter。`Inference:` 共享 schema/index 降低动态创建 Collection 的管理成本，并天然支持一次选择多个 KB。

### 为什么 Stable Chunk ID 很重要

它是人类原文、向量实体和黄金标签之间唯一稳定连接。没有它，重建 Collection 后 Milvus auto ID 会变化，Eval 与 UI 来源无法对齐。

### 为什么 Stateful 和 Stateless API 同时存在

Stateful 支持自然多轮、编辑和歧义；Stateless 便于外部系统以明确请求获取可重复结果，不引入服务器会话生命周期。

## 53. 当前架构最值得注意的问题

以下均来自当前源码审计；只记录，不在本文档任务中修复。

### High Priority

1. **Gateway 有可预测默认 Token**

   未设置 `API_SECRET_KEY` 时使用 `sk-my-default-secret`。任何公开部署若漏配环境变量，Bearer 形同已知口令。应改为启动失败或明确仅开发模式允许。

2. **Stateful Chat 的同步 I/O 阻塞 Event Loop**

   `run_pipeline()` 是 async，但内部同步执行 Rewrite `requests`、Planning `OpenAI`、多次 Embedding、Milvus Hybrid Search 和 Rerank。创建 asyncio background task 并不会把这些调用移出 event loop；并发 Chat、SSE 和其他请求可能被长调用阻塞。Stateless Search 已使用 `asyncio.to_thread()`，Stateful 尚未统一。

3. **Entity LLM 提取的 async/sync 接口不匹配**

   Worker 把 async `chat_stream` 注入同步 `EntityTracker._extract_llm()`；代码不 await coroutine，就调用 `.strip()`，捕获异常后回退规则，并可能产生“coroutine was never awaited”。因此文档声称的在线 LLM 实体抽取实际未工作。

4. **同一 Session 没有请求级并发控制**

   每个请求独立从 SQLite 重建状态、计算相同 `turn_index`、再插入和覆盖 state。两个并发请求可发生 Lost Update、重复顺序号、Last Report/Slots 后写覆盖前写。SQLite WAL 只保证数据库事务，不保证业务 Session 序列化。

5. **主 Report Schema 与 edit/refine Prompt 漂移**

   `structure.md` 输出 `results[]`；`edit.md/refine.md` 仍要求旧版顶层 `影像学表现/诊断意见` 字典，handler 的 Key 保护也只检查旧格式。编辑/润色可能把报告转换为另一种 Schema，或无法执行预期结构保护。

6. **Docker Volume 与当前 Data Pipeline 路径不一致**

   Worker 实际上传到 `/app/app/data_pipeline/report_template` 并生成 `output_markdown`；Compose 挂载的是 `./app/report_template:/app/app/report_template` 和旧 `xlsx_slices`，未挂载实际上传/Markdown目录。容器重建可能丢失源 Excel/Markdown，除非部署环境另有外部保护。

7. **Worker 直连绕过鉴权**

   这是设计边界而非代码漏洞：Worker `/api/*` 和 `/internal/*` 自身无 Bearer。任何网络或 Compose 变更若公开 `:8001`，将绕过 Gateway。

### Medium Priority

1. **MilvusClient 生命周期不完整**：Chat、`/api/info`、`/api/memory` 每次 `_get_or_create_session()` 新建 client，未见 finally close；长时间运行可能积累连接资源。
2. **配置热重载不完整**：YAML reload 后，Chat/Embedding model、Rerank model/key、Pipeline Top-K、collection 等导入时常量仍旧；UI 的“已生效”文案过强。
3. **歧义持久化名实不符**：SQLite 有 `last_ambiguity`，但 Worker save/load 不使用它；实际全局 dict 重启丢失，clear/delete 也未清理对应 cache。
4. **Memory 重复 Embedding**：`MemoryRetriever` 每轮新建，“增量索引”无法跨请求复用；恢复后的每条 STM 消息每轮都重新 Embedding。
5. **STM Summary 未进入主 Pipeline Prompt**：STM 会生成规则摘要，Memory API 可显示，但 Pipeline 只用 `get_history()`，不调用 `build_messages()`/`get_summaries()` 注入已淘汰内容。
6. **Eval 覆盖面易被高估**：当前只评估无 metadata filter 的 kb-only Dense/Sparse + Rerank，不覆盖 Rewrite/Planner/Navigator/三通道选择或 Agent。
7. **网络 Client 分散**：除 `llm_client.py` 外，Rewrite、Planner、Rerank、建库 Embedding、Server model test 各自直接使用 requests/OpenAI，超时、重试、缓存和 tracing 行为不统一。
8. **Schema 多处重复**：Gateway 与 Worker Stateless Pydantic 分开定义，Tool Schema、Prompt Schema、Display 兼容、SQLite/Milvus字段也分散，容易局部修改后漂移。
9. **多 Tool Final 语义模糊**：Pipeline 执行一个响应中的全部 Tool Calls，但用 `any_final` 结束并返回遇到的首个可展示 Final；混合 Final/错误/非 Final 时没有明确聚合策略。
10. **前后端事件契约不完全对齐**：`query_plan/cache_hit` 无 UI，`search_result` 无主发送点；Recall 的 legacy count 字段不能准确表达各 Channel 贡献。

### Low Priority

1. Frontend 是大型单文件/全局状态，没有自动化测试、Router 或构建期类型检查。
2. Chat 与 Eval 维护两套 SSE parser，支持的 frame 语义不同。
3. `decay_factor` 配置和 getter 当前未使用。
4. 多个 Prompt 文件存在但主链路未引用，容易让维护者误判实际行为。
5. Context usage 使用 `chars/2` 和固定 4096 窗口估算，不代表真实 tokenizer/model context。
6. Memory Embedding 失败时硬编码 1024 维零向量；若配置维度变化，降级向量可能与成功向量不兼容。
7. Frontend LocalStorage 持久化渲染后的医学文本，隐私和数据保留策略需部署方明确。
8. `config.yml` 包含具体环境服务地址；未覆盖环境变量时可能意外连接到非预期远程服务。

## 54. 测试体系

| 层 | 当前验证 | 明显缺口 |
| --- | --- | --- |
| Agent | `test_memory.py` 间接测 Prompt/Intent；手工 `/api/chat` | 无 Tool 决策、multi-tool、Final 分支自动化 |
| RAG | `test_rag_planning.py` 测 Planner、channels、dedup、rewrite fallback | 无真实多通道贡献、RRF 参数、Rerank fallback 单测 |
| Memory | `test_memory.py` 自定义/pytest-compatible 测规则实体、switch、resolve、retriever | 无在线 async Entity LLM、SQLite restore 并发测试 |
| Tools | 主要靠集成/手工 | 三个 handler、Schema 保持、歧义缓存缺单测 |
| Data Pipeline | `test_knowledge_base_chunks.py` 测 Excel parser 和 SQLite chunks | 无完整 Markdown→Embedding→Milvus 事务/失败恢复测试 |
| API | `tests_api/` 多为外部模型连通性脚本 | 无 FastAPI TestClient、Gateway auth/proxy、SSE contract 测试 |
| Eval | Eval runner 自身及 CLI validate | metrics 有简单纯函数但无专门 test；不测 Planner/Agent |
| Frontend | 人工浏览器检查 | 无 unit、E2E、lint、类型检查 |

纯逻辑优先运行：

```powershell
pytest app/test/test_rag_planning.py -v
pytest app/test/test_knowledge_base_chunks.py -v
python -m app.test.test_memory
```

`test_rerank_flow.py` 在模块顶层就连接 Milvus/Embedding/Rerank，且使用旧 collection/path 常量，不是隔离测试。完整 `pytest app/test/` 会触发真实外部依赖的风险，运行前先查看测试代码。

## 55. 调试指南

### Chat 不工作

先看 Gateway/Worker health 和浏览器 Network；确认 KB Active 且 request 有 `selected_kbs`；再看 `server.py::chat` 日志、SSE 是否收到 error/done、模型/Milvus启动校验。

### Agent 选错 Tool

看 `tool_orchestrator.md`、三个 Tool Schema 描述、Pipeline 注入的 Entity/STM/Last Report，以及 `chat_with_tools` 返回的 tool calls。确认 Last Report 是否真的持久化。

### RAG 搜不到

依次看 QueryPlan event/log、selected KB、Metadata Tree、filter expression、Embedding维度、Collection load、Candidate count、Rerank。用 `verify_chunk_id_retrieval.py` 做三端身份检查。

### Metadata Filter 错误

从 SQLite `knowledge_bases.metadata_tree` 和 Markdown Front Matter 开始，检查 Navigator candidates、Planner slots/source，再检查 `build_filter_expression()`。不要先手改全局 metadata JSON。

### Rerank 排序异常

确认 Candidate 文本和顺序、Rerank URL/model/key、返回 index/score、`rerank_top_k`，比较 RRF 顺序和 Rerank 后顺序；基础设施错误在 Chat 会降级，在 Eval 会终止。

### 报告结构错

先区分是 `structure.md` 新报告，还是 `edit.md/refine.md` 旧 Schema；检查 `extract_json()`、Tool `_is_final`、`json_to_display()` 和 Last Report 原始 JSON，不要只看前端 Markdown。

### 多轮上下文错误

查 SQLite turns/session_state，再看 `_get_or_create_session()` 恢复、Entity Slots、Intent switch、STM max rounds、Memory Retrieval event。并发复现时考虑同 Session race。

### KB 数据不对

从源 Excel列/Sheet/行开始，检查 parser errors、Markdown Front Matter/正文、SQLite chunk 数、Metadata Tree、Milvus `kb_id/chunk_id`。避免只看 UI progress。

### SSE 前端没反应

对照 Backend emit type 与 `handleStreamEvent()` switch；确认 frame 是单行 `data: `、JSON payload字段匹配、最终有 `done/[DONE]`；检查 60 秒前端超时和 120 秒 Worker queue timeout。

### Eval 结果下降

先固定 Dataset、Active KB 集、Embedding/Rerank模型和 Top-K；检查黄金 ID 仍存在；查看 bad cases 的 first rank/score。记住当前 Eval 不测 Planner，不能用它直接判断 metadata 规划退化。

## 56. 项目的“最重要十个概念”

1. 系统生成医学影像**文本**，不分析图像像素。
2. Agent 不等于 RAG；Agent 决定直接回复或调用哪个 Tool。
3. RAG 是 `rag_search` Tool 的内部能力；edit/refine 不重新检索。
4. Memory 是用户 Session，RAG 是共享 KB，来源和生命周期不同。
5. Query Planner 把自然语言拆成 semantic、keyword 和 metadata filters。
6. Metadata Navigator 只约束合法目录，不负责召回。
7. Dense + Sparse × 并行 channels 经 RRF 是第一阶段，Cross-Encoder Rerank 是第二阶段。
8. `is_final` 让报告 Tool 直接结束，避免主 LLM 二次改写。
9. Stable Chunk ID 串联 Markdown、SQLite、Milvus 和 Eval。
10. Data Pipeline 与 Online Retrieval 通过模型、维度、Sparse、schema、metadata 和 ID 强耦合。

## 57. 5-Minute Refresh

**项目一句话**：这是一个文本型医学影像报告 Agent；主模型在直接回复、RAG 新报告、编辑旧报告、润色旧报告之间选择。

**主架构**：Browser → Gateway（公开鉴权/代理）→ Worker（API/SSE/Session）→ Pipeline → Memory + Prompt + Tools；RAG 访问 Embedding/Milvus/Rerank，SessionStore 访问 SQLite。

**一次请求**：恢复 Session → Entity/Intent/Resolve → 可选 Rewrite → 相关 STM → orchestrator LLM → 一个 Tool 批次 → Final Tool 或一次最终 LLM → 保存 Last Report/Slots/Turn → SSE。

**三个 Tool**：`rag_search` 新报告；`edit_report` 精确改事实；`refine_report` 改表达。三个成功结果通常都是 Final。

**RAG**：Plan → 合法 Metadata → Query Embedding + Hash Sparse → strict/modality/kb-only 并行 → RRF → chunk ID dedup → Rerank → references → `structure.md`。

**Memory 四组件**：STM 保存窗口；EntityTracker 保存结构化语境；MemoryRetriever选相关历史；SessionStore跨请求恢复。

**Data Pipeline**：Excel Row → Chunk/Markdown → SQLite + Metadata Tree → Dense/Sparse → shared Milvus，`kb_id` 隔离，`chunk_id` 对齐。

**Eval**：Query + expected chunk IDs → kb-only Hybrid + Rerank → Hit@1/3/5；不评估报告，也不覆盖 Planner。

**修改入口**：决策看 Pipeline/orchestrator；报告看 Tools/Prompts；检索看 `rag/`；状态看 `memory/`+server；数据看 `data_pipeline/`；UI 看三个 front 文件。

**关键风险**：默认 Gateway Token、Stateful 同步 I/O、Entity async mismatch、同 Session race、Report Prompt schema 漂移、Docker volume 路径。

## 58. 30-Minute Review Path

不要从头机械读到尾，按目标顺序：

1. 2 分钟：§1–3，恢复业务边界和架构。
2. 5 分钟：§4–5，追踪 Stateful/Stateless 生命周期。
3. 4 分钟：§9–13，恢复 Agent、Tool 和 Final Result。
4. 7 分钟：§14–25，恢复 Planner、Metadata、Hybrid、RRF、Rerank 和报告上下文。
5. 4 分钟：§26–31，恢复 Prompt、Memory、Session、SQLite。
6. 3 分钟：§32–41，连接离线建库和 Eval。
7. 2 分钟：§6、§42–43，恢复 Frontend/SSE。
8. 2 分钟：§51、§55，恢复修改和调试入口。
9. 1 分钟：§53，查看当前高风险是否已修复。

若只准备改一个模块，再转到对应 README 与最近的 `AGENTS.md`，不要用本文件替代源码阅读。

## 59. Glossary

| 术语 | 本项目中的意义 |
| --- | --- |
| Gateway | 公开 FastAPI 边界，Bearer/来源检查并代理 Worker |
| Worker | 承载前端、业务 API、Agent、KB、Eval 的 FastAPI 进程 |
| Pipeline | `run_pipeline()` 的一轮 Agent 编排，不是无限循环 |
| Tool | 主 LLM 可选择的业务动作，具有 JSON Schema 和 handler |
| RAG | 从共享医学模板 KB 检索参考并辅助生成报告的链路 |
| QueryPlan | Query 的语义文本、词面文本、合法 filters 和 slot provenance |
| Metadata Navigator | 合并真实 KB 层级并限制 Planner 候选的验证器 |
| Recall Channel | strict/modality/kb-only 的一个标量过滤范围 |
| RRF | 用排名位置融合 Dense/Sparse/Channels 的第一阶段 ranker |
| Rerank | 对 Query 和候选全文做第二阶段 Cross-Encoder 排序 |
| STM | 当前 Session 最近 `max_rounds` 轮的运行时历史 |
| Entity | modality/body part/history/diagnosis/intent 结构化槽位 |
| Session | 由 `session_id` 标识、可从 SQLite 恢复的多轮状态 |
| Last Report | 最近一次 Final 报告原始 JSON，供编辑/润色/合并 |
| Chunk | 一个接受的 Excel Row 派生的主要可检索报告单元 |
| Stable Chunk ID | KB、文件名和 combined text 的确定性 32 位 MD5 |
| Knowledge Base | 一个上传 Excel 对应的逻辑数据域，以 `kb_id` 隔离 |
| Retrieval Eval | 用 expected chunk IDs 和 Hit@K 衡量检索排名的评测 |

## 60. Important Unknowns

- **Unknown:** 实际生产是否始终覆盖 `API_SECRET_KEY`，源码无法证明部署环境值。
- **Unknown:** 真实并发量、延迟目标、SLA 和 Session 同时请求概率没有仓库内规范。
- **Unknown:** 当前外部 Chat/Embedding/Rerank/Milvus 服务的可用性、版本和 Tool Calling兼容性；本次未联网调用验证。
- **Unknown:** Docker 部署是否通过 Compose 之外的 volume/备份补偿了源 Excel 与 Markdown 挂载路径。
- **Unknown:** `report_generation.md`、`multi_disease.md` 和 ReAct prompts 是否计划未来恢复使用；当前源码主路径未引用。
- **Needs verification:** 医疗文本在浏览器 LocalStorage、日志、LangSmith 和外部模型服务之间的合规/脱敏策略未在仓库形成完整事实。
- **Needs verification:** Main LLM 一次返回多个 Tool Calls 是否是预期支持场景；代码能执行，但产品语义未定义。
- **Needs verification:** Edit/Refine 是否在真实模型上稳定保持 `results[]`，Prompt 与 handler 当前没有保证。

### Documentation Conflicts Found During Verification

- `app/chat/AGENTS.md` 声称 LLM 网络请求统一由 `llm_client.py` 封装；实际 Rewrite、Planning、Rerank、建库 Embedding 和 Model Test 都有直接客户端。
- `app/chat/AGENTS.md` 把 `RAGSearchRequest/ReportGenerateRequest` 列在 `chat/schemas.py`；实际定义在 `chat/server.py`。
- `app/tools/AGENTS.md` 与 `app/prompt/AGENTS.md` 把 `report_generation.md/multi_disease.md` 描述为当前 `rag_search` 输入；实际 handler 只构建 `structure.md`。
- `app/rag/AGENTS.md` 把全局 `metadata.json` 描述为主要 Navigator 依赖；正式 Stateful/Stateless 服务传入 SQLite 中所选/Active KB 的 Metadata Trees，JSON 仅是直接构造 fallback。
- `app/rag/AGENTS.md` 固定描述特定 Rerank model；真实 model 由配置选择，不应视为代码常量语义。
- `app/data_pipeline/AGENTS.md` 中若干函数签名/metadata 消费关系与当前源码不完全一致；操作时以 `process_reports.py`、`extract_metadata.py` 和调用点为准。
- README 将 Eval 概括为“复用生产检索链”；更精确的事实是它复用 Dense/Sparse Retrieval 和 Rerank，但自己构造无 filter Plan，不执行 Rewrite/Planner/Navigator。
- `SessionStore` schema 和 Memory 文档暗示歧义可持久化；Worker 当前实际使用进程内 `_ambiguity_cache`，保存 state 时未传 `last_ambiguity`。

这些冲突未在本次任务中修改其他文档或代码。未来修复实现或文档后，应同步更新本节。
