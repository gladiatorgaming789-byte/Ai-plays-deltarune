from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from deltarune_agent.deltamod_package import DELTARUNE_CURRENT_HASHES
from mods.speed.tools import build_packages, build_payloads
from mods.speed.tools.release_config import (
    BUILD_INFO_FILENAME,
    SUPPORTED_CHAPTERS,
    SUPPORTED_GAME_BUILD,
    TARGET_VERSION,
    VERSION,
)


def test_archive_source_uses_data_bytes_not_stale_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    data = b"verified current chapter"
    checksum = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(
        build_payloads,
        "DELTARUNE_CURRENT_HASHES",
        {1: checksum},
    )
    archive_path = tmp_path / "Deltarune.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("chapter1_windows/data.win", data)
        archive.writestr("chapter1_windows/data.win.hash", "stale")

    sources, archive_hash = build_payloads._extract_archive_sources(
        archive_path,
        [1],
        tmp_path,
    )

    assert sources[1].read_bytes() == data
    assert archive_hash == hashlib.sha256(archive_path.read_bytes()).hexdigest()


def test_archive_source_rejects_unknown_game_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        build_payloads,
        "DELTARUNE_CURRENT_HASHES",
        {1: "0" * 64},
    )
    archive_path = tmp_path / "Deltarune.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("chapter1_windows/data.win", b"other build")

    with pytest.raises(RuntimeError, match="does not match"):
        build_payloads._extract_archive_sources(
            archive_path,
            [1],
            tmp_path,
        )


def test_package_builder_rejects_payloads_from_stale_source(tmp_path: Path):
    source = tmp_path / "AiSpeed.csx"
    source.write_text("current", encoding="utf-8")
    info = {
        "speed_mod_version": VERSION,
        "deltarune_target_version": TARGET_VERSION,
        "supported_game_build": SUPPORTED_GAME_BUILD,
        "g3mtool_version": "1.2.5",
        "script_sha256": hashlib.sha256(b"current").hexdigest(),
        "chapters": list(SUPPORTED_CHAPTERS),
        "clean_chapter_sha256": {
            str(chapter): checksum
            for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
        },
        "source": {"mode": "installed-files"},
    }
    (tmp_path / BUILD_INFO_FILENAME).write_text(
        json.dumps(info),
        encoding="utf-8",
    )

    assert build_packages._build_provenance(tmp_path, source) == info
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="script_sha256"):
        build_packages._build_provenance(tmp_path, source)

