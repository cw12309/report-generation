# API 上线使用指南

API 上线允许你选择一个或多个知识库，将它们发布为一个支持多轮上下文的 Agent API，供外部系统通过 HTTP + SSE 调用。

## 1. 快速开始

```text
选择 Knowledge Base
      ↓
创建 API
      ↓
保存 API Key
      ↓
复制 Endpoint
      ↓
发送第一条消息
      ↓
取得 conversation_id
      ↓
后续请求复用 conversation_id
```

最重要的两件事：创建或轮换后立即安全保存完整 API Key；第一次调用后保存服务端返回的 `conversation_id`。

## 2. 创建一个 API

1. 打开左侧导航中的“API 上线”。
2. 点击“创建 API”。
3. 输入名称。
4. 按需填写描述。
5. 选择一个或多个 Active Knowledge Base。
6. 点击“创建并上线”。

Deployment 创建后，Knowledge Base Scope 会固定绑定。外部客户端不能在请求中使用 `selected_kbs` 指定或切换其他 Knowledge Base。需要调整范围时，请在“API 上线”页面编辑 Deployment。

## 3. Knowledge Base 是什么

选择哪些 KB，决定这个 API 可以从哪些医学模板库中检索。例如：

```text
胸部 CT API
绑定：
- 胸部 CT 模板
- 肺结节模板
```

这个 API 后续只能在上述 KB 范围内工作。创建和编辑 Deployment 时只允许选择状态为 Active 的 KB。

## 4. API Key

创建成功后，一次性弹窗会显示类似下面的完整密钥：

```text
rg_live_xxxxxxxxxxxxx
```

这是当前 Deployment 的认证密钥。**完整 API Key 只显示一次。**关闭弹窗后，列表和详情只能看到 Prefix，例如：

```text
rg_live_abcd****
```

如果 Key 丢失，无法恢复原 Key。请使用“重新生成密钥”创建新 Key。

## 5. API Key 安全

不要把 API Key：

- 提交到 Git；
- 写入公开 JavaScript；
- 发到聊天群；
- 写入 URL；
- 记录到公开日志。

推荐由调用方服务器的环境变量或 Secret Manager 安全保存，并仅在服务端发起 Deployment API 请求。

## 6. Endpoint

Deployment Detail 中会显示相对 Endpoint：

```text
POST /api/v1/deployments/{deployment_id}/chat
```

真实调用地址通常是：

```text
https://YOUR_HOST/api/v1/deployments/{deployment_id}/chat
```

请求必须包含：

```text
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json
```

请将 `YOUR_HOST`、`{deployment_id}` 和 `YOUR_API_KEY` 替换为部署环境提供的值。

## 7. 第一次调用

第一次请求只需要传 `message`，不需要传 `conversation_id`：

```bash
curl -N \
  -X POST "https://YOUR_HOST/api/v1/deployments/dep_xxx/chat" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "CT头颅，考虑脑出血"
  }'
```

`-N` 会关闭 cURL 的输出缓冲，便于立即看到 SSE 事件。

## 8. SSE 是什么

这个 API 不会一次性返回单个 JSON，而是使用 Server-Sent Events 逐步返回处理状态和结果：

```text
data: {"type":"conversation","conversation_id":"conv_xxx"}

data: {"type":"status","stage":"retrieving","message":"正在检索知识库"}

data: {"type":"report","content":"...","data":{"results":[]}}

data: {"type":"done","conversation_id":"conv_xxx"}

data: [DONE]
```

客户端应逐行读取以 `data:` 开头的内容，并在收到 `[DONE]` 后结束本轮读取。

## 9. conversation_id

第一次请求开始时，服务端首先返回：

```json
{
  "type": "conversation",
  "conversation_id": "conv_xxx"
}
```

调用方应保存 `conversation_id`。后续继续当前对话时，把它放回请求体。

## 10. 第二轮调用

假设第一轮发送“CT头颅，考虑脑出血”，并得到：

```text
conversation_id = conv_xxx
```

第二轮调用：

```bash
curl -N \
  -X POST "https://YOUR_HOST/api/v1/deployments/dep_xxx/chat" \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "把右侧病灶描述得详细一点",
    "conversation_id": "conv_xxx"
  }'
```

