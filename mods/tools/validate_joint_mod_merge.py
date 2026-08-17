from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deltarune_agent.deltamod_package import (  # noqa: E402
    DELTARUNE_CURRENT_HASHES,
    DELTARUNE_CURRENT_MD5,
    DELTARUNE_PATCH_LABEL,
    DELTARUNE_STEAM_BUILD_ID,
)


MINIMUM_G3MTOOL_VERSION = (1, 2, 5)
SPEED_CODE = {"gml_Object_obj_time_Step_1"}
TELEMETRY_CODE = {
    "gml_Object_obj_mainchara_Step_0",
    "gml_Object_obj_mainchara_Draw_0",
    "gml_Object_obj_heart_Draw_0",
    "gml_Object_obj_writer_Draw_0",
    "gml_Object_obj_choicer_neo_Draw_0",
    "gml_Object_obj_choicer_old_Draw_0",
    "gml_Object_obj_savemenu_Draw_0",
    "gml_GlobalScript_ossafe_init",
    "gml_GlobalScript_ossafe_file_delete",
    "gml_GlobalScript_ossafe_file_exists",
    "gml_GlobalScript_ossafe_file_text_open_read",
    "gml_GlobalScript_ossafe_file_text_open_write",
    "gml_GlobalScript_ossafe_ini_open",
}
TELEMETRY_DRAW_CODE = {
    "gml_Object_obj_mainchara_Draw_0",
    "gml_Object_obj_heart_Draw_0",
    "gml_Object_obj_writer_Draw_0",
    "gml_Object_obj_choicer_neo_Draw_0",
    "gml_Object_obj_choicer_old_Draw_0",
    "gml_Object_obj_savemenu_Draw_0",
}
SPEED_MARKERS = (b"AI_SPEED_MOD|1|", b"DRSPEED|1|multiplier=")
TELEMETRY_MARKER = b"DRTEL|9|"
AUTOSAVE_MARKER = b"__ai_start_autosave_done"
MERGE_ORDER = ("speed", "telemetry")


@dataclass(frozen=True)
class PayloadSet:
    kind: str
    source_type: str
    source: Path
    payloads: dict[int, Path]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _version_tuple(text: str) -> tuple[int, int, int]:
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", text)
    if match is None:
        raise RuntimeError(f"No three-part version was found in: {text!r}")
    return tuple(int(part) for part in match.groups())


def require_g3mtool(path: Path) -> str:
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
    if result.returncode != 0:
        raise RuntimeError(
            f"G3MTool version check failed ({result.returncode}): {output}"
        )
    version = _version_tuple(output)
    if version < MINIMUM_G3MTOOL_VERSION:
        required = ".".join(str(part) for part in MINIMUM_G3MTOOL_VERSION)
        observed = ".".join(str(part) for part in version)
        raise RuntimeError(
            f"G3MTool {observed} is not safe for this joint merge; "
            f"{required} or newer is required"
        )
    return ".".join(str(part) for part in version)


def run_command(*command: str) -> str:
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


def read_patch_manifest(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if names.count("g3mpatch.json") != 1:
                raise RuntimeError(
                    f"{path.name} must contain exactly one g3mpatch.json"
                )
            manifest = json.loads(archive.read("g3mpatch.json"))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid G3MTool patch: {path}") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Invalid G3MTool manifest root: {path}")
    return manifest


def _resource_name(item: object) -> str:
    if isinstance(item, str) and item:
        return item
    if isinstance(item, dict):
        name = item.get("name")
        if isinstance(name, str) and name:
            return name
    raise RuntimeError(f"Patch resource entry has no valid name: {item!r}")


def resource_identities(manifest: dict[str, Any]) -> set[tuple[str, str]]:
    """Return resource type/name pairs, ignoring changed/new/deleted action."""
    resources = manifest.get("resources")
    if not isinstance(resources, dict):
        raise RuntimeError("Patch manifest has no resource map")
    identities: set[tuple[str, str]] = set()
    for resource_type, groups in resources.items():
        if not isinstance(resource_type, str) or not isinstance(groups, dict):
            raise RuntimeError("Patch resource map is malformed")
        for action in ("changed", "new", "deleted"):
            items = groups.get(action, [])
            if not isinstance(items, list):
                raise RuntimeError(
                    f"Patch {resource_type}.{action} must be a list"
                )
            for item in items:
                identity = (resource_type, _resource_name(item))
                if identity in identities:
                    raise RuntimeError(
                        f"Patch repeats resource {identity[0]}/{identity[1]}"
                    )
                identities.add(identity)
    return identities


