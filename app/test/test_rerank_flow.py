"""完整流程测试: 多路召回 → Rerank → Prompt拼接 → LLM输入预览"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"))

from pymilvus import MilvusClient
from config import get_embed_client, get_embed_model, get_db_path

from rag.rerank import rerank_documents, get_rerank_config
from rag.retrieval import multi_recall
from rag.query_rewrite import parse_query_keywords

DB_PATH = get_db_path()
COLLECTION_NAME = "report_slices"

TOP_K = 5
RERANK_TOP_K = 3

client = MilvusClient(uri=DB_PATH)
client.load_collection(COLLECTION_NAME)

query = "CT脑出血"

keywords = parse_query_keywords(query)

print("=" * 60)
print(f"用户问题: {query}")
print(f"解析关键词: 检查类型={keywords.get('检查类型', '-') or '-'} | 部位={keywords.get('部位', '-') or '-'} | 诊断关键词={', '.join(keywords.get('诊断关键词', [])) or '-'}")
print(f"多路召回: 每路 top-{TOP_K}")
print(f"Rerank: top-{RERANK_TOP_K}")
print(f"Rerank模型: {get_rerank_config()['rerank_model']}")
print("=" * 60)

response = get_embed_client().embeddings.create(
    model=get_embed_model(), input=[query], timeout=30
)
query_vec = response.data[0].embedding

print(f"\n📌 第一步: 多路召回 (向量检索 + 元数据过滤 + 关键词检索)")
print("-" * 60)
candidates = multi_recall(query_vec, keywords, top_k=TOP_K, client=client)

path_counts = {}
for c in candidates:
    path = c.get("_recall_path", "vector")
    path_counts[path] = path_counts.get(path, 0) + 1

print(f"  召回路径: {' | '.join(f'{k}: {v}条' for k, v in sorted(path_counts.items()))}")
print(f"  合并去重后: {len(candidates)} 条候选")

for i, c in enumerate(candidates[:10], 1):
    paths_str = "+".join(c.get("_recall_paths", []))
    dist_str = f"相似度={c.get('_distance', -1):.4f}" if c.get("_distance", -1) >= 0 else "精确匹配"
    print(f"  候选{i} [{paths_str}] {dist_str} | 来源={c['source']} | 诊断={c.get('诊断结论', '')}")

documents = [e["text"] for e in candidates]

print(f"\n📌 第二步: Rerank 重排序 (top-{RERANK_TOP_K})")
print("-" * 60)
try:
    rerank_results = rerank_documents(query, documents, top_n=RERANK_TOP_K)
    reranked_entities = []
    for rr in rerank_results:
        idx = rr.get("index", 0)
        rerank_score = rr.get("relevance_score", 0)
        entity = candidates[idx]
        entity["_rerank_score"] = rerank_score
        reranked_entities.append(entity)
        print(f"  原始位置 候选{idx+1} [{entity.get('_recall_path', 'vector')}] → Rerank分数: {rerank_score:.4f}")
        print(f"  来源: {entity['source']}")
        print(f"  诊断: {entity.get('诊断结论', '')}")
except Exception as e:
    print(f"  Rerank失败({e})，使用向量检索结果")
    reranked_entities = candidates[:RERANK_TOP_K]
    for entity in reranked_entities:
        entity["_rerank_score"] = -1

print(f"\n📌 第三步: Prompt 拼接 (带Rerank分数)")
print("=" * 60)
contexts = []
for i, ent in enumerate(reranked_entities, 1):
    rerank_score = ent.get("_rerank_score", 0)
    contexts.append(f"【参考{i}】(Rerank相关性分数: {rerank_score:.4f}，来源: {ent['source']})\n{ent['text']}")

context_text = "\n\n".join(contexts)
print(context_text)

print("\n" + "=" * 60)
print(f"📌 第四步: 完整 Prompt 预览 (发给 LLM 的内容)")
print("=" * 60)

PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rag", "prompt.md")
system_prompt = "你是一个医疗影像报告生成助手。"
if os.path.exists(PROMPT_FILE):
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        system_prompt = f.read().strip()

user_prompt = f"参考信息：\n{context_text}\n\n用户问题：{query}"

print(f"\n[System Prompt]: ({len(system_prompt)} 字符)")
print(system_prompt[:300] + "...")

print(f"\n[User Prompt]:")
print(user_prompt)

print("\n✅ 流程结束 — 以上就是 LLM 收到的完整输入")
client.close()