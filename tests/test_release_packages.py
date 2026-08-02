from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from deltarune_agent.deltamod_package import (
    DELTARUNE_CURRENT_HASHES,
    validate_package,
)


ROOT = Path(__file__).resolve().parents[1]
SPEED_ROOT = ROOT / "mods" / "speed"
SPEED_DIRECTORY = SPEED_ROOT / "deltamod"
TELEMETRY_ROOT = ROOT / "mods" / "telemetry"
TELEMETRY_DIRECTORY = TELEMETRY_ROOT / "deltamod"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_built_speed_release_records_match_every_archive():
    release = json.loads(
        (SPEED_ROOT / "release_1.2.0.json").read_text(encoding="utf-8")
    )
    assert release["clean_chapter_sha256"] == {
        str(chapter): checksum
        for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
    }
    assert release["default_multiplier"] == 2
    assert release["supported_multipliers"] == list(range(1, 11))
    assert release["format"] == (
        "DeltaMod ZIP-only release with ignored G3MTool intermediates"
    )
    assert release["minimum_g3mtool_version_for_multi_code_merge"] == "1.2.5"
    assert release["merge_support"] is True
    assert set(release["chapter_payloads"]) == {
        str(chapter) for chapter in range(1, 6)
    }

    for record in release["packages"]:
        package = SPEED_DIRECTORY / record["file"]
        assert package.is_file()
        assert package.stat().st_size == record["size"]
        assert _sha256(package) == record["sha256"]
        validate_package(package)
        with zipfile.ZipFile(package) as archive:
            assert archive.namelist() == record["root_entries"]
            metadata = json.loads(archive.read("meta.json"))
            assert metadata["metadata"]["mergeSupport"] is True
            payloads = [
                name
                for name in archive.namelist()
                if name.endswith(".g3mpatch")
            ]
            assert len(payloads) == len(record["chapters"])
            assert not any(name.endswith(".csx") for name in archive.namelist())
            modding = archive.read("modding.xml").decode("utf-8")
            assert modding.count('type="g3mpatch"') == len(payloads)
            for chapter in record["chapters"]:
                expected = release["chapter_payloads"][str(chapter)]
                payload_name = f"Chapter{chapter}Speed.g3mpatch"
                assert payload_name in payloads
                assert len(archive.read(payload_name)) == expected["size"]
                assert (
                    hashlib.sha256(archive.read(payload_name)).hexdigest()
                    == expected["sha256"]
                )


def test_speed_deltamod_directory_contains_only_release_zips():
    entries = sorted(SPEED_DIRECTORY.iterdir())
    files = [entry for entry in entries if entry.is_file()]

    assert len(files) == 6
    assert all(entry.suffix.casefold() == ".zip" for entry in files)
    assert not list(SPEED_DIRECTORY.glob("*.g3mpatch"))

    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "mods/speed/.build/" in ignore
    assert "mods/speed/deltamod/*.g3mpatch" in ignore
    assert "*.g3mpatch binary" in attributes


def test_current_telemetry_release_is_rebuilt_and_has_a_distinct_id():
    release = json.loads(
        (TELEMETRY_ROOT / "release_9.1.0.json").read_text(encoding="utf-8")
    )
    record = release["package"]
    package = TELEMETRY_DIRECTORY / record["file"]
    assert package.is_file()
    assert package.stat().st_size == record["size"]
    assert _sha256(package) == record["sha256"]
    assert release["telemetry_mod_version"] == "9.1.0"
    assert release["telemetry_protocol"] == 9
    assert release["steam_build_id"] == "24484059"
    assert release["clean_chapter_sha256"] == {
        str(chapter): checksum
        for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
    }
    assert record["package_id"] == "github.ai-telemetry.gladiatorgaming789-byte"
    assert record["package_id"] != "github.ai-speed.gladiatorgaming789-byte"
    validate_package(package)

    with zipfile.ZipFile(package) as archive:
        assert archive.namelist() == record["root_entries"]
        metadata = json.loads(archive.read("meta.json"))
        assert metadata["metadata"]["mergeSupport"] is True
        assert {
            item["file"]: item["checksum"]
            for item in metadata["neededFiles"]
        } == {
            f"./chapter{chapter}_windows/data.win": checksum
            for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
        }
        payloads = [
            name for name in archive.namelist() if name.endswith(".g3mpatch")
        ]
        assert len(payloads) == 5
        assert not any(
            name.endswith((".csx", ".xdelta", ".vcdiff"))
            for name in archive.namelist()
        )
        for chapter in range(1, 6):
            name = f"Chapter{chapter}Telemetry.g3mpatch"
            expected = release["chapter_payloads"][str(chapter)]
            assert name in payloads
            assert len(archive.read(name)) == expected["size"]
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected["sha256"]


def test_telemetry_deltamod_directory_contains_only_current_release_files():
    files = sorted(path for path in TELEMETRY_DIRECTORY.iterdir() if path.is_file())
    archives = [path for path in files if path.suffix.casefold() == ".zip"]

    assert [path.name for path in archives] == [
        "Telemetry-All-Chapters-DeltaMod-v9.1.0.zip"
    ]
    assert not list(TELEMETRY_DIRECTORY.glob("*.g3mpatch"))
