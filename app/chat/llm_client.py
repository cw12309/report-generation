"""大模型网络请求封装"""

import logging

from openai import APIConnectionError, APITimeoutError, APIStatusError

from config import get_embed_client, get_llm_model, get_embed_model

from .utils import retry
from rag.rerank import rerank_documents

logger = logging.getLogger(__name__)


# =============================================================================
# Embedding
# =============================================================================
def get_embedding(text):
    """使用 OpenAI SDK 获取文本向量"""
    try:
        client = get_embed_client()
        response = client.embeddings.create(model=get_embed_model(), input=text)
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"[get_embedding] 请求失败: {e}", exc_info=True)
        raise


# =============================================================================
# Token 估算
# =============================================================================
def _estimate_tokens(messages):
    total_chars = sum(len(msg.get("content", "") or "") for msg in messages)
    return total_chars, total_chars // 2


# =============================================================================
# 流式 LLM 调用
# =============================================================================
def _build_candidate_chain(model_override: str, base_url_override: str,
                           timeout: float | None = None, max_retries: int | None = None,
                           api_key_override: str = "") -> list:
    """构建候选模型链：[(model, base_url, client), ...]。

    首选为请求指定的模型；其后追加 config.yml 中其余可用模型作为降级候选。
    timeout/max_retries 透传给客户端（快速失败链路用）。
    每个候选使用其条目生效的 API Key（单模型 LLM_KEY_<NAME> 优先，回退全局 CHAT_API_KEY）。
    """
    from config import (create_llm_client, get_llm_api_key, get_llm_api_key_for_entry,
                        get_llm_base_url, get_llm_entries, get_llm_entry_by_name, get_llm_model)

    candidates = []
    if base_url_override and model_override:
        candidates.append((model_override, base_url_override,
                           create_llm_client(base_url_override, api_key_override or get_llm_api_key(),
                                             timeout=timeout, max_retries=max_retries)))
    else:
        active_entry = get_llm_entry_by_name("") or {}
        active_key = active_entry.get("api_key") or get_llm_api_key()
        candidates.append((get_llm_model(), get_llm_base_url(),
                           create_llm_client(get_llm_base_url(), active_key,
                                             timeout=timeout, max_retries=max_retries)))

    seen = {(candidates[0][0], candidates[0][1])}
    for entry in get_llm_entries():
        key = (entry["model"], entry["base_url"])
        if key not in seen:
            seen.add(key)
            candidates.append((entry["model"], entry["base_url"],
                               create_llm_client(entry["base_url"], get_llm_api_key_for_entry(entry),
                                                 timeout=timeout, max_retries=max_retries)))
    return candidates


