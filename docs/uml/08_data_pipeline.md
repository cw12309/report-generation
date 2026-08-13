**Data Pipeline — 离线数据管线（Excel → Markdown → 向量库 → 元数据树）**

```mermaid
sequenceDiagram
    autonumber

    participant User as 👤 管理员
    participant Server as 🖥️ Server API
    participant PR as 📄 process_reports
    participant FS as 📁 文件系统
    participant BDB as 🗄️ build_vector_db
    participant Embed as 🧠 Embedding API
    participant Milvus as 🗃️ Milvus
    participant EM as 🏷️ extract_metadata
    participant SS as 💾 SessionStore

    rect rgb(240, 248, 255)
        Note over User,SS: 阶段 1 — Excel → Markdown 转换
    end

    User->>Server: 上传 Excel 文件
    Server->>PR: process_uploaded_excels(kb_id, xlsx_paths)
    PR->>PR: 解析 Excel → 标准化列名(FIELD_MAPPING)
    PR->>PR: 逐行 normalize_row → 生成层级路径
    PR->>FS: 创建文件夹结构<br/>(检查类型/一级/二级/...)
    PR->>FS: 写入 Markdown 文件<br/>(含 YAML Front Matter + chunk_id)

    alt 生成失败
        PR-->>Server: errors 列表
        Server-->>User: 返回错误信息
    else 生成成功
        PR-->>Server: chunks 列表
    end

    rect rgb(255, 248, 240)
        Note over User,SS: 阶段 2 — Markdown → 向量库
    end

    Server->>BDB: build_db(kb_id, markdown_dir)
    BDB->>BDB: parse_markdown → 提取字段<br/>(modality/层级/表现/意见)
    BDB->>BDB: ensure_collection_schema<br/>(验证/创建 Milvus Collection)

    alt Collection 不存在
        BDB->>Milvus: create_collection (Dense + Sparse Schema)
    else Schema 不兼容
        BDB-->>Server: Schema 迁移错误
        Server-->>User: 提示执行 migrate_milvus_schema.py
    end

    BDB->>Embed: Dense Embedding (批次请求)
    Embed-->>BDB: 密集向量

    BDB->>BDB: Sparse 向量化 (BM25)

    BDB->>Milvus: 先删除旧 kb_id 实体<br/>再批量插入新向量

    alt 写入失败
        BDB-->>Server: 错误
        Server-->>User: 构建失败
    else 写入成功
        BDB-->>Server: 写入条数
    end

    rect rgb(240, 255, 240)
        Note over User,SS: 阶段 3 — 元数据提取
    end

    Server->>EM: extract_all(kb_id, output_dir)
    EM->>FS: 扫描 Markdown 文件
    EM->>EM: extract_frontmatter → 提取 YAML 元数据
    EM->>EM: _insert_metadata → 构建层级树
    EM->>FS: 写入 metadata.json

    alt 写入失败
        EM-->>Server: 错误
    else 写入成功
        EM-->>Server: 元数据树
    end

    rect rgb(255, 255, 240)
        Note over User,SS: 阶段 4 — 状态更新
    end

    Server->>SS: update_knowledge_base(status="active", progress=100)
    Server-->>User: 知识库就绪 ✅
```

---

**参与组件**

| 组件 | 职责 |
|------|------|
| `process_reports.py` | Excel 解析 → 层级文件夹 → Markdown（含 Front Matter） |
| `build_vector_db.py` | Markdown 解析 → Dense+Sparse 向量化 → Milvus 写入 |
| `extract_metadata.py` | 扫描 Markdown Front Matter → 构建层级元数据树 → `metadata.json` |
| `chunk_id.py` | 三端统一的 chunk_id 生成（MD5） |
| `rebuild_all_knowledge_bases.py` | 全量重建编排：Markdown + SQLite chunks + Milvus |
| `migrate_milvus_schema.py` | Milvus Schema 检查与迁移工具 |

---

**全量重建流程（rebuild_all_knowledge_bases.py）**

```mermaid
flowchart TD
    A["管理员执行 --confirm"] --> B["rebuild_local_artifacts"]
    B --> B1["遍历 SQLite 知识库列表"]
    B1 --> B2["process_uploaded_excels → 生成 Markdown"]
    B2 --> B3["extract_all → 生成 metadata.json"]
    B3 --> B4["replace_knowledge_base_chunks → SQLite"]
    B4 --> B5{"所有 KB 完成？"}
    B5 -->|否| B1
    B5 -->|是| C["recreate_collection"]
    C --> C1["DROP Collection"]
    C1 --> C2["create_collection (新 Schema)"]
    C2 --> D["build_db (全量 Markdown → Milvus)"]
    D --> E{"SQLite 条数 == Milvus 条数？"}
    E -->|否| ERR["数量不一致，状态→failed"]
    E -->|是| F["update_knowledge_base(status=active)"]
    F --> G["✅ 全量重建完成"]
```

---

**Schema 字段（Milvus Collection）**

| 字段 | 类型 | 用途 |
|------|------|------|
| `kb_id` | VARCHAR(64) | 知识库标识 |
| `chunk_id` | VARCHAR(32) | 三端统一 MD5 标识 |
| `modality` | VARCHAR(32) | 检查类型（CT/MR/...） |
| `level_1~4` | VARCHAR(64) | 层级目录 |
| `module_name` | VARCHAR(128) | 模块名称 |
| `imaging_findings` | VARCHAR(4096) | 影像学表现 |
| `imaging_opinion` | VARCHAR(4096) | 影像学意见 |
| `combined_text` | VARCHAR(8192) | 组合文本（检索用） |
| `dense_vector` | FLOAT_VECTOR | 密集向量（语义检索） |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | 稀疏向量（关键词检索） |

---

**与运行时管线的衔接**

```
Data Pipeline 产出           →  运行时管线消费
─────────────────────────────────────────────────
Milvus 向量库                →  rag/retrieval.py 检索
metadata.json                →  rag/metadata_navigator.py 导航
                           →  rag/query_planner.py 查询规划
                           →  memory/ 生成实体配置
Markdown 文件                →  build_vector_db.py 增量构建
SQLite chunks               →  memory/session_store.py 管理
```