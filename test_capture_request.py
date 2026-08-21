"""临时脚本：捕获 OpenAI SDK 发送给 vLLM 的真实 HTTP 请求体。

运行: python test_capture_request.py
完成后可删除: del test_capture_request.py
"""

import json
import os
import sys
import asyncio

import httpx
from openai import AsyncOpenAI


class CaptureTransport(httpx.AsyncHTTPTransport):
    def __init__(self):
        super().__init__()
        self.captured = []

    async def handle_async_request(self, request):
        body = request.content.decode("utf-8") if request.content else ""
        try:
            obj = json.loads(body)
        except Exception:
            obj = body
        self.captured.append({
            "url": str(request.url),
            "body": obj,
        })
        return await super().handle_async_request(request)


async def main():
    os.environ.setdefault("CHAT_URL", "http://14.22.86.97:11001/v1/chat/completions")
    os.environ.setdefault("CHAT_MODEL", "qwen36-27b")

    transport = CaptureTransport()
    http_client = httpx.AsyncClient(transport=transport, timeout=30.0)

    base_url = "http://14.22.86.97:11001/v1"
    client = AsyncOpenAI(base_url=base_url, api_key="not-needed", http_client=http_client)

    dummy_tool = {
        "type": "function",
        "function": {
            "name": "dummy_tool",
            "description": "test tool",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    print("=" * 60)
    print("Test A: 带 tools，不传 tool_choice（模拟当前 chat_with_tools）")
    print("=" * 60)
    try:
        resp = await client.chat.completions.create(
            model="qwen36-27b",
            messages=[{"role": "user", "content": "回复 OK"}],
            tools=[dummy_tool],
            max_tokens=100,
            temperature=0.0,
        )
        print(f"结果: finish_reason={resp.choices[0].finish_reason}")
    except Exception as e:
        print(f"错误: {e}")

    print("")
    print("=" * 60)
    print("Test B: 带 tools，显式 tool_choice='none'")
    print("=" * 60)
    try:
        resp = await client.chat.completions.create(
            model="qwen36-27b",
            messages=[{"role": "user", "content": "回复 OK"}],
            tools=[dummy_tool],
            tool_choice="none",
            max_tokens=100,
            temperature=0.0,
        )
        print(f"结果: finish_reason={resp.choices[0].finish_reason}")
    except Exception as e:
        print(f"错误: {e}")

    print("")
    print("=" * 60)
    print("Test C: 无 tools")
    print("=" * 60)
    try:
        resp = await client.chat.completions.create(
            model="qwen36-27b",
            messages=[{"role": "user", "content": "回复 OK"}],
            max_tokens=100,
            temperature=0.0,
        )
        print(f"结果: finish_reason={resp.choices[0].finish_reason}")
    except Exception as e:
        print(f"错误: {e}")

    print("")
    print("=" * 60)
    print("捕获的 HTTP 请求体分析")
    print("=" * 60)

    for i, req in enumerate(transport.captured):
        print(f"\n--- Request {i+1}: {req['url']} ---")
        body = req["body"]
        if isinstance(body, dict):
            print(f"  model:        {body.get('model', 'N/A')}")
            print(f"  stream:       {body.get('stream', 'N/A')}")
            print(f"  temperature:  {body.get('temperature', 'N/A')}")
            print(f"  max_tokens:   {body.get('max_tokens', 'N/A')}")
            tools = body.get("tools")
            print(f"  has_tools:    {tools is not None}")
            if tools:
                print(f"  tool_count:   {len(tools)}")
                print(f"  tool_names:   {[t['function']['name'] for t in tools]}")
            tc = body.get("tool_choice")
            if tc is None:
                print(f"  tool_choice:  *** NOT PRESENT ***")
            else:
                print(f"  tool_choice:  {json.dumps(tc)}")
        else:
            print(f"  body: {body}")

    await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())