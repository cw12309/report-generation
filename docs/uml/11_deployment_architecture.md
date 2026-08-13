**Deployment Architecture — 部署架构（Gateway → Worker 拓扑）**

```mermaid
sequenceDiagram
    autonumber

    participant Client as 🌐 外部客户端
    participant Gateway as 🚪 Gateway (8000)
    participant Worker as ⚙️ Worker (8001)

    rect rgb(240, 248, 255)
        Note over Client,Worker: 公开 API — 报告生成
    end

    Client->>Gateway: POST /api/v1/report/generate<br/>Authorization: Bearer <api_key>
    Gateway->>Gateway: verify_api_key (Bearer Token)
    Gateway->>Worker: POST /internal/report/generate<br/>(转发原请求)
    Worker->>Worker: run_pipeline → 03 运行时管线
    Worker-->>Gateway: SSE 流式响应
    Gateway-->>Client: SSE 流式响应

    rect rgb(255, 248, 240)
        Note over Client,Worker: 公开 API — RAG 检索
    end

    Client->>Gateway: POST /api/v1/rag/search<br/>Authorization: Bearer <api_key>
    Gateway->>Gateway: verify_api_key
    Gateway->>Worker: POST /internal/rag/search
    Worker-->>Gateway: 检索结果
    Gateway-->>Client: 检索结果

    rect rgb(240, 255, 240)
        Note over Client,Worker: 公开 API — Deployment Chat
    end

    Client->>Gateway: POST /api/v1/deployments/{id}/chat<br/>Authorization: Bearer <deployment_key>
    Gateway->>Gateway: extract_deployment_api_key → X-Deployment-API-Key
    Gateway->>Worker: POST /internal/deployments/{id}/chat<br/>X-Deployment-API-Key: <deployment_key>
    Worker->>Worker: 校验 Deployment Key → 执行对话
    Worker-->>Gateway: SSE 流式响应
    Gateway-->>Client: SSE 流式响应

    rect rgb(255, 255, 240)
        Note over Client,Worker: 内部 API — 直接访问 Worker
    end

    Client->>Worker: POST /api/chat (对话)
    Worker->>Worker: 执行完整运行时管线
    Worker-->>Client: SSE 流式响应

    Client->>Worker: POST /api/kb/upload (知识库上传)
    Worker->>Worker: 08 数据管线
    Worker-->>Client: 构建结果

    Client->>Worker: POST /api/eval/run (评测)
    Worker->>Worker: 09 评测管线
    Worker-->>Client: SSE 流式结果
```

---

**拓扑结构**

```mermaid
flowchart LR
    subgraph External["🌐 外部网络"]
        Client["客户端<br/>(浏览器/API)"]
    end

    subgraph Docker["🐳 Docker Compose"]
        subgraph Gateway["Gateway :8000"]
            Auth["API Key 鉴权"]
            Proxy["HTTP 代理"]
        end

        subgraph Worker["Worker :8001"]
            Server["FastAPI Server"]
            Pipeline["运行时管线<br/>(Phase 1/2/3)"]
            DataPipeline["数据管线<br/>(08)"]
            Eval["评测管线<br/>(09)"]
        end

        subgraph Storage["💾 存储"]
            Milvus[("Milvus<br/>向量库")]
            SQLite[("SQLite<br/>sessions.db")]
            Markdown[("Markdown<br/>报告文件")]
        end
    end

    Client -->|"公开 API<br/>(Bearer Token)"| Auth
    Auth -->|"内部转发<br/>(无需鉴权)"| Server
    Server --> Pipeline
    Server --> DataPipeline
    Server --> Eval
    Pipeline --> Milvus
    Pipeline --> SQLite
    DataPipeline --> Milvus
    DataPipeline --> Markdown
    Eval --> Milvus
    Eval --> SQLite
```

---

**Gateway 职责**

| 职责 | 说明 |
|------|------|
| 鉴权 | `verify_api_key` — 验证 Bearer Token |
| 代理 | 转发请求到 Worker，透传 SSE 流式响应 |
| 隔离 | Gateway 不导入任何 Worker 代码（模型/向量库等），纯 HTTP 代理 |
| Deployment 鉴权 | `extract_deployment_api_key` — 提取 Deployment Key 通过内部 Header 传给 Worker |

**Worker 路由**

| 路由前缀 | 用途 |
|----------|------|
| `/internal/*` | Gateway 转发的内部请求（无需鉴权） |
| `/api/chat` | 对话（直接访问） |
| `/api/kb/*` | 知识库管理 |
| `/api/eval/*` | 评测 |
| `/api/deployments/*` | Deployment 管理 |
| `/api/session/*` | 会话管理 |
| `/api/config` | 配置管理 |

---

**关键设计**

| 机制 | 说明 |
|------|------|
| 双层鉴权 | Gateway 验证 API Key（公开），Worker 内部路由无需鉴权 |
| Deployment 隔离 | 每个 Deployment 有独立 Key，通过 `X-Deployment-API-Key` Header 传递 |
| 流式透传 | Gateway 使用 `httpx.AsyncClient` 流式转发 SSE，不缓冲完整响应 |
| 超时保护 | Worker 请求超时 600s，Gateway 返回 504 |
| 独立部署 | Gateway 和 Worker 是两个独立进程，通过 Docker Compose 编排 |