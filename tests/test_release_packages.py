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


def test_speed_release_remains_exact_byte_pinned():
    release = json.loads(
        (SPEED_ROOT / "release_1.4.0.json").read_text(encoding="utf-8")
    )
    package_path = SPEED_DIRECTORY / release["package"]["file"]

    assert speed_packages.VERSION == "1.4.0"
    assert release["speed_mod_version"] == "1.4.0"
    assert release["target_version"] == "1.05"
    assert release["source_sha256"] == sha256_csx_file(SPEED_ROOT / "AiSpeed.csx")
    assert release["package"]["patch_type"] == "csx"
    assert package_path.stat().st_size == release["package"]["size"]
    assert _sha256(package_path) == release["package"]["sha256"]
    validation = validate_csx_package(package_path, expected_chapters=(1, 2, 3, 4, 5))
    assert validation["patch_type"] == "csx"


def test_telemetry_931_is_current_safe_source_candidate():
    current = json.loads(
        (TELEMETRY_ROOT / "release_9.3.1.json").read_text(encoding="utf-8")
    )
    withdrawn = json.loads(
        (TELEMETRY_ROOT / "release_9.3.0.json").read_text(encoding="utf-8")
    )
    source = (TELEMETRY_ROOT / "AiTelemetry.csx").read_text(encoding="utf-8")

    assert telemetry_packages.VERSION == "9.3.1"
    assert telemetry_packages.TELEMETRY_PROTOCOL == 9
    assert current["telemetry_mod_version"] == "9.3.1"
    assert current["telemetry_protocol"] == 9
    assert current["target_version"] == "1.05"
    assert current["package"]["file"] == (
        "Telemetry-All-Chapters-DeltaMod-CSX-v9.3.1.zip"
    )
    assert current["package"]["patch_type"] == "csx"
    assert current["package"]["committed"] is False
    assert current["required_safety_marker"] == "AI_BACKGROUND_AUTOSAVE_V2"
    assert withdrawn["status"] == "withdrawn"
    assert withdrawn["package"]["committed"] is False
    assert "AI_BACKGROUND_AUTOSAVE_V2" in source
    assert 'if (string_length(global.__ai_instance_id) > 0)' in source
    assert "scr_save();" in source
    assert source.index('if (string_length(global.__ai_instance_id) > 0)') < source.index(
        "scr_save();"
    )


def test_support_201_composes_speed_140_and_safe_telemetry_931():
    current = json.loads(
        (SUPPORT_ROOT / "release_2.0.1.json").read_text(encoding="utf-8")
    )
    withdrawn = json.loads(
        (SUPPORT_ROOT / "release_2.0.0.json").read_text(encoding="utf-8")
    )
    speed_source = SPEED_ROOT / "AiSpeed.csx"
    telemetry_source = TELEMETRY_ROOT / "AiTelemetry.csx"
    combined = support_packages.combined_source_bytes(
        speed_source,
        telemetry_source,
    ).decode("utf-8")

    assert support_packages.VERSION == "2.0.1"
    assert support_packages.SPEED_COMPONENT_VERSION == "1.4.0"
    assert support_packages.TELEMETRY_COMPONENT_VERSION == "9.3.1"
    assert support_packages.TELEMETRY_PROTOCOL == 9
    assert current["version"] == "2.0.1"
    assert current["speed_component_version"] == "1.4.0"
    assert current["telemetry_component_version"] == "9.3.1"
    assert current["package"]["file"] == (
        "AI-Support-All-Chapters-DeltaMod-CSX-v2.0.1.zip"
    )
    assert current["package"]["committed"] is False
    assert withdrawn["status"] == "withdrawn"
    assert "void InstallAiSpeed()" in combined
    assert "void InstallAiTelemetry()" in combined
    assert "AI_SPEED_MOD|1|" in combined
    assert "DRTEL|9|" in combined
    assert "AI_MULTI_INSTANCE|1|" in combined
    assert "AI_BACKGROUND_AUTOSAVE_V2" in combined
    assert combined.rstrip().endswith("InstallAiTelemetry();")


def test_unsafe_930_and_200_archives_are_not_committed():
    assert not (
        TELEMETRY_DIRECTORY / "Telemetry-All-Chapters-DeltaMod-CSX-v9.3.0.zip"
    ).exists()
    assert not (
        SUPPORT_DIRECTORY / "AI-Support-All-Chapters-DeltaMod-CSX-v2.0.0.zip"
    ).exists()


def test_only_safe_optional_current_source_archives_may_exist():
    speed_files = sorted(path.name for path in SPEED_DIRECTORY.glob("*.zip"))
    telemetry_files = sorted(path.name for path in TELEMETRY_DIRECTORY.glob("*.zip"))
    support_files = sorted(path.name for path in SUPPORT_DIRECTORY.glob("*.zip"))
    assert speed_files == ["AI-Speed-All-Chapters-DeltaMod-CSX-v1.4.0.zip"]
    assert set(telemetry_files) <= {
        "Telemetry-All-Chapters-DeltaMod-CSX-v9.3.1.zip"
    }
    assert set(support_files) <= {
        "AI-Support-All-Chapters-DeltaMod-CSX-v2.0.1.zip"
    }

    for directory, expected in (
        (TELEMETRY_DIRECTORY, "Telemetry-All-Chapters-DeltaMod-CSX-v9.3.1.zip"),
        (SUPPORT_DIRECTORY, "AI-Support-All-Chapters-DeltaMod-CSX-v2.0.1.zip"),
    ):
        package = directory / expected
        if package.is_file():
            validation = validate_csx_package(
                package,
                expected_chapters=(1, 2, 3, 4, 5),
            )
            assert validation["patch_type"] == "csx"
            with zipfile.ZipFile(package) as archive:
                payload_names = [
                    name
                    for name in archive.namelist()
                    if name.startswith("Chapter") and name.endswith(".csx")
                ]
                assert len(payload_names) == 5
                assert all(
                    b"AI_BACKGROUND_AUTOSAVE_V2" in archive.read(name)
                    for name in payload_names
                )


def test_current_builders_keep_protocol_and_safe_autosave_contract():
    assert speed_packages.VERSION == "1.4.0"
    assert telemetry_packages.VERSION == "9.3.1"
    assert support_packages.VERSION == "2.0.1"
    assert telemetry_packages.TELEMETRY_PROTOCOL == 9
    assert support_packages.TELEMETRY_PROTOCOL == 9
    speed_source = (SPEED_ROOT / "AiSpeed.csx").read_text(encoding="utf-8")
    telemetry_source = (TELEMETRY_ROOT / "AiTelemetry.csx").read_text(encoding="utf-8")
    assert "AI_SPEED_MOD|1|" in speed_source
    assert "DRTEL|9|" in telemetry_source
    assert "AI_MULTI_INSTANCE|1|" in telemetry_source
    assert "AI_BACKGROUND_AUTOSAVE_V2" in telemetry_source