def code_texts(path: Path) -> dict[str, bytes]:
    manifest = read_patch_manifest(path)
    resources = manifest.get("resources", {})
    code_entries = resources.get("CodeEntries")
    if not isinstance(code_entries, dict):
        return {}
    changed = code_entries.get("changed")
    if not isinstance(changed, list):
        raise RuntimeError(f"{path.name} has malformed changed CodeEntries")
    result: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as archive:
        archive_names = set(archive.namelist())
        for item in changed:
            name = _resource_name(item)
            if not isinstance(item, dict) or not isinstance(
                item.get("files"), dict
            ):
                raise RuntimeError(f"{path.name}: {name} has no file map")
            referenced_paths = [str(member) for member in item["files"].values()]
            if not referenced_paths:
                # G3MTool 1.2.5 can emit an empty changed record for an
                # unrelated, pre-existing code entry that it cannot
                # decompile (observed in Chapter 3's Susiezilla collision
                # event). Keep the identity in the normalization report. A
                # required mod entry would still fail its marker checks.
                result[name] = b""
                continue
            if any(member not in archive_names for member in referenced_paths):
                raise RuntimeError(
                    f"{path.name}: {name} references a missing code file"
                )
            gml_paths = [
                str(member)
                for member in referenced_paths
                if str(member).casefold().endswith(".gml")
            ]
            if len(gml_paths) > 1:
                raise RuntimeError(
                    f"{path.name}: {name} references multiple GML files"
                )
            if name in result:
                raise RuntimeError(f"{path.name} repeats code entry {name}")
            if gml_paths:
                result[name] = archive.read(gml_paths[0])
            else:
                # Some original scripts cannot be decompiled by G3MTool and
                # therefore appear as ASM-only normalization changes. Scan all
                # referenced assembly payloads so relocated string markers
                # would still be detected instead of silently skipped.
                result[name] = b"\n".join(
                    archive.read(member) for member in referenced_paths
                )
    return result


