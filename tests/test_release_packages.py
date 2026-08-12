from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from deltarune_agent.deltamod_csx_package import sha256_csx_file, validate_csx_package
from deltarune_agent.deltamod_package import DELTARUNE_CURRENT_HASHES
from mods.speed.tools import build_packages as speed_packages
from mods.support.tools import build_packages as support_packages
from mods.telemetry.tools import build_packages as telemetry_packages


ROOT = Path(__file__).resolve().parents[1]
MODS_ROOT = ROOT / "mods"
SPEED_ROOT = MODS_ROOT / "speed"
SPEED_DIRECTORY = SPEED_ROOT / "deltamod"
TELEMETRY_ROOT = MODS_ROOT / "telemetry"
TELEMETRY_DIRECTORY = TELEMETRY_ROOT / "deltamod"
SUPPORT_ROOT = MODS_ROOT / "support"
SUPPORT_DIRECTORY = SUPPORT_ROOT / "deltamod"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_hashes() -> dict[str, str]:
    return {
        str(chapter): checksum
        for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
    }


def _assert_semantic_matrix(record: dict[str, object]) -> None:
    assert record["utmt_cli_version"] == "0.9.1.2"
    assert record["telemetry_only"] == "pass Chapters 1-5"
    assert record["speed_only"] == "pass Chapters 1-5"
    assert record["telemetry_then_speed"] == "pass Chapters 1-5"
    assert record["speed_then_telemetry"] == "pass Chapters 1-5"
    assert record["obj_time_bbox_leak"] is False


def test_validated_game_baseline_matches_current_hash_constants():
    baseline = json.loads(
        (MODS_ROOT / "validated_deltarune_build.json").read_text(encoding="utf-8")
    )
    assert baseline["steam_build_id"] == "24484059"
    assert baseline["deltarune_target_version"] == "1.05"
    assert baseline["chapter5_version_marker"] == "v0.0.253"
    assert baseline["chapter_sha256"] == _expected_hashes()
    assert len(baseline["source_archive_sha256"]) == 64
    assert baseline["validation"]["obj_time_bbox_leak"] is False


def test_speed_release_manifest_records_corrected_csx_candidate():
    release = json.loads(
        (SPEED_ROOT / "release_1.3.1.json").read_text(encoding="utf-8")
    )
    package_path = SPEED_DIRECTORY / release["package"]["file"]

    assert speed_packages.VERSION == "1.3.1"
    assert release["format"] == "DeltaMod direct-CSX source package"
    assert release["status"] == (
        "source-level validation passed; DeltaMod runtime verification pending"
    )
    assert release["version"] == "1.3.1"
    assert release["target_version"] == "1.05"
    assert release["steam_build_id"] == "24484059"
    assert release["chapter5_version_marker"] == "v0.0.253"
    assert release["clean_chapter_sha256"] == _expected_hashes()
    assert release["source_sha256"] == sha256_csx_file(SPEED_ROOT / "AiSpeed.csx")
    assert release["package"]["patch_type"] == "csx"
    assert release["package"]["size"] == 23689
    assert release["package"]["sha256"] == (
        "bab2cd4ce2340ed4b15c83037b9dea8500e267e640972834b9a22fd41dfd0d3d"
    )
    assert package_path.stat().st_size == release["package"]["size"]
    assert _sha256(package_path) == release["package"]["sha256"]
    validation = validate_csx_package(package_path, expected_chapters=(1, 2, 3, 4, 5))
    assert validation["patch_type"] == "csx"
    with zipfile.ZipFile(package_path) as archive:
        modding = archive.read("modding.xml").decode("utf-8")
    assert 'type="xdelta"' not in modding
    assert modding.count('type="csx"') == 5
    _assert_semantic_matrix(release["semantic_validation"])


def test_telemetry_release_manifest_records_corrected_csx_candidate():
    release = json.loads(
        (TELEMETRY_ROOT / "release_9.2.1.json").read_text(encoding="utf-8")
    )
    package_path = TELEMETRY_DIRECTORY / release["package"]["file"]

    assert telemetry_packages.VERSION == "9.2.1"
    assert telemetry_packages.TELEMETRY_PROTOCOL == 9
    assert release["format"] == "DeltaMod direct-CSX source package"
    assert release["status"] == (
        "source-level validation passed; DeltaMod runtime verification pending"
    )
    assert release["version"] == "9.2.1"
    assert release["telemetry_protocol"] == 9
    assert release["target_version"] == "1.05"
    assert release["steam_build_id"] == "24484059"
    assert release["chapter5_version_marker"] == "v0.0.253"
    assert release["clean_chapter_sha256"] == _expected_hashes()
    assert release["source_sha256"] == sha256_csx_file(
        TELEMETRY_ROOT / "AiTelemetry.csx"
    )
    assert release["package"]["patch_type"] == "csx"
    assert release["package"]["size"] == 53389
    assert release["package"]["sha256"] == (
        "609afc19c41e2e65001bb7d3eb8a3f18918fb6dd214a3e9ed91c04202cb88ef1"
    )
    assert package_path.stat().st_size == release["package"]["size"]
    assert _sha256(package_path) == release["package"]["sha256"]
    validation = validate_csx_package(package_path, expected_chapters=(1, 2, 3, 4, 5))
    assert validation["patch_type"] == "csx"
    with zipfile.ZipFile(package_path) as archive:
        modding = archive.read("modding.xml").decode("utf-8")
    assert 'type="xdelta"' not in modding
    assert modding.count('type="csx"') == 5
    _assert_semantic_matrix(release["semantic_validation"])


