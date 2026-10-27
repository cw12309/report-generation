"""报告模板库管理：优秀报告范文的收藏/编辑/启停/删除 + 生成时匹配注入。

存储：app/reporting/prompts/templates.yml（单文件全量，yml 列表）
入库校验：ReportValidator 校验范文结构，不合格拒绝入库
匹配：route_id 过滤 → match_keywords/tags 精确命中 → 字符 bigram Jaccard 相似度兜底
注入：service.generate_report 路由解析后调用 match_template，命中注入 system prompt 范例章节
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

TEMPLATES_FILE = Path(__file__).parent / "prompts" / "templates.yml"

# 内存缓存（保存/热重载时刷新）
_cache: list[dict] = []
_cache_loaded = False


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_templates(force: bool = False) -> list[dict]:
    """加载模板库（带缓存）"""
    global _cache, _cache_loaded
    if _cache_loaded and not force:
        return _cache
    _cache = []
    if TEMPLATES_FILE.exists():
        try:
            with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            items = data.get("templates") if isinstance(data, dict) else data
            if isinstance(items, list):
                _cache = [t for t in items if isinstance(t, dict) and t.get("id")]
        except Exception as e:
            logger.warning(f"读取模板库失败: {e}")
    _cache_loaded = True
    return _cache


def reload_templates() -> list[dict]:
    return load_templates(force=True)


def save_templates(templates: list[dict]):
    """写盘并刷新缓存"""
    global _cache, _cache_loaded
    TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        yaml.safe_dump({"templates": templates}, f, allow_unicode=True, sort_keys=False, width=1000)
    _cache = list(templates)
    _cache_loaded = True
    logger.info(f"模板库已保存: {len(templates)} 条")


def _new_id() -> str:
    return f"tpl_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def validate_template_payload(payload: dict, existing_ids: list[str], allow_same: bool = False) -> list[str]:
    """模板入库/编辑校验：结构 + 校验器范文检查"""
    errors: list[str] = []
    name = str(payload.get("name") or "").strip()
    route_id = str(payload.get("route_id") or "").strip()
    findings = str(payload.get("imaging_findings") or "").strip()
    diagnosis = str(payload.get("imaging_diagnosis") or "").strip()
    if not name:
        errors.append("模板名称不能为空")
    if not route_id:
        errors.append("必须指定归属路由")
    if not findings:
        errors.append("影像所见范文不能为空")
    if not diagnosis:
        errors.append("影像诊断范文不能为空")
    if errors:
        return errors

    # 范文结构校验：复用 ReportValidator 的三行所见/范式规则（构造最小 LLMReportOutput）
    try:
        from .schemas import LLMReportOutput, ReportContent
        from .report_validator import ReportValidator

        out = LLMReportOutput(
            report=ReportContent(imaging_findings=findings, imaging_diagnosis=diagnosis),
            questions=[],
            question_round=0,
            can_ask_questions=False,
        )
        verr = ReportValidator().validate(out, question_round=0)
        # 范文允许含 XX 占位以外的宽松项：仅拦截结构性硬伤（三行/范式/标题）
        hard = [e for e in verr if any(k in e for k in ("三行", "行", "范式", "归纳", "演绎", "标题"))]
        if hard:
            errors.append("范文结构校验未通过：" + "；".join(hard[:3]))
    except Exception as e:
        logger.warning(f"模板范文校验异常（跳过）: {e}")
    return errors


def upsert_template(payload: dict) -> dict:
    """新增或更新模板，返回落盘后的模板"""
    templates = load_templates()
    payload = dict(payload or {})
    tid = str(payload.get("id") or "").strip()
    is_new = not tid
    if is_new:
        tid = _new_id()
        while any(t.get("id") == tid for t in templates):
            tid = _new_id() + "_x"
    payload["id"] = tid
    payload["name"] = str(payload.get("name") or "").strip()
    payload["route_id"] = str(payload.get("route_id") or "").strip()
    payload["tags"] = [str(t).strip() for t in (payload.get("tags") or []) if str(t).strip()]
    payload["match_keywords"] = [str(k).strip() for k in (payload.get("match_keywords") or []) if str(k).strip()]
    payload["imaging_findings"] = str(payload.get("imaging_findings") or "").strip()
    payload["imaging_diagnosis"] = str(payload.get("imaging_diagnosis") or "").strip()
    payload["source"] = str(payload.get("source") or "manual")
    payload["enabled"] = bool(payload.get("enabled", True))
    payload["quality_score"] = max(1, min(5, int(payload.get("quality_score") or 5)))
    payload["updated_at"] = _now()

    replaced = False
    for i, t in enumerate(templates):
        if t.get("id") == tid:
            payload.setdefault("usage_count", int(t.get("usage_count") or 0))
            payload.setdefault("created_at", t.get("created_at") or _now())
            templates[i] = payload
            replaced = True
            break
    if not replaced:
        payload.setdefault("usage_count", 0)
        payload["created_at"] = _now()
        templates.append(payload)
    save_templates(templates)
    return payload


def delete_template(tid: str) -> bool:
    templates = load_templates()
    kept = [t for t in templates if t.get("id") != tid]
    if len(kept) == len(templates):
        return False
    save_templates(kept)
    return True


def toggle_template(tid: str) -> Optional[bool]:
    templates = load_templates()
    for t in templates:
        if t.get("id") == tid:
            t["enabled"] = not bool(t.get("enabled", True))
            save_templates(templates)
            return bool(t["enabled"])
    return None


def bump_usage(tid: str):
    """引用计数 +1（生成命中时调用，不触发全量重写缓存以外的 IO 风险）"""
    templates = load_templates()
    for t in templates:
        if t.get("id") == tid:
            t["usage_count"] = int(t.get("usage_count") or 0) + 1
            save_templates(templates)
            return


# ===== 匹配与注入 =====

_STOP = set("的了是在和有与及或等个一不未见明显大致约左右侧上下内外部行扫描范围本次检查图像质量")


def _bigrams(text: str) -> set:
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)} - _STOP


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_template(route_id: str, doctor_input: str, threshold: float = 0.18) -> Optional[dict]:
    """按路由+医生输入匹配最佳启用模板。

    优先级：match_keywords/tags 精确子串命中（计分）→ bigram Jaccard 相似度兜底。
    """
    templates = [t for t in load_templates() if t.get("enabled", True) and t.get("route_id") == route_id]
    if not templates:
        return None
    text = doctor_input or ""
    scored: list[tuple[float, dict]] = []
    for t in templates:
        # 文本证据分：命中关键词/标签 + bigram 相似度；不足阈值直接跳过（先验不得单独触发）
        base = 0.0
        for kw in (t.get("match_keywords") or []) + (t.get("tags") or []):
            if kw and kw in text:
                base += 2.0 + len(kw) * 0.1
        sim = _jaccard(_bigrams(text), _bigrams(f"{t.get('imaging_findings', '')} {t.get('imaging_diagnosis', '')}"))
        base += sim * 3.0
        if base < threshold:
            continue
        # 质量分与使用次数仅作同分排序先验
        score = base + int(t.get("quality_score") or 5) * 0.02 + min(int(t.get("usage_count") or 0), 20) * 0.005
        scored.append((score, t))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    logger.info(f"模板库命中: {best.get('id')} score={best_score:.2f}")
    return best


def build_injection_block(tpl: dict) -> str:
    """生成注入 system prompt 的范例章节"""
    name = tpl.get("name") or "本院优秀范例"
    return (
        "\n---\n\n"
        "### 本院优秀范例参考（风格与结构参照）\n\n"
        f"以下为本院医生认可的{tpl.get('route_id', '')}优秀报告《{name}》，"
        "生成时请参照其行文风格、三行结构、分级书写与建议句专业度；"
        "**数值、节段、病灶位置等一律以医生本次输入为准，严禁照抄范例数值**；"
        "范式选择（演绎/归纳）、追问设计、章节标题仍以上文路由规则为准，范例仅作风格参照。\n\n"
        "影像所见：\n"
        f"{tpl.get('imaging_findings', '')}\n"
        "影像诊断：\n"
        f"{tpl.get('imaging_diagnosis', '')}\n"
    )
