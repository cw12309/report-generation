"""部位特化规则加载器 (Structure Rules Loader)

负责从 manifest.yaml 建立规则元数据注册表，按 (modality, body_part)
精确匹配部位特化 MRI 报告规则 Markdown 文件，支持别名归一化和延迟加载。

设计原则:
- 确定性路由: 仅做精确匹配 + alias 展开，不做 fuzzy/embedding/语义推断
- 延迟加载: 初始化时只读 manifest，正文首次请求时才读取并缓存
- 模块级缓存: 遵循项目现有的 prompt/__init__.py (_CACHE) 和 config.py (_config) 风格
- 路径稳定: 不依赖 cwd，基于本文件位置解析项目根目录
"""

from __future__ import annotations

import logging
import os
import yaml
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROMPT_DIR = os.path.dirname(os.path.abspath(__file__))
_STRUCTURE_DIR = os.path.join(_PROMPT_DIR, "structure")
_MANIFEST_PATH = os.path.join(_STRUCTURE_DIR, "manifest.yaml")

_registry: Dict[Tuple[str, str], "RuleMetadata"] = {}
_alias_index: Dict[Tuple[str, str], str] = {}
_content_cache: Dict[str, str] = {}
_initialized: bool = False

MODALITY_NORMALIZE_MAP: Dict[str, str] = {
    "mr": "MR",
    "mri": "MR",
    "MRI": "MR",
}


@dataclass
class RuleMetadata:
    """单条规则元数据"""
    id: str
    modality: str
    body_part: str
    aliases: List[str]
    file: str
    version: str


@dataclass
class StructureRulesResult:
    """规则加载结果"""
    content: str
    normalized_modality: Optional[str]
    normalized_body_parts: List[str]
    matched_rule_ids: List[str]
    matched_rule_files: List[str]
    unmatched_body_parts: List[str]
    cache_hits: List[str] = field(default_factory=list)


class StructureRulesLoader:
    """部位特化规则加载器

    使用方式:
        result = StructureRulesLoader.get_rules("MR", ["腰椎", "胸椎"])
        if result.content:
            print(result.content)
    """

    @staticmethod
    def _ensure_initialized() -> None:
        global _registry, _alias_index, _initialized
        if _initialized:
            return
        _registry.clear()
        _alias_index.clear()
        _load_manifest()
        _initialized = True

    @staticmethod
    def get_rules(
        modality: Optional[str],
        body_parts: Optional[List[str]],
    ) -> StructureRulesResult:
        """根据 modality 和 body_parts 加载对应规则。

        Args:
            modality: 检查类型，如 "MR"、"MRI"、"CT"。None 时全部 unmatched。
            body_parts: 部位列表，如 ["腰椎", "颈椎"]。None 或空列表时全部 unmatched。

        Returns:
            StructureRulesResult: 包含拼接后的规则内容、匹配/未匹配详情。
        """
        StructureRulesLoader._ensure_initialized()

        normalized_modality = _normalize_modality(modality)
        requested_body_parts = body_parts or []

        logger.debug(
            "get_rules: modality_raw=%s modality_normalized=%s requested_body_parts=%s",
            modality, normalized_modality, requested_body_parts,
        )

        matched_ids: List[str] = []
        matched_files: List[str] = []
        unmatched: List[str] = []
        cache_hits: List[str] = []
        content_parts: List[str] = []

        if normalized_modality is None:
            unmatched = list(requested_body_parts)
            logger.info("get_rules: modality 归一化后为 None，全部 unmatched")
            return StructureRulesResult(
                content="",
                normalized_modality=None,
                normalized_body_parts=[],
                matched_rule_ids=[],
                matched_rule_files=[],
                unmatched_body_parts=unmatched,
                cache_hits=[],
            )

        for bp_raw in requested_body_parts:
            if not bp_raw or not bp_raw.strip():
                unmatched.append(bp_raw or "")
                continue

            normalized_bp = _normalize_body_part(normalized_modality, bp_raw.strip())
            if normalized_bp is None:
                unmatched.append(bp_raw.strip())
                continue

            key = (normalized_modality, normalized_bp)
            rule = _registry.get(key)

            if rule is None:
                unmatched.append(bp_raw.strip())
                logger.debug("get_rules: no rule for (%s, %s)", normalized_modality, normalized_bp)
                continue

            if rule.id in _content_cache:
                content = _content_cache[rule.id]
                cache_hits.append(rule.id)
                logger.debug("get_rules: cache hit for rule_id=%s", rule.id)
            else:
                content = _load_rule_content(rule)
                _content_cache[rule.id] = content
                logger.debug("get_rules: cache miss, loaded rule_id=%s", rule.id)

            matched_ids.append(rule.id)
            matched_files.append(rule.file)

            header = f"# Body-Part Rule: {rule.body_part}"
            content_parts.append(f"{header}\n\n{content}")

        combined_content = "\n\n---\n\n".join(content_parts)

        logger.info(
            "get_rules: matched_rule_ids=%s matched_rule_files=%s unmatched_body_parts=%s cache_hits=%s",
            matched_ids, matched_files, unmatched, cache_hits,
        )

        return StructureRulesResult(
            content=combined_content,
            normalized_modality=normalized_modality,
            normalized_body_parts=[
                _normalize_body_part(normalized_modality, bp.strip())
                for bp in requested_body_parts if bp and bp.strip()
                and _normalize_body_part(normalized_modality, bp.strip()) is not None
            ],
            matched_rule_ids=matched_ids,
            matched_rule_files=matched_files,
            unmatched_body_parts=unmatched,
            cache_hits=cache_hits,
        )

    @staticmethod
    def reload() -> None:
        """强制重新加载 manifest 并清空内容缓存。"""
        global _initialized, _registry, _alias_index, _content_cache
        _registry.clear()
        _alias_index.clear()
        _content_cache.clear()
        _initialized = False
        StructureRulesLoader._ensure_initialized()
        logger.info("StructureRulesLoader: manifest 和缓存已重新加载")


