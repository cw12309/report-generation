"""Rebuild the medical report collection with dense and sparse vectors."""

import argparse
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import yaml
from pymilvus import (
    AnnSearchRequest,
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
    RRFRanker,
)

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import (  # noqa: E402
    get_collection_name,
    get_db_path,
    get_embed_client,
    get_embed_dimension,
    get_embed_model,
    get_markdown_dir,
    get_milvus_token,
)
from rag.sparse_vector import text_to_sparse_vector  # noqa: E402
from data_pipeline.chunk_id import generate_chunk_id  # noqa: E402
from milvus_compat import create_milvus_client  # noqa: E402

logger = logging.getLogger(__name__)

SCALAR_INDEX_FIELDS = (
    "kb_id",
    "chunk_id",
    "modality",
    "level_1",
    "level_2",
    "level_3",
    "level_4",
)


def _scalar_indexes_unsupported(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "create_index missing required 'metric_type' parameter" in message
        or "create_index only supports vector fields" in message
    )


def _uses_milvus_lite() -> bool:
    uri = str(get_db_path() or "").strip()
    if uri.lower().startswith(("http://", "https://")):
        return False
    return Path(uri).suffix.lower() in {".db", ".sqlite", ".sqlite3"}


def _ensure_scalar_indexes(
    client: MilvusClient,
    collection_name: str,
    field_names=SCALAR_INDEX_FIELDS,
) -> List[str]:
    """Best-effort scalar indexes without blocking Milvus Lite startup.

    Some Milvus Lite builds bundled with pymilvus 3.0 expose scalar-index APIs
    but reject scalar index creation at runtime. Filtering still works without
    these optional indexes, so only that known compatibility error is ignored.
    """
    if _uses_milvus_lite():
        logger.info("Milvus Lite 使用无标量索引兼容模式: %s", collection_name)
        return []

    indexes = client.list_indexes(collection_name)
    index_names = {getattr(index, "field_name", None) or str(index) for index in indexes}
    missing = [field_name for field_name in field_names if field_name not in index_names]
    if not missing:
        return []

    index_params = client.prepare_index_params()
    for field_name in missing:
        index_params.add_index(field_name=field_name, index_type="INVERTED")
    try:
        client.create_index(collection_name=collection_name, index_params=index_params)
    except Exception as exc:
        if not _scalar_indexes_unsupported(exc):
            raise
        logger.warning(
            "当前 Milvus 后端不支持标量索引，跳过可选 INVERTED 索引 %s: %s",
            missing,
            exc,
        )
        return []

    logger.info("已补建标量 INVERTED 索引 %s: %s", missing, collection_name)
    return missing


def _section(content: str, heading: str) -> str:
    marker = f"### {heading}"
    if marker not in content:
        return ""
    body = content.split(marker, 1)[1]
    if "\n### " in body:
        body = body.split("\n### ", 1)[0]
    return body.strip()


def parse_markdown(path: Path, kb_id_override: Optional[str] = None) -> Dict:
    """Parse one generated Markdown report into the collection fields."""
    content = path.read_text(encoding="utf-8")
    metadata: Dict[str, str] = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            loaded = yaml.safe_load(parts[1]) or {}
            metadata = {str(key): str(value or "") for key, value in loaded.items()}
            body = parts[2].strip()

    findings = _section(body, "影像学表现")
    opinion = _section(body, "影像学意见")
    combined_text = "\n".join(filter(None, [
        f"检查类型：{metadata.get('modality', '')}",
        f"一级目录：{metadata.get('level_1', '')}",
        f"二级目录：{metadata.get('level_2', '')}",
        f"三级目录：{metadata.get('level_3', '')}",
        f"四级目录：{metadata.get('level_4', '')}",
        f"模块名称：{metadata.get('module_name', '')}",
        f"影像学表现：{findings}" if findings else "",
        f"影像学意见：{opinion}" if opinion else "",
    ]))
    frontmatter_kb_id = metadata.get("kb_id", "")[:64]
    kb_id = (kb_id_override or frontmatter_kb_id)[:64]
    stored_chunk_id = metadata.get("chunk_id", "").lower()
    stored_id_is_usable = bool(re.fullmatch(r"[0-9a-f]{32}", stored_chunk_id))
    override_matches_frontmatter = not kb_id_override or kb_id == frontmatter_kb_id
    chunk_id = (
        stored_chunk_id
        if stored_id_is_usable and override_matches_frontmatter
        else generate_chunk_id(kb_id, path.name, combined_text)
    )
    return {
        "kb_id": kb_id,
        "chunk_id": chunk_id,
        "modality": metadata.get("modality", "")[:32],
        "level_1": metadata.get("level_1", "")[:64],
        "level_2": metadata.get("level_2", "")[:64],
        "level_3": metadata.get("level_3", "")[:64],
        "level_4": metadata.get("level_4", "")[:64],
        "module_name": metadata.get("module_name", "")[:128],
        "imaging_findings": findings[:4096],
        "imaging_opinion": opinion[:4096],
        "combined_text": combined_text[:8192],
    }


