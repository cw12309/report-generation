# AGENTS.md — Frontend Coding Agent Navigation

## Purpose

`front/` 是 Worker 直接托管的 Web UI，负责 Agent Chat、Knowledge Base、API 上线、Eval、Monitor、Config 和 SSE 展示。它不负责 Agent 决策、RAG、Milvus、Session 权威状态、Deployment 鉴权或公开 API 的执行；这些由 Backend 管理。人类使用与开发说明见 [README.md](README.md)，本文只为 Coding Agent 提供修改导航。文档与实现冲突时以源码为准。

## Frontend Architecture

```text
index.html ── fixed DOM / pages / forms / modals
     │
     ├── app.js ── page switching / Fetch / SSE / runtime state / LocalStorage
     │
     └── style.css ── layout / components / dynamic states / responsive rules
```

原生 HTML/CSS/经典 browser JavaScript，无 framework、Router、module、中央 Store、Build Step、lint、unit test 或自动化浏览器测试。`API_BASE = "/api"`；同源 Fetch、全局脚本变量、DOM、LocalStorage 和 `.page.active` 共同构成运行方式，页面切换不改变 URL。

## File Map

| File | Responsibility |
| --- | --- |
| `index.html` | 六个页面、一级导航、表单、Modal 和固定 DOM ID；引用静态资源版本 |
| `app.js` | 页面行为、Fetch/SSE、渲染、运行时状态和 LocalStorage |
| `style.css` | 全站布局、页面组件、状态 class、Modal、代码块和响应式样式 |
| `README.md` | 面向人类的 Frontend 使用与开发指南 |
| `AGENTS.md` | 本导航；Coding Agent 的页面、状态、契约和变更入口 |
| `docs/api-deployment-guide.md` | 面向调用方的 API 上线使用手册；页面内帮助窗口的单一内容源 |

## Page Map

| Page | DOM / important regions | Main JavaScript | APIs / state |
| --- | --- | --- | --- |
| Agent | `pageAgent`, `chatContainer`, `userInput`, `memoryPanel`, `chatHistoryList`, `agentKbModal` | `sendMessage()`, `handleStreamEvent()`, assistant render helpers, `initChatHistory()` | `/api/chat`, `/api/clear`, `/api/memory`, `/api/kbs`; `SESSION_ID`, processing/reader/controller, selected KBs, local conversation |
| Knowledge Base | `pageKB`, `manageKbList`, `kbDetailArea`, `kbChunkList`, `xlsxInput`, `kbLog` | `refreshKnowledgeBases()`, `renderManageKbList()`, `loadKnowledgeBaseChunks()`, build/upload/delete helpers | `/api/kbs`, `/api/kbs/{id}/chunks`, `/api/kb/*`; KB list, selected management KB, polling/abort state |
| API 上线 | `pageDeployments`, `apiDeploymentList`, create/detail/edit/secret/guide modals, KB selectors, `apiDemoCode` | Deployment load/render/actions, `loadApiDeploymentGuide()`, safe guide renderer, `bindApiDeploymentInteractions()` | `/api/deployments*`, `/api/kbs`, static Markdown guide; Deployment and guide-loading state |
| Eval | `pageEval`, `evalStatus`, `evalDetailsBody`, `evalCaseModal`, `evalAutoGenerateModal` | dataset CRUD/search, `consumeSSEStream()`, `runRetrievalEvaluation()`, `restoreEvaluationState()`, auto-generate helpers | `/api/eval/*`, `/api/kb/filter-options`, `/api/kb/chunks`; run/cancel/poll/filter/abort state |
| Monitor | `pageMonitor`, `monitorSessionsList`, `sessionDetailModal` | `loadSessionsList()`, `viewSessionDetail()`, `deleteSession()` | `/api/sessions`, `/api/memory`, `/api/session` |
| Config | `pageConfig`, `configNav`, `configForm`, `configGroupLlm/Retrieval/System`, `llmConfigPanel` | model card helpers, `loadConfig()`, `saveConfig()`, `testModelConnection()`, active-model helpers | `/api/config`, `/api/test-model`; `_configData`, `_currentLlmName`; Milvus URI 与 `.env` 的 `MILVUS_URI` 双向同步 |

