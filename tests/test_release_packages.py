from __future__ import annotations

import json
from pathlib import Path

from deltarune_agent.deltamod_csx_package import sha256_csx_file
from deltarune_agent.deltamod_package import DELTARUNE_CURRENT_HASHES
from mods.speed.tools import build_packages as speed_packages
from mods.telemetry.tools import build_packages as telemetry_packages


ROOT = Path(__file__).resolve().parents[1]
MODS_ROOT = ROOT / "mods"
SPEED_ROOT = MODS_ROOT / "speed"
SPEED_DIRECTORY = SPEED_ROOT / "deltamod"
TELEMETRY_ROOT = MODS_ROOT / "telemetry"
TELEMETRY_DIRECTORY = TELEMETRY_ROOT / "deltamod"


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


def test_speed_release_manifest_records_direct_csx_candidate():
    release = json.loads(
        (SPEED_ROOT / "release_1.3.0.json").read_text(encoding="utf-8")
    )

    assert speed_packages.VERSION == "1.3.0"
    assert release["format"] == "DeltaMod direct-CSX source package"
    assert release["status"] == (
        "source-level validation passed; DeltaMod runtime verification pending"
    )
    assert release["version"] == "1.3.0"
    assert release["target_version"] == "1.05"
    assert release["steam_build_id"] == "24484059"
    assert release["chapter5_version_marker"] == "v0.0.253"
    assert release["clean_chapter_sha256"] == _expected_hashes()
    assert release["source"] == "AiSpeed.csx"
    assert release["source_sha256"] == sha256_csx_file(SPEED_ROOT / "AiSpeed.csx")
    assert release["reproducibility"]["canonical_text"] == (
        "UTF-8 without BOM, LF line endings"
    )
    assert release["reproducibility"]["compression"] == (
        "STORED (no compression; zlib-independent)"
    )

    package = release["package"]
    assert package["file"] == "AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.0.zip"
    assert package["size"] == 23704
    assert package["sha256"] == (
        "08ee5fcb0278c97cd2197b97df23c2be852eefd630be1d1f146bbaab1300c842"
    )
    assert package["root_entries"] == [
        "meta.json",
        "modding.xml",
        "Chapter1Speed.csx",
        "Chapter2Speed.csx",
        "Chapter3Speed.csx",
        "Chapter4Speed.csx",
        "Chapter5Speed.csx",
    ]
    _assert_semantic_matrix(release["semantic_validation"])


def test_telemetry_release_manifest_records_direct_csx_candidate():
    release = json.loads(
        (TELEMETRY_ROOT / "release_9.2.0.json").read_text(encoding="utf-8")
    )

    assert telemetry_packages.VERSION == "9.2.0"
    assert telemetry_packages.TELEMETRY_PROTOCOL == 9
    assert release["format"] == "DeltaMod direct-CSX source package"
    assert release["status"] == (
        "source-level validation passed; DeltaMod runtime verification pending"
    )
    assert release["version"] == "9.2.0"
    assert release["telemetry_protocol"] == 9
    assert release["target_version"] == "1.05"
    assert release["steam_build_id"] == "24484059"
    assert release["chapter5_version_marker"] == "v0.0.253"
    assert release["clean_chapter_sha256"] == _expected_hashes()
    assert release["source"] == "AiTelemetry.csx"
    assert release["source_sha256"] == sha256_csx_file(
        TELEMETRY_ROOT / "AiTelemetry.csx"
    )
    assert release["reproducibility"]["canonical_text"] == (
        "UTF-8 without BOM, LF line endings"
    )
    assert release["reproducibility"]["compression"] == (
        "STORED (no compression; zlib-independent)"
    )

    package = release["package"]
    assert package["file"] == "Telemetry-All-Chapters-DeltaMod-CSX-v9.2.0.zip"
    assert package["size"] == 53404
    assert package["sha256"] == (
        "c66e2f679ce8892c6aaefc6dddb47efef571e60b239714528fde962c99f9a710"
    )
    assert package["root_entries"] == [
        "meta.json",
        "modding.xml",
        "Chapter1Telemetry.csx",
        "Chapter2Telemetry.csx",
        "Chapter3Telemetry.csx",
        "Chapter4Telemetry.csx",
        "Chapter5Telemetry.csx",
    ]
    _assert_semantic_matrix(release["semantic_validation"])


def test_only_current_validated_binary_packages_are_committed():
    speed_files = sorted(path.name for path in SPEED_DIRECTORY.iterdir() if path.is_file())
    telemetry_files = sorted(
        path.name for path in TELEMETRY_DIRECTORY.iterdir() if path.is_file()
    )

    assert speed_files == [
        "AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.0.zip",
        "README.md",
    ]
    assert telemetry_files == [
        "README.md",
        "Telemetry-All-Chapters-DeltaMod-CSX-v9.2.0.zip",
    ]
    assert not list(SPEED_DIRECTORY.glob("*.g3mpatch"))
    assert not list(TELEMETRY_DIRECTORY.glob("*.g3mpatch"))


def test_direct_csx_builders_target_the_candidate_versions():
    assert speed_packages.VERSION == "1.3.0"
    assert telemetry_packages.VERSION == "9.2.0"
    assert telemetry_packages.TELEMETRY_PROTOCOL == 9

    speed_source = (SPEED_ROOT / "AiSpeed.csx").read_text(encoding="utf-8")
    telemetry_source = (TELEMETRY_ROOT / "AiTelemetry.csx").read_text(
        encoding="utf-8"
    )
    assert "AI speed mod v1.3.0" in speed_source
    assert "AI_SPEED_MOD|1|" in speed_source
    assert "DRTEL|9|" in telemetry_source
