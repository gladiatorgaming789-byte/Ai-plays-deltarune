from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
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


VERSION = "9.3.1"
TELEMETRY_PROTOCOL = 9
NAME = "AI Plays Deltarune Telemetry"
DESCRIPTION = (
    "Direct-CSX telemetry v9 with independent-process UDP ports, visible AI "
    "identities, isolated training saves, and training-only background autosave."
)
AUTHOR = "gladiatorgaming789-byte"
URL = "https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune"
PACKAGE_ID = "github.ai-telemetry.gladiatorgaming789-byte"


# v9.3.0 could already contain DRTEL v9 + multi-instance support while still
# carrying the old, unsafe startup autosave. The original installer returned
# early on those markers, which meant importing 9.3.1 could never add the V2
# safety fix. Keep the source file readable, but harden the materialized package
# so a stale v9 installation is explicitly rejected and must be rebuilt from a
# clean data.win rather than layered with another autosave hook.
_LEGACY_GUARD = '''const string marker = "DRTEL|9|";
const string instanceMarker = "AI_MULTI_INSTANCE|1|";
bool hasTelemetry = Data.Strings.Any(item => item.Content.Contains(marker));
bool hasInstanceSupport = Data.Strings.Any(item => item.Content.Contains(instanceMarker));
if (hasTelemetry && hasInstanceSupport)
{
    ScriptMessage("AI telemetry v9 with multi-instance support is already present. No changes were made.");
    return;
}
'''

_HARDENED_GUARD = '''const string marker = "DRTEL|9|";
const string instanceMarker = "AI_MULTI_INSTANCE|1|";
const string safeAutosaveMarker = "AI_BACKGROUND_AUTOSAVE_V2";
bool hasTelemetry = Data.Strings.Any(item => item.Content.Contains(marker));
bool hasInstanceSupport = Data.Strings.Any(item => item.Content.Contains(instanceMarker));
bool hasSafeAutosave = Data.Strings.Any(item => item.Content.Contains(safeAutosaveMarker));
if (hasTelemetry && hasInstanceSupport && hasSafeAutosave)
{
    ScriptMessage("AI telemetry v9.3.1 with multi-instance support and training-only autosave safety is already present. No changes were made.");
    return;
}
if (hasTelemetry && hasInstanceSupport && !hasSafeAutosave)
{
    throw new Exception(
        "An older AI telemetry v9 installation is present without AI_BACKGROUND_AUTOSAVE_V2. " +
        "This is the withdrawn startup-autosave build. Restore the clean data.win before " +
        "importing Telemetry 9.3.1 / AI Support 2.0.1 so the unsafe autosave hook cannot be layered."
    );
}
'''


def _hardened_source(source: Path) -> tuple[str, str]:
    text = source.read_text(encoding="utf-8")
    if _LEGACY_GUARD not in text:
        raise RuntimeError(
            "Telemetry source guard changed unexpectedly; refusing to materialize "
            "9.3.1 without explicitly verifying stale-v9 upgrade behavior."
        )
    return text.replace(_LEGACY_GUARD, _HARDENED_GUARD, 1), text


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
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--chapter", type=int, action="append", choices=SUPPORTED_CHAPTERS)
    parser.add_argument("--clean-hashes", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=telemetry_root / "deltamod" / f"Telemetry-All-Chapters-DeltaMod-CSX-v{VERSION}.zip",
    )
    parser.add_argument("--manifest", type=Path, default=telemetry_root / f"release_{VERSION}.json")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    telemetry_root = Path(__file__).resolve().parents[1]
    chapters = tuple(sorted(args.chapter or SUPPORTED_CHAPTERS))
    if len(chapters) != len(set(chapters)):
        raise RuntimeError("A chapter was selected more than once")
    source = telemetry_root / "AiTelemetry.csx"
    hashes = _clean_hashes(args.clean_hashes, chapters)
    hardened_source, original_source = _hardened_source(source)

    with tempfile.TemporaryDirectory(prefix="deltarune-ai-telemetry-source-") as temp_dir:
        materialized_source = Path(temp_dir) / "AiTelemetry.csx"
        materialized_source.write_text(hardened_source, encoding="utf-8", newline="\n")
        package = build_csx_package(
            script=materialized_source,
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
        )

    validation = validate_csx_package(package, expected_chapters=chapters)
    with zipfile.ZipFile(package) as archive:
        root_entries = archive.namelist()
        payloads = [
            archive.read(name)
            for name in root_entries
            if name.startswith("Chapter") and name.endswith("Telemetry.csx")
        ]
        if not payloads or not all(b"AI_BACKGROUND_AUTOSAVE_V2" in payload for payload in payloads):
            raise RuntimeError("Telemetry package lost the training-only autosave v2 safety marker")
        if not payloads or not all(b"withdrawn startup-autosave build" in payload for payload in payloads):
            raise RuntimeError("Telemetry package lost stale-v9 migration protection")

    release = {
        "format": "DeltaMod direct-CSX source package",
        "status": "source-level validation passed; runtime verification pending",
        "reason": (
            "v9.3.1 restricts the invisible startup checkpoint to named multi-instance "
            "training processes and explicitly rejects the withdrawn v9 startup-autosave "
            "installation instead of layering a second hook."
        ),
        "telemetry_mod_version": VERSION,
        "telemetry_protocol": TELEMETRY_PROTOCOL,
        "target_version": args.target_version,
        "chapters": list(chapters),
        "merge_support": True,
        "source": source.name,
        "source_sha256": sha256_csx_file(source),
        "materialized_source_sha256": __import__("hashlib").sha256(hardened_source.encode("utf-8")).hexdigest(),
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
    manifest.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(package)
    print(f"Release manifest: {manifest}")
    if hashes is None:
        print("Compatibility hashes were intentionally omitted. Verify the CSX package against refreshed clean chapter files before release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
