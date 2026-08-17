from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from mods.tools.validate_joint_mod_merge import (
    AUTOSAVE_MARKER,
    SPEED_CODE,
    SPEED_MARKERS,
    TELEMETRY_CODE,
    TELEMETRY_DRAW_CODE,
    TELEMETRY_MARKER,
    code_texts,
    ensure_safe_work_directory,
    load_payload_set,
    markers_in_file,
    resource_identities,
    validate_merged_code,
    validate_payload_structure,
)


def _write_patch(
    path: Path,
    code: dict[str, bytes],
    *,
    original_md5: str = "a" * 32,
    asm_only: set[str] | None = None,
    empty_code: set[str] | None = None,
) -> None:
    asm_only = asm_only or set()
    empty_code = empty_code or set()
    changed = []
    members: dict[str, bytes] = {}
    for name, text in code.items():
        if name in empty_code:
            changed.append({"name": name, "files": {}})
            continue
        suffix = ".asm" if name in asm_only else ".gml"
        member = f"CodeEntries/{name}/{name}{suffix}"
        changed.append(
            {
                "name": name,
                "files": {f"{name}{suffix}": member},
            }
        )
        members[member] = text
    manifest = {
        "original": {"md5": original_md5},
        "resources": {
            "CodeEntries": {
                "changed": changed,
                "new": [],
                "deleted": [],
            }
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("g3mpatch.json", json.dumps(manifest))
        for member, contents in members.items():
            archive.writestr(member, contents)


def _speed_code() -> dict[str, bytes]:
    return {
        next(iter(SPEED_CODE)): b"\n".join(SPEED_MARKERS),
    }


def _telemetry_code() -> dict[str, bytes]:
    result = {name: TELEMETRY_MARKER for name in TELEMETRY_DRAW_CODE}
    result["gml_Object_obj_mainchara_Step_0"] = AUTOSAVE_MARKER
    for name in TELEMETRY_CODE - set(result):
        result[name] = b"AI_MULTI_INSTANCE|1| __ai_save_prefix"
    return result


def test_resource_identities_ignore_change_action() -> None:
    manifest = {
        "resources": {
            "CodeEntries": {
                "changed": [{"name": "code_a"}],
                "new": ["code_b"],
                "deleted": [],
            },
            "Sprites": {
                "changed": [],
                "new": [],
                "deleted": [{"name": "sprite_a"}],
            },
        }
    }
    assert resource_identities(manifest) == {
        ("CodeEntries", "code_a"),
        ("CodeEntries", "code_b"),
        ("Sprites", "sprite_a"),
    }


def test_payload_structure_accepts_disjoint_exact_code_sets(
    tmp_path: Path,
) -> None:
    speed = tmp_path / "speed.g3mpatch"
    telemetry = tmp_path / "telemetry.g3mpatch"
    _write_patch(speed, _speed_code())
    _write_patch(telemetry, _telemetry_code())
    speed_resources, telemetry_resources = validate_payload_structure(
        speed,
        telemetry,
        expected_md5="a" * 32,
    )
    assert speed_resources.isdisjoint(telemetry_resources)
    assert set(code_texts(speed)) == SPEED_CODE
    assert set(code_texts(telemetry)) == TELEMETRY_CODE


def test_payload_structure_rejects_resource_overlap(tmp_path: Path) -> None:
    speed = tmp_path / "speed.g3mpatch"
    telemetry = tmp_path / "telemetry.g3mpatch"
    _write_patch(speed, _speed_code())
    overlapping = _telemetry_code()
    overlapping.update(_speed_code())
    _write_patch(telemetry, overlapping)
    with pytest.raises(RuntimeError, match="resources overlap"):
        validate_payload_structure(
            speed,
            telemetry,
            expected_md5="a" * 32,
        )


def test_merged_code_rejects_telemetry_relocated_to_obj_time(
    tmp_path: Path,
) -> None:
    patch = tmp_path / "merged.g3mpatch"
    merged = _speed_code() | _telemetry_code()
    speed_name = next(iter(SPEED_CODE))
    merged[speed_name] += b"\nbbox_top\n" + TELEMETRY_MARKER
    _write_patch(patch, merged)
    with pytest.raises(RuntimeError, match="relocated into obj_time"):
        validate_merged_code(patch)


def test_merged_code_accepts_markers_in_intended_events(tmp_path: Path) -> None:
    patch = tmp_path / "merged.g3mpatch"
    _write_patch(patch, _speed_code() | _telemetry_code())
    result = validate_merged_code(patch)
    assert set(result["mod_code_entries"]) == SPEED_CODE | TELEMETRY_CODE
    assert result["additional_recompiled_code_entries"] == []


def test_merged_code_allows_unmarked_compiler_normalization(
    tmp_path: Path,
) -> None:
    patch = tmp_path / "merged.g3mpatch"
    normalized = _speed_code() | _telemetry_code()
    normalized["gml_GlobalScript_compiler_normalized"] = b"unrelated code"
    _write_patch(patch, normalized)
    result = validate_merged_code(patch)
    assert result["additional_recompiled_code_entries"] == [
        "gml_GlobalScript_compiler_normalized"
    ]


def test_merged_code_scans_asm_only_normalization_entries(
    tmp_path: Path,
) -> None:
    patch = tmp_path / "merged.g3mpatch"
    name = "gml_Object_original_code_with_no_decompile"
    normalized = _speed_code() | _telemetry_code() | {name: b"asm only"}
    _write_patch(patch, normalized, asm_only={name})
    result = validate_merged_code(patch)
    assert result["additional_recompiled_code_entries"] == [name]


def test_merged_code_rejects_marker_relocated_to_asm_only_entry(
    tmp_path: Path,
) -> None:
    patch = tmp_path / "merged.g3mpatch"
    name = "gml_Object_wrong_asm_owner"
    normalized = _speed_code() | _telemetry_code() | {name: TELEMETRY_MARKER}
    _write_patch(patch, normalized, asm_only={name})
    with pytest.raises(RuntimeError, match="markers were lost or relocated"):
        validate_merged_code(patch)


def test_merged_code_allows_unrelated_empty_g3mtool_record(
    tmp_path: Path,
) -> None:
    patch = tmp_path / "merged.g3mpatch"
    name = "gml_Object_original_code_g3mtool_could_not_decompile"
    code = _speed_code() | _telemetry_code() | {name: b""}
    _write_patch(patch, code, empty_code={name})
    result = validate_merged_code(patch)
    assert result["additional_recompiled_code_entries"] == [name]


def test_load_payload_set_extracts_all_chapters_from_zip(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        for chapter in range(1, 6):
            archive.writestr(
                f"Chapter{chapter}Speed.g3mpatch",
                f"speed-{chapter}",
            )
    destination = tmp_path / "inputs"
    destination.mkdir()
    selected = load_payload_set(
        "speed",
        package,
        tmp_path / "unused",
        destination,
    )
    assert selected.source_type == "DeltaMod ZIP"
    assert {
        chapter: path.read_text(encoding="utf-8")
        for chapter, path in selected.payloads.items()
    } == {chapter: f"speed-{chapter}" for chapter in range(1, 6)}


def test_work_directory_inside_game_install_is_rejected(tmp_path: Path) -> None:
    game = tmp_path / "DELTARUNE"
    game.mkdir()
    with pytest.raises(RuntimeError, match="refuses to write"):
        ensure_safe_work_directory(game / "validation", game)
    ensure_safe_work_directory(tmp_path / "safe-validation", game)


def test_markers_in_file_scans_without_loading_output_copy(
    tmp_path: Path,
) -> None:
    output = tmp_path / "merged.win"
    output.write_bytes(b"prefix-AI_SPEED_MOD|1|-middle-DRTEL|9|-suffix")
    assert markers_in_file(
        output,
        (b"AI_SPEED_MOD|1|", b"DRTEL|9|", b"not-present"),
    ) == {
        "AI_SPEED_MOD|1|": True,
        "DRTEL|9|": True,
        "not-present": False,
    }
