"""Shared in-process observability store for Monitor, traces, and logs."""

from __future__ import annotations

import contextvars
import logging
import math
import re
import threading
import time
import uuid
from collections import Counter, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional


_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "observability_request_id", default=""
)
_session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "observability_session_id", default=""
)

_SECRET_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|secret|password|passwd|token|credential)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+|\bsk-[A-Za-z0-9_-]{8,}\b"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(query|prompt|content|message|title|clinical_history|"
    r"user_input|assistant_output|arguments|changes|slots|diagnosis|"
    r"selected_diagnosis|body_part)\s*[:=]\s*([^,;\]\}]+)"
)
_SENSITIVE_LABEL_RE = re.compile(
    r"(?i)(?:无法解析\s*JSON|实体更新|实体意图|上下文消解|查询改写|"
    r"用户已选择诊断|歧义.*选项|Embedding\s*失败)\s*[:：]?.*$"
)
_SERIALIZED_MAPPING_RE = re.compile(r"\{.*\}")
_SERIALIZED_SEQUENCE_RE = re.compile(r"\[[^\]\r\n]{1,500}\]")
_IGNORED_LOGGERS = ("uvicorn.access", "httpx", "httpcore", "watchfiles")
_TRACE_DETAIL_KEYS = {
    "code",
    "intent",
    "phase",
    "caller",
    "tool",
    "tool_name",
    "current",
    "total",
    "count",
    "candidate_count",
    "result_count",
    "top_k",
    "rerank_top_k",
    "percent",
    "total_time",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:20]}"


def normalize_request_id(value: str) -> str:
    candidate = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate):
        return candidate
    return new_request_id()


def current_request_id() -> str:
    return _request_id_var.get()


def current_session_id() -> str:
    return _session_id_var.get()


@contextmanager
def bind_observability_context(
    *, request_id: str = "", session_id: str = ""
) -> Iterator[None]:
    request_token = _request_id_var.set(request_id or _request_id_var.get())
    session_token = _session_id_var.set(session_id or _session_id_var.get())
    try:
        yield
    finally:
        _session_id_var.reset(session_token)
        _request_id_var.reset(request_token)


def redact_text(value: Any, *, max_length: int = 500) -> str:
    text = str(value or "")
    text = _SECRET_VALUE_RE.sub(
        lambda match: f"{match.group(1)}[REDACTED]" if match.group(1) else "[REDACTED]",
        text,
    )
    text = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        text,
    )
    text = _SENSITIVE_LABEL_RE.sub("[REDACTED PATIENT DATA]", text)
    text = _SERIALIZED_MAPPING_RE.sub("{[REDACTED]}", text)
    text = _SERIALIZED_SEQUENCE_RE.sub("[REDACTED]", text)
    text = text.replace("\r", " ").replace("\n", " ")
    if len(text) > max_length:
        return f"{text[:max_length]}…"
    return text


