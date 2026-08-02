from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deltarune_agent.deltamod_package import (
    DELTARUNE_CURRENT_HASHES,
    DELTARUNE_CURRENT_MD5,
    DELTARUNE_PATCH_LABEL,
    DELTARUNE_STEAM_BUILD_ID,
    PatchSpec,
    build_package,
    validate_package,
)


VERSION = "9.1.0"
TARGET_VERSION = "1.05"
NAME = "AI Plays Deltarune Telemetry"
DESCRIPTION = (
    "Localhost-only telemetry protocol v9 for the external AI Plays "
    f"Deltarune controller, rebuilt for {DELTARUNE_PATCH_LABEL} (Steam build "
    f"{DELTARUNE_STEAM_BUILD_ID}). Separate speed and telemetry merging "
    "requires G3MTool 1.2.5 or newer."
)
AUTHOR = "gladiatorgaming789-byte"
URL = "https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune"
PACKAGE_ID = "github.ai-telemetry.gladiatorgaming789-byte"
MINIMUM_G3MTOOL_VERSION = (1, 2, 5)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _payloads(directory: Path) -> dict[int, Path]:
    payloads = {
        chapter: directory / f"Chapter{chapter}Telemetry.g3mpatch"
        for chapter in range(1, 6)
    }
    missing = [str(path) for path in payloads.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Missing temporary telemetry payloads. Run build_payloads.py first:\n"
            + "\n".join(missing)
        )
    return payloads


def _validated_provenance(
    telemetry_root: Path,
    payload_directory: Path,
    payloads: dict[int, Path],
) -> dict[str, object]:
    record_path = payload_directory.parent / "payloads.json"
    if not record_path.is_file():
        raise FileNotFoundError(
            "Missing payload provenance. Re-run build_payloads.py before "
            "packaging."
        )
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Telemetry payload provenance is invalid") from exc
    if not isinstance(record, dict) or record.get("record_version") != 1:
        raise RuntimeError("Unsupported telemetry payload provenance version")
    if record.get("steam_build_id") != DELTARUNE_STEAM_BUILD_ID:
        raise RuntimeError(
            "Telemetry payloads target a stale Steam build; rebuild them"
        )
    if record.get("telemetry_protocol") != 9:
        raise RuntimeError("Telemetry payload protocol does not match v9")
    script = telemetry_root / "AiTelemetry.csx"
    if (
        record.get("manual_source") != script.name
        or record.get("manual_source_sha256") != _sha256(script)
    ):
        raise RuntimeError(
            "AiTelemetry.csx changed after payload generation; rebuild payloads"
        )
    version_match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)",
        str(record.get("g3mtool_version", "")),
    )
    if version_match is None or tuple(
        int(part) for part in version_match.groups()
    ) < MINIMUM_G3MTOOL_VERSION:
        raise RuntimeError(
            "Telemetry payloads were not built with merge-safe G3MTool 1.2.5+"
        )

    chapter_records = record.get("chapters")
    if not isinstance(chapter_records, list):
        raise RuntimeError("Telemetry provenance has no chapter records")
    by_chapter = {
        item.get("chapter"): item
        for item in chapter_records
        if isinstance(item, dict)
    }
    if (
        len(chapter_records) != 5
        or len(by_chapter) != 5
        or set(by_chapter) != set(range(1, 6))
    ):
        raise RuntimeError("Telemetry provenance must cover Chapters 1-5")
    for chapter, payload in payloads.items():
        item = by_chapter[chapter]
        if (
            item.get("clean_sha256") != DELTARUNE_CURRENT_HASHES[chapter]
            or item.get("clean_md5") != DELTARUNE_CURRENT_MD5[chapter]
            or item.get("payload_size") != payload.stat().st_size
            or item.get("payload_sha256") != _sha256(payload)
        ):
            raise RuntimeError(
                f"Chapter {chapter} payload does not match current provenance"
            )
    return record


def build_parser() -> argparse.ArgumentParser:
    telemetry_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Package ignored telemetry G3MTool intermediates into one "
            "ZIP-only Chapters 1-5 DeltaMod release."
        )
    )
    parser.add_argument(
        "--payload-directory",
        type=Path,
        default=telemetry_root / ".build" / "payloads",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=telemetry_root / "deltamod",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=telemetry_root / f"release_{VERSION}.json",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    telemetry_root = Path(__file__).resolve().parents[1]
    payload_directory = args.payload_directory.expanduser().resolve()
    output_directory = args.output_directory.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    payloads = _payloads(payload_directory)
    provenance = _validated_provenance(
        telemetry_root,
        payload_directory,
        payloads,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    output = (
        output_directory / f"Telemetry-All-Chapters-DeltaMod-v{VERSION}.zip"
    )
    package = build_package(
        patches=[
            PatchSpec(
                chapter,
                payloads[chapter],
                DELTARUNE_CURRENT_HASHES[chapter],
                f"Chapter{chapter}Telemetry.g3mpatch",
            )
            for chapter in range(1, 6)
        ],
        output=output,
        target_version=TARGET_VERSION,
        name=NAME,
        version=VERSION,
        description=DESCRIPTION,
        authors=[AUTHOR],
        url=URL,
        package_id=PACKAGE_ID,
        merge_support=True,
    )
    validate_package(package)
    with zipfile.ZipFile(package) as archive:
        metadata = json.loads(archive.read("meta.json"))
        root_entries = archive.namelist()

    source = telemetry_root / "AiTelemetry.csx"
    release = {
        "format": "DeltaMod ZIP-only release with ignored G3MTool intermediates",
        "steam_build_id": DELTARUNE_STEAM_BUILD_ID,
        "deltarune_target_version": TARGET_VERSION,
        "telemetry_mod_version": VERSION,
        "telemetry_protocol": 9,
        "minimum_g3mtool_version_for_speed_merge": "1.2.5",
        "merge_support": True,
        "manual_source": source.name,
        "manual_source_sha256": _sha256(source),
        "clean_chapter_sha256": {
            str(chapter): checksum
            for chapter, checksum in DELTARUNE_CURRENT_HASHES.items()
        },
        "chapter_payloads": {
            str(chapter): {
                "archive_name": payloads[chapter].name,
                "size": payloads[chapter].stat().st_size,
                "sha256": _sha256(payloads[chapter]),
            }
            for chapter in range(1, 6)
        },
        "package": {
            "file": package.name,
            "size": package.stat().st_size,
            "sha256": _sha256(package),
            "root_entries": root_entries,
            "package_id": metadata["metadata"]["packageID"],
            "target_version": metadata["deltaruneTargetVersion"],
            "chapters": list(range(1, 6)),
            "merge_support": metadata["metadata"]["mergeSupport"],
        },
        "payload_provenance": {
            "record_version": provenance["record_version"],
            "g3mtool_version": provenance["g3mtool_version"],
        },
    }
    manifest_path.write_text(
        json.dumps(release, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(package)
    print(f"Release manifest: {manifest_path}")
    print("Loose .g3mpatch files remain only in the ignored .build directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
