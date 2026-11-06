"""Template injection counters. No report text or patient input is stored here."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "template_usage.sqlite3"


@contextmanager
def _connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=2.0)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS template_usage (
            attempt_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            template_id TEXT NOT NULL,
            template_version TEXT NOT NULL,
            successful INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS template_usage_owner ON template_usage(username, template_id)")
        yield db
        db.commit()
    finally:
        db.close()


def record_injection(username: str, reference: dict):
    """A unique generation attempt increments injection count at most once."""
    with _connection() as db:
        db.execute("INSERT OR IGNORE INTO template_usage(attempt_id, username, template_id, template_version) VALUES (?, ?, ?, ?)",
                   (reference["attempt_id"], username, reference["template_id"], reference["template_version"]))


def mark_success(username: str, reference: dict):
    """Only a validated, persisted generation counts as successful; retries are idempotent."""
    if not reference or not reference.get("validation_passed"):
        return
    with _connection() as db:
        db.execute("UPDATE template_usage SET successful=1 WHERE attempt_id=? AND username=? AND template_id=? AND template_version=?",
                   (reference["attempt_id"], username, reference["template_id"], reference["template_version"]))


def counts(username: str) -> dict:
    with _connection() as db:
        rows = db.execute("SELECT template_id, COUNT(*), SUM(successful) FROM template_usage WHERE username=? GROUP BY template_id", (username,)).fetchall()
    return {tid: {"injection_count": injected, "success_count": successful} for tid, injected, successful in rows}


def annotate(templates: list[dict], username: str) -> list[dict]:
    stats = counts(username)
    result = []
    for template in templates:
        legacy = int(template.get("usage_count") or 0)
        current = stats.get(template["id"], {"injection_count": 0, "success_count": 0})
        result.append({**template, **current, "stats_available": True, "legacy_usage_count": legacy,
                       "usage_count": legacy + current["injection_count"]})
    return result
