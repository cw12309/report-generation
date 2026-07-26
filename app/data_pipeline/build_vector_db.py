"""Rebuild the medical report collection with dense and sparse vectors."""

import argparse
import random
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import requests
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
    get_embed_api_key,
    get_embed_base_url,
    get_embed_dimension,
    get_embed_model,
    get_markdown_dir,
    get_milvus_token,
)
from rag.sparse_vector import text_to_sparse_vector  # noqa: E402


def _section(content: str, heading: str) -> str:
    marker = f"### {heading}"
    if marker not in content:
        return ""
    body = content.split(marker, 1)[1]
    if "\n### " in body:
        body = body.split("\n### ", 1)[0]
    return body.strip()


def parse_markdown(path: Path) -> Dict:
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
    return {
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


def _embedding_url() -> str:
    base_url = get_embed_base_url().rstrip("/")
    return base_url if base_url.endswith("/embeddings") else f"{base_url}/embeddings"


def get_dense_embeddings(
    texts: List[str],
    batch_size: int,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[List[float]]:
    headers = {"Content-Type": "application/json"}
    api_key = get_embed_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    embeddings: List[List[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        for attempt in range(5):
            try:
                response = requests.post(
                    _embedding_url(),
                    headers=headers,
                    json={"model": get_embed_model(), "input": batch},
                    timeout=120,
                )
                response.raise_for_status()
                items = sorted(response.json()["data"], key=lambda item: item["index"])
                embeddings.extend(item["embedding"] for item in items)
                if progress_callback:
                    completed = min(start + len(batch), len(texts))
                    percentage = completed * 100 / len(texts)
                    progress_callback(
                        f"Dense向量化: {completed}/{len(texts)} ({percentage:.1f}%)"
                    )
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep((attempt + 1) * 5)
    return embeddings


def _client() -> MilvusClient:
    kwargs = {"uri": get_db_path()}
    token = get_milvus_token()
    if token:
        kwargs["token"] = token
    return MilvusClient(**kwargs)


def create_collection(client: MilvusClient) -> None:
    """Create the exact scalar, dense, and sparse schema and its indexes."""
    schema = CollectionSchema(fields=[
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
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
    indexes.add_index(field_name="modality", index_type="INVERTED")
    indexes.add_index(field_name="level_1", index_type="INVERTED")
    indexes.add_index(field_name="level_2", index_type="INVERTED")
    indexes.add_index(field_name="level_3", index_type="INVERTED")
    indexes.add_index(field_name="level_4", index_type="INVERTED")
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


def build_db(
    input_dir: Optional[str] = None,
    batch_size: int = 32,
    rebuild: bool = True,
    progress_callback: Optional[Callable] = None,
) -> int:
    """Drop and rebuild the collection from all generated Markdown reports."""
    def log(message: str, level: str = "info") -> None:
        print(message, flush=True)
        if progress_callback:
            progress_callback({"level": level, "msg": message})

    base_dir = Path(input_dir or get_markdown_dir()).resolve()
    if not base_dir.is_dir():
        raise FileNotFoundError(f"Markdown目录不存在: {base_dir}")

    markdown_files = sorted(base_dir.rglob("*.md"))
    reports = [parse_markdown(path) for path in markdown_files]
    reports = [report for report in reports if report["combined_text"]]
    log(f"发现有效Markdown报告: {len(reports)} 个")

    client = _client()
    collection_name = get_collection_name()
    try:
        if client.has_collection(collection_name):
            log(f"删除现有集合: {collection_name}")
            client.drop_collection(collection_name)
        create_collection(client)
        log(f"集合已创建: {collection_name}")

        dense_vectors = get_dense_embeddings(
            [report["combined_text"] for report in reports],
            batch_size,
            progress_callback=log,
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

        for start in range(0, len(rows), 500):
            client.insert(
                collection_name=collection_name,
                data=rows[start:start + 500],
            )
            log(f"写入进度: {min(start + 500, len(rows))}/{len(rows)}")
        client.flush(collection_name)
        client.load_collection(collection_name)
        log(f"建库完成，共写入 {len(rows)} 条")
        return len(rows)
    finally:
        client.close()


def build_demo() -> int:
    """Create the same schema and insert three self-contained mock reports."""
    samples = [
        {
            "modality": "CT", "level_1": "头颅", "level_2": "",
            "level_3": "颅脑平扫", "level_4": "", "module_name": "脑出血",
            "imaging_findings": "右侧基底节区见片状高密度影，周围轻度水肿。",
            "imaging_opinion": "右侧基底节区脑出血。",
        },
        {
            "modality": "CT", "level_1": "头颅", "level_2": "",
            "level_3": "颅脑平扫", "level_4": "", "module_name": "脑梗死",
            "imaging_findings": "左侧额叶见片状低密度影，灰白质界限欠清。",
            "imaging_opinion": "考虑左侧额叶脑梗死。",
        },
        {
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
        sample["dense_vector"] = [
            random_source.random() for _ in range(get_embed_dimension())
        ]
        sample["sparse_vector"] = text_to_sparse_vector(sample["combined_text"])

    client = _client()
    collection_name = get_collection_name()
    try:
        if client.has_collection(collection_name):
            client.drop_collection(collection_name)
        create_collection(client)
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
    parser = argparse.ArgumentParser(description="重建医疗报告Dense+Sparse混合向量库")
    parser.add_argument("--input", default=get_markdown_dir(), help="Markdown根目录")
    parser.add_argument("--batch-size", type=int, default=32, help="Dense向量化批次")
    parser.add_argument("--demo", action="store_true", help="重建并插入3条模拟数据")
    args = parser.parse_args()
    if args.demo:
        build_demo()
    else:
        build_db(args.input, args.batch_size)


if __name__ == "__main__":
    main()
