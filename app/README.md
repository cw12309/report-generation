# 医疗影像报告生成Agent v2

基于 RAG（检索增强生成）的医学影像报告结构化输出系统，通过 **Tool Calling 架构**让 LLM 自主决定调用工具（检索生成 / 编辑修改 / 风格重写 / 直接回复），将用户输入的检查信息转化为规范的影像学报告。

## 核心特性

- **Tool Calling 架构**：LLM 自主决策调用哪个工具（`rag_search` / `edit_report` / `refine_report`），替代硬编码意图分类，架构更灵活可扩展
- **OpenAI SDK 集成**：从原生 `requests` 迁移至官方 `openai` SDK，全异步链路（`AsyncOpenAI` + `async for`），支持多模型服务无缝切换
- **LangSmith 监控**：全面接入 LangSmith 可观测平台，自动追踪 LLM Token 消耗、工具调用耗时、Agent 思考链路，支持按模态/部位筛选日志
- **PromptBuilder 统一拼装**：消除 `rag_tool.py`、`refine_tool.py`、`pipeline.py` 中重复的 Prompt 拼装代码，LTM 偏好 + Entity 上下文 + 模板 + Last Report 由 `PromptBuilder.build()` 统一管理，拼接顺序和 reasoning 去除逻辑单点维护
- **三层记忆架构**：Entity Tracker（实体槽位）+ STM（短期记忆）+ LTM（长期偏好），独立解耦
- **LLM 实体提取**：双引擎机制（LLM JSON 结构化提取优先级 + 规则兜底），关键词按长度降序匹配，支持多部位（如 `CT 肝脏和胆囊` → `body_part: ["肝脏", "胆囊"]`）
- **意图切换清洗**：切换检查部位时强制清空 STM 和 last_report，杜绝旧病灶残留
- **多轮对话**：上下文消解 + 指代消解，继承检查类型，多轮报告自动合并去重
- **短期记忆**：可配置轮数（前端配置）+ 对话摘要，可通过前端面板查看
- **Agent 思考过程**：前端可视化展示工具调用、查询改写、召回、Rerank、推理全流程
- **流式输出**：FastAPI + SSE 推送，报告和闲聊消息实时流式渲染
- **配置统一管理**：`config.yml` 一站式配置，环境变量自动覆盖
- **Rerank 降级**：Rerank 服务不可用时自动降级为纯向量检索
- **知识库管理**：前端上传 xlsx → 自动切片 → 提取元数据 → 构建向量库，全流程可视化
- **配置管理**：前端可视化编辑 `config.yml`，在线测试模型连通性，保存即生效
- **会话持久化**：基于 SQLite 的 SessionStore，对话历史/实体槽位/上一轮报告自动保存，断线恢复、历史会话管理
- **记忆检索注入**：基于语义相似度的按需检索，替代全量注入 LTM/STM，减少无关记忆干扰，降低 Token 消耗
- **歧义检测与用户选择**：同病不同修饰（如"脑出血"vs"基底节区脑出血"vs"脑出血（破入脑室）"）时，前端弹出按钮让用户精确选择，避免 LLM 猜测
- **歧义缓存加速**：用户点击歧义选项后，复用上次的检索+Rerank 结果，跳过昂贵的多路召回和重排序，显著降低响应延迟
- **部位补全增强**：当 `parse_query_keywords()` 从文本中提取不到部位时（如"CT 脑出血"中"脑"不是独立部位词），自动从 Entity Tracker 的实体槽位和诊断关键词启发式推断中两层补全部位，提升元数据过滤和关键词检索精度，不修改向量检索 query 文本以保证语义相似度不受影响
- **缺少模态拦截**：当用户输入只有部位/诊断但没有检查类型时（如"脑出血"），自动追问用户补充 CT/MR 等，避免生成不完整报告

## 系统架构

```
用户输入 "CT脑出血"
       │
       ▼
┌──────────────────────────┐
│  预处理                   │
│  ├ 模糊检测 → 追问       │
│  ├ 缺少模态拦截 → 追问    │
│  ├ 上下文消解 → 指代继承  │
│  ├ 术语标准化             │
│  └ 查询改写               │
└──────────┬───────────────┘
           ▼
┌──────────────────────────────────────────────────┐
│  Tool Calling 主循环                              │
│                                                   │
│  1. MemoryRetriever: 检索最相关 LTM/STM 片段 → 构建 System Prompt │
│  2. chat_with_tools(messages, tools=[...])        │
│     → LLM 自主决策：调用工具 or 直接回复          │
│                                                    │
│     ┌──────────────┬──────────────┬──────────────┐ │
│     ▼              ▼              ▼              ▼ │
│  rag_search    edit_report   refine_report   直接回复│
│  ┌────────┐    ┌────────┐    ┌────────┐    ┌────┐ │
│  │多路召回 │    │修改报告│    │重写风格│    │闲聊│ │
│  │Rerank  │    │保留Key │    │跳过检索│    │回复│ │
│  │结构化生成│   │按指令改│    │保留内容│    └────┘ │
│  └────────┘    └────────┘    └────────┘            │
│                                                    │
│  3. 工具返回 _is_final: true → 跳过二次 LLM 调用    │
│     直接发送报告到前端，避免引入幻觉                │
│     非 final 结果（错误/文本）→ 二次 LLM 包装回复    │
└──────────┬───────────────────────────────────────┘
           ▼
┌──────────────────────────────────────┐
│  存储 / 记忆更新                       │
│  ├ STM: add_turn                    │
│  ├ EntityTracker: 槽位更新           │
│  ├ LTM: 偏好写入                     │
│  ├ last_report 更新                 │
│  ├ ★ MemoryRetriever: 语义检索注入  │
│  └ ★ SessionStore: SQLite 持久化    │
│     对话记录 + 实体槽位 + 报告       │
└──────────────────────────────────────┘
```

## 项目结构

