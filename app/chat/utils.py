"""无状态的工具函数"""

import json
import logging
import re
import time
from functools import wraps

import requests

from .report_schema import ReportSchemaError, parse_report_output

logger = logging.getLogger(__name__)


# =============================================================================
# 重试装饰器
# =============================================================================
def retry(max_attempts=3, delay=2, exceptions=(requests.RequestException,)):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        raise
                    logger.warning(f"{e}，{delay}s 后重试 ({attempt+1}/{max_attempts})")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator


# =============================================================================
# JSON 提取工具
# =============================================================================
def _extract_json(text):
    """从 LLM 输出中提取 JSON"""
    text = text.strip()

    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        text = match.group(1).strip()

    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    logger.warning(f"无法解析 JSON: {text[:200]}...")
    return {"error": "JSON 解析失败", "raw": text[:500]}


# =============================================================================
# JSON 报告 → Markdown 展示文本
# =============================================================================
def json_to_display(report_json):
    """将 JSON 报告转换为 Markdown 展示文本

    边界处兼容旧格式，但展示层只消费规范化后的 Canonical results[]。
    非法或非报告文本不会作为正式报告原样显示。
    """
    try:
        report_json = parse_report_output(report_json)
    except ReportSchemaError:
        logger.warning("拒绝展示非法 StructuredReport")
        return ""

    lines = []

    results = report_json["results"]
    lines.append("## 一、影像学表现")
    lines.append("")
    for item in results:
        imaging = item["影像学表现"]
        if imaging:
            for line in imaging.replace(r'\n', '\n').splitlines():
                lines.append(line)
            lines.append("")

    lines.append("## 二、诊断意见")
    lines.append("")
    for item in results:
        diagnosis = item["诊断意见"]
        if diagnosis:
            for line in diagnosis.replace(r'\n', '\n').splitlines():
                lines.append(line)
            lines.append("")

    return "\n".join(lines).strip()
