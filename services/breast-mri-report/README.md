# Breast MRI 报告工作台

乳腺 MRI 结构化报告生成工作台，作为 `report-generation` 主项目的内部微服务运行。

## 快速开始

### 自动启动（推荐）

直接启动主项目，FastAPI 会在 startup 时自动拉起本服务：

```bash
cd report-generation
python -m app.chat.server --web
```

### 手动启动

```bash
cd services/breast-mri-report
python start.py --open
```

## 环境变量

| 变量                  | 说明                                             | 默认值      |
| --------------------- | ------------------------------------------------ | ----------- |
| `PORT`                | 服务端口                                         | `4173`      |
| `HOST`                | 监听地址                                         | `127.0.0.1` |
| `VLLM_BASE_URL`       | LLM API 地址（自动从主项目 config.py 读取）      | —           |
| `VLLM_MODEL`          | LLM 模型名（自动从主项目 config.py 读取）        | —           |
| `VLLM_API_KEY`        | LLM API Key（可选，自动从主项目 config.py 读取） | —           |
| `BASIC_AUTH_USER`     | Basic Auth 用户名（可选，不配则无认证）          | —           |
| `BASIC_AUTH_PASSWORD` | Basic Auth 密码（配了 USER 必须同时配）          | —           |

## 依赖

- Node.js >= 18
- 纯 Node.js 原生模块，无 npm 依赖
- 前端为原生 JS + ES Module，无框架依赖

## 技术栈

- **后端**：Node.js 原生 HTTP + ES Module
- **前端**：原生 JS + ES Module + CSS
- **领域逻辑**：`src/domain/` 可被 Node 和浏览器直接 import
- **LLM 调用**：OpenAI 兼容 API（通过主项目配置的 LLM 服务）
