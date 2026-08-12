from __future__ import annotations

import json
from pathlib import Path

import pytest

from mods.telemetry.tools import build_packages


def test_clean_hash_map_accepts_exact_selected_chapters(tmp_path: Path) -> None:
    path = tmp_path / "hashes.json"
    path.write_text(
        json.dumps({"2": "ab" * 32, "5": "cd" * 32}),
        encoding="utf-8",
    )
    assert build_packages._clean_hashes(path, (2, 5)) == {
        2: "ab" * 32,
        5: "cd" * 32,
    }


def test_clean_hash_map_rejects_partial_or_extra_chapters(tmp_path: Path) -> None:
    path = tmp_path / "hashes.json"
    path.write_text(json.dumps({"2": "ab" * 32}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="exactly the chapters"):
        build_packages._clean_hashes(path, (2, 3))


def test_target_version_is_required() -> None:
    parser = build_packages.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    assert parser.parse_args(["--target-version", "1.05"]).target_version == "1.05"


def test_telemetry_release_keeps_protocol_while_bumping_package() -> None:
    assert build_packages.VERSION == "9.2.1"
    assert build_packages.TELEMETRY_PROTOCOL == 9
    assert "Direct-CSX" in build_packages.DESCRIPTION
