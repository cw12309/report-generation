**Phase 1 — 输入处理（Pre-LLM）**

```mermaid
sequenceDiagram
    actor User
    participant CLI as CLI / Server
    participant Pipe as Pipeline
    participant ET as EntityTracker
    participant QR as QueryRewrite

    User->>CLI: "CT 头颅 脑出血"
    CLI->>Pipe: 启动管线处理

    Pipe->>ET: 从用户输入提取实体
    ET-->>Pipe: 检查类型:CT, 部位:头颅, 诊断:脑出血

    Pipe->>ET: 检测用户意图
    ET-->>Pipe: 新会话

    Pipe->>ET: 上下文消解（补全省略信息）
    ET-->>Pipe: "CT 头颅 脑出血"

    Pipe->>QR: 术语标准化
    QR-->>Pipe: "CT 头颅 脑出血"

    Pipe->>QR: 是否需要改写查询？
    QR-->>Pipe: 无需改写
```
