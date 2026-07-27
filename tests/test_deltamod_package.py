from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from deltarune_agent.deltamod_package import (
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
        assert metadata["deltaruneTargetVersion"] == "1.05"
        assert metadata["neededFiles"] == [
            {
                "file": "./Chapter5DataPatch.xdelta",
                "checksum": hashlib.sha256(source.read_bytes()).hexdigest(),
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