## Navigation

Navigation order is `agent → kb → deployments → eval → monitor → config`, mapping to `pageAgent`, `pageKB`, `pageDeployments`, `pageEval`, `pageMonitor`, `pageConfig`. There is no `showPage()` function: a top-level `.sidebar-btn` click listener directly toggles `.active`.

Page-entry loads: Agent/KB call `refreshKnowledgeBases()`; Deployments calls `loadApiDeployments()`; Eval calls `restoreEvaluationState()`; Monitor calls `loadSessionsList()`; Config calls `loadConfig()`. Adding a page requires synchronizing the sidebar button, page container, listener `pageMap`, entry initialization, CSS and documentation. Do not bind the same handler again on each visit.

## DOM Ownership

Navigation owns `.sidebar`, `.sidebar-btn[data-page]` and `.page`. Agent owns chat/history/memory plus the document-level `agentKbModal`; KB owns `manageKbList` and detail/chunk regions; Deployment owns its list and five modals, including `apiDeploymentGuideModal`, with plaintext Key allowed only temporarily in `apiRevealedKey`; Eval owns metrics/table and two document-level modals; Monitor owns list/detail modal; Config owns only LLM/Retrieval/System groups.

## JavaScript Responsibility Map

- Navigation/Chat: sidebar listener + local `pageMap`; `sendMessage()` → line SSE → `handleStreamEvent()` → thinking/report helpers.
- KB/Deployment: `refreshKnowledgeBases()` and KB render/build/upload/delete/polling; Deployment load/render/actions, secret/example helpers, and the DOM-only Markdown guide renderer.
- Eval: dataset/filter helpers, `consumeSSEStream()`, run/cancel/restore/auto-generate; Config and Monitor each own their load/render/action groups.

## Frontend State Map

### Runtime JS State

- Chat/KB: `SESSION_ID`, `currentConversationId`, processing/reader/controller; `knowledgeBases`, selected IDs, management selection, polling/abort state.
- Deployment/Eval/Config: `_apiDeployments`, KB copies, selected/detail IDs, language/loading (no plaintext-Key variable); Eval flags/request IDs/timers/filters/controllers; `_configData`, `_currentLlmName`.

### LocalStorage

| Key | Meaning |
| --- | --- |
| `chat_history_list` | UI conversation index, title/time and associated backend `session_id` |
| `chat_messages_{conversationId}` | Rendered message `innerHTML` and thinking `outerHTML` snapshot |
| `selectedKnowledgeBaseIds` | Agent-selected KB ID list |

### Backend-owned State

Session Turns, Entity Slots, Last Report, Thinking, authoritative KB metadata/chunks, Deployment records/secrets/conversation mappings and Eval state/results belong to Backend/SQLite. Frontend copies are display/request state, never the source of truth.

## Session Model

- `SESSION_ID`: Browser Agent request identity sent to `/api/chat`; normally `web_*`, authoritative state lives in SQLite.
- `currentConversationId`: LocalStorage/UI history identity generated as `conv_*`; history records map it to `session_id`.
- Deployment `conversation_id`: Public API identity returned by the Deployment SSE `conversation` event; the management UI only documents it and does not run public chat.

These values are not interchangeable even when two use a `conv_` prefix. Internal Agent UI stays on `/api/chat`; public clients stay on `/api/v1/deployments/{deployment_id}/chat`.

## API Map

