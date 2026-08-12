from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


SUPPORTED_CHAPTERS = tuple(range(1, 6))
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
PACKAGE_ID_PATTERN = re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+\.[a-z0-9-]+$")
PAYLOAD_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
PATCH_LINE_PATTERN = re.compile(
    r'^<patch type="csx" patch="\./([^"/]+\.csx)" '
    r'to="\./chapter([1-5])_windows/data\.win"/>$'
)
REQUIRED_CSX_MARKERS = (
    "EnsureDataLoaded();",
    "CodeImportGroup",
    ".Import();",
)
DETERMINISTIC_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_csx_bytes(payload: bytes, *, label: str) -> str:
    """Validate a DeltaMod/UndertaleModTool source patch without executing it."""

    if not payload:
        raise ValueError(f"CSX patch is empty: {label}")
    if b"\x00" in payload:
        raise ValueError(f"CSX patch contains binary NUL data: {label}")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"CSX patch is not valid UTF-8: {label}") from exc
    if not text.strip():
        raise ValueError(f"CSX patch contains no executable source: {label}")
    missing = [marker for marker in REQUIRED_CSX_MARKERS if marker not in text]
    if missing:
        raise ValueError(
            f"CSX patch lacks required safe installer structure ({', '.join(missing)}): "
            f"{label}"
        )
    return text