服务端会恢复上一轮对话、Entity Context、Last Report 和 Memory，因此可以连续交流。调用方不需要也不会接触内部 `session_id`。

## 11. 开启新对话

如果希望开始完全独立的新对话，不要传旧的 `conversation_id`：

```json
{
  "message": "新的问题"
}
```

服务端会生成新的 Conversation：

```text
同一个 conversation_id = 同一条多轮上下文
新的 conversation_id   = 新的独立对话
```

## 12. Public SSE Events

| Event | 含义 |
| --- | --- |
| `conversation` | 当前多轮 Conversation ID；每轮流开始时发送 |
| `status` | 当前处理阶段及说明 |
| `message` | 普通 Agent 回复 |
| `report` | 报告结果，可同时包含展示文本和结构化数据 |
| `ambiguous` | 检索结果存在诊断歧义，需要用户选择 |
| `error` | Agent 执行期间发生错误 |
| `done` | 本轮处理完成 |
| `[DONE]` | SSE 流结束标记 |

公开接口不会发送 `query_plan`、`recall`、`rerank`、`memory_retrieval` 或 `tool_executed` 等内部事件。

## 13. report Event

`report` 事件的真实公开结构为：

```json
{
  "type": "report",
  "content": "## 影像学表现\n...",
  "data": {
    "results": [
      {
        "影像学表现": "...",
        "诊断意见": "..."
      }
    ]
  }
}
```

`content` 是适合直接展示的文本。`data` 是可选的结构化报告对象，只有后端本轮取得结构化报告时才会出现；调用方应先判断字段是否存在。

## 14. message Event

如果 Agent 不需要生成报告，例如返回普通说明、补充问题或普通回复，可能发送：

```json
{
  "type": "message",
  "content": "请补充检查部位。"
}
```

## 15. ambiguous Event

如果检索结果存在诊断歧义，系统可能发送：

```json
{
  "type": "ambiguous",
  "options": ["脑出血", "钙化"],
  "message": "请选择一个诊断后继续"
}
```

选择后，在同一个 Conversation 中发起下一次请求：

```json
{
  "message": "继续生成报告",
  "conversation_id": "conv_xxx",
  "selected_diagnosis": "脑出血"
}
```

`selected_diagnosis` 必须来自当前 `ambiguous.options`。

## 16. Python 完整示例

安装依赖：

```bash
pip install requests
```

下面的示例会发起第一轮、读取 SSE、保存 `conversation_id`、处理 `message/report/ambiguous/error`，再发起第二轮：

```python
import json
import requests

API_URL = "https://YOUR_HOST/api/v1/deployments/dep_xxx/chat"
API_KEY = "YOUR_API_KEY"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def chat(message, conversation_id=None, selected_diagnosis=None):
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if selected_diagnosis:
        payload["selected_diagnosis"] = selected_diagnosis

    current_conversation_id = conversation_id

    with requests.post(
        API_URL,
        headers=HEADERS,
        json=payload,
        stream=True,
        timeout=600,
    ) as response:
        response.raise_for_status()
        response.encoding = "utf-8"

        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue

            raw = line[5:].lstrip()
            if raw == "[DONE]":
                break

            event = json.loads(raw)
            event_type = event.get("type")

            if event_type == "conversation":
                current_conversation_id = event["conversation_id"]
                print("conversation_id:", current_conversation_id)
            elif event_type == "message":
                print(event.get("content", ""))
            elif event_type == "report":
                print(event.get("content", ""))
                structured_report = event.get("data")
                if structured_report:
                    print(json.dumps(structured_report, ensure_ascii=False, indent=2))
            elif event_type == "ambiguous":
                print("请选择诊断:", event.get("options", []))
            elif event_type == "error":
                raise RuntimeError(
                    f"{event.get('code', 'AGENT_ERROR')}: "
                    f"{event.get('message', 'API error')}"
                )

    if not current_conversation_id:
        raise RuntimeError("服务端未返回 conversation_id")
    return current_conversation_id


conversation_id = chat("CT头颅，考虑脑出血")

chat(
    "把右侧病灶描述得详细一点",
    conversation_id=conversation_id,
)
```

