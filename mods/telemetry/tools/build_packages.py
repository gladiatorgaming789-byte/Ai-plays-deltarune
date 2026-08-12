from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from mods.tools.deltamod_csx_loader import (
    SUPPORTED_CHAPTERS,
    build_csx_package,
    sha256_csx_file,
    sha256_file,
    validate_csx_package,
)


VERSION = "9.2.1"
TELEMETRY_PROTOCOL = 9
NAME = "AI Plays Deltarune Telemetry"
DESCRIPTION = (
    "Direct-CSX localhost-only telemetry protocol v9 for the external AI "
    "controller. DeltaMod executes the source installer separately for each "
    "chapter, avoiding compiled GameMaker variable-table merges."
)
AUTHOR = "gladiatorgaming789-byte"
URL = "https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune"
PACKAGE_ID = "github.ai-telemetry.gladiatorgaming789-byte"


def _clean_hashes(path: Path | None, chapters: tuple[int, ...]) -> dict[int, str] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read clean hash map: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Clean hash map must be a JSON object")
    try:
        hashes = {int(chapter): str(checksum) for chapter, checksum in payload.items()}
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Clean hash map keys must be chapter numbers") from exc
    if set(hashes) != set(chapters):
        raise RuntimeError(
            "Clean hash map must contain exactly the chapters being packaged"
        )
    return hashes


def build_parser() -> argparse.ArgumentParser:
    telemetry_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description=(
            "Build a DeltaMod direct-CSX telemetry package. The target version "
            "is required so an outdated game version is never guessed."
        )
    )
    parser.add_argument(
        "--target-version",
        required=True,
        help="exact Deltarune version expected by the installed DeltaMod build",
    )
    parser.add_argument(
        "--chapter",
        type=int,
        action="append",
        choices=SUPPORTED_CHAPTERS,
        help="chapter to include; repeat as needed (default: Chapters 1-5)",
    )
    parser.add_argument(
        "--clean-hashes",
        type=Path,
        help=(
            "optional JSON object mapping included chapter numbers to freshly "
            "measured clean data.win SHA-256 values"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            telemetry_root
            / "deltamod"
            / f"Telemetry-All-Chapters-DeltaMod-CSX-v{VERSION}.zip"
        ),
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
    chapters = tuple(sorted(args.chapter or SUPPORTED_CHAPTERS))
    if len(chapters) != len(set(chapters)):
        raise RuntimeError("A chapter was selected more than once")
    source = telemetry_root / "AiTelemetry.csx"
    hashes = _clean_hashes(args.clean_hashes, chapters)
    package = build_csx_package(
        script=source,
        chapters=chapters,
        output=args.output,
        target_version=args.target_version,
        payload_label="Telemetry",
        name=NAME,
        version=VERSION,
        description=DESCRIPTION,
        authors=[AUTHOR],
        url=URL,
        package_id=PACKAGE_ID,
        clean_hashes=hashes,
        merge_support=True,
    )
    validation = validate_csx_package(package, expected_chapters=chapters)
    with zipfile.ZipFile(package) as archive:
        root_entries = archive.namelist()

    release = {
        "format": "DeltaMod direct-CSX source package",
        "status": "source-level migration; runtime verification pending",
        "reason": (
            "Compiled speed and telemetry packages could corrupt shared "
            "GameMaker variable indexes when enabled together."
        ),
        "telemetry_mod_version": VERSION,
        "telemetry_protocol": TELEMETRY_PROTOCOL,
        "target_version": args.target_version,
        "chapters": list(chapters),
        "merge_support": True,
        "source": source.name,
        "source_sha256": sha256_csx_file(source),
        "clean_hashes_included": hashes is not None,
        "package": {
            "file": package.name,
            "size": package.stat().st_size,
            "sha256": sha256_file(package),
            "root_entries": root_entries,
            **validation,
        },
    }
    manifest = args.manifest.expanduser().resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(release, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(package)
    print(f"Release manifest: {manifest}")
    if hashes is None:
        print(
            "Compatibility hashes were intentionally omitted. Verify the CSX "
            "package against refreshed clean chapter files before release."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
