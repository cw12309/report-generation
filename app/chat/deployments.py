"""API Deployment security, public-event mapping, and conversation locking."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional


DEPLOYMENT_KEY_HEADER = "X-Deployment-API-Key"
DEPLOYMENT_STATUSES = {"active", "disabled"}


def normalize_selected_kbs(
    selected_kbs: list[str],
    active_knowledge_bases: dict[str, dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Normalize a Deployment scope and require every KB to be currently active."""
    normalized = list(dict.fromkeys(
        kb_id.strip() for kb_id in selected_kbs if kb_id and kb_id.strip()
    ))
    if not normalized:
        raise ValueError("至少选择一个 Active 知识库")
    invalid = [kb_id for kb_id in normalized if kb_id not in active_knowledge_bases]
    if invalid:
        raise ValueError(f"知识库不存在或不可用: {', '.join(invalid)}")
    return normalized, [active_knowledge_bases[kb_id] for kb_id in normalized]


def generate_deployment_id() -> str:
    """Return a non-sequential public deployment identifier."""
    return f"dep_{secrets.token_urlsafe(12)}"


def generate_conversation_id() -> str:
    """Return a non-sequential public conversation identifier."""
    return f"conv_{secrets.token_urlsafe(16)}"


def generate_internal_session_id() -> str:
    """Return an opaque session ID used only by the existing stateful pipeline."""
    return f"api_session_{secrets.token_urlsafe(18)}"


def generate_api_key() -> tuple[str, str, str]:
    """Generate a high-entropy key and return plaintext, SHA-256 hash, and prefix.

    The plaintext value is deliberately returned only to the creation/rotation
    caller.  Persistence receives only the deterministic hash and display
    prefix.  SHA-256 is appropriate here because the generated secret contains
    256 bits of entropy and is not derived from a human password.
    """
    secret = f"rg_live_{secrets.token_urlsafe(32)}"
    prefix = secret[:16]
    return secret, hash_api_key(secret), prefix


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_api_key(api_key: str, expected_hash: str) -> bool:
    """Compare a candidate key with a persisted hash in constant time."""
    if not api_key or not expected_hash:
        return False
    candidate_hash = hash_api_key(api_key)
    return secrets.compare_digest(
        candidate_hash.encode("ascii"),
        expected_hash.encode("ascii"),
    )


def mask_api_key_prefix(prefix: str) -> str:
    return f"{prefix}****" if prefix else ""


def map_public_event(
    event_type: str,
    data: dict[str, Any],
    *,
    conversation_id: str,
) -> Optional[dict[str, Any]]:
    """Map internal pipeline events to the stable public SSE contract."""
    if event_type == "status":
        message = str(data.get("message") or "正在处理请求")
        lowered = message.lower()
        if "检索" in message or "search" in lowered:
            stage = "retrieving"
        elif "报告" in message or "生成" in message:
            stage = "generating"
        elif data.get("phase") == "done":
            stage = "completed"
        else:
            stage = "processing"
        return {"type": "status", "stage": stage, "message": message}

    if event_type in {"search", "recall", "rerank"}:
        messages = {
            "search": "正在检索知识库",
            "recall": "正在筛选候选报告",
            "rerank": "正在精排参考报告",
        }
        return {
            "type": "status",
            "stage": "retrieving",
            "message": messages[event_type],
        }

    if event_type == "message":
        return {"type": "message", "content": str(data.get("content") or "")}

    if event_type == "report":
        event = {"type": "report", "content": str(data.get("content") or "")}
        structured = data.get("data")
        if isinstance(structured, dict):
            event["data"] = structured
        return event

    if event_type == "ambiguous":
        return {
            "type": "ambiguous",
            "options": data.get("options") if isinstance(data.get("options"), list) else [],
            "message": str(data.get("message") or "请选择一个诊断后继续"),
        }

    if event_type == "error":
        return {
            "type": "error",
            "code": str(data.get("code") or "AGENT_ERROR"),
            "message": str(data.get("message") or "处理请求失败"),
        }

    if event_type == "done":
        return {"type": "done", "conversation_id": conversation_id}

    return None


class ConversationLockRegistry:
    """Serialize one public conversation while allowing different ones in parallel.

    Locks are reference-counted and removed when no request is waiting or using
    them, avoiding an unbounded registry for one-shot conversations.
    """

    def __init__(self) -> None:
        self._guard = asyncio.Lock()
        self._entries: dict[tuple[str, str], tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, deployment_id: str, conversation_id: str) -> AsyncIterator[None]:
        key = (deployment_id, conversation_id)
        async with self._guard:
            lock, users = self._entries.get(key, (asyncio.Lock(), 0))
            self._entries[key] = (lock, users + 1)

        try:
            async with lock:
                yield
        finally:
            async with self._guard:
                current = self._entries.get(key)
                if current is None:
                    return
                current_lock, users = current
                if users <= 1:
                    self._entries.pop(key, None)
                else:
                    self._entries[key] = (current_lock, users - 1)
