# Breast MRI 报告工作台 (AI Coding Agent 导航)

## File Map

| 路径                | 角色       | 说明                                                      |
| ------------------- | ---------- | --------------------------------------------------------- |
| `start.py`          | 启动器     | 独立启动脚本，自动从主项目 config.py 读取 LLM 配置        |
| `launcher.py`       | 生命周期   | 供主项目 server.py 调用的进程管理模块 (start/stop/health) |
| `package.json`      | 元信息     | Node 项目描述，含 `npm start` 入口                        |
| `index.html`        | 前端入口   | 乳腺 MRI 报告工作台 SPA 入口                              |
| `styles.css`        | 前端样式   | 全局样式                                                  |
| `demo.html`         | 演示页     | 独立演示页面                                              |
| `assets/`           | 静态资源   | 图片、图标等                                              |
| `scripts/serve.mjs` | HTTP 服务  | Node.js 原生 HTTP 服务，路由分发 + 静态文件               |
| `src/app.js`        | 前端逻辑   | 前端主逻辑，ES Module                                     |
| `src/domain/`       | 领域逻辑   | 5 个文件，核心领域逻辑（ES Module，Node/Browser 共用）    |
| `src/server/`       | 服务端逻辑 | 3 个文件                                                  |

## 服务端架构

```
scripts/serve.mjs          ← HTTP 入口，路由分发
  ├── src/server/agentService.mjs   ← Agent 核心：LLM 调用 + 结构化提取
  ├── src/server/httpSecurity.mjs   ← CSP / Basic Auth / 安全头
  └── src/server/staticFiles.mjs    ← 静态文件服务 + MIME 类型
```

## API 路由

| 路由                | 方法 | 说明                                                  |
| ------------------- | ---- | ----------------------------------------------------- |
| `/api/health`       | GET  | 健康检查 `{status, service, release}`                 |
| `/api/model-status` | GET  | LLM 模型连通性检查                                    |
| `/api/agent/turn`   | POST | Agent 对话，`{message, currentCase}` → 结构化提取结果 |
| `/*`                | GET  | 静态文件服务                                          |

## 配置来源

```
主项目 config.yml
  └── breast_mri:
        host: "127.0.0.1"
        port: 4173
              │
              ▼
主项目 config.py
  ├── _env_or_config()              ← 优先级：.env 非空 → config.yml → 报错
  ├── get_breast_mri_host()         → HOST
  ├── get_breast_mri_port()         → PORT（校验 1-65535）
  ├── get_breast_mri_llm_base_url() → VLLM_BASE_URL（已规范化为 /v1 格式）
  ├── get_breast_mri_llm_model()    → VLLM_MODEL
  └── get_breast_mri_llm_api_key()  → VLLM_API_KEY
         │
         ▼
    start.py / launcher.py（注入环境变量）
         │
         ▼
    Node process.env.PORT / HOST / VLLM_BASE_URL / VLLM_MODEL / VLLM_API_KEY
         │
         ▼
    serve.mjs（无硬编码默认值，PORT/HOST 缺失时直接报错退出）
    agentService.mjs（无硬编码默认值，LLM 配置缺失时 Agent 功能不可用）
```

## 启动方式

| 方式         | 命令                                               | 说明                                   |
| ------------ | -------------------------------------------------- | -------------------------------------- |
| 自动（推荐） | 启动主项目 FastAPI                                 | server.py startup 自动拉起 Node 子进程 |
| 手动         | `cd services/breast-mri-report && python start.py` | 独立调试用                             |
| Docker       | `docker compose -f compose.production.yml up`      | 容器化部署                             |

> **注意：** 不要直接运行 `npm start` 或 `node scripts/serve.mjs`。`PORT` 和 `HOST` 由启动器从主项目配置注入，直接运行会因缺少环境变量而报错退出。

## 与主项目的关系

- 作为主项目的**可选内部微服务**，失败不影响 Agent/RAG/Eval/Deployment
- 主项目通过 FastAPI 代理层提供同源访问：
  - `/api/breast-mri/health` → 代理到 Node `/api/health`
  - `/api/breast-mri/turn` → 代理到 Node `/api/agent/turn`
  - `/breast-mri/*` → 代理到 Node 静态文件
- Node 仅监听 `127.0.0.1`，不对外暴露

## 注意事项

- 需要 Node.js >= 18
- 纯 Node.js 原生模块，无 npm 依赖
- 前端为原生 JS + ES Module，无框架依赖
- `src/domain/` 可被 Node 和浏览器直接 import
- PORT/HOST 由启动器注入，`serve.mjs` 无硬编码默认值，缺失时直接退出
- LLM 配置缺失时 Agent 功能不可用，但服务本身仍可启动（健康检查正常）
- 端口被未知程序占用时抛出 `BREAST_MRI_PORT_CONFLICT`，不会自动 kill 进程
