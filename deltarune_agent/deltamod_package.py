from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import zipfile


SUPPORTED_CHAPTERS = range(1, 6)
VCDIFF_MAGIC = b"\xd6\xc3\xc4\x00"
SUPPORTED_PAYLOAD_SUFFIXES = {".xdelta", ".vcdiff", ".g3mpatch"}
PACKAGE_ID_PATTERN = re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+\.[a-z0-9-]+$")
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
MD5_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
PATCH_LINE_PATTERN = re.compile(
    r'^<patch type="(xdelta|g3mpatch)" patch="\./([^"/]+)" '
    r'to="\./chapter([1-5])_windows/data\.win"/>$'
)

DELTARUNE_STEAM_BUILD_ID = "24484059"
DELTARUNE_PATCH_LABEL = "Chapter 5 v0.0.253"

# Verified clean Steam data.win hashes for the current supported build. These
# identify the installed input files DeltaMod is allowed to patch; they are
# deliberately independent from both package payload hashes and the stale
# data.win.hash sidecars shipped for Chapters 2-5 in this build.
DELTARUNE_CURRENT_HASHES = {
    1: "82c2bb61b8d78cd287120f6301588fecba34ec5a890bac711b7a8774c760ec70",
    2: "047c5ab003e3e017a709c02757e119c81e0327760169512110fd276b19241e68",
    3: "c1a0925343694ec9b9adcbf2f916a720b02fd1b999286cfe8fe6a52f3320f714",
    4: "ed64789586238b52375e994e1c1cf13694dd2d0dab57d13e639b9c892e37d8f2",
    5: "370dfd141d2955d5a1960122919b16e4092b52ffbb85fda541bc4680c6b3b85c",
}
DELTARUNE_CURRENT_MD5 = {
    1: "0ccbfd7c4f9fb1b86de1e2aaec0bacc9",
    2: "1592c9bffa2d9e53ddeedc0c4f9a07d6",
    3: "b43158db2e958e767ebb1aae72fb05a1",
    4: "27e36f883f4ade21707dc8261072d416",
    5: "9c80e6300e0548d933cc006f3c22760d",
}

# Compatibility aliases for older build scripts. The public game version is
# still reported to DeltaMod as 1.05, but these values now identify Steam build
# 24484059 rather than the older files originally associated with these names.
DELTARUNE_105_HASHES = DELTARUNE_CURRENT_HASHES
DELTARUNE_105_MD5 = DELTARUNE_CURRENT_MD5

DEFAULT_NAME = "AI Plays Deltarune Telemetry"
DEFAULT_VERSION = "9.1.0"
DEFAULT_DESCRIPTION = (
    "Localhost-only runtime telemetry for the external AI Plays Deltarune "
    "controller."
)
DEFAULT_AUTHOR = "gladiatorgaming789-byte"
DEFAULT_URL = "https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune"
DEFAULT_PACKAGE_ID = "github.ai-telemetry.gladiatorgaming789-byte"


@dataclass(frozen=True)
class PatchSpec:
    chapter: int
    source: Path
    clean_sha256: str | None = None
    archive_name_override: str | None = None

    @property
    def archive_name(self) -> str:
        if self.archive_name_override:
            return self.archive_name_override
        suffix = self.source.suffix.casefold()
        return f"Chapter{self.chapter}DataPatch{suffix}"

    @property
    def destination(self) -> str:
        return f"./chapter{self.chapter}_windows/data.win"

    @property
    def source_checksum(self) -> str:
        return (
            self.clean_sha256
            or DELTARUNE_105_HASHES.get(self.chapter)
            or ""
        ).casefold()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_package_id(package_id: str) -> str:
    package_id = package_id.strip().casefold()
    if not PACKAGE_ID_PATTERN.fullmatch(package_id):
        raise ValueError(
            "package_id must contain exactly three dot-separated lowercase "
            "segments using letters, numbers, or hyphens"
        )
    return package_id


