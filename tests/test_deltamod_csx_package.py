from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
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


def _build_from_source(source: Path, output: Path) -> Path:
    return build_csx_package(
        script=source,
        chapters=[1, 2],
        output=output,
        target_version="1.05",
        payload_label="Example",
        name="Example CSX",
        version="1.0.0",
        description="Test source patch",
        authors=["tester"],
        url="https://example.invalid",
        package_id="example.csx.tests",
    )


def test_builder_writes_one_source_payload_per_chapter(tmp_path: Path) -> None:
    output = _build(tmp_path)
    result = validate_csx_package(output, expected_chapters=(1, 2))
    assert result["chapters"] == [1, 2]
    assert result["merge_support"] is True
    assert result["patch_type"] == "csx"
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
            '<patch type="csx" patch="./Chapter1Example.csx" '
            'to="./chapter1_windows/data.win"/>',
            '<patch type="csx" patch="./Chapter2Example.csx" '
            'to="./chapter2_windows/data.win"/>',
        ]
        metadata = json.loads(archive.read("meta.json"))
        assert "neededFiles" not in metadata


def test_validator_rejects_raw_csx_declared_as_xdelta(tmp_path: Path) -> None:
    output = _build(tmp_path)
    broken = tmp_path / "broken-xdelta-routing.zip"
    with zipfile.ZipFile(output) as source, zipfile.ZipFile(broken, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name == "modding.xml":
                payload = payload.replace(b'type="csx"', b'type="xdelta"')
            target.writestr(name, payload)

    with pytest.raises(ValueError, match="dedicated csx-type"):
        validate_csx_package(broken)


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


def test_builder_is_identical_for_lf_and_crlf_checkouts(tmp_path: Path) -> None:
    lf_source = tmp_path / "lf" / "AiExample.csx"
    crlf_source = tmp_path / "crlf" / "AiExample.csx"
    lf_source.parent.mkdir()
    crlf_source.parent.mkdir()
    lf_source.write_bytes(VALID_CSX.encode("utf-8"))
    crlf_source.write_bytes(VALID_CSX.replace("\n", "\r\n").encode("utf-8"))

    lf_package = _build_from_source(lf_source, tmp_path / "lf.zip")
    crlf_package = _build_from_source(crlf_source, tmp_path / "crlf.zip")

    assert lf_package.read_bytes() == crlf_package.read_bytes()
    with zipfile.ZipFile(lf_package) as archive:
        assert {item.date_time for item in archive.infolist()} == {
            (1980, 1, 1, 0, 0, 0)
        }
        for name in archive.namelist():
            if name.endswith(".csx"):
                assert b"\r\n" not in archive.read(name)


def test_builder_repeated_runs_are_byte_identical(tmp_path: Path) -> None:
    source = _script(tmp_path / "AiExample.csx")
    first = _build_from_source(source, tmp_path / "first.zip")
    first_bytes = first.read_bytes()
    second = _build_from_source(source, tmp_path / "second.zip")
    assert second.read_bytes() == first_bytes


@pytest.mark.parametrize(
    "builder_relative",
    [
        "mods/speed/tools/build_packages.py",
        "mods/telemetry/tools/build_packages.py",
    ],
)
def test_mod_builders_run_without_site_packages(
    tmp_path: Path,
    builder_relative: str,
) -> None:
    """Package materialization must not depend on Pillow, Qt, or other runtime deps."""

    repository_root = Path(__file__).resolve().parents[1]
    clean_hashes = tmp_path / "clean_hashes.json"
    clean_hashes.write_text(
        json.dumps({"1": "00" * 32}),
        encoding="utf-8",
    )
    output = tmp_path / f"{Path(builder_relative).parents[1].name}.zip"
    manifest = tmp_path / f"{Path(builder_relative).parents[1].name}.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(repository_root / builder_relative),
            "--target-version",
            "1.05",
            "--chapter",
            "1",
            "--clean-hashes",
            str(clean_hashes),
            "--output",
            str(output),
            "--manifest",
            str(manifest),
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert output.is_file()
    assert manifest.is_file()


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
