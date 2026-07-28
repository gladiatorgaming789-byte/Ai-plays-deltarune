from pathlib import Path

import pytest

from mods.telemetry.deltamod.validate_vcdiff_layout import (
    VCDIFF_MAGIC,
    validate_standard_layout,
)


def _single_window(*, declare_compressor: bool, delta_indicator: int) -> bytes:
    header = (
        b"\x01\x01"
        if declare_compressor
        else b"\x00"
    )
    body = bytes(
        (
            1,  # target window length
            delta_indicator,
            1,  # data section length
            0,  # instruction section length
            0,  # address section length
        )
    ) + b"x"
    window = b"\x00" + bytes((len(body),)) + body
    return VCDIFF_MAGIC + header + window


def test_validator_accepts_declared_secondary_compression(tmp_path: Path):
    patch = tmp_path / "compressed.xdelta"
    patch.write_bytes(
        _single_window(
            declare_compressor=True,
            delta_indicator=1,
        )
    )

    assert validate_standard_layout(patch) == 1


def test_validator_rejects_undeclared_secondary_compression(tmp_path: Path):
    patch = tmp_path / "invalid.xdelta"
    patch.write_bytes(
        _single_window(
            declare_compressor=False,
            delta_indicator=1,
        )
    )

    with pytest.raises(ValueError, match="without declaring"):
        validate_standard_layout(patch)


def test_validator_rejects_unknown_delta_indicator_bits(tmp_path: Path):
    patch = tmp_path / "invalid-bits.xdelta"
    patch.write_bytes(
        _single_window(
            declare_compressor=True,
            delta_indicator=8,
        )
    )

    with pytest.raises(ValueError, match="invalid VCDIFF delta indicator"):
        validate_standard_layout(patch)
