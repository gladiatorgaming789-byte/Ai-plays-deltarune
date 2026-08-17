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
        (SPEED_ROOT / "release_1.4.0.json").read_text(encoding="utf-8")
    )
    package_path = SPEED_DIRECTORY / release["package"]["file"]

    assert speed_packages.VERSION == "1.4.0"
    assert release["format"] == "DeltaMod direct-CSX source package"
    assert release["status"] == "source-level migration; runtime verification pending"
    assert release["speed_mod_version"] == "1.4.0"
    assert release["target_version"] == "1.05"
    assert release["source_sha256"] == sha256_csx_file(SPEED_ROOT / "AiSpeed.csx")
    assert release["package"]["patch_type"] == "csx"
    assert release["package"]["size"] == 9009
    assert release["package"]["sha256"] == (
        "927ec13f0187225eb5c0277d3154747bb9e9ada11135b1a97528a94d1bccb3b9"
    )
    assert package_path.stat().st_size == release["package"]["size"]
    assert _sha256(package_path) == release["package"]["sha256"]
    validation = validate_csx_package(package_path, expected_chapters=(1, 2, 3, 4, 5))
    assert validation["patch_type"] == "csx"
    with zipfile.ZipFile(package_path) as archive:
        modding = archive.read("modding.xml").decode("utf-8")
    assert 'type="xdelta"' not in modding
    assert modding.count('type="csx"') == 5


def test_telemetry_release_manifest_records_corrected_csx_candidate():
    release = json.loads(
        (TELEMETRY_ROOT / "release_9.3.0.json").read_text(encoding="utf-8")
    )
    package_path = TELEMETRY_DIRECTORY / release["package"]["file"]

    assert telemetry_packages.VERSION == "9.3.0"
    assert telemetry_packages.TELEMETRY_PROTOCOL == 9
    assert release["format"] == "DeltaMod direct-CSX source package"
    assert release["status"] == "source-level migration; runtime verification pending"
    assert release["telemetry_mod_version"] == "9.3.0"
    assert release["telemetry_protocol"] == 9
    assert release["target_version"] == "1.05"
    assert release["source_sha256"] == sha256_csx_file(
        TELEMETRY_ROOT / "AiTelemetry.csx"
    )
    assert release["package"]["patch_type"] == "csx"
    assert release["package"]["size"] == 22590
    assert release["package"]["sha256"] == (
        "17d16270731dd44b347f8b42b73bab198cc08a3d0860673271953d639f319784"
    )
    assert package_path.stat().st_size == release["package"]["size"]
    assert _sha256(package_path) == release["package"]["sha256"]
    validation = validate_csx_package(package_path, expected_chapters=(1, 2, 3, 4, 5))
    assert validation["patch_type"] == "csx"
    with zipfile.ZipFile(package_path) as archive:
        modding = archive.read("modding.xml").decode("utf-8")
    assert 'type="xdelta"' not in modding
    assert modding.count('type="csx"') == 5


def test_support_release_is_atomic_speed_and_telemetry_candidate():
    release = json.loads(
        (SUPPORT_ROOT / "release_2.0.0.json").read_text(encoding="utf-8")
    )
    package_path = SUPPORT_DIRECTORY / release["package"]["file"]

    assert support_packages.VERSION == "2.0.0"
    assert support_packages.SPEED_COMPONENT_VERSION == "1.4.0"
    assert support_packages.TELEMETRY_COMPONENT_VERSION == "9.3.0"
    assert support_packages.TELEMETRY_PROTOCOL == 9
    assert release["version"] == "2.0.0"
    assert release["speed_component_version"] == "1.4.0"
    assert release["telemetry_component_version"] == "9.3.0"
    assert release["telemetry_protocol"] == 9
    assert release["target_version"] == "1.05"
    assert release["speed_source_sha256"] == sha256_csx_file(SPEED_ROOT / "AiSpeed.csx")
    assert release["telemetry_source_sha256"] == sha256_csx_file(
        TELEMETRY_ROOT / "AiTelemetry.csx"
    )
    assert release["combined_source_sha256"] == (
        "44875b23c8d24f089e3fc448de941b003ddd34ecce5e1b77709c7fcfce535568"
    )
    assert release["package"]["patch_type"] == "csx"
    assert release["package"]["size"] == 27503
    assert release["package"]["sha256"] == (
        "aa6c7e23f77207c5bcf11e8c5701e96c414af222e73add6d70975c1e763de571"
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
    assert "AI_MULTI_INSTANCE|1|" in source
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
        "AI-Speed-All-Chapters-DeltaMod-CSX-v1.4.0.zip",
        "README.md",
    ]
    assert telemetry_files == [
        "README.md",
        "Telemetry-All-Chapters-DeltaMod-CSX-v9.3.0.zip",
    ]
    assert support_files == [
        "AI-Support-All-Chapters-DeltaMod-CSX-v2.0.0.zip",
        "README.md",
    ]


def test_current_builders_target_corrected_versions_without_changing_protocol():
    assert speed_packages.VERSION == "1.4.0"
    assert telemetry_packages.VERSION == "9.3.0"
    assert support_packages.VERSION == "2.0.0"
    assert telemetry_packages.TELEMETRY_PROTOCOL == 9
    assert support_packages.TELEMETRY_PROTOCOL == 9
    speed_source = (SPEED_ROOT / "AiSpeed.csx").read_text(encoding="utf-8")
    telemetry_source = (TELEMETRY_ROOT / "AiTelemetry.csx").read_text(encoding="utf-8")
    assert "AI_SPEED_MOD|1|" in speed_source
    assert "DRTEL|9|" in telemetry_source
    assert "AI_MULTI_INSTANCE|1|" in telemetry_source


def test_support_runtime_validation_passed_all_current_chapters():
    report = json.loads(
        (SUPPORT_ROOT / "validation_2.0.0.json").read_text(encoding="utf-8")
    )
    assert report["result"] == "PASS"
    assert report["installed_game_modified"] is False
    assert report["package_sha256"] == (
        "aa6c7e23f77207c5bcf11e8c5701e96c414af222e73add6d70975c1e763de571"
    )
    assert [record["chapter"] for record in report["chapters"]] == [1, 2, 3, 4, 5]
    assert all(record["result"] == "PASS" for record in report["chapters"])
