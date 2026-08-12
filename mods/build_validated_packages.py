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

PACKAGE_SPECS = (
    (
        "Speed",
        MODS_ROOT / "speed" / "tools" / "build_packages.py",
        MODS_ROOT / "speed" / "deltamod" / "AI-Speed-All-Chapters-DeltaMod-CSX-v1.3.1.zip",
        MODS_ROOT / "speed" / "release_1.3.1.json",
    ),
    (
        "Telemetry",
        MODS_ROOT / "telemetry" / "tools" / "build_packages.py",
        MODS_ROOT / "telemetry" / "deltamod" / "Telemetry-All-Chapters-DeltaMod-CSX-v9.2.1.zip",
        MODS_ROOT / "telemetry" / "release_9.2.1.json",
    ),
    (
        "Support",
        MODS_ROOT / "support" / "tools" / "build_packages.py",
        MODS_ROOT / "support" / "deltamod" / "AI-Support-All-Chapters-DeltaMod-CSX-v1.0.0.zip",
        MODS_ROOT / "support" / "release_1.0.0.json",
    ),
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


def build_package(
    label: str,
    builder: Path,
    output: Path,
    release_path: Path,
    *,
    target_version: str,
    hash_map: dict[str, str],
) -> None:
    expected_size, expected_sha256 = expected_package(release_path)
    if package_is_valid(output, expected_size, expected_sha256):
        _verify_csx_declarations(output)
        print(f"[Mods] {label} package ready: {output.name}")
        return

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
        for label, builder, output, release_path in PACKAGE_SPECS:
            build_package(
                label,
                builder,
                output,
                release_path,
                target_version=target_version,
                hash_map=hash_map,
            )
    except (OSError, KeyError, ValueError, json.JSONDecodeError, RuntimeError, zipfile.BadZipFile) as exc:
        print(f"[Mods] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
