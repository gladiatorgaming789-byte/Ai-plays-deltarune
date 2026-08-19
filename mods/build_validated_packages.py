from __future__ import annotations

import binascii
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODS_ROOT = ROOT / "mods"
VALIDATED_BUILD = MODS_ROOT / "validated_deltarune_build.json"
DOS_TIME = 0
DOS_DATE = 33
VERSION_NEEDED = 20
VERSION_MADE_BY_UNIX = (3 << 8) | 20
EXTERNAL_ATTR = 0o100644 << 16
META_KEY_ORDER = ("metadata", "deltaruneTargetVersion", "neededFiles", "exporter")

# Speed has not changed and remains pinned to its previously validated exact
# bytes. Telemetry/Support changed for the training-save safety fix; while GitHub
# Actions is unable to materialize new binary artifacts, the launcher rebuilds
# those two current packages directly from the committed source and verifies the
# package structure, version, clean-file hashes, and safety markers.
PACKAGE_SPECS = (
    {
        "label": "Speed",
        "builder": MODS_ROOT / "speed" / "tools" / "build_packages.py",
        "output": MODS_ROOT / "speed" / "deltamod" / "AI-Speed-All-Chapters-DeltaMod-CSX-v1.4.0.zip",
        "release": MODS_ROOT / "speed" / "release_1.4.0.json",
        "version": "1.4.0",
        "markers": (b"AI_SPEED_MOD|1|",),
        "exact": True,
    },
    {
        "label": "Telemetry",
        "builder": MODS_ROOT / "telemetry" / "tools" / "build_packages.py",
        "output": MODS_ROOT / "telemetry" / "deltamod" / "Telemetry-All-Chapters-DeltaMod-CSX-v9.3.1.zip",
        "release": None,
        "version": "9.3.1",
        "markers": (b"DRTEL|9|", b"AI_MULTI_INSTANCE|1|", b"AI_BACKGROUND_AUTOSAVE_V2"),
        "exact": False,
    },
    {
        "label": "Support",
        "builder": MODS_ROOT / "support" / "tools" / "build_packages.py",
        "output": MODS_ROOT / "support" / "deltamod" / "AI-Support-All-Chapters-DeltaMod-CSX-v2.0.1.zip",
        "release": None,
        "version": "2.0.1",
        "markers": (
            b"AI_SPEED_MOD|1|",
            b"DRTEL|9|",
            b"AI_MULTI_INSTANCE|1|",
            b"AI_BACKGROUND_AUTOSAVE_V2",
        ),
        "exact": False,
    },
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return value


def expected_package(release_path: Path) -> tuple[int, str]:
    release = load_json(release_path)
    package = release.get("package")
    if not isinstance(package, dict):
        raise RuntimeError(f"Missing package record in {release_path}")
    size = int(package["size"])
    checksum = str(package["sha256"]).lower()
    if len(checksum) != 64:
        raise RuntimeError(f"Invalid package SHA-256 in {release_path}")
    if package.get("patch_type") != "csx":
        raise RuntimeError(f"Release is not pinned to DeltaMod csx patch type: {release_path}")
    return size, checksum


def package_is_valid(path: Path, expected_size: int, expected_sha256: str) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == expected_size
        and sha256_file(path) == expected_sha256
    )


def _canonical_meta_bytes(payload: bytes) -> bytes:
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("meta.json must be a JSON object")
    ordered: dict[str, object] = {}
    for key in META_KEY_ORDER:
        if key in value:
            ordered[key] = value[key]
    for key in sorted(set(value).difference(ordered)):
        ordered[key] = value[key]
    return (json.dumps(ordered, indent=4, ensure_ascii=False) + "\n").encode("utf-8")