def validate_csx_file(path: Path) -> str:
    path = path.expanduser().resolve()
    if path.suffix.casefold() != ".csx":
        raise ValueError(f"direct DeltaMod source patches must use .csx: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"CSX patch does not exist: {path}")
    return validate_csx_bytes(path.read_bytes(), label=str(path))


def canonical_csx_bytes(payload: bytes, *, label: str) -> bytes:
    """Return validated UTF-8 CSX with platform-independent LF newlines."""

    text = validate_csx_bytes(payload, label=label)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.encode("utf-8")


def canonical_csx_file_bytes(path: Path) -> bytes:
    path = path.expanduser().resolve()
    if path.suffix.casefold() != ".csx":
        raise ValueError(f"direct DeltaMod source patches must use .csx: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"CSX patch does not exist: {path}")
    return canonical_csx_bytes(path.read_bytes(), label=str(path))


def sha256_csx_file(path: Path) -> str:
    return sha256_bytes(canonical_csx_file_bytes(path))


def _validate_package_id(package_id: str) -> str:
    package_id = package_id.strip().casefold()
    if not PACKAGE_ID_PATTERN.fullmatch(package_id):
        raise ValueError(
            "package_id must contain exactly three dot-separated lowercase "
            "segments using letters, numbers, or hyphens"
        )
    return package_id


def _normalize_chapters(chapters: list[int] | tuple[int, ...]) -> tuple[int, ...]:
    if not chapters:
        raise ValueError("at least one chapter is required")
    normalized = tuple(sorted(chapters))
    if len(normalized) != len(set(normalized)):
        raise ValueError("a chapter was supplied more than once")
    invalid = [chapter for chapter in normalized if chapter not in SUPPORTED_CHAPTERS]
    if invalid:
        raise ValueError(
            "unsupported chapter(s): " + ", ".join(str(item) for item in invalid)
        )
    return normalized


def _normalize_hashes(
    chapters: tuple[int, ...],
    clean_hashes: dict[int, str] | None,
) -> dict[int, str] | None:
    if clean_hashes is None:
        return None
    if set(clean_hashes) != set(chapters):
        raise ValueError(
            "clean_hashes must contain exactly the chapters included in the package"
        )
    normalized: dict[int, str] = {}
    for chapter in chapters:
        checksum = str(clean_hashes[chapter]).casefold()
        if not SHA256_PATTERN.fullmatch(checksum):
            raise ValueError(
                f"chapter {chapter} clean data.win checksum must be a SHA-256"
            )
        normalized[chapter] = checksum
    return normalized


def _payload_name(chapter: int, payload_label: str) -> str:
    return f"Chapter{chapter}{payload_label}.csx"


def _metadata(
    *,
    chapters: tuple[int, ...],
    clean_hashes: dict[int, str] | None,
    target_version: str,
    name: str,
    version: str,
    description: str,
    authors: list[str],
    url: str,
    package_id: str,
    merge_support: bool,
) -> dict[str, object]:
    result: dict[str, object] = {
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
        "exporter": {"tool": "AI Plays Deltarune direct-CSX DeltaMod Builder"},
    }
    if clean_hashes is not None:
        result["neededFiles"] = [
            {
                "file": f"./chapter{chapter}_windows/data.win",
                "checksum": clean_hashes[chapter],
            }
            for chapter in chapters
        ]
    return result


def _modding_xml(chapters: tuple[int, ...], payload_label: str) -> str:
    # DeltaMod's dedicated csx patch type executes raw UndertaleModTool scripts.
    # Declaring these files as xdelta routes them through G3MTool's ZIP-backed
    # merge path, which rejects plain source with a missing ZIP central directory.
    return "".join(
        f'<patch type="csx" patch="./{_payload_name(chapter, payload_label)}" '
        f'to="./chapter{chapter}_windows/data.win"/>\n'
        for chapter in chapters
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=DETERMINISTIC_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _write_zip_entry(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    archive.writestr(
        _zip_info(name),
        payload,
        compress_type=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    )


def build_csx_package(
    *,
    script: Path,
    chapters: list[int] | tuple[int, ...],
    output: Path,
    target_version: str,
    payload_label: str,
    name: str,
    version: str,
    description: str,
    authors: list[str],
    url: str,
    package_id: str,
    clean_hashes: dict[int, str] | None = None,
    merge_support: bool = True,
) -> Path:
    """Build a deterministic root-only DeltaMod source-CSX package.

    Raw UndertaleModTool scripts are declared through DeltaMod's dedicated
    ``csx`` patch type. Each chapter receives a separate archive member with
    identical canonical source bytes. ZIP metadata and text line endings are
    fixed so the same inputs produce the same package on every platform.
    """

    script = script.expanduser().resolve()
    script_bytes = canonical_csx_file_bytes(script)
    normalized_chapters = _normalize_chapters(chapters)
    normalized_hashes = _normalize_hashes(normalized_chapters, clean_hashes)
    target_version = target_version.strip()
    if not target_version:
        raise ValueError("target_version cannot be blank")
    if not PAYLOAD_LABEL_PATTERN.fullmatch(payload_label):
        raise ValueError(
            "payload_label may contain only letters, numbers, underscores, and hyphens"
        )
    package_id = _validate_package_id(package_id)
    cleaned_authors = [author.strip() for author in authors if author.strip()]
    if not cleaned_authors:
        raise ValueError("at least one author is required")

    metadata_bytes = (
        json.dumps(
            _metadata(
                chapters=normalized_chapters,
                clean_hashes=normalized_hashes,
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
        + "\n"
    ).encode("utf-8")
    modding_bytes = _modding_xml(normalized_chapters, payload_label).encode("utf-8")

    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_name(f".{output.name}.tmp")
    temporary_output.unlink(missing_ok=True)

    try:
        with zipfile.ZipFile(temporary_output, "w") as archive:
            _write_zip_entry(archive, "meta.json", metadata_bytes)
            _write_zip_entry(archive, "modding.xml", modding_bytes)
            for chapter in normalized_chapters:
                _write_zip_entry(
                    archive,
                    _payload_name(chapter, payload_label),
                    script_bytes,
                )
        validate_csx_package(
            temporary_output,
            expected_chapters=normalized_chapters,
        )
        temporary_output.replace(output)
    finally:
        temporary_output.unlink(missing_ok=True)
    return output


def validate_csx_package(
    package: Path,
    *,
    expected_chapters: tuple[int, ...] | None = None,
) -> dict[str, object]:
    package = package.expanduser().resolve()
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("package contains duplicate archive entries")
        for name in names:
            pure = PurePosixPath(name)
            if pure.is_absolute() or ".." in pure.parts or len(pure.parts) != 1:
                raise ValueError("all DeltaMod package files must be at the ZIP root")

        required = {"meta.json", "modding.xml"}
        if not required.issubset(names):
            missing = ", ".join(sorted(required.difference(names)))
            raise ValueError(f"package is missing required file(s): {missing}")

        try:
            metadata = json.loads(archive.read("meta.json").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("meta.json is invalid") from exc
        details = metadata.get("metadata")
        if not isinstance(details, dict) or details.get("game") != "toby.deltarune":
            raise ValueError("meta.json must target toby.deltarune")
        _validate_package_id(str(details.get("packageID", "")))
        if not isinstance(details.get("mergeSupport"), bool):
            raise ValueError("meta.json must explicitly declare boolean mergeSupport")
        if not str(metadata.get("deltaruneTargetVersion", "")).strip():
            raise ValueError("meta.json must declare deltaruneTargetVersion")

        declared_chapters: set[int] = set()
        declared_payloads: set[str] = set()
        payload_hashes: set[str] = set()
        lines = archive.read("modding.xml").decode("utf-8").splitlines()
        if not lines:
            raise ValueError("modding.xml must contain at least one patch")
        for line in lines:
            match = PATCH_LINE_PATTERN.fullmatch(line.strip())
            if match is None:
                raise ValueError(
                    "modding.xml must use dedicated csx-type patches targeting exact "
                    "chapter data.win paths"
                )
            payload_name = match.group(1)
            chapter = int(match.group(2))
            if chapter in declared_chapters:
                raise ValueError("modding.xml patches one chapter more than once")
            if payload_name in declared_payloads:
                raise ValueError("each chapter must use its own CSX archive member")
            if payload_name not in names:
                raise ValueError(f"modding.xml references a missing patch: {payload_name}")
            payload = archive.read(payload_name)
            canonical_payload = canonical_csx_bytes(payload, label=payload_name)
            if payload != canonical_payload:
                raise ValueError(
                    f"CSX payload must use canonical UTF-8/LF text: {payload_name}"
                )
            payload_hashes.add(sha256_bytes(payload))
            declared_chapters.add(chapter)
            declared_payloads.add(payload_name)

        if len(payload_hashes) != 1:
            raise ValueError("all per-chapter CSX payloads must contain identical source")
        expected = set(expected_chapters) if expected_chapters is not None else declared_chapters
        if declared_chapters != expected:
            raise ValueError("package targets an unexpected chapter set")

        needed_files = metadata.get("neededFiles")
        if needed_files is not None:
            if not isinstance(needed_files, list) or not needed_files:
                raise ValueError("neededFiles must be a non-empty list when present")
            needed_chapters: set[int] = set()
            for item in needed_files:
                if not isinstance(item, dict):
                    raise ValueError("neededFiles entries must be objects")
                match = re.fullmatch(
                    r"\./chapter([1-5])_windows/data\.win",
                    str(item.get("file", "")),
                )
                if match is None:
                    raise ValueError("neededFiles contains an invalid data.win path")
                chapter = int(match.group(1))
                if chapter in needed_chapters:
                    raise ValueError("neededFiles contains a duplicate chapter")
                if not SHA256_PATTERN.fullmatch(str(item.get("checksum", ""))):
                    raise ValueError("neededFiles checksum must be a SHA-256")
                needed_chapters.add(chapter)
            if needed_chapters != declared_chapters:
                raise ValueError("neededFiles and modding.xml target different chapters")

        payload_entries = set(names).difference(required)
        if payload_entries != declared_payloads:
            raise ValueError(
                "the ZIP contains undeclared payloads or omits a declared payload"
            )
        return {
            "chapters": sorted(declared_chapters),
            "payload_sha256": next(iter(payload_hashes)),
            "package_id": details["packageID"],
            "merge_support": details["mergeSupport"],
            "target_version": metadata["deltaruneTargetVersion"],
            "has_needed_files": needed_files is not None,
            "patch_type": "csx",
        }


__all__ = [
    "SUPPORTED_CHAPTERS",
    "build_csx_package",
    "canonical_csx_bytes",
    "canonical_csx_file_bytes",
    "sha256_bytes",
    "sha256_csx_file",
    "sha256_file",
    "validate_csx_bytes",
    "validate_csx_file",
    "validate_csx_package",
]
