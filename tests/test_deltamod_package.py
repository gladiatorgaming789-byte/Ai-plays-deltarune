from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from deltarune_agent.deltamod_package import (
    DELTARUNE_105_HASHES,
    DELTARUNE_105_MD5,
    PatchSpec,
    VCDIFF_MAGIC,
    build_package,
    validate_package,
)


def _patch(path: Path, payload: bytes = b"payload") -> Path:
    path.write_bytes(VCDIFF_MAGIC + payload)
    return path


def test_builder_matches_deltamod_root_layout(tmp_path: Path):
    source = _patch(tmp_path / "chapter5.xdelta")
    output = build_package(
        patches=[PatchSpec(5, source)],
        output=tmp_path / "telemetry.zip",
        target_version="1.05",
    )

    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "Chapter5DataPatch.xdelta",
            "meta.json",
            "modding.xml",
        }
        metadata = json.loads(archive.read("meta.json"))
        assert metadata["metadata"]["game"] == "toby.deltarune"
        assert metadata["metadata"]["mergeSupport"] is True
        assert metadata["metadata"]["packageID"].count(".") == 2
        assert metadata["deltaruneTargetVersion"] == "1.05"
        assert metadata["neededFiles"] == [
            {
                "file": "./chapter5_windows/data.win",
                "checksum": DELTARUNE_105_HASHES[5],
            }
        ]
        assert archive.read("modding.xml").decode("utf-8") == (
            '<patch type="xdelta" patch="./Chapter5DataPatch.xdelta" '
            'to="./chapter5_windows/data.win"/>\n'
        )


def test_builder_sorts_multiple_chapter_patches(tmp_path: Path):
    chapter5 = _patch(tmp_path / "five.xdelta", b"five")
    chapter1 = _patch(tmp_path / "one.xdelta", b"one")
    output = build_package(
        patches=[PatchSpec(5, chapter5), PatchSpec(1, chapter1)],
        output=tmp_path / "telemetry.zip",
        target_version="1.05",
    )

    with zipfile.ZipFile(output) as archive:
        assert archive.read("modding.xml").decode("utf-8").splitlines() == [
            '<patch type="xdelta" patch="./Chapter1DataPatch.xdelta" '
            'to="./chapter1_windows/data.win"/>',
            '<patch type="xdelta" patch="./Chapter5DataPatch.xdelta" '
            'to="./chapter5_windows/data.win"/>',
        ]


def test_builder_rejects_non_vcdiff_files(tmp_path: Path):
    source = tmp_path / "renamed.xdelta"
    source.write_bytes(b"not an xdelta patch")

    with pytest.raises(ValueError, match="not a VCDIFF"):
        build_package(
            patches=[PatchSpec(1, source)],
            output=tmp_path / "telemetry.zip",
            target_version="1.05",
        )


def test_builder_rejects_duplicate_chapters(tmp_path: Path):
    first = _patch(tmp_path / "first.xdelta", b"first")
    second = _patch(tmp_path / "second.xdelta", b"second")

    with pytest.raises(ValueError, match="supplied more than once"):
        build_package(
            patches=[PatchSpec(2, first), PatchSpec(2, second)],
            output=tmp_path / "telemetry.zip",
            target_version="1.05",
        )


def test_failed_build_preserves_existing_output(tmp_path: Path):
    output = tmp_path / "telemetry.zip"
    output.write_bytes(b"existing output")
    invalid = tmp_path / "invalid.xdelta"
    invalid.write_bytes(b"invalid")

    with pytest.raises(ValueError):
        build_package(
            patches=[PatchSpec(3, invalid)],
            output=output,
            target_version="1.05",
        )

    assert output.read_bytes() == b"existing output"


def test_validator_rejects_nested_package_files(tmp_path: Path):
    package = tmp_path / "nested.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Telemetry/meta.json", "{}")
        archive.writestr("Telemetry/modding.xml", "")

    with pytest.raises(ValueError, match="ZIP root"):
        validate_package(package)