def _validate_g3mpatch_archive(
    archive: zipfile.ZipFile,
    *,
    label: str,
) -> dict[str, object]:
    names = archive.namelist()
    if len(names) != len(set(names)):
        raise ValueError(f"G3M patch contains duplicate entries: {label}")
    for name in names:
        pure = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or pure.is_absolute()
            or ".." in pure.parts
        ):
            raise ValueError(f"G3M patch contains an unsafe path: {label}")
    if "g3mpatch.json" not in names:
        raise ValueError(f"G3M patch is missing g3mpatch.json: {label}")
    corrupt_entry = archive.testzip()
    if corrupt_entry is not None:
        raise ValueError(
            f"G3M patch has a corrupt ZIP entry {corrupt_entry!r}: {label}"
        )
    try:
        manifest = json.loads(
            archive.read("g3mpatch.json").decode("utf-8-sig")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"G3M patch manifest is invalid: {label}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"G3M patch manifest must be an object: {label}")

    tool = manifest.get("tool")
    if (
        not isinstance(tool, dict)
        or str(tool.get("name", "")).casefold() != "g3mtool"
        or not str(tool.get("version", "")).strip()
    ):
        raise ValueError(
            f"G3M patch lacks G3MTool creator metadata: {label}"
        )
    for side in ("original", "modified"):
        details = manifest.get(side)
        if (
            not isinstance(details, dict)
            or not isinstance(details.get("size"), int)
            or details["size"] <= 0
            or not MD5_PATTERN.fullmatch(str(details.get("md5", "")))
        ):
            raise ValueError(
                f"G3M patch has invalid {side} file metadata: {label}"
            )

    resources = manifest.get("resources")
    if not isinstance(resources, dict) or not resources:
        raise ValueError(f"G3M patch has no resource changes: {label}")
    referenced_files: set[str] = set()
    change_count = 0
    for resource_type, changes in resources.items():
        if not isinstance(resource_type, str) or not isinstance(changes, dict):
            raise ValueError(
                f"G3M patch has invalid resource metadata: {label}"
            )
        for disposition in ("changed", "new", "deleted"):
            entries = changes.get(disposition)
            if not isinstance(entries, list):
                raise ValueError(
                    f"G3M patch resource list is invalid: {label}"
                )
            change_count += len(entries)
            for entry in entries:
                if not isinstance(entry, dict) or not str(
                    entry.get("name", "")
                ).strip():
                    raise ValueError(
                        f"G3M patch resource entry is invalid: {label}"
                    )
                files = entry.get("files", {})
                if not isinstance(files, dict):
                    raise ValueError(
                        f"G3M patch resource files are invalid: {label}"
                    )
                for archive_name in files.values():
                    if not isinstance(archive_name, str):
                        raise ValueError(
                            f"G3M patch resource path is invalid: {label}"
                        )
                    referenced_files.add(archive_name)
    if change_count == 0:
        raise ValueError(f"G3M patch has no resource changes: {label}")
    missing = referenced_files.difference(names)
    if missing:
        raise ValueError(
            "G3M patch references missing resource files "
            f"({', '.join(sorted(missing))}): {label}"
        )

    apply_plan = manifest.get("applyPlan")
    if (
        not isinstance(apply_plan, dict)
        or not str(apply_plan.get("mode", "")).strip()
        or not isinstance(apply_plan.get("requiresCodePipeline"), bool)
        or not isinstance(apply_plan.get("requiresTexturePipeline"), bool)
    ):
        raise ValueError(f"G3M patch apply plan is invalid: {label}")
    return manifest


def _validate_g3mpatch_bytes(
    payload: bytes,
    *,
    label: str,
) -> dict[str, object]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return _validate_g3mpatch_archive(archive, label=label)
    except zipfile.BadZipFile as exc:
        raise ValueError(
            f"patch is not a valid G3M patch ZIP archive: {label}"
        ) from exc


def _validate_payload(path: Path) -> dict[str, object] | None:
    suffix = path.suffix.casefold()
    if suffix not in SUPPORTED_PAYLOAD_SUFFIXES:
        raise ValueError(
            f"unsupported DeltaMod merge payload {path.name}; use "
            ".g3mpatch, .xdelta, or .vcdiff (raw .csx is manual-install only)"
        )
    if suffix in {".xdelta", ".vcdiff"}:
        with path.open("rb") as stream:
            magic = stream.read(len(VCDIFF_MAGIC))
        if magic != VCDIFF_MAGIC:
            raise ValueError(f"patch is not a VCDIFF/xdelta stream: {path}")
        return None
    return _validate_g3mpatch_bytes(path.read_bytes(), label=str(path))


def _parse_patch(value: str) -> PatchSpec:
    chapter_text, separator, path_text = value.partition("=")
    if not separator or not chapter_text or not path_text:
        raise argparse.ArgumentTypeError(
            "patches must use CHAPTER=PATH, for example "
            "5=Chapter5Speed.g3mpatch"
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
    if path.suffix.casefold() not in SUPPORTED_PAYLOAD_SUFFIXES:
        raise argparse.ArgumentTypeError(
            "DeltaMod patches must use .g3mpatch, .xdelta, or .vcdiff files"
        )
    return PatchSpec(chapter, path)


def _validated_specs(specs: list[PatchSpec]) -> list[PatchSpec]:
    if not specs:
        raise ValueError("at least one chapter patch is required")
    chapters: set[int] = set()
    validated: list[PatchSpec] = []
    for spec in sorted(specs, key=lambda item: item.chapter):
        if spec.chapter not in SUPPORTED_CHAPTERS:
            raise ValueError(f"unsupported chapter: {spec.chapter}")
        if spec.chapter in chapters:
            raise ValueError(f"chapter {spec.chapter} was supplied more than once")
        chapters.add(spec.chapter)
        source = spec.source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"patch file does not exist: {source}")
        patch_manifest = _validate_payload(source)
        if patch_manifest is not None:
            original = patch_manifest["original"]
            assert isinstance(original, dict)
            actual_md5 = str(original["md5"]).casefold()
            expected_md5 = DELTARUNE_105_MD5[spec.chapter]
            if actual_md5 != expected_md5:
                raise ValueError(
                    f"{source.name} targets a different clean data.win "
                    f"than chapter {spec.chapter}"
                )
        checksum = spec.source_checksum
        if not SHA256_PATTERN.fullmatch(checksum):
            raise ValueError(
                f"chapter {spec.chapter} requires a valid clean data.win SHA-256"
            )
        archive_name = spec.archive_name_override
        if archive_name is not None:
            pure = PurePosixPath(archive_name)
            if (
                pure.is_absolute()
                or len(pure.parts) != 1
                or pure.suffix.casefold() != source.suffix.casefold()
            ):
                raise ValueError("archive_name_override must be one root-level payload name")
        validated.append(
            PatchSpec(
                spec.chapter,
                source,
                checksum,
                archive_name,
            )
        )

    by_name: dict[str, Path] = {}
    for spec in validated:
        previous = by_name.get(spec.archive_name)
        if previous is None:
            by_name[spec.archive_name] = spec.source
        elif _sha256_file(previous) != _sha256_file(spec.source):
            raise ValueError(
                f"multiple different payloads use archive name {spec.archive_name}"
            )
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
    merge_support: bool,
) -> dict[str, object]:
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
            "mergeSupport": merge_support,
        },
        "deltaruneTargetVersion": target_version,
        "neededFiles": [
            {
                "file": spec.destination,
                "checksum": spec.source_checksum,
            }
            for spec in specs
        ],
        "exporter": {"tool": "AI Plays Deltarune DeltaMod Builder"},
    }


