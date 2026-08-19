from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def test_exact_pinned_speed_package_remains_committed() -> None:
    package = (
        REPOSITORY_ROOT
        / "mods"
        / "speed"
        / "deltamod"
        / "AI-Speed-All-Chapters-DeltaMod-CSX-v1.4.0.zip"
    )
    release_path = REPOSITORY_ROOT / "mods" / "speed" / "release_1.4.0.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    expected = release["package"]
    assert package.is_file()
    assert package.name == expected["file"]
    assert package.stat().st_size == expected["size"]
    assert _sha256(package) == expected["sha256"]
    assert expected["patch_type"] == "csx"


def test_unsafe_training_autosave_packages_are_withdrawn_and_absent() -> None:
    records = (
        (
            REPOSITORY_ROOT / "mods" / "telemetry" / "release_9.3.0.json",
            REPOSITORY_ROOT
            / "mods"
            / "telemetry"
            / "deltamod"
            / "Telemetry-All-Chapters-DeltaMod-CSX-v9.3.0.zip",
            "Telemetry 9.3.1",
        ),
        (
            REPOSITORY_ROOT / "mods" / "support" / "release_2.0.0.json",
            REPOSITORY_ROOT
            / "mods"
            / "support"
            / "deltamod"
            / "AI-Support-All-Chapters-DeltaMod-CSX-v2.0.0.zip",
            "AI Support 2.0.1",
        ),
    )
    for release_path, package, replacement in records:
        payload = json.loads(release_path.read_text(encoding="utf-8"))
        assert payload["status"] == "withdrawn"
        assert payload["package"]["committed"] is False
        assert replacement in payload["replacement"]
        assert not package.exists()


def test_current_safe_source_records_require_v2_autosave_marker() -> None:
    records = (
        REPOSITORY_ROOT / "mods" / "telemetry" / "release_9.3.1.json",
        REPOSITORY_ROOT / "mods" / "support" / "release_2.0.1.json",
    )
    for path in records:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "current source candidate" in payload["status"]
        assert payload["required_safety_marker"] == "AI_BACKGROUND_AUTOSAVE_V2"
        assert payload["package"]["patch_type"] == "csx"
        assert payload["package"]["committed"] is False


def test_compiled_release_records_remain_withdrawn() -> None:
    records = (
        REPOSITORY_ROOT / "mods" / "speed" / "release_1.2.0.json",
        REPOSITORY_ROOT / "mods" / "telemetry" / "release_9.1.0.json",
    )
    for path in records:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["status"] == "withdrawn"
        assert "bbox_top" in payload["reason"]


def test_xdelta_routed_csx_release_records_are_withdrawn() -> None:
    records = (
        REPOSITORY_ROOT / "mods" / "speed" / "release_1.3.0.json",
        REPOSITORY_ROOT / "mods" / "telemetry" / "release_9.2.0.json",
    )
    for path in records:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["status"] == "withdrawn"
        assert "type=xdelta" in payload["reason"]
        assert "End of Central Directory" in payload["reason"]
        assert "type=csx" in payload["replacement"]


def test_historical_support_200_runtime_validation_is_not_current_release_gate() -> None:
    validation = json.loads(
        (REPOSITORY_ROOT / "mods" / "support" / "validation_2.0.0.json").read_text(
            encoding="utf-8"
        )
    )
    withdrawn = json.loads(
        (REPOSITORY_ROOT / "mods" / "support" / "release_2.0.0.json").read_text(
            encoding="utf-8"
        )
    )
    assert validation["result"] == "PASS"
    assert withdrawn["status"] == "withdrawn"
    assert withdrawn["package"]["sha256"] == validation["package_sha256"]
