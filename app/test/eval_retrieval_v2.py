"""RAG 检索流水线真实评估脚本 (基于 JSONL 黄金测试集)

用法示例：
  python app/test/eval_retrieval_v2.py --dataset app/test/retrieval_eval_dataset.jsonl
  python app/test/eval_retrieval_v2.py --dataset app/test/retrieval_eval_dataset.jsonl -k 5
"""
import argparse
import json
import os
import sys

# 将项目根目录加入 sys.path，解决 app.xxx 导包找不到的问题
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '.env'))

from pymilvus import MilvusClient
from rag.query_rewrite import parse_query_keywords
from rag.retrieval import multi_recall
from rag.rerank import rerank_documents
from chat.llm_client import get_embedding
from chat.pipeline import RAG_TOP_K, RERANK_TOP_K
from memory.entity_tracker import DIAGNOSIS_TO_BODY_PART

def _infer_part_from_diagnosis(diagnosis: str) -> str:
    """从诊断推断部位"""
    return DIAGNOSIS_TO_BODY_PART.get(diagnosis, "")
from config import get_collection_name, get_db_path

COLLECTION_NAME = get_collection_name()
DB_PATH = get_db_path()


def load_dataset(file_path):
    dataset = []
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"找不到测试集文件: {file_path}")
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                dataset.append(json.loads(line))
    return dataset


def run_actual_pipeline(query, client, use_rerank=True, use_completion=True, top_k=3):
    """接入真实的 RAG 流水线进行检索"""
    
    # 1. 获取查询向量
    query_vec = get_embedding(query)
    
    # 2. 提取初始关键词
    keywords = parse_query_keywords(query)
    
    # 3. 部位补全机制
    if use_completion and not keywords.get("部位"):
        inferred_part = _infer_part_from_diagnosis(query)
        if inferred_part:
            keywords["部位"] = inferred_part
    
    # 4. 多路召回 (向量 + 元数据过滤 + 关键词)
    candidates = multi_recall(query_vec, keywords, top_k=top_k, client=client)
    
    if not candidates:
        return []
    
    # 5. Rerank 精排
    if use_rerank:
        try:
            documents = [e["text"] for e in candidates]
            rerank_results = rerank_documents(query, documents, top_n=top_k)
            reranked_entities = []
            for rr in rerank_results:
                idx = rr.get("index", 0)
                if idx < len(candidates):
                    reranked_entities.append(candidates[idx])
            final_results = reranked_entities
        except Exception as e:
            print(f"  [Warning] Rerank 失败，自动降级: {e}")
            final_results = candidates[:top_k]
    else:
        final_results = candidates[:top_k]
    
    # 6. 提取 source 文件名
    retrieved_sources = [res.get("source", "") for res in final_results]
    
    return retrieved_sources


def evaluate_pipeline(dataset, client, use_rerank, use_completion, top_k):
    hits = 0
    mrr_sum = 0.0
    n_samples = len(dataset)
    
    for i, item in enumerate(dataset, 1):
        query = item["query"]
        target = item["expected_source"]
        
        print(f"  [{i}/{n_samples}] 查询: {query[:30]}...", end="\r")
        
        # 调用真实流水线
        retrieved_sources = run_actual_pipeline(
            query=query,
            client=client,
            use_rerank=use_rerank,
            use_completion=use_completion,
            top_k=top_k
        )
        
        # 命中率与 MRR 计算
        if target in retrieved_sources:
            hits += 1
            rank = retrieved_sources.index(target) + 1
            mrr_sum += 1.0 / rank
    
    print(f"  完成 {n_samples} 条查询")
    
    hit_rate = hits / n_samples * 100 if n_samples > 0 else 0
    mrr = mrr_sum / n_samples if n_samples > 0 else 0
    
    return hit_rate, mrr


def main():
    parser = argparse.ArgumentParser(description="RAG 检索质量工业级评估")
    parser.add_argument("--dataset", type=str, default="app/test/retrieval_eval_dataset.jsonl", help="黄金测试集路径")
    parser.add_argument("-k", "--top-k", type=int, default=3, help="评估的 top-K 值（默认 3）")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    print(f"成功加载测试集，共 {len(dataset)} 条查询\n")
    
    # 初始化 Milvus 客户端
    client = MilvusClient(uri=DB_PATH)
    client.load_collection(COLLECTION_NAME)
    
    print("=" * 60)
    print(f"开始 A/B 对比评估 (Top-{args.top_k})")
    print("=" * 60)

    # A: 完整生产环境
    print(">>> 正在运行 [完整流水线: 向量 + 部位补全 + Rerank] ...")
    hit_a, mrr_a = evaluate_pipeline(dataset, client, use_rerank=True, use_completion=True, top_k=args.top_k)
    
    # B: 降级 (无 Rerank)
    print(">>> 正在运行 [降级流水线: 仅向量 + 部位补全，无 Rerank] ...")
    hit_b, mrr_b = evaluate_pipeline(dataset, client, use_rerank=False, use_completion=True, top_k=args.top_k)

    # C: 裸奔 (无补全无精排)
    print(">>> 正在运行 [裸 RAG: 仅向量检索，无补全无精排] ...")
    hit_c, mrr_c = evaluate_pipeline(dataset, client, use_rerank=False, use_completion=False, top_k=args.top_k)

    client.close()

    print("\n" + "=" * 60)
    print("评估结果报告 (A/B Test)")
    print("=" * 60)
    print(f"{'配置':<35} | {'Hit Rate':<10} | {'MRR':<10}")
    print("-" * 60)
    print(f"{'完整流水线 (向量+补全+Rerank)':<30} | {hit_a:>8.1f}% | {mrr_a:>8.4f}")
    print(f"{'降级流水线 (向量+补全，无Rerank)':<28} | {hit_b:>8.1f}% | {mrr_b:>8.4f}")
    print(f"{'裸 RAG (仅向量，无补全无精排)':<26} | {hit_c:>8.1f}% | {mrr_c:>8.4f}")
    
    print("\n结论与洞察:")
    print(f"1. Rerank 服务为召回率提升了 {hit_a - hit_b:.1f}%")
    print(f"2. 部位推断补全机制为召回率提升了 {hit_b - hit_c:.1f}%")

if __name__ == "__main__":
    main()