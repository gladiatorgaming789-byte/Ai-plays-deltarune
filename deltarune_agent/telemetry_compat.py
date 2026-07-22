from __future__ import annotations

import sys
from typing import Callable

from . import telemetry as telemetry_module


EXPECTED_TELEMETRY_BUILD = "v9-run10-window-autodetect-v1"
_BUILD_PREFIX = "build="
_INSTALLED_FLAG = "_ai_telemetry_compatibility_guard_installed"
_reported_warnings: set[str] = set()


def telemetry_update_warning(packet: bytes) -> str | None:
    """Return a clear update warning for an old installed telemetry patch.

    Only core packets are expected to carry the build marker. Optional motion,
    collision, render, and timing packets may arrive before the matching core
    datagram on UDP, so they must not cause a false warning by themselves.
    """

    start = packet.find(telemetry_module.MAGIC)
    if start < 0:
        return None
    try:
        fields = (
            packet[start:]
            .rstrip(b"\x00")
            .decode("utf-8", errors="replace")
            .split("|")
        )
        version = int(fields[1])
    except (IndexError, ValueError):
        return None

    if version < telemetry_module.PROTOCOL_VERSION:
        return (
            "Telemetry mod update required: the game is sending protocol "
            f"v{version}, but this controller expects v{telemetry_module.PROTOCOL_VERSION}. "
            "Updating the Python folder does not update data.win; restore the clean "
            "chapter backup and reapply mods/telemetry/AiTelemetry.csx."
        )
    if version > telemetry_module.PROTOCOL_VERSION:
        return (
            "Telemetry/controller mismatch: the game telemetry protocol is newer "
            f"(v{version}) than this controller (v{telemetry_module.PROTOCOL_VERSION}). "
            "Update the Python project before running live input."
        )

    values: dict[str, str] = {}
    for token in fields[8:]:
        if token == "end":
            break
        key, separator, value = token.partition("=")
        if separator and key:
            values[key] = value
    if values.get("part", "core") != "core":
        return None

    installed_build = values.get("build", "")
    if installed_build == EXPECTED_TELEMETRY_BUILD:
        return None
    if installed_build:
        detail = f"installed build {installed_build!r}"
    else:
        detail = "an older v9 patch with no build marker"
    return (
        "Telemetry mod update required: the game is using "
        f"{detail}, while this controller expects {EXPECTED_TELEMETRY_BUILD!r}. "
        "Updating or pulling the project folder alone does not modify the chapter's "
        "data.win. Restore the clean chapter backup, run the current "
        "mods/telemetry/AiTelemetry.csx, and save the newly patched data.win."
    )


def _report_once(message: str) -> None:
    if message in _reported_warnings:
        return
    _reported_warnings.add(message)
    print(f"TELEMETRY UPDATE WARNING: {message}", file=sys.stderr, flush=True)


def install_telemetry_compatibility_guard() -> None:
    """Wrap packet parsing once so GUI and CLI runs surface patch mismatches."""

    if getattr(telemetry_module, _INSTALLED_FLAG, False):
        return
    original: Callable = telemetry_module.parse_packet

    def guarded_parse_packet(packet: bytes, received_at: float | None = None):
        warning = telemetry_update_warning(packet)
        if warning:
            _report_once(warning)
        return original(packet, received_at=received_at)

    telemetry_module.parse_packet = guarded_parse_packet
    setattr(telemetry_module, _INSTALLED_FLAG, True)
