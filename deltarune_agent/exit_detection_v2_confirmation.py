"""Observed-transition confirmation for Exit Detection v2.

Exit Detection v2 intentionally removes the legacy ``possible_exit`` label from
unresolved visual candidates.  The older confirmation routine only considers
records that still carry that label, so a real room transition could otherwise
fail to confirm the candidate that just proved itself.

This bridge keeps pre-crossing detection strict while making an observed room
change authoritative after the fact.  It associates a transition only with a
nearby v2 candidate supported by spatial evidence; it never invents a route or
uses game-specific knowledge.
"""

from __future__ import annotations

from typing import Mapping

from . import guessing_v3 as v3
from .exit_detection_v2 import (
    EXIT_DETECTION_VERSION,
    _safe_float,
    _safe_int,
    exit_candidate_source,
)
from .policy import StarterPolicy


_INSTALLED = False
_ORIGINAL_CONFIRM_VISUAL_EXIT = None
MAX_CONFIRM_CELL_DISTANCE = 3


def _anchor_cell(record: Mapping[str, object]) -> tuple[int, int] | None:
    value = record.get("anchor_cell")
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None


def _candidate_transition_distance(
    self: StarterPolicy,
    key: tuple[str, int, int],
    record: Mapping[str, object],
    source_cell: tuple[int, int],
) -> tuple[int, int] | None:
    source_region = self._region(source_cell)
    region_distance = max(
        abs(key[1] - source_region[0]),
        abs(key[2] - source_region[1]),
    )
    anchor = _anchor_cell(record)
    if anchor is not None:
        cell_distance = abs(anchor[0] - source_cell[0]) + abs(anchor[1] - source_cell[1])
        if cell_distance > MAX_CONFIRM_CELL_DISTANCE:
            return None
        return cell_distance, region_distance
    # Without an anchor, require the exact source region rather than borrowing
    # a neighboring visual feature merely because the old detector grouped it
    # into a large screen region.
    if region_distance != 0:
        return None
    return MAX_CONFIRM_CELL_DISTANCE + 1, region_distance


def transition_candidate_matches(
    self: StarterPolicy,
    room: str,
    source_cell: tuple[int, int],
) -> list[tuple[tuple[str, int, int], dict[str, object]]]:
    """Return spatially plausible v2 candidates for an observed room crossing."""

    matches: list[
        tuple[
            tuple[int, int, int, float, int, int],
            tuple[str, int, int],
            dict[str, object],
        ]
    ] = []
    for key, record in self.screen_regions.items():
        if key[0] != room:
            continue
        if exit_candidate_source(record) is None:
            continue
        distance = _candidate_transition_distance(self, key, record, source_cell)
        if distance is None:
            continue
        cell_distance, region_distance = distance
        state = str(record.get("exit_candidate_state") or "")
        state_rank = (
            0
            if state == "semantic_ready"
            else 1
            if state in {"visual_candidate", "needs_approach_evidence"}
            else 2
            if state == "geometry_candidate"
            else 3
        )
        score = _safe_float(record.get("exit_candidate_visual_score"), 0.0)
        last_seen = _safe_int(record.get("last_seen_step"), 0)
        matches.append(
            (
                (
                    cell_distance,
                    region_distance,
                    state_rank,
                    -score,
                    -last_seen,
                    key[1] + key[2],
                ),
                key,
                record,
            )
        )
    return [
        (key, record)
        for _score, key, record in sorted(matches, key=lambda item: item[0])
    ]


def confirm_candidate_from_transition(
    self: StarterPolicy,
    room: str,
    source_cell: tuple[int, int],
    target_room: str,
) -> tuple[str, int, int] | None:
    """Promote the best nearby v2 candidate using an actual room transition."""

    matches = transition_candidate_matches(self, room, source_cell)
    if not matches:
        return None
    key, record = matches[0]
    record["exit_detection_version"] = EXIT_DETECTION_VERSION
    record["exit_candidate_state"] = "confirmed"
    record["exit_candidate_visual_score"] = 1.0
    record["hypothesis"] = "possible_exit"
    record["guess_state"] = "confirmed"
    record["confirmed_target_room"] = target_room
    record["confirmed_at_cell"] = list(source_cell)
    record["completed_tests"] = max(
        1,
        _safe_int(record.get("completed_tests", record.get("inspections", 0))),
    )
    record["inspections"] = int(record["completed_tests"])
    reasons = record.get("exit_candidate_reasons")
    if not isinstance(reasons, list):
        reasons = []
    reasons = [str(reason) for reason in reasons[-7:]]
    reasons.append(
        f"observed transition from {room} to {target_room} at source cell {source_cell}"
    )
    record["exit_candidate_reasons"] = reasons

    # Recompute beliefs after setting the authoritative lifecycle state. V3
    # preserves confirmed guesses while retaining the full evidence ledger.
    v3.refresh_guess_record_v3(record, region=(key[1], key[2]))
    record["hypothesis"] = "possible_exit"
    record["guess_state"] = "confirmed"
    record["guess_semantic_state"] = "possible_exit"
    self.map_updates.append(self._screen_region_map_update(key, record))
    return key


def _confirm_visual_exit_v2(
    self: StarterPolicy,
    room: str,
    source_cell: tuple[int, int],
    target_room: str,
) -> None:
    assert _ORIGINAL_CONFIRM_VISUAL_EXIT is not None
    _ORIGINAL_CONFIRM_VISUAL_EXIT(self, room, source_cell, target_room)
    # The old routine may already have confirmed a semantic-ready candidate. If
    # so, this is idempotent; otherwise it catches a deliberately unresolved v2
    # candidate that the real transition just proved.
    confirm_candidate_from_transition(self, room, source_cell, target_room)


def install_exit_detection_v2_confirmation() -> None:
    global _INSTALLED, _ORIGINAL_CONFIRM_VISUAL_EXIT
    if _INSTALLED:
        return
    _ORIGINAL_CONFIRM_VISUAL_EXIT = StarterPolicy._confirm_visual_exit
    StarterPolicy._confirm_visual_exit = _confirm_visual_exit_v2  # type: ignore[method-assign]
    _INSTALLED = True


__all__ = [
    "confirm_candidate_from_transition",
    "install_exit_detection_v2_confirmation",
    "transition_candidate_matches",
]