def validate_payload_structure(
    speed_patch: Path,
    telemetry_patch: Path,
    *,
    expected_md5: str,
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    speed_manifest = read_patch_manifest(speed_patch)
    telemetry_manifest = read_patch_manifest(telemetry_patch)
    for label, manifest in (
        ("speed", speed_manifest),
        ("telemetry", telemetry_manifest),
    ):
        original = manifest.get("original")
        if not isinstance(original, dict) or str(
            original.get("md5", "")
        ).casefold() != expected_md5:
            raise RuntimeError(
                f"{label} patch was not built against this clean chapter"
            )

    speed_resources = resource_identities(speed_manifest)
    telemetry_resources = resource_identities(telemetry_manifest)
    overlap = speed_resources & telemetry_resources
    if overlap:
        details = ", ".join(
            f"{kind}/{name}" for kind, name in sorted(overlap)
        )
        raise RuntimeError(f"Speed and telemetry resources overlap: {details}")

    speed_gml = code_texts(speed_patch)
    telemetry_gml = code_texts(telemetry_patch)
    if set(speed_gml) != SPEED_CODE:
        raise RuntimeError(
            "Speed payload must change only obj_time Begin Step; got "
            + ", ".join(sorted(speed_gml))
        )
    if set(telemetry_gml) != TELEMETRY_CODE:
        raise RuntimeError(
            "Telemetry payload changed an unexpected code set: "
            + ", ".join(sorted(telemetry_gml))
        )
    speed_text = speed_gml[next(iter(SPEED_CODE))]
    if any(marker not in speed_text for marker in SPEED_MARKERS):
        raise RuntimeError("Speed payload is missing its installation markers")
    if TELEMETRY_MARKER in speed_text or AUTOSAVE_MARKER in speed_text:
        raise RuntimeError("Telemetry code was relocated into obj_time")
    for name in TELEMETRY_DRAW_CODE:
        if TELEMETRY_MARKER not in telemetry_gml[name]:
            raise RuntimeError(f"Telemetry marker is missing from {name}")
    if AUTOSAVE_MARKER not in telemetry_gml[
        "gml_Object_obj_mainchara_Step_0"
    ]:
        raise RuntimeError("Telemetry autosave marker is missing")
    return speed_resources, telemetry_resources


def validate_merged_code(path: Path) -> dict[str, Any]:
    texts = code_texts(path)
    expected = SPEED_CODE | TELEMETRY_CODE
    if not expected.issubset(texts):
        missing = sorted(expected - set(texts))
        raise RuntimeError(
            "Merged output lost required changed code entries: "
            f"missing={missing}"
        )
    speed_text = texts[next(iter(SPEED_CODE))]
    if any(marker not in speed_text for marker in SPEED_MARKERS):
        raise RuntimeError("Speed code marker was lost during joint merge")
    relocation_markers = (
        TELEMETRY_MARKER,
        AUTOSAVE_MARKER,
        b"AI_TELEMETRY_V9",
        b"bbox_top",
    )
    if any(marker in speed_text for marker in relocation_markers):
        raise RuntimeError(
            "Telemetry code was relocated into obj_time during joint merge"
        )
    marker_owners = {
        "AI_SPEED_MOD|1|": sorted(
            name for name, text in texts.items() if SPEED_MARKERS[0] in text
        ),
        "DRSPEED|1|multiplier=": sorted(
            name for name, text in texts.items() if SPEED_MARKERS[1] in text
        ),
        "DRTEL|9|": sorted(
            name for name, text in texts.items() if TELEMETRY_MARKER in text
        ),
        "__ai_start_autosave_done": sorted(
            name for name, text in texts.items() if AUTOSAVE_MARKER in text
        ),
    }
    speed_owner = sorted(SPEED_CODE)
    if marker_owners["AI_SPEED_MOD|1|"] != speed_owner or marker_owners[
        "DRSPEED|1|multiplier="
    ] != speed_owner:
        raise RuntimeError(
            "Speed markers were lost or relocated during joint merge: "
            f"{marker_owners}"
        )
    if marker_owners["DRTEL|9|"] != sorted(TELEMETRY_DRAW_CODE):
        raise RuntimeError(
            "Telemetry markers were lost or relocated during joint merge: "
            f"{marker_owners['DRTEL|9|']}"
        )
    autosave_owner = ["gml_Object_obj_mainchara_Step_0"]
    if marker_owners["__ai_start_autosave_done"] != autosave_owner:
        raise RuntimeError(
            "Telemetry autosave code was lost or relocated: "
            f"{marker_owners['__ai_start_autosave_done']}"
        )

    # G3MTool's final code pipeline can normalize and recompile untouched
    # scripts even when the input resources are disjoint. Those entries are
    # acceptable only when none of this project's unique markers moved into
    # them; marker ownership above enforces that invariant.
    all_changed = sorted(texts)
    return {
        "mod_code_entries": sorted(expected),
        "additional_recompiled_code_entries": sorted(set(texts) - expected),
        "all_changed_code_entries": all_changed,
        "marker_owners": marker_owners,
    }


def _package_version(path: Path) -> tuple[int, int, int]:
    try:
        return _version_tuple(path.stem)
    except RuntimeError:
        return (0, 0, 0)


def discover_package(kind: str) -> Path | None:
    if kind == "speed":
        root = REPOSITORY_ROOT / "mods" / "speed" / "deltamod"
        pattern = "AI-Speed-All-Chapters-DeltaMod-v*.zip"
    elif kind == "telemetry":
        root = REPOSITORY_ROOT / "mods" / "telemetry" / "deltamod"
        pattern = "Telemetry-All-Chapters-DeltaMod-v*.zip"
    else:
        raise ValueError(f"Unknown payload kind: {kind}")
    matches = sorted(root.glob(pattern), key=_package_version)
    return matches[-1] if matches else None


def _payload_member(kind: str, chapter: int) -> str:
    suffix = "Speed" if kind == "speed" else "Telemetry"
    return f"Chapter{chapter}{suffix}.g3mpatch"


def load_payload_set(
    kind: str,
    package: Path | None,
    build_directory: Path,
    destination: Path,
) -> PayloadSet:
    selected = package or discover_package(kind)
    if selected is not None:
        selected = selected.expanduser().resolve()
        if not selected.is_file():
            raise FileNotFoundError(f"{kind} package was not found: {selected}")
        payloads: dict[int, Path] = {}
        with zipfile.ZipFile(selected) as archive:
            names = archive.namelist()
            for chapter in range(1, 6):
                member = _payload_member(kind, chapter)
                if names.count(member) != 1:
                    raise RuntimeError(
                        f"{selected.name} must contain exactly one {member}"
                    )
                output = destination / f"{kind}-{member}"
                with archive.open(member) as source, output.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                payloads[chapter] = output
        return PayloadSet(kind, "DeltaMod ZIP", selected, payloads)

    build_directory = build_directory.expanduser().resolve()
    payloads = {
        chapter: build_directory / _payload_member(kind, chapter)
        for chapter in range(1, 6)
    }
    missing = [str(path) for path in payloads.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"No current {kind} package or complete build payload set exists:\n"
            + "\n".join(missing)
        )
    return PayloadSet(kind, "ignored build payloads", build_directory, payloads)


def extract_clean_sources(archive_path: Path, destination: Path) -> dict[int, Path]:
    sources: dict[int, Path] = {}
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        for chapter in range(1, 6):
            member = f"chapter{chapter}_windows/data.win"
            if names.count(member) != 1:
                raise RuntimeError(
                    f"Deltarune.zip must contain exactly one {member}"
                )
            output = destination / f"chapter{chapter}.clean.win"
            with archive.open(member) as source, output.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            observed_sha256 = sha256_file(output)
            observed_md5 = md5_file(output)
            if observed_sha256 != DELTARUNE_CURRENT_HASHES[chapter]:
                raise RuntimeError(
                    f"Chapter {chapter} clean SHA-256 mismatch: "
                    f"{observed_sha256}"
                )
            if observed_md5 != DELTARUNE_CURRENT_MD5[chapter]:
                raise RuntimeError(
                    f"Chapter {chapter} clean MD5 mismatch: {observed_md5}"
                )
            sources[chapter] = output
    return sources


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def ensure_safe_work_directory(work: Path, game_directory: Path) -> None:
    if _is_within(work, game_directory):
        raise RuntimeError(
            "The validator refuses to write inside the Deltarune installation"
        )


def _resource_strings(resources: Iterable[tuple[str, str]]) -> list[str]:
    return [f"{kind}/{name}" for kind, name in sorted(resources)]


def _payload_source_record(payload_set: PayloadSet) -> dict[str, Any]:
    record: dict[str, Any] = {
        "source_type": payload_set.source_type,
        "source": str(payload_set.source),
    }
    if payload_set.source.is_file():
        record.update(
            {
                "size": payload_set.source.stat().st_size,
                "sha256": sha256_file(payload_set.source),
            }
        )
    return record


def markers_in_file(path: Path, markers: Iterable[bytes]) -> dict[str, bool]:
    marker_list = tuple(markers)
    with path.open("rb") as stream, mmap.mmap(
        stream.fileno(),
        length=0,
        access=mmap.ACCESS_READ,
    ) as contents:
        return {
            marker.decode("ascii"): contents.find(marker) >= 0
            for marker in marker_list
        }


def validate_chapter(
    *,
    chapter: int,
    clean: Path,
    speed_patch: Path,
    telemetry_patch: Path,
    g3mtool: Path,
    work: Path,
) -> dict[str, Any]:
    print(
        f"Chapter {chapter}: checking disjoint resources, then merging "
        "speed -> telemetry",
        flush=True,
    )
    clean_sha256 = sha256_file(clean)
    clean_md5 = md5_file(clean)
    if clean_sha256 != DELTARUNE_CURRENT_HASHES[chapter]:
        raise RuntimeError(f"Chapter {chapter} clean source changed before merge")
    speed_resources, telemetry_resources = validate_payload_structure(
        speed_patch,
        telemetry_patch,
        expected_md5=clean_md5,
    )

    chapter_root = work / f"chapter{chapter}"
    chapter_root.mkdir()
    merged = chapter_root / "speed-then-telemetry.win"
    merged_diff = chapter_root / "merged-output.g3mpatch"
    started = time.perf_counter()

    # This is the same low-to-high patch order used by DeltaMod. No merge
    # flags are added, so this also exercises the exact non-overlap code path.
    merge_output = run_command(
        str(g3mtool),
        "patch",
        "merge",
        str(clean),
        str(speed_patch),
        str(telemetry_patch),
        "-a",
        str(merged),
    )
    markers = markers_in_file(
        merged,
        SPEED_MARKERS + (TELEMETRY_MARKER, AUTOSAVE_MARKER),
    )
    if not all(markers.values()):
        missing = [name for name, present in markers.items() if not present]
        raise RuntimeError(
            f"Chapter {chapter} merged output lost markers: {missing}"
        )

    # Re-diff the final data file so markers can be tied to the exact code
    # resources where they belong. This catches the old obj_time/bbox_top
    # relocation failure that a whole-file byte search would miss.
    run_command(
        str(g3mtool),
        "patch",
        "create",
        str(clean),
        str(merged),
        str(merged_diff),
    )
    merged_code = validate_merged_code(merged_diff)
    elapsed = time.perf_counter() - started
    if sha256_file(clean) != clean_sha256 or md5_file(clean) != clean_md5:
        raise RuntimeError(f"Chapter {chapter} clean source was modified")

    print(
        f"Chapter {chapter}: PASS in {elapsed:.1f}s; all "
        f"{len(SPEED_CODE | TELEMETRY_CODE)} mod code entries "
        "remain in their intended events "
        f"({len(merged_code['additional_recompiled_code_entries'])} "
        "additional compiler-normalized entries)",
        flush=True,
    )
    return {
        "chapter": chapter,
        "clean_size": clean.stat().st_size,
        "clean_sha256": clean_sha256,
        "clean_md5": clean_md5,
        "clean_unchanged": True,
        "speed_payload_sha256": sha256_file(speed_patch),
        "telemetry_payload_sha256": sha256_file(telemetry_patch),
        "speed_resources": _resource_strings(speed_resources),
        "telemetry_resources": _resource_strings(telemetry_resources),
        "resource_overlap": [],
        "merge_order": list(MERGE_ORDER),
        "merged_size": merged.stat().st_size,
        "merged_sha256": sha256_file(merged),
        "markers": markers,
        "merged_code_validation": merged_code,
        "code_relocation_detected": False,
        "elapsed_seconds": round(elapsed, 3),
        "g3mtool_output_tail": merge_output[-1000:],
    }


def build_parser() -> argparse.ArgumentParser:
    program_files = Path(
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    )
    game_directory = (
        program_files / "Steam" / "steamapps" / "common" / "DELTARUNE"
    )
    local_app_data = Path.home() / "AppData" / "Local"
    parser = argparse.ArgumentParser(
        description=(
            "Safely validate speed + telemetry DeltaMod merging for Chapters "
            "1-5 using clean files from Deltarune.zip only."
        )
    )
    parser.add_argument(
        "--source-archive",
        type=Path,
        default=game_directory / "Deltarune.zip",
    )
    parser.add_argument(
        "--g3mtool",
        type=Path,
        default=(
            local_app_data
            / "deltamod"
            / "win-unpacked"
            / "resources"
            / "app"
            / "tools"
            / "G3MTool-win32.exe"
        ),
    )
    parser.add_argument("--speed-package", type=Path)
    parser.add_argument("--telemetry-package", type=Path)
    parser.add_argument(
        "--speed-build-directory",
        type=Path,
        default=REPOSITORY_ROOT / "mods" / "speed" / ".build" / "payloads",
    )
    parser.add_argument(
        "--telemetry-build-directory",
        type=Path,
        default=(
            REPOSITORY_ROOT / "mods" / "telemetry" / ".build" / "payloads"
        ),
    )
    parser.add_argument(
        "--work-directory",
        type=Path,
        help="optional non-Steam directory in which to retain merged outputs",
    )
    parser.add_argument("--report", type=Path)
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
    source_archive = args.source_archive.expanduser().resolve()
    g3mtool = args.g3mtool.expanduser().resolve()
    if not source_archive.is_file():
        raise FileNotFoundError(f"Deltarune.zip was not found: {source_archive}")
    if not g3mtool.is_file():
        raise FileNotFoundError(f"G3MTool was not found: {g3mtool}")
    chapters = sorted(set(args.chapters or range(1, 6)))
    g3mtool_version = require_g3mtool(g3mtool)
    archive_hash_before = sha256_file(source_archive)
    game_directory = source_archive.parent

    if args.work_directory is None:
        context: Any = tempfile.TemporaryDirectory(
            prefix="ai-deltarune-joint-mod-validation-"
        )
    else:
        retained = args.work_directory.expanduser().resolve()
        ensure_safe_work_directory(retained, game_directory)
        retained.mkdir(parents=True, exist_ok=True)
        context = nullcontext(str(retained))

    print(
        f"Using G3MTool {g3mtool_version}; source is {source_archive.name} "
        f"for Steam build {DELTARUNE_STEAM_BUILD_ID}",
        flush=True,
    )
    with context as temporary:
        work = Path(temporary)
        ensure_safe_work_directory(work, game_directory)
        inputs = work / "inputs"
        inputs.mkdir(exist_ok=True)
        clean_sources = extract_clean_sources(source_archive, inputs)
        speed = load_payload_set(
            "speed",
            args.speed_package,
            args.speed_build_directory,
            inputs,
        )
        telemetry = load_payload_set(
            "telemetry",
            args.telemetry_package,
            args.telemetry_build_directory,
            inputs,
        )
        print(
            f"Speed input: {speed.source_type} ({speed.source.name})\n"
            f"Telemetry input: {telemetry.source_type} "
            f"({telemetry.source.name})",
            flush=True,
        )
        chapter_records = [
            validate_chapter(
                chapter=chapter,
                clean=clean_sources[chapter],
                speed_patch=speed.payloads[chapter],
                telemetry_patch=telemetry.payloads[chapter],
                g3mtool=g3mtool,
                work=work,
            )
            for chapter in chapters
        ]

    archive_hash_after = sha256_file(source_archive)
    if archive_hash_after != archive_hash_before:
        raise RuntimeError("The source Deltarune.zip changed during validation")
    report = {
        "format": "AI Deltarune separate-mod joint merge validation v1",
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": "PASS",
        "steam_build_id": DELTARUNE_STEAM_BUILD_ID,
        "patch_label": DELTARUNE_PATCH_LABEL,
        "g3mtool_version": g3mtool_version,
        "merge_order": list(MERGE_ORDER),
        "source_archive": {
            "name": source_archive.name,
            "sha256_before": archive_hash_before,
            "sha256_after": archive_hash_after,
            "unchanged": True,
        },
        "speed_input": _payload_source_record(speed),
        "telemetry_input": _payload_source_record(telemetry),
        "chapters": chapter_records,
        "summary": {
            "chapters_passed": len(chapter_records),
            "chapters_requested": len(chapters),
            "resource_sets_disjoint": True,
            "all_markers_present": True,
            "code_relocation_detected": False,
            "source_archive_unchanged": True,
            "steam_install_written": False,
        },
    }
    report_text = json.dumps(report, indent=2) + "\n"
    if args.report is not None:
        report_path = args.report.expanduser().resolve()
        ensure_safe_work_directory(report_path, game_directory)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            report_text,
            encoding="utf-8",
            newline="\n",
        )
        print(f"Validation report: {report_path}", flush=True)
    print(
        f"PASS: {len(chapter_records)}/{len(chapters)} chapters merged in "
        "speed -> telemetry order; resource sets are disjoint and code "
        "remained in the intended events.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
