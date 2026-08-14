"""启动入口：CLI + Web 模式"""

import asyncio
import logging
import sys
import traceback
import uuid

from dotenv import load_dotenv
from memory.entity_tracker import EntityTracker
from memory.short_term import ShortTermMemory
from memory.session_store import SessionStore
from config import get_db_path, get_collection_name, get_max_rounds, get_milvus_token
from milvus_compat import create_milvus_client

from .llm_client import chat_stream
from .pipeline import run_pipeline

logger = logging.getLogger(__name__)

# 从配置加载常量
DB_PATH = get_db_path()
COLLECTION_NAME = get_collection_name()


def main():
    """CLI 模式入口"""
    if "--web" in sys.argv:
        from .server import web_main
        web_main()
        return

    print(f"\n{'='*60}")
    print(f"  影像报告生成Agent v2 CLI")
    print(f"  {'='*60}")
    print(f"  输入问题开始对话，输入 clear 清空会话，输入 quit 退出")
    print(f"  {'='*60}\n")

    SESSION_ID = f"rag_v2_{uuid.uuid4().hex[:8]}"
    stm = ShortTermMemory(max_rounds=get_max_rounds())
    entity_tracker = EntityTracker(llm_chat_fn=chat_stream)
    client = create_milvus_client(DB_PATH, get_milvus_token())
    client.load_collection(COLLECTION_NAME)
    last_report = [""]

    def _emit(event_type, data):
        if event_type == "report":
            print(f"\n📋 报告:\n{data['content']}\n")
        elif event_type == "reasoning":
            print(f"💭 推理: {data['content']}")
        elif event_type == "intent":
            print(f"🎯 意图: {data['intent']}")
        elif event_type == "entity_update":
            print(f"🔄 实体更新: {data['changes']}, 槽位: {data['slots']}")
        elif event_type == "intent_switch":
            print(f"🔄 {data['message']}")
        elif event_type == "error":
            print(f"❌ 错误: {data['message']}")

    try:
        while True:
            user_input = input("\n👤 用户: ").strip()
            if not user_input:
                continue

            if user_input.lower() == "quit":
                print("👋 再见！")
                break

            if user_input.lower() == "clear":
                stm.clear(SESSION_ID)
                entity_tracker.clear()
                last_report[0] = ""
                print("🧹 会话已清空\n")
                continue

            result = asyncio.run(run_pipeline(
                user_input, SESSION_ID,
                stm, entity_tracker, client,
                last_report,
                _emit,
            ))
    except KeyboardInterrupt:
        print("\n👋 再见！")
    except Exception as e:
        logger.error("CLI 主循环未捕获异常", exc_info=True)
        print(f"\n❌ 未捕获异常: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
