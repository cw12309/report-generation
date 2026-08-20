"""会话持久化存储

基于 SQLite 将会话数据（对话历史、实体槽位、上一轮报告、思考过程）持久化到磁盘，
实现会话自动保存、断线恢复、历史会话查询与管理。

数据库路径: data/sessions.db（若目录不存在则自动创建）

表结构:
  - sessions:      会话元数据（id, title, created_at）
  - turns:         对话记录（session_id, turn_index, user_input, assistant_output）
  - session_state: 会话状态（entity_slots JSON, last_report, updated_at）
  - thinking:      思考过程（session_id, turn_index, event_type, event_data JSON）
"""

import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from collections.abc import Iterator
from typing import Any, Optional

from data_pipeline.chunk_id import generate_chunk_id

logger = logging.getLogger(__name__)
KNOWLEDGE_BASE_STATUSES = {
    "uploaded", "processing", "active", "failed", "disabled",
    "recovered", "degraded",
}


class SessionStore:
    """SQLite 会话持久化存储"""

    def __init__(self, db_path: str = "data/sessions.db"):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    # ── 数据库连接管理 ──────────────────────────────────────

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── 建表 ────────────────────────────────────────────────

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id         TEXT PRIMARY KEY,
                    title      TEXT NOT NULL DEFAULT '',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS turns (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL,
                    turn_index      INTEGER NOT NULL,
                    user_input      TEXT NOT NULL DEFAULT '',
                    assistant_output TEXT NOT NULL DEFAULT '',
                    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS session_state (
                    session_id   TEXT UNIQUE NOT NULL,
                    entity_slots TEXT NOT NULL DEFAULT '{}',
                    last_report  TEXT NOT NULL DEFAULT '',
                    last_ambiguity TEXT NOT NULL DEFAULT '{}',
                    selected_kbs TEXT NOT NULL DEFAULT '[]',
                    updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_turns_session
                    ON turns(session_id, turn_index);

                CREATE TABLE IF NOT EXISTS thinking (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_thinking_session
                    ON thinking(session_id, turn_index);

                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    kb_id         VARCHAR(64) PRIMARY KEY,
                    kb_name       VARCHAR(255) NOT NULL,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status        VARCHAR(32) NOT NULL DEFAULT 'active',
                    progress      INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100),
                    source_path   TEXT NOT NULL DEFAULT '',
                    source_name   TEXT NOT NULL DEFAULT '',
                    source_hash   TEXT NOT NULL DEFAULT '',
                    metadata_tree TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_knowledge_bases_status
                    ON knowledge_bases(status, created_at);

                CREATE INDEX IF NOT EXISTS idx_knowledge_bases_source_hash
                    ON knowledge_bases(source_hash);

                CREATE TABLE IF NOT EXISTS api_deployments (
                    deployment_id TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    description   TEXT NOT NULL DEFAULT '',
                    status        TEXT NOT NULL DEFAULT 'active'
                                  CHECK(status IN ('active', 'disabled')),
                    api_key_hash  TEXT NOT NULL,
                    api_key_prefix TEXT NOT NULL,
                    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_used_at  DATETIME,
                    deleted_at    DATETIME
                );

                CREATE INDEX IF NOT EXISTS idx_api_deployments_status
                    ON api_deployments(status, created_at);

                CREATE TABLE IF NOT EXISTS api_deployment_knowledge_bases (
                    deployment_id TEXT NOT NULL,
                    kb_id         TEXT NOT NULL,
                    position      INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (deployment_id, kb_id),
                    FOREIGN KEY (deployment_id) REFERENCES api_deployments(deployment_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_api_deployment_kbs_kb
                    ON api_deployment_knowledge_bases(kb_id, deployment_id);

                CREATE TABLE IF NOT EXISTS api_conversations (
                    deployment_id     TEXT NOT NULL,
                    conversation_id   TEXT NOT NULL,
                    internal_session_id TEXT NOT NULL UNIQUE,
                    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (deployment_id, conversation_id),
                    FOREIGN KEY (deployment_id) REFERENCES api_deployments(deployment_id)
                        ON DELETE RESTRICT,
                    FOREIGN KEY (internal_session_id) REFERENCES sessions(id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_api_conversations_updated
                    ON api_conversations(deployment_id, updated_at);

                CREATE TABLE IF NOT EXISTS knowledge_base_chunks (
                    chunk_id          TEXT PRIMARY KEY,
                    kb_id             VARCHAR(64) NOT NULL,
                    chunk_index       INTEGER NOT NULL,
                    source_file       TEXT NOT NULL DEFAULT '',
                    sheet_name        TEXT NOT NULL DEFAULT '',
                    source_row        INTEGER,
                    modality          TEXT NOT NULL DEFAULT '',
                    level_1           TEXT NOT NULL DEFAULT '',
                    level_2           TEXT NOT NULL DEFAULT '',
                    level_3           TEXT NOT NULL DEFAULT '',
                    level_4           TEXT NOT NULL DEFAULT '',
                    module_name       TEXT NOT NULL DEFAULT '',
                    imaging_findings  TEXT NOT NULL DEFAULT '',
                    imaging_opinion   TEXT NOT NULL DEFAULT '',
                    combined_text     TEXT NOT NULL DEFAULT '',
                    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (kb_id) REFERENCES knowledge_bases(kb_id) ON DELETE CASCADE,
                    UNIQUE (kb_id, chunk_index)
                );

                CREATE INDEX IF NOT EXISTS idx_kb_chunks_kb
                    ON knowledge_base_chunks(kb_id, chunk_index);

                CREATE TABLE IF NOT EXISTS evaluation_state (
                    eval_key       TEXT PRIMARY KEY,
                    status         TEXT NOT NULL DEFAULT 'idle',
                    current        INTEGER NOT NULL DEFAULT 0,
                    total          INTEGER NOT NULL DEFAULT 0,
                    progress       INTEGER NOT NULL DEFAULT 0,
                    current_query  TEXT NOT NULL DEFAULT '',
                    message        TEXT NOT NULL DEFAULT '',
                    result_json    TEXT NOT NULL DEFAULT '{}',
                    started_at     DATETIME,
                    completed_at   DATETIME,
                    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)

        # 迁移：为已有表添加新字段（如果不存在）
        with self._get_conn() as conn:
            try:
                conn.execute("ALTER TABLE session_state ADD COLUMN last_ambiguity TEXT NOT NULL DEFAULT '{}'")
                logger.info("已迁移 session_state 表：添加 last_ambiguity 字段")
            except Exception:
                pass  # 字段已存在，忽略
            try:
                conn.execute("ALTER TABLE session_state ADD COLUMN selected_kbs TEXT NOT NULL DEFAULT '[]'")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE knowledge_bases ADD COLUMN metadata_tree TEXT NOT NULL DEFAULT '{}'")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE knowledge_bases ADD COLUMN source_path TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "ALTER TABLE knowledge_bases ADD COLUMN progress "
                    "INTEGER NOT NULL DEFAULT 0 CHECK(progress BETWEEN 0 AND 100)"
                )
                logger.info("已迁移 knowledge_bases 表：添加 progress 字段")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "ALTER TABLE knowledge_bases ADD COLUMN source_name TEXT NOT NULL DEFAULT ''"
                )
                logger.info("已迁移 knowledge_bases 表：添加 source_name 字段")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "ALTER TABLE knowledge_bases ADD COLUMN source_hash TEXT NOT NULL DEFAULT ''"
                )
                logger.info("已迁移 knowledge_bases 表：添加 source_hash 字段")
            except sqlite3.OperationalError:
                pass

    # ── 评测现场 ────────────────────────────────────────────

    def save_evaluation_runtime_state(
        self,
        *,
        status: str,
        current: int = 0,
        total: int = 0,
        progress: int = 0,
        current_query: str = "",
        message: str = "",
        eval_key: str = "retrieval",
    ) -> None:
        """Persist the latest task progress without overwriting the last result."""
        safe_progress = max(0, min(100, int(progress)))
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_state (
                    eval_key, status, current, total, progress,
                    current_query, message, started_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(eval_key) DO UPDATE SET
                    status=excluded.status,
                    current=excluded.current,
                    total=excluded.total,
                    progress=excluded.progress,
                    current_query=excluded.current_query,
                    message=excluded.message,
                    started_at=CASE
                        WHEN excluded.status = 'running' AND evaluation_state.status != 'running'
                        THEN CURRENT_TIMESTAMP
                        ELSE evaluation_state.started_at
                    END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    eval_key,
                    str(status or "idle"),
                    max(0, int(current)),
                    max(0, int(total)),
                    safe_progress,
                    str(current_query or ""),
                    str(message or ""),
                ),
            )

    def save_evaluation_result(
        self,
        result: dict,
        *,
        eval_key: str = "retrieval",
    ) -> None:
        """Atomically persist the complete latest evaluation result."""
        result_json = json.dumps(result or {}, ensure_ascii=False)
        total = int((result or {}).get("metrics", {}).get("total", 0) or 0)
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_state (
                    eval_key, status, current, total, progress, message,
                    result_json, started_at, completed_at, updated_at
                ) VALUES (?, 'completed', ?, ?, 100, ?, ?,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(eval_key) DO UPDATE SET
                    status='completed',
                    current=excluded.current,
                    total=excluded.total,
                    progress=100,
                    current_query='',
                    message=excluded.message,
                    result_json=excluded.result_json,
                    completed_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (eval_key, total, total, "评测完成", result_json),
            )

    def get_evaluation_state(self, eval_key: str = "retrieval") -> Optional[dict]:
        """Load the persisted task state and deserialize its complete result."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM evaluation_state WHERE eval_key = ?",
                (eval_key,),
            ).fetchone()
        if row is None:
            return None
        state = dict(row)
        try:
            result = json.loads(state.pop("result_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            result = {}
        state["result"] = result if isinstance(result, dict) else {}
        return state

    def invalidate_evaluation_result(self, eval_key: str = "retrieval") -> None:
        """Clear stale metrics after the underlying test dataset changes."""
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO evaluation_state (
                    eval_key, status, result_json, message, updated_at
                ) VALUES (?, 'idle', '{}', '测试集已更新，等待重新评测', CURRENT_TIMESTAMP)
                ON CONFLICT(eval_key) DO UPDATE SET
                    status='idle',
                    current=0,
                    total=0,
                    progress=0,
                    current_query='',
                    message='测试集已更新，等待重新评测',
                    result_json='{}',
                    completed_at=NULL,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (eval_key,),
            )

    # ── 会话元数据 ──────────────────────────────────────────

    def create_session(self, session_id: str, title: str = "") -> str:
        """创建新会话，返回 session_id"""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title) VALUES (?, ?)",
                (session_id, title),
            )
            conn.execute(
                "INSERT INTO session_state (session_id, entity_slots, last_report) VALUES (?, '{}', '')",
                (session_id,),
            )
        logger.info("会话已创建: %s", session_id)
        return session_id

    def update_title(self, session_id: str, title: str):
        """更新会话标题"""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )

    # ── 对话记录 ────────────────────────────────────────────

    def save_turn(self, session_id: str, turn_index: int, user_input: str, assistant_output: str):
        """保存单轮对话"""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO turns (session_id, turn_index, user_input, assistant_output) VALUES (?, ?, ?, ?)",
                (session_id, turn_index, user_input, assistant_output),
            )

    def get_turns(self, session_id: str) -> list[dict]:
        """获取会话的所有对话记录，按 turn_index 升序"""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT turn_index, user_input, assistant_output FROM turns WHERE session_id = ? ORDER BY turn_index ASC",
                (session_id,),
            ).fetchall()
        return [{"turn_index": r["turn_index"], "user_input": r["user_input"], "assistant_output": r["assistant_output"]} for r in rows]

    # ── 会话状态 ────────────────────────────────────────────

    def save_state(self, session_id: str, entity_slots: dict, last_report: str,
                   last_ambiguity: dict = None, selected_kbs: Optional[list[str]] = None):
        """保存会话上下文状态（实体槽位 + 上一轮报告 + 歧义缓存）"""
        slots_json = json.dumps(entity_slots, ensure_ascii=False)
        ambiguity_json = json.dumps(last_ambiguity or {}, ensure_ascii=False)
        selected_kbs_json = json.dumps(selected_kbs or [], ensure_ascii=False)
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO session_state (session_id, entity_slots, last_report, last_ambiguity, selected_kbs, updated_at) "
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(session_id) DO UPDATE SET entity_slots=excluded.entity_slots, last_report=excluded.last_report, "
                "last_ambiguity=excluded.last_ambiguity, selected_kbs=excluded.selected_kbs, updated_at=CURRENT_TIMESTAMP",
                (session_id, slots_json, last_report, ambiguity_json, selected_kbs_json),
            )

    def load_state(self, session_id: str) -> Optional[dict]:
        """加载会话状态，返回 {entity_slots, last_report, last_ambiguity} 或 None"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT entity_slots, last_report, last_ambiguity, selected_kbs FROM session_state WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            slots = json.loads(row["entity_slots"])
        except (json.JSONDecodeError, TypeError):
            slots = {}
        try:
            ambiguity = json.loads(row["last_ambiguity"])
        except (json.JSONDecodeError, TypeError):
            ambiguity = {}
        try:
            selected_kbs = json.loads(row["selected_kbs"])
        except (json.JSONDecodeError, TypeError):
            selected_kbs = []
        return {
            "entity_slots": slots,
            "last_report": row["last_report"] or "",
            "last_ambiguity": ambiguity,
            "selected_kbs": selected_kbs if isinstance(selected_kbs, list) else [],
        }

    # ── 思考过程 ────────────────────────────────────────────

    def save_thinking(self, session_id: str, turn_index: int, events: list[dict]):
        """保存一轮对话的思考过程事件列表"""
        if not events:
            return
        with self._get_conn() as conn:
            conn.executemany(
                "INSERT INTO thinking (session_id, turn_index, event_type, event_data) VALUES (?, ?, ?, ?)",
                [(session_id, turn_index, e["type"], json.dumps(e.get("data", {}), ensure_ascii=False)) for e in events],
            )

    def get_thinking(self, session_id: str, turn_index: Optional[int] = None) -> list[dict]:
        """获取思考过程事件列表"""
        with self._get_conn() as conn:
            if turn_index is not None:
                rows = conn.execute(
                    "SELECT event_type, event_data FROM thinking WHERE session_id = ? AND turn_index = ? ORDER BY id ASC",
                    (session_id, turn_index),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT turn_index, event_type, event_data FROM thinking WHERE session_id = ? ORDER BY turn_index ASC, id ASC",
                    (session_id,),
                ).fetchall()
        result = []
        for r in rows:
            try:
                data = json.loads(r["event_data"])
            except (json.JSONDecodeError, TypeError):
                data = {}
            entry = {"type": r["event_type"], "data": data}
            if "turn_index" in r.keys():
                entry["turn_index"] = r["turn_index"]
            result.append(entry)
        return result

    # ── 完整加载/恢复 ───────────────────────────────────────

    def load_session(self, session_id: str) -> Optional[dict]:
        """加载完整会话数据，返回 {title, turns, state, thinking} 或 None"""
        with self._get_conn() as conn:
            session_row = conn.execute(
                "SELECT id, title, created_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if session_row is None:
            return None

        turns = self.get_turns(session_id)
        state = self.load_state(session_id) or {"entity_slots": {}, "last_report": ""}
        thinking = self.get_thinking(session_id)

        return {
            "id": session_row["id"],
            "title": session_row["title"],
            "created_at": session_row["created_at"],
            "turns": turns,
            "state": state,
            "thinking": thinking,
        }

    def session_exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return row is not None

    # ── 会话列表 ────────────────────────────────────────────

    def list_sessions(self) -> list[dict]:
        """查询所有会话，按 created_at 倒序返回"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT s.id, s.title, s.created_at,
                          (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turn_count
                   FROM sessions s
                   ORDER BY s.created_at DESC"""
            ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "turn_count": r["turn_count"],
            }
            for r in rows
        ]

    # ── 删除 ────────────────────────────────────────────────

    def delete_session(self, session_id: str):
        """级联删除会话、对话记录和状态"""
        with self._get_conn() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        logger.info("会话已删除: %s", session_id)

    # ── 知识库元数据 ────────────────────────────────────────

    def create_knowledge_base(self, kb_id: str, kb_name: str,
                              status: str = "uploaded",
                              metadata_tree: Optional[dict] = None,
                              source_path: str = "",
                              source_name: str = "",
                              source_hash: str = "") -> dict:
        if status not in KNOWLEDGE_BASE_STATUSES:
            raise ValueError(f"不支持的知识库状态: {status}")
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO knowledge_bases (kb_id, kb_name, status, source_path, "
                "source_name, source_hash, metadata_tree) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kb_id, kb_name, status, source_path, source_name, source_hash,
                 json.dumps(metadata_tree or {}, ensure_ascii=False)),
            )
        return self.get_knowledge_base(kb_id)

    def update_knowledge_base(self, kb_id: str, *, status: Optional[str] = None,
                              metadata_tree: Optional[dict] = None,
                              progress: Optional[int] = None,
                              source_hash: Optional[str] = None,
                              source_name: Optional[str] = None) -> None:
        assignments, values = [], []
        if status is not None:
            if status not in KNOWLEDGE_BASE_STATUSES:
                raise ValueError(f"不支持的知识库状态: {status}")
            assignments.append("status = ?")
            values.append(status)
        if metadata_tree is not None:
            assignments.append("metadata_tree = ?")
            values.append(json.dumps(metadata_tree, ensure_ascii=False))
        if progress is not None:
            progress = int(progress)
            if not 0 <= progress <= 100:
                raise ValueError("知识库构建进度必须在 0-100 之间")
            assignments.append("progress = ?")
            values.append(progress)
        if source_hash is not None:
            assignments.append("source_hash = ?")
            values.append(source_hash)
        if source_name is not None:
            assignments.append("source_name = ?")
            values.append(source_name)
        if assignments:
            values.append(kb_id)
            with self._get_conn() as conn:
                conn.execute(
                    f"UPDATE knowledge_bases SET {', '.join(assignments)} WHERE kb_id = ?",
                    values,
                )

    def update_knowledge_base_progress(self, kb_id: str, progress: int) -> None:
        """更新构建进度；每次使用独立短事务，适合后台任务节流调用。"""
        self.update_knowledge_base(kb_id, progress=progress)

    @staticmethod
    def _knowledge_base_row(row: sqlite3.Row) -> dict:
        result = dict(row)
        try:
            result["metadata_tree"] = json.loads(result.get("metadata_tree") or "{}")
        except (json.JSONDecodeError, TypeError):
            result["metadata_tree"] = {}
        result["progress"] = min(100, max(0, int(result.get("progress") or 0)))
        return result

    def get_knowledge_base(self, kb_id: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT kb_id, kb_name, created_at, status, progress, source_path, "
                "source_name, source_hash, metadata_tree "
                "FROM knowledge_bases WHERE kb_id = ?", (kb_id,),
            ).fetchone()
        return self._knowledge_base_row(row) if row else None

    def list_knowledge_bases(self, status: Optional[str] = None) -> list[dict]:
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT kb_id, kb_name, created_at, status, progress, source_path, "
                    "source_name, source_hash, metadata_tree "
                    "FROM knowledge_bases WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT kb_id, kb_name, created_at, status, progress, source_path, "
                    "source_name, source_hash, metadata_tree "
                    "FROM knowledge_bases ORDER BY created_at DESC"
                ).fetchall()
        return [self._knowledge_base_row(row) for row in rows]

    def delete_knowledge_base(self, kb_id: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
        return cursor.rowcount > 0

    def upsert_knowledge_base_from_catalog(
        self,
        kb_id: str,
        kb_name: str,
        source_name: str,
        source_hash: str,
    ) -> dict:
        existing = self.get_knowledge_base(kb_id)
        if existing is not None:
            return existing
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO knowledge_bases (kb_id, kb_name, status, source_name, "
                "source_hash, metadata_tree) VALUES (?, ?, 'recovered', ?, ?, '{}')",
                (kb_id, kb_name, source_name, source_hash),
            )
        return self.get_knowledge_base(kb_id)

    def find_knowledge_base_by_hash(self, source_hash: str) -> Optional[dict]:
        if not source_hash:
            return None
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_bases WHERE source_hash = ? ORDER BY created_at DESC LIMIT 1",
                (source_hash,),
            ).fetchone()
        return self._knowledge_base_row(row) if row else None

    # ── API Deployments ─────────────────────────────────────

    @staticmethod
    def _api_deployment_row(row: sqlite3.Row) -> dict:
        return {
            "deployment_id": row["deployment_id"],
            "name": row["name"],
            "description": row["description"] or "",
            "status": row["status"],
            "api_key_hash": row["api_key_hash"],
            "api_key_prefix": row["api_key_prefix"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_used_at": row["last_used_at"],
            "deleted_at": row["deleted_at"],
        }

    def create_api_deployment(
        self,
        *,
        deployment_id: str,
        name: str,
        description: str,
        selected_kbs: list[str],
        api_key_hash: str,
        api_key_prefix: str,
    ) -> dict:
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO api_deployments (
                    deployment_id, name, description, status,
                    api_key_hash, api_key_prefix
                ) VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (deployment_id, name, description, api_key_hash, api_key_prefix),
            )
            conn.executemany(
                """
                INSERT INTO api_deployment_knowledge_bases (
                    deployment_id, kb_id, position
                ) VALUES (?, ?, ?)
                """,
                [(deployment_id, kb_id, index) for index, kb_id in enumerate(selected_kbs)],
            )
        return self.get_api_deployment(deployment_id)

    def _load_api_deployment_kbs(self, conn: sqlite3.Connection, deployment_id: str) -> list[dict]:
        rows = conn.execute(
            """
            SELECT dkb.kb_id, dkb.position, kb.kb_name, kb.status
            FROM api_deployment_knowledge_bases dkb
            LEFT JOIN knowledge_bases kb ON kb.kb_id = dkb.kb_id
            WHERE dkb.deployment_id = ?
            ORDER BY dkb.position ASC, dkb.kb_id ASC
            """,
            (deployment_id,),
        ).fetchall()
        return [
            {
                "kb_id": row["kb_id"],
                "kb_name": row["kb_name"] or row["kb_id"],
                "status": row["status"] or "missing",
            }
            for row in rows
        ]

    def get_api_deployment(
        self,
        deployment_id: str,
        *,
        include_deleted: bool = False,
    ) -> Optional[dict]:
        with self._get_conn() as conn:
            sql = "SELECT * FROM api_deployments WHERE deployment_id = ?"
            if not include_deleted:
                sql += " AND deleted_at IS NULL"
            row = conn.execute(sql, (deployment_id,)).fetchone()
            if row is None:
                return None
            result = self._api_deployment_row(row)
            knowledge_bases = self._load_api_deployment_kbs(conn, deployment_id)
        result["knowledge_bases"] = knowledge_bases
        result["selected_kbs"] = [item["kb_id"] for item in knowledge_bases]
        unavailable = [item for item in knowledge_bases if item["status"] != "active"]
        result["health"] = "healthy" if knowledge_bases and not unavailable else "degraded"
        result["unavailable_kbs"] = unavailable
        return result

    def list_api_deployments(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT deployment_id
                FROM api_deployments
                WHERE deleted_at IS NULL
                ORDER BY created_at DESC, deployment_id DESC
                """
            ).fetchall()
        return [
            deployment
            for row in rows
            if (deployment := self.get_api_deployment(row["deployment_id"])) is not None
        ]

    def update_api_deployment(
        self,
        deployment_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        selected_kbs: Optional[list[str]] = None,
    ) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM api_deployments WHERE deployment_id = ? AND deleted_at IS NULL",
                (deployment_id,),
            ).fetchone()
            if row is None:
                return None
            assignments: list[str] = []
            values: list[Any] = []
            if name is not None:
                assignments.append("name = ?")
                values.append(name)
            if description is not None:
                assignments.append("description = ?")
                values.append(description)
            if assignments:
                assignments.append("updated_at = CURRENT_TIMESTAMP")
                values.append(deployment_id)
                conn.execute(
                    f"UPDATE api_deployments SET {', '.join(assignments)} WHERE deployment_id = ?",
                    values,
                )
            if selected_kbs is not None:
                conn.execute(
                    "DELETE FROM api_deployment_knowledge_bases WHERE deployment_id = ?",
                    (deployment_id,),
                )
                conn.executemany(
                    """
                    INSERT INTO api_deployment_knowledge_bases (
                        deployment_id, kb_id, position
                    ) VALUES (?, ?, ?)
                    """,
                    [(deployment_id, kb_id, index) for index, kb_id in enumerate(selected_kbs)],
                )
                conn.execute(
                    "UPDATE api_deployments SET updated_at = CURRENT_TIMESTAMP WHERE deployment_id = ?",
                    (deployment_id,),
                )
        return self.get_api_deployment(deployment_id)

    def set_api_deployment_status(self, deployment_id: str, status: str) -> Optional[dict]:
        if status not in {"active", "disabled"}:
            raise ValueError(f"不支持的 Deployment 状态: {status}")
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE api_deployments
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE deployment_id = ? AND deleted_at IS NULL
                """,
                (status, deployment_id),
            )
        return self.get_api_deployment(deployment_id) if cursor.rowcount else None

    def rotate_api_deployment_key(
        self,
        deployment_id: str,
        *,
        api_key_hash: str,
        api_key_prefix: str,
    ) -> Optional[dict]:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE api_deployments
                SET api_key_hash = ?, api_key_prefix = ?, updated_at = CURRENT_TIMESTAMP
                WHERE deployment_id = ? AND deleted_at IS NULL
                """,
                (api_key_hash, api_key_prefix, deployment_id),
            )
        return self.get_api_deployment(deployment_id) if cursor.rowcount else None

    def soft_delete_api_deployment(self, deployment_id: str) -> bool:
        """Disable and hide a deployment while retaining conversation/session history."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE api_deployments
                SET status = 'disabled', deleted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE deployment_id = ? AND deleted_at IS NULL
                """,
                (deployment_id,),
            )
        return cursor.rowcount > 0

    def touch_api_deployment(self, deployment_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE api_deployments
                SET last_used_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE deployment_id = ? AND deleted_at IS NULL
                """,
                (deployment_id,),
            )

    # ── API Deployment conversations ───────────────────────

    def create_api_conversation(
        self,
        *,
        deployment_id: str,
        conversation_id: str,
        internal_session_id: str,
    ) -> dict:
        """Create the existing Session and namespaced public mapping atomically."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, title) VALUES (?, '')",
                (internal_session_id,),
            )
            conn.execute(
                "INSERT INTO session_state (session_id, entity_slots, last_report) VALUES (?, '{}', '')",
                (internal_session_id,),
            )
            conn.execute(
                """
                INSERT INTO api_conversations (
                    deployment_id, conversation_id, internal_session_id
                ) VALUES (?, ?, ?)
                """,
                (deployment_id, conversation_id, internal_session_id),
            )
        return self.get_api_conversation(deployment_id, conversation_id)

    def get_api_conversation(self, deployment_id: str, conversation_id: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT deployment_id, conversation_id, internal_session_id,
                       created_at, updated_at
                FROM api_conversations
                WHERE deployment_id = ? AND conversation_id = ?
                """,
                (deployment_id, conversation_id),
            ).fetchone()
        return dict(row) if row else None

    def touch_api_conversation(self, deployment_id: str, conversation_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE api_conversations
                SET updated_at = CURRENT_TIMESTAMP
                WHERE deployment_id = ? AND conversation_id = ?
                """,
                (deployment_id, conversation_id),
            )

    # ── 知识库切片 ──────────────────────────────────────────

    def replace_knowledge_base_chunks(self, kb_id: str, chunks: list[dict]) -> int:
        """在单个事务内完整替换某知识库的切片原文。"""
        rows = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_index = int(chunk.get("chunk_index") or index)
            fallback_filename = str(
                chunk.get("markdown_filename")
                or chunk.get("source_file")
                or f"chunk-{chunk_index}.md"
            )
            chunk_id = str(chunk.get("chunk_id") or generate_chunk_id(
                kb_id,
                fallback_filename,
                str(chunk.get("combined_text") or ""),
            ))
            rows.append((
                chunk_id,
                kb_id,
                chunk_index,
                str(chunk.get("source_file") or ""),
                str(chunk.get("sheet_name") or ""),
                chunk.get("source_row"),
                str(chunk.get("modality") or ""),
                str(chunk.get("level_1") or ""),
                str(chunk.get("level_2") or ""),
                str(chunk.get("level_3") or ""),
                str(chunk.get("level_4") or ""),
                str(chunk.get("module_name") or ""),
                str(chunk.get("imaging_findings") or ""),
                str(chunk.get("imaging_opinion") or ""),
                str(chunk.get("combined_text") or ""),
            ))

        with self._get_conn() as conn:
            # sqlite3 连接上下文会在异常时自动回滚，避免只写入部分切片。
            conn.execute("DELETE FROM knowledge_base_chunks WHERE kb_id = ?", (kb_id,))
            if rows:
                conn.executemany(
                    """
                    INSERT INTO knowledge_base_chunks (
                        chunk_id, kb_id, chunk_index, source_file, sheet_name,
                        source_row, modality, level_1, level_2, level_3, level_4,
                        module_name, imaging_findings, imaging_opinion, combined_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
        return len(rows)

    def list_knowledge_base_chunks(self, kb_id: str, page: int = 1,
                                   size: int = 50) -> dict:
        """分页读取知识库切片，按生成顺序稳定返回。"""
        page = max(1, int(page))
        size = min(100, max(1, int(size)))
        offset = (page - 1) * size
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM knowledge_base_chunks WHERE kb_id = ?",
                (kb_id,),
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT chunk_id, kb_id, chunk_index, source_file, sheet_name,
                       source_row, modality, level_1, level_2, level_3, level_4,
                       module_name, imaging_findings, imaging_opinion, combined_text,
                       created_at
                FROM knowledge_base_chunks
                WHERE kb_id = ?
                ORDER BY chunk_index ASC
                LIMIT ? OFFSET ?
                """,
                (kb_id, size, offset),
            ).fetchall()
        return {"total": int(total), "items": [dict(row) for row in rows]}

    def list_active_chunk_options(
        self,
        query: str = "",
        limit: int = 20,
        preview_length: int = 150,
        kb_name: str = "",
        exam_type: str = "",
        level1: str = "",
        level2: str = "",
        level3: str = "",
        level4: str = "",
    ) -> list[dict]:
        """Search compact chunk options from active knowledge bases for Eval UI."""
        preview_length = max(20, min(200, int(preview_length)))
        limit = max(1, min(100, int(limit)))
        search_query = str(query or "").strip()
        where_sql = "WHERE kb.status = 'active'"
        params: list[Any] = []
        if search_query:
            where_sql += " AND c.combined_text LIKE ?"
            params.append(f"%{search_query}%")
        exact_filters = (
            ("kb.kb_name", kb_name),
            ("c.modality", exam_type),
            ("c.level_1", level1),
            ("c.level_2", level2),
            ("c.level_3", level3),
            ("c.level_4", level4),
        )
        for column, value in exact_filters:
            normalized_value = str(value or "").strip()
            if normalized_value:
                where_sql += f" AND TRIM({column}) = ?"
                params.append(normalized_value)
        params.append(limit)
        with self._get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT c.chunk_id, c.kb_id, kb.kb_name, c.combined_text
                FROM knowledge_base_chunks AS c
                INNER JOIN knowledge_bases AS kb ON kb.kb_id = c.kb_id
                {where_sql}
                ORDER BY kb.created_at DESC, c.chunk_index ASC
                LIMIT ?
                """,
                params,
            ).fetchall()

        options = []
        for row in rows:
            compact_text = " ".join(str(row["combined_text"] or "").split())
            preview = compact_text[:preview_length]
            if len(compact_text) > preview_length:
                preview += "..."
            options.append({
                "chunk_id": str(row["chunk_id"]),
                "content_preview": preview or "（空切片）",
                "kb_id": str(row["kb_id"]),
                "kb_name": str(row["kb_name"]),
            })
        return options

    def list_active_chunk_filter_options(
        self,
        kb_name: str = "",
        exam_type: str = "",
        level1: str = "",
        level2: str = "",
        level3: str = "",
    ) -> dict[str, list[str]]:
        """Return prefix-filtered DISTINCT values for the Eval cascading filters."""
        selected = {
            "kb_name": str(kb_name or "").strip(),
            "exam_type": str(exam_type or "").strip(),
            "level1": str(level1 or "").strip(),
            "level2": str(level2 or "").strip(),
            "level3": str(level3 or "").strip(),
        }
        option_specs = (
            ("kb_names", "kb.kb_name", ()),
            ("exam_types", "c.modality", (("kb.kb_name", "kb_name"),)),
            ("level1", "c.level_1", (
                ("kb.kb_name", "kb_name"),
                ("c.modality", "exam_type"),
            )),
            ("level2", "c.level_2", (
                ("kb.kb_name", "kb_name"),
                ("c.modality", "exam_type"),
                ("c.level_1", "level1"),
            )),
            ("level3", "c.level_3", (
                ("kb.kb_name", "kb_name"),
                ("c.modality", "exam_type"),
                ("c.level_1", "level1"),
                ("c.level_2", "level2"),
            )),
            ("level4", "c.level_4", (
                ("kb.kb_name", "kb_name"),
                ("c.modality", "exam_type"),
                ("c.level_1", "level1"),
                ("c.level_2", "level2"),
                ("c.level_3", "level3"),
            )),
        )

        result: dict[str, list[str]] = {}
        with self._get_conn() as conn:
            for result_key, value_column, prefix_filters in option_specs:
                where_parts = [
                    "kb.status = 'active'",
                    f"NULLIF(TRIM({value_column}), '') IS NOT NULL",
                ]
                params: list[str] = []
                for filter_column, selected_key in prefix_filters:
                    selected_value = selected[selected_key]
                    if selected_value:
                        where_parts.append(f"TRIM({filter_column}) = ?")
                        params.append(selected_value)
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT TRIM({value_column}) AS value
                    FROM knowledge_base_chunks AS c
                    INNER JOIN knowledge_bases AS kb ON kb.kb_id = c.kb_id
                    WHERE {' AND '.join(where_parts)}
                    ORDER BY value COLLATE NOCASE
                    """,
                    params,
                ).fetchall()
                result[result_key] = [str(row["value"]) for row in rows]
        return result

    # ── 工具方法 ────────────────────────────────────────────

    @staticmethod
    def generate_session_id() -> str:
        """生成新的会话 ID"""
        return f"rag_v2_{uuid.uuid4().hex[:8]}"