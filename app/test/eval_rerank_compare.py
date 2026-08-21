"""RAG 检索召回率对比评估脚本。

对比 向量检索 vs 向量检索+Rerank 的召回率，
评估 Rerank 是否提升了 top-1 的命中率。

用法示例：
  python eval_rerank_compare.py                    # 默认抽 30 条，向量检索 top-5，Rerank top-3
  python eval_rerank_compare.py -n 50 -k 10 -r 5   # 抽 50 条，向量检索 top-10，Rerank top-5
  python eval_rerank_compare.py -n 20 --no-rerank  # 只评估向量检索，跳过 Rerank
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
from rag.rerank import rerank_documents

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


def evaluate(n_samples=30, vec_top_k=5, rerank_top_k=3, no_rerank=False):
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

    sample_indices = random.sample(range(total), n_samples)
    samples = [records[i] for i in sample_indices]

    queries = [generate_query(r) for r in samples]
    target_sources = [r["source"] for r in samples]

    print(f"\n生成 {n_samples} 条查询，开始向量化...")
    query_vectors = get_embeddings(queries, batch_size=16)
    print("向量化完成\n")

    client.load_collection(COLLECTION_NAME)
    print(f"开始向量检索 (top-{vec_top_k})...")
    all_vec_results = client.search(
        collection_name=COLLECTION_NAME,
        data=query_vectors,
        limit=vec_top_k,
        output_fields=["source", "text"],
    )
    client.close()

    vec_hit_top1 = 0
    vec_hit_topk = 0
    vec_mrr = 0.0

    for i, hits_list in enumerate(all_vec_results):
        target = target_sources[i]
        retrieved = [h["entity"]["source"] for h in hits_list]
        if retrieved[0] == target:
            vec_hit_top1 += 1
        if target in retrieved:
            vec_hit_topk += 1
            vec_mrr += 1.0 / (retrieved.index(target) + 1)

    print("=" * 70)
    print("【向量检索结果】")
    print("=" * 70)
    print(f"  Top-1 Hit Rate: {vec_hit_top1 / n_samples * 100:.1f}% ({vec_hit_top1}/{n_samples})")
    print(f"  Top-{vec_top_k} Hit Rate: {vec_hit_topk / n_samples * 100:.1f}% ({vec_hit_topk}/{n_samples})")
    print(f"  Top-{vec_top_k} MRR:      {vec_mrr / n_samples:.4f}")

    if no_rerank:
        print("\n跳过 Rerank 评估")
        return

    print(f"\n开始 Rerank 精排 (top-{rerank_top_k})...")
    rerank_hit_top1 = 0
    rerank_hit_topk = 0
    rerank_mrr = 0.0
    rerank_improved = 0
    rerank_degraded = 0
    detail_cases = []

    for i, hits_list in enumerate(all_vec_results):
        target = target_sources[i]
        query = queries[i]

        vec_top1_source = hits_list[0]["entity"]["source"]
        vec_sources = [h["entity"]["source"] for h in hits_list]
        documents = [h["entity"]["text"] for h in hits_list]

        try:
            rerank_results = rerank_documents(query, documents, top_n=rerank_top_k)
        except Exception as e:
            print(f"  样本{i+1} Rerank失败: {e}")
            if vec_top1_source == target:
                rerank_hit_top1 += 1
            if target in vec_sources:
                rerank_hit_topk += 1
                rerank_mrr += 1.0 / (vec_sources.index(target) + 1)
            continue

        reranked_sources = [vec_sources[rr["index"]] for rr in rerank_results]

        rerank_top1_source = reranked_sources[0] if reranked_sources else vec_top1_source

        if rerank_top1_source == target:
            rerank_hit_top1 += 1
        if target in reranked_sources:
            rerank_hit_topk += 1
            rerank_mrr += 1.0 / (reranked_sources.index(target) + 1)

        if vec_top1_source != target and rerank_top1_source == target:
            rerank_improved += 1
            detail_cases.append({
                "type": "IMPROVED",
                "query": query,
                "target": target,
                "vec_top1": vec_top1_source,
                "rerank_top1": rerank_top1_source,
                "rerank_scores": [round(rr["relevance_score"], 4) for rr in rerank_results],
            })
        elif vec_top1_source == target and rerank_top1_source != target:
            rerank_degraded += 1
            detail_cases.append({
                "type": "DEGRADED",
                "query": query,
                "target": target,
                "vec_top1": vec_top1_source,
                "rerank_top1": rerank_top1_source,
                "rerank_scores": [round(rr["relevance_score"], 4) for rr in rerank_results],
            })

        time.sleep(0.3)

    print("\n" + "=" * 70)
    print("【向量检索 + Rerank 结果】")
    print("=" * 70)
    print(f"  Top-1 Hit Rate: {rerank_hit_top1 / n_samples * 100:.1f}% ({rerank_hit_top1}/{n_samples})")
    print(f"  Top-{rerank_top_k} Hit Rate: {rerank_hit_topk / n_samples * 100:.1f}% ({rerank_hit_topk}/{n_samples})")
    print(f"  Top-{rerank_top_k} MRR:      {rerank_mrr / n_samples:.4f}")

    print("\n" + "=" * 70)
    print("【对比汇总】")
    print("=" * 70)
    print(f"  向量检索 Top-1 命中率:       {vec_hit_top1 / n_samples * 100:.1f}%")
    print(f"  向量检索+Rerank Top-1 命中率: {rerank_hit_top1 / n_samples * 100:.1f}%")
    diff = (rerank_hit_top1 - vec_hit_top1) / n_samples * 100
    print(f"  Rerank 提升:                  {diff:+.1f}%")
    print(f"  Rerank 改善案例数:            {rerank_improved} (向量检索未命中但Rerank命中)")
    print(f"  Rerank 退化案例数:            {rerank_degraded} (向量检索命中但Rerank未命中)")

    if detail_cases:
        print("\n" + "=" * 70)
        print("【变化案例详情】")
        print("=" * 70)
        for case in detail_cases:
            tag = "✅ 改善" if case["type"] == "IMPROVED" else "❌ 退化"
            print(f"\n  {tag}")
            print(f"    查询:       {case['query'][:80]}")
            print(f"    目标:       {case['target']}")
            print(f"    向量Top-1:  {case['vec_top1']}")
            print(f"    Rerank Top-1: {case['rerank_top1']}")
            print(f"    Rerank分数: {case['rerank_scores']}")


def main():
    parser = argparse.ArgumentParser(description="RAG 检索召回率对比评估（向量检索 vs 向量检索+Rerank）")
    parser.add_argument("-n", "--num-samples", type=int, default=30, help="抽样数量（默认30）")
    parser.add_argument("-k", "--vec-top-k", type=int, default=5, help="向量检索 top-K（默认5）")
    parser.add_argument("-r", "--rerank-top-k", type=int, default=3, help="Rerank top-K（默认3）")
    parser.add_argument("--no-rerank", action="store_true", help="跳过 Rerank 评估")
    args = parser.parse_args()

    random.seed(42)
    evaluate(
        n_samples=args.num_samples,
        vec_top_k=args.vec_top_k,
        rerank_top_k=args.rerank_top_k,
        no_rerank=args.no_rerank,
    )


if __name__ == "__main__":
    main()