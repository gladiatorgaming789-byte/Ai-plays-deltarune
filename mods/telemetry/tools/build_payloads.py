from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deltarune_agent.deltamod_package import (
    DELTARUNE_CURRENT_HASHES,
    DELTARUNE_CURRENT_MD5,
    DELTARUNE_STEAM_BUILD_ID,
)


EXPECTED_CODE = {
    "gml_Object_obj_mainchara_Step_0": b"__ai_start_autosave_done",
    "gml_Object_obj_mainchara_Draw_0": b"DRTEL|9|",
    "gml_Object_obj_heart_Draw_0": b"DRTEL|9|",
    "gml_Object_obj_writer_Draw_0": b"DRTEL|9|",
    "gml_Object_obj_choicer_neo_Draw_0": b"DRTEL|9|",
    "gml_Object_obj_choicer_old_Draw_0": b"DRTEL|9|",
    "gml_Object_obj_savemenu_Draw_0": b"DRTEL|9|",
    "gml_GlobalScript_ossafe_init": b"AI_MULTI_INSTANCE|1|",
    "gml_GlobalScript_ossafe_file_delete": b"__ai_save_prefix",
    "gml_GlobalScript_ossafe_file_exists": b"__ai_save_prefix",
    "gml_GlobalScript_ossafe_file_text_open_read": b"__ai_save_prefix",
    "gml_GlobalScript_ossafe_file_text_open_write": b"__ai_save_prefix",
    "gml_GlobalScript_ossafe_ini_open": b"__ai_save_prefix",
}
TELEMETRY_MARKERS = (
    b"DRTEL|9|",
    b"__ai_start_autosave_done",
    b"AI_MULTI_INSTANCE|1|",
)
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MINIMUM_G3MTOOL_VERSION = (1, 2, 5)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(*command: str) -> str:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if part.strip()
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}:\n"
            f"{subprocess.list2cmdline(command)}\n{output}"
        )
    return output


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
        required = ".".join(str(part) for part in MINIMUM_G3MTOOL_VERSION)
        raise RuntimeError(
            f"G3MTool {observed} is unsafe for separate telemetry + speed "
            f"merges. Install G3MTool {required} or newer first."
        )
    return version


def _verify_source(path: Path, chapter: int) -> Path:
    observed_sha256 = _sha256(path)
    if observed_sha256 != DELTARUNE_CURRENT_HASHES[chapter]:
        raise RuntimeError(
            f"Chapter {chapter} source is not the supported clean Steam "
            f"build: expected {DELTARUNE_CURRENT_HASHES[chapter]}, got "
            f"{observed_sha256} ({path})"
        )
    observed_md5 = _md5(path)
    if observed_md5 != DELTARUNE_CURRENT_MD5[chapter]:
        raise RuntimeError(
            f"Chapter {chapter} source MD5 does not match the verified build"
        )
    return path


def _extract_archive_sources(
    archive_path: Path,
    chapters: list[int],
    destination: Path,
) -> dict[int, Path]:
    sources: dict[int, Path] = {}
    with zipfile.ZipFile(archive_path) as archive:
        for chapter in chapters:
            member = f"chapter{chapter}_windows/data.win"
            try:
                info = archive.getinfo(member)
            except KeyError as exc:
                raise RuntimeError(
                    f"Source archive has no {member}: {archive_path}"
                ) from exc
            output = destination / f"chapter{chapter}.clean.win"
            with archive.open(info) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            sources[chapter] = _verify_source(output, chapter)
    return sources


def _find_installed_sources(
    game_directory: Path,
    chapters: list[int],
) -> dict[int, Path]:
    sources: dict[int, Path] = {}
    for chapter in chapters:
        chapter_directory = game_directory / f"chapter{chapter}_windows"
        candidates = (
            chapter_directory / "data.win",
            chapter_directory / "data.win.bak",
            chapter_directory / "data.original.win",
            chapter_directory / "data.unmodded.win",
        )
        observations: list[str] = []
        for candidate in candidates:
            if not candidate.is_file():
                continue
            checksum = _sha256(candidate)
            if checksum == DELTARUNE_CURRENT_HASHES[chapter]:
                sources[chapter] = _verify_source(candidate, chapter)
                break
            observations.append(f"{candidate.name}={checksum}")
        else:
            details = ", ".join(observations) or "no candidates found"
            raise RuntimeError(
                f"Chapter {chapter} has no verified clean source ({details})"
            )
    return sources


def _write_zip_entry(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
) -> None:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload, compresslevel=9)


