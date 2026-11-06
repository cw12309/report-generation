"""知识库模板参照：按路由从 Milvus 检索本院同模态模板切片，格式化为参考资料。

用途：
1. 新路由草稿生成：把本院知识库中同部位/同模态的「影像学表现+影像学意见」
   模板对作为锚点材料，供草稿 skill 挖掘本院分级术语/病变清单/书写句式。
2. 报告生成风格参照：generate/iterate 时注入少量同模态模板对，统一本院文风。

检索复用 app/rag 管线（query_planner 规则规划 + Dense/Sparse 混合召回 + 降级），
不走 LLM 规划（use_llm=False）以保持低延迟与确定性。
"""

import logging
import re
from pathlib import Path
from typing import Optional

from config import get_db_path, get_markdown_dir, get_milvus_token

from app.chat.llm_client import get_embedding
from app.milvus_compat import create_milvus_client
from app.rag.query_planner import build_query_plan
from app.rag.retrieval import retrieve_with_fallback

logger = logging.getLogger(__name__)

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
_SEC_RE = re.compile(r"###\s*(影像学表现|影像学意见)\s*\n(.*?)(?=\n###|\Z)", re.S)


def _parse_markdown_slice(path: Path) -> Optional[dict]:
    """解析本地 Markdown 切片（frontmatter + 影像学表现/意见两节）"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta: dict = {}
    m = _FM_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    sections = {name: body.strip() for name, body in _SEC_RE.findall(text)}
    findings = sections.get("影像学表现", "")
    opinion = sections.get("影像学意见", "")
    if not findings and not opinion:
        return None
    return {
        "title": meta.get("module_name", ""),
        "level_1": meta.get("level_1", ""),
        "modality": meta.get("modality", ""),
        "findings": findings,
        "opinion": opinion,
    }


def _local_markdown_reference(modality: str, terms: list[str], top_k: int) -> list[dict]:
    """Milvus 不可用时的本地兜底：扫描 markdown 切片目录，按目录路径+内容词命中筛选。

    目录结构 {kb_id}/{modality}/{level_1}/[{level_2}/]{module}.md，路径即元数据，
    先用路径过滤（零 IO 成本）再解析候选文件，避免全量解析 3000+ 文件。
    """
    root = Path(get_markdown_dir())
    if not root.is_dir() or not terms:
        return []
    # 目录 modality 命名与路由不完全一致（MR/MRI、MG/钼靶），按别名集合匹配
    _MOD_ALIASES = {
        "MRI": {"MRI", "MR"}, "MR": {"MRI", "MR"},
        "CT": {"CT"}, "DR": {"DR", "CR", "X线"}, "MG": {"MG", "钼靶"},
    }
    mod = (modality or "").strip().upper()
    mod_set = _MOD_ALIASES.get(mod, {mod}) if mod else set()
    # level_1 目录命名同义词（颅脑↔头颅/头部、腰/颈/胸椎↔脊柱），扩展路径过滤词
    _LEVEL_SYN = {
        "脑": "头部", "颅脑": "头颅", "头颅": "头部",
        "腰椎": "脊柱", "颈椎": "脊柱", "胸椎": "脊柱",
    }
    path_terms = list(terms)
    for t in terms:
        syn = _LEVEL_SYN.get(t)
        if syn and syn not in path_terms:
            path_terms.append(syn)
    scored: list[tuple[int, dict]] = []
    for p in root.rglob("*.md"):
        parts = p.relative_to(root).parts
        if len(parts) < 3:
            continue
        if mod_set and parts[1].upper() not in mod_set:
            continue
        path_hay = "/".join(parts[1:]).lower()
        if not any(t in path_hay for t in path_terms):
            continue
        item = _parse_markdown_slice(p)
        if not item:
            continue
        hay = f"{item['title']} {item['level_1']} {item['findings']} {item['opinion']}".lower()
        hits = sum(1 for t in terms if t in hay)
        if hits:
            scored.append((hits, item))
        if len(scored) >= top_k * 8:
            break
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:top_k]]


def kb_reference_for_route(
    name: str,
    modality: str = "",
    body_parts: Optional[list] = None,
    aliases: Optional[list] = None,
    top_k: int = 10,
    max_chars: int = 8000,
    focus: str = "",
) -> dict:
    """按路由名称/部位从知识库检索同模态模板切片，返回格式化参考资料。

    Returns: {"text", "chars", "count", "items"}；检索不可用/无命中时 text="" 且 count=0。
    Raises: 不抛异常（内部兜底为空结果），调用方按 count 判断。
    """
    name = (name or "").strip()
    if not name:
        return {"text": "", "chars": 0, "count": 0, "items": []}
    parts = " ".join(str(p) for p in (body_parts or []) if str(p).strip())
    alias = " ".join(str(a) for a in (aliases or [])[:3] if str(a).strip())
    query = f"{name} {parts} {alias} 影像报告 影像学表现 影像学意见 模板 术语 分级".strip()

    # 部位相关性过滤词：防止同模态跨部位切片（如腰椎路由引到乳腺模板）污染参照
    from .route_admin import _focus_terms

    terms = [t.lower() for t in _focus_terms(focus or name)] if (focus or name) else []

    candidates = []
    try:
        plan = build_query_plan(query, use_llm=False)
        mod = (modality or "").strip().upper()
        if mod:
            plan.setdefault("filters", {})["modality"] = mod
        query_vec = get_embedding(plan["semantic_query"])
        client = create_milvus_client(get_db_path(), get_milvus_token())
        try:
            candidates, _details = retrieve_with_fallback(
                plan=plan, query_vector=query_vec, top_k=top_k, client=client
            )
        finally:
            try:
                client.close()
            except Exception:
                pass
    except Exception as e:
        # Milvus 不可用（未启动/远程不可达）：回退本地 Markdown 切片目录检索
        logger.warning(f"Milvus 模板参照检索失败，回退本地 Markdown 切片: {type(e).__name__}: {e}")
        items = _local_markdown_reference(modality, terms, top_k)
        return _format_reference(items, max_chars)

    items: list[dict] = []
    seen: set[str] = set()
    for e in candidates or []:
        cid = str(e.get("chunk_id") or e.get("id") or "")
        if cid and cid in seen:
            continue
        seen.add(cid)
        findings = str(e.get("imaging_findings") or "").strip()
        opinion = str(e.get("imaging_opinion") or "").strip()
        if not findings and not opinion:
            continue
        if terms:
            hay = f"{e.get('module_name', '')} {e.get('level_1', '')} {e.get('level_2', '')} {findings} {opinion}".lower()
            if not any(t in hay for t in terms):
                continue
        items.append({
            "title": str(e.get("module_name") or e.get("level_3") or e.get("level_2") or "").strip(),
            "level_1": str(e.get("level_1") or "").strip(),
            "modality": str(e.get("modality") or "").strip(),
            "findings": findings,
            "opinion": opinion,
        })
        if len(items) >= top_k:
            break

    if not items:
        # Milvus 有连接但无命中：同样回退本地切片，保证有参照可用
        items = _local_markdown_reference(modality, terms, top_k)

    if not items:
        return {"text": "", "chars": 0, "count": 0, "items": []}
    return _format_reference(items, max_chars)


def _format_reference(items: list[dict], max_chars: int) -> dict:
    blocks = []
    for it in items:
        line = f"【{it['title'] or '模板'}】影像学表现：{it['findings']}｜影像学意见：{it['opinion']}"
        if it["level_1"]:
            line += f"（部位：{it['level_1']}）"
        blocks.append(line)
    text = "\n".join(blocks)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n…（模板切片过多，已截断）"
    return {"text": text, "chars": len(text), "count": len(items), "items": items}
