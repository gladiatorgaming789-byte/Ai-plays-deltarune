"""Production facade for Independent Population Training v2.1.

The v2.1 supervisor owns process/safety mechanics. This facade binds scoring to
structured events that the current policy actually emits, so a renamed or
missing summary counter cannot silently change tournament results.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from . import multi_instance_training_v21 as v21


_ORIGINAL_UPDATE = v21._update_candidate_event


def _counter(candidate, name: str) -> int:
    return int(getattr(candidate, name, 0) or 0)


def _set(candidate, name: str, value: object) -> None:
    setattr(candidate, name, value)


def _observed_score(candidate) -> tuple[float, float]:
    points = (
        50.0 * _counter(candidate, "observed_non_discovery_progress")
        + 15.0 * max(0, len(candidate.rooms) - 1)
        + 10.0 * _counter(candidate, "observed_choice_successes")
        + 3.0 * _counter(candidate, "observed_new_interactables")
        + min(10.0, 0.25 * _counter(candidate, "observed_new_open_edges"))
        - 5.0 * _counter(candidate, "observed_flavor_interactions")
        - 8.0 * _counter(candidate, "observed_choice_failures")
        - 0.05 * candidate.decisions
    )
    return points, 100.0 * points / (candidate.decisions + 64)


def _update_candidate_event(candidate, payload: dict[str, object]) -> None:
    _ORIGINAL_UPDATE(candidate, payload)
    updates = payload.get("map_updates")
    if not isinstance(updates, list):
        updates = []

    interaction_ids = getattr(candidate, "_score_interaction_ids", None)
    if not isinstance(interaction_ids, set):
        interaction_ids = set()
        _set(candidate, "_score_interaction_ids", interaction_ids)
    open_edges = getattr(candidate, "_score_open_edges", None)
    if not isinstance(open_edges, set):
        open_edges = set()
        _set(candidate, "_score_open_edges", open_edges)
    progress_ids = getattr(candidate, "_score_progress_ids", None)
    if not isinstance(progress_ids, set):
        progress_ids = set()
        _set(candidate, "_score_progress_ids", progress_ids)
    choice_ids = getattr(candidate, "_score_choice_ids", None)
    if not isinstance(choice_ids, set):
        choice_ids = set()
        _set(candidate, "_score_choice_ids", choice_ids)
    flavor_ids = getattr(candidate, "_score_flavor_ids", None)
    if not isinstance(flavor_ids, set):
        flavor_ids = set()
        _set(candidate, "_score_flavor_ids", flavor_ids)

    try:
        step = int(payload.get("step") or 0)
    except (TypeError, ValueError):
        step = 0

    for index, update in enumerate(updates):
        if not isinstance(update, Mapping):
            continue
        kind = str(update.get("type") or "")
        if kind == "story_progress":
            event = str(update.get("event") or "")
            identity = (step, index, event, str(update.get("room") or ""))
            if identity not in progress_ids:
                progress_ids.add(identity)
                if event != "discovered a new room":
                    _set(
                        candidate,
                        "observed_non_discovery_progress",
                        _counter(candidate, "observed_non_discovery_progress") + 1,
                    )
        elif kind == "choice_outcome":
            identity = (
                step,
                index,
                str(update.get("room") or ""),
                int(update.get("pattern") or 0),
            )
            if identity not in choice_ids:
                choice_ids.add(identity)
                field = (
                    "observed_choice_successes"
                    if bool(update.get("successful"))
                    else "observed_choice_failures"
                )
                _set(candidate, field, _counter(candidate, field) + 1)
        elif kind == "interaction_outcome":
            cell = update.get("cell")
            cell_key = tuple(cell) if isinstance(cell, list) and len(cell) == 2 else ()
            identity = (str(update.get("room") or ""), *cell_key)
            if identity and identity not in interaction_ids:
                interaction_ids.add(identity)
                _set(
                    candidate,
                    "observed_new_interactables",
                    _counter(candidate, "observed_new_interactables") + 1,
                )
            if str(update.get("usefulness") or "") == "flavor":
                flavor_identity = (step, *identity)
                if flavor_identity not in flavor_ids:
                    flavor_ids.add(flavor_identity)
                    _set(
                        candidate,
                        "observed_flavor_interactions",
                        _counter(candidate, "observed_flavor_interactions") + 1,
                    )
        elif kind == "open_edge":
            source = update.get("from_cell")
            target = update.get("to_cell")
            if (
                isinstance(source, list)
                and len(source) == 2
                and isinstance(target, list)
                and len(target) == 2
            ):
                identity = (
                    str(update.get("room") or ""),
                    int(source[0]),
                    int(source[1]),
                    int(target[0]),
                    int(target[1]),
                )
                if identity not in open_edges:
                    open_edges.add(identity)
                    _set(
                        candidate,
                        "observed_new_open_edges",
                        _counter(candidate, "observed_new_open_edges") + 1,
                    )

    candidate.total_points, candidate.normalized_score = _observed_score(candidate)


def _summary_count(summary: Mapping[str, object], key: str) -> int:
    value = summary.get(key)
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _finalize_candidates(workspace):
    for candidate in workspace.candidates:
        candidate.exit_code = (
            candidate.controller_process.returncode
            if candidate.controller_process is not None
            else -1
        )
        candidate.summary_path = v21.legacy._find_summary(candidate)
        if candidate.exit_code != 0:
            v21._disqualify(candidate, f"controller exited with code {candidate.exit_code}")
        if candidate.summary_path is None:
            v21._disqualify(candidate, "run summary missing")
            continue
        try:
            summary = json.loads(candidate.summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            v21._disqualify(candidate, "run summary unreadable")
            continue
        if not isinstance(summary, Mapping):
            v21._disqualify(candidate, "run summary malformed")
            continue

        candidate.story_progress = _counter(candidate, "observed_non_discovery_progress")
        candidate.total_points, candidate.normalized_score = _observed_score(candidate)
        # Penalties that are intrinsically end-of-run aggregates remain sourced
        # from the current policy summary. Positive/negative outcome rewards
        # above come only from structured event records emitted by this run.
        candidate.total_points -= (
            15.0 * _summary_count(summary, "rapid_room_returns")
            + 10.0 * _summary_count(summary, "oscillation_breaks")
            + 4.0 * _summary_count(summary, "coherence_goal_failures")
            + 2.0 * _summary_count(summary, "broad_recovery_resets")
        )
        candidate.normalized_score = (
            100.0 * candidate.total_points / (candidate.decisions + 64)
        )
        v21._validate_candidate_run(candidate, summary)
        candidate.status = "completed" if not candidate.disqualified else "disqualified"

    eligible_candidates = [
        candidate
        for candidate in workspace.candidates
        if not candidate.disqualified
        and candidate.exit_code == 0
        and candidate.decisions >= v21.MIN_ACTIVE_DECISIONS
    ]
    required = max(
        2,
        __import__("math").ceil(
            len(workspace.candidates) * v21.PROMOTION_QUORUM_FRACTION
        ),
    )
    if len(eligible_candidates) < required:
        return (
            False,
            None,
            f"No winner can be recommended: {len(eligible_candidates)}/{required} "
            "required clean, sufficiently exposed independent AIs passed all gates.",
        )
    winner = sorted(
        eligible_candidates,
        key=lambda candidate: (
            -candidate.normalized_score,
            -candidate.story_progress,
            candidate.safety_penalties,
            candidate.candidate_id,
        ),
    )[0]
    return (
        True,
        winner,
        f"{winner.label} achieved the best independent normalized score "
        f"({winner.normalized_score:.3f}) among {len(eligible_candidates)} clean "
        f"candidates; promotion quorum was {required}/{len(workspace.candidates)}.",
    )


def run_multi_instance_training(args: Any):
    """Run the v2.1 supervisor with current-event scoring installed."""

    original_update = v21._update_candidate_event
    original_finalize = v21._finalize_candidates
    v21._update_candidate_event = _update_candidate_event
    v21._finalize_candidates = _finalize_candidates
    try:
        return v21.run_multi_instance_training(args)
    finally:
        v21._update_candidate_event = original_update
        v21._finalize_candidates = original_finalize


__all__ = ["run_multi_instance_training"]
