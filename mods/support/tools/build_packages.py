from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from mods.tools.deltamod_csx_package_impl import (
    SUPPORTED_CHAPTERS,
    build_csx_package,
    canonical_csx_file_bytes,
    sha256_file,
    validate_csx_bytes,
    validate_csx_package,
)


VERSION = "1.0.0"
SPEED_COMPONENT_VERSION = "1.3.1"
TELEMETRY_COMPONENT_VERSION = "9.2.1"
TELEMETRY_PROTOCOL = 9
NAME = "AI Plays Deltarune Support"
DESCRIPTION = (
    "Atomic direct-CSX installer for AI speed controls and localhost telemetry. "
    "Both components are compiled in one UndertaleModTool invocation so DeltaMod "
    "cannot overwrite one component with the other from the same data.win backup."
)
AUTHOR = "gladiatorgaming789-byte"
URL = "https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune"
PACKAGE_ID = "github.ai-support.gladiatorgaming789-byte"


def _clean_hashes(path: Path | None, chapters: tuple[int, ...]) -> dict[int, str] | None:
    if path is None:
        return None
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Clean hash map must be a JSON object")
    hashes = {int(chapter): str(checksum) for chapter, checksum in payload.items()}
    if set(hashes) != set(chapters):
        raise RuntimeError(
            "Clean hash map must contain exactly the chapters being packaged"
        )
    return hashes


def _component_body(path: Path) -> str:
    text = canonical_csx_file_bytes(path).decode("utf-8")
    body: list[str] = []
    removed_ensure = False
    for line in text.splitlines():
        stripped = line.strip()
        if line.startswith("using ") and stripped.endswith(";"):
            continue
        if stripped == "EnsureDataLoaded();" and not removed_ensure:
            removed_ensure = True
            continue
        body.append(line)
    if not removed_ensure:
        raise RuntimeError(f"Component source did not contain EnsureDataLoaded(): {path}")
    return "\n".join(body).strip() + "\n"


def _indent(text: str) -> str:
    return "\n".join(("    " + line) if line else "" for line in text.splitlines())


def combined_source_bytes(speed_source: Path, telemetry_source: Path) -> bytes:
    speed_body = _component_body(speed_source)
    telemetry_body = _component_body(telemetry_source)
    source = (
        "using System;\n"
        "using System.Linq;\n"
        "using UndertaleModLib.Compiler;\n\n"
        "EnsureDataLoaded();\n\n"
        "void InstallAiSpeed()\n"
        "{\n"
        f"{_indent(speed_body)}\n"
        "}\n\n"
        "void InstallAiTelemetry()\n"
        "{\n"
        f"{_indent(telemetry_body)}\n"
        "}\n\n"
        "InstallAiSpeed();\n"
        "InstallAiTelemetry();\n"
    )
    payload = source.encode("utf-8")
    validate_csx_bytes(payload, label="generated AiSupport.csx")
    if source.count("AI_SPEED_MOD|1|") < 2:
        raise RuntimeError("Generated support source lost the speed marker")
    if source.count("DRTEL|9|") < 2:
        raise RuntimeError("Generated support source lost telemetry protocol v9")
    return payload


def build_parser() -> argparse.ArgumentParser:
    support_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Build the atomic DeltaMod Speed + Telemetry CSX package."
    )
    parser.add_argument("--target-version", required=True)
    parser.add_argument(
        "--chapter",
        type=int,
        action="append",
        choices=SUPPORTED_CHAPTERS,
        help="chapter to include; repeat as needed (default: Chapters 1-5)",
    )
    parser.add_argument("--clean-hashes", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            support_root
            / "deltamod"
            / f"AI-Support-All-Chapters-DeltaMod-CSX-v{VERSION}.zip"
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=support_root / f"release_{VERSION}.json",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    support_root = Path(__file__).resolve().parents[1]
    speed_source = REPOSITORY_ROOT / "mods" / "speed" / "AiSpeed.csx"
    telemetry_source = REPOSITORY_ROOT / "mods" / "telemetry" / "AiTelemetry.csx"
    chapters = tuple(sorted(args.chapter or SUPPORTED_CHAPTERS))
    if len(chapters) != len(set(chapters)):
        raise RuntimeError("A chapter was selected more than once")
    hashes = _clean_hashes(args.clean_hashes, chapters)
    combined_bytes = combined_source_bytes(speed_source, telemetry_source)

    with tempfile.TemporaryDirectory(prefix="deltarune-ai-support-source-") as temp_dir:
        generated_source = Path(temp_dir) / "AiSupport.csx"
        generated_source.write_bytes(combined_bytes)
        package = build_csx_package(
            script=generated_source,
            chapters=chapters,
            output=args.output,
            target_version=args.target_version,
            payload_label="Support",
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
        "format": "DeltaMod atomic combined direct-CSX source package",
        "status": "source-composition validation passed; DeltaMod runtime verification pending",
        "version": VERSION,
        "speed_component_version": SPEED_COMPONENT_VERSION,
        "telemetry_component_version": TELEMETRY_COMPONENT_VERSION,
        "telemetry_protocol": TELEMETRY_PROTOCOL,
        "target_version": args.target_version,
        "chapters": list(chapters),
        "merge_support": True,
        "speed_source": speed_source.name,
        "speed_source_sha256": hashlib.sha256(canonical_csx_file_bytes(speed_source)).hexdigest(),
        "telemetry_source": telemetry_source.name,
        "telemetry_source_sha256": hashlib.sha256(canonical_csx_file_bytes(telemetry_source)).hexdigest(),
        "combined_source_sha256": hashlib.sha256(combined_bytes).hexdigest(),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
