from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from deltarune_agent.deltamod_package import (
    PatchSpec,
    build_package,
    validate_package,
)


HERE = Path(__file__).resolve().parent
SOURCE_PACKAGE = HERE / "Telemetry-DeltaMod-v9.0.1.zip"
OUTPUT_PACKAGE = HERE / "Telemetry-DeltaMod-v9.0.2.zip"
PACKAGE_ID = "github.ai-telemetry.gladiatorgaming789-byte"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rebuild() -> Path:
    if not SOURCE_PACKAGE.is_file():
        raise FileNotFoundError(
            "The verified v9.0.1 package is required as the metadata-only "
            "v9.0.2 payload source."
        )
    with tempfile.TemporaryDirectory(prefix="telemetry-902-") as temporary:
        root = Path(temporary)
        specs: list[PatchSpec] = []
        with zipfile.ZipFile(SOURCE_PACKAGE) as archive:
            for chapter in range(1, 6):
                name = f"Chapter{chapter}DataPatch.xdelta"
                payload = archive.read(name)
                path = root / name
                path.write_bytes(payload)
                specs.append(PatchSpec(chapter, path))
        build_package(
            patches=specs,
            output=OUTPUT_PACKAGE,
            target_version="1.05",
            name="AI Plays Deltarune Telemetry",
            version="9.0.2",
            description=(
                "Localhost-only telemetry v9 for the external AI Plays "
                "Deltarune controller. This release repairs DeltaMod metadata."
            ),
            package_id=PACKAGE_ID,
        )
    validate_package(OUTPUT_PACKAGE)
    record = {
        "file": OUTPUT_PACKAGE.name,
        "version": "9.0.2",
        "telemetry_protocol": 9,
        "size": OUTPUT_PACKAGE.stat().st_size,
        "sha256": _sha256(OUTPUT_PACKAGE),
        "package_id": PACKAGE_ID,
        "target_version": "1.05",
        "merge_support": True,
        "payload_source": SOURCE_PACKAGE.name,
        "payloads_unchanged": True,
    }
    (HERE / "release_9.0.2.json").write_text(
        json.dumps(record, indent=2) + "\n",
        encoding="utf-8",
    )
    return OUTPUT_PACKAGE


if __name__ == "__main__":
    print(rebuild())