def _canonical_zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    output = bytearray()
    central: list[tuple[bytes, int, int, int]] = []
    for name, payload in entries:
        name_bytes = name.encode("utf-8")
        crc32 = binascii.crc32(payload) & 0xFFFFFFFF
        offset = len(output)
        size = len(payload)
        output += struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            VERSION_NEEDED,
            0,
            0,
            DOS_TIME,
            DOS_DATE,
            crc32,
            size,
            size,
            len(name_bytes),
            0,
        )
        output += name_bytes
        output += payload
        central.append((name_bytes, crc32, size, offset))

    central_start = len(output)
    for name_bytes, crc32, size, offset in central:
        output += struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            VERSION_MADE_BY_UNIX,
            VERSION_NEEDED,
            0,
            0,
            DOS_TIME,
            DOS_DATE,
            crc32,
            size,
            size,
            len(name_bytes),
            0,
            0,
            0,
            0,
            EXTERNAL_ATTR,
            offset,
        )
        output += name_bytes
    central_size = len(output) - central_start
    count = len(central)
    output += struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        count,
        count,
        central_size,
        central_start,
        0,
    )
    return bytes(output)


def canonicalize_zip_storage(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as source:
        entries: list[tuple[str, bytes]] = []
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "meta.json":
                payload = _canonical_meta_bytes(payload)
            entries.append((info.filename, payload))
    path.write_bytes(_canonical_zip_bytes(entries))


def _verify_csx_declarations(path: Path) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        lines = archive.read("modding.xml").decode("utf-8").splitlines()
    if len(lines) != 5:
        raise RuntimeError(f"Expected five CSX patch declarations in {path.name}")
    for line in lines:
        stripped = line.strip()
        if 'type="csx"' not in stripped or not stripped.endswith("data.win\"/>"):
            raise RuntimeError(
                f"Invalid DeltaMod CSX declaration in {path.name}: {stripped}"
            )
        if 'type="xdelta"' in stripped:
            raise RuntimeError(
                f"Raw CSX must never be routed through xdelta/G3MTool: {path.name}"
            )


def _verify_source_package(
    path: Path,
    *,
    expected_version: str,
    hash_map: dict[str, str],
    required_markers: tuple[bytes, ...],
) -> None:
    _verify_csx_declarations(path)
    with zipfile.ZipFile(path, "r") as archive:
        meta = json.loads(archive.read("meta.json").decode("utf-8"))
        if not isinstance(meta, dict) or not isinstance(meta.get("metadata"), dict):
            raise RuntimeError(f"Invalid metadata in {path.name}")
        metadata = meta["metadata"]
        if str(metadata.get("version") or "") != expected_version:
            raise RuntimeError(
                f"{path.name} reports version {metadata.get('version')!r}; "
                f"expected {expected_version}."
            )
        needed = meta.get("neededFiles")
        if not isinstance(needed, list) or len(needed) != 5:
            raise RuntimeError(f"{path.name} does not pin all five clean chapter files")
        observed_hashes: dict[str, str] = {}
        for record in needed:
            if not isinstance(record, dict):
                raise RuntimeError(f"Malformed neededFiles entry in {path.name}")
            file_name = str(record.get("file") or "")
            chapter = next(
                (
                    str(value)
                    for value in range(1, 6)
                    if file_name == f"./chapter{value}_windows/data.win"
                ),
                None,
            )
            if chapter is None:
                raise RuntimeError(f"Unexpected needed file in {path.name}: {file_name}")
            observed_hashes[chapter] = str(record.get("checksum") or "")
        if observed_hashes != hash_map:
            raise RuntimeError(f"{path.name} clean data.win hashes do not match validated build")

        payload_names = [
            name
            for name in archive.namelist()
            if name not in {"meta.json", "modding.xml"}
        ]
        if len(payload_names) != 5:
            raise RuntimeError(f"{path.name} does not contain five per-chapter CSX payloads")
        for payload_name in payload_names:
            payload = archive.read(payload_name)
            for marker in required_markers:
                if marker not in payload:
                    raise RuntimeError(
                        f"{path.name} payload {payload_name} lost required marker "
                        f"{marker.decode('ascii', errors='replace')}"
                    )


def _build(
    label: str,
    builder: Path,
    output: Path,
    *,
    target_version: str,
    hash_map: dict[str, str],
) -> None:
    output.unlink(missing_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deltarune-ai-mod-build-") as temp_dir:
        temp = Path(temp_dir)
        hashes_path = temp / "clean_hashes.json"
        manifest_path = temp / f"{label.lower()}_manifest.json"
        hashes_path.write_text(
            json.dumps(hash_map, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(builder),
                "--target-version",
                target_version,
                "--clean-hashes",
                str(hashes_path),
                "--output",
                str(output),
                "--manifest",
                str(manifest_path),
            ],
            cwd=ROOT,
            check=False,
        )
        if result.returncode != 0:
            output.unlink(missing_ok=True)
            raise RuntimeError(
                f"{label} package builder failed with exit code {result.returncode}"
            )


def build_package(
    spec: dict[str, object],
    *,
    target_version: str,
    hash_map: dict[str, str],
) -> None:
    label = str(spec["label"])
    builder = Path(spec["builder"])
    output = Path(spec["output"])
    version = str(spec["version"])
    markers = tuple(spec.get("markers") or ())
    exact = bool(spec.get("exact"))
    release_value = spec.get("release")

    if exact:
        if release_value is None:
            raise RuntimeError(f"Pinned package {label} is missing its release record")
        release_path = Path(release_value)
        expected_size, expected_sha256 = expected_package(release_path)
        if package_is_valid(output, expected_size, expected_sha256):
            _verify_csx_declarations(output)
            print(f"[Mods] {label} package ready: {output.name}")
            return
        _build(
            label,
            builder,
            output,
            target_version=target_version,
            hash_map=hash_map,
        )
        canonicalize_zip_storage(output)
        _verify_csx_declarations(output)
        actual_size = output.stat().st_size
        actual_sha256 = sha256_file(output)
        if actual_size != expected_size or actual_sha256 != expected_sha256:
            output.unlink(missing_ok=True)
            raise RuntimeError(
                f"{label} package did not reproduce the validated release bytes. "
                f"Expected {expected_size} bytes / {expected_sha256}, got "
                f"{actual_size} bytes / {actual_sha256}."
            )
        print(
            f"[Mods] {label} package built and verified: {output.name} "
            f"({expected_sha256[:12]}...)"
        )
        return

    try:
        if output.is_file():
            _verify_source_package(
                output,
                expected_version=version,
                hash_map=hash_map,
                required_markers=markers,
            )
            print(
                f"[Mods] {label} source-validated package ready: {output.name} "
                f"({sha256_file(output)[:12]}...)"
            )
            return
    except (OSError, ValueError, KeyError, json.JSONDecodeError, RuntimeError, zipfile.BadZipFile):
        output.unlink(missing_ok=True)

    _build(
        label,
        builder,
        output,
        target_version=target_version,
        hash_map=hash_map,
    )
    _verify_source_package(
        output,
        expected_version=version,
        hash_map=hash_map,
        required_markers=markers,
    )
    print(
        f"[Mods] {label} package rebuilt from committed safe source: {output.name} "
        f"({sha256_file(output)[:12]}...)"
    )


def main() -> int:
    try:
        validated = load_json(VALIDATED_BUILD)
        target_version = str(validated["deltarune_target_version"])
        raw_hashes = validated["chapter_sha256"]
        if not isinstance(raw_hashes, dict):
            raise RuntimeError("validated chapter_sha256 must be a JSON object")
        hash_map = {str(key): str(value) for key, value in raw_hashes.items()}
        if set(hash_map) != {"1", "2", "3", "4", "5"}:
            raise RuntimeError("validated chapter hashes must contain Chapters 1-5")
        for spec in PACKAGE_SPECS:
            build_package(
                spec,
                target_version=target_version,
                hash_map=hash_map,
            )
    except (OSError, KeyError, ValueError, json.JSONDecodeError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"[Mods] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
