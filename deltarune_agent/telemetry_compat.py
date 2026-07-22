from __future__ import annotations

import sys
from typing import Callable

from . import telemetry as telemetry_module


_INSTALLED_FLAG = "_ai_telemetry_compatibility_guard_installed"
_reported_warnings: set[str] = set()


def telemetry_update_warning(packet: bytes) -> str | None:
    """Return an update warning only when the telemetry protocol is incompatible.

    The Python controller revision and telemetry protocol are separate. A run's
    ``agent_revision`` comes from Python and cannot be made stale by an old
    ``data.win`` patch. The telemetry installer needs reapplying only when this
    protocol changes or a game update replaces the modified chapter file.
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
    return None


def _report_once(message: str) -> None:
    if message in _reported_warnings:
        return
    _reported_warnings.add(message)
    print(f"TELEMETRY UPDATE WARNING: {message}", file=sys.stderr, flush=True)


def install_telemetry_compatibility_guard() -> None:
    """Wrap packet parsing once so GUI and CLI runs surface protocol mismatches."""

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
