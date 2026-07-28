from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deltarune_agent.deltamod_package import DELTARUNE_105_HASHES


TARGET_CODE = "gml_Object_obj_time_Step_1"
SPEED_MARKERS = (b"AI_SPEED_MOD|1|", b"DRSPEED|1|multiplier=")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MINIMUM_G3MTOOL_VERSION = (1, 2, 5)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(*command: str) -> None:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    details = "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if part.strip()
    )
    raise RuntimeError(
        f"Command failed with exit code {result.returncode}:\n"
        f"{subprocess.list2cmdline(command)}\n{details}"
    )


def _require_supported_g3mtool(path: Path) -> tuple[int, int, int]:
    result = subprocess.run(
        (str(path), "--version"),
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if part.strip()
    )
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", output)
    if result.returncode != 0 or match is None:
        raise RuntimeError(
            f"Could not determine the G3MTool version from {path}: "
            f"{output or 'no version output'}"
        )
    version = tuple(int(part) for part in match.groups())
    if version < MINIMUM_G3MTOOL_VERSION:
        observed = ".".join(str(part) for part in version)
        required = ".".join(
            str(part) for part in MINIMUM_G3MTOOL_VERSION
        )
        raise RuntimeError(
            f"G3MTool {observed} is unsafe for separate telemetry + speed "
            f"merges. Install G3MTool {required} or newer first."
        )
    return version


def _find_clean_source(game_directory: Path, chapter: int) -> Path:
    chapter_directory = game_directory / f"chapter{chapter}_windows"
    candidates = (
        chapter_directory / "data.win",
        chapter_directory / "data.win.bak",
        chapter_directory / "data.original.win",
        chapter_directory / "data.unmodded.win",
    )
    expected = DELTARUNE_105_HASHES[chapter]
    observed: list[str] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        checksum = _sha256(candidate)
        if checksum == expected:
            return candidate
        observed.append(f"{candidate.name}={checksum}")
    details = ", ".join(observed) if observed else "no candidate files found"
    raise RuntimeError(
        f"Chapter {chapter} has no verified clean Deltarune 1.05 source "
        f"({details})"
    )


def _write_zip_entry(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
) -> None:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload, compresslevel=9)


