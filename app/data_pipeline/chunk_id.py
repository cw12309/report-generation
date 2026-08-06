"""Stable identifiers shared by the SQLite, Markdown, and Milvus pipelines."""

import hashlib


def generate_chunk_id(kb_id: str, filename: str, text: str) -> str:
    """Return a deterministic 32-character MD5 identifier for one KB chunk."""
    payload = f"{kb_id}\n{filename}\n{text}".encode("utf-8")
    return hashlib.md5(payload).hexdigest()
