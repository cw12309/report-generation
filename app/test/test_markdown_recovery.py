"""测试 Markdown 格式报告的 Recovery 能力"""

import sys
from pathlib import Path

import pytest

# 添加 app 目录到 sys.path
APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from chat.report_schema import parse_report_output, ReportSchemaError


def test_parse_markdown_report_standard_format():
    """测试标准 Markdown 格式的解析"""
    markdown = """
## 一、影像学表现

右侧基底节区见高密度影，CT值约60HU，范围约3.2×2.8cm，周围轻度水肿。

## 二、诊断意见

右侧基底节区脑出血
"""
    result = parse_report_output(markdown)
    
    assert "results" in result
    assert len(result["results"]) == 1
    assert "影像学表现" in result["results"][0]
    assert "诊断意见" in result["results"][0]
    assert "右侧基底节区" in result["results"][0]["影像学表现"]
    assert "脑出血" in result["results"][0]["诊断意见"]


def test_parse_markdown_report_with_extra_whitespace():
    """测试带额外空白的 Markdown 格式"""
    markdown = """

## 一、影像学表现

右侧基底节区见高密度影。


## 二、诊断意见

右侧基底节区脑出血

"""
    result = parse_report_output(markdown)
    assert len(result["results"]) == 1
    assert "右侧基底节区" in result["results"][0]["影像学表现"]


def test_parse_markdown_report_alternative_headings():
    """测试备选标题格式"""
    markdown = """
## 影像学所见

右侧基底节区见高密度影。

## 印象

右侧基底节区脑出血
"""
    result = parse_report_output(markdown)
    assert len(result["results"]) == 1
    assert "右侧基底节区" in result["results"][0]["影像学表现"]
    assert "脑出血" in result["results"][0]["诊断意见"]


def test_parse_markdown_report_multiline_content():
    """测试多行内容"""
    markdown = """
## 一、影像学表现

1. 右侧基底节区见高密度影，CT值约60HU
2. 范围约3.2×2.8cm
3. 周围轻度水肿

## 二、诊断意见

1. 右侧基底节区脑出血
2. 建议临床相关性
"""
    result = parse_report_output(markdown)
    assert len(result["results"]) == 1
    imaging = result["results"][0]["影像学表现"]
    diagnosis = result["results"][0]["诊断意见"]
    
    # 验证多行都被保留
    assert "右侧基底节区" in imaging
    assert "3.2×2.8cm" in imaging
    assert "周围轻度水肿" in imaging
    assert "脑出血" in diagnosis
    assert "临床相关性" in diagnosis


def test_parse_json_report_still_works():
    """确保 JSON 格式仍然正常工作"""
    json_report = {
        "results": [
            {
                "影像学表现": "右侧基底节区见高密度影。",
                "诊断意见": "右侧基底节区脑出血"
            }
        ]
    }
    result = parse_report_output(json_report)
    assert result["results"] == json_report["results"]


def test_parse_json_string_still_works():
    """确保 JSON 字符串格式仍然正常工作"""
    json_str = '{"results": [{"影像学表现": "右侧基底节区见高密度影。", "诊断意见": "右侧基底节区脑出血"}]}'
    result = parse_report_output(json_str)
    assert len(result["results"]) == 1
    assert "脑出血" in result["results"][0]["诊断意见"]


def test_parse_empty_markdown_raises_error():
    """测试空 Markdown 报告抛出错误"""
    markdown = """
## 一、影像学表现

## 二、诊断意见
"""
    with pytest.raises(ReportSchemaError, match="未包含有效"):
        parse_report_output(markdown)


def test_parse_markdown_with_only_imaging():
    """测试只有影像学表现的 Markdown"""
    markdown = """
## 一、影像学表现

右侧基底节区见高密度影。
"""
    result = parse_report_output(markdown)
    assert len(result["results"]) == 1
    assert "右侧基底节区" in result["results"][0]["影像学表现"]
    assert result["results"][0]["诊断意见"] == "未记录"


def test_parse_markdown_with_only_diagnosis():
    """测试只有诊断意见的 Markdown"""
    markdown = """
## 二、诊断意见

右侧基底节区脑出血
"""
    result = parse_report_output(markdown)
    assert len(result["results"]) == 1
    assert result["results"][0]["影像学表现"] == "未记录"
    assert "脑出血" in result["results"][0]["诊断意见"]
