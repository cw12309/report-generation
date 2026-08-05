"""Remove orphaned knowledge-base files not present in SQLite.

Run from the project root:

    python app/scripts/cleanup_disk.py

Use ``--dry-run`` to print candidates without deleting anything.
"""

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "sessions.db"
DATA_PIPELINE = PROJECT_ROOT / "app" / "data_pipeline"
TARGET_DIRS = (
    DATA_PIPELINE / "report_template",
    DATA_PIPELINE / "output_markdown",
)


def load_valid_kb_ids(db_path: Path) -> set[str]:
    """Read the authoritative KB ID whitelist from SQLite."""
    if not db_path.is_file():
        raise FileNotFoundError(f"SQLite 数据库不存在: {db_path}")
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT kb_id FROM knowledge_bases").fetchall()
    return {str(row[0]) for row in rows if row and row[0]}


def remove_path(path: Path, dry_run: bool) -> None:
    if dry_run:
        print(f"[预览] 将清理僵尸路径: {path}")
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            return
    print(f"成功清理僵尸文件夹/文件: {path.name}")


def cleanup(dry_run: bool = False, db_path: Path = DEFAULT_DB) -> int:
    valid_ids = load_valid_kb_ids(db_path)
    print(f"SQLite 合法知识库数量: {len(valid_ids)}")
    removed = 0

    for target_dir in TARGET_DIRS:
        if not target_dir.exists():
            print(f"目录不存在，跳过: {target_dir}")
            continue
        for child in target_dir.iterdir():
            # 合法 KB 目录/文件名必须与 SQLite 中的 kb_id 完全一致。
            if child.name in valid_ids:
                continue
            remove_path(child, dry_run)
            removed += 1

    print(f"清理完成，共处理 {removed} 个僵尸路径。")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="清理未被 SQLite 记录的知识库物理文件")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 数据库路径")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不执行删除")
    args = parser.parse_args()
    try:
        cleanup(dry_run=args.dry_run, db_path=args.db.resolve())
    except Exception as exc:
        print(f"清理失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
