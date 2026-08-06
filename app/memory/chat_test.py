"""短期记忆与实体上下文测试终端。

纯多轮对话，只测试 ShortTermMemory 的当前会话历史和 EntityTracker
的上下文消解，不读取或写入任何跨会话用户偏好。

用法：
  python chat_test.py
  python chat_test.py --debug
"""

import json
import os
import sys
import uuid

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.entity_tracker import EntityTracker
from memory.short_term import ShortTermMemory


SESSION_ID = f"test_{uuid.uuid4().hex[:8]}"
ENV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".env",
)


def chat_stream(messages, max_tokens=1024, temperature=0.7):
    load_dotenv(ENV_PATH)
    chat_url = os.environ.get("CHAT_URL", "http://14.22.86.97:11001/v1/chat/completions")
    chat_model = os.environ.get("CHAT_MODEL", "qwen36_27b_lora")
    payload = {
        "model": chat_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }

    full_reply = ""
    with requests.post(
        chat_url,
        headers={"Content-Type": "application/json"},
        json=payload,
        stream=True,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            data = line[len("data:"):].strip() if line.startswith("data:") else line
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                for choice in obj.get("choices", []):
                    content = choice.get("delta", {}).get("content", "")
                    if content:
                        print(content, end="", flush=True)
                        full_reply += content
            except (json.JSONDecodeError, AttributeError):
                continue
    print()
    return full_reply


def summarize_fn(messages: list[dict]) -> str:
    load_dotenv(ENV_PATH)
    chat_url = os.environ.get("CHAT_URL", "http://14.22.86.97:11001/v1/chat/completions")
    chat_model = os.environ.get("CHAT_MODEL", "qwen36_27b_lora")
    payload = {
        "model": chat_model,
        "messages": messages,
        "max_tokens": 80,
        "temperature": 0.3,
        "stream": False,
    }
    try:
        response = requests.post(
            chat_url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def main():
    debug = "--debug" in sys.argv
    stm = ShortTermMemory(max_rounds=5, summarize_fn=summarize_fn)
    entity_tracker = EntityTracker()

    load_dotenv(ENV_PATH)
    chat_model = os.environ.get("CHAT_MODEL", "qwen36_27b_lora")
    base_system = "你是一个有帮助的AI助手。"

    print("=== 当前会话记忆测试已启动 ===")
    print(f"模型: {chat_model}")
    print(f"短期记忆: 最近 {stm.max_rounds} 轮")
    if debug:
        print("调试模式: 开启（显示上下文消解和当前会话状态）")
    print("\n命令:\n  exit/quit - 退出\n  clear     - 清空当前会话记忆\n  info      - 查看当前会话状态\n")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("再见！")
            break
        if user_input.lower() == "clear":
            stm.clear(SESSION_ID)
            entity_tracker.clear()
            print("🧹 当前会话记忆和实体槽位已清空\n")
            continue
        if user_input.lower() == "info":
            info = stm.session_info(SESSION_ID)
            print(f"📊 当前会话: {info['session_id']}")
            print(f"   轮次: {info['turns']}/{info['max_rounds']} (总计: {info['total_turns']})")
            print(f"   实体槽位: {entity_tracker.slots}")
            print(f"   摘要: {info['summary_count']} 条")
            for index, summary in enumerate(stm.get_summaries(SESSION_ID), 1):
                print(f"   摘要{index}: {summary}")
            for index, message in enumerate(stm.get_history(SESSION_ID)):
                content = message["content"]
                preview = content[:60] + "..." if len(content) > 60 else content
                print(f"   [{index}] {message['role']}: {preview}")
            print()
            continue

        entity_tracker.update_from_query(user_input)
        enhanced = entity_tracker.resolve_context(user_input)
        if debug and enhanced != user_input:
            print(f"🔗 上下文消解: '{user_input}' → '{enhanced}'")

        messages = stm.build_messages(SESSION_ID, "", enhanced)
        entity_context = entity_tracker.to_context_prompt()
        messages[0]["content"] = (
            f"{base_system}\n\n{entity_context}" if entity_context else base_system
        )

        if debug:
            info = stm.session_info(SESSION_ID)
            print(f"📊 当前会话: {info['turns']}/{info['max_rounds']} 轮 | 实体槽位: {entity_tracker.slots}")

        print("AI: ", end="", flush=True)
        try:
            reply = chat_stream(messages)
            stm.add_turn(SESSION_ID, user_input, reply)
        except Exception as error:
            print(f"\n（出错: {error}）")
        print()


if __name__ == "__main__":
    main()
