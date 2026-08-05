#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a hierarchical metadata tree from generated Markdown reports."""

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PIPELINE_DIR = Path(__file__).resolve().parent
DATA_DIR = PIPELINE_DIR / "output_markdown"
DEFAULT_OUTPUT = PIPELINE_DIR / "metadata.json"
LEVEL_FIELDS = (
    ("level_1", "一级目录"),
    ("level_2", "二级目录"),
    ("level_3", "三级目录"),
    ("level_4", "四级目录"),
)

def extract_frontmatter(md_file: Path) -> Dict[str, str]:
    """Read YAML Front Matter without recording the Markdown file path."""
    try:
        content = md_file.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return {}

        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}

        loaded = yaml.safe_load(parts[1]) or {}
        if not isinstance(loaded, dict):
            return {}
        return {
            str(key): str(value).strip()
            for key, value in loaded.items()
            if value is not None and str(value).strip()
        }
    except Exception as exc:
        logger.error("解析失败 %s: %s", md_file.name, exc)
        return {}


def _insert_metadata(tree: Dict[str, Any], metadata: Dict[str, str]) -> None:
    modality = metadata.get("modality", "").strip()
    if not modality:
        return

    node = tree["检查类型"].setdefault(modality, {})
    for field, label in LEVEL_FIELDS:
        value = metadata.get(field, "").strip()
        if value:
            node = node.setdefault(label, {}).setdefault(value, {})

def _sort_tree(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sort_tree(child)
            for key, child in sorted(value.items(), key=lambda item: item[0])
        }
    if isinstance(value, list):
        return sorted(value)
    return value


def extract_all(data_dir: Path = None, kb_id: Optional[str] = None) -> Dict[str, Any]:
    """Scan all Markdown files and return a deduplicated hierarchy tree."""
    data_dir = Path(data_dir or DATA_DIR)
    if not data_dir.is_dir():
        logger.warning("数据目录不存在: %s", data_dir)
        return {}

    md_files = sorted(data_dir.rglob("*.md"))
    logger.info("找到 %d 个 Markdown 文件", len(md_files))

    tree: Dict[str, Any] = {"检查类型": {}}
    parsed_count = 0
    for md_file in md_files:
        metadata = extract_frontmatter(md_file)
        if metadata and (not kb_id or metadata.get("kb_id") == kb_id):
            _insert_metadata(tree, metadata)
            parsed_count += 1

    logger.info("成功解析 %d 个 Markdown 文件", parsed_count)
    return _sort_tree(tree)


def _collect_values(value: Any, label: str) -> set:
    values = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == label:
                if isinstance(child, dict):
                    values.update(child.keys())
                elif isinstance(child, list):
                    values.update(child)
            values.update(_collect_values(child, label))
    elif isinstance(value, list):
        for child in value:
            values.update(_collect_values(child, label))
    return values


def print_stats(metadata_tree: Dict[str, Any]) -> None:
    """Print unique counts for every hierarchy level."""
    if not metadata_tree:
        logger.info("没有数据")
        return

    modalities = metadata_tree.get("检查类型", {})
    logger.info("=" * 60)
    logger.info("层级元数据统计")
    logger.info("=" * 60)
    logger.info("检查类型: %d 个", len(modalities))
    for _, label in LEVEL_FIELDS:
        logger.info("%s: %d 个", label, len(_collect_values(metadata_tree, label)))


def print_samples(metadata_tree: Dict[str, Any], n: int = 3) -> None:
    """Print the first N modality subtrees."""
    modalities = metadata_tree.get("检查类型", {})
    samples = dict(list(modalities.items())[:n])
    if samples:
        logger.info(
            "层级样本（前 %d 个检查类型）:\n%s",
            len(samples),
            json.dumps({"检查类型": samples}, ensure_ascii=False, indent=2),
        )


def save_json(
    metadata_tree: Dict[str, Any],
    output_path: Path = None,
    kb_id: Optional[str] = None,
) -> None:
    """Save the hierarchy as JSON."""
    output_path = Path(
        output_path
        or (PIPELINE_DIR / f"metadata_{kb_id}.json" if kb_id else DEFAULT_OUTPUT)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(metadata_tree, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("已保存到: %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成报告模板层级元数据")
    parser.add_argument("--stats", action="store_true", help="显示层级统计")
    parser.add_argument("--sample", type=int, default=0, help="显示检查类型样本数")
    parser.add_argument("--no-save", action="store_true", help="不保存 JSON")
    args = parser.parse_args()

    metadata_tree = extract_all()
    if args.stats:
        print_stats(metadata_tree)
    if args.sample > 0:
        print_samples(metadata_tree, args.sample)
    if not args.no_save and metadata_tree:
        save_json(metadata_tree)


if __name__ == "__main__":
    main()
