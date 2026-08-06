#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
医疗报告模板处理脚本
将Excel文件转换为层级文件夹结构，并生成Markdown文件
"""

import argparse
import pandas as pd
import os
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional

try:
    from .chunk_id import generate_chunk_id
except ImportError:  # 兼容直接执行 process_reports.py
    from chunk_id import generate_chunk_id


# 字段映射：原始列名 -> 标准字段名
FIELD_MAPPING = {
    '检查类型': 'modality',
    '一级目录': 'level_1',
    '二级目录': 'level_2',
    '三级目录': 'level_3',
    '四级目录': 'level_4',
    '模块名称': 'module_name'
}

# 标准字段顺序
STANDARD_FIELDS = ['modality', 'level_1', 'level_2', 'level_3', 'level_4', 'module_name']
CONTENT_FIELDS = ['影像学表现', '影像学意见']

# Windows非法字符
ILLEGAL_CHARS = r'[<>:"/\\|?*\x00-\x1f]'


def sanitize_filename(name: str) -> str:
    """清理文件名，移除非法字符"""
    if pd.isna(name) or name == '':
        return ''
    # 移除非法字符（包括Windows保留字符）
    name = re.sub(ILLEGAL_CHARS, '', str(name))
    # 移除首尾空格和点
    name = name.strip().strip('.')
    # 移除控制字符和其他可能导致问题的字符
    name = re.sub(r'[\x00-\x1f]', '', name)
    # 限制文件名长度（Windows限制255字符）
    if len(name) > 200:
        name = name[:200]
    # 如果为空，返回占位符
    return name if name else '_'


def sanitize_folder_name(name: str) -> str:
    """清理文件夹名，移除非法字符"""
    return sanitize_filename(name)


def normalize_row(row: pd.Series) -> Dict:
    """将一行数据标准化为标准字段"""
    result = {field: '' for field in STANDARD_FIELDS + CONTENT_FIELDS}
    
    for col_name, value in row.items():
        if pd.isna(value):
            continue
        value_str = str(value).strip()
        # 跳过nan字符串
        if value_str.lower() == 'nan' or value_str == '':
            continue
        
        # 查找映射
        if col_name in FIELD_MAPPING:
            std_field = FIELD_MAPPING[col_name]
            result[std_field] = value_str
        elif col_name in CONTENT_FIELDS:
            result[col_name] = value_str
        elif col_name in STANDARD_FIELDS:
            result[col_name] = value_str
    
    return result


def create_folder_structure(base_path: Path, data: Dict) -> Path:
    """根据层级结构创建文件夹"""
    levels = [
        data.get('modality', ''),
        data.get('level_1', ''),
        data.get('level_2', ''),
        data.get('level_3', ''),
        data.get('level_4', ''),
    ]
    
    # 构建路径（跳过空层级）
    current_path = base_path
    for level in levels:
        if level and str(level).strip() and str(level).lower() != 'nan':
            folder_name = sanitize_folder_name(str(level))
            if folder_name:
                current_path = current_path / folder_name
    
    # 创建文件夹
    try:
        current_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"创建文件夹失败: {current_path}, 错误: {e}")
        raise
    
    return current_path


def create_markdown_content(
    data: Dict,
    kb_id: str = "",
    chunk_id: str = "",
) -> str:
    """生成Markdown文件内容"""
    # YAML Frontmatter
    frontmatter = "---\n"
    if kb_id:
        frontmatter += f"kb_id: {kb_id}\n"
    if chunk_id:
        frontmatter += f"chunk_id: {chunk_id}\n"
    for field in STANDARD_FIELDS:
        value = data.get(field, '')
        # 转义YAML中的特殊字符
        if ':' in value or '"' in value or "'" in value or '\n' in value:
            value = f'"{value}"'
        frontmatter += f"{field}: {value}\n"
    frontmatter += "---\n\n"
    
    # 正文
    content = frontmatter
    
    # 影像学表现
    imaging_findings = data.get('影像学表现', '')
    if imaging_findings:
        content += "### 影像学表现\n\n"
        content += imaging_findings + "\n\n"
    
    # 影像学意见
    imaging_opinion = data.get('影像学意见', '')
    if imaging_opinion:
        content += "### 影像学意见\n\n"
        content += imaging_opinion + "\n"
    
    return content


def create_combined_text(data: Dict) -> str:
    """生成用于原文展示和向量化的统一切片文本。"""
    findings = data.get("影像学表现", "")
    opinion = data.get("影像学意见", "")
    return "\n".join(filter(None, [
        f"检查类型：{data.get('modality', '')}",
        f"一级目录：{data.get('level_1', '')}",
        f"二级目录：{data.get('level_2', '')}",
        f"三级目录：{data.get('level_3', '')}",
        f"四级目录：{data.get('level_4', '')}",
        f"模块名称：{data.get('module_name', '')}",
        f"影像学表现：{findings}" if findings else "",
        f"影像学意见：{opinion}" if opinion else "",
    ]))


def process_excel_file(file_path: str, base_output_path: Path, filename_counter: Dict,
                       content_cache: Dict, diff_records: List,
                       kb_id: str = "") -> Dict:
    """处理单个Excel文件
    
    Args:
        file_path: Excel文件路径
        base_output_path: 输出目录
        filename_counter: 全局文件名计数器，用于处理命名冲突
        content_cache: 内容缓存，用于判断重复数据
        diff_records: 差异记录列表，用于生成报告
    """
    stats = {
        'file': file_path,
        'total_rows': 0,
        'processed_rows': 0,
        'skipped_duplicates': 0,
        'different_content': 0,
        'errors': [],
        'chunks': [],
    }
    
    try:
        # 读取所有sheet
        xl = pd.ExcelFile(file_path)
        
        for sheet_name in xl.sheet_names:
            try:
                df = pd.read_excel(file_path, sheet_name=sheet_name)
                stats['total_rows'] += len(df)
                
                print(f"\n处理文件: {file_path} - Sheet: {sheet_name}")
                print(f"总行数: {len(df)}")
                
                for idx, row in df.iterrows():
                    try:
                        # 标准化数据
                        normalized_data = normalize_row(row)
                        
                        # 检查是否有有效内容
                        if not any(normalized_data.get(f, '') for f in STANDARD_FIELDS):
                            continue
                        
                        # 创建文件夹结构
                        folder_path = create_folder_structure(base_output_path, normalized_data)
                        
                        # 生成Markdown文件名（处理命名冲突）
                        module_name = normalized_data.get('module_name', '')
                        safe_module_name = sanitize_filename(module_name) if module_name else 'unknown'
                        
                        # 生成唯一key（基于层级和模块名称）
                        folder_key = str(folder_path)
                        name_key = f"{folder_key}|{safe_module_name}"
                        
                        # 获取内容
                        imaging_findings = normalized_data.get('影像学表现', '')
                        imaging_opinion = normalized_data.get('影像学意见', '')
                        content_key = f"{imaging_findings}|{imaging_opinion}"
                        
                        # 构建来源信息
                        source_info = {
                            'file': Path(file_path).name,
                            'sheet': sheet_name,
                            'row': idx + 2,
                            'data': normalized_data.copy()
                        }
                        
                        # 检查是否已存在相同的数据
                        if name_key in content_cache:
                            # 检查内容是否相同
                            existing_contents = content_cache[name_key]
                            is_duplicate = False
                            
                            for existing_content_key in existing_contents:
                                if existing_content_key == content_key:
                                    # 内容完全相同，跳过（去重）
                                    is_duplicate = True
                                    break
                            
                            if is_duplicate:
                                stats['skipped_duplicates'] += 1
                                continue
                            else:
                                # 内容不同，需要生成带编号的文件
                                stats['different_content'] += 1
                                filename_counter[name_key] = filename_counter.get(name_key, 0) + 1
                                md_filename = f"{safe_module_name}_{filename_counter[name_key]}.md"
                                # 添加新内容到缓存
                                existing_contents.append(content_key)
                                
                                # 记录差异
                                diff_records.append({
                                    'module_name': safe_module_name,
                                    'folder_path': str(folder_path.relative_to(base_output_path)),
                                    'sources': [source_info]
                                })
                        else:
                            # 第一次遇到这个key
                            content_cache[name_key] = [content_key]
                            md_filename = f"{safe_module_name}.md"
                            # 初始化差异记录（只有一条，等待后续添加）
                            content_cache[f"{name_key}_first_source"] = source_info
                        
                        md_path = folder_path / md_filename
                        
                        combined_text = create_combined_text(normalized_data)
                        chunk_id = generate_chunk_id(kb_id, md_filename, combined_text)

                        # SQLite、Markdown 和 Milvus 共用同一个稳定 chunk_id。
                        md_content = create_markdown_content(
                            normalized_data,
                            kb_id=kb_id,
                            chunk_id=chunk_id,
                        )
                        
                        with open(md_path, 'w', encoding='utf-8') as f:
                            f.write(md_content)

                        stats['chunks'].append({
                            'chunk_id': chunk_id,
                            'markdown_filename': md_filename,
                            'source_file': Path(file_path).name,
                            'sheet_name': str(sheet_name),
                            'source_row': int(idx + 2),
                            'modality': normalized_data.get('modality', ''),
                            'level_1': normalized_data.get('level_1', ''),
                            'level_2': normalized_data.get('level_2', ''),
                            'level_3': normalized_data.get('level_3', ''),
                            'level_4': normalized_data.get('level_4', ''),
                            'module_name': normalized_data.get('module_name', ''),
                            'imaging_findings': imaging_findings,
                            'imaging_opinion': imaging_opinion,
                            'combined_text': combined_text,
                        })
                        
                        stats['processed_rows'] += 1
                    
                    except Exception as e:
                        stats['errors'].append(f"行 {idx + 1}: {str(e)}")
                        
            except Exception as e:
                stats['errors'].append(f"Sheet {sheet_name}: {str(e)}")
    
    except Exception as e:
        stats['errors'].append(f"文件读取错误: {str(e)}")
    
    return stats


def process_uploaded_excel(file_path: str, base_output_path: Path, kb_id: str) -> Dict:
    """Process one upload into an isolated KB directory."""
    return process_uploaded_excels([file_path], base_output_path, kb_id)


def process_uploaded_excels(file_paths: List[str], base_output_path: Path, kb_id: str) -> Dict:
    """Process multiple Excel files into one isolated KB directory."""
    if not kb_id or len(kb_id) > 64:
        raise ValueError("kb_id 必须是 1-64 个字符")
    if not file_paths:
        raise ValueError("至少需要一个 Excel 文件")
    base_output_path = Path(base_output_path) / kb_id
    # 每次重新解析均完整替换该知识库的 Markdown，避免遗留旧切片。
    shutil.rmtree(base_output_path, ignore_errors=True)
    base_output_path.mkdir(parents=True, exist_ok=True)
    filename_counter, content_cache, diff_records = {}, {}, []
    all_stats = []
    for file_path in file_paths:
        all_stats.append(process_excel_file(
            file_path, base_output_path, filename_counter, content_cache,
            diff_records, kb_id=kb_id,
        ))
    chunks = [chunk for stats in all_stats for chunk in stats['chunks']]
    for chunk_index, chunk in enumerate(chunks, start=1):
        chunk['chunk_index'] = chunk_index
    return {
        "files": len(all_stats),
        "total_rows": sum(item["total_rows"] for item in all_stats),
        "processed_rows": sum(item["processed_rows"] for item in all_stats),
        "skipped_duplicates": sum(item["skipped_duplicates"] for item in all_stats),
        "different_content": sum(item["different_content"] for item in all_stats),
        "errors": [error for item in all_stats for error in item["errors"]],
        "chunks": chunks,
    }


def check_empty_modality(input_dir: Path, xlsx_files: List[str]):
    """检查Excel文件中检查类型为空的行"""
    print("\n" + "=" * 60)
    print("检查检查类型为空的数据")
    print("=" * 60)

    total_empty = 0
    for f in xlsx_files:
        file_path = input_dir / f
        if file_path.exists():
            xl = pd.ExcelFile(file_path)
            for sheet in xl.sheet_names:
                df = pd.read_excel(file_path, sheet_name=sheet)
                if '检查类型' in df.columns:
                    empty_mask = df['检查类型'].isna() | (df['检查类型'].astype(str).str.strip() == '') | (df['检查类型'].astype(str).str.lower() == 'nan')
                    empty_modality = df[empty_mask]
                    if len(empty_modality) > 0:
                        print(f'\n文件: {f} - Sheet: {sheet}')
                        print(f'modality为空的行数: {len(empty_modality)}')
                        for idx, row in empty_modality.head(3).iterrows():
                            l1 = str(row.get('一级目录', ''))[:15]
                            l2 = str(row.get('二级目录', ''))[:15]
                            mod = str(row.get('模块名称', ''))[:20]
                            print(f'  行{idx+2}: 一级={l1}, 二级={l2}, 模块={mod}')
                        total_empty += len(empty_modality)

    if total_empty == 0:
        print("\n没有检查类型为空的数据")
    else:
        print(f"\n总计: {total_empty} 行检查类型为空")


def generate_final_report(diff_records: List, content_cache: Dict, output_path: Path, all_stats: List, xlsx_files: List):
    """生成最终报告Markdown文件"""
    report_path = output_path.parent / '报告整理结果.md'
    
    # 统计模块名称为空的数量
    empty_module_count = sum(1 for r in diff_records if r['module_name'] == 'unknown')
    
    # 计算统计信息
    total_rows = sum(s['total_rows'] for s in all_stats)
    total_processed = sum(s['processed_rows'] for s in all_stats)
    total_skipped = sum(s['skipped_duplicates'] for s in all_stats)
    total_different = sum(s['different_content'] for s in all_stats)
    total_errors = sum(len(s['errors']) for s in all_stats)
    
    # 生成输出目录分析
    output_analysis = analyze_output(output_path)
    
    # 生成空值检查信息（检查类型、一级目录、模块名称）
    empty_fields_info = ""
    for f in xlsx_files:
        file_path = output_path.parent / 'report_template' / f
        if file_path.exists():
            xl = pd.ExcelFile(file_path)
            for sheet in xl.sheet_names:
                df = pd.read_excel(file_path, sheet_name=sheet)
                empty_rows = []
                
                for idx, row in df.iterrows():
                    modality = str(row.get('检查类型', '')).strip()
                    level1 = str(row.get('一级目录', '')).strip()
                    module = str(row.get('模块名称', '')).strip()
                    
                    has_empty = False
                    modality_empty = not modality or modality.lower() == 'nan'
                    level1_empty = not level1 or level1.lower() == 'nan'
                    module_empty = not module or module.lower() == 'nan'
                    
                    if modality_empty or level1_empty or module_empty:
                        empty_rows.append({
                            'row': idx + 2,
                            'modality': '⚠️ 空' if modality_empty else modality[:20],
                            'level1': '⚠️ 空' if level1_empty else level1[:20],
                            'module': '⚠️ 空' if module_empty else module[:30]
                        })
                
                if empty_rows:
                    empty_fields_info += f"### {f} - {sheet}\n\n"
                    empty_fields_info += f"共 {len(empty_rows)} 行存在空值：\n\n"
                    empty_fields_info += "| 行号 | 检查类型 | 一级目录 | 模块名称 |\n"
                    empty_fields_info += "|------|----------|----------|----------|\n"
                    for item in empty_rows:
                        empty_fields_info += f"| 行{item['row']} | {item['modality']} | {item['level1']} | {item['module']} |\n"
                    empty_fields_info += "\n"
    
    with open(report_path, 'w', encoding='utf-8') as f:
        # 标题
        f.write("# 报告整理结果\n\n")
        f.write(f"生成时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # 一、处理统计
        f.write("## 一、处理统计\n\n")
        f.write(f"- **处理的Excel文件数**: {len(xlsx_files)} 个\n")
        f.write(f"- **总行数**: {total_rows} 行\n")
        f.write(f"- **已处理（生成文件）**: {total_processed} 行\n")
        f.write(f"- **跳过重复（内容相同）**: {total_skipped} 行\n")
        f.write(f"- **内容不同（带编号）**: {total_different} 行\n")
        if total_errors > 0:
            f.write(f"- **错误数**: {total_errors} 个\n")
        f.write("\n")
        
        # 二、输出目录分析
        f.write("## 二、输出目录分析\n\n")
        f.write(output_analysis)
        
        # 三、字段空值检查
        f.write("## 三、字段空值检查\n\n")
        if empty_fields_info:
            f.write("> ⚠️ 以下数据存在字段空值，需要补充\n\n")
            f.write(empty_fields_info)
        else:
            f.write("> ✅ 没有字段空值数据\n\n")
        
        # 四、同名但内容不同的数据
        if diff_records:
            f.write("## 四、同名但内容不同的数据\n\n")
            if empty_module_count > 0:
                f.write(f"> ⚠️ **注意**: 其中 **{empty_module_count}** 组数据的模块名称为空，需要在Excel中补充\n\n")
            f.write(f"共发现 **{len(diff_records)}** 组同名但内容不同的数据\n\n")
            f.write("---\n\n")
            
            for i, record in enumerate(diff_records, 1):
                module_name = record['module_name']
                folder_path = record['folder_path']
                sources = record.get('sources', [])
                
                # 模块名称显示
                if module_name == 'unknown':
                    f.write(f"### {i}. ⚠️ 模块名称为空\n\n")
                else:
                    f.write(f"### {i}. {module_name}\n\n")
                
                f.write(f"**路径**: `{folder_path}`\n\n")
                
                # 获取第一个来源
                folder_key = str(output_path / folder_path)
                name_key = f"{folder_key}|{module_name}"
                first_source_key = f"{name_key}_first_source"
                first_source = content_cache.get(first_source_key, {})
                
                # 收集所有来源
                all_sources = []
                if first_source:
                    all_sources.append(first_source)
                all_sources.extend(sources)
                
                # 创建对比表格（动态列数）
                num_sources = len(all_sources)
                if num_sources > 0:
                    # 表头
                    header = "| 对比项 | " + " | ".join([f"来源 {j+1}" for j in range(num_sources)]) + " |\n"
                    separator = "|--------|" + "|".join(["--------" for _ in range(num_sources)]) + "|\n"
                    f.write(header)
                    f.write(separator)
                    
                    # 文件、Sheet、行号
                    f.write("| **文件** | " + " | ".join([s.get('file', '') for s in all_sources]) + " |\n")
                    f.write("| **Sheet** | " + " | ".join([s.get('sheet', '') for s in all_sources]) + " |\n")
                    f.write("| **行号** | " + " | ".join([str(s.get('row', '')) for s in all_sources]) + " |\n")
                    
                    # 对比各字段
                    for field in STANDARD_FIELDS:
                        values = []
                        for s in all_sources:
                            data = s.get('data', {})
                            v = str(data.get(field, ''))[:40]
                            if field == 'module_name' and not v:
                                v = '⚠️ 空'
                            if len(str(data.get(field, ''))) > 40:
                                v += '...'
                            values.append(v)
                        if any(values):
                            f.write(f"| **{field}** | " + " | ".join(values) + " |\n")
                    
                    # 影像学表现
                    findings = []
                    for s in all_sources:
                        data = s.get('data', {})
                        v = str(data.get('影像学表现', '')).replace('\n', ' ').replace('\r', '')[:60]
                        if len(str(data.get('影像学表现', '')).replace('\n', ' ').replace('\r', '')) > 60:
                            v += '...'
                        findings.append(v)
                    f.write("| **影像学表现** | " + " | ".join(findings) + " |\n")
                    
                    # 影像学意见
                    opinions = []
                    for s in all_sources:
                        data = s.get('data', {})
                        v = str(data.get('影像学意见', '')).replace('\n', ' ').replace('\r', '')[:60]
                        if len(str(data.get('影像学意见', '')).replace('\n', ' ').replace('\r', '')) > 60:
                            v += '...'
                        opinions.append(v)
                    f.write("| **影像学意见** | " + " | ".join(opinions) + " |\n")
                else:
                    f.write("| **提示** | 未找到来源数据 | 请检查数据 |\n")
                
                f.write("\n---\n\n")
    
    print(f"\n已生成报告: {report_path}")


def analyze_output(output_dir: Path) -> str:
    """分析输出目录的内容，返回Markdown格式报告"""
    if not output_dir.exists():
        return "输出目录不存在"

    md_content = ""

    # 统计一级目录（检查类型）
    level1_dirs = [d.name for d in output_dir.iterdir() if d.is_dir()]
    md_content += "### 一级目录（检查类型）\n\n"
    for d in sorted(level1_dirs):
        md_content += f"- {d}\n"
    md_content += "\n"

    # 统计每个检查类型的文件数
    md_content += "### 各检查类型的Markdown文件数\n\n"
    md_content += "| 检查类型 | 文件数 |\n|----------|--------|\n"
    for d in sorted(output_dir.iterdir()):
        if d.is_dir():
            md_count = len(list(d.rglob('*.md')))
            md_content += f"| {d.name} | {md_count} 个文件 |\n"
    md_content += "\n"

    # 统计总文件数
    total_md = len(list(output_dir.rglob('*.md')))
    total_dirs = sum(1 for _ in output_dir.rglob('*') if _.is_dir())
    md_content += "### 总计\n\n"
    md_content += f"- **总文件夹数**: {total_dirs}\n"
    md_content += f"- **总Markdown文件数**: {total_md}\n"

    # 同名文件统计
    duplicate_files = list(output_dir.rglob('*_*.md'))
    if duplicate_files:
        md_content += f"- **同名文件(带编号)数**: {len(duplicate_files)}\n"
    
    md_content += "\n"

    return md_content


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="将 Excel 报告模板切片为带 kb_id 的 Markdown")
    parser.add_argument("--input-file", help="单个 Excel 文件路径")
    parser.add_argument("--output", help="Markdown 根目录，默认 output_markdown")
    parser.add_argument("--kb-id", help="逻辑知识库 ID（建议使用 UUID）")
    args = parser.parse_args()

    if args.input_file:
        if not args.kb_id:
            parser.error("--input-file 必须同时提供 --kb-id")
        output_dir = Path(args.output or (Path(__file__).resolve().parent / "output_markdown"))
        stats = process_uploaded_excel(args.input_file, output_dir, args.kb_id)
        print(f"已生成知识库 {args.kb_id} 的 Markdown: {stats['processed_rows']} 条")
        return

    # 输入输出路径
    # 以脚本所在的 data_pipeline 目录为根目录，确保从任意工作目录运行都使用同一组输入输出。
    pipeline_dir = Path(__file__).resolve().parent
    template_dir = pipeline_dir / 'report_template'
    output_dir = pipeline_dir / 'output_markdown'

    # 自动获取report_template目录中的所有xlsx文件
    xlsx_files = []
    if template_dir.exists():
        for f in template_dir.glob('*.xlsx'):
            # 排除临时文件（以~$开头）
            if not f.name.startswith('~$'):
                xlsx_files.append(f.name)
        xlsx_files.sort()

    print("=" * 60)
    print("医疗报告模板处理脚本")
    print("=" * 60)
    print(f"模板目录: {template_dir}")
    print(f"输出目录: {output_dir}")
    print(f"待处理文件数: {len(xlsx_files)}")
    if xlsx_files:
        print(f"文件列表: {xlsx_files}")
    print("=" * 60)
    
    # 清空并创建输出目录
    if output_dir.exists():
        shutil.rmtree(output_dir)
        print("已清空旧的输出目录")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 全局文件名计数器（用于处理命名冲突）
    filename_counter = {}
    # 内容缓存（用于判断重复数据）
    content_cache = {}
    # 差异记录列表（用于生成报告）
    diff_records = []

    # 处理每个文件
    all_stats = []
    for xlsx_file in xlsx_files:
        file_path = template_dir / xlsx_file
        if file_path.exists():
            stats = process_excel_file(str(file_path), output_dir, filename_counter, content_cache, diff_records)
            all_stats.append(stats)
        else:
            print(f"文件不存在: {file_path}")
    
    # 计算总计
    total_rows = sum(s['total_rows'] for s in all_stats)
    total_processed = sum(s['processed_rows'] for s in all_stats)
    total_skipped = sum(s['skipped_duplicates'] for s in all_stats)
    total_different = sum(s['different_content'] for s in all_stats)
    total_errors = sum(len(s['errors']) for s in all_stats)
    
    # 打印处理进度
    print("\n" + "=" * 60)
    print("处理完成！")
    print("=" * 60)
    print(f"总行数: {total_rows}")
    print(f"已处理: {total_processed}")
    print(f"跳过重复: {total_skipped}")
    print(f"内容不同: {total_different}")
    if total_errors > 0:
        print(f"错误数: {total_errors}")

    # 分析输出目录
    analyze_output(output_dir)

    # 检查空的检查类型
    check_empty_modality(template_dir, xlsx_files)

    # 生成最终报告
    generate_final_report(diff_records, content_cache, output_dir, all_stats, xlsx_files)


if __name__ == '__main__':
    main()
