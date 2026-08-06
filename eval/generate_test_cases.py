"""Use the configured LLM to generate Retrieval Eval cases from SQLite chunks.

Examples:
    python eval/generate_test_cases.py
    python eval/generate_test_cases.py --count 20 --min-length 150
"""

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_DIR.parent
APP_DIR = PROJECT_ROOT / "app"
for import_root in (PROJECT_ROOT, APP_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from chat.llm_client import chat_with_tools  # noqa: E402
from eval.run_retrieval_eval import DATASET_LOCK  # noqa: E402

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "sessions.db"
DEFAULT_DATASET_PATH = EVAL_DIR / "datasets" / "retrieval_test_cases.json"
DEFAULT_COUNT = 50
DEFAULT_MIN_LENGTH = 100
MD5_PATTERN = re.compile(r"^[0-9a-f]{32}$")
SYSTEM_PROMPT = (
    "你是一个专业的医学用户。请根据以下这段文本，提出一个简短且符合真实搜索习惯的"
    "查询词（Query）。要求：不要超过一句话，不要带回答内容，只输出 Query 文本本身。"
)


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """Read the current JSON dataset without mutating it."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"测试集必须是 JSON 数组: {path}")
    return data


def collect_expected_chunk_ids(cases: list[dict[str, Any]]) -> set[str]:
    """Collect valid chunk IDs already covered by the evaluation dataset."""
    chunk_ids: set[str] = set()
    for case in cases:
        values = case.get("expected_chunk_ids", []) if isinstance(case, dict) else []
        if isinstance(values, str):
            values = values.replace("，", ",").split(",")
        if not isinstance(values, list):
            continue
        for value in values:
            chunk_id = str(value).strip().lower()
            if MD5_PATTERN.fullmatch(chunk_id):
                chunk_ids.add(chunk_id)
    return chunk_ids


def sample_chunks(
    db_path: Path,
    count: int,
    min_length: int,
    excluded_chunk_ids: set[str],
) -> list[dict[str, str]]:
    """Randomly sample meaningful chunks from active knowledge bases."""
    if not db_path.is_file():
        raise FileNotFoundError(f"SQLite 数据库不存在: {db_path}")

    # Keep the SQL variable count below SQLite's common 999-parameter limit.
    sql_excluded_ids = sorted(excluded_chunk_ids) if len(excluded_chunk_ids) <= 900 else []
    exclusion_sql = ""
    params: list[Any] = [min_length]
    if sql_excluded_ids:
        placeholders = ",".join("?" for _ in sql_excluded_ids)
        exclusion_sql = f" AND LOWER(c.chunk_id) NOT IN ({placeholders})"
        params.extend(sql_excluded_ids)

    # Pull extra candidates so invalid IDs or a growing existing dataset do not
    # prevent the script from reaching the requested successful generation count.
    candidate_limit = max(count * 10, 500)
    params.append(candidate_limit)
    query = f"""
        SELECT c.chunk_id, c.kb_id, c.combined_text
        FROM knowledge_base_chunks AS c
        INNER JOIN knowledge_bases AS kb ON kb.kb_id = c.kb_id
        WHERE kb.status = 'active'
          AND c.combined_text IS NOT NULL
          AND LENGTH(TRIM(c.combined_text)) > ?
          AND LENGTH(c.chunk_id) = 32
          {exclusion_sql}
        ORDER BY RANDOM()
        LIMIT ?
    """

    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(query, params).fetchall()
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "读取 knowledge_base_chunks 失败，请先完成知识库构建和 SQLite 切片入库"
        ) from exc
    finally:
        connection.close()

    candidates = []
    for row in rows:
        chunk_id = str(row["chunk_id"] or "").strip().lower()
        if not MD5_PATTERN.fullmatch(chunk_id) or chunk_id in excluded_chunk_ids:
            continue
        candidates.append({
            "chunk_id": chunk_id,
            "kb_id": str(row["kb_id"] or ""),
            "text": str(row["combined_text"] or "").strip(),
        })
    return candidates


def normalize_query(raw_query: str) -> str:
    """Remove common model wrappers while retaining the generated query text."""
    lines = [line.strip() for line in str(raw_query or "").splitlines() if line.strip()]
    if not lines:
        return ""
    query = re.sub(r"^(?:query|查询|问题)\s*[:：]\s*", "", lines[0], flags=re.IGNORECASE)
    query = re.sub(r"^[-*•]\s*", "", query)
    return query.strip(" \t\r\n\"'“”‘’")[:200]


async def generate_query(text: str, retries: int = 3) -> str:
    """Generate one search-style query with bounded retries."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"医学知识库切片：\n{text}"},
    ]
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            content, _ = await chat_with_tools(
                messages,
                tools=None,
                max_tokens=80,
                temperature=0.25,
                debug=False,
            )
            query = normalize_query(content or "")
            if query:
                return query
            raise ValueError("LLM 返回了空 Query")
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                await asyncio.sleep(attempt * 1.5)
    raise RuntimeError(f"连续 {retries} 次生成失败: {last_error}") from last_error


