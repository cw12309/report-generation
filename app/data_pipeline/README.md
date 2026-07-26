# Data Pipeline

该目录负责将 Excel 医疗报告模板整理为 Markdown，并构建 Milvus
Dense + Sparse 混合向量知识库。

## 处理流程

```text
report_template/*.xlsx
        |
        v
process_reports.py
        |
        v
output_markdown/**/*.md
        |
        +----------------------+
        |                      |
        v                      v
extract_metadata.py    build_vector_db.py
        |                      |
        v                      v
metadata.json          Milvus: medical_reports
```

## 目录说明

```text
data_pipeline/
├── report_template/       # 输入的 Excel 报告模板
├── output_markdown/       # process_reports.py 生成的 Markdown
├── process_reports.py     # Excel 转 Markdown
├── extract_metadata.py    # 从 Markdown 提取元数据
├── build_vector_db.py     # 构建 Dense + Sparse Milvus 知识库
└── README.md
```

`report_template` 和 `output_markdown` 已加入 `.gitignore`。

所有模型、数据路径及 Milvus 连接配置统一位于项目根目录的
`config.yml`。

## 1. 生成 Markdown

将 `report_template` 中的所有 `.xlsx` 文件整理为 Markdown：

```powershell
python app/data_pipeline/process_reports.py
```

主要输出：

```text
app/data_pipeline/output_markdown/
app/data_pipeline/报告整理结果.md
```

输出目录结构由以下标准字段决定：

```text
modality / level_1 / level_2 / level_3 / level_4
```

`module_name` 用作 Markdown 文件名。同一目录下模块名称和内容均相同
时跳过重复数据；模块名称相同但内容不同时，生成 `_1`、`_2` 等编号文件。

## 2. 提取元数据

从 `output_markdown` 中所有 Markdown 的 YAML Front Matter 汇总目录层级：

```powershell
python app/data_pipeline/extract_metadata.py
```

默认输出：

```text
app/data_pipeline/metadata.json
```

JSON 记录检查类型及完整目录层级，不记录 Markdown 文件路径：

```text
检查类型
└── 一级目录
    └── 二级目录
        └── 三级目录
            └── 四级目录
```

空层级会跳过。模块名称和 Markdown 文件路径不会写入该文件。

常用参数：

```powershell
python app/data_pipeline/extract_metadata.py --stats
python app/data_pipeline/extract_metadata.py --sample 5
python app/data_pipeline/extract_metadata.py --no-save
```

## 3. 构建 Milvus 知识库

运行：

```powershell
python app/data_pipeline/build_vector_db.py
```

该命令会：

1. 扫描 `output_markdown` 中的全部 Markdown。
2. 解析报告元数据、影像学表现和影像学意见。
3. 调用配置的 BGE-M3 服务生成 1024 维 Dense 向量。
4. 根据报告文本生成 Sparse 词面向量。
5. 删除已存在的 `medical_reports` Collection。
6. 使用最新 Schema 和索引重新创建 Collection。
7. 写入所有报告并 Load Collection。

注意：当前建库采用全量重建模式，不是增量追加。执行前请确认允许删除
远程 Milvus 中已有的 `medical_reports` Collection。

### 进度输出

Dense 向量按批次显示进度：

```text
Dense向量化: 640/2800 (22.9%)
```

Sparse 向量每 100 条显示一次进度：

```text
Sparse向量化: 700/2800 (25.0%)
```

可以调整 Dense 批次大小：

```powershell
python app/data_pipeline/build_vector_db.py --batch-size 16
```

## Milvus Schema

Collection 名称：

```text
medical_reports
```

| 字段               | 类型                | 说明                   |
| ------------------ | ------------------- | ---------------------- |
| `id`               | INT64               | Milvus 自动生成的主键  |
| `modality`         | VARCHAR(32)         | 检查类型               |
| `level_1`          | VARCHAR(64)         | 一级目录               |
| `level_2`          | VARCHAR(64)         | 二级目录               |
| `level_3`          | VARCHAR(64)         | 三级目录               |
| `level_4`          | VARCHAR(64)         | 四级目录               |
| `module_name`      | VARCHAR(128)        | 模块名称               |
| `imaging_findings` | VARCHAR(4096)       | 影像学表现             |
| `imaging_opinion`  | VARCHAR(4096)       | 影像学意见             |
| `combined_text`    | VARCHAR(8192)       | 用于检索的完整组合文本 |
| `dense_vector`     | FLOAT_VECTOR(1024)  | BGE-M3 语义向量        |
| `sparse_vector`    | SPARSE_FLOAT_VECTOR | 词面稀疏向量           |

## 索引

| 字段            | 索引                  | 参数                             |
| --------------- | --------------------- | -------------------------------- |
| `modality`      | INVERTED              | 标量过滤                         |
| `level_1`       | INVERTED              | 标量过滤                         |
| `level_2`       | INVERTED              | 标量过滤                         |
| `level_3`       | INVERTED              | 标量过滤                         |
| `level_4`       | INVERTED              | 标量过滤                         |
| `dense_vector`  | HNSW                  | COSINE, M=16, efConstruction=256 |
| `sparse_vector` | SPARSE_INVERTED_INDEX | IP, drop_ratio_build=0.2         |

## Hybrid Search

检索时分别构造 Dense 和 Sparse 的 `AnnSearchRequest`，两路请求使用相同
的标量过滤条件，并通过 `RRFRanker` 融合。

例如查询“CT 脑出血”时，可使用：

```text
modality == "CT" and level_1 == "头颅"
```

检索流程：

```text
查询文本
├── BGE-M3 Dense 向量 ──────┐
├── 词面 Sparse 向量 ───────┼── RRFRanker ── 最终候选
└── modality/level_1 过滤 ──┘
```

## 模拟数据测试

以下命令会重建 Collection、插入三条模拟报告，并执行一次带标量过滤的
Dense + Sparse Hybrid Search：

```powershell
python app/data_pipeline/build_vector_db.py --demo
```

该模式同样会删除远程 `medical_reports` Collection，请勿在正式知识库上
随意执行。
