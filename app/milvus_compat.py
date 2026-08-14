"""Milvus client factory and narrowly scoped Milvus Lite compatibility fixes."""

from __future__ import annotations

import inspect
import json
import logging
import os
import shutil
import threading
from typing import Any

from pymilvus import MilvusClient


logger = logging.getLogger(__name__)
_PATCH_LOCK = threading.Lock()


def _is_local_uri(uri: str) -> bool:
    normalized = str(uri or "").strip().lower()
    return not normalized.startswith(("http://", "https://"))


def _install_windows_manifest_replace(manifest_class: type, manifest_module: Any) -> bool:
    """Use Windows-safe replacement in the affected Milvus Lite save method."""
    current_save = manifest_class.save
    if getattr(current_save, "_uses_windows_replace", False):
        return True

    try:
        source = inspect.getsource(current_save)
    except (OSError, TypeError):
        return False
    if "os.rename(tmp_path, target_path)" not in source:
        return False

    def save(self) -> None:
        os.makedirs(self._data_dir, exist_ok=True)

        new_version = self._version + 1
        payload = self._to_payload()
        payload["version"] = new_version

        target_path = os.path.join(self._data_dir, manifest_module.MANIFEST_FILENAME)
        prev_path = os.path.join(self._data_dir, manifest_module.MANIFEST_PREV_FILENAME)
        tmp_path = os.path.join(self._data_dir, manifest_module.MANIFEST_TMP_FILENAME)

        try:
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, indent=2, sort_keys=True, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())

            if os.path.exists(target_path):
                try:
                    shutil.copy2(target_path, prev_path)
                except OSError as exc:
                    logger.warning("manifest: failed to create .prev backup: %s", exc)

            os.replace(tmp_path, target_path)
        except BaseException:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

        self._version = new_version
        try:
            directory_fd = os.open(self._data_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass

    save._uses_windows_replace = True
    save.__name__ = current_save.__name__
    save.__doc__ = current_save.__doc__
    manifest_class.save = save
    return True


def apply_milvus_lite_windows_compat(uri: str) -> bool:
    """Apply the known local Milvus Lite manifest fix when it is needed."""
    if os.name != "nt" or not _is_local_uri(uri):
        return False

    try:
        from milvus_lite.storage import manifest as manifest_module
    except ImportError:
        return False

    with _PATCH_LOCK:
        patched = _install_windows_manifest_replace(
            manifest_module.Manifest,
            manifest_module,
        )
    if patched:
        logger.info("已启用 Windows Milvus Lite manifest 原子替换兼容处理")
    return patched


def create_milvus_client(uri: str, token: str = "") -> MilvusClient:
    """Create local or remote Milvus clients through one compatibility path."""
    apply_milvus_lite_windows_compat(uri)
    kwargs = {"uri": uri}
    if token:
        kwargs["token"] = token
    return MilvusClient(**kwargs)