```
app/
├── chat/                    # ★ 对话模块（模块化重构）
│   ├── __init__.py          # 包标识
│   ├── schemas.py           # Pydantic 数据模型（请求/响应 Schema）
│   ├── utils.py             # 无状态工具函数（retry、JSON提取、报告格式化）
│   ├── llm_client.py        # 大模型网络请求封装（OpenAI SDK + LangSmith wrapper）
│   ├── pipeline.py          # Agent 核心编排（检索、工具注册、run_pipeline）
│   ├── server.py            # FastAPI Web 服务层（所有路由 + web_main）
│   ├── main.py              # 启动入口（CLI + --web 模式切换）
│   └── README.md            # chat 模块说明
├── tools/                 # ★ 工具模块（Tool Calling）
│   ├── registry.py        # 工具注册中心（ToolResult 数据类 + OpenAI 兼容 schema）
│   ├── utils.py           # 工具共用辅助函数（JSON提取）
│   ├── rag_tool.py        # rag_search — RAG 检索 + 结构化报告生成
│   ├── edit_tool.py       # edit_report — 修改已有报告
│   ├── refine_tool.py     # refine_report — 重写报告风格
│   └── __init__.py
├── rag/
│   ├── query_planner.py   # 自然语言 → modality/level_1～4 结构化检索计划
│   ├── metadata_navigator.py # 层级候选披露和合法值校验
│   ├── sparse_vector.py   # 建库与检索共用的 Sparse 词面向量
│   ├── retrieval.py       # 标量过滤 + Dense/Sparse + RRF + 动态降级
│   ├── rerank.py          # 外部 Rerank 精排
│   └── README.md          # RAG 检索流程说明
│   ├── query_rewrite.py   # 查询改写模块（模糊检测 + 术语标准化 + 关键词解析 + LLM 改写）
│   ├── rerank.py          # Rerank 精排模块（SiliconFlow API）
│   └── prompt.md          # 影像报告结构化规范（实际 prompt 模板在 app/prompt/ 目录）
├── memory/
│   ├── short_term.py      # 短期记忆（对话历史 + 自动摘要）
│   ├── long_term.py       # 长期记忆（持久化偏好存储）
│   ├── entity_tracker.py  # 独立实体追踪（模态/部位槽位 + 意图判断 + 上下文消解）
│   ├── chat_test.py       # 记忆模块测试脚本
│   ├── data/              # 长期记忆数据库文件（ltm.db）
│   └── README.md          # 记忆模块说明
├── data_pipeline/
│   ├── process_reports.py # Excel 模板整理为层级 Markdown
│   ├── build_vector_db.py # Dense + Sparse Milvus 知识库全量构建
│   ├── extract_metadata.py# 汇总 modality 与 level_1～4（生成 metadata.json）
│   ├── README.md          # 数据管线、Schema 与运行说明
│   ├── output_markdown/   # 生成的层级 Markdown（已忽略）
│   └── report_template/   # 原始 xlsx 报告模板（已忽略）
├── config.py              # 配置读取器（config.yml → 各模块）
├── prompt/                # 系统提示词模板（各阶段 LLM prompt）
│   ├── builder.py         # ★ PromptBuilder — 统一 Prompt 拼装器（LTM + Entity + 模板 + Last Report）
│   ├── __init__.py        # load_prompt() 模板加载器
│   ├── tool_orchestrator.md # ★ Tool Calling 编排提示词（指导 LLM 决策工具调用）
│   ├── structure.md       # 报告结构化提取（检索结果 → JSON）
│   ├── edit.md            # 编辑已有报告
│   ├── refine.md          # 重写报告风格
│   ├── entity_extraction.md # 实体提取（LLM JSON 结构化提取 prompt）
│   ├── chat.md            # 闲聊回复
│   ├── intent.md          # 意图识别（旧版，保留兼容）
│   ├── report_generation.md # 报告生成（综合 prompt）
│   ├── rewrite.md         # 查询改写
│   ├── summarize.md       # 对话摘要
│   ├── react_system.md    # ReAct 系统 prompt
│   ├── react_simple.md    # ReAct 简化 prompt
│   └── multi_disease.md   # 多病种报告
├── store/                 # ★ 会话持久化（Phase 4）
│   ├── session_store.py   # SessionStore — SQLite 持久化存储
│   └── __init__.py
├── test/                  # 测试与评估脚本
│   ├── test_memory.py              # 记忆模块单元测试（意图切换/实体提取/LTM注入/上下文消解）
│   ├── test_phase2_verification.py # Phase 2 验证测试（LLM提取/脏数据/异常回退/向后兼容）
│   ├── _test_search.py             # 向量检索快速测试
│   ├── eval_retrieval.py           # 检索命中率评估
│   ├── eval_rerank_compare.py      # 向量检索 vs Rerank 对比评估
│   ├── test_rerank_flow.py         # 完整流程测试
│   └── tests_api/                  # API 连通性测试
└── README.md              # 本文件
```

## 配置说明

所有配置统一在项目根目录的 `config.yml` 中管理，通过 `config.py` 读取。优先级：**环境变量 > config.yml > 默认值**。敏感信息（如 API Key）建议通过环境变量传入。

## 使用方式

### Web 界面

```bash
python python_start.py
```

浏览器访问 `http://localhost:8000`，前端提供：

| 功能           | 说明                                                                 |
| -------------- | -------------------------------------------------------------------- |
| 聊天输入       | 输入检查信息，如 `CT 脑出血`、`MR 膝关节`；缺少检查类型时自动追问    |
| 结构化报告     | 影像学表现 + 诊断意见，实时流式渲染                                  |
| 历史对话       | 左侧栏展示所有对话记录，支持切换、删除，对话自动保存到 SQLite 持久化 |
| 短期记忆       | 弹窗查看当前会话的对话历史、实体、摘要                               |
| Agent 思考过程 | 折叠面板，展示工具调用、检索查询、召回、Rerank、推理、总耗时         |
| 清空会话       | 重置会话状态和短期记忆                                               |
| 知识库管理     | 上传 xlsx 报告模板、在线切片、提取元数据、构建向量库                 |
| 配置管理       | 可视化编辑模型配置（LLM/Embedding/Rerank），在线测试连通性           |

### 命令行

```bash
python app/chat/main.py
```

## 知识库管理

前端提供完整的知识库（Excel 报告模板）管理界面，支持以下操作：