def _g3mpatch(
    path: Path,
    *,
    original_md5: str = DELTARUNE_105_MD5[5],
) -> Path:
    code_path = "CodeEntries/example/example.gml"
    manifest = {
        "createdAt": "2026-07-27T00:00:00Z",
        "tool": {"name": "G3MTool", "version": "1.2.1"},
        "original": {"size": 100, "md5": original_md5},
        "modified": {"size": 101, "md5": "02" * 16},
        "resources": {
            "CodeEntries": {
                "changed": [
                    {
                        "name": "example",
                        "files": {"example.gml": code_path},
                    }
                ],
                "new": [],
                "deleted": [],
            }
        },
        "statistics": {
            "totalChanged": 1,
            "totalNew": 0,
            "totalDeleted": 0,
            "totalChangedFiles": 1,
            "totalNewFiles": 0,
        },
        "applyPlan": {
            "mode": "standard",
            "requiresCodePipeline": True,
            "requiresTexturePipeline": False,
            "requiresAssetReorder": False,
            "requiresHeavyFinalize": True,
            "supportsDirectResourceApply": False,
            "simpleResourceTypes": [],
            "heavyResourceTypes": ["CodeEntries"],
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("g3mpatch.json", json.dumps(manifest))
        archive.writestr(code_path, "show_debug_message(\"test\");")
    return path


def test_builder_packages_g3mpatch_with_matching_patch_type(tmp_path: Path):
    source = _g3mpatch(tmp_path / "Chapter5Speed.g3mpatch")
    output = build_package(
        patches=[
            PatchSpec(
                5,
                source,
                archive_name_override="Chapter5Speed.g3mpatch",
            )
        ],
        output=tmp_path / "speed.zip",
        target_version="1.05",
        package_id="github.ai-speed.gladiatorgaming789-byte",
    )

    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "Chapter5Speed.g3mpatch",
            "meta.json",
            "modding.xml",
        }
        assert archive.read("modding.xml").decode("utf-8") == (
            '<patch type="g3mpatch" '
            'patch="./Chapter5Speed.g3mpatch" '
            'to="./chapter5_windows/data.win"/>\n'
        )


def test_builder_rejects_invalid_package_id(tmp_path: Path):
    source = _patch(tmp_path / "chapter1.xdelta")

    with pytest.raises(ValueError, match="exactly three"):
        build_package(
            patches=[PatchSpec(1, source)],
            output=tmp_path / "bad.zip",
            target_version="1.05",
            package_id="github.too.many.parts",
        )


def test_builder_rejects_raw_csx_as_a_deltamod_merge_payload(tmp_path: Path):
    source = tmp_path / "AiSpeed.csx"
    source.write_text(
        "EnsureDataLoaded();\n"
        "var imports = new CodeImportGroup(Data);\n"
        "imports.QueueAppend(code, \"x\");\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manual-install only"):
        build_package(
            patches=[PatchSpec(1, source)],
            output=tmp_path / "bad.zip",
            target_version="1.05",
        )


def test_builder_rejects_g3mpatch_without_a_zip_directory(tmp_path: Path):
    source = tmp_path / "broken.g3mpatch"
    source.write_bytes(b"this is not a zip archive")

    with pytest.raises(ValueError, match="valid G3M patch ZIP archive"):
        build_package(
            patches=[PatchSpec(1, source)],
            output=tmp_path / "bad.zip",
            target_version="1.05",
        )


def test_builder_rejects_g3mpatch_for_the_wrong_chapter(tmp_path: Path):
    source = _g3mpatch(
        tmp_path / "Chapter1Speed.g3mpatch",
        original_md5=DELTARUNE_105_MD5[1],
    )

    with pytest.raises(ValueError, match="different clean data.win"):
        build_package(
            patches=[PatchSpec(2, source)],
            output=tmp_path / "bad.zip",
            target_version="1.05",
        )


def test_explicit_clean_source_hash_is_written_to_needed_files(tmp_path: Path):
    source = _patch(tmp_path / "custom.xdelta")
    clean_hash = "ab" * 32
    output = build_package(
        patches=[PatchSpec(2, source, clean_hash)],
        output=tmp_path / "custom.zip",
        target_version="1.05",
    )

    with zipfile.ZipFile(output) as archive:
        needed = json.loads(archive.read("meta.json"))["neededFiles"]
    assert needed == [
        {"file": "./chapter2_windows/data.win", "checksum": clean_hash}
    ]


def test_builder_can_mark_an_atomic_package_nonmergeable(tmp_path: Path):
    source = _patch(tmp_path / "combined.xdelta")
    output = build_package(
        patches=[PatchSpec(1, source)],
        output=tmp_path / "combined.zip",
        target_version="1.05",
        package_id="github.combined-test.example",
        merge_support=False,
    )

    validate_package(output)
    with zipfile.ZipFile(output) as archive:
        metadata = json.loads(archive.read("meta.json"))
    assert metadata["metadata"]["mergeSupport"] is False
