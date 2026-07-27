from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile

SUPPORTED_CHAPTERS = range(1, 6)
VCDIFF_MAGIC = b"\xd6\xc3\xc4\x00"
DEFAULT_NAME = "AI Plays Deltarune Telemetry"
DEFAULT_VERSION = "9.0.0"
DEFAULT_DESCRIPTION = (
    "Localhost-only runtime telemetry for the external AI Plays Deltarune "
    "controller."
)
DEFAULT_AUTHOR = "gladiatorgaming789-byte"
DEFAULT_URL = "https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune"
DEFAULT_PACKAGE_ID = "github.ai-plays-deltarune.gladiatorgaming789-byte"


@dataclass(frozen=True, order=True)
class PatchSpec:
    chapter: int
    source: Path

    @property
    def archive_name(self) -> str:
        return f"Chapter{self.chapter}DataPatch.xdelta"

    @property
    def destination(self) -> str:
        return f"./chapter{self.chapter}_windows/data.win"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_patch(value: str) -> PatchSpec:
    chapter_text, separator, path_text = value.partition("=")
    if not separator or not chapter_text or not path_text:
        raise argparse.ArgumentTypeError(
            "patches must use CHAPTER=PATH, for example "
            "5=Chapter5DataPatch.xdelta"
        )
    try:
        chapter = int(chapter_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "chapter must be an integer from 1 through 5"
        ) from exc
    if chapter not in SUPPORTED_CHAPTERS:
        raise argparse.ArgumentTypeError("chapter must be from 1 through 5")
    path = Path(path_text).expanduser()
    if path.suffix.lower() != ".xdelta":
        raise argparse.ArgumentTypeError(
            "DeltaMod telemetry patches must use .xdelta files"
        )
    return PatchSpec(chapter, path)


def _validated_specs(specs: list[PatchSpec]) -> list[PatchSpec]:
    if not specs:
        raise ValueError("at least one chapter patch is required")
    chapters: set[int] = set()
    validated: list[PatchSpec] = []
    for spec in sorted(specs):
        if spec.chapter not in SUPPORTED_CHAPTERS:
            raise ValueError(f"unsupported chapter: {spec.chapter}")
        if spec.chapter in chapters:
            raise ValueError(f"chapter {spec.chapter} was supplied more than once")
        chapters.add(spec.chapter)
        source = spec.source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"patch file does not exist: {source}")
        with source.open("rb") as stream:
            magic = stream.read(len(VCDIFF_MAGIC))
        if magic != VCDIFF_MAGIC:
            raise ValueError(f"patch is not a VCDIFF/xdelta stream: {source}")
        validated.append(PatchSpec(spec.chapter, source))
    return validated


def _metadata(
    *,
    specs: list[PatchSpec],
    target_version: str,
    name: str,
    version: str,
    description: str,
    authors: list[str],
    url: str,
    package_id: str,
) -> dict[str, object]:
    needed_files = [
        {
            "file": f"./{spec.archive_name}",
            "checksum": _sha256_file(spec.source),
        }
        for spec in specs
    ]
    return {
        "metadata": {
            "name": name,
            "version": version,
            "description": description,
            "author": authors,
            "url": url,
            "color": {"r": 65, "g": 145, "b": 255},
            "tags": ["utility", "other"],
            "game": "toby.deltarune",
            "packageID": package_id,
        },
        "deltaruneTargetVersion": target_version,
        "neededFiles": needed_files,
        "exporter": {"tool": "AI Plays Deltarune DeltaMod Builder"},
    }


def _modding_xml(specs: list[PatchSpec]) -> str:
    return "\n".join(
        f'<patch type="xdelta" patch="./{spec.archive_name}" '
        f'to="{spec.destination}"/>'
        for spec in specs
    ) + "\n"