| 操作       | 说明                                                                                             | 对应 API                        |
| ---------- | ------------------------------------------------------------------------------------------------ | ------------------------------- |
| 上传模板   | 上传 `.xlsx` 格式的影像报告模板文件                                                              | `POST /api/kb/upload`           |
| 整理模板   | 将 xlsx 数据按标准层级整理为独立 Markdown 报告                           | `process_reports.py`             |
| 提取元数据 | 从 Markdown 汇总 `modality → level_1～4` 目录树，生成 `metadata.json` | `POST /api/kb/extract-metadata`  |
| 构建向量库 | 将 Markdown 转为 Dense + Sparse 向量并全量重建远程 Milvus Collection  | `POST /api/kb/build`             |
| 查看状态   | 查看当前向量库条目数、Markdown 文件数和元数据状态                       | `GET /api/kb/status`             |
| 查看文件   | 列出已上传的报告模板                                                     | `GET /api/kb/files`              |

```
上传 xlsx
   │
   ▼
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│ 整理模板     │ →  │ 提取元数据    │ →  │ 构建向量库       │
│ 层级化 .md   │    │ modality/L1   │    │ Dense + Sparse   │
└─────────────┘    └──────────────┘    └──────────────┘
```

## 配置管理

前端提供可视化配置面板，可在线编辑 `config.yml` 而不需要直接修改文件：

| 功能     | 说明                                                                         |
| -------- | ---------------------------------------------------------------------------- |
| 模型列表 | 管理多个 LLM / Embedding / Rerank 模型配置（base_url、model、api_key、参数） |
| 激活切换 | 从模型列表中选择当前使用的模型（通过 `active_models` 指定）                  |
| 连接测试 | 在线测试 LLM / Embedding / Rerank 的 API 连通性，显示响应预览                |
| 实时生效 | 保存配置后自动调用 `reload_config()`，无需重启服务                           |
| 安全保护 | API Key 在前端显示为掩码占位符，实际值从 `.env` 环境变量读取                 |

配置优先级：**环境变量 > config.yml > 默认值**

## 报告生成流程

完整流程分 3 个阶段：

```
用户输入 "脑梗"（第2轮，上轮是"CT 脑出血"）
       │
       ▼
┌─ Phase 1: 预处理 ────────────────────────────┐
│ 1. 模糊检测 → 不过于模糊，跳过              │
│ 2. 缺少模态拦截 → 有模态，跳过              │
│ 3. 上下文消解 → "脑梗" 缺少检查类型 → 继承  │
│    上轮的 "CT" → "CT 脑梗"                  │
│ 4. 术语标准化 → "CT 脑梗"                   │
│ 5. 查询改写 → 有诊断词，无需改写             │
│ 6. 获取历史 → 最近 6 轮对话                 │
└──────────────┬──────────────────────────────┘
               ▼
┌─ Phase 2: Tool Calling 主循环 ─────────────────┐
│                                                │
│  1. 构建 System Prompt                         │
│     PromptBuilder.build("tool_orchestrator",    │
│       LTM 偏好 + Entity 上下文)                  │
│     注入上一轮报告（供工具决策参考）              │
│                                                │
│  2. chat_with_tools(messages, tools=[          │
│       rag_search, edit_report, refine_report   │
│     ])                                         │
│     → LLM 自主决策：调用 rag_search            │
│                                                │
│  3. 执行 rag_search 工具                        │
│     ├ PromptBuilder.build("structure", ...)      │
│     │   统一拼装 LTM + Entity + 模板 + Last Report│
│     ├ 参数容错：缺失 modality/body_part 从      │
│     │   EntityTracker 自动补全                  │
│     ├ ★ 部位补全：parse_query_keywords() 提取不到部位时，│
│     │   从 entity_tracker 槽位 + 诊断关键词启发式推断│
│     │   （如 "CT 脑出血" → 部位补全为 "头颅"）   │
│     │   仅增强元数据/关键词路径，不修改向量检索文本│
│     ├ 流式状态：_emit("status") 推送            │
│     │   searching → generating → done           │
│     ├ 多路召回（向量 + 元数据 + 关键词）       │
│     ├ Rerank 精排 → Top-10                     │
│     ├ 歧义检测：top-10 分数接近且诊断多样时     │
│     │   → 缓存检索结果到全局 dict               │
│     │   → _emit("ambiguous") → 前端弹出按钮     │
│     │   → 用户点击后 selected_diagnosis 传入    │
│     │   → 命中缓存，跳过检索，直接生成报告      │
│     ├ 注入 LTM 偏好 + Entity 上下文            │
│     └ LLM 结构化提取 → 报告 JSON                │
│                                                │
│  4. 检测 _is_final: true                            │
│     ├ 保存 last_report（确保下一轮编辑可用）        │
│     ├ 跳过二次 LLM 调用，避免引入幻觉               │
│     └ 直接 json_to_display → _emit("report")         │
│                                                │
│  LLM 其他决策：                                 │
│  → 无 tool_calls → 直接流式回复（闲聊）        │
│  → edit_report → 修改已有报告，同样 is_final   │
│  → refine_report → 重写报告，同样 is_final     │
│  → 只有非 final 结果（错误等）才启用二次 LLM    │
└──────────────┬──────────────────────────────────┘
               ▼
┌─ Phase 3: 后处理（仅非 final 路径） ─────────────┐
│ 1. 更新 last_report（从工具结果提取）          │
│ 2. STM 记录对话轮次                            │
│ 3. _emit("message") → 前端渲染                  │
└────────────────────────────────────────────────┘
```

## 多轮对话机制

### 三层记忆架构

| 层级            | 模块                | 存储内容                       | 生命周期             |
| --------------- | ------------------- | ------------------------------ | -------------------- |
| Entity Tracker  | `entity_tracker.py` | 当前模态/部位/病史/诊断槽位    | 单次会话             |
| STM（短期记忆） | `short_term.py`     | 最近 N 轮对话历史 + 自动摘要   | 单次会话，可配置轮数 |
| LTM（长期记忆） | `long_term.py`      | 用户偏好（模态/部位/格式偏好） | 跨会话持久化         |

### 实体提取（Entity Tracker）

