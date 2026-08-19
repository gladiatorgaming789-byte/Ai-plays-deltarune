"""Best-effort temporal alignment between screen capture and UDP telemetry.

The gameplay loop captures the window and polls telemetry sequentially. At high
simulation speeds, always pairing the screenshot with the newest packet can
associate an older image with newer camera/player coordinates. This installer
brackets each capture between the previous and current telemetry observations,
selects the temporally closest safe sample, and invalidates visual evidence when
alignment is too uncertain.
"""

from __future__ import annotations

from dataclasses import replace
import time
from typing import Any


SYNC_VERSION = 1
MAX_SYNC_OFFSET_SECONDS = 0.120
MAX_PREVIOUS_SAMPLE_AGE_SECONDS = 0.250
_INSTALLED = False
_CAPTURE_MIDPOINT: float | None = None
_CAPTURE_DURATION = 0.0
_LAST_SYNC: dict[str, object] = {}


def current_sync_status() -> dict[str, object]:
    return dict(_LAST_SYNC)


def _same_identity(left, right) -> bool:
    if left is None or right is None:
        return False
    left_agent = getattr(left, "agent_id", None)
    right_agent = getattr(right, "agent_id", None)
    if left_agent and right_agent and left_agent != right_agent:
        return False
    return True


def _transition_trace_requires_current(receiver, previous, current) -> bool:
    trace = getattr(receiver, "overworld_trace", ())
    rooms = [
        str(getattr(sample, "room_name", "") or getattr(sample, "room_id", ""))
        for sample in trace
        if sample is not None
    ]
    rooms = [room for room in rooms if room and room.casefold() != "unknown"]
    if len(set(rooms)) > 1:
        return True
    if previous is None or current is None:
        return False
    previous_room = str(
        getattr(previous, "room_name", "") or getattr(previous, "room_id", "")
    )
    current_room = str(
        getattr(current, "room_name", "") or getattr(current, "room_id", "")
    )
    return bool(previous_room and current_room and previous_room != current_room)


def install_frame_telemetry_sync() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .hierarchical_policy import HierarchicalPolicy
    from .observer import ScreenObserver
    from .telemetry import TelemetryReceiver

    original_observe = ScreenObserver.observe
    original_poll = TelemetryReceiver.poll
    original_diagnostics = TelemetryReceiver.diagnostics
    original_validate = HierarchicalPolicy.validate_observation

    def observe(observer, step: int):
        global _CAPTURE_MIDPOINT, _CAPTURE_DURATION
        started = time.monotonic()
        observation = original_observe(observer, step)
        ended = time.monotonic()
        _CAPTURE_MIDPOINT = (started + ended) / 2.0
        _CAPTURE_DURATION = max(0.0, ended - started)
        return observation

    def poll(receiver):
        global _LAST_SYNC
        previous = getattr(receiver, "_frame_sync_previous", None)
        current = original_poll(receiver)
        capture_time = _CAPTURE_MIDPOINT
        if current is None or capture_time is None:
            if current is not None:
                receiver._frame_sync_previous = current
            _LAST_SYNC = {
                "version": SYNC_VERSION,
                "available": False,
                "reason": "capture or telemetry sample unavailable",
            }
            return current

        selected = current
        source = "current"
        current_offset = abs(float(current.received_at) - capture_time)
        previous_offset: float | None = None
        if previous is not None and _same_identity(previous, current):
            previous_offset = abs(float(previous.received_at) - capture_time)
            previous_age = capture_time - float(previous.received_at)
            if (
                0.0 <= previous_age <= MAX_PREVIOUS_SAMPLE_AGE_SECONDS
                and previous_offset < current_offset
                and not _transition_trace_requires_current(receiver, previous, current)
            ):
                selected = previous
                source = "previous"

        selected_offset = abs(float(selected.received_at) - capture_time)
        reliable = selected_offset <= MAX_SYNC_OFFSET_SECONDS
        receiver._frame_sync_previous = current
        receiver._frame_sync_samples = int(
            getattr(receiver, "_frame_sync_samples", 0) or 0
        ) + 1
        receiver._frame_sync_offset_total = float(
            getattr(receiver, "_frame_sync_offset_total", 0.0) or 0.0
        ) + selected_offset
        receiver._frame_sync_max_offset = max(
            float(getattr(receiver, "_frame_sync_max_offset", 0.0) or 0.0),
            selected_offset,
        )
        if not reliable:
            receiver._frame_sync_unreliable = int(
                getattr(receiver, "_frame_sync_unreliable", 0) or 0
            ) + 1
        if source == "previous":
            receiver._frame_sync_previous_selections = int(
                getattr(receiver, "_frame_sync_previous_selections", 0) or 0
            ) + 1

        _LAST_SYNC = {
            "version": SYNC_VERSION,
            "available": True,
            "reliable": reliable,
            "source": source,
            "offset_seconds": selected_offset,
            "current_offset_seconds": current_offset,
            "previous_offset_seconds": previous_offset,
            "capture_duration_seconds": _CAPTURE_DURATION,
            "packet_sequence": getattr(selected, "packet_sequence", None),
            "room": getattr(selected, "room_name", None),
            "mode": getattr(selected, "mode", None),
        }
        return selected

    def diagnostics(receiver) -> dict[str, Any]:
        result = dict(original_diagnostics(receiver))
        samples = int(getattr(receiver, "_frame_sync_samples", 0) or 0)
        total = float(getattr(receiver, "_frame_sync_offset_total", 0.0) or 0.0)
        result["frame_telemetry_sync"] = {
            "version": SYNC_VERSION,
            "samples": samples,
            "unreliable_samples": int(
                getattr(receiver, "_frame_sync_unreliable", 0) or 0
            ),
            "previous_sample_selections": int(
                getattr(receiver, "_frame_sync_previous_selections", 0) or 0
            ),
            "average_offset_seconds": total / samples if samples else None,
            "maximum_offset_seconds": (
                float(getattr(receiver, "_frame_sync_max_offset", 0.0) or 0.0)
                if samples
                else None
            ),
            "maximum_reliable_offset_seconds": MAX_SYNC_OFFSET_SECONDS,
            "latest": current_sync_status(),
        }
        return result

    def validate(policy, observation, telemetry):
        status = current_sync_status()
        if (
            telemetry is not None
            and status.get("available")
            and status.get("reliable") is False
            and getattr(observation, "visual_valid", True)
        ):
            observation = replace(observation, visual_valid=False)
            policy.map_updates.append(
                {
                    "type": "frame_telemetry_sync",
                    "version": SYNC_VERSION,
                    "status": "visual_rejected",
                    "offset_seconds": status.get("offset_seconds"),
                    "capture_duration_seconds": status.get("capture_duration_seconds"),
                    "packet_sequence": status.get("packet_sequence"),
                    "room": status.get("room"),
                }
            )
        return original_validate(policy, observation, telemetry)

    ScreenObserver.observe = observe  # type: ignore[method-assign]
    TelemetryReceiver.poll = poll  # type: ignore[method-assign]
    TelemetryReceiver.diagnostics = diagnostics  # type: ignore[method-assign]
    HierarchicalPolicy.validate_observation = validate  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = [
    "MAX_PREVIOUS_SAMPLE_AGE_SECONDS",
    "MAX_SYNC_OFFSET_SECONDS",
    "SYNC_VERSION",
    "current_sync_status",
    "install_frame_telemetry_sync",
]
