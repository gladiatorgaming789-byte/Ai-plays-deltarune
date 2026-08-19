"""Compile the combined AI Support CSX against clean Chapters 1-5.

This never writes inside the game installation. G3MTool works in a temporary
directory, then the validator decompiles the resulting patch to prove that all
speed, telemetry, isolated-save, and training-only autosave hooks stayed in
their intended resources.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deltarune_agent.deltamod_csx_package import validate_csx_package
from deltarune_agent.deltamod_package import DELTARUNE_CURRENT_HASHES
from mods.tools.validate_joint_mod_merge import (
    SPEED_MARKERS,
    TELEMETRY_MARKER,
    AUTOSAVE_MARKER,
    require_g3mtool,
    run_command,
    sha256_file,
    validate_merged_code,
)


def _parser() -> argparse.ArgumentParser:
    local = Path.home() / "AppData" / "Local"
    game = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    game /= "Steam/steamapps/common/DELTARUNE"
    parser = argparse.ArgumentParser(
        description="Validate the combined AI Support 2.0.1 CSX on clean Chapters 1-5."
    )
    parser.add_argument(
        "--package",
        type=Path,
        default=ROOT / "mods/support/deltamod/AI-Support-All-Chapters-DeltaMod-CSX-v2.0.1.zip",
    )
    parser.add_argument("--game-directory", type=Path, default=game)
    parser.add_argument(
        "--g3mtool",
        type=Path,
        default=local / "deltamod/win-unpacked/resources/app/tools/G3MTool-win32.exe",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "mods/support/validation_2.0.1.json",
    )
    parser.add_argument(
        "--chapter",
        action="append",
        type=int,
        choices=range(1, 6),
        dest="chapters",
        help="chapter to validate; repeat as needed (default: Chapters 1-5)",
    )
    return parser


def _clean_source(game: Path, chapter: int) -> Path:
    directory = game / f"chapter{chapter}_windows"
    for name in ("data.win.bak", "data.win"):
        path = directory / name
        if path.is_file() and sha256_file(path) == DELTARUNE_CURRENT_HASHES[chapter]:
            return path
    raise FileNotFoundError(f"No verified clean Chapter {chapter} data.win was found")


def main() -> int:
    args = _parser().parse_args()
    package = args.package.expanduser().resolve()
    game = args.game_directory.expanduser().resolve()
    g3mtool = args.g3mtool.expanduser().resolve()
    validation = validate_csx_package(package, expected_chapters=range(1, 6))
    tool_version = require_g3mtool(g3mtool)
    package_hash = sha256_file(package)
    chapters = sorted(set(args.chapters or range(1, 6)))
    records: list[dict[str, object]] = []

    with tempfile.TemporaryDirectory(prefix="ai-support-runtime-validation-") as raw:
        work = Path(raw)
        with zipfile.ZipFile(package) as archive:
            for chapter in chapters:
                clean = _clean_source(game, chapter)
                clean_hash = sha256_file(clean)
                script = work / f"Chapter{chapter}Support.csx"
                with archive.open(script.name) as source, script.open("wb") as target:
                    shutil.copyfileobj(source, target)
                source_bytes = script.read_bytes()
                if AUTOSAVE_MARKER not in source_bytes:
                    raise RuntimeError(
                        f"Chapter {chapter} support source lacks training-only autosave v2 marker"
                    )
                patch = work / f"chapter{chapter}.g3mpatch"
                applied = work / f"chapter{chapter}.applied.win"
                print(f"Chapter {chapter}: compiling combined AI Support 2.0.1", flush=True)
                run_command(str(g3mtool), "patch", "create", str(clean), str(script), str(patch))
                run_command(str(g3mtool), "patch", "validate", str(patch), "--data", str(clean))
                run_command(str(g3mtool), "patch", "apply", str(clean), str(patch), str(applied))
                marker_bytes = SPEED_MARKERS + (
                    TELEMETRY_MARKER,
                    AUTOSAVE_MARKER,
                    b"AI_MULTI_INSTANCE|1|",
                )
                applied_bytes = applied.read_bytes()
                missing = [marker.decode("ascii") for marker in marker_bytes if marker not in applied_bytes]
                if missing:
                    raise RuntimeError(f"Chapter {chapter} lost markers: {missing}")
                code = validate_merged_code(patch)
                if sha256_file(clean) != clean_hash:
                    raise RuntimeError(f"Chapter {chapter} clean source was modified")
                records.append(
                    {
                        "chapter": chapter,
                        "clean_sha256": clean_hash,
                        "changed_mod_code_entries": code["mod_code_entries"],
                        "training_only_autosave_v2": True,
                        "result": "PASS",
                    }
                )
                print(f"Chapter {chapter}: PASS", flush=True)

    report = {
        "format": "AI Support 2.0.1 combined CSX runtime validation v1",
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": "PASS",
        "package": package.name,
        "package_sha256": package_hash,
        "package_validation": validation,
        "g3mtool_version": tool_version,
        "chapters": records,
        "installed_game_modified": False,
    }
    destination = args.report.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Validation report: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