| 机制             | 说明                                                                                                                                                                               |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 实体槽位         | `modality`（检查类型）、`body_part`（检查部位，**列表存储，支持多个**）、`clinical_history`（病史/症状）、`diagnosis`（已确认诊断列表）、`intent`（new_session / append / switch） |
| LLM 提取（优先） | 调用 LLM 返回 JSON `{"modality": "CT", "body_part": ["脑部", "颈部"]}`，支持数组输出多部位                                                                                         |
| 规则兜底         | LLM 不可用时按关键词长度降序匹配（PET-CT > CT > T），所有匹配部位追加到列表，长词优先去重（"膝关节"已匹配则跳过"膝"）                                                              |
| 上下文消解       | 省略主语时自动补全："再看看肝脏" → "CT 脑部 肝脏 再看看肝脏"（多部位拼接）                                                                                                         |
| 意图检测         | 粗粒度判断：new_session / append / switch（切换时清空 STM + last_report）                                                                                                          |

### Tool Calling 机制

| 工具              | 触发条件                             | 系统动作                                                             |
| ----------------- | ------------------------------------ | -------------------------------------------------------------------- |
| **rag_search**    | 生成/检索报告（有检查类型和部位）    | 多路召回 → Rerank 精排 → LLM 结构化提取 → 标记 `_is_final: true`     |
| **edit_report**   | 修改具体医学内容（如改CT值、删病变） | 调用 LLM 修改报告，保留 key 结构不破坏，标记 `_is_final: true`       |
| **refine_report** | 调整风格/详细程度（如"写详细点"）    | **跳过检索**，基于已有报告重写，保留医学内容，标记 `_is_final: true` |
| **直接回复**      | 闲聊/问候/功能咨询                   | LLM 直接回复，不调用工具                                             |

### 工具注册与执行

```
ToolRegistry 初始化
├── rag_search      ← create_rag_search_handler(chat_fn, ltm, entity_tracker, ..., last_report)
│                     内部使用 PromptBuilder.build("structure", ...) 统一拼装 System Prompt
├── edit_report     ← create_edit_report_handler(chat_fn, ..., last_report)
└── refine_report   ← create_refine_report_handler(chat_fn, ltm, entity_tracker, ..., last_report)
                      内部使用 PromptBuilder.build("refine", ...) 统一拼装 System Prompt

LLM 返回 tool_calls → registry.execute(id, name, arguments)
→ 返回 ToolResult(content, is_final)
→ is_final=True 时跳过二次 LLM 调用，直接发送报告
失败时返回 error JSON → LLM 可据此重试或向用户解释
```

### 对话流控制

| 机制       | 说明                                                           |
| ---------- | -------------------------------------------------------------- |
| 上下文消解 | 缺少检查类型时自动继承上一轮（"脑梗"→"CT 脑梗"）               |
| 指代消解   | "它"、"这个" 等代词替换为上一轮实体                            |
| 报告合并   | 新病变追加到已有报告，已有病变保留不覆盖                       |
| 短期记忆   | 可配置轮数（默认 10 轮），超出自动压缩为摘要                   |
| 切换清洗   | 意图切换时强制清空 STM 和 last_report，杜绝旧病灶残留          |
| 编辑保护   | EDIT 模式下保留报告 JSON 的 key 结构，仅按指令修改内容         |
| 重写保护   | REFINE 模式下 Key 集合变化时自动回退旧报告，杜绝医学内容被篡改 |

### 记忆检索注入

Phase 3 改动：改变全量注入 LTM/STM 到 Prompt 的做法，改为 **基于语义相关性按需检索**，仅将最相关的偏好和历史注入。

| 步骤               | 说明                                                                 |
| ------------------ | -------------------------------------------------------------------- |
| 索引 LTM 偏好      | `MemoryRetriever.index_ltm()` → 增量追加，仅 Embedding 新偏好        |
| 索引 STM 历史      | `MemoryRetriever.index_stm()` → 增量追加，仅 Embedding 新增消息      |
| 检索               | 增强 Query → 向量化 → 余弦相似度排序 → 返回 Top-K（LTM: 3 / STM: 3） |
| PromptBuilder 拼装 | 只注入检索结果，减少无关记忆干扰，降低 Token 消耗                    |

| 配置         | 默认值 | 说明               |
| ------------ | ------ | ------------------ |
| `top_k_ltm`  | 3      | 返回的偏好条数     |
| `top_k_stm`  | 3      | 返回的历史消息条数 |
| `相似度阈值` | 0.3    | 低于阈值不返回结果 |

**增量索引策略**：首次调用时全量 Embedding，后续每轮仅对新出现的偏好/消息执行 Embedding。旧向量缓存复用，性能提升 ~90%（以 10 轮对话为例，API 调用从 ~400 次降至 ~50 次）。

## 工具设计与风险控制

在医疗报告这个严肃场景，我们对三个关键风险做了专门设计：

| 风险点                       | 解决方案                                                                                                                                                                                                                                         |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **二次 LLM 调用引入幻觉**    | 工具生成的结构化报告标记 `_is_final: true`，主循环检测后**跳过二次 LLM 调用**，直接将工具结果发送给前端。同时在 return 前保存 `last_report`，确保下一轮编辑/重写可以正常使用。                                                                   |
| **工具执行过程中前端无反馈** | 工具内部通过 `_emit("status")` 推送进度事件，每个阶段都有状态更新：<br>`searching` → `generating` → `done`，前端可以实时展示 Agent 思考过程。                                                                                                    |
| **LLM 漏传工具参数**         | 关键参数缺失时自动从记忆补全：<br>- `rag_search` 缺失 `modality`/`body_part` → 从 `EntityTracker` 补全<br>- `edit_report`/`refine_report` 缺失 `current_report` → 从 `last_report` 补全<br>工具层和主循环双层兜底，即使 LLM 漏传参数也不会报错。 |

### 歧义检测与用户选择

当知识库中存在同一疾病的多种变体（如"脑出血"、"基底节区脑出血"、"脑出血（破入脑室）"等），且 Rerank 分数接近时，系统自动触发歧义检测，让用户精确选择，而非由 LLM 猜测。