def _is_retryable_error(e: Exception) -> bool:
    """判断是否为可降级重试的错误（连接失败/超时/5xx/网关错误）。"""
    if isinstance(e, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(e, APIStatusError):
        return e.status_code >= 500 or e.status_code in (429, 502, 503, 504)
    return False


async def check_model_available(name: str) -> tuple:
    """轻量探活指定模型是否可响应（供前端选择模型时预校验）。

    先尝试 /v1/models；若该接口不可用（404/405 等）则退化为最小 completion 请求。
    Returns:
        (ok: bool, message: str)
    """
    from config import create_llm_client, get_llm_api_key, get_llm_entry_by_name

    entry = get_llm_entry_by_name(name)
    if not entry:
        return False, f"配置中不存在模型: {name}"
    # 探活用短超时、不重试，快速给出结论；使用该条目生效的 API Key
    client = create_llm_client(entry["base_url"], entry.get("api_key") or get_llm_api_key(),
                               timeout=10.0, max_retries=0)
    try:
        await client.models.list()
        return True, ""
    except Exception as e:
        if _is_retryable_error(e):
            return False, f"模型节点不可达 ({entry['base_url']}): {type(e).__name__}"
        # /v1/models 不被支持时，退化为最小 completion 探活（关闭思考加快响应）
        try:
            await client.chat.completions.create(
                model=entry["model"],
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
                stream=False,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            return True, ""
        except Exception as e2:
            return False, f"模型无响应: {type(e2).__name__}: {str(e2)[:120]}"


# 思考开关实测用提示：简单题目，开启思考的模型会明显产生推理过程
_THINKING_PROBE_MESSAGES = [{"role": "user", "content": "9.9 和 9.11 哪个大？请简短给出结论。"}]

# 思考内容泄漏到正文的特征（用于判定"思考混入 content"）
_THINKING_LEAK_MARKERS = (
    "thinking process", "here's a thinking", "let me think", "step 1",
    "首先", "思考过程", "分析如下", "让我们", "wait", "hmm",
)


async def _probe_thinking_once(entry: dict, client, enable_thinking: bool) -> dict:
    """以流式方式实测一次思考开关，返回 reasoning/content 字数与耗时。"""
    import time as _time

    reasoning, content = [], []
    t0 = _time.perf_counter()
    try:
        stream = await client.chat.completions.create(
            model=entry["model"],
            messages=_THINKING_PROBE_MESSAGES,
            max_tokens=600,
            temperature=0.1,
            stream=True,
            extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = getattr(delta, "reasoning_content", None)
            if rc:
                reasoning.append(rc)
            if delta.content:
                content.append(delta.content)
        return {
            "ok": True, "error": "",
            "reasoning_chars": len("".join(reasoning)),
            "content_chars": len("".join(content)),
            "content_head": "".join(content)[:160],
            "elapsed": round(_time.perf_counter() - t0, 2),
        }
    except Exception as e:
        return {
            "ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}",
            "reasoning_chars": 0, "content_chars": 0, "content_head": "",
            "elapsed": round(_time.perf_counter() - t0, 2),
        }


async def test_thinking_switch(name: str) -> dict:
    """实测指定模型对思考开关的支持情况（供前端配置前预校验）。

    分别以 enable_thinking=False / True 各发一次流式请求，比较 reasoning 通道与正文增量，判定：
      - reasoning_channel: 思考走独立通道，开启后正文仍是纯答案（推荐）
      - content_mixed:     思考混入正文，开启后会挤占 max_tokens 并可能干扰 JSON 解析（有风险）
      - no_effect:         开关无效果（模型不支持思考或参数被忽略）
      - unsupported:       模型无法响应
    """
    from config import create_llm_client, get_llm_api_key, get_llm_entry_by_name

    entry = get_llm_entry_by_name(name)
    if not entry:
        return {"name": name, "supported": False, "mode": "unsupported",
                "message": f"配置中不存在模型: {name}", "off": {}, "on": {}}

    client = create_llm_client(entry["base_url"], entry.get("api_key") or get_llm_api_key(),
                               timeout=90.0, max_retries=0)
    off = await _probe_thinking_once(entry, client, False)
    if not off["ok"]:
        return {"name": name, "supported": False, "mode": "unsupported",
                "message": f"模型无响应: {off['error']}", "off": off, "on": {}}

    on = await _probe_thinking_once(entry, client, True)
    if not on["ok"]:
        return {"name": name, "supported": False, "mode": "unsupported",
                "message": f"开启思考后模型无响应: {on['error']}", "off": off, "on": on}

    if on["reasoning_chars"] > 0:
        mode, supported = "reasoning_channel", True
        message = f"支持思考，推理过程走独立通道（{on['reasoning_chars']} 字），可安全开启"
    else:
        head = on["content_head"].lower()
        leaked = any(m in head for m in _THINKING_LEAK_MARKERS)
        grew = on["content_chars"] > max(off["content_chars"] * 3, off["content_chars"] + 200)
        if leaked or grew:
            mode, supported = "content_mixed", True
            message = (f"⚠️ 开启后思考内容会混入正文（{off['content_chars']}→{on['content_chars']} 字），"
                       f"将挤占输出额度并可能干扰报告 JSON 解析，不建议用于报告生成")
        else:
            mode, supported = "no_effect", False
            message = "该模型不支持思考开关（开启后输出无变化），保持默认即可"

    return {"name": name, "supported": supported, "mode": mode, "message": message,
            "off": off, "on": on}


async def chat_stream(messages, max_tokens=2048, temperature=0.3, _emit=None, debug=False, caller="chat_stream",
                      model_override: str = "", base_url_override: str = "",
                      timeout: float | None = None, max_retries: int | None = None,
                      enable_thinking: bool | None = None, api_key_override: str = ""):
    """异步流式 LLM 调用，使用 OpenAI SDK。首选模型失败时自动降级到候选链中的下一个模型。

    Args:
        messages: 消息列表
        max_tokens: 最大 token 数
        temperature: 温度参数
        _emit: SSE 事件发射器
        debug: 是否打印调试信息
        caller: 调用者标识
        model_override: 覆盖模型名（为空则用全局激活模型）
        base_url_override: 覆盖 base_url（为空则用全局激活地址）
        timeout: 单次请求超时秒数（None=SDK默认600s）
        max_retries: SDK 内部重试次数（None=SDK默认2；快速失败链路传0）
        enable_thinking: 是否开启模型思考（None=不干预；False 通过 chat_template_kwargs 关闭 Qwen3 思考）

    Returns:
        str: 完整生成的文本
    """
    total_chars, est_tokens = _estimate_tokens(messages)
    if debug:
        logger.info(f"[{caller}] 发送请求: {len(messages)} messages, {total_chars} chars, 估算 ~{est_tokens} tokens")

    candidates = _build_candidate_chain(model_override, base_url_override,
                                        timeout=timeout, max_retries=max_retries,
                                        api_key_override=api_key_override)
    last_error = None

    for idx, (model, base_url, client) in enumerate(candidates):
        if idx > 0:
            logger.warning(f"[{caller}] 模型 {candidates[idx-1][0]} 失败，自动降级到 {model} ({base_url})")
            if _emit:
                _emit("notice", {"content": f"模型 {candidates[idx-1][0]} 不可用，已自动切换到 {model}"})
        try:
            create_kwargs = {}
            if enable_thinking is not None:
                # Qwen3 系列通过 chat_template_kwargs 控制思考开关（vLLM/OpenAI 兼容接口）
                create_kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": enable_thinking}}
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                **create_kwargs,
            )

            full_text = ""
            reasoning_text = ""
            token_count = 0
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # 思考内容走独立的 reasoning_content 通道（开启思考的模型），不计入正文
                reasoning_delta = getattr(delta, "reasoning_content", None)
                if reasoning_delta:
                    reasoning_text += reasoning_delta
                    if _emit:
                        _emit("reasoning", {"content": reasoning_delta})
                content = delta.content
                if content:
                    token_count += 1
                    if _emit:
                        _emit("token", {"content": content})
                    full_text += content

            if debug:
                logger.info(f"[{caller}] 完成: 模型={model}, 收到 {token_count} tokens, {len(full_text)} chars"
                            f"{f', reasoning {len(reasoning_text)} chars' if reasoning_text else ''}")
            return full_text.strip()

        except Exception as e:
            last_error = e
            if _is_retryable_error(e) and idx < len(candidates) - 1:
                logger.warning(f"[{caller}] 模型 {model} 调用失败({type(e).__name__})，尝试下一个候选模型")
                continue
            logger.error(f"[{caller}] LLM 调用失败: 模型={model}, {type(e).__name__}: {e}", exc_info=True)
            raise

    raise last_error


# =============================================================================
# 非流式 LLM 调用（支持 Tool Calling）
# =============================================================================
import json

async def chat_with_tools(messages, tools=None, max_tokens=512, temperature=0.3, debug=False):
    """非流式 LLM 调用，支持 Tool Calling，使用 OpenAI SDK。

    当传入 tools 参数时，API 可能返回 tool_calls 而非 content。
    返回 (content, tool_calls) 元组，其中 tool_calls 为列表或 None。

    Args:
        messages: 消息列表
        tools: OpenAI 兼容的 tools schema 列表，为 None 时不启用工具
        max_tokens: 最大 token 数
        temperature: 温度参数
        debug: 是否打印调试信息

    Returns:
        tuple: (content_text, tool_calls_list)
            - content_text: 文本回复（可能为 None）
            - tool_calls_list: tool_calls 列表，每项为 {"id": str, "name": str, "arguments": dict}
    """
    total_chars, est_tokens = _estimate_tokens(messages)
    if debug:
        logger.info(f"[chat_with_tools] 发送请求: {len(messages)} messages, {total_chars} chars, "
                     f"tools={len(tools) if tools else 0}")

    try:
        from config import (create_llm_client, get_llm_api_key, get_llm_base_url,
                            get_llm_entry_by_name, get_llm_model)
        active_entry = get_llm_entry_by_name("") or {}
        active_key = active_entry.get("api_key") or get_llm_api_key()
        client = create_llm_client(get_llm_base_url(), active_key)
        response = await client.chat.completions.create(
            model=get_llm_model(),
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools if tools else None,
        )

        choice = response.choices[0]
        message = choice.message

        content = message.content or ""
        raw_tool_calls = message.tool_calls or []

        tool_calls = []
        if raw_tool_calls:
            for tc in raw_tool_calls:
                func_name = tc.function.name
                func_args_str = tc.function.arguments

                try:
                    func_args = json.loads(func_args_str)
                except json.JSONDecodeError:
                    func_args = {}

                tool_calls.append({
                    "id": tc.id,
                    "name": func_name,
                    "arguments": func_args,
                })

        if debug:
            if tool_calls:
                logger.info(f"[chat_with_tools] 完成: {len(tool_calls)} tool_calls: "
                             f"{[tc['name'] for tc in tool_calls]}")
            else:
                logger.info(f"[chat_with_tools] 完成: {len(content or '')} chars content")

        return content, tool_calls if tool_calls else None

    except APIConnectionError as e:
        logger.error(f"[chat_with_tools] LLM 连接失败: {e}", exc_info=True)
        raise
    except APITimeoutError:
        logger.error(f"[chat_with_tools] LLM 请求超时", exc_info=True)
        raise
    except APIStatusError as e:
        logger.error(f"[chat_with_tools] LLM 返回错误: HTTP {e.status_code} - {e.message}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"[chat_with_tools] 未知错误: {e}", exc_info=True)
        raise


# =============================================================================
# Rerank 带重试
# =============================================================================
@retry()
def rerank_with_retry(query, documents, top_n=3):
    return rerank_documents(query, documents, top_n=top_n)
