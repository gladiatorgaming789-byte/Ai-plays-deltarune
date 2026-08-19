"""Failure-safe promotion for Independent Population Training v2.1."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from . import multi_instance_training as legacy
from .training_workspace import memory_inventory


_ORIGINAL_PROMOTE = legacy.promote_multi_instance_training_run


def promote_multi_instance_training_run(
    run_directory: Path,
    profile_memory: Path,
) -> dict[str, object]:
    """Promote a winner without importing training-only runtime window metadata."""

    run_directory = Path(run_directory).resolve()
    profile_memory = Path(profile_memory).resolve()
    audit = _ORIGINAL_PROMOTE(run_directory, profile_memory)
    backup_text = str(audit.get("backup_directory") or "")
    backup = Path(backup_text) if backup_text else Path()
    if not backup_text or not backup.is_dir():
        raise OSError(
            "Promotion backup is missing; runtime metadata cannot be sanitized safely."
        )

    try:
        source = backup / "window_titles.json"
        destination = profile_memory / "window_titles.json"
        if source.is_file():
            shutil.copy2(source, destination)
        else:
            destination.unlink(missing_ok=True)
        if not memory_inventory(profile_memory):
            raise OSError("Promotion metadata sanitization produced an empty profile.")
    except BaseException:
        failed = profile_memory.parent / f".{profile_memory.name}.training-metadata-failed"
        if failed.exists():
            shutil.rmtree(failed, ignore_errors=True)
        os.replace(profile_memory, failed)
        os.replace(backup, profile_memory)
        raise

    audit["runtime_metadata_sanitized"] = ["window_titles.json"]
    legacy._write_json(run_directory / "promotion.json", audit)
    manifest_path = run_directory / "training_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["promotion"] = audit
    legacy._write_json(manifest_path, manifest)
    return audit


__all__ = ["promote_multi_instance_training_run"]