**歧义检测算法** (`detect_ambiguity` in `rag_tool.py`)：

```
1. Rerank top-10 中，如果 top-1 和 top-2 分数差 < 阈值（默认 0.03）
2. 收集 top-10 内所有不同的诊断结论
3. 如果不同诊断 ≥ 2 种 → 触发歧义
```

**交互流程**（支持多次点击不同按钮）：

```
第一轮：用户输入 "CT 脑出血"
  ├→ 多路召回 + Rerank（top-10）
  ├→ detect_ambiguity() → 检测到 9 种不同诊断
  ├→ 缓存 (search_result, reranked_entities) 到全局 dict
  ├→ _emit("ambiguous") → 前端渲染 9 个按钮
  └→ 等待用户选择

用户点击按钮 "基底节区脑出血"
  ├→ 前端传入 selected_diagnosis="基底节区脑出血"
  ├→ 跳过 Phase 1（实体提取/意图检测），保留 modality/body_part 槽位
  ├→ LLM 强制调用 rag_search（enhanced 注入明确指令）
  ├→ 命中缓存 → 跳过检索和 Rerank
  ├→ _filter_by_selected_diagnosis() → 只保留诊断结论匹配的参考
  ├→ _rebuild_search_result() → 重建检索文本，LLM 只看到匹配结果
  ├→ 生成对应报告 → _emit("report")
  └→ 缓存不删除 → 用户可以继续点击其他按钮

用户再次点击 "脑出血（破入脑室）"
  ├→ 同上流程 → 缓存命中 → 过滤 → 生成不同报告
  └→ 缓存仍然保留

用户输入新查询 "CT 脑梗" → 清除旧缓存 → 重新检索
```

**缓存加速**：

| 节点         | 第一轮  | 后续点击（任意次数） |
| ------------ | ------- | -------------------- |
| 多路召回     | ✅ 执行 | ❌ 跳过（缓存命中）  |
| Rerank       | ✅ 执行 | ❌ 跳过（缓存命中）  |
| 歧义检测     | ✅ 触发 | ❌ 跳过              |
| 结果过滤     | ❌      | ✅ 精确过滤匹配      |
| LLM 生成报告 | ❌      | ✅ 执行              |

**缓存生命周期**：全局 `_ambiguity_cache` dict 按 `session_id` 索引，**直到用户输入新查询才清除**。点击不同按钮不清除缓存，允许多次点击。

**过滤机制** (`_filter_by_selected_diagnosis` in `rag_tool.py`)：

```
精确匹配：d == selected_diagnosis → 优先返回这些
↓
如果没有精确匹配 → 返回包含匹配：(selected_diagnosis in d) or (d in selected_diagnosis)
↓
都没有 → 返回全部兜底
```

这样保证：用户点哪个 → LLM 只看到哪个 → 一定生成对应报告。

**实体槽位保留**：

当 `selected_diagnosis` 存在时，**跳过 Phase 1（实体提取/意图检测/上下文消解）**，保留上一轮的 `modality` 和 `body_part` 槽位。这样：

- 不会丢失检查类型/部位信息
- 缓存可以正确命中（参数完整）
- 避免重新提取清空原有槽位导致检索参数缺失

### 前端歧义交互

前端监听 `type: "ambiguous"` SSE 事件：

```javascript
case "ambiguous":
  addAmbiguousOptions(event)  // 渲染按钮列表，显示分数百分比
  break
```

用户点击按钮时，调用 `window.selectAmbiguityOption(option)`，将选项名作为 `selected_diagnosis` 参数传给后端 `/api/chat`。按钮样式仿照聊天界面，每行显示诊断名称和 Rerank 分数百分比。

### PromptBuilder 统一拼装

`rag_tool.py`、`refine_tool.py` 和 `rag_chat_v2.py` 中原本各自包含一套手动拼接 LTM 偏好 + Entity 上下文 + 模板 + Last Report 的代码，维护成本高且容易不一致。引入 `PromptBuilder` 后：

| 调用方           | `PromptBuilder.build()` 调用                                                           | 模板                   |
| ---------------- | -------------------------------------------------------------------------------------- | ---------------------- |
| `rag_tool.py`    | `PromptBuilder.build("structure", ltm_prefs=..., entity_context=..., last_report=...)` | `structure.md`         |
| `refine_tool.py` | `PromptBuilder.build("refine", ltm_prefs=..., entity_context=...)`                     | `refine.md`            |
| `pipeline.py`    | `PromptBuilder.build("tool_orchestrator", ltm_prefs=..., entity_context=...)`          | `tool_orchestrator.md` |

拼接顺序（优先级从高到低）：LTM 偏好 → Entity 上下文 → 基础模板 → Last Report（自动去除 `reasoning` 字段），各段用 `\n\n---\n\n` 分隔。

### 检索前部位补全

`search_reports()` 在 `parse_query_keywords()` 提取关键词后，对缺失的部位做两层补全，增强元数据过滤和关键词检索路径的精确度。

**典型场景**：用户输入 "CT 脑出血"

- `parse_query_keywords()` 提取：检查类型="CT"，部位=""（空），诊断关键词=["出血"]
- 原因："脑"后面紧跟"出血"，不是独立部位词，不被 `_is_standalone_part()` 判定为部位

**两层补全机制**：

| 层级 | 来源                    | 说明                                                                                                                                                        |
| ---- | ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 层 1 | Entity Tracker 实体槽位 | 如果 `slots["body_part"]` 有值（LLM 提取或规则匹配得到），取第一个部位作为补全                                                                              |
| 层 2 | 诊断关键词启发式推断    | 如果 entity_tracker 也识别不到，通过 `_DIAGNOSIS_TO_PART` 映射表从诊断关键词或原始 query 中推断（如 "脑出血" → "头颅"、"肺结节" → "胸部"、"肝脏" → "腹部"） |

**设计原则**：只增强结构化过滤路径（元数据过滤 / 关键词检索）的部位字段，**不修改向量检索用的 query 文本**，保证语义相似度计算不受影响。

**效果对比**：

