from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from deltarune_agent.deltamod_package import (
    DELTARUNE_CURRENT_HASHES,
    DELTARUNE_CURRENT_MD5,
    DELTARUNE_STEAM_BUILD_ID,
    validate_package,
)
from mods.telemetry.tools import build_packages
from mods.telemetry.tools.build_payloads import EXPECTED_CODE


TELEMETRY_ROOT = Path(__file__).resolve().parents[1]
DELTAMOD_ROOT = TELEMETRY_ROOT / "deltamod"
RELEASE_PATH = TELEMETRY_ROOT / "release_9.1.0.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_current_release_is_root_only_semantic_code_patch() -> None:
    release = json.loads(RELEASE_PATH.read_text(encoding="utf-8"))
    package_record = release["package"]
    package = DELTAMOD_ROOT / package_record["file"]

    assert release["steam_build_id"] == DELTARUNE_STEAM_BUILD_ID
    assert release["telemetry_mod_version"] == "9.1.0"
    assert release["telemetry_protocol"] == 9
    assert release["minimum_g3mtool_version_for_speed_merge"] == "1.2.5"
    assert release["clean_chapter_sha256"] == {
        str(chapter): checksum
        for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
    }
    assert package.stat().st_size == package_record["size"]
    assert _sha256(package) == package_record["sha256"]
    validate_package(package)

    with zipfile.ZipFile(package) as archive:
        assert archive.namelist() == package_record["root_entries"]
        metadata = json.loads(archive.read("meta.json"))
        assert metadata["metadata"]["packageID"] == (
            "github.ai-telemetry.gladiatorgaming789-byte"
        )
        assert metadata["metadata"]["mergeSupport"] is True
        assert {
            item["file"]: item["checksum"]
            for item in metadata["neededFiles"]
        } == {
            f"./chapter{chapter}_windows/data.win": checksum
            for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
        }

        modding = archive.read("modding.xml").decode("utf-8")
        assert modding.count('type="g3mpatch"') == 5
        assert 'type="xdelta"' not in modding
        for chapter in range(1, 6):
            payload_name = f"Chapter{chapter}Telemetry.g3mpatch"
            payload = archive.read(payload_name)
            expected = release["chapter_payloads"][str(chapter)]
            assert len(payload) == expected["size"]
            assert hashlib.sha256(payload).hexdigest() == expected["sha256"]
            with zipfile.ZipFile(io.BytesIO(payload)) as patch:
                manifest = json.loads(patch.read("g3mpatch.json"))
                assert set(manifest["resources"]) == {"CodeEntries"}
                code = manifest["resources"]["CodeEntries"]
                assert {item["name"] for item in code["changed"]} == set(
                    EXPECTED_CODE
                )
                assert code["new"] == []
                assert code["deleted"] == []
                assert manifest["original"]["md5"] == (
                    DELTARUNE_CURRENT_MD5[chapter]
                )


def test_release_directory_has_no_stale_or_loose_payloads() -> None:
    files = sorted(path for path in DELTAMOD_ROOT.iterdir() if path.is_file())
    assert [path.name for path in files] == [
        "README.md",
        "Telemetry-All-Chapters-DeltaMod-v9.1.0.zip",
    ]
    assert not list(DELTAMOD_ROOT.glob("*.g3mpatch"))


def test_package_builder_rejects_payload_tampering(tmp_path: Path) -> None:
    telemetry_root = tmp_path / "telemetry"
    payload_directory = telemetry_root / ".build" / "payloads"
    payload_directory.mkdir(parents=True)
    source = telemetry_root / "AiTelemetry.csx"
    source.write_text("telemetry source", encoding="utf-8")

    payloads: dict[int, Path] = {}
    chapters: list[dict[str, object]] = []
    for chapter in range(1, 6):
        payload = payload_directory / f"Chapter{chapter}Telemetry.g3mpatch"
        payload.write_bytes(f"payload-{chapter}".encode("ascii"))
        payloads[chapter] = payload
        chapters.append(
            {
                "chapter": chapter,
                "clean_sha256": DELTARUNE_CURRENT_HASHES[chapter],
                "clean_md5": DELTARUNE_CURRENT_MD5[chapter],
                "payload_size": payload.stat().st_size,
                "payload_sha256": _sha256(payload),
            }
        )
    provenance = {
        "record_version": 1,
        "steam_build_id": DELTARUNE_STEAM_BUILD_ID,
        "telemetry_protocol": 9,
        "manual_source": source.name,
        "manual_source_sha256": _sha256(source),
        "g3mtool_version": "1.2.5",
        "chapters": chapters,
    }
    (payload_directory.parent / "payloads.json").write_text(
        json.dumps(provenance),
        encoding="utf-8",
    )

    assert build_packages._validated_provenance(
        telemetry_root,
        payload_directory,
        payloads,
    ) == provenance

    source.write_text("changed source", encoding="utf-8")
    with pytest.raises(RuntimeError, match="AiTelemetry.csx changed"):
        build_packages._validated_provenance(
            telemetry_root,
            payload_directory,
            payloads,
        )
    source.write_text("telemetry source", encoding="utf-8")

    payloads[3].write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="Chapter 3 payload"):
        build_packages._validated_provenance(
            telemetry_root,
            payload_directory,
            payloads,
        )
