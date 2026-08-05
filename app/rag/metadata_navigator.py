"""Navigate the modality/level hierarchy used to constrain retrieval plans."""

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from config import get_metadata_path

FIELD_LABELS = {
    "level_1": "一级目录",
    "level_2": "二级目录",
    "level_3": "三级目录",
    "level_4": "四级目录",
}
LABEL_FIELDS = {label: field for field, label in FIELD_LABELS.items()}
LEVEL_FIELDS = tuple(FIELD_LABELS)


class MetadataNavigator:
    """Load hierarchy JSON and expose progressively constrained candidates."""

    def __init__(self, metadata_path: Optional[str] = None,
                 metadata_trees: Optional[Iterable[Dict]] = None):
        if metadata_trees is not None:
            self.metadata_path = None
            self.tree = self.merge_trees(metadata_trees)
            self.paths = self._flatten_paths()
            return
        configured = Path(metadata_path or get_metadata_path())
        fallback = Path(__file__).resolve().parents[1] / "data_pipeline" / "metadata.json"
        self.metadata_path = configured
        self.tree = self._read_tree(configured)
        if not self.tree.get("检查类型"):
            self.metadata_path = fallback
            self.tree = self._read_tree(fallback)
        if not self.tree.get("检查类型"):
            self.tree = {"检查类型": {}}
        self.paths = self._flatten_paths()

    @classmethod
    def from_knowledge_bases(cls, knowledge_bases: Iterable[Dict]):
        return cls(metadata_trees=[
            kb.get("metadata_tree", {}) for kb in knowledge_bases
            if kb.get("status") == "active"
        ])

    @staticmethod
    def merge_trees(trees: Iterable[Dict]) -> Dict:
        """Merge selected KB trees without mutating their SQLite payloads."""
        merged: Dict = {"检查类型": {}}

        def merge_node(target: Dict, source: Dict) -> None:
            for key, value in source.items():
                if isinstance(value, dict):
                    merge_node(target.setdefault(key, {}), value)
                else:
                    target[key] = value

        for tree in trees:
            if isinstance(tree, dict):
                merge_node(merged, tree)
        return merged

    @staticmethod
    def _read_tree(path: Path) -> Dict:
        if not path.is_file():
            return {"检查类型": {}}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("检查类型"), dict):
                return loaded
        except (OSError, json.JSONDecodeError):
            pass
        return {"检查类型": {}}

    def _flatten_paths(self) -> List[Dict[str, str]]:
        records: List[Dict[str, str]] = []

        def visit(node, current):
            found_child = False
            if isinstance(node, dict):
                for label, values in node.items():
                    field = LABEL_FIELDS.get(label)
                    if not field or not isinstance(values, dict):
                        continue
                    for value, child in values.items():
                        found_child = True
                        updated = dict(current)
                        updated[field] = value
                        visit(child, updated)
            if not found_child:
                records.append(dict(current))

        for modality, subtree in self.tree.get("检查类型", {}).items():
            visit(subtree, {"modality": modality})
        return records

    def modalities(self) -> List[str]:
        return sorted(self.tree.get("检查类型", {}).keys())

    def candidates(self, field: str, selected: Optional[Dict[str, str]] = None) -> List[str]:
        """Return allowed values under already selected ancestor fields."""
        selected = selected or {}
        values = set()
        for record in self.paths:
            if all(
                not selected.get(key) or record.get(key) == selected[key]
                for key in ("modality", *LEVEL_FIELDS)
                if key != field
            ):
                value = record.get(field)
                if value:
                    values.add(value)
        return sorted(values)

    def match(self, text: str, field: str, selected: Optional[Dict[str, str]] = None) -> str:
        """Find the longest allowed hierarchy value explicitly present in text."""
        options = self.modalities() if field == "modality" else self.candidates(field, selected)
        for value in sorted(options, key=len, reverse=True):
            if value and value in text:
                return value
        return ""

    def best_matching_path(self, text: str, modality: str = "") -> Dict[str, str]:
        """Choose one valid path that best explains all explicit terms."""
        best_path: Dict[str, str] = {}
        best_score = (0, 0, 0)
        for record in self.paths:
            if modality and record.get("modality") != modality:
                continue
            matched_fields = []
            matched_values = set()
            for field in LEVEL_FIELDS:
                value = record.get(field, "")
                if value and value in text and value not in matched_values:
                    matched_fields.append(field)
                    matched_values.add(value)
            score = (
                sum(len(value) for value in matched_values),
                len(matched_values),
                max((LEVEL_FIELDS.index(field) + 1 for field in matched_fields), default=0),
            )
            if score > best_score:
                best_score = score
                best_path = {
                    field: record[field]
                    for field in matched_fields
                }
        return best_path

    def validate(self, field: str, value: str, selected: Optional[Dict[str, str]] = None) -> bool:
        if not value:
            return False
        options: Iterable[str]
        if field == "modality":
            options = self.modalities()
        else:
            options = self.candidates(field, selected)
        return value in options

    def has_data(self) -> bool:
        return bool(self.paths)
