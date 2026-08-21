"""RAG 检索命中率评估脚本。

从向量库中随机抽样，用切片内容构造查询，评估检索命中率。

用法示例：
  python eval_retrieval.py                    # 默认抽 100 条，top-3
  python eval_retrieval.py -n 50 -k 5         # 抽 50 条，top-5
  python eval_retrieval.py -n 200 -k 1 3 5    # 抽 200 条，同时评估 top-1/3/5
"""
import argparse
import os
import random
import sys
import time

from dotenv import load_dotenv
from pymilvus import MilvusClient

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from config import get_embed_client, get_embed_model, get_db_path

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"))

DB_PATH = get_db_path()
COLLECTION_NAME = "report_slices"


def get_embeddings(texts, batch_size=16):
    client = get_embed_client()
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        for attempt in range(5):
            try:
                response = client.embeddings.create(
                    model=get_embed_model(), input=batch, timeout=120
                )
                data = sorted(response.data, key=lambda item: item.index)
                all_embeddings.extend(item.embedding for item in data)
                break
            except Exception as e:
                if attempt < 4:
                    time.sleep((attempt + 1) * 3)
                else:
                    raise RuntimeError(f"向量化失败: {e}") from e
        time.sleep(0.2)
    return all_embeddings


def get_all_records(client):
    client.load_collection(COLLECTION_NAME)
    results = client.query(
        COLLECTION_NAME,
        filter="",
        output_fields=["text", "source", "检查类型", "部位", "检查项目", "诊断结论"],
    )
    return results


def generate_query(record):
    parts = []
    for key in ("检查类型", "部位", "检查项目", "诊断结论"):
        val = record.get(key, "")
        if val:
            parts.append(val)
    query = " ".join(parts)
    if not query:
        query = record.get("text", "")[:100]
    return query


def search_batch(client, query_vectors, top_k):
    client.load_collection(COLLECTION_NAME)
    results = client.search(
        collection_name=COLLECTION_NAME,
        data=query_vectors,
        limit=top_k,
        output_fields=["source"],
    )
    return results


def evaluate(n_samples=100, top_ks=(1, 3, 5)):
    if not os.path.exists(DB_PATH):
        print("向量数据库不存在，请先运行 build_vector_db.py", file=sys.stderr)
        return

    client = MilvusClient(uri=DB_PATH)

    print("加载全部记录...")
    records = get_all_records(client)
    total = len(records)
    print(f"数据库共 {total} 条记录")

    if n_samples > total:
        n_samples = total
        print(f"样本数超过总数，调整为 {n_samples}")

    max_k = max(top_ks)
    sample_indices = random.sample(range(total), n_samples)
    samples = [records[i] for i in sample_indices]

    queries = [generate_query(r) for r in samples]
    target_sources = [r["source"] for r in samples]

    print(f"\n生成 {n_samples} 条查询，开始向量化...")
    query_vectors = get_embeddings(queries, batch_size=16)
    print("向量化完成\n")

    print(f"开始检索 (top-{max_k})...")
    all_results = search_batch(client, query_vectors, max_k)
    client.close()

    print("=" * 60)
    print("评估结果")
    print("=" * 60)

    for k in top_ks:
        hits = 0
        mrr_sum = 0.0
        for i, hits_list in enumerate(all_results):
            target = target_sources[i]
            retrieved_sources = [h["entity"]["source"] for h in hits_list[:k]]
            if target in retrieved_sources:
                hits += 1
                rank = retrieved_sources.index(target) + 1
                mrr_sum += 1.0 / rank

        hit_rate = hits / n_samples * 100
        mrr = mrr_sum / n_samples

        print(f"\n--- Top-{k} ---")
        print(f"  Hit Rate: {hit_rate:.1f}% ({hits}/{n_samples})")
        print(f"  MRR:      {mrr:.4f}")

    print("\n" + "=" * 60)
    print("示例查询（前5条）:")
    print("=" * 60)
    for i in range(min(5, n_samples)):
        target = target_sources[i]
        retrieved = [h["entity"]["source"] for h in all_results[i][:max(top_ks)]]
        hit_mark = "Y" if target in retrieved else "N"
        print(f"\n  查询: {queries[i][:80]}")
        print(f"  目标: {target}")
        print(f"  检索: {retrieved[:3]}")
        print(f"  命中: {hit_mark}")


def main():
    parser = argparse.ArgumentParser(description="RAG 检索命中率评估")
    parser.add_argument("-n", "--num-samples", type=int, default=100, help="抽样数量（默认100）")
    parser.add_argument("-k", "--top-ks", type=int, nargs="+", default=[1, 3, 5], help="评估的 top-K 值（默认 1 3 5）")
    args = parser.parse_args()

    random.seed(42)
    evaluate(n_samples=args.num_samples, top_ks=tuple(args.top_ks))


if __name__ == "__main__":
    main()