| 查询        | 补前 keywords            | 补后 keywords                | 对检索精度的影响                                           |
| ----------- | ------------------------ | ---------------------------- | ---------------------------------------------------------- |
| "CT 脑出血" | {检查类型:"CT", 部位:""} | {检查类型:"CT", 部位:"头颅"} | 元数据过滤从单条件变为双条件，排除其他部位的"出血"相关记录 |
| "CT 肺结节" | {检查类型:"CT", 部位:""} | {检查类型:"CT", 部位:"胸部"} | 关键词检索时增加部位过滤，排除腹部/头部的"结节"匹配        |

**实现位置**：`chat/pipeline.py` 中的 `_infer_part_from_diagnosis()` 函数和 `search_reports()` 的部位补全逻辑。

### 工具参数 Schema

| 工具              | 参数             | 必填 | 说明                                                                          |
| ----------------- | ---------------- | ---- | ----------------------------------------------------------------------------- |
| **rag_search**    | `query`          | 是   | 经过上下文消解的查询语句，包含检查类型和部位                                  |
|                   | `modality`       | 否   | 检查类型（CT/MR/MRI/DR/X线/超声/PET-CT/DSA/CTA/不限），缺失自动从 Entity 补全 |
|                   | `body_part`      | 否   | 检查部位，缺失自动从 Entity 补全                                              |
| **edit_report**   | `current_report` | 是   | 当前报告的 JSON 字符串，缺失自动从 last_report 补全                           |
|                   | `instruction`    | 是   | 修改指令，描述要改什么内容                                                    |
| **refine_report** | `current_report` | 是   | 当前报告的 JSON 字符串，缺失自动从 last_report 补全                           |
|                   | `style`          | 是   | 重写风格指令（如："更详细"、"简洁一点"）                                      |

## 向量数据库 Schema

集合名：`medical_reports`（远程 Milvus）

每个 Markdown 报告对应一条 Milvus Entity。标量字段、报告正文、Dense
向量和 Sparse 向量存放在同一个 Collection 中。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INT64 | Milvus 自动生成的主键 |
| `modality` | VARCHAR(32) | 检查类型 |
| `level_1` | VARCHAR(64) | 一级目录 |
| `level_2` | VARCHAR(64) | 二级目录 |
| `level_3` | VARCHAR(64) | 三级目录 |
| `level_4` | VARCHAR(64) | 四级目录 |
| `module_name` | VARCHAR(128) | 模块名称 |
| `imaging_findings` | VARCHAR(4096) | 影像学表现 |
| `imaging_opinion` | VARCHAR(4096) | 影像学意见 |
| `combined_text` | VARCHAR(8192) | 元数据与报告正文的组合检索文本 |
| `dense_vector` | FLOAT_VECTOR(1024) | BGE-M3 语义向量 |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | 词面稀疏向量 |

索引：

| 字段 | 索引 | 参数 |
|---|---|---|
| `modality` | INVERTED | 标量过滤 |
| `level_1` | INVERTED | 标量过滤 |
| `level_2` | INVERTED | 标量过滤 |
| `level_3` | INVERTED | 标量过滤 |
| `level_4` | INVERTED | 标量过滤 |
| `dense_vector` | HNSW | COSINE, M=16, efConstruction=256 |
| `sparse_vector` | SPARSE_INVERTED_INDEX | IP, drop_ratio_build=0.2 |

检索时先从问题和对话历史中规划 `modality`、`level_1～4`，并依据
`metadata.json` 校验层级关系；随后为 Dense 和 Sparse 分别构造
`AnnSearchRequest`，两路请求使用相同的标量过滤条件，通过 `RRFRanker`
完成粗排。严格条件召回不足时按 `level_4 → level_1` 逐层放宽，最后交给
外部 Rerank 模型精排。

## RAG 检索架构

`app/rag` 负责把自然语言问题转换为经过元数据校验的检索计划，并在
Milvus 中完成标量过滤、Dense/Sparse 双路召回、RRF 粗排和动态降级；
`chat/pipeline.py` 负责串联该流程，并对粗排候选执行外部 Rerank 精排。

```text
用户问题 + Entity Tracker 历史槽位
                  │
                  ▼
           Query Planner
      modality + level_1～4
                  │
                  ▼
        Metadata Navigator
      层级候选披露 + 合法值校验
                  │
                  ▼
          最严格的标量过滤
             ┌────┴────┐
             ▼         ▼
       Dense HNSW   Sparse Inverted
          COSINE          IP
             └────┬────┘
                  ▼
          Milvus RRFRanker
             粗排 Top-K
                  │
        候选不足时逐级放宽
                  │
                  ▼
          外部 Rerank 精排
             最终 Top-K
```

### 层级元数据导航

`extract_metadata.py` 生成的 `metadata.json` 只保存目录关系，不保存模块名称
或 Markdown 文件路径：

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

空层级会被跳过，因此 `level_2` 为空时，三级目录可以直接位于一级目录
节点下。`MetadataNavigator` 会把目录树展开为合法路径集合，用于获取候选
检查类型和目录值、匹配当前问题中的最长合法值，以及校验 LLM 输出。

### 结构化 Query Planner

`build_query_plan()` 按以下优先级确定检索字段：

1. 当前问题中明确出现且存在于元数据中的值。
2. Entity Tracker 历史槽位中的检查类型和部位。
3. LLM 从 Metadata Navigator 提供的候选值中选择的值。

LLM 不能自由生成目录名称，不属于合法层级路径的值会被丢弃。规划结果示例：

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
    "level_3": {
      "value": "颅脑平扫",
      "confidence": 1.0,
      "source": "current_query"
    }
  }
}
```

`semantic_query` 保留完整自然语言，用于生成 Dense 向量；
`keyword_query` 会移除已经识别的检查类型和层级名称，用于生成 Sparse
词面向量。

### 标量过滤与双路召回

只有经过元数据校验的字段才会进入 Milvus 过滤表达式：

```text
modality == "CT"
and level_1 == "头颅"
and level_3 == "颅脑平扫"
```

Dense 和 Sparse 的 `AnnSearchRequest` 使用相同的标量过滤表达式：

```python
dense_request = AnnSearchRequest(
    data=[query_vector],
    anns_field="dense_vector",
    param={"metric_type": "COSINE", "params": {"ef": 64}},
    expr=filter_expr,
    limit=top_k,
)

