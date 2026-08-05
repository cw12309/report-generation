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
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)
KNOWLEDGE_BASE_STATUSES = {"uploaded", "processing", "active", "failed", "disabled"}


class SessionStore:
    """SQLite 会话持久化存储"""

    def __init__(self, db_path: str = "data/sessions.db"):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    # ── 数据库连接管理 ──────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

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
                    metadata_tree TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_knowledge_bases_status
                    ON knowledge_bases(status, created_at);

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
                              source_path: str = "") -> dict:
        if status not in KNOWLEDGE_BASE_STATUSES:
            raise ValueError(f"不支持的知识库状态: {status}")
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO knowledge_bases (kb_id, kb_name, status, source_path, metadata_tree) VALUES (?, ?, ?, ?, ?)",
                (kb_id, kb_name, status, source_path, json.dumps(metadata_tree or {}, ensure_ascii=False)),
            )
        return self.get_knowledge_base(kb_id)

    def update_knowledge_base(self, kb_id: str, *, status: Optional[str] = None,
                              metadata_tree: Optional[dict] = None,
                              progress: Optional[int] = None) -> None:
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
                "SELECT kb_id, kb_name, created_at, status, progress, source_path, metadata_tree "
                "FROM knowledge_bases WHERE kb_id = ?", (kb_id,),
            ).fetchone()
        return self._knowledge_base_row(row) if row else None

    def list_knowledge_bases(self, status: Optional[str] = None) -> list[dict]:
        with self._get_conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT kb_id, kb_name, created_at, status, progress, source_path, metadata_tree "
                    "FROM knowledge_bases WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT kb_id, kb_name, created_at, status, progress, source_path, metadata_tree "
                    "FROM knowledge_bases ORDER BY created_at DESC"
                ).fetchall()
        return [self._knowledge_base_row(row) for row in rows]

    def delete_knowledge_base(self, kb_id: str) -> bool:
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM knowledge_bases WHERE kb_id = ?", (kb_id,))
        return cursor.rowcount > 0

    # ── 知识库切片 ──────────────────────────────────────────

    def replace_knowledge_base_chunks(self, kb_id: str, chunks: list[dict]) -> int:
        """在单个事务内完整替换某知识库的切片原文。"""
        rows = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_index = int(chunk.get("chunk_index") or index)
            rows.append((
                str(chunk.get("chunk_id") or f"{kb_id}:{chunk_index:08d}"),
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

    # ── 工具方法 ────────────────────────────────────────────

    @staticmethod
    def generate_session_id() -> str:
        """生成新的会话 ID"""
        return f"rag_v2_{uuid.uuid4().hex[:8]}"