def get_dense_embeddings(
    texts: List[str],
    batch_size: int,
    progress_callback: Optional[Callable[[str], None]] = None,
    batch_progress_callback: Optional[Callable[[int, int], None]] = None,
) -> List[List[float]]:
    client = get_embed_client()
    embeddings: List[List[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        for attempt in range(5):
            try:
                response = client.embeddings.create(
                    model=get_embed_model(),
                    input=batch,
                    timeout=120,
                )
                items = sorted(response.data, key=lambda item: item.index)
                embeddings.extend(item.embedding for item in items)
                if progress_callback:
                    completed = min(start + len(batch), len(texts))
                    percentage = completed * 100 / len(texts)
                    progress_callback(
                        f"Dense向量化: {completed}/{len(texts)} ({percentage:.1f}%)"
                    )
                if batch_progress_callback:
                    batch_progress_callback(
                        min(start + len(batch), len(texts)), len(texts)
                    )
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep((attempt + 1) * 5)
    return embeddings


def _client() -> MilvusClient:
    return create_milvus_client(get_db_path(), get_milvus_token())


def create_collection(client: MilvusClient) -> None:
    """Create the exact scalar, dense, and sparse schema and its indexes."""
    schema = CollectionSchema(fields=[
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="modality", dtype=DataType.VARCHAR, max_length=32),
        FieldSchema(name="level_1", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="level_2", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="level_3", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="level_4", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="module_name", dtype=DataType.VARCHAR, max_length=128),
        FieldSchema(name="imaging_findings", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="imaging_opinion", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="combined_text", dtype=DataType.VARCHAR, max_length=8192),
        FieldSchema(
            name="dense_vector",
            dtype=DataType.FLOAT_VECTOR,
            dim=get_embed_dimension(),
        ),
        FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),
    ])

    indexes = client.prepare_index_params()
    indexes.add_index(
        field_name="dense_vector",
        index_type="HNSW",
        metric_type="COSINE",
        params={"M": 16, "efConstruction": 256},
    )
    indexes.add_index(
        field_name="sparse_vector",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP",
        params={"drop_ratio_build": 0.2},
    )
    client.create_collection(
        collection_name=get_collection_name(),
        schema=schema,
        index_params=indexes,
    )
    _ensure_scalar_indexes(client, get_collection_name())


def ensure_collection_schema(client: MilvusClient) -> dict:
    """Validate/create the shared multi-KB Collection without destructive changes.

    Milvus does not support adding a field to an existing Collection in place,
    so an old Collection is reported as a migration error instead of being
    silently dropped during application startup.
    """
    collection_name = get_collection_name()
    if not client.has_collection(collection_name):
        create_collection(client)
        return {"created": True, "valid": True, "missing_fields": []}

    description = client.describe_collection(collection_name)
    schema = description.get("schema", description)
    fields = schema.get("fields", []) if isinstance(schema, dict) else []
    field_names = {field.get("name") for field in fields}
    required_fields = {
        "kb_id", "chunk_id", "modality", "level_1", "level_2", "level_3", "level_4",
        "module_name", "imaging_findings", "imaging_opinion", "combined_text",
        "dense_vector", "sparse_vector",
    }
    missing_fields = sorted(required_fields - field_names)
    if missing_fields:
        raise RuntimeError(
            f"Milvus Collection {collection_name} 缺少字段 {missing_fields}。"
            "Milvus 不支持原地添加字段，请先执行 migrate_milvus_schema.py "
            "完成一次受控迁移。"
        )
    _ensure_scalar_indexes(client, collection_name, ("kb_id", "chunk_id"))
    return {"created": False, "valid": True, "missing_fields": []}