sparse_request = AnnSearchRequest(
    data=[text_to_sparse_vector(keyword_query)],
    anns_field="sparse_vector",
    param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
    expr=filter_expr,
    limit=top_k,
)
```

Milvus 使用 `RRFRanker()` 融合语义排名和关键词排名，得到粗排候选。

### 动态降级

严格过滤范围内的累计候选不足时，按照目录深度逐级放宽：

```text
modality + level_1 + level_2 + level_3 + level_4
modality + level_1 + level_2 + level_3
modality + level_1 + level_2
modality + level_1
modality
```

目录降级不会移除 `modality`。严格阶段的结果优先保留，后续阶段按 Milvus
实体 `id` 去重。每个阶段都会记录实际过滤条件、过滤表达式和召回数量。

### Rerank 与运行事件

RRF 候选返回 `chat/pipeline.py` 后，外部 Rerank 服务根据
`combined_text` 重新评分并选出最终 Top-K。Rerank 不再次访问 Milvus；
服务异常时会退化为直接使用 RRF 粗排结果。

检索过程向前端发送以下事件：

| 事件 | 内容 |
|---|---|
| `query_plan` | 结构化检索计划、字段置信度和来源 |
| `recall` | 候选数量及所有动态降级阶段 |
| `rerank` | 精排分数、来源和诊断名称 |

### Collection 加载校验

每次召回前，`ensure_collection_loaded()` 会读取 Collection Load State。
Collection 未加载时主动调用 `load_collection()` 并再次校验；仍未加载则
抛出明确错误。因此 Milvus 重启或 Collection 被 Attu Release 后，下一次
检索会尝试恢复其加载状态。

## 离线工具脚本

### 生成 Markdown

将 `app/data_pipeline/report_template` 中的 xlsx 模板整理到
`app/data_pipeline/output_markdown`：

```bash
python app/data_pipeline/process_reports.py
```

输出目录按照 `modality / level_1 / level_2 / level_3 / level_4` 构建，
`module_name` 用作 Markdown 文件名。同目录下模块名称和内容均相同时跳过，
名称相同但内容不同时生成 `_1`、`_2` 等编号文件。

### 构建向量数据库

```bash
python app/data_pipeline/build_vector_db.py
python app/data_pipeline/build_vector_db.py --batch-size 16
```

当前采用全量重建：执行时会删除已存在的 `medical_reports` Collection，
按最新 Schema 重建，然后导入全部 Markdown。向量化过程显示百分比：

```text
Dense向量化: 640/2800 (22.9%)
Sparse向量化: 700/2800 (25.0%)
```

模拟建表、插入三条报告并执行 Hybrid Search：

```bash
python app/data_pipeline/build_vector_db.py --demo
```

注意：`--demo` 同样会删除 `medical_reports`，不要在正式知识库上随意执行。

### 提取层级元数据

```bash
python app/data_pipeline/extract_metadata.py
python app/data_pipeline/extract_metadata.py --stats
python app/data_pipeline/extract_metadata.py --sample 3
```

默认生成 `app/data_pipeline/metadata.json`，记录检查类型和完整目录层级，
不记录 Markdown 文件名或模块名称：

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

`report_template`、`output_markdown`、`metadata.json` 和报告整理结果均已
加入 `.gitignore`。

### 检索评估

```bash
python app/test/eval_retrieval.py                     # 抽 100 条，评估 top-1/3/5
python app/test/eval_retrieval.py -n 50 -k 5          # 抽 50 条，评估 top-5
```

### Rerank 对比评估

```bash
python app/test/eval_rerank_compare.py                     # 抽 30 条，向量 top-5，Rerank top-3
python app/test/eval_rerank_compare.py -n 50 -k 10 -r 5    # 抽 50 条，向量 top-10，Rerank top-5
```

## 技术栈

| 组件          | 技术                    | 说明                       |
| ------------- | ----------------------- | -------------------------- |
| Web 框架      | FastAPI + uvicorn       | SSE 流式推送               |
| 前端          | 原生 HTML/CSS/JS        | 无框架依赖                 |
| 向量数据库    | 远程 Milvus (pymilvus)  | Dense + Sparse 混合检索    |
| Embedding     | bge-m3 (1024 维)        | 本地部署，OpenAI 兼容 API  |
| Rerank        | Qwen3-VL-Reranker-8B    | SiliconFlow 云端 API       |
| LLM 生成/改写 | Qwen (qwen36-27b)       | 本地部署，OpenAI SDK 调用  |
| 配置管理      | config.yml + PyYAML     | 环境变量自动覆盖           |
| 短期记忆      | 内存 OrderedDict        | 轮次淘汰 + 自动摘要        |
| 会话持久化    | SQLite (Python sqlite3) | 会话数据自动保存，断线恢复 |
| 向量检索      | HNSW + Sparse Inverted  | COSINE/IP + RRF 融合       |
| 可观测性      | LangSmith               | LLM 调用链路、Token 监控   |

## 会话持久化

`SessionStore` 将会话数据从内存迁移到 SQLite 数据库（`data/sessions.db`），实现断线恢复和跨进程会话管理。

### 数据库表结构

| 表              | 字段                                                                                       | 说明                   |
| --------------- | ------------------------------------------------------------------------------------------ | ---------------------- |
| `sessions`      | `id` (PK), `title`, `created_at`                                                           | 会话元数据             |
| `turns`         | `id` (PK), `session_id` (FK), `turn_index`, `user_input`, `assistant_output`, `created_at` | 对话记录，外键级联删除 |
| `session_state` | `session_id` (UNIQUE, FK), `entity_slots` (JSON), `last_report`, `updated_at`              | 实体槽位 + 上一轮报告  |

### 自动保存时机

| 时机           | 保存内容                                            |
| -------------- | --------------------------------------------------- |
| 每轮对话完成后 | `save_turn()` — 用户输入 + 助手输出                 |
| 每轮对话完成后 | `save_state()` — entity_tracker.slots + last_report |
| 首轮对话       | 自动更新标题（取用户输入前 20 字）                  |
| 会话创建时     | `create_session()` — 插入 sessions + session_state  |
| 会话删除时     | `delete_session()` — 级联删除三张表                 |

### 新增 API

| 接口                       | 方法   | 说明                    |
| -------------------------- | ------ | ----------------------- |
| `/api/sessions`            | GET    | 返回历史会话列表        |
| `/api/session?session_id=` | DELETE | 删除指定会话（级联）    |
| `/api/session/new`         | POST   | 生成并返回新 session_id |

### 容错设计

- 写入失败仅记录 Warning 日志，不阻断 Agent 主流程
- `load_session()` 返回 `None` 时自动创建新会话，兼容旧数据
- JSON 反序列化失败时回退为空字典 `{}`，不崩溃

## 前端对话存储（localStorage）

前端使用浏览器 localStorage 存储对话的完整 HTML（含思考过程），实现**刷新页面后无需调 API 即可恢复 UI**。

| 存储键                | 说明                                                                   |
| --------------------- | ---------------------------------------------------------------------- |
| `chatHistory`         | 对话列表（`[{id, title, time}, ...]`），用于左侧栏展示                 |
| `chatMessages_conv_*` | 单个对话的完整消息（`[{role, content, thinking}, ...]`），含 HTML 片段 |

### 保存时机

- 每轮 SSE 流结束后（`done` 事件）→ `saveCurrentConversation()`
- 将 assistant 消息的 `.thinking-container` 完整 HTML 保存到 `thinking` 字段
- 用户切换到其他对话前 → 先保存当前对话

### 刷新恢复流程

```
页面刷新 → initChatHistory()
  → 读取 localStorage → 渲染对话列表
  → 用户点击某个对话 → loadConversation(convId)
    → 读取 chatMessages_conv_xxx
    → 遍历消息：assistant 消息前插入 thinking-container（思考过程）
    → 渲染消息内容到 chatContainer
    → 不调任何后端 API，纯前端渲染
