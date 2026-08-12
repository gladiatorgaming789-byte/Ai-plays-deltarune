from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Mapping

from . import evaluation, run_artifacts


_LOOP_MARKERS = (
    "loop",
    "oscillation",
    "stalled recovery",
    "repetitive behavior",
    "room bounce",
    "rapid return",
)


def _structured_story_progress_count(
    events: Iterable[Mapping[str, Any]],
) -> int:
    count = 0
    for event in events:
        updates = event.get("map_updates")
        if not isinstance(updates, list):
            continue
        if any(
            isinstance(update, Mapping)
            and str(update.get("type") or "") == "story_progress"
            for update in updates
        ):
            count += 1
    return count


def calculate_metrics(events):
    rows = list(events)
    metrics = _ORIGINAL_CALCULATE_METRICS(rows)
    structured = _structured_story_progress_count(rows)
    # Structured map updates are authoritative. They are emitted exactly when
    # the policy records an observed progress event, unlike action reasons that
    # may merely describe a search for progress.
    return replace(metrics, story_progress_events=structured)


def build_run_diagnostics(events):
    rows = list(events)
    diagnostics = _ORIGINAL_BUILD_RUN_DIAGNOSTICS(rows)
    diagnostics["loop_recovery_steps"] = sum(
        any(marker in str(event.get("reason") or "").casefold() for marker in _LOOP_MARKERS)
        for event in rows
    )
    return diagnostics


def install_run20_reporting() -> None:
    evaluation.calculate_metrics = calculate_metrics
    run_artifacts.build_run_diagnostics = build_run_diagnostics


_ORIGINAL_CALCULATE_METRICS = evaluation.calculate_metrics
_ORIGINAL_BUILD_RUN_DIAGNOSTICS = run_artifacts.build_run_diagnostics
