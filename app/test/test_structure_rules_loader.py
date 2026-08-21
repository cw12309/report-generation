"""部位特化规则加载器回归测试

覆盖:
  - 精确匹配 / alias 归一化 / modality 归一化
  - 多 body_part 组合 / 部分匹配
  - 未知 rule (unmatched) 不抛异常
  - CT 不 fallback 到 MR
  - 缓存命中 (第二次不读文件)
  - manifest 配置错误 fail fast
  - 路径不依赖 cwd
"""

from __future__ import annotations

import os
import sys
import tempfile
import yaml
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import pytest
from prompt.structure_rules_loader import (
    StructureRulesLoader,
    StructureRulesResult,
    _STRUCTURE_DIR,
    _MANIFEST_PATH,
    _normalize_modality,
)


def _make_manifest(rules: list) -> str:
    return yaml.dump({"rules": rules}, allow_unicode=True, default_flow_style=False)


def _write_rule_file(dir_path: str, filename: str, content: str) -> str:
    full = os.path.join(dir_path, filename)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return full


def _make_rule_content(body_part: str, unique: str = "") -> str:
    return f"# {body_part} 标准化报告模板\n\n## 影像描述模块\n\n{unique}\n\n## 诊断意见\n\n结论模板"


class TestNormalization:
    def test_modality_mr_variants(self):
        assert _normalize_modality("MR") == "MR"
        assert _normalize_modality("MRI") == "MR"
        assert _normalize_modality("mr") == "MR"
        assert _normalize_modality("mri") == "MR"

    def test_modality_ct_not_normalized(self):
        assert _normalize_modality("CT") == "CT"

    def test_modality_none_and_empty(self):
        assert _normalize_modality(None) is None
        assert _normalize_modality("") is None
        assert _normalize_modality("  ") is None


class TestExactMatch:
    def test_mr_lumbar_exact(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["腰椎"])
        assert "腰椎" in result.content
        assert "腰椎MRI标准化报告模板" in result.content
        assert result.matched_rule_ids == ["mr_lumbar_spine"]
        assert result.matched_rule_files == ["腰椎MRI标准化报告模板.md"]
        assert result.unmatched_body_parts == []

    def test_mr_brain_exact(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["颅脑"])
        assert "颅脑" in result.content
        assert result.matched_rule_ids == ["mr_brain"]
        assert result.unmatched_body_parts == []

    def test_mr_cardiac_exact(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["心脏"])
        assert result.matched_rule_ids == ["mr_cardiac"]
        assert result.unmatched_body_parts == []

    def test_mr_breast_exact(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["乳腺"])
        assert result.matched_rule_ids == ["mr_breast"]
        assert result.unmatched_body_parts == []

    def test_mr_cervical_exact(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["颈椎"])
        assert result.matched_rule_ids == ["mr_cervical_spine"]
        assert result.unmatched_body_parts == []

    def test_mr_thoracic_exact(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["胸椎"])
        assert result.matched_rule_ids == ["mr_thoracic_spine"]
        assert result.unmatched_body_parts == []