def append_cases_atomically(
    dataset_path: Path,
    generated_cases: list[dict[str, Any]],
) -> tuple[int, int]:
    """Append non-duplicate cases through an atomic file replacement."""
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    with DATASET_LOCK:
        latest_cases = load_dataset(dataset_path)
        latest_ids = collect_expected_chunk_ids(latest_cases)
        cases_to_add = [
            case for case in generated_cases
            if case["expected_chunk_ids"][0] not in latest_ids
        ]
        if not cases_to_add:
            return 0, len(latest_cases)

        updated_cases = [*latest_cases, *cases_to_add]
        temp_path = dataset_path.with_name(
            f".{dataset_path.name}.{os.getpid()}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(updated_cases, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, dataset_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()
    return len(cases_to_add), len(updated_cases)


async def stream_generate_cases(
    count: int = DEFAULT_COUNT,
    min_length: int = DEFAULT_MIN_LENGTH,
    db_path: Path = DEFAULT_DB_PATH,
    dataset_path: Path = DEFAULT_DATASET_PATH,
) -> AsyncIterator[dict[str, Any]]:
    """Yield JSON-safe progress events while generating and persisting cases."""
    if count <= 0:
        raise ValueError("count 必须大于 0")
    if count > 100:
        raise ValueError("count 不能超过 100")
    if min_length < 0:
        raise ValueError("min_length 不能小于 0")

    db_path = Path(db_path)
    dataset_path = Path(dataset_path)
    existing_cases = load_dataset(dataset_path)
    existing_ids = collect_expected_chunk_ids(existing_cases)
    candidates = sample_chunks(db_path, count, min_length, existing_ids)
    if not candidates:
        raise RuntimeError("没有找到符合条件且尚未进入测试集的 active 知识库切片")

    generated_queries = {
        str(case.get("query") or "").strip().casefold()
        for case in existing_cases
        if isinstance(case, dict) and case.get("query")
    }
    current = 0
    failed_count = 0
    duplicate_count = 0
    dataset_total = len(existing_cases)

    yield {
        "type": "start",
        "current": 0,
        "total": count,
        "progress": 0,
        "candidate_count": len(candidates),
        "dataset_total": dataset_total,
        "message": f"已抽取 {len(candidates)} 条候选切片，开始调用 LLM",
    }

    for candidate_index, candidate in enumerate(candidates, start=1):
        if current >= count:
            break
        try:
            query = await generate_query(candidate["text"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed_count += 1
            yield {
                "type": "warning",
                "current": current,
                "total": count,
                "progress": int(current / count * 100),
                "message": f"切片 {candidate['chunk_id']} 生成失败，已跳过: {exc}",
            }
            continue

        query_key = query.casefold()
        if query_key in generated_queries:
            duplicate_count += 1
            yield {
                "type": "warning",
                "current": current,
                "total": count,
                "progress": int(current / count * 100),
                "message": f"生成了重复 Query，已跳过: {query}",
            }
            continue

        case = {
            "query": query,
            "expected_chunk_ids": [candidate["chunk_id"]],
            "metadata": {
                "type": "auto_generated",
                "kb_id": candidate["kb_id"],
            },
        }
        added_count, dataset_total = append_cases_atomically(dataset_path, [case])
        if not added_count:
            duplicate_count += 1
            continue

        generated_queries.add(query_key)
        current += 1
        yield {
            "type": "progress",
            "current": current,
            "total": count,
            "progress": int(current / count * 100),
            "query": query,
            "chunk_id": candidate["chunk_id"],
            "kb_id": candidate["kb_id"],
            "candidate_index": candidate_index,
            "dataset_total": dataset_total,
            "message": f"已生成: {query}",
        }

    yield {
        "type": "done",
        "current": current,
        "total": count,
        "progress": 100,
        "generated": current,
        "failed": failed_count,
        "duplicates": duplicate_count,
        "dataset_total": dataset_total,
        "complete": current >= count,
        "message": (
            f"生成完毕，共新增 {current} 条测试用例"
            if current >= count
            else f"候选切片已用尽，本次新增 {current}/{count} 条测试用例"
        ),
    }


async def generate_test_cases(
    db_path: Path,
    dataset_path: Path,
    count: int = DEFAULT_COUNT,
    min_length: int = DEFAULT_MIN_LENGTH,
) -> dict[str, int]:
    print("=" * 72)
    print("Retrieval Eval 测试用例自动生成")
    print(f"SQLite: {db_path}")
    print(f"数据集: {dataset_path}")
    print(f"目标数量: {count}，最小长度: > {min_length}")
    print("=" * 72)
    result: dict[str, int] | None = None
    async for event in stream_generate_cases(
        count=count,
        min_length=min_length,
        db_path=db_path,
        dataset_path=dataset_path,
    ):
        event_type = event["type"]
        if event_type == "start":
            print(event["message"])
        elif event_type == "progress":
            print(
                f"\n[{event['current']:02d}/{event['total']:02d}] "
                f"KB: {event['kb_id']}"
            )
            print(f"  Chunk ID: {event['chunk_id']}")
            print(f"  Query: {event['query']}")
        elif event_type == "warning":
            print(f"  [跳过] {event['message']}")
        elif event_type == "done":
            result = {
                "requested": event["total"],
                "generated": event["generated"],
                "added": event["generated"],
                "failed": event["failed"],
                "total": event["dataset_total"],
            }

    if result is None:
        raise RuntimeError("自动生成流程未返回完成事件")
    print("\n" + "=" * 72)
    print(f"生成完成: 成功新增 {result['added']} 条，LLM 失败 {result['failed']} 条")
    print(f"测试集当前总数: {result['total']} 条")
    if result["added"] < count:
        print(f"提示: 本次少于目标 {count} 条，可再次运行脚本补充剩余用例。")
    print("=" * 72)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 SQLite 医学切片逆向生成 Retrieval Eval 测试用例",
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="目标生成数量，默认 50")
    parser.add_argument(
        "--min-length",
        type=int,
        default=DEFAULT_MIN_LENGTH,
        help="切片最小字符数（使用严格大于），默认 100",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite 数据库路径")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="目标 Retrieval Eval JSON 文件",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(
            generate_test_cases(
                db_path=args.db.resolve(),
                dataset_path=args.dataset.resolve(),
                count=args.count,
                min_length=args.min_length,
            )
        )
    except KeyboardInterrupt:
        print("\n已由用户中止；此前显示成功的用例均已安全写入。")
        return 130
    except Exception as exc:
        print(f"\n生成失败: {exc}", file=sys.stderr)
        return 1
    return 0 if result["added"] >= result["requested"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
