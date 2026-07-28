from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from deltarune_agent.deltamod_package import (
    DELTARUNE_105_HASHES,
    validate_package,
)


ROOT = Path(__file__).resolve().parents[1]
SPEED_ROOT = ROOT / "mods" / "speed"
SPEED_DIRECTORY = SPEED_ROOT / "deltamod"
TELEMETRY_DIRECTORY = ROOT / "mods" / "telemetry" / "deltamod"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_built_speed_release_records_match_every_archive():
    release = json.loads(
        (SPEED_ROOT / "release_1.1.0.json").read_text(encoding="utf-8")
    )
    assert release["clean_chapter_sha256"] == {
        str(chapter): checksum
        for chapter, checksum in DELTARUNE_105_HASHES.items()
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


def test_telemetry_902_is_metadata_only_and_has_a_distinct_id():
    release = json.loads(
        (TELEMETRY_DIRECTORY / "release_9.0.2.json").read_text(
            encoding="utf-8"
        )
    )
    current = TELEMETRY_DIRECTORY / release["file"]
    previous = TELEMETRY_DIRECTORY / release["payload_source"]
    assert current.is_file() and previous.is_file()
    assert _sha256(current) == release["sha256"]
    assert release["payloads_unchanged"] is True
    assert release["package_id"] == "github.ai-telemetry.gladiatorgaming789-byte"
    assert release["package_id"] != "github.ai-speed.gladiatorgaming789-byte"
    validate_package(current)

    with zipfile.ZipFile(current) as current_zip, zipfile.ZipFile(
        previous
    ) as previous_zip:
        metadata = json.loads(current_zip.read("meta.json"))
        assert metadata["metadata"]["mergeSupport"] is True
        assert {
            item["file"]: item["checksum"]
            for item in metadata["neededFiles"]
        } == {
            f"./chapter{chapter}_windows/data.win": checksum
            for chapter, checksum in DELTARUNE_105_HASHES.items()
        }
        for chapter in range(1, 6):
            name = f"Chapter{chapter}DataPatch.xdelta"
            assert current_zip.read(name) == previous_zip.read(name)
