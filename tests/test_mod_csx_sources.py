from __future__ import annotations

from pathlib import Path

import pytest

from deltarune_agent.deltamod_csx_package import (
    build_csx_package,
    validate_csx_file,
    validate_csx_package,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("relative_source", "label", "package_id", "version"),
    (
        (
            "mods/speed/AiSpeed.csx",
            "Speed",
            "github.ai-speed.gladiatorgaming789-byte",
            "1.3.0",
        ),
        (
            "mods/telemetry/AiTelemetry.csx",
            "Telemetry",
            "github.ai-telemetry.gladiatorgaming789-byte",
            "9.2.0",
        ),
    ),
)
def test_real_mod_source_builds_a_direct_csx_package(
    tmp_path: Path,
    relative_source: str,
    label: str,
    package_id: str,
    version: str,
) -> None:
    source = REPOSITORY_ROOT / relative_source
    validate_csx_file(source)
    output = build_csx_package(
        script=source,
        chapters=[1, 3, 5],
        output=tmp_path / f"{label}.zip",
        target_version="test-version",
        payload_label=label,
        name=f"Test {label}",
        version=version,
        description="CI-only source package validation",
        authors=["gladiatorgaming789-byte"],
        url="https://github.com/gladiatorgaming789-byte/Ai-plays-deltarune",
        package_id=package_id,
    )
    result = validate_csx_package(output, expected_chapters=(1, 3, 5))
    assert result["chapters"] == [1, 3, 5]
    assert result["has_needed_files"] is False
    assert result["merge_support"] is True
