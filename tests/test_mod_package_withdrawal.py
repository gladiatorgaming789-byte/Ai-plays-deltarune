from __future__ import annotations

import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_withdrawn_compiled_packages_are_not_committed() -> None:
    package_roots = (
        REPOSITORY_ROOT / "mods" / "speed" / "deltamod",
        REPOSITORY_ROOT / "mods" / "telemetry" / "deltamod",
    )
    committed_archives = [
        path
        for root in package_roots
        for path in root.glob("*.zip")
    ]
    assert committed_archives == []


def test_old_release_records_are_explicitly_withdrawn() -> None:
    records = (
        REPOSITORY_ROOT / "mods" / "speed" / "release_1.2.0.json",
        REPOSITORY_ROOT / "mods" / "telemetry" / "release_9.1.0.json",
    )
    for path in records:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["status"] == "withdrawn"
        assert "bbox_top" in payload["reason"]
        assert "Direct-CSX" in payload["replacement"]
