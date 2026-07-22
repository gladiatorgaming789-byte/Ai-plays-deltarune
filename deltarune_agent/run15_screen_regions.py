from __future__ import annotations

from dataclasses import replace

from . import policy as policy_module
from .run13_screen_regions import FLOOR_EVIDENCE_PREFIX
from .run14_screen_regions import analyze_screen_regions as run14_analyze_screen_regions
from .screen_regions import ScreenRegionObservation
from .telemetry import TelemetrySample


SCROLLING_ROOM_RATIO = 1.35
SCROLLING_FLOOR_CONTACT_PREFIX = "unconfirmed floor contact along a scrolling-room boundary"
SCROLLING_FLOOR_MAX_INTEREST = 0.28
SCROLLING_FLOOR_MAX_OPENING_SCORE = 0.38


def _is_parallel_scrolling_boundary(
    telemetry: TelemetrySample,
    edge: str | None,
) -> bool:
    values = (
        telemetry.room_width,
        telemetry.room_height,
        telemetry.camera_width,
        telemetry.camera_height,
    )
    if any(value is None for value in values):
        return False
    room_width, room_height, camera_width, camera_height = (
        float(value) for value in values
    )
    if min(room_width, room_height, camera_width, camera_height) <= 0:
        return False
    if edge in {"top", "bottom"}:
        return room_width > camera_width * SCROLLING_ROOM_RATIO
    if edge in {"left", "right"}:
        return room_height > camera_height * SCROLLING_ROOM_RATIO
    return False


def analyze_screen_regions(
    frame,
    telemetry: TelemetrySample,
) -> list[ScreenRegionObservation]:
    """Keep visible-floor exits strong only when the whole boundary is local.

    Run fourteen correctly used the orange floor strip at the bottom of Kris's
    bedroom as an exit clue. In the first long Dark World room, however, the same
    test fired on dozens of tiny platform fragments touching the bottom of a
    3,640-pixel scrolling room. A local camera crop cannot establish that one of
    those fragments is a room exit. It remains ordinary visual evidence until a
    mapped path or a more structured doorway confirms it.
    """

    observations = run14_analyze_screen_regions(frame, telemetry)
    result: list[ScreenRegionObservation] = []
    for observation in observations:
        if (
            observation.feature_summary.startswith(FLOOR_EVIDENCE_PREFIX)
            and _is_parallel_scrolling_boundary(telemetry, observation.edge_hint)
        ):
            result.append(
                replace(
                    observation,
                    interest=min(
                        observation.interest,
                        SCROLLING_FLOOR_MAX_INTEREST,
                    ),
                    hypothesis=None,
                    feature_summary=(
                        f"{SCROLLING_FLOOR_CONTACT_PREFIX}; "
                        "a local camera crop cannot prove a room transition"
                    ),
                    edge_opening_score=min(
                        observation.edge_opening_score,
                        SCROLLING_FLOOR_MAX_OPENING_SCORE,
                    ),
                )
            )
            continue
        result.append(observation)
    return result


def install_run15_screen_region_analyzer() -> None:
    policy_module.analyze_screen_regions = analyze_screen_regions
