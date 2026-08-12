from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

CURRENT_PACKAGES = (
    (
        REPOSITORY_ROOT
        / "mods"
        / "speed"
        / "deltamod"
        / "AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.0.zip",
        REPOSITORY_ROOT / "mods" / "speed" / "release_1.3.0.json",
    ),
    (
        REPOSITORY_ROOT
        / "mods"
        / "telemetry"
        / "deltamod"
        / "Telemetry-All-Chapters-DeltaMod-CSX-v9.2.0.zip",
        REPOSITORY_ROOT / "mods" / "telemetry" / "release_9.2.0.json",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_only_current_validated_mod_packages_are_committed() -> None:
    expected_paths = {package for package, _release in CURRENT_PACKAGES}
    package_roots = (
        REPOSITORY_ROOT / "mods" / "speed" / "deltamod",
        REPOSITORY_ROOT / "mods" / "telemetry" / "deltamod",
    )
    committed_archives = {
        path
        for root in package_roots
        for path in root.glob("*.zip")
    }
    assert committed_archives == expected_paths

    for package, release_path in CURRENT_PACKAGES:
        release = json.loads(release_path.read_text(encoding="utf-8"))
        expected = release["package"]
        assert package.name == expected["file"]
        assert package.stat().st_size == expected["size"]
        assert _sha256(package) == expected["sha256"]


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