def _minimize_patch(source: Path, output: Path) -> None:
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read("g3mpatch.json"))
        resources = manifest.get("resources")
        if not isinstance(resources, dict):
            raise RuntimeError("G3MTool produced a patch without resources")
        unexpected_types = set(resources).difference(
            {"CodeEntries", "Sounds"}
        )
        if unexpected_types:
            raise RuntimeError(
                "Speed source unexpectedly changed resource types: "
                + ", ".join(sorted(unexpected_types))
            )
        sounds = resources.get("Sounds")
        if sounds is not None and (
            not isinstance(sounds, dict)
            or sounds.get("new") != []
            or sounds.get("deleted") != []
        ):
            raise RuntimeError(
                "Speed source unexpectedly added or deleted sounds"
            )
        code_entries = resources.get("CodeEntries")
        if not isinstance(code_entries, dict):
            raise RuntimeError("G3MTool patch did not contain CodeEntries")
        changed = code_entries.get("changed")
        if (
            not isinstance(changed, list)
            or len(changed) != 1
            or not isinstance(changed[0], dict)
            or changed[0].get("name") != TARGET_CODE
            or code_entries.get("new") != []
            or code_entries.get("deleted") != []
        ):
            raise RuntimeError(
                "Speed source changed code outside obj_time Begin Step"
            )
        files = changed[0].get("files")
        if not isinstance(files, dict) or not files:
            raise RuntimeError("Changed speed code has no patch files")
        referenced = tuple(str(name) for name in files.values())
        gml_names = [name for name in referenced if name.endswith(".gml")]
        if len(gml_names) != 1:
            raise RuntimeError("Speed patch must contain exactly one GML file")
        gml = archive.read(gml_names[0])
        if any(marker not in gml for marker in SPEED_MARKERS):
            raise RuntimeError(
                "Speed patch GML is missing synchronization markers"
            )

        manifest["resources"] = {"CodeEntries": code_entries}
        manifest["statistics"] = {
            "totalChanged": 1,
            "totalNew": 0,
            "totalDeleted": 0,
            "totalChangedFiles": len(referenced),
            "totalNewFiles": 0,
        }
        manifest["applyPlan"] = {
            "mode": "standard",
            "requiresCodePipeline": True,
            "requiresTexturePipeline": False,
            "requiresAssetReorder": False,
            "requiresHeavyFinalize": True,
            "supportsDirectResourceApply": False,
            "simpleResourceTypes": [],
            "heavyResourceTypes": ["CodeEntries"],
        }
        keep = referenced + (
            "Helpers/object_events.json",
            "Helpers/variables_functions.json",
        )
        output.unlink(missing_ok=True)
        with zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as result:
            _write_zip_entry(
                result,
                "g3mpatch.json",
                (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
            )
            for name in keep:
                _write_zip_entry(result, name, archive.read(name))


def _build_chapter(
    *,
    chapter: int,
    clean_source: Path,
    script: Path,
    g3mtool: Path,
    output: Path,
    temporary_root: Path,
) -> None:
    original_hash = _sha256(clean_source)
    if original_hash != DELTARUNE_105_HASHES[chapter]:
        raise RuntimeError(f"Chapter {chapter} clean source hash changed")

    chapter_root = temporary_root / f"chapter{chapter}"
    chapter_root.mkdir()
    modified = chapter_root / f"Chapter{chapter}Speed.win"
    raw_patch = chapter_root / f"Chapter{chapter}Raw.g3mpatch"
    minimized = chapter_root / output.name
    applied = chapter_root / f"Chapter{chapter}Applied.win"

    print(f"Chapter {chapter}: compiling {script.name}")
    _run(
        str(g3mtool),
        "execute",
        str(script),
        "--data",
        str(clean_source),
        "--output",
        str(modified),
    )
    _run(
        str(g3mtool),
        "patch",
        "create",
        str(clean_source),
        str(modified),
        str(raw_patch),
    )
    _minimize_patch(raw_patch, minimized)
    _run(
        str(g3mtool),
        "patch",
        "validate",
        str(minimized),
        "--data",
        str(clean_source),
    )
    _run(
        str(g3mtool),
        "patch",
        "apply",
        str(clean_source),
        str(minimized),
        str(applied),
    )
    applied_bytes = applied.read_bytes()
    if any(marker not in applied_bytes for marker in SPEED_MARKERS):
        raise RuntimeError(
            f"Chapter {chapter} applied output is missing speed markers"
        )
    if _sha256(clean_source) != original_hash:
        raise RuntimeError(f"Chapter {chapter} clean source was modified")
    output.parent.mkdir(parents=True, exist_ok=True)
    minimized.replace(output)
    print(
        f"Chapter {chapter}: wrote temporary payload {output.name} "
        f"({output.stat().st_size:,} bytes)"
    )


def build_parser() -> argparse.ArgumentParser:
    local_app_data = Path.home() / "AppData" / "Local"
    default_g3mtool = (
        local_app_data
        / "deltamod"
        / "win-unpacked"
        / "resources"
        / "app"
        / "tools"
        / "G3MTool-win32.exe"
    )
    program_files = Path(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    )
    speed_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Compile AiSpeed.csx into ignored, temporary per-chapter "
            "G3MTool payloads without modifying the installed game."
        )
    )
    parser.add_argument(
        "--game-directory",
        type=Path,
        default=(
            program_files / "Steam" / "steamapps" / "common" / "DELTARUNE"
        ),
    )
    parser.add_argument(
        "--g3mtool",
        type=Path,
        default=default_g3mtool,
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=speed_root / ".build" / "payloads",
    )
    parser.add_argument(
        "--chapter",
        action="append",
        type=int,
        choices=range(1, 6),
        dest="chapters",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    game_directory = args.game_directory.expanduser().resolve()
    g3mtool = args.g3mtool.expanduser().resolve()
    output_directory = args.output_directory.expanduser().resolve()
    script = Path(__file__).resolve().parents[1] / "AiSpeed.csx"
    chapters = sorted(set(args.chapters or range(1, 6)))
    if not g3mtool.is_file():
        raise FileNotFoundError(f"G3MTool was not found: {g3mtool}")
    version = _require_supported_g3mtool(g3mtool)
    print(
        "Using G3MTool "
        + ".".join(str(part) for part in version)
        + " (separate-mod merge fix verified)"
    )
    if not script.is_file():
        raise FileNotFoundError(f"Speed source was not found: {script}")
    output_directory.mkdir(parents=True, exist_ok=True)

    sources = {
        chapter: _find_clean_source(game_directory, chapter)
        for chapter in chapters
    }
    with tempfile.TemporaryDirectory(
        prefix=".speed-payload-build-",
        dir=output_directory.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        for chapter in chapters:
            _build_chapter(
                chapter=chapter,
                clean_source=sources[chapter],
                script=script,
                g3mtool=g3mtool,
                output=(
                    output_directory
                    / f"Chapter{chapter}Speed.g3mpatch"
                ),
                temporary_root=temporary_root,
            )
    print(
        "Payloads are build intermediates under "
        f"{output_directory}; Git ignores this directory."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