| Page | Method and path | Purpose |
| --- | --- | --- |
| Agent | `POST /api/chat`, `POST /api/clear`, `GET /api/memory`, `GET /api/kbs` | Stateful chat, reset, memory view and per-turn KB selection |
| KB | `GET /api/kbs`, `GET /api/kbs/{id}/chunks`, `POST /api/kb/upload`, `POST /api/kb/{id}/build`, `DELETE /api/kb/{id}` | KB list, chunks, upload, build and delete |
| KB maintenance | `POST /api/kb/build`, `POST /api/kb/extract-metadata` | Legacy/maintenance build and metadata actions still called by UI |
| Deployments | `GET/POST /api/deployments`, `GET/PATCH/DELETE /api/deployments/{id}` | List/create/detail/update/soft-delete |
| Deployments | `POST /api/deployments/{id}/enable`, `/disable`, `/rotate-key` | Status and one-time key rotation |
| Eval | `GET/DELETE /api/eval/dataset`, `DELETE /api/eval/dataset/{chunk_id}`, `POST /api/eval/add_case` | Dataset management |
| Eval | `POST /api/eval/run`, `/cancel`, `/auto_generate`; `GET /api/eval/status` | Run/cancel/restore and generate via SSE |
| Eval | `GET /api/kb/filter-options`, `GET /api/kb/chunks` | Cascading filters and candidate chunk search |
| Monitor | `GET /api/sessions`, `GET /api/memory`, `DELETE /api/session` | Session list/detail/delete |
| Config | `GET/POST /api/config`, `POST /api/test-model` | Masked config load/save and connection test |

## SSE Map

### Agent Chat SSE

`handleStreamEvent()` explicitly consumes `status`, `intent`, `query_rewrite`, `context_resolve`, `search`, `recall`, `rerank`, `search_result`, `token`, `reasoning`, `report`, `message`, `error`, `entity_update`, `intent_switch`, `memory_retrieval`, `tool_executed`, `ambiguous`, `context_usage`, `done`; the reader also recognizes `[DONE]`. Backend emits `query_plan` and `cache_hit`, but the current switch ignores them. `search_result` remains a compatibility branch without a current main-path sender.

### Deployment Public SSE

Management UI does not call the public stream; it only renders cURL/Python examples. Documented public events must remain `conversation`, `status`, `message`, `report`, `ambiguous`, `error`, `done`, `[DONE]`; do not expose internal retrieval/memory/tool events.

### Eval SSE

`consumeSSEStream()` parses blank-line-separated frames. Auto-generation consumes `start`, `warning`, `progress`, `done`, `error`; retrieval runs consume `start`, `progress`, `done`, `cancelled`, `error`. This parser differs from Agent Chat’s line reader.

## Report Rendering

The Agent `report` event supplies display text in `event.content`; Frontend does not parse structured `results[]`. `completeAssistantStream()` passes text through `renderMarkdown()`, which escapes HTML and supports only `##`/`###`, bold and line breaks. Rendered assistant HTML and adjacent thinking markup are later persisted in `chat_messages_{conversationId}`; DOM/class changes require snapshot compatibility review.

## Knowledge Base Integration

`refreshKnowledgeBases()` supplies both KB management and Agent selector state. Agent sends current `selectedKnowledgeBaseIds` as `selected_kbs` on every `/api/chat` request. Deployment create/edit selectors independently use Active KBs from `/api/kbs` and persist the fixed scope on the Deployment; public clients must never send `selected_kbs`.

## Deployment Security Rules

1. Plaintext Key may exist only in successful create/rotate responses and temporary `apiRevealedKey`; clear it on modal close/navigation and retain no JS secret object.
2. Never write it to LocalStorage, SessionStorage, URL, dataset, console or logs; lists/details show only `api_key_prefix` and must not fetch the full Key.
3. Examples always use `YOUR_API_KEY`; Deployment KB scope is server-fixed, so public examples/requests never include `selected_kbs`.

## Frontend / Backend Contract

`/api/chat` accepts `{query, session_id, selected_diagnosis, selected_kbs}` and returns internal SSE. Deployment management responses drive health/prefix, and only create/rotate may contain `api_key`; public chat accepts `{message, conversation_id?, selected_diagnosis?}` plus Deployment Bearer Key. Agent chooses KB per request, Deployment binds Active KBs at management time. Eval datasets use JSON, while run/auto-generate use distinct SSE contracts.

Contract changes require checking `../app/chat/server.py`, relevant request models (`server.py` and `schemas.py`), `../app/gateway.py` for public Deployment behavior, backend tests, and documentation. Do not scan unrelated Backend modules.

## Styling Map

