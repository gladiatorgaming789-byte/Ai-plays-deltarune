from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from deltarune_agent.deltamod_csx_package import (
    build_csx_package,
    validate_csx_bytes,
    validate_csx_package,
)


VALID_CSX = """using UndertaleModLib.Compiler;
EnsureDataLoaded();
var imports = new CodeImportGroup(Data);
imports.QueueAppend(Data.Code.ByName(\"example\"), \"show_debug_message(1);\");
imports.Import();
"""


def _script(path: Path, text: str = VALID_CSX) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _build(
    tmp_path: Path,
    *,
    chapters: list[int] | None = None,
    clean_hashes: dict[int, str] | None = None,
) -> Path:
    return build_csx_package(
        script=_script(tmp_path / "AiExample.csx"),
        chapters=chapters or [2, 1],
        output=tmp_path / "example.zip",
        target_version="1.05",
        payload_label="Example",
        name="Example CSX",
        version="1.0.0",
        description="Test source patch",
        authors=["tester"],
        url="https://example.invalid",
        package_id="example.csx.tests",
        clean_hashes=clean_hashes,
    )


def test_builder_writes_one_source_payload_per_chapter(tmp_path: Path) -> None:
    output = _build(tmp_path)
    result = validate_csx_package(output, expected_chapters=(1, 2))
    assert result["chapters"] == [1, 2]
    assert result["merge_support"] is True
    assert result["has_needed_files"] is False

    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "Chapter1Example.csx",
            "Chapter2Example.csx",
            "meta.json",
            "modding.xml",
        }
        assert archive.read("Chapter1Example.csx") == archive.read(
            "Chapter2Example.csx"
        )
        assert archive.read("modding.xml").decode("utf-8").splitlines() == [
            '<patch type="xdelta" patch="./Chapter1Example.csx" '
            'to="./chapter1_windows/data.win"/>',
            '<patch type="xdelta" patch="./Chapter2Example.csx" '
            'to="./chapter2_windows/data.win"/>',
        ]
        metadata = json.loads(archive.read("meta.json"))
        assert "neededFiles" not in metadata


def test_builder_can_pin_fresh_clean_data_hashes(tmp_path: Path) -> None:
    hashes = {1: "ab" * 32, 2: "cd" * 32}
    output = _build(tmp_path, clean_hashes=hashes)
    result = validate_csx_package(output, expected_chapters=(1, 2))
    assert result["has_needed_files"] is True
    with zipfile.ZipFile(output) as archive:
        metadata = json.loads(archive.read("meta.json"))
    assert metadata["neededFiles"] == [
        {
            "file": "./chapter1_windows/data.win",
            "checksum": hashes[1],
        },
        {
            "file": "./chapter2_windows/data.win",
            "checksum": hashes[2],
        },
    ]


def test_builder_rejects_duplicate_chapters(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="supplied more than once"):
        _build(tmp_path, chapters=[1, 1])


def test_builder_rejects_stale_or_partial_hash_sets(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly the chapters"):
        _build(tmp_path, clean_hashes={1: "ab" * 32})


def test_csx_validation_rejects_empty_binary_and_non_installer_source() -> None:
    with pytest.raises(ValueError, match="empty"):
        validate_csx_bytes(b"", label="empty.csx")
    with pytest.raises(ValueError, match="binary NUL"):
        validate_csx_bytes(b"EnsureDataLoaded();\x00", label="binary.csx")
    with pytest.raises(ValueError, match="safe installer structure"):
        validate_csx_bytes(b"EnsureDataLoaded();", label="incomplete.csx")


def test_validator_rejects_different_per_chapter_sources(tmp_path: Path) -> None:
    output = _build(tmp_path)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name == "Chapter2Example.csx":
                payload += b"\n// changed"
            target.writestr(name, payload)
    with pytest.raises(ValueError, match="identical source"):
        validate_csx_package(tampered)