def _normalize_modality(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    return MODALITY_NORMALIZE_MAP.get(raw.strip(), raw.strip())


def _normalize_body_part(modality: str, raw: str) -> Optional[str]:
    key = (modality, raw)
    if key in _registry:
        return raw
    alias_canonical = _alias_index.get(key)
    if alias_canonical is not None:
        return alias_canonical
    return None


def _load_manifest() -> None:
    if not os.path.exists(_MANIFEST_PATH):
        logger.warning("manifest 文件不存在 (%s)，部位特化规则将不可用", _MANIFEST_PATH)
        return

    with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("manifest 顶层必须是字典")

    rules = data.get("rules")
    if not isinstance(rules, list):
        raise ValueError("manifest 中 'rules' 必须是有效列表")

    _validate_and_register(rules)
    logger.info(
        "manifest 加载完成: %d 条规则, %d 个别名映射",
        len(_registry), len(_alias_index),
    )


def _validate_and_register(rules: list) -> None:
    seen_ids: set = set()
    seen_pairs: set = set()

    for i, rule_entry in enumerate(rules):
        if not isinstance(rule_entry, dict):
            raise ValueError(f"rules[{i}] 必须是字典，实际类型: {type(rule_entry).__name__}")

        rule_id = rule_entry.get("id")
        modality = rule_entry.get("modality")
        body_part = rule_entry.get("body_part")
        aliases = rule_entry.get("aliases", [])
        file_name = rule_entry.get("file")
        version = rule_entry.get("version", "1.0")

        if not rule_id or not isinstance(rule_id, str):
            raise ValueError(f"rules[{i}]: 'id' 字段缺失或不是字符串")
        if not modality or not isinstance(modality, str):
            raise ValueError(f"rules[{i}] (id={rule_id}): 'modality' 字段缺失或不是字符串")
        if not body_part or not isinstance(body_part, str):
            raise ValueError(f"rules[{i}] (id={rule_id}): 'body_part' 字段缺失或不是字符串")
        if not file_name or not isinstance(file_name, str):
            raise ValueError(f"rules[{i}] (id={rule_id}): 'file' 字段缺失或不是字符串")
        if not isinstance(aliases, list):
            raise ValueError(f"rules[{i}] (id={rule_id}): 'aliases' 必须是列表")

        if rule_id in seen_ids:
            raise ValueError(f"rules[{i}]: rule id '{rule_id}' 重复")
        seen_ids.add(rule_id)

        pair = (modality, body_part)
        if pair in seen_pairs:
            raise ValueError(
                f"rules[{i}]: (modality='{modality}', body_part='{body_part}') 重复, "
                f"id={rule_id}"
            )
        seen_pairs.add(pair)

        _validate_file_path(file_name, rule_id)

        for alias in aliases:
            alias_key = (modality, alias)
            if alias_key in _alias_index:
                existing = _alias_index[alias_key]
                if existing != body_part:
                    raise ValueError(
                        f"rules[{i}] (id={rule_id}): alias '{alias}' 在 modality='{modality}' "
                        f"下已映射到 '{existing}'，不能再次映射到 '{body_part}'"
                    )
            _alias_index[alias_key] = body_part

        metadata = RuleMetadata(
            id=rule_id,
            modality=modality,
            body_part=body_part,
            aliases=list(aliases),
            file=file_name,
            version=version,
        )
        _registry[pair] = metadata


def _validate_file_path(file_name: str, rule_id: str) -> None:
    if not file_name.endswith(".md"):
        raise ValueError(
            f"rule id='{rule_id}': file '{file_name}' 必须是 .md 文件"
        )

    normalized = os.path.normpath(file_name)
    if normalized.startswith("..") or os.path.isabs(normalized):
        raise ValueError(
            f"rule id='{rule_id}': file '{file_name}' 包含路径遍历，"
            f"仅允许 docs/structure/ 下的文件名"
        )

    full_path = os.path.join(_STRUCTURE_DIR, normalized)
    real_rule_dir = os.path.realpath(_STRUCTURE_DIR)
    real_file_path = os.path.realpath(full_path)

    if not real_file_path.startswith(real_rule_dir + os.sep) and real_file_path != real_rule_dir:
        raise ValueError(
            f"rule id='{rule_id}': file '{file_name}' 解析后路径不在 "
            f"docs/structure/ 目录内"
        )

    if not os.path.isfile(full_path):
        raise FileNotFoundError(
            f"rule id='{rule_id}': 规则文件不存在: {full_path}"
        )


def _load_rule_content(rule: RuleMetadata) -> str:
    file_path = os.path.join(_STRUCTURE_DIR, rule.file)
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read().strip()