`style.css` contains shared layout/navigation/page/button/form rules, then feature sections for memory, model modal, KB, Config, independent API page, Monitor and Eval, plus responsive media queries. Deployment uses cards, text badges, modals and code blocks. If HTML classes or IDs change, search CSS and JS together; if JS toggles a class such as `active`, `hidden`, `is-selected`, `is-running` or health/status classes, verify every visual state and narrow layout.

## Asset Cache Rule

`index.html` loads `/static/style.css?v=38` and `/static/app.js?v=56`. After changing `style.css` or `app.js`, increment the corresponding query value in `index.html`; it is manual cache invalidation, not a release version.

## Change Map

| Change | Start here | Also verify |
| --- | --- | --- |
| Add top-level page | `index.html` sidebar/page DOM; sidebar listener `pageMap` | entry load, CSS, resource version, README/AGENTS |
| Agent Chat | Chat DOM, `sendMessage()`, `handleStreamEvent()` | `/api/chat` schema, reader termination, processing/error state, LocalStorage |
| SSE contract | Backend emit site, parser, event switch | thinking/report UI, ignored events, done/error and both SSE parsers |
| KB UI | KB DOM and `refreshKnowledgeBases()` group | Agent selector, poll/abort logic, KB routes, Deployment Active-KB selector |
| API 上线 UI | Deployment DOM/functions and `docs/api-deployment-guide.md` | management routes, KB selector, one-time Key/guide modals, safe renderer, copy actions, responsive styles |
| Deployment API | frontend management/examples | Worker/Gateway, schemas, deployment tests, security rules and docs |
| Eval UI | Eval DOM, dataset/filter/run helpers | Eval event types, status restore/polling, cancellation and result fields |
| Config | config groups, model serialization/load/save | `/api/config`, `/api/test-model`, active-model panel; keep Deployment separate |
| LocalStorage | key constants and history helpers | old snapshot compatibility, UI/backend session mapping, sensitive-data risk |
| Report display | `handleStreamEvent()`, stream/render helpers | backend report content, `json_to_display()`, references and saved HTML |

## Testing / Validation

- Parse `app.js`; check browser console, duplicate/missing IDs and touched selectors.
- Exercise navigation/entry loads/modals/repeated visits plus loading/empty/error/disabled/responsive states and Deployment secret cleanup.
- Verify Fetch method/path/payload and SSE types against Backend; check cache values, Markdown links and `git diff --check`; run the smallest relevant backend test for contract changes.
- No automated browser test currently exists.
## Local Rules

- Keep native HTML/CSS/classic JS and current DOM switching; fixed IDs are cross-file contracts, and dynamic backend text must use `escapeHtml()` or `textContent`.
- Browser state is not authoritative; keep Agent and public Deployment chat separate, never persist Deployment secrets or let public callers choose KB scope.
- Update this file only when responsibilities, symbols, state, APIs, SSE, dependencies or validation paths change.

## Common Pitfalls

- Missing navigation mapping/entry load, stale DOM selectors/classes, or stale `?v=`; confusing local `currentConversationId`, Agent `SESSION_ID` and Deployment `conversation_id`.
- Persisting a plaintext Key, adding public `selected_kbs`, moving Deployment UI into Config, or mixing Agent/Deployment KB rules.
- Assuming backend events are rendered (`query_plan/cache_hit`) or `search_result` is emitted; treating both SSE parsers/token behavior as identical, or changing report DOM without checking stored snapshots.
## Human Documentation

Frontend setup, page usage and API examples belong in [README.md](README.md). Whole-project architecture and review paths belong in [../PROJECT_GUIDE.md](../PROJECT_GUIDE.md). Do not copy those guides into this navigation file.
## Agent Workflow

1. Read root [../AGENTS.md](../AGENTS.md), then this file; use Page/Change Map to select minimal frontend context.
2. Read Backend only to change/verify a contract; coordinate DOM/behavior/styles and validate syntax, IDs, navigation, routes, SSE, state/security and cache versions.
3. Update this file in the same change when structure, API, state ownership or responsibility changes.