def build_db(
    input_dir: Optional[str] = None,
    batch_size: int = 32,
    rebuild: bool = False,
    kb_id: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
    build_progress_callback: Optional[Callable[[int, str], None]] = None,
) -> int:
    """Incrementally replace one logical KB in the shared collection."""
    def log(message: str, level: str = "info") -> None:
        print(message, flush=True)
        if progress_callback:
            progress_callback({"level": level, "msg": message})

    def report_progress(progress: int, message: str) -> None:
        """Report best-effort progress without interrupting vector generation."""
        if not build_progress_callback:
            return
        try:
            build_progress_callback(min(100, max(0, int(progress))), message)
        except Exception as exc:
            print(f"进度上报失败（已忽略）: {exc}", flush=True)

    base_dir = Path(input_dir or get_markdown_dir()).resolve()
    if not base_dir.is_dir():
        raise FileNotFoundError(f"Markdown目录不存在: {base_dir}")

    markdown_files = sorted(base_dir.rglob("*.md"))
    reports = [parse_markdown(path, kb_id_override=kb_id) for path in markdown_files]
    missing_kb = [path for path, report in zip(markdown_files, reports) if not report.get("kb_id")]
    if missing_kb:
        raise ValueError("Markdown 缺少 kb_id frontmatter，请传入 kb_id 或重新执行切片")
    reports = [report for report in reports if report["combined_text"]]
    log(f"发现有效Markdown报告: {len(reports)} 个")
    if not reports:
        raise ValueError("没有可写入 Milvus 的有效 Markdown 报告")
    report_progress(20, f"已加载 {len(reports)} 个有效报告")

    client = _client()
    collection_name = get_collection_name()
    try:
        if not client.has_collection(collection_name):
            create_collection(client)
            log(f"集合已创建: {collection_name}")
        def on_dense_progress(completed: int, total: int) -> None:
            progress = 20 + int((completed / total) * 50)
            report_progress(progress, f"Dense向量化: {completed}/{total}")

        dense_vectors = get_dense_embeddings(
            [report["combined_text"] for report in reports],
            batch_size,
            progress_callback=log,
            batch_progress_callback=on_dense_progress,
        )
        rows = []
        total_reports = len(reports)
        for index, (report, dense_vector) in enumerate(
            zip(reports, dense_vectors),
            start=1,
        ):
            rows.append({
                **report,
                "dense_vector": dense_vector,
                "sparse_vector": text_to_sparse_vector(report["combined_text"]),
            })
            if index % 100 == 0 or index == total_reports:
                percentage = index * 100 / total_reports
                log(f"Sparse向量化: {index}/{total_reports} ({percentage:.1f}%)")
            sparse_progress = 70 + int((index / total_reports) * 12)
            report_progress(
                sparse_progress,
                f"Sparse向量化: {index}/{total_reports}",
            )

        # 向量全部生成成功后再删除旧实体，减少外部 Embedding 失败造成的数据窗口。
        target_kbs = sorted({report["kb_id"] for report in reports})
        for target_kb in target_kbs:
            escaped_kb = target_kb.replace("\\", "").replace('"', "")
            client.delete(
                collection_name=collection_name,
                filter=f'kb_id == "{escaped_kb}"',
            )
            log(f"已清理知识库旧实体: {target_kb}")

        for start in range(0, len(rows), 500):
            inserted = min(start + 500, len(rows))
            client.insert(
                collection_name=collection_name,
                data=rows[start:start + 500],
            )
            log(f"写入进度: {inserted}/{len(rows)}")
            insert_progress = 82 + int((inserted / len(rows)) * 16)
            report_progress(insert_progress, f"写入 Milvus: {inserted}/{len(rows)}")
        client.flush(collection_name)
        client.load_collection(collection_name)
        report_progress(99, "Milvus 已刷新并加载 Collection")
        log(f"建库完成，共写入 {len(rows)} 条")
        return len(rows)
    finally:
        client.close()