def build_package(
    *,
    patches: list[PatchSpec],
    output: Path,
    target_version: str,
    name: str = DEFAULT_NAME,
    version: str = DEFAULT_VERSION,
    description: str = DEFAULT_DESCRIPTION,
    authors: list[str] | None = None,
    url: str = DEFAULT_URL,
    package_id: str = DEFAULT_PACKAGE_ID,
) -> Path:
    specs = _validated_specs(patches)
    target_version = target_version.strip()
    if not target_version:
        raise ValueError("target_version cannot be blank")
    cleaned_authors = [
        author.strip()
        for author in (authors or [DEFAULT_AUTHOR])
        if author.strip()
    ]
    if not cleaned_authors:
        raise ValueError("at least one author is required")

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_name(f".{output.name}.tmp")
    temporary_output.unlink(missing_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="deltamod-telemetry-") as temp:
            root = Path(temp)
            for spec in specs:
                shutil.copyfile(spec.source, root / spec.archive_name)
            (root / "meta.json").write_text(
                json.dumps(
                    _metadata(
                        specs=specs,
                        target_version=target_version,
                        name=name,
                        version=version,
                        description=description,
                        authors=cleaned_authors,
                        url=url,
                        package_id=package_id,
                    ),
                    indent=4,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "modding.xml").write_text(
                _modding_xml(specs), encoding="utf-8"
            )
            with zipfile.ZipFile(
                temporary_output,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
                    archive.write(path, arcname=path.name)
        validate_package(temporary_output)
        temporary_output.replace(output)
    finally:
        temporary_output.unlink(missing_ok=True)
    return output


def validate_package(package: Path) -> None:
    package = package.expanduser().resolve()
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("package contains duplicate archive entries")
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 1:
                raise ValueError(
                    "all DeltaMod package files must be at the ZIP root"
                )
        required = {"meta.json", "modding.xml"}
        if not required.issubset(names):
            missing = ", ".join(sorted(required.difference(names)))
            raise ValueError(f"package is missing required file(s): {missing}")

        metadata = json.loads(archive.read("meta.json").decode("utf-8"))
        details = metadata.get("metadata")
        if not isinstance(details, dict) or details.get("game") != "toby.deltarune":
            raise ValueError("meta.json must target toby.deltarune")
        needed_files = metadata.get("neededFiles")
        if not isinstance(needed_files, list) or not needed_files:
            raise ValueError("meta.json must contain neededFiles entries")

        expected_patches: set[str] = set()
        for item in needed_files:
            if not isinstance(item, dict):
                raise ValueError("neededFiles entries must be objects")
            filename = str(item.get("file", ""))
            checksum = str(item.get("checksum", ""))
            if not filename.startswith("./"):
                raise ValueError("neededFiles paths must begin with ./")
            archive_name = filename[2:]
            if archive_name not in names:
                raise ValueError(
                    f"neededFiles entry is absent from the ZIP: {archive_name}"
                )
            payload = archive.read(archive_name)
            if not payload.startswith(VCDIFF_MAGIC):
                raise ValueError(f"patch is not VCDIFF/xdelta: {archive_name}")
            if checksum.lower() != _sha256_bytes(payload):
                raise ValueError(f"checksum mismatch for {archive_name}")
            expected_patches.add(archive_name)

        declared_patches: set[str] = set()
        lines = archive.read("modding.xml").decode("utf-8").splitlines()
        for line in (line.strip() for line in lines if line.strip()):
            if not line.startswith('<patch type="xdelta" ') or not line.endswith("/>"):
                raise ValueError(
                    "modding.xml contains an unsupported patch instruction"
                )
            marker = 'patch="./'
            start = line.find(marker)
            end = line.find('"', start + len(marker))
            if start < 0 or end < 0:
                raise ValueError("modding.xml patch path is invalid")
            patch_name = line[start + len(marker) : end]
            if patch_name not in names:
                raise ValueError(
                    f"modding.xml references a missing patch: {patch_name}"
                )
            valid_destination = any(
                f'to="./chapter{chapter}_windows/data.win"' in line
                for chapter in SUPPORTED_CHAPTERS
            )
            if not valid_destination:
                raise ValueError(
                    "modding.xml contains an invalid chapter destination"
                )
            declared_patches.add(patch_name)

        if declared_patches != expected_patches:
            raise ValueError(
                "meta.json and modding.xml reference different patch files"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a DeltaMod-compatible telemetry ZIP from chapter xdelta patches."
        )
    )
    parser.add_argument(
        "--patch",
        action="append",
        required=True,
        type=_parse_patch,
        metavar="CHAPTER=PATH",
        help="chapter number and xdelta file; repeat for multiple chapters",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--author", action="append", dest="authors")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--package-id", default=DEFAULT_PACKAGE_ID)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = build_package(
        patches=args.patch,
        output=args.output,
        target_version=args.target_version,
        name=args.name,
        version=args.version,
        description=args.description,
        authors=args.authors,
        url=args.url,
        package_id=args.package_id,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