def test_support_release_is_atomic_speed_and_telemetry_candidate():
    release = json.loads(
        (SUPPORT_ROOT / "release_1.0.0.json").read_text(encoding="utf-8")
    )
    package_path = SUPPORT_DIRECTORY / release["package"]["file"]

    assert support_packages.VERSION == "1.0.0"
    assert support_packages.SPEED_COMPONENT_VERSION == "1.3.1"
    assert support_packages.TELEMETRY_COMPONENT_VERSION == "9.2.1"
    assert support_packages.TELEMETRY_PROTOCOL == 9
    assert release["version"] == "1.0.0"
    assert release["speed_component_version"] == "1.3.1"
    assert release["telemetry_component_version"] == "9.2.1"
    assert release["telemetry_protocol"] == 9
    assert release["target_version"] == "1.05"
    assert release["steam_build_id"] == "24484059"
    assert release["chapter5_version_marker"] == "v0.0.253"
    assert release["clean_chapter_sha256"] == _expected_hashes()
    assert release["speed_source_sha256"] == sha256_csx_file(SPEED_ROOT / "AiSpeed.csx")
    assert release["telemetry_source_sha256"] == sha256_csx_file(
        TELEMETRY_ROOT / "AiTelemetry.csx"
    )
    assert release["combined_source_sha256"] == (
        "14d82f34ef5e2c61e4abb486bdc6a22efc9056d10a23a8378e80134b64a9595e"
    )
    assert release["package"]["patch_type"] == "csx"
    assert release["package"]["size"] == 81579
    assert release["package"]["sha256"] == (
        "b017fe942d67c713b3c0ee7fe003787a024f600eed2ebb9314b33d67221ea5b5"
    )
    assert package_path.stat().st_size == release["package"]["size"]
    assert _sha256(package_path) == release["package"]["sha256"]
    validation = validate_csx_package(package_path, expected_chapters=(1, 2, 3, 4, 5))
    assert validation["patch_type"] == "csx"

    with zipfile.ZipFile(package_path) as archive:
        modding = archive.read("modding.xml").decode("utf-8")
        source = archive.read("Chapter1Support.csx").decode("utf-8")
        assert all(
            archive.read(f"Chapter{chapter}Support.csx").decode("utf-8") == source
            for chapter in range(1, 6)
        )
    assert 'type="xdelta"' not in modding
    assert modding.count('type="csx"') == 5
    assert "void InstallAiSpeed()" in source
    assert "void InstallAiTelemetry()" in source
    assert "AI_SPEED_MOD|1|" in source
    assert "DRTEL|9|" in source
    assert source.rstrip().endswith("InstallAiTelemetry();")
    assert hashlib.sha256(source.encode("utf-8")).hexdigest() == release[
        "combined_source_sha256"
    ]


def test_only_current_validated_binary_packages_are_committed():
    speed_files = sorted(path.name for path in SPEED_DIRECTORY.iterdir() if path.is_file())
    telemetry_files = sorted(
        path.name for path in TELEMETRY_DIRECTORY.iterdir() if path.is_file()
    )
    support_files = sorted(path.name for path in SUPPORT_DIRECTORY.iterdir() if path.is_file())
    assert speed_files == [
        "AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.1.zip",
        "README.md",
    ]
    assert telemetry_files == [
        "README.md",
        "Telemetry-All-Chapters-DeltaMod-CSX-v9.2.1.zip",
    ]
    assert support_files == [
        "AI-Support-All-Chapters-DeltaMod-CSX-v1.0.0.zip",
        "README.md",
    ]


def test_current_builders_target_corrected_versions_without_changing_protocol():
    assert speed_packages.VERSION == "1.3.1"
    assert telemetry_packages.VERSION == "9.2.1"
    assert support_packages.VERSION == "1.0.0"
    assert telemetry_packages.TELEMETRY_PROTOCOL == 9
    assert support_packages.TELEMETRY_PROTOCOL == 9
    speed_source = (SPEED_ROOT / "AiSpeed.csx").read_text(encoding="utf-8")
    telemetry_source = (TELEMETRY_ROOT / "AiTelemetry.csx").read_text(encoding="utf-8")
    assert "AI_SPEED_MOD|1|" in speed_source
    assert "DRTEL|9|" in telemetry_source