def build_demo() -> int:
    """Create the same schema and insert three self-contained mock reports."""
    samples = [
        {
            "kb_id": "demo",
            "modality": "CT", "level_1": "头颅", "level_2": "",
            "level_3": "颅脑平扫", "level_4": "", "module_name": "脑出血",
            "imaging_findings": "右侧基底节区见片状高密度影，周围轻度水肿。",
            "imaging_opinion": "右侧基底节区脑出血。",
        },
        {
            "kb_id": "demo",
            "modality": "CT", "level_1": "头颅", "level_2": "",
            "level_3": "颅脑平扫", "level_4": "", "module_name": "脑梗死",
            "imaging_findings": "左侧额叶见片状低密度影，灰白质界限欠清。",
            "imaging_opinion": "考虑左侧额叶脑梗死。",
        },
        {
            "kb_id": "demo",
            "modality": "MR", "level_1": "头颅", "level_2": "",
            "level_3": "头颅MRI", "level_4": "", "module_name": "脑膜瘤",
            "imaging_findings": "右侧额部见类圆形异常信号，边界清楚。",
            "imaging_opinion": "右侧额部占位，考虑脑膜瘤。",
        },
    ]
    random_source = random.Random(20260724)
    for sample in samples:
        sample["combined_text"] = "\n".join([
            f"检查类型：{sample['modality']}",
            f"一级目录：{sample['level_1']}",
            f"三级目录：{sample['level_3']}",
            f"模块名称：{sample['module_name']}",
            f"影像学表现：{sample['imaging_findings']}",
            f"影像学意见：{sample['imaging_opinion']}",
        ])
        sample["chunk_id"] = generate_chunk_id(
            sample["kb_id"], f"{sample['module_name']}.md", sample["combined_text"]
        )
        sample["dense_vector"] = [
            random_source.random() for _ in range(get_embed_dimension())
        ]
        sample["sparse_vector"] = text_to_sparse_vector(sample["combined_text"])

    client = _client()
    collection_name = get_collection_name()
    try:
        if not client.has_collection(collection_name):
            create_collection(client)
        else:
            client.delete(
                collection_name=collection_name,
                filter='kb_id == "demo"',
            )
        client.insert(collection_name=collection_name, data=samples)
        client.flush(collection_name)
        client.load_collection(collection_name)
        print(f"模拟数据写入完成，共 {len(samples)} 条", flush=True)

        filter_expr = 'modality == "CT" and level_1 == "头颅"'
        dense_request = AnnSearchRequest(
            data=[samples[0]["dense_vector"]],
            anns_field="dense_vector",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=3,
            expr=filter_expr,
        )
        sparse_request = AnnSearchRequest(
            data=[text_to_sparse_vector("脑出血")],
            anns_field="sparse_vector",
            param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
            limit=3,
            expr=filter_expr,
        )
        results = client.hybrid_search(
            collection_name=collection_name,
            reqs=[dense_request, sparse_request],
            ranker=RRFRanker(),
            limit=3,
            output_fields=["module_name", "imaging_opinion"],
        )
        for hit in results[0]:
            entity = hit["entity"]
            print(
                entity["module_name"],
                entity["imaging_opinion"],
                hit["distance"],
                flush=True,
            )
        return len(samples)
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="增量构建医疗报告Dense+Sparse混合向量库")
    parser.add_argument("--input", default=get_markdown_dir(), help="Markdown根目录")
    parser.add_argument("--batch-size", type=int, default=32, help="Dense向量化批次")
    parser.add_argument("--kb-id", help="逻辑知识库 ID（Markdown 未包含时必填）")
    parser.add_argument("--demo", action="store_true", help="重建并插入3条模拟数据")
    args = parser.parse_args()
    if args.demo:
        build_demo()
    else:
        build_db(args.input, args.batch_size, kb_id=args.kb_id)


if __name__ == "__main__":
    main()