## 17. Enable / Disable

### Disable

停用后，该 API 立即停止接受外部调用，但 Deployment 配置和历史 Conversation 仍然保留。

### Enable

启用后恢复外部调用。恢复前请同时确认 Health 为 Healthy。

## 18. Rotate API Key

以下情况应轮换 Key：

- Key 泄漏；
- Key 丢失；
- 定期安全轮换。

点击“重新生成密钥”并确认后会显示一次新 Key。**生成新 Key 后，旧 Key 立即失效。**所有调用方都必须及时更新安全存储中的 Key。

## 19. Delete

当前 Delete 是 Soft Delete。删除 Deployment 后：

- API 不能继续调用；
- 不删除绑定的 Knowledge Base；
- 历史 Conversation 数据保留。

公开调用不会透露已删除 Deployment 是否存在；它会像无效 Key 一样返回认证失败。

## 20. Health / Degraded

- Healthy：至少绑定一个 KB，并且所有绑定 KB 都是 Active。
- Degraded：没有可用绑定，或至少一个绑定 KB 不是 Active。

详情中的不可用 KB 会携带后端实际状态，例如 `uploaded`、`processing`、`failed` 或 `missing`。删除 KB 后，原绑定项可能显示为 `missing`。Degraded 时 Public API 会拒绝请求并返回 `409 DEPLOYMENT_KB_UNAVAILABLE`，不会自动切换到其他 KB。

## 21. 常见问题

### 为什么创建 API 时看不到某个 Knowledge Base？

创建和编辑 Deployment 时只显示 Active KB。请先完成该 KB 的构建并确认状态为 Active。

### API Key 关掉之后怎么看？

无法再次查看完整 Key，只能点击“重新生成密钥”进行 Rotate。

### 为什么返回 401？

可能没有传 `Authorization`、Bearer 格式不正确、Key 错误、Key 已轮换，或者 Deployment 不存在/已删除。

### 为什么 API 之前能用现在不能用？

检查 Deployment 是否 Disabled、Health 是否 Degraded，以及绑定 KB 是否仍为 Active。

### 怎么开启新对话？

不传旧的 `conversation_id`。

### 怎么保持多轮？

复用同一个 `conversation_id`。

### 可以在 Request 中指定 selected_kbs 吗？

不能。请求模型拒绝额外字段，KB 范围由 Deployment 固定。

### 为什么不是普通 JSON Response？

因为接口使用 SSE 依次返回 Conversation ID、处理状态和最终结果。

## 22. 常见错误

| HTTP | Code / Detail | 含义与处理 |
| --- | --- | --- |
| 401 | `缺少 Bearer Token` / `无效的 Bearer Token` | 检查 `Authorization: Bearer YOUR_API_KEY` 的存在和格式 |
| 401 | `INVALID_DEPLOYMENT_KEY` | Key 无效、已轮换，或 Deployment 不存在/已删除 |
| 403 | `DEPLOYMENT_DISABLED` | Deployment 已停用；确认后在管理页重新启用 |
| 409 | `DEPLOYMENT_KB_UNAVAILABLE` | 绑定 KB 不可用；查看 Degraded 原因并恢复 KB |
| 404 | `CONVERSATION_NOT_FOUND` | 当前 Deployment 下找不到该 `conversation_id`；检查 ID，或不传 ID 开启新对话 |
| 422 | Validation Error / `message 不能为空` | 请求字段缺失、超长、存在 `selected_kbs` 等额外字段，或 `message` 为空 |
| 404 | `Deployment 不存在` | 管理接口查询或修改了不存在/已删除的 Deployment |

HTTP 建连成功后，Agent 执行期间的错误通过 SSE `error` 事件返回，其中包含 `code` 和 `message`。

## 23. 1-Minute Cheat Sheet

```text
创建：
选择 Active KB → 创建 API → 保存 API Key

调用：
POST Deployment Endpoint
Authorization: Bearer YOUR_API_KEY

第一轮：
只传 message

保存：
conversation_id

下一轮：
message + conversation_id

新会话：
不传旧 conversation_id

Key 丢失：
Rotate

暂停 API：
Disable
```
