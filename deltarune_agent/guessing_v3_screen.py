"""Raw-observation bridge for Guessing v3 multi-view consistency.

The legacy screen-region memory intentionally keeps the clearest/best feature
anchor stable so animation cannot make a remembered target drift. That is good
for routing, but unsuitable for *measuring* whether independent observations
agree in world space. This module wraps the currently installed screen analyzer
and retains only the newest raw observations long enough for Guessing v3 to
sample them during the same policy update.

No game-specific labels or progression knowledge are introduced here.
"""

from __future__ import annotations

from typing import Any

from . import policy as policy_module


_INSTALLED = False
_ORIGINAL_ANALYZE = None
_LATEST_RAW: dict[tuple[int, int], dict[str, object]] = {}


def _raw_record(observation: Any) -> dict[str, object]:
    record: dict[str, object] = {
        "interest": float(getattr(observation, "interest", 0.0) or 0.0),
        "signature": str(getattr(observation, "appearance_signature", "") or ""),
    }
    focus_x = getattr(observation, "focus_world_x", None)
    focus_y = getattr(observation, "focus_world_y", None)
    if focus_x is not None and focus_y is not None:
        record["focus_world"] = [round(float(focus_x), 2), round(float(focus_y), 2)]
    feature_box = getattr(observation, "feature_box_world", None)
    if isinstance(feature_box, (list, tuple)) and len(feature_box) == 4:
        record["feature_box_world"] = [round(float(value), 2) for value in feature_box]
    passage_box = getattr(observation, "passage_box_world", None)
    if isinstance(passage_box, (list, tuple)) and len(passage_box) == 4:
        record["passage_box_world"] = [round(float(value), 2) for value in passage_box]
    return record


def analyze_screen_regions(frame, telemetry):
    assert _ORIGINAL_ANALYZE is not None
    observations = _ORIGINAL_ANALYZE(frame, telemetry)
    _LATEST_RAW.clear()
    for observation in observations:
        try:
            key = (int(observation.region_x), int(observation.region_y))
        except (AttributeError, TypeError, ValueError):
            continue
        _LATEST_RAW[key] = _raw_record(observation)
    return observations


def latest_raw_observation(region: tuple[int, int]) -> dict[str, object] | None:
    value = _LATEST_RAW.get((int(region[0]), int(region[1])))
    return dict(value) if value is not None else None


def install_guessing_v3_screen_observer() -> None:
    """Wrap whichever analyzer is active after Run15 installation."""

    global _INSTALLED, _ORIGINAL_ANALYZE
    if _INSTALLED:
        return
    _ORIGINAL_ANALYZE = policy_module.analyze_screen_regions
    policy_module.analyze_screen_regions = analyze_screen_regions
    _INSTALLED = True


__all__ = [
    "analyze_screen_regions",
    "install_guessing_v3_screen_observer",
    "latest_raw_observation",
]
