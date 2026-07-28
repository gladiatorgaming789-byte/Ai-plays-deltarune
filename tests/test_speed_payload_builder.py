from __future__ import annotations

import json
from pathlib import Path
import zipfile

import pytest

from mods.speed.deltamod.build_payloads import (
    _minimize_patch,
    _require_supported_g3mtool,
)


CODE_ROOT = "CodeEntries/gml_Object_obj_time_Step_1"
GML_PATH = f"{CODE_ROOT}/gml_Object_obj_time_Step_1.gml"
ASM_PATH = f"{CODE_ROOT}/gml_Object_obj_time_Step_1.asm"


def _raw_patch(path: Path, *, extra_type: str | None = None) -> Path:
    resources: dict[str, object] = {
        "CodeEntries": {
            "changed": [
                {
                    "name": "gml_Object_obj_time_Step_1",
                    "files": {
                        "gml_Object_obj_time_Step_1.gml": GML_PATH,
                        "gml_Object_obj_time_Step_1.asm": ASM_PATH,
                    },
                }
            ],
            "new": [],
            "deleted": [],
        },
        "Sounds": {
            "changed": [
                {
                    "name": "normalization_only",
                    "files": {
                        "sound.json": "Sounds/normalization_only/sound.json"
                    },
                }
            ],
            "new": [],
            "deleted": [],
        },
    }
    if extra_type is not None:
        resources[extra_type] = {
            "changed": [],
            "new": [{"name": "unexpected", "files": {}}],
            "deleted": [],
        }
    manifest = {
        "resources": resources,
        "statistics": {},
        "applyPlan": {},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("g3mpatch.json", json.dumps(manifest))
        archive.writestr(
            GML_PATH,
            'global.marker = "AI_SPEED_MOD|1|";\n'
            'global.packet = "DRSPEED|1|multiplier=";\n',
        )
        archive.writestr(ASM_PATH, ":[0]\npush.v self.marker\n")
        archive.writestr("Helpers/object_events.json", "{}")
        archive.writestr("Helpers/variables_functions.json", "{}")
        archive.writestr(
            "Sounds/normalization_only/sound.json",
            "{}",
        )
    return path


def test_minimized_speed_patch_keeps_only_the_intended_code(tmp_path: Path):
    source = _raw_patch(tmp_path / "raw.g3mpatch")
    output = tmp_path / "minimal.g3mpatch"

    _minimize_patch(source, output)

    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("g3mpatch.json"))
        assert set(manifest["resources"]) == {"CodeEntries"}
        assert manifest["statistics"]["totalChanged"] == 1
        assert manifest["applyPlan"]["heavyResourceTypes"] == ["CodeEntries"]
        assert set(archive.namelist()) == {
            "g3mpatch.json",
            GML_PATH,
            ASM_PATH,
            "Helpers/object_events.json",
            "Helpers/variables_functions.json",
        }


def test_minimized_speed_patch_rejects_unexpected_resource_changes(
    tmp_path: Path,
):
    source = _raw_patch(
        tmp_path / "raw.g3mpatch",
        extra_type="Sprites",
    )

    with pytest.raises(RuntimeError, match="unexpectedly changed"):
        _minimize_patch(source, tmp_path / "minimal.g3mpatch")


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("1.2.5", (1, 2, 5)),
        ("G3MTool 1.3.0+release", (1, 3, 0)),
    ],
)
def test_payload_builder_accepts_merge_safe_g3mtool(
    monkeypatch: pytest.MonkeyPatch,
    reported: str,
    expected: tuple[int, int, int],
):
    class Result:
        returncode = 0
        stdout = reported
        stderr = ""

    monkeypatch.setattr(
        "mods.speed.deltamod.build_payloads.subprocess.run",
        lambda *args, **kwargs: Result(),
    )

    assert _require_supported_g3mtool(Path("G3MTool.exe")) == expected


def test_payload_builder_rejects_known_broken_g3mtool(
    monkeypatch: pytest.MonkeyPatch,
):
    class Result:
        returncode = 0
        stdout = "1.2.1"
        stderr = ""

    monkeypatch.setattr(
        "mods.speed.deltamod.build_payloads.subprocess.run",
        lambda *args, **kwargs: Result(),
    )

    with pytest.raises(RuntimeError, match="unsafe.*1.2.5"):
        _require_supported_g3mtool(Path("G3MTool-win32.exe"))