def _modding_xml(specs: list[PatchSpec]) -> str:
    lines: list[str] = []
    for spec in specs:
        patch_type = (
            "g3mpatch"
            if spec.source.suffix.casefold() == ".g3mpatch"
            else "xdelta"
        )
        lines.append(
            f'<patch type="{patch_type}" patch="./{spec.archive_name}" '
            f'to="{spec.destination}"/>'
        )
    return "\n".join(lines) + "\n"


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
    merge_support: bool = True,
) -> Path:
    specs = _validated_specs(patches)
    target_version = target_version.strip()
    if not target_version:
        raise ValueError("target_version cannot be blank")
    package_id = _validate_package_id(package_id)
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
        with tempfile.TemporaryDirectory(prefix="deltamod-package-") as temp:
            root = Path(temp)
            copied: set[str] = set()
            for spec in specs:
                if spec.archive_name in copied:
                    continue
                shutil.copyfile(spec.source, root / spec.archive_name)
                copied.add(spec.archive_name)
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
                        merge_support=merge_support,
                    ),
                    indent=4,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            (root / "modding.xml").write_text(
                _modding_xml(specs),
                encoding="utf-8",
                newline="\n",
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
        _validate_package_id(str(details.get("packageID", "")))
        if not isinstance(details.get("mergeSupport"), bool):
            raise ValueError(
                "meta.json must explicitly declare boolean mergeSupport"
            )
        if not str(metadata.get("deltaruneTargetVersion", "")).strip():
            raise ValueError("meta.json must declare deltaruneTargetVersion")

        needed_files = metadata.get("neededFiles")
        if not isinstance(needed_files, list) or not needed_files:
            raise ValueError("meta.json must contain neededFiles entries")
        needed_chapters: set[int] = set()
        for item in needed_files:
            if not isinstance(item, dict):
                raise ValueError("neededFiles entries must be objects")
            filename = str(item.get("file", ""))
            match = re.fullmatch(r"\./chapter([1-5])_windows/data\.win", filename)
            if match is None:
                raise ValueError(
                    "neededFiles must point to exact installed chapter data.win paths"
                )
            chapter = int(match.group(1))
            if chapter in needed_chapters:
                raise ValueError("neededFiles contains a duplicate chapter")
            needed_chapters.add(chapter)
            if not SHA256_PATTERN.fullmatch(str(item.get("checksum", ""))):
                raise ValueError("neededFiles checksum must be a SHA-256")

        declared_chapters: set[int] = set()
        declared_payloads: set[str] = set()
        payload_chapters: dict[str, int] = {}
        lines = archive.read("modding.xml").decode("utf-8").splitlines()
        if not lines:
            raise ValueError("modding.xml must contain at least one patch")
        for line in lines:
            match = PATCH_LINE_PATTERN.fullmatch(line.strip())
            if match is None:
                raise ValueError(
                    "modding.xml contains an unsupported patch instruction "
                    "or invalid chapter destination"
                )
            patch_type = match.group(1)
            payload_name = match.group(2)
            chapter = int(match.group(3))
            if chapter in declared_chapters:
                raise ValueError("modding.xml patches one chapter more than once")
            if payload_name not in names:
                raise ValueError(
                    f"modding.xml references a missing patch: {payload_name}"
                )
            if payload_name in payload_chapters:
                raise ValueError(
                    "one patch payload cannot target multiple chapters"
                )
            expected_type = (
                "g3mpatch"
                if PurePosixPath(payload_name).suffix.casefold() == ".g3mpatch"
                else "xdelta"
            )
            if patch_type != expected_type:
                raise ValueError(
                    f"modding.xml patch type does not match {payload_name}"
                )
            declared_chapters.add(chapter)
            declared_payloads.add(payload_name)
            payload_chapters[payload_name] = chapter

        if declared_chapters != needed_chapters:
            raise ValueError(
                "meta.json neededFiles and modding.xml target different chapters"
            )
        payload_entries = set(names).difference(required)
        if payload_entries != declared_payloads:
            raise ValueError(
                "the ZIP contains undeclared payloads or omits a declared payload"
            )

        for payload_name in declared_payloads:
            suffix = PurePosixPath(payload_name).suffix.casefold()
            payload = archive.read(payload_name)
            if suffix in {".xdelta", ".vcdiff"}:
                if not payload.startswith(VCDIFF_MAGIC):
                    raise ValueError(
                        f"patch is not VCDIFF/xdelta: {payload_name}"
                    )
            elif suffix == ".g3mpatch":
                manifest = _validate_g3mpatch_bytes(
                    payload,
                    label=payload_name,
                )
                original = manifest["original"]
                assert isinstance(original, dict)
                chapter = payload_chapters[payload_name]
                if (
                    str(original["md5"]).casefold()
                    != DELTARUNE_105_MD5[chapter]
                ):
                    raise ValueError(
                        f"{payload_name} targets a different clean data.win "
                        f"than chapter {chapter}"
                    )
            else:
                raise ValueError(f"unsupported package payload: {payload_name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a root-only DeltaMod package from G3MTool or VCDIFF "
            "chapter patches."
        )
    )
    parser.add_argument(
        "--patch",
        action="append",
        required=True,
        type=_parse_patch,
        metavar="CHAPTER=PATH",
        help=(
            "chapter number and G3MPATCH/VCDIFF payload; repeat for "
            "multiple chapters"
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION)
    parser.add_argument("--author", action="append", dest="authors")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--package-id", default=DEFAULT_PACKAGE_ID)
    parser.add_argument(
        "--no-merge-support",
        action="store_false",
        dest="merge_support",
        help="mark an atomic package as unsafe to combine with other patches",
    )
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
        merge_support=args.merge_support,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