class TestModalityNormalization:
    def test_mri_normalized_to_mr(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MRI", ["腰椎"])
        assert result.normalized_modality == "MR"
        assert result.matched_rule_ids == ["mr_lumbar_spine"]
        assert result.unmatched_body_parts == []

    def test_lowercase_mr(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("mr", ["腰椎"])
        assert result.normalized_modality == "MR"
        assert result.matched_rule_ids == ["mr_lumbar_spine"]


class TestAliasNormalization:
    def test_lumbar_alias_腰椎MRI(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["腰椎MRI"])
        assert "腰椎" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_lumbar_spine"]
        assert result.unmatched_body_parts == []

    def test_lumbar_alias_l_spine(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["L-spine"])
        assert "腰椎" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_lumbar_spine"]

    def test_lumbar_alias_lumbar_spine(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["lumbar spine"])
        assert "腰椎" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_lumbar_spine"]

    def test_brain_alias(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["brain"])
        assert "颅脑" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_brain"]

    def test_cervical_alias(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["C-spine"])
        assert "颈椎" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_cervical_spine"]

    def test_mri_modality_with_alias(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MRI", ["腰椎MRI"])
        assert result.normalized_modality == "MR"
        assert "腰椎" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_lumbar_spine"]


class TestContentIsolation:
    def test_cervical_only_has_cervical_content(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["颈椎"])
        content = result.content
        assert "颈椎" in content
        assert "Pfirrmann" in content or "颈椎" in content
        assert result.matched_rule_ids == ["mr_cervical_spine"]

    def test_cervical_does_not_contain_lumbar_landmark(self):
        StructureRulesLoader.reload()
        cervical = StructureRulesLoader.get_rules("MR", ["颈椎"]).content
        lumbar = StructureRulesLoader.get_rules("MR", ["腰椎"]).content
        assert "腰椎" not in cervical


class TestNoCtFallback:
    def test_ct_lumbar_unmatched(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("CT", ["腰椎"])
        assert result.content == ""
        assert result.matched_rule_ids == []
        assert result.matched_rule_files == []
        assert result.unmatched_body_parts == ["腰椎"]

    def test_ct_does_not_fallback_to_mr(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("CT", ["腰椎"])
        assert result.normalized_modality == "CT"
        assert result.content == ""


class TestUnknownBodyPart:
    def test_unknown_part_no_exception(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["不存在的部位"])
        assert result.content == ""
        assert result.matched_rule_ids == []
        assert result.unmatched_body_parts == ["不存在的部位"]

    def test_none_modality_all_unmatched(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules(None, ["腰椎"])
        assert result.content == ""
        assert result.matched_rule_ids == []
        assert result.unmatched_body_parts == ["腰椎"]

    def test_none_body_parts_all_unmatched(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", None)
        assert result.content == ""
        assert result.unmatched_body_parts == []

    def test_empty_body_parts_list(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", [])
        assert result.content == ""
        assert result.unmatched_body_parts == []


class TestMultipleBodyParts:
    def test_two_parts_both_loaded(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["腰椎", "胸椎"])
        assert "腰椎" in result.content
        assert "胸椎" in result.content
        assert result.matched_rule_ids == ["mr_lumbar_spine", "mr_thoracic_spine"]
        assert result.unmatched_body_parts == []

    def test_two_parts_correct_order(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["腰椎", "胸椎"])
        lumbar_pos = result.content.index("# Body-Part Rule: 腰椎")
        thoracic_pos = result.content.index("# Body-Part Rule: 胸椎")
        assert lumbar_pos < thoracic_pos

    def test_reversed_order(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["胸椎", "腰椎"])
        thoracic_pos = result.content.index("# Body-Part Rule: 胸椎")
        lumbar_pos = result.content.index("# Body-Part Rule: 腰椎")
        assert thoracic_pos < lumbar_pos

    def test_partial_success(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["腰椎", "不存在的部位"])
        assert result.matched_rule_ids == ["mr_lumbar_spine"]
        assert result.unmatched_body_parts == ["不存在的部位"]
        assert "腰椎" in result.content

    def test_three_parts(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["腰椎", "颈椎", "胸椎"])
        assert len(result.matched_rule_ids) == 3
        assert result.unmatched_body_parts == []


class TestCache:
    def test_second_call_cache_hit(self):
        StructureRulesLoader.reload()
        first = StructureRulesLoader.get_rules("MR", ["腰椎"])
        assert first.cache_hits == []

        second = StructureRulesLoader.get_rules("MR", ["腰椎"])
        assert second.cache_hits == ["mr_lumbar_spine"]
        assert second.content == first.content

    def test_cache_hit_no_file_read(self):
        StructureRulesLoader.reload()
        StructureRulesLoader.get_rules("MR", ["腰椎"])

        with patch("prompt.structure_rules_loader._load_rule_content") as mock_load:
            StructureRulesLoader.get_rules("MR", ["腰椎"])
            mock_load.assert_not_called()

    def test_cache_hit_mixed(self):
        StructureRulesLoader.reload()
        StructureRulesLoader.get_rules("MR", ["腰椎"])

        result = StructureRulesLoader.get_rules("MR", ["腰椎", "胸椎"])
        assert "mr_lumbar_spine" in result.cache_hits
        assert "mr_thoracic_spine" not in result.cache_hits
        assert result.matched_rule_ids == ["mr_lumbar_spine", "mr_thoracic_spine"]


class TestManifestValidation:
    def test_duplicate_id_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_rule_file(tmpdir, "腰椎MRI标准化报告模板.md", "# 腰椎模板")
            _write_rule_file(tmpdir, "颈椎MRI标准化诊断模板.md", "# 颈椎模板")
            rules = [
                {"id": "dup", "modality": "MR", "body_part": "腰椎",
                 "aliases": [], "file": "腰椎MRI标准化报告模板.md", "version": "1.0"},
                {"id": "dup", "modality": "MR", "body_part": "颈椎",
                 "aliases": [], "file": "颈椎MRI标准化诊断模板.md", "version": "1.0"},
            ]
            manifest_path = _write_rule_file(tmpdir, "manifest.yaml", _make_manifest(rules))
            with patch("prompt.structure_rules_loader._MANIFEST_PATH", manifest_path):
                with patch("prompt.structure_rules_loader._STRUCTURE_DIR", tmpdir):
                    with pytest.raises(ValueError, match="重复"):
                        StructureRulesLoader.reload()

    def test_duplicate_pair_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_rule_file(tmpdir, "腰椎MRI标准化报告模板.md", "# 腰椎模板")
            rules = [
                {"id": "a", "modality": "MR", "body_part": "腰椎",
                 "aliases": [], "file": "腰椎MRI标准化报告模板.md", "version": "1.0"},
                {"id": "b", "modality": "MR", "body_part": "腰椎",
                 "aliases": [], "file": "腰椎MRI标准化报告模板.md", "version": "1.0"},
            ]
            manifest_path = _write_rule_file(tmpdir, "manifest.yaml", _make_manifest(rules))
            with patch("prompt.structure_rules_loader._MANIFEST_PATH", manifest_path):
                with patch("prompt.structure_rules_loader._STRUCTURE_DIR", tmpdir):
                    with pytest.raises(ValueError, match="重复"):
                        StructureRulesLoader.reload()

    def test_alias_collision_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_rule_file(tmpdir, "腰椎MRI标准化报告模板.md", "# 腰椎模板")
            _write_rule_file(tmpdir, "颈椎MRI标准化诊断模板.md", "# 颈椎模板")
            rules = [
                {"id": "a", "modality": "MR", "body_part": "腰椎",
                 "aliases": ["spine"], "file": "腰椎MRI标准化报告模板.md", "version": "1.0"},
                {"id": "b", "modality": "MR", "body_part": "颈椎",
                 "aliases": ["spine"], "file": "颈椎MRI标准化诊断模板.md", "version": "1.0"},
            ]
            manifest_path = _write_rule_file(tmpdir, "manifest.yaml", _make_manifest(rules))
            with patch("prompt.structure_rules_loader._MANIFEST_PATH", manifest_path):
                with patch("prompt.structure_rules_loader._STRUCTURE_DIR", tmpdir):
                    with pytest.raises(ValueError, match="已映射"):
                        StructureRulesLoader.reload()

    def test_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = [
                {"id": "a", "modality": "MR", "body_part": "腰椎",
                 "aliases": [], "file": "不存在的文件.md", "version": "1.0"},
            ]
            manifest_path = _write_rule_file(tmpdir, "manifest.yaml", _make_manifest(rules))
            with patch("prompt.structure_rules_loader._MANIFEST_PATH", manifest_path):
                with patch("prompt.structure_rules_loader._STRUCTURE_DIR", tmpdir):
                    with pytest.raises(FileNotFoundError, match="不存在"):
                        StructureRulesLoader.reload()

    def test_path_traversal_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = [
                {"id": "a", "modality": "MR", "body_part": "腰椎",
                 "aliases": [], "file": "../outside.md", "version": "1.0"},
            ]
            manifest_path = _write_rule_file(tmpdir, "manifest.yaml", _make_manifest(rules))
            with patch("prompt.structure_rules_loader._MANIFEST_PATH", manifest_path):
                with patch("prompt.structure_rules_loader._STRUCTURE_DIR", tmpdir):
                    with pytest.raises(ValueError, match="路径遍历"):
                        StructureRulesLoader.reload()

    def test_not_md_file_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rules = [
                {"id": "a", "modality": "MR", "body_part": "腰椎",
                 "aliases": [], "file": "腰椎模板.txt", "version": "1.0"},
            ]
            manifest_path = _write_rule_file(tmpdir, "manifest.yaml", _make_manifest(rules))
            with patch("prompt.structure_rules_loader._MANIFEST_PATH", manifest_path):
                with patch("prompt.structure_rules_loader._STRUCTURE_DIR", tmpdir):
                    with pytest.raises(ValueError, match="\\.md"):
                        StructureRulesLoader.reload()


class TestPathIndependence:
    def test_cwd_independence(self):
        original_cwd = os.getcwd()
        try:
            os.chdir(tempfile.gettempdir())
            StructureRulesLoader.reload()
            result = StructureRulesLoader.get_rules("MR", ["腰椎"])
            assert result.matched_rule_ids == ["mr_lumbar_spine"]
            assert "腰椎" in result.content
        finally:
            os.chdir(original_cwd)


class TestResultStructure:
    def test_result_fields(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["腰椎"])
        assert isinstance(result, StructureRulesResult)
        assert isinstance(result.content, str)
        assert result.normalized_modality == "MR"
        assert isinstance(result.normalized_body_parts, list)
        assert isinstance(result.matched_rule_ids, list)
        assert isinstance(result.matched_rule_files, list)
        assert isinstance(result.unmatched_body_parts, list)
        assert isinstance(result.cache_hits, list)

    def test_result_content_has_body_part_headers(self):
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["腰椎", "颈椎"])
        assert "# Body-Part Rule: 腰椎" in result.content
        assert "# Body-Part Rule: 颈椎" in result.content
        assert "---" in result.content


class TestEntityTrackerRealisticValues:
    """模拟 EntityTracker 实际可能产生的 body_part 值，验证路由覆盖"""

    def test_头颅_hits_颅脑(self):
        """EntityTracker BODY_PART_PATTERNS 包含 '头颅'，规则提取时直接输出"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["头颅"])
        assert "颅脑" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_brain"]
        assert result.matched_rule_files == ["颅脑MRI标准化报告模板.md"]
        assert result.unmatched_body_parts == []

    def test_头部_hits_颅脑(self):
        """EntityTracker BODY_PART_PATTERNS 包含 '头部'"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["头部"])
        assert "颅脑" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_brain"]
        assert result.unmatched_body_parts == []

    def test_大脑_hits_颅脑(self):
        """EntityTracker BODY_PART_PATTERNS 包含 '大脑'"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["大脑"])
        assert "颅脑" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_brain"]
        assert result.unmatched_body_parts == []

    def test_脑部_hits_颅脑(self):
        """EntityTracker BODY_PART_PATTERNS 包含 '脑部',
        DIAGNOSIS_TO_BODY_PART 也将脑部疾病映射到 '脑部'"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["脑部"])
        assert "颅脑" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_brain"]
        assert result.unmatched_body_parts == []

    def test_头颅_with_mri_modality(self):
        """EntityTracker 提取 '头颅' + LLM 提取 'MRI' 的组合"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MRI", ["头颅"])
        assert result.normalized_modality == "MR"
        assert "颅脑" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_brain"]
        assert result.unmatched_body_parts == []

    def test_脑部_from_diagnosis_inference(self):
        """模拟 DIAGNOSIS_TO_BODY_PART 推断: '脑梗' → '脑部'"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["脑部"])
        assert "颅脑" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_brain"]

    def test_心血管_does_not_match_心脏(self):
        """EntityTracker BODY_PART_PATTERNS 包含 '心血管',
        但心血管 ≠ 心脏，不应命中"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["心血管"])
        assert result.matched_rule_ids == []
        assert result.unmatched_body_parts == ["心血管"]

    def test_腰椎_颈椎_胸椎_exact_still_work(self):
        """确认新增别名没有破坏原有精确匹配"""
        StructureRulesLoader.reload()
        for bp in ["腰椎", "颈椎", "胸椎"]:
            result = StructureRulesLoader.get_rules("MR", [bp])
            assert result.matched_rule_ids, f"{bp} 应命中但未命中"
            assert result.unmatched_body_parts == [], f"{bp} 不应有 unmatched"

    def test_脑部_and_腰椎_multi_parts(self):
        """多部位混合: EntityTracker 推断的 '脑部' + 用户明确的 '腰椎'"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["脑部", "腰椎"])
        assert "颅脑" in result.normalized_body_parts
        assert "腰椎" in result.normalized_body_parts
        assert result.matched_rule_ids == ["mr_brain", "mr_lumbar_spine"]
        assert result.unmatched_body_parts == []

    def test_content_contains_颅脑_rule(self):
        """验证通过 '头颅' 加载的内容确实是颅脑规则"""
        StructureRulesLoader.reload()
        result = StructureRulesLoader.get_rules("MR", ["头颅"])
        assert "颅脑" in result.content
        assert "# Body-Part Rule: 颅脑" in result.content