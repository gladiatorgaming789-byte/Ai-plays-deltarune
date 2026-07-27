from __future__ import annotations

import argparse
from pathlib import Path

VCDIFF_MAGIC = b"\xd6\xc3\xc4\x00"
VCD_SOURCE = 0x01
VCD_TARGET = 0x02
VCD_ADLER32 = 0x04


def _read_varint(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    while True:
        if position >= len(data):
            raise ValueError("truncated VCDIFF varint")
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, position


def validate_standard_layout(path: Path) -> int:
    data = path.read_bytes()
    if not data.startswith(VCDIFF_MAGIC):
        raise ValueError(f"not a VCDIFF/xdelta stream: {path}")

    position = len(VCDIFF_MAGIC)
    header_indicator = data[position]
    position += 1
    if header_indicator & 0x01:
        position += 1
    if header_indicator & 0x02:
        length, position = _read_varint(data, position)
        position += length
    if header_indicator & 0x04:
        length, position = _read_varint(data, position)
        position += length
    if header_indicator & ~0x07:
        raise ValueError(f"unsupported VCDIFF header indicator: {header_indicator:#x}")

    windows = 0
    while position < len(data):
        window_start = position
        indicator = data[position]
        position += 1
        if indicator & (VCD_SOURCE | VCD_TARGET):
            _, position = _read_varint(data, position)
            _, position = _read_varint(data, position)

        delta_length, position = _read_varint(data, position)
        window_end = position + delta_length
        _, position = _read_varint(data, position)  # target window length
        delta_indicator = data[position]
        position += 1
        if delta_indicator != 0:
            raise ValueError(
                f"secondary-compressed VCDIFF sections are unsupported: {path}"
            )

        data_length, position = _read_varint(data, position)
        instruction_length, position = _read_varint(data, position)
        address_length, position = _read_varint(data, position)

        # RFC 3284/xdelta3 places the optional four-byte Adler-32 value here,
        # immediately before the three encoded sections. The original telemetry
        # payload generator mistakenly put it at the end of each window, which
        # DeltaMod interpreted as an invalid source/checksum combination.
        if indicator & VCD_ADLER32:
            position += 4

        position += data_length + instruction_length + address_length
        if position != window_end:
            raise ValueError(
                f"non-standard VCDIFF window layout at byte {window_start}: "
                f"parsed end {position}, declared end {window_end}"
            )
        windows += 1

    if position != len(data):
        raise ValueError(f"trailing bytes in VCDIFF stream: {path}")
    return windows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate standard xdelta3/VCDIFF window and checksum placement."
    )
    parser.add_argument("patches", nargs="+", type=Path)
    args = parser.parse_args()
    for patch in args.patches:
        windows = validate_standard_layout(patch)
        print(f"{patch}: valid standard VCDIFF layout ({windows} windows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