def sanitize_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Recursively sanitize structured values before they reach Monitor."""
    if _SECRET_KEY_RE.search(str(key or "")):
        return "[REDACTED]"
    if depth >= 4:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            str(child_key): sanitize_value(
                child_value,
                key=str(child_key),
                depth=depth + 1,
            )
            for child_key, child_value in list(value.items())[:30]
        }
    if isinstance(value, (list, tuple, set)):
        return [
            sanitize_value(item, key=key, depth=depth + 1)
            for item in list(value)[:30]
        ]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value)


def _safe_trace_details(event_type: str, data: Optional[dict]) -> dict:
    payload = data if isinstance(data, dict) else {}
    details = {
        key: sanitize_value(value, key=key)
        for key, value in payload.items()
        if key in _TRACE_DETAIL_KEYS
    }
    if event_type == "entity_update":
        changes = payload.get("changes")
        if isinstance(changes, dict):
            details["changed_fields"] = sorted(str(key) for key in changes)[:20]
    if event_type == "query_plan":
        filters = payload.get("filters")
        if isinstance(filters, dict):
            details["filter_fields"] = sorted(str(key) for key in filters)[:20]
    if event_type == "tool_executed":
        tool_name = payload.get("name") or payload.get("tool")
        if tool_name:
            details["tool"] = redact_text(tool_name, max_length=80)
    if event_type == "intent" and isinstance(payload.get("tools"), list):
        details["tools"] = [
            redact_text(tool_name, max_length=80)
            for tool_name in payload["tools"][:10]
        ]
    if event_type in {"report", "message"}:
        details["generated"] = True
    return details


def pipeline_stage(event_type: str, data: Optional[dict] = None) -> Optional[str]:
    event_type = str(event_type or "").lower()
    payload = data if isinstance(data, dict) else {}
    if event_type == "intent" and payload.get("tools"):
        return "tool"
    if event_type in {"intent", "entity_update", "intent_switch", "ambiguous"}:
        return "entity"
    if event_type in {"memory_retrieval", "context_usage", "context_resolve"}:
        return "memory"
    if event_type == "tool_executed":
        return "tool"
    if event_type in {
        "query_plan",
        "query_rewrite",
        "recall",
        "rerank",
        "search",
        "search_result",
        "cache_hit",
    }:
        return "rag"
    if event_type == "error":
        code = str(payload.get("code") or "").upper()
        return "recovery" if code.startswith("RECOVERY_") else "error"
    if event_type == "status":
        message = str(payload.get("message") or "")
        if "重试" in message or "recovery" in message.lower():
            return "recovery"
        return None
    if event_type in {"report", "message"}:
        return "report"
    if event_type == "done":
        return "done"
    return None


class ObservabilityStore:
    def __init__(
        self,
        *,
        max_traces: int = 500,
        max_logs: int = 2000,
        max_trace_events: int = 120,
    ) -> None:
        self.max_traces = max(10, int(max_traces))
        self.max_logs = max(100, int(max_logs))
        self.max_trace_events = max(20, int(max_trace_events))
        self._lock = threading.RLock()
        self._started_at = time.time()
        self._traces: dict[str, dict] = {}
        self._trace_order: deque[str] = deque()
        self._logs: deque[dict] = deque(maxlen=self.max_logs)
        self._durations: deque[float] = deque(maxlen=2000)
        self._request_count = 0
        self._success_count = 0
        self._error_count = 0
        self._active_request_ids: set[str] = set()
        self._log_sequence = 0

    def reset(self) -> None:
        with self._lock:
            self._started_at = time.time()
            self._traces.clear()
            self._trace_order.clear()
            self._logs.clear()
            self._durations.clear()
            self._request_count = 0
            self._success_count = 0
            self._error_count = 0
            self._active_request_ids.clear()
            self._log_sequence = 0

    def start_request(
        self,
        request_id: str,
        *,
        method: str,
        path: str,
        session_id: str = "",
    ) -> None:
        now = _utc_now()
        trace = {
            "request_id": request_id,
            "method": str(method or "").upper(),
            "path": redact_text(path, max_length=240),
            "session_id": redact_text(session_id, max_length=128),
            "started_at": now,
            "finished_at": None,
            "status_code": None,
            "duration_ms": None,
            "state": "running",
            "events": [
                {
                    "stage": "request",
                    "event_type": "request",
                    "timestamp": now,
                    "details": {
                        "method": str(method or "").upper(),
                        "path": redact_text(path, max_length=240),
                    },
                }
            ],
        }
        with self._lock:
            if request_id not in self._traces:
                self._trace_order.append(request_id)
            self._traces[request_id] = trace
            self._active_request_ids.add(request_id)
            while len(self._trace_order) > self.max_traces:
                expired = self._trace_order.popleft()
                self._traces.pop(expired, None)
                self._active_request_ids.discard(expired)

    def add_trace_stage(
        self,
        request_id: str,
        stage: str,
        *,
        event_type: str = "",
        details: Optional[dict] = None,
        session_id: str = "",
    ) -> None:
        if not request_id or not stage:
            return
        with self._lock:
            trace = self._traces.get(request_id)
            if trace is None:
                self.start_request(
                    request_id,
                    method="INTERNAL",
                    path="agent_pipeline",
                    session_id=session_id,
                )
                trace = self._traces[request_id]
            if session_id and not trace.get("session_id"):
                trace["session_id"] = redact_text(session_id, max_length=128)
            if len(trace["events"]) >= self.max_trace_events:
                return
            trace["events"].append(
                {
                    "stage": str(stage),
                    "event_type": str(event_type or stage),
                    "timestamp": _utc_now(),
                    "details": sanitize_value(details or {}),
                }
            )

    def record_pipeline_event(
        self,
        request_id: str,
        event_type: str,
        data: Optional[dict] = None,
        *,
        session_id: str = "",
    ) -> None:
        stage = pipeline_stage(event_type, data)
        if stage:
            self.add_trace_stage(
                request_id,
                stage,
                event_type=event_type,
                details=_safe_trace_details(event_type, data),
                session_id=session_id,
            )

    def finish_request(
        self,
        request_id: str,
        *,
        status_code: int,
        duration_ms: float,
    ) -> None:
        status_code = int(status_code or 500)
        duration_ms = max(0.0, float(duration_ms or 0.0))
        with self._lock:
            trace = self._traces.get(request_id)
            if trace is not None:
                trace["finished_at"] = _utc_now()
                trace["status_code"] = status_code
                trace["duration_ms"] = round(duration_ms, 2)
                trace["state"] = "success" if status_code < 400 else "error"
            self._active_request_ids.discard(request_id)
            self._request_count += 1
            self._durations.append(duration_ms)
            if status_code < 400:
                self._success_count += 1
            else:
                self._error_count += 1

    def add_log(
        self,
        *,
        level: str,
        module: str,
        message: str,
        request_id: str = "",
        session_id: str = "",
        error_type: str = "",
    ) -> dict:
        with self._lock:
            self._log_sequence += 1
            event = {
                "id": self._log_sequence,
                "timestamp": _utc_now(),
                "level": str(level or "INFO").upper(),
                "module": redact_text(module, max_length=160),
                "message": redact_text(message),
                "session_id": redact_text(session_id, max_length=128),
                "request_id": redact_text(request_id, max_length=128),
            }
            if error_type:
                event["error_type"] = redact_text(error_type, max_length=120)
            self._logs.append(event)
            return dict(event)

    def list_logs(
        self,
        *,
        after_id: int = 0,
        level: str = "",
        module: str = "",
        session_id: str = "",
        request_id: str = "",
        limit: int = 200,
    ) -> list[dict]:
        normalized_level = str(level or "").upper()
        normalized_module = str(module or "").lower()
        normalized_session = str(session_id or "")
        normalized_request = str(request_id or "")
        with self._lock:
            items = [dict(item) for item in self._logs if item["id"] > int(after_id)]
        filtered = [
            item
            for item in items
            if (not normalized_level or item["level"] == normalized_level)
            and (not normalized_module or normalized_module in item["module"].lower())
            and (not normalized_session or item["session_id"] == normalized_session)
            and (not normalized_request or item["request_id"] == normalized_request)
        ]
        return filtered[-max(1, min(500, int(limit))):]

    def list_traces(self, *, limit: int = 100) -> list[dict]:
        with self._lock:
            request_ids = list(self._trace_order)[-max(1, min(500, int(limit))):]
            traces = [self._traces[request_id] for request_id in reversed(request_ids)]
            return [self._trace_summary(trace) for trace in traces]

    def get_trace(self, request_id: str) -> Optional[dict]:
        with self._lock:
            trace = self._traces.get(request_id)
            return sanitize_value(trace) if trace is not None else None

    @staticmethod
    def _trace_summary(trace: dict) -> dict:
        return {
            key: trace.get(key)
            for key in (
                "request_id",
                "method",
                "path",
                "session_id",
                "started_at",
                "finished_at",
                "status_code",
                "duration_ms",
                "state",
            )
        } | {
            "stages": [event.get("stage") for event in trace.get("events", [])]
        }

    def overview(self) -> dict:
        with self._lock:
            durations = sorted(self._durations)
            average = sum(durations) / len(durations) if durations else 0.0
            if durations:
                percentile_index = max(0, math.ceil(len(durations) * 0.95) - 1)
                p95 = durations[percentile_index]
            else:
                p95 = 0.0
            success_rate = (
                self._success_count * 100 / self._request_count
                if self._request_count
                else 100.0
            )
            level_counts = Counter(item["level"] for item in self._logs)
            recent_errors = [
                dict(item)
                for item in reversed(self._logs)
                if item["level"] in {"ERROR", "CRITICAL"}
            ][:5]
            return {
                "service_status": "healthy" if success_rate >= 95 else "degraded",
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "request_count": self._request_count,
                "success_count": self._success_count,
                "success_rate": round(success_rate, 2),
                "error_count": self._error_count,
                "active_requests": len(self._active_request_ids),
                "average_latency_ms": round(average, 2),
                "p95_latency_ms": round(p95, 2),
                "log_level_counts": dict(level_counts),
                "recent_errors": recent_errors,
            }


class StructuredLogHandler(logging.Handler):
    """Capture sanitized log records without console traceback or raw payloads."""

    def __init__(self, store: ObservabilityStore) -> None:
        super().__init__(level=logging.INFO)
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(_IGNORED_LOGGERS):
                return
            error_type = ""
            if record.exc_info and record.exc_info[0]:
                error_type = record.exc_info[0].__name__
            # Exception arguments commonly contain upstream response bodies or
            # user content. Keep the stable message prefix and exception type,
            # but never format traceback/exception arguments into Monitor.
            # Keep the logging template instead of interpolating args: an arg
            # can be a query, entity mapping, tool payload, or upstream body.
            message = str(record.msg)
            if record.exc_info:
                message = str(record.msg).split(":", 1)[0]
            self.store.add_log(
                level=record.levelname,
                module=record.name,
                message=message,
                request_id=current_request_id(),
                session_id=current_session_id(),
                error_type=error_type,
            )
        except Exception:
            self.handleError(record)


observability_store = ObservabilityStore()


def install_structured_log_handler() -> StructuredLogHandler:
    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, StructuredLogHandler) and handler.store is observability_store:
            return handler
    handler = StructuredLogHandler(observability_store)
    root_logger.addHandler(handler)
    return handler