def _minimize_patch(source: Path, output: Path, chapter: int) -> None:
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read("g3mpatch.json"))
        resources = manifest.get("resources")
        if not isinstance(resources, dict):
            raise RuntimeError("G3MTool produced a patch without resources")
        unexpected_types = set(resources).difference({"CodeEntries", "Sounds"})
        if unexpected_types:
            raise RuntimeError(
                "Telemetry compilation changed unexpected resource types: "
                + ", ".join(sorted(unexpected_types))
            )

        code_entries = resources.get("CodeEntries")
        if not isinstance(code_entries, dict):
            raise RuntimeError("Telemetry patch has no CodeEntries")
        changed = code_entries.get("changed")
        if not isinstance(changed, list):
            raise RuntimeError("Telemetry CodeEntries metadata is invalid")
        if code_entries.get("new") != [] or code_entries.get("deleted") != []:
            raise RuntimeError("Telemetry must not add or delete code entries")
        by_name = {
            entry.get("name"): entry
            for entry in changed
            if isinstance(entry, dict)
        }
        if set(by_name) != set(EXPECTED_CODE):
            actual = ", ".join(sorted(str(name) for name in by_name))
            raise RuntimeError(
                f"Chapter {chapter} telemetry changed unexpected code: {actual}"
            )

        referenced: list[str] = []
        for name, marker in EXPECTED_CODE.items():
            entry = by_name[name]
            files = entry.get("files")
            if not isinstance(files, dict) or not files:
                raise RuntimeError(f"{name} has no patch files")
            paths = [str(path) for path in files.values()]
            gml_paths = [path for path in paths if path.endswith(".gml")]
            if len(gml_paths) != 1 or marker not in archive.read(gml_paths[0]):
                raise RuntimeError(
                    f"{name} is missing its telemetry installation marker"
                )
            referenced.extend(paths)

        manifest["resources"] = {"CodeEntries": code_entries}
        # G3MTool records wall-clock time and an internal materialized filename.
        # Neither affects application; normalizing them makes release payloads
        # reproducible when the clean data and script are unchanged.
        manifest["createdAt"] = "1980-01-01T00:00:00Z"
        manifest["original"]["filename"] = "data.win"
        manifest["modified"]["filename"] = "data.telemetry.win"
        manifest["statistics"] = {
            "totalChanged": len(EXPECTED_CODE),
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
        keep = tuple(referenced) + (
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
) -> dict[str, object]:
    original_hash = _sha256(clean_source)
    chapter_root = temporary_root / f"chapter{chapter}"
    chapter_root.mkdir()
    modified = chapter_root / f"Chapter{chapter}Telemetry.win"
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
    _minimize_patch(raw_patch, minimized, chapter)
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
    if any(marker not in applied_bytes for marker in TELEMETRY_MARKERS):
        raise RuntimeError(
            f"Chapter {chapter} applied output is missing telemetry markers"
        )
    if _sha256(clean_source) != original_hash:
        raise RuntimeError(f"Chapter {chapter} clean source was modified")
    output.parent.mkdir(parents=True, exist_ok=True)
    minimized.replace(output)
    record = {
        "chapter": chapter,
        "clean_size": clean_source.stat().st_size,
        "clean_sha256": original_hash,
        "clean_md5": DELTARUNE_CURRENT_MD5[chapter],
        "patched_size": applied.stat().st_size,
        "patched_sha256": _sha256(applied),
        "payload_size": output.stat().st_size,
        "payload_sha256": _sha256(output),
        "changed_code_entries": sorted(EXPECTED_CODE),
    }
    print(
        f"Chapter {chapter}: wrote {output.name} "
        f"({output.stat().st_size:,} bytes)"
    )
    return record


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
    game_directory = (
        program_files / "Steam" / "steamapps" / "common" / "DELTARUNE"
    )
    telemetry_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Compile AiTelemetry.csx into ignored per-chapter G3MTool "
            "payloads without modifying the installed game."
        )
    )
    parser.add_argument("--game-directory", type=Path, default=game_directory)
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=game_directory / "Deltarune.zip",
        help=(
            "ZIP containing chapterN_windows/data.win. If absent, verified "
            "clean files are discovered in --game-directory."
        ),
    )
    parser.add_argument("--g3mtool", type=Path, default=default_g3mtool)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=telemetry_root / ".build" / "payloads",
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
    source_archive = args.source_archive.expanduser().resolve()
    g3mtool = args.g3mtool.expanduser().resolve()
    output_directory = args.output_directory.expanduser().resolve()
    script = Path(__file__).resolve().parents[1] / "AiTelemetry.csx"
    chapters = sorted(set(args.chapters or range(1, 6)))
    if not g3mtool.is_file():
        raise FileNotFoundError(f"G3MTool was not found: {g3mtool}")
    version = _require_supported_g3mtool(g3mtool)
    print(
        "Using G3MTool "
        + ".".join(str(part) for part in version)
        + " (separate-mod merge fix required)"
    )
    if not script.is_file():
        raise FileNotFoundError(f"Telemetry source was not found: {script}")
    output_directory.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(
        prefix=".telemetry-payload-build-",
        dir=output_directory.parent,
    ) as temporary:
        temporary_root = Path(temporary)
        if source_archive.is_file():
            print(f"Reading clean chapter files from {source_archive}")
            sources = _extract_archive_sources(
                source_archive,
                chapters,
                temporary_root,
            )
        else:
            print(f"Reading clean chapter files from {game_directory}")
            sources = _find_installed_sources(game_directory, chapters)
        build_root = temporary_root / "build"
        build_root.mkdir()
        for chapter in chapters:
            records.append(
                _build_chapter(
                    chapter=chapter,
                    clean_source=sources[chapter],
                    script=script,
                    g3mtool=g3mtool,
                    output=(
                        output_directory
                        / f"Chapter{chapter}Telemetry.g3mpatch"
                    ),
                    temporary_root=build_root,
                )
            )

    record_path = output_directory.parent / "payloads.json"
    record_path.write_text(
        json.dumps(
            {
                "record_version": 1,
                "steam_build_id": DELTARUNE_STEAM_BUILD_ID,
                "telemetry_protocol": 9,
                "manual_source": script.name,
                "manual_source_sha256": _sha256(script),
                "g3mtool_version": ".".join(str(part) for part in version),
                "source": (
                    str(source_archive)
                    if source_archive.is_file()
                    else str(game_directory)
                ),
                "chapters": records,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Build record: {record_path}")
    print("Loose payloads remain only in the ignored .build directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