```

### 限制

- localStorage 是浏览器本地存储，**换浏览器/换电脑/清缓存后消失**
- 后端 SQLite（`data/sessions.db`）是真正的永久存储
- 两者互补：localStorage 负责 UI 快照恢复，SQLite 负责跨设备/跨会话恢复

## OpenAI SDK 迁移

项目已从原生 `requests` HTTP 调用迁移至官方 `openai` Python SDK，实现全异步链路。

### 迁移变更

| 原方式                            | 新方式                                                   |
| --------------------------------- | -------------------------------------------------------- |
| `requests.post(..., stream=True)` | `await client.chat.completions.create(..., stream=True)` |
| 手动解析 JSON 字典                | SDK 返回强类型 Chunk 对象                                |
| 手动拼接 SSE 流                   | `async for chunk in stream` 迭代                         |
| 同步 `queue.Queue`                | 异步 `asyncio.Queue` + `asyncio.create_task`             |

### Client 工厂模式

为保证"前端修改配置保存即生效"，**不在全局静态初始化 Client**，而是每次调用时动态创建：

```python
# config.py
def get_llm_client() -> AsyncOpenAI:
    return wrappers.wrap_openai(AsyncOpenAI(
        base_url=get_llm_base_url().rstrip("/"),  # 只需填到 /v1
        api_key=get_llm_api_key() or "not-needed",
    ))
```

### URL 补全规则

前端填写 URL 只需到 `/v1`，后端自动补全：

| 模型类型  | 前端填写                 | 后端处理                               |
| --------- | ------------------------ | -------------------------------------- |
| LLM       | `http://x.x.x.x:port/v1` | SDK 自动拼接 `/chat/completions`       |
| Embedding | `http://x.x.x.x:port/v1` | SDK 自动拼接 `/embeddings`             |
| Rerank    | `http://x.x.x.x:port/v1` | `_normalize_base_url()` 补全 `/rerank` |

### 依赖

```txt
openai>=1.0.0        # OpenAI 官方 SDK
requests>=2.28.0     # 保留用于 Rerank 等非 OpenAI 兼容调用
```

## LangSmith 可观测性

项目已全面接入 LangSmith 监控平台，自动追踪 LLM 调用链路、Token 消耗和工具执行耗时。

### 埋点位置

| 层级      | 文件                   | 装饰器/Wrapper                  | Trace 名称                  |
| --------- | ---------------------- | ------------------------------- | --------------------------- |
| SDK 层    | `config.py`            | `wrappers.wrap_openai()`        | 自动追踪 LLM/Embedding 调用 |
| Chain 层  | `pipeline.py`          | `@traceable(run_type="chain")`  | `Agent_Main_Pipeline`       |
| Prompt 层 | `prompt/builder.py`    | `@traceable(run_type="prompt")` | `Build_Medical_Prompt`      |
| Tool 层   | `tools/rag_tool.py`    | `@traceable(run_type="tool")`   | `Tool_RAG_Search`           |
| Tool 层   | `tools/edit_tool.py`   | `@traceable(run_type="tool")`   | `Tool_Edit_Report`          |
| Tool 层   | `tools/refine_tool.py` | `@traceable(run_type="tool")`   | `Tool_Refine_Report`        |
| Tool 层   | `rag/rerank.py`        | `@traceable(run_type="tool")`   | `SiliconFlow_Rerank`        |

### Metadata 注入

每次请求自动将以下上下文注入 LangSmith，支持后台按条件筛选：

```python
run_tree.add_metadata({
    "session_id": session_id,
    "modality": entity_tracker.slots.get("modality", ""),
    "body_part": entity_tracker.slots.get("body_part", ""),
})
```

### 环境变量

```env
LANGCHAIN_API_KEY=lsv2_pt_xxx
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_TRACING_V2=true
LANGCHAIN_PROJECT=Medical-Agent-v2
```

### 查看监控

访问 [https://smith.langchain.com](https://smith.langchain.com)，进入 `Medical-Agent-v2` 项目即可查看：

- 完整的 Agent 调用链路图
- 每次 LLM 调用的 Token 消耗和耗时
- 工具执行的输入/输出和耗时
- 按 `modality`、`body_part` 筛选特定场景的日志
