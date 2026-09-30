"""检查两个 Milvus 实例的数据量，确认报告生成应连向哪一个。"""

from pymilvus import MilvusClient

TARGETS = {
    "milvus-standalone (19530)": "http://127.0.0.1:19530",
    "rimag-milvus (19531)": "http://127.0.0.1:19531",
}


def inspect(label: str, uri: str) -> None:
    print(f"=== {label} -> {uri}")
    try:
        client = MilvusClient(uri=uri)
        collections = client.list_collections()
        print(f"  collections: {collections}")
        for name in collections:
            try:
                stats = client.get_collection_stats(name)
                print(f"    {name}: {stats}")
            except Exception as exc:
                print(f"    {name}: stats 失败 {type(exc).__name__}")
        client.close()
    except Exception as exc:
        print(f"  连接失败: {type(exc).__name__}: {str(exc)[:120]}")
    print()


if __name__ == "__main__":
    for label, uri in TARGETS.items():
        inspect(label, uri)
