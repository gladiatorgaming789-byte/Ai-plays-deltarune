from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deltarune_agent.deltamod_package import (
    DELTARUNE_CURRENT_HASHES,
    PatchSpec,
    build_package,
    validate_package,
)
from mods.speed.tools.release_config import (
    BUILD_INFO_FILENAME,
    MINIMUM_G3MTOOL_VERSION,
    SUPPORTED_CHAPTERS,
    SUPPORTED_GAME_BUILD,
    TARGET_VERSION,
    VERSION,
)


NAME = "AI Deltarune Run Speed"
DESCRIPTION = (
    "Reversible 1x-10x simulation speed controls with localhost "
    "synchronization for standalone play or telemetry-synchronized AI runs. "
    f"Targets {SUPPORTED_GAME_BUILD}. Multi-code-patch merging requires "
    "G3MTool 1.2.5 or newer."
)
AUTHOR = "gladiatorgaming789-byte"
URL = "https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune"
ALL_PACKAGE_ID = "github.ai-speed.gladiatorgaming789-byte"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payloads(directory: Path) -> dict[int, Path]:
    payloads = {
        chapter: directory / f"Chapter{chapter}Speed.g3mpatch"
        for chapter in SUPPORTED_CHAPTERS
    }
    missing = [str(path) for path in payloads.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing temporary speed payloads. Run build_payloads.py first:\n"
            + "\n".join(missing)
        )
    return payloads


def _build_provenance(directory: Path, source: Path) -> dict[str, object]:
    path = directory / BUILD_INFO_FILENAME
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {BUILD_INFO_FILENAME}. Run build_payloads.py before "
            "packaging so source provenance can be verified."
        )
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid payload provenance: {path}") from exc
    expected = {
        "speed_mod_version": VERSION,
        "deltarune_target_version": TARGET_VERSION,
        "supported_game_build": SUPPORTED_GAME_BUILD,
        "script_sha256": _sha256(source),
        "chapters": list(SUPPORTED_CHAPTERS),
        "clean_chapter_sha256": {
            str(chapter): checksum
            for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
        },
    }
    mismatches = [
        key for key, value in expected.items() if info.get(key) != value
    ]
    if mismatches:
        raise ValueError(
            "Payload provenance does not match this release source: "
            + ", ".join(mismatches)
            + ". Rebuild all payloads before packaging."
        )
    version_text = str(info.get("g3mtool_version", ""))
    try:
        tool_version = tuple(int(part) for part in version_text.split("."))
    except ValueError as exc:
        raise ValueError(
            "Payload provenance has an invalid G3MTool version"
        ) from exc
    if len(tool_version) != 3 or tool_version < MINIMUM_G3MTOOL_VERSION:
        required = ".".join(
            str(part) for part in MINIMUM_G3MTOOL_VERSION
        )
        raise ValueError(f"Payloads require G3MTool {required} or newer")
    source_details = info.get("source")
    if not isinstance(source_details, dict) or source_details.get("mode") not in {
        "archive",
        "installed-files",
    }:
        raise ValueError("Payload provenance has invalid source details")
    return info


def _spec(chapter: int, payload: Path) -> PatchSpec:
    return PatchSpec(
        chapter,
        payload,
        archive_name_override=f"Chapter{chapter}Speed.g3mpatch",
    )


def _archive_record(path: Path, chapters: list[int]) -> dict[str, object]:
    validate_package(path)
    with zipfile.ZipFile(path) as archive:
        metadata = json.loads(archive.read("meta.json"))
        root_entries = archive.namelist()
    return {
        "file": path.name,
        "size": path.stat().st_size,
        "sha256": _sha256(path),
        "root_entries": root_entries,
        "package_id": metadata["metadata"]["packageID"],
        "target_version": metadata["deltaruneTargetVersion"],
        "chapters": chapters,
        "merge_support": metadata["metadata"]["mergeSupport"],
    }


def _build_one(
    *,
    chapters: list[int],
    payloads: dict[int, Path],
    output: Path,
    package_id: str,
) -> Path:
    return build_package(
        patches=[_spec(chapter, payloads[chapter]) for chapter in chapters],
        output=output,
        target_version=TARGET_VERSION,
        name=NAME,
        version=VERSION,
        description=DESCRIPTION,
        authors=[AUTHOR],
        url=URL,
        package_id=package_id,
        merge_support=True,
    )


def build_parser() -> argparse.ArgumentParser:
    speed_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Package ignored G3MTool intermediates into ZIP-only DeltaMod "
            "releases and write a text manifest beside the speed source."
        )
    )
    parser.add_argument(
        "--payload-directory",
        type=Path,
        default=speed_root / ".build" / "payloads",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=speed_root / "deltamod",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=speed_root / f"release_{VERSION}.json",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    speed_root = Path(__file__).resolve().parents[1]
    payload_directory = args.payload_directory.expanduser().resolve()
    output_directory = args.output_directory.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    source = speed_root / "AiSpeed.csx"
    provenance = _build_provenance(payload_directory, source)
    payloads = _payloads(payload_directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    packages: list[tuple[Path, list[int]]] = []
    all_chapters = list(SUPPORTED_CHAPTERS)
    packages.append(
        (
            _build_one(
                chapters=all_chapters,
                payloads=payloads,
                output=(
                    output_directory
                    / f"AI-Speed-All-Chapters-DeltaMod-v{VERSION}.zip"
                ),
                package_id=ALL_PACKAGE_ID,
            ),
            all_chapters,
        )
    )
    for chapter in all_chapters:
        packages.append(
            (
                _build_one(
                    chapters=[chapter],
                    payloads=payloads,
                    output=(
                        output_directory
                        / f"AI-Speed-Chapter-{chapter}-DeltaMod-v{VERSION}.zip"
                    ),
                    package_id=(
                        f"github.ai-speed-chapter{chapter}."
                        "gladiatorgaming789-byte"
                    ),
                ),
                [chapter],
            )
        )

    release = {
        "format": "DeltaMod ZIP-only release with ignored G3MTool intermediates",
        "minimum_g3mtool_version_for_multi_code_merge": "1.2.5",
        "deltarune_target_version": TARGET_VERSION,
        "supported_game_build": SUPPORTED_GAME_BUILD,
        "speed_mod_version": VERSION,
        "default_multiplier": 2,
        "supported_multipliers": list(range(1, 11)),
        "merge_support": True,
        "manual_source": source.name,
        "manual_source_sha256": _sha256(source),
        "intermediate_directory": ".build/payloads",
        "build_provenance": provenance,
        "chapter_payloads": {
            str(chapter): {
                "archive_name": payloads[chapter].name,
                "size": payloads[chapter].stat().st_size,
                "sha256": _sha256(payloads[chapter]),
            }
            for chapter in all_chapters
        },
        "clean_chapter_sha256": {
            str(chapter): checksum
            for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
        },
        "packages": [
            _archive_record(package, chapters)
            for package, chapters in packages
        ],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(release, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    for package, _ in packages:
        print(package)
    print(f"Release manifest: {manifest_path}")
    print(
        "Loose .g3mpatch files remain only in the ignored .build directory."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
