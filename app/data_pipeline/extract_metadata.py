#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
元数据提取 - extract_metadata.py
================================

功能：从 Markdown 文件中提取元数据信息，并导出为 JSON 格式。

使用场景：
    - 统计报告模板数量
    - 查看元数据分布
    - 调试数据解析问题
    - 生成数据索引

使用方式：
    python extract_metadata.py                      # 提取所有元数据
    python extract_metadata.py --stats               # 仅显示统计信息
    python extract_metadata.py --sample 5            # 显示前 5 个样本
"""

import json
import logging
import argparse
from pathlib import Path
from collections import Counter
from typing import List, Dict

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# 数据目录（相对于 data_pipeline）
PIPELINE_DIR = Path(__file__).resolve().parent
DATA_DIR = PIPELINE_DIR / "output_markdown"


def extract_frontmatter(md_file: Path) -> Dict:
    """提取单个 Markdown 文件的 YAML frontmatter"""
    try:
        content = md_file.read_text(encoding='utf-8')
        
        # 匹配 frontmatter
        if not content.startswith("---"):
            return {}
        
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        
        frontmatter_text = parts[1]
        metadata = {}
        
        for line in frontmatter_text.strip().split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                metadata[key.strip()] = value.strip().strip('"').strip("'")
        
        # 添加文件路径
        metadata['file_path'] = str(md_file.relative_to(PIPELINE_DIR))
        
        return metadata
    except Exception as e:
        logger.error(f"解析失败 {md_file.name}: {e}")
        return {}


def extract_all(data_dir: Path = None) -> List[Dict]:
    """提取所有 Markdown 文件的元数据"""
    data_dir = data_dir or DATA_DIR
    
    if not data_dir.exists():
        logger.warning(f"数据目录不存在: {data_dir}")
        return []
    
    md_files = list(data_dir.rglob("*.md"))
    logger.info(f"找到 {len(md_files)} 个 Markdown 文件")
    
    metadata_list = []
    for md_file in md_files:
        metadata = extract_frontmatter(md_file)
        if metadata:
            metadata_list.append(metadata)
    
    logger.info(f"✅ 成功提取 {len(metadata_list)} 个文件的元数据")
    return metadata_list


def print_stats(metadata_list: List[Dict]) -> None:
    """打印统计信息"""
    if not metadata_list:
        logger.info("没有数据")
        return
    
    # 统计各字段分布
    modality_count = Counter(m.get('modality', '') for m in metadata_list)
    level_1_count = Counter(m.get('level_1', '') for m in metadata_list)
    
    logger.info("=" * 60)
    logger.info("📊 数据统计")
    logger.info("=" * 60)
    logger.info(f"总文件数: {len(metadata_list)}")
    
    logger.info("\n检查类型分布:")
    for modality, count in modality_count.most_common():
        logger.info(f"  {modality}: {count}")
    
    logger.info("\n部位分布（前10）:")
    for level, count in level_1_count.most_common(10):
        logger.info(f"  {level}: {count}")


def print_samples(metadata_list: List[Dict], n: int = 3) -> None:
    """打印样本数据"""
    if not metadata_list:
        return
    
    logger.info("=" * 60)
    logger.info(f"📋 样本数据（前 {min(n, len(metadata_list))} 个）")
    logger.info("=" * 60)
    
    for i, meta in enumerate(metadata_list[:n], 1):
        logger.info(f"\n样本 {i}:")
        logger.info(json.dumps(meta, ensure_ascii=False, indent=2))


def save_json(metadata_list: List[Dict], output_path: Path = None) -> None:
    """保存为 JSON 文件"""
    output_path = output_path or PIPELINE_DIR / "metadata.json"
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(metadata_list, f, ensure_ascii=False, indent=2)
    
    logger.info(f"\n✅ 已保存到: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="元数据提取工具")
    parser.add_argument("--stats", action="store_true", help="仅显示统计信息")
    parser.add_argument("--sample", type=int, default=0, help="显示样本数量")
    parser.add_argument("--no-save", action="store_true", help="不保存 JSON 文件")
    args = parser.parse_args()
    
    # 提取元数据
    metadata_list = extract_all()
    
    if args.stats:
        print_stats(metadata_list)
    
    if args.sample > 0:
        print_samples(metadata_list, args.sample)
    
    if not args.no_save and metadata_list:
        save_json(metadata_list